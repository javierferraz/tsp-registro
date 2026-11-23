"""Botones de Telegram que deciden (PAA2 P3-3, S-1268; 06 §4 y 02 §9).

Camino de una pulsación: Telegram → Worker de Cloudflare (`telegram/worker.mjs`: comprueba el
secreto del webhook, que quien pulsa es Javier y que la acción es de la lista) →
`repository_dispatch` de GitHub → workflow `telegram.yml` de este repo → `org telegram`, que
vuelve a validar la acción, escribe el evento con papel javier y avisa por Telegram.

**Lista cerrada, sin texto libre** (D-17 de 02): pausar, reanudar, aprobar o rechazar una tarea
propuesta, ver su detalle. Nada publica: publicar una tanda sigue pidiendo la sesión de GitHub.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

from .esquemas import Rechazo
from .registro import Registro

IDENTIDAD = "telegram"
# Misma expresión que el Worker: si se cambia una, se cambia la otra (y sus tests).
_ACCION = re.compile(r"^(?:p:(suave|dura|emergencia)|(r)|([axd]):([A-Za-z0-9][A-Za-z0-9._-]{0,40}))$")
_PULSACION = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def interpretar(accion: str) -> tuple[str, str]:
    """`callback_data` de un botón → (orden, argumento). Rechaza lo que no sea de la lista."""
    m = _ACCION.match(accion or "")
    if not m:
        raise Rechazo(f"acción fuera de la lista cerrada: {accion!r}")
    nivel, reanudar, letra, ent_id = m.groups()
    if nivel:
        return "pausar", nivel
    if reanudar:
        return "reanudar", ""
    return {"a": "aprobar", "x": "rechazar", "d": "detalle"}[letra], ent_id


def pausas_activas(reg: Registro) -> list[str]:
    return sorted(i for i, e in reg.entidades("pausa").items() if e["estado"] == "activa")


def _boton_siguiente(reg: Registro) -> list[tuple[str, str]]:
    return [("▶ Reanudar", "r")] if pausas_activas(reg) else [("⏸ Pausar", "p:suave")]


def aplicar(reg: Registro, accion: str, pulsacion: str) -> dict:
    """Ejecuta una pulsación. Devuelve {texto, botones, eventos}; `eventos` son los ids nuevos.
    La clave de idempotencia sale del id de la pulsación: si Telegram o GitHub la repiten,
    no se registra dos veces."""
    if not _PULSACION.match(pulsacion or ""):
        raise Rechazo(f"id de pulsación no válido: {pulsacion!r}")
    orden, arg = interpretar(accion)
    escribir = lambda tipo, ent, clave, datos: reg.escribir(  # noqa: E731
        tipo, ent, "javier", IDENTIDAD, clave, datos)

    if orden == "pausar":
        r = escribir("pausa.activada", ("pausa", f"tg-{pulsacion}"), f"tg:{pulsacion}",
                     {"nivel": arg, "origen": "telegram", "ambito": "general"})
        texto = (f"⏸ Pausa {arg} registrada (pausa tg-{pulsacion}). Nada nuevo debería arrancar; "
                 "quién la respeta llega en la fase 5.")
        return {"texto": texto, "botones": _boton_siguiente(reg), "eventos": [r.id] * r.nuevo}
    if orden == "reanudar":
        activas = pausas_activas(reg)
        if not activas:
            return {"texto": "▶ No había ninguna pausa activa: nada que reanudar.",
                    "botones": _boton_siguiente(reg), "eventos": []}
        ids = []
        for p in activas:
            r = escribir("pausa.retirada", ("pausa", p), f"tg:{pulsacion}:{p}", {})
            ids += [r.id] * r.nuevo
        return {"texto": f"▶ Reanudado: {len(activas)} pausa(s) retirada(s) ({', '.join(activas)}).",
                "botones": _boton_siguiente(reg), "eventos": ids}
    if orden in ("aprobar", "rechazar"):
        tipo, datos = (("tarea.aprobada", {"motivo": "aprobada por Javier desde Telegram"})
                       if orden == "aprobar" else
                       ("tarea.descartada", {"motivo": "rechazada por Javier desde Telegram"}))
        r = escribir(tipo, ("tarea", arg), f"tg:{pulsacion}", datos)
        return {"texto": f"{'✅ Aprobada' if orden == 'aprobar' else '❌ Rechazada'}: tarea {arg}.",
                "botones": [], "eventos": [r.id] * r.nuevo}
    # detalle: solo estado y fechas; el registro no guarda títulos (D-8)
    for tipo in ("tarea", "tarjeta"):
        ent = reg.ver(tipo, arg)
        if ent:
            texto = (f"🔎 {tipo} {arg}: estado «{ent['estado']}», actualizada "
                     f"{ent.get('actualizada', '?')[:16].replace('T', ' ')} UTC, "
                     f"{len(ent.get('historial', []))} cambio(s).")
            return {"texto": texto, "botones": [], "eventos": []}
    return {"texto": f"🔎 No hay ninguna tarea ni tarjeta {arg} en el registro.", "botones": [],
            "eventos": []}


def panel(reg: Registro) -> dict:
    activas = pausas_activas(reg)
    texto = (f"⏸ En pausa: {', '.join(activas)}." if activas else "▶ Sin pausa: la organización corre.")
    return {"texto": texto, "botones": _boton_siguiente(reg), "eventos": []}


def enviar(texto: str, botones: list[tuple[str, str]]) -> bool:
    """Manda el mensaje al canal de TSPBot. Credenciales solo del entorno (secretos de GitHub
    TSPBOT_TOKEN y TSPBOT_CHAT_ID). Nunca lanza: un aviso que falla no deshace el evento."""
    tok, chat = os.environ.get("TSPBOT_TOKEN"), os.environ.get("TSPBOT_CHAT_ID")
    if not (tok and chat):
        print("org telegram: sin TSPBOT_TOKEN/TSPBOT_CHAT_ID; no se avisa.", file=sys.stderr)
        return False
    campos = {"chat_id": chat, "text": texto[:4000]}
    if botones:
        campos["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in botones]]})
    try:
        with urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok,
                                    data=urllib.parse.urlencode(campos).encode(), timeout=20) as r:
            return bool(json.load(r).get("ok"))
    except Exception as e:  # noqa: BLE001
        print(f"org telegram: aviso fallido ({type(e).__name__})", file=sys.stderr)
        return False
