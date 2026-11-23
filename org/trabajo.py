"""Contrato de trabajo ampliado (02 §4 y §8.3; PAA2 P4-1, S-1269): latido agrupado, punto de
control, caducidad y el paquete que recibe el trabajador que retoma una tarea.

- **Latido agrupado.** El trabajador puede latir tan a menudo como quiera (`org latido`): solo
  se escribe un evento si el último de esa tarea tiene 10 minutos o más (12 si el día ya pasa
  de 500 eventos), y al escribirse se sube junto con todo lo pendiente. Entre medias, los
  ficheros que dice haber tocado se guardan en el índice local y salen en el siguiente latido o
  punto de control. Así son 6 latidos por hora y tarea como mucho, frente a la caducidad de 20.
- **Punto de control.** Qué está hecho, qué sigue y qué ficheros tiene a medias. Se escribe
  siempre y se sube: es lo que permite que otro siga.
- **Caducidad.** El planificador marca `caducada` la tarea en curso sin latido en 20 minutos;
  después la devuelve a `lista` y la asigna a otro, que lee `contrato()` y sigue desde el
  último punto de control en la misma rama.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from .esquemas import TIPOS, Rechazo, describir
from .estado import _FMT

INTERVALO_LATIDO = timedelta(minutes=10)    # 02 §9 ter: subida agrupada cada 10 min
INTERVALO_SATURADO = timedelta(minutes=12)  # con el día por encima del presupuesto
CADUCIDAD = timedelta(minutes=20)           # 02 §9 ter: caducidad por falta de latido
# El trabajador late como mucho cada 5 min: así el latido escrito nunca llega más tarde que
# intervalo + 5 (15, o 17 saturado), con margen frente a los 20 de la caducidad.
LATIR_CADA = timedelta(minutes=5)
PRESUPUESTO_DIARIO = 500                    # 02 §3.1 ter: menos de 500 eventos al día


def _ahora(ahora):
    return ahora or datetime.now(timezone.utc)


def _fecha(s: str) -> datetime:
    return datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc)


def volumen(reg, ahora=None) -> dict:
    """Eventos de hoy (UTC) frente al presupuesto diario."""
    por_tipo = reg.contar_eventos(_ahora(ahora).strftime("%Y-%m-%d"))
    total = sum(por_tipo.values())
    return {"hoy": total, "presupuesto": PRESUPUESTO_DIARIO,
            "latidos": por_tipo.get("tarea.latido", 0),
            "importador": sum(n for t, n in por_tipo.items()
                              if t.split(".")[0] in ("tarjeta", "ejecucion", "informe")),
            "superado": total >= PRESUPUESTO_DIARIO}


def _subir(reg) -> str:
    try:
        return reg.subir()
    except Rechazo as e:  # sin red o sin git: queda en local y sale con la siguiente subida
        return f"sin subir: {e}"


def latido(reg, tarea_id: str, identidad: str, ficheros=(), ahora=None, subir=True) -> dict:
    ahora = _ahora(ahora)
    saturado = False

    def paso(db):
        nonlocal saturado
        hoy = db.execute("SELECT COUNT(*) FROM eventos WHERE fecha >= ?",
                         (ahora.strftime("%Y-%m-%d"),)).fetchone()[0]
        saturado = hoy >= PRESUPUESTO_DIARIO
        intervalo = INTERVALO_SATURADO if saturado else INTERVALO_LATIDO
        tarea = reg._entidad(db, "tarea", tarea_id)
        if tarea is None or tarea["estado"] != "en_curso":
            raise Rechazo(f"tarea {tarea_id}: no está en curso "
                          f"({tarea['estado'] if tarea else 'no existe'})")
        db.executemany("INSERT OR IGNORE INTO latidos_pendientes VALUES (?, ?)",
                       [(tarea_id, f) for f in ficheros])
        ultimo = tarea.get("ultimo_latido")
        if ultimo and ahora - _fecha(ultimo) < intervalo:
            falta = intervalo - (ahora - _fecha(ultimo))
            return {"escrito": False, "siguiente_en_s": int(falta.total_seconds())}
        pendientes = [f for (f,) in db.execute(
            "SELECT fichero FROM latidos_pendientes WHERE tarea=? ORDER BY fichero",
            (tarea_id,))]
        r = reg._escribir_en(db, "tarea.latido", ("tarea", tarea_id), "trabajador", identidad,
                             f"{tarea_id}:latido:{identidad}:{ahora:%Y%m%dT%H%M%S%f}",
                             {"ficheros": pendientes} if pendientes else {}, None, 2, ahora)
        db.execute("DELETE FROM latidos_pendientes WHERE tarea=?", (tarea_id,))
        return {"escrito": True, "id": r.id, "ficheros": pendientes}

    out = reg._tx(paso)
    out["saturado"] = saturado
    if out["escrito"] and subir:
        out["subida"] = _subir(reg)
    return out


def punto_control(reg, tarea_id: str, identidad: str, hecho: str, sigue: str, ficheros=(),
                  rama=None, commit=None, a_medias=(), ahora=None, subir=True) -> dict:
    """Siempre se escribe (lleva también los ficheros acumulados por los latidos) y se sube."""
    ahora = _ahora(ahora)

    def paso(db):
        previos = [f for (f,) in db.execute(
            "SELECT fichero FROM latidos_pendientes WHERE tarea=?", (tarea_id,))]
        datos = {"hecho": hecho, "sigue": sigue,
                 "ficheros": sorted(set(previos) | set(ficheros))}
        datos.update({k: v for k, v in (("rama", rama), ("commit", commit)) if v})
        if a_medias:
            datos["a_medias"] = sorted(a_medias)
        r = reg._escribir_en(db, "tarea.punto_control", ("tarea", tarea_id), "trabajador",
                             identidad, f"{tarea_id}:punto_control:{secrets.token_hex(6)}",
                             datos, None, 2, ahora)
        db.execute("DELETE FROM latidos_pendientes WHERE tarea=?", (tarea_id,))
        return {"escrito": True, "id": r.id, **datos}

    out = reg._tx(paso)
    if subir:
        out["subida"] = _subir(reg)
    return out


def caducar(reg, identidad: str, minutos: int | None = None, ahora=None) -> list[dict]:
    """El planificador: marca `caducada` cada tarea en curso sin latido en `minutos` (20)."""
    ahora = _ahora(ahora)
    limite = timedelta(minutes=minutos) if minutos else CADUCIDAD
    out = []
    for tid, t in reg.entidades("tarea").items():
        ultimo = t.get("ultimo_latido")
        if t["estado"] != "en_curso" or not ultimo or ahora - _fecha(ultimo) < limite:
            continue
        r = reg.escribir("tarea.caducada", ("tarea", tid), "planificador", identidad,
                         f"{tid}:caducada:{ultimo}", {"motivo": f"sin latido desde {ultimo}"},
                         ahora=ahora)
        out.append({"tarea": tid, "ultimo_latido": ultimo, "id": r.id,
                    "punto_control": "punto_control" in t})
    return out


_CAMPOS_TAREA = ("objetivo", "titulo", "repo", "tipo_trabajo", "alcance", "criterios",
                 "no_objetivos", "dependencias", "tamano", "modelo", "presupuesto")


def contrato(reg, tarea_id: str, ahora=None) -> dict:
    """Lo que recibe el trabajador (02 §4): la tarea, sus recursos, y si viene de un relevo,
    el último punto de control y los ficheros tocados. Más el esquema del informe de entrega."""
    from .recursos import vivas

    t = reg.ver("tarea", tarea_id)
    if t is None:
        raise Rechazo(f"no existe tarea:{tarea_id}")
    out = {"tarea": tarea_id, "estado": t["estado"],
           **{k: t[k] for k in _CAMPOS_TAREA if k in t},
           "ejecucion": t.get("ejecucion"),
           "recursos": [{"reserva": r["id"], "recurso": r["recurso"], "titular": r["titular"],
                         "caduca": r["caduca"]}
                        for r in vivas(reg, None, _ahora(ahora)) if r.get("tarea") == tarea_id],
           "latir_cada_min": int(LATIR_CADA.total_seconds() // 60),
           "version_esquema": 2,  # latido, punto de control y entrega: v2 (solo del ejecutor)
           "informe_v2": describir(TIPOS["tarea.entregada"].versiones[2])}
    if t.get("ejecuciones_anteriores") or t.get("punto_control"):
        pc = t.get("punto_control")
        out["relevo"] = {
            "ejecuciones_anteriores": len(t.get("ejecuciones_anteriores", [])),
            "punto_control": pc,
            "rama": (pc or {}).get("rama") or (t.get("ejecucion") or {}).get("rama"),
            "ficheros": t.get("ficheros", []),
        }
    return out
