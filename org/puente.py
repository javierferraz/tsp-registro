"""Puente tablero ⇄ registro (PAA2 P4-3, S-1271): el registro pasa a mandar, repo a repo.

El tablero sigue siendo la pantalla y guarda el texto (títulos, cuerpos, notas: D-8 no deja
copiarlo aquí), pero **el estado de las tarjetas de un repo migrado lo manda el registro**:

- **Del tablero al registro.** Cada movimiento de una tarjeta (sus notas de transición, en
  orden, con quién lo hizo) se traduce al camino legal más corto del ciclo de la tarea (02 §3.3),
  con los papeles que el puente puede llevar: planificador, dirección y trabajador. Mover a
  Empezar desde el navegador cuenta como aprobación del planificador «sin firma» (la firmada es
  el botón de Telegram: decisión de Javier, 2026-11-23).
- **Terminada no se escribe:** la pone el validador. Mover a Terminada una tarea entregada lanza
  el validador (`despachos`); la tarjeta se queda en Terminada y la tarea, entregada, hasta su
  veredicto: si la cierra no se mueve nada, si la devuelve o la bloquea vuelve a su columna.
- **Del registro al tablero (proyección).** Si el registro va por delante (el validador la cerró,
  la devolvió o la bloqueó), la tarjeta se mueve a la columna que le toca. La misma proyección,
  aplicada a todas las tarjetas, es la vuelta atrás (`exportar`).
- **Discrepancia:** un movimiento que el registro no acepta (cerrar sin validar, una columna
  desconocida). La tarjeta vuelve a donde dice el registro y la etapa en curso vuelve a empezar
  su ciclo (`migracion.discrepancia`).

Etapas (`migracion:tablero-N`): una etapa se supera con un ciclo limpio, es decir, cuando todos
los escritores (navegador, vigías, Rosetta, Linux…) han escrito desde su inicio o desde la última
discrepancia sin ninguna discrepancia. Entonces empieza sola la siguiente. Quién escribió y cuándo
lo pone quien llama (el lado del tablero lo saca de git y del registro de eventos de board.json).

Este módulo no toca board.json: devuelve lo que hay que mover y el lado del tablero
(`tablero/puente.py` del repo central) lo aplica con su cerrojo.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from .comparar import COLUMNA_A_ESTADO
from .esquemas import TIPOS

IDENTIDAD = "puente-tablero"
PREFERENCIA = ("planificador", "direccion", "trabajador")  # papeles que lleva el puente
CENTRAL = "2606-thestromboliproject"

# Estado de la tarea → estado comprimido del tablero (el de COLUMNA_A_ESTADO).
COMPRIMIDO = {
    "propuesta": "propuesta", "aprobada": "aprobada", "lista": "aprobada",
    "caducada": "aprobada", "en_curso": "en_curso", "en_pausa": "en_curso",
    "devuelta": "en_curso", "bloqueada": "bloqueada", "entregada": "entregada",
    "verificada": "entregada", "integrada": "entregada", "cerrada": "cerrada",
    "descartada": "descartada",
}
COLUMNA_DE = {"propuesta": "propuesta", "aprobada": "adelante", "en_curso": "curso",
              "bloqueada": "info", "entregada": "validar", "cerrada": "hecha",
              "descartada": "descartada"}
# Una propuesta con una pregunta escrita va a Pendiente sin dejar de ser propuesta (CLAUDE.md:
# «si algo no se entiende, moverlo a info con la pregunta»): no es un bloqueo de trabajo.
TAMBIEN = {"info": {"propuesta"}}
TERMINADA = "hecha"
FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def compatible(columna: str, estado: str) -> bool:
    comp = COMPRIMIDO.get(estado)
    return comp is not None and (COLUMNA_A_ESTADO.get(columna) == comp
                                 or comp in TAMBIEN.get(columna, set()))


def columna(estado: str, actual: str | None = None) -> str:
    """La columna de un estado; la actual si ya le vale (pedida y propuesta son lo mismo)."""
    if actual and compatible(actual, estado):
        return actual
    return COLUMNA_DE[COMPRIMIDO[estado]]


def _pasos():
    out = []
    for nombre, t in TIPOS.items():
        if t.entidad != "tarea" or t.desde is None or t.hasta is None or t.espejo:
            continue
        papel = next((p for p in PREFERENCIA if p in t.papeles), None)
        if papel and nombre != "tarea.caducada":  # caducar es del planificador de verdad
            out.append((nombre, papel, t))
    return out


PASOS = _pasos()


def camino(desde: str, objetivo: str) -> list[tuple[str, str, str]] | None:
    """[(tipo, papel, estado nuevo)] más corto de `desde` a un estado de columna `objetivo`
    (estado comprimido), solo con los papeles del puente. [] si ya está; None si no hay."""
    if COMPRIMIDO.get(desde) == objetivo:
        return []
    cola, vistos = deque([(desde, [])]), {desde}
    while cola:
        e, ruta = cola.popleft()
        for nombre, papel, t in PASOS:
            if e not in t.desde or t.hasta in vistos:
                continue
            r = ruta + [(nombre, papel, t.hasta)]
            if COMPRIMIDO[t.hasta] == objetivo:
                return r
            vistos.add(t.hasta)
            cola.append((t.hasta, r))
    return None


def _fecha(texto: str | None) -> datetime | None:
    if not texto:
        return None
    for fmt in (FMT, "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(texto, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _actor(nota: dict) -> str:
    a = nota.get("actor") or {}
    if a.get("kind") == "human":
        return "javier"
    by = nota.get("by") or a.get("id") or "tablero"
    return by if by.replace("-", "").isalnum() and len(by) <= 32 else "tablero"


def _commits(card: dict) -> list[str]:
    return [str(e["sha"])[:12] for e in ((card.get("evidence") or {}).get("commits") or [])
            if isinstance(e, dict) and e.get("sha")]


def _datos(nombre: str, cid: str, de: str, a: str, actor: str, card: dict) -> dict:
    v = TIPOS[nombre].versiones[1]
    if nombre == "tarea.asignada":
        return {"ejecutor": actor}
    if nombre == "tarea.entregada":
        c = _commits(card)
        return {"resumen": f"{cid} a Por validar", **({"commits": c} if c else {})}
    if nombre == "tarea.aprobada" and actor == "javier":
        return {"motivo": "Javier en el tablero (sin firma)"}
    if "motivo" in v.obligatorios or "motivo" in v.opcionales:
        return {"motivo": f"tablero: {de} -> {a} ({actor})"}
    return {}


def transiciones(card: dict) -> list[dict]:
    out = []
    for n in card.get("notes") or []:
        if n.get("kind") == "transition" and n.get("to") and n.get("by") != "registro":
            f = _fecha(n.get("at"))
            if f:
                out.append({"id": n.get("id") or "", "de": n.get("from") or "", "a": n["to"],
                            "fecha": f, "actor": _actor(n)})
    return sorted(out, key=lambda m: m["fecha"])


def resolver(apps: dict[str, str], manifest: dict | None = None):
    """repo_de(card): el repo de una tarjeta. Sin app → repo central; con app → `apps` de la
    configuración o, si no, el repo que dice el manifest (cartera); si no, «app:<nombre>»."""
    mapa = dict(apps)
    if manifest:
        from .cartera import generar
        for f in generar(manifest).values():
            if f.get("repo") and f.get("nombre"):
                mapa.setdefault(f["nombre"], f["repo"])

    def repo_de(card: dict) -> str:
        app = (card.get("app") or "").strip()
        return mapa.get(app) or (f"app:{app}" if app else CENTRAL)
    return repo_de


# --- etapas ------------------------------------------------------------------------------
def etapas(reg) -> list[dict]:
    """Las etapas del registro, por número: {n, id, estado, repos, desde, discrepancias}."""
    out = []
    for eid, e in reg.entidades("migracion").items():
        if eid.startswith("tablero-") and eid[8:].isdigit():
            out.append({"n": int(eid[8:]), "id": eid, "estado": e["estado"],
                        "repos": e.get("repos", []), "creada": e["creada"],
                        "discrepancias": e.get("discrepancias", [])})
    return sorted(out, key=lambda x: x["n"])


def repos_gestionados(reg, todos: set[str] | None = None) -> set[str]:
    """Repos cuyas tarjetas manda el registro: los de las etapas iniciadas y no revertidas.
    «*» es «el resto»: cualquier repo de `todos` (o todos, si no se pasa)."""
    out: set[str] = set()
    for e in etapas(reg):
        if e["estado"] in ("en_ciclo", "superada"):
            out |= set(e["repos"])
    return out


def _gestionado(repo: str, gestionados: set[str]) -> bool:
    return repo in gestionados or "*" in gestionados


def ciclo(etapa: dict, escritores: list[str], actividad: dict[str, list[str]]) -> dict:
    """¿Ha pasado la etapa un ciclo limpio? `actividad` = {escritor: [fechas ISO en que escribió]}.
    El ciclo cuenta desde el inicio de la etapa o desde su última discrepancia."""
    desde = max([etapa["creada"]] + [d["fecha"] for d in etapa["discrepancias"]])
    t0 = _fecha(desde)
    vistos = sorted(e for e in escritores
                    if any((_fecha(f) or t0) > t0 for f in actividad.get(e, [])))
    return {"etapa": etapa["n"], "desde": desde, "vistos": vistos,
            "faltan": sorted(set(escritores) - set(vistos)),
            "limpio": set(vistos) >= set(escritores)}


# --- el plan -----------------------------------------------------------------------------
def planear(reg, tablero: dict, repo_de, gestionados: set[str], *, exportar=False) -> dict:
    """Lo que hay que escribir en el registro y mover en el tablero. No escribe nada.

    `repo_de(card)` → nombre del repo de la tarjeta. `exportar=True` es la vuelta atrás: el
    registro manda sin mirar los movimientos del tablero (solo proyección)."""
    eventos, proyecciones, discrepancias, despachos, creadas = [], [], [], [], []
    for card in tablero.get("cards", []):
        cid = card.get("id")
        if not isinstance(cid, str) or not cid.startswith("S-"):
            continue
        repo = repo_de(card)
        if not repo or not _gestionado(repo, gestionados):
            continue
        col = card.get("col") or ""
        t = reg.ver("tarea", cid)
        movs = transiciones(card)
        if t is None:
            if exportar or col in (TERMINADA, "descartada"):
                continue  # histórico: lo cerrado antes de migrar se queda como estaba
            eventos.append({"tipo": "tarea.propuesta", "entidad": ("tarea", cid),
                            "papel": "direccion", "clave": f"puente:{cid}:alta",
                            "datos": {"titulo": cid, "repo": repo, "tipo_trabajo": "tablero"}})
            creadas.append(cid)
            estado = "propuesta"
            ult = movs[-1] if movs else None
            pendientes = [{"id": "alta", "de": "propuesta", "a": col,
                           "actor": ult["actor"] if ult else "tablero"}]
        else:
            estado = t["estado"]
            hist = t.get("historial") or []
            ultimo = _fecha(hist[-1]["fecha"] if hist else t.get("creada"))
            pendientes = [] if exportar else [m for m in movs if ultimo and m["fecha"] > ultimo]
        despacho = False
        for i, m in enumerate(pendientes):
            destino = m["a"]
            if destino not in COLUMNA_A_ESTADO:
                discrepancias.append({"tarjeta": cid, "de": m["de"], "a": destino,
                                      "clase": "columna desconocida", "escritor": m["actor"]})
                break
            if compatible(destino, estado):
                continue
            if destino == TERMINADA:
                if COMPRIMIDO[estado] == "entregada":
                    despacho = True
                    continue
                # Javier puede cerrar desde cualquier columna (y el alta de una tarjeta ya
                # terminada también): pasa por entregada y decide el validador. Un agente que
                # cierra sin Por validar es una discrepancia.
                objetivo = "entregada" if (t is None or m["actor"] == "javier") else None
                if objetivo is None:
                    discrepancias.append({"tarjeta": cid, "de": m["de"], "a": destino,
                                          "clase": "terminada sin pasar por Por validar",
                                          "escritor": m["actor"]})
                    break
                despacho = True  # alta de una tarjeta ya terminada: decide el validador
            else:
                objetivo = COLUMNA_A_ESTADO[destino]
            ruta = camino(estado, objetivo)
            if ruta is None:
                discrepancias.append({"tarjeta": cid, "de": m["de"], "a": destino,
                                      "clase": "salto sin camino en el ciclo",
                                      "escritor": m["actor"]})
                break
            for j, (nombre, papel, hasta) in enumerate(ruta):
                eventos.append({"tipo": nombre, "entidad": ("tarea", cid), "papel": papel,
                                "clave": f"puente:{cid}:{m['id']}:{j}:{nombre}",
                                "datos": _datos(nombre, cid, m["de"], destino, m["actor"], card)})
                estado = hasta
        espera = (not exportar and col == TERMINADA and COMPRIMIDO[estado] == "entregada"
                  and not (discrepancias and discrepancias[-1]["tarjeta"] == cid))
        if (despacho or espera) and COMPRIMIDO[estado] == "entregada":
            despachos.append(cid)  # cada pasada: el lado del tablero no relanza antes de tiempo
        if espera:
            continue  # en Terminada a la espera del validador; si devuelve o bloquea, se mueve
        if not compatible(col, estado):
            a = columna(estado, col)
            if discrepancias and discrepancias[-1]["tarjeta"] == cid:
                d = discrepancias[-1]
                nota = (f"El registro no acepta {d['de']} -> {d['a']} ({d['clase']}): la tarjeta "
                        f"vuelve a donde dice el registro. Cuenta como discrepancia de la etapa.")
            elif cid in despachos:
                nota = ("Terminada la pone el validador: lo he lanzado en GitHub y la tarjeta "
                        "espera aquí su veredicto.")
            else:
                nota = _nota_registro(t, estado)
            proyecciones.append({"tarjeta": cid, "de": col, "a": a, "nota": nota})
    return {"eventos": eventos, "proyecciones": proyecciones, "discrepancias": discrepancias,
            "despachos": despachos, "creadas": creadas}


def _nota_registro(t: dict | None, estado: str) -> str:
    v = (t or {}).get("veredicto") or {}
    motivo = v.get("motivo") or ((t or {}).get("historial") or [{}])[-1].get("motivo")
    if estado == "cerrada":
        return "Cerrada por el validador (el registro manda)."
    if estado == "devuelta" and motivo:
        return f"Devuelta por el validador: {motivo[:400]}"
    if estado == "bloqueada" and motivo:
        return f"Bloqueada en el registro: {motivo[:400]}"
    return f"El registro dice «{estado}»: la tarjeta va a su columna."


def aplicar(reg, plan: dict, etapa: dict | None) -> dict:
    """Escribe los eventos del plan (un lote) y, si hay etapa en ciclo, sus discrepancias."""
    lote = list(plan["eventos"])
    if etapa and etapa["estado"] == "en_ciclo":
        for d in plan["discrepancias"]:
            lote.append({"tipo": "migracion.discrepancia",
                         "entidad": ("migracion", etapa["id"]), "papel": "planificador",
                         "clave": f"puente:discrepancia:{d['tarjeta']}:{d['de']}:{d['a']}:"
                                  f"{len(etapa['discrepancias'])}",
                         "datos": {k: d[k] for k in ("tarjeta", "de", "a", "clase", "escritor")}})
    res = reg.escribir_lote(lote, "planificador", IDENTIDAD) if lote else []
    rechazos = [{"tipo": e["tipo"], "tarea": e["entidad"][1], "motivo": str(r)}
                for e, r in zip(lote, res) if isinstance(r, Exception)]
    return {"escritos": sum(1 for r in res if not isinstance(r, Exception) and r.nuevo),
            "rechazados": rechazos}


def avanzar(reg, config_etapas: list[dict], escritores: list[str],
            actividad: dict[str, list[str]]) -> dict:
    """Empieza la primera etapa si no hay ninguna, y supera la actual si su ciclo es limpio
    (y empieza la siguiente). Devuelve qué ha pasado."""
    hechas = {e["n"]: e for e in etapas(reg)}
    if any(e["estado"] == "revertida" for e in hechas.values()):
        return {"estado": "revertida"}
    actual = next((e for e in hechas.values() if e["estado"] == "en_ciclo"), None)
    out: dict = {"estado": "en_ciclo"}
    if actual is None:
        n = max(hechas, default=0) + 1
        if n > len(config_etapas):
            return {"estado": "terminada", "ultima": max(hechas, default=0)}
        _iniciar(reg, n, config_etapas[n - 1])
        return {"estado": "iniciada", "etapa": n}
    c = ciclo(actual, escritores, actividad)
    out["ciclo"] = c
    if c["limpio"]:
        reg.escribir("migracion.superada", ("migracion", actual["id"]), "planificador",
                     IDENTIDAD, f"puente:superada:{actual['id']}",
                     {"escritores": c["vistos"], "ciclo_desde": c["desde"]})
        out.update(estado="superada", etapa=actual["n"])
        if actual["n"] < len(config_etapas):
            _iniciar(reg, actual["n"] + 1, config_etapas[actual["n"]])
            out["siguiente"] = actual["n"] + 1
    return out


def _iniciar(reg, n: int, conf: dict) -> None:
    reg.escribir("migracion.iniciada", ("migracion", f"tablero-{n}"), "planificador", IDENTIDAD,
                 f"puente:iniciada:tablero-{n}", {"etapa": n, "repos": list(conf["repos"])})
