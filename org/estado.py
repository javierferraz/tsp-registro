"""Estado calculado: aplica un evento ya validado al estado de su entidad.

Es una función pura sobre un diccionario, para que escribir un evento y recalcular desde cero
pasen por el mismo código y den lo mismo.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta

from .esquemas import CUALQUIERA, ESTADO_EN_DATOS, Conflicto, Tipo


def aplicar(actual: dict | None, ev: dict, tipo: Tipo) -> dict:
    """Devuelve el estado nuevo de la entidad. `actual` es None si aún no existe."""
    ent_id = ev["entidad"]["id"]
    hasta = ev["datos"]["estado"] if tipo.hasta == ESTADO_EN_DATOS else tipo.hasta
    if tipo.desde is None:
        if actual is not None:
            raise Conflicto(f"{ev['entidad']['tipo']} {ent_id} ya existe")
        nuevo = {"id": ent_id, "estado": hasta, "creada": ev["fecha"], "historial": []}
        if not tipo.espejo:
            nuevo.update(copy.deepcopy(ev["datos"]))
    else:
        if actual is None:
            raise Conflicto(f"{ev['entidad']['tipo']} {ent_id} no existe")
        if CUALQUIERA not in tipo.desde and actual["estado"] not in tipo.desde:
            raise Conflicto(f"{ev['tipo']} no vale desde el estado «{actual['estado']}» "
                          f"(vale desde: {sorted(tipo.desde)})")
        nuevo = copy.deepcopy(actual)
        if hasta is not None:
            nuevo["estado"] = hasta
    if tipo.espejo:  # la copia entera del origen, sustituida en cada evento
        nuevo["datos"] = copy.deepcopy(ev["datos"])

    nuevo["actualizada"] = ev["fecha"]
    if hasta is not None:
        nuevo["historial"].append({"fecha": ev["fecha"], "estado": hasta,
                                   "evento": ev["id"], "tipo": ev["tipo"],
                                   "autor": ev["autor"], **_resumen(ev["datos"])})

    t, d = ev["tipo"], ev["datos"]
    if t in _DE_OTRO and actual:  # 02 §10 y 03 §10: quien implementa no verifica ni revisa
        autores = {(actual.get("ejecucion") or {}).get("ejecutor"), actual.get("entregada_por")}
        if ev["autor"]["identidad"] in autores - {None}:
            raise Conflicto(f"{t}: {ev['autor']['identidad']} hizo la tarea; quien implementa "
                            f"no verifica (02 §10)")
    if ev["version_esquema"] >= 2 and t in _DEL_EJECUTOR:
        ejecutor = (actual or {}).get("ejecucion", {}).get("ejecutor")
        if ejecutor and ejecutor != ev["autor"]["identidad"]:
            raise Conflicto(f"{t}: la tarea la lleva {ejecutor}, no "
                            f"{ev['autor']['identidad']} (una sola ejecución viva, 02 §8.3)")
    if t == "tarea.asignada":
        if "ejecucion" in nuevo:  # relevo: la ejecución anterior queda a la vista
            nuevo.setdefault("ejecuciones_anteriores", []).append(
                {**nuevo["ejecucion"], "fin": ev["fecha"]})
        nuevo["ejecucion"] = {"inicio": ev["fecha"], **d}
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.latido":
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.punto_control":
        nuevo["punto_control"] = {"fecha": ev["fecha"], "autor": ev["autor"]["identidad"], **d}
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.valorada":  # S-1272: la última valoración manda; el historial está en eventos
        nuevo["valoracion"] = {"fecha": ev["fecha"], "autor": ev["autor"], **d}
    elif t == "tarea.entregada":
        nuevo["entrega"] = {"fecha": ev["fecha"], **d}
        nuevo["entregada_por"] = ev["autor"]["identidad"]
    elif t == "revision.emitida":
        nuevo.setdefault("revisiones", []).append(
            {"fecha": ev["fecha"], "evento": ev["id"], "autor": ev["autor"], **d})
    elif t in ("tarea.verificada", "tarea.devuelta"):
        if ev["version_esquema"] >= 2:
            nuevo["veredicto"] = {"fecha": ev["fecha"], "evento": ev["id"], "tipo": t,
                                  "autor": ev["autor"], **d}
        if t == "tarea.devuelta":
            nuevo["devoluciones"] = nuevo.get("devoluciones", 0) + 1
    elif t.startswith("reserva."):
        _reserva(actual, nuevo, ev)
    elif t == "decision.tomada":
        nuevo.setdefault("decisiones", []).append(
            {"fecha": ev["fecha"], "evento": ev["id"], "autor": ev["autor"], **d})
    elif t == "migracion.discrepancia":  # P4-3: reinicia el ciclo de la etapa (puente.py)
        nuevo.setdefault("discrepancias", []).append({"fecha": ev["fecha"], **d})
    elif t == "evidencia.verificada":
        nuevo.setdefault("evidencias", []).append(
            {"fecha": ev["fecha"], "evento": ev["id"], "autor": ev["autor"], **d})
    if t in _DEL_EJECUTOR and d.get("ficheros"):  # la lista de ficheros tocados, al día
        nuevo["ficheros"] = sorted(set(nuevo.get("ficheros", [])) | set(d["ficheros"]))
    return nuevo


# Con la v2 solo los escribe quien lleva la tarea: un trabajador que vuelve tras caducar no
# pisa el relevo.
_DEL_EJECUTOR = ("tarea.latido", "tarea.punto_control", "tarea.entregada")
# Los escribe otra ejecución: nunca el ejecutor ni quien entregó (S-1270).
_DE_OTRO = ("tarea.verificada", "evidencia.verificada", "revision.emitida")
_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def caduca(fecha: str, minutos: int) -> str:
    return (datetime.strptime(fecha, _FMT) + timedelta(minutes=minutos)).strftime(_FMT)


def _reserva(actual, nuevo, ev) -> None:
    t, quien = ev["tipo"], ev["autor"]
    if t == "reserva.pedida":
        nuevo.update(titular=quien["identidad"], evento=ev["id"],
                     caduca=caduca(ev["fecha"], ev["datos"]["minutos"]))
        return
    if quien["papel"] == "trabajador" and quien["identidad"] != actual["titular"]:
        raise Conflicto(f"{t}: la reserva es de {actual['titular']}, no de {quien['identidad']}")
    if t == "reserva.renovada":
        if ev["fecha"] > actual["caduca"]:  # caducada: otro puede tenerla ya; no se resucita
            raise Conflicto(f"reserva.renovada: caducó el {actual['caduca']}")
        nuevo.update(minutos=ev["datos"]["minutos"],
                     caduca=caduca(ev["fecha"], ev["datos"]["minutos"]))


def _resumen(datos: dict) -> dict:
    return {"motivo": datos["motivo"]} if "motivo" in datos else {}
