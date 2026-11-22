"""Estado calculado: aplica un evento ya validado al estado de su entidad.

Es una función pura sobre un diccionario, para que escribir un evento y recalcular desde cero
pasen por el mismo código y den lo mismo.
"""
from __future__ import annotations

import copy

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
    if t == "tarea.asignada":
        nuevo["ejecucion"] = {"inicio": ev["fecha"], **d}
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.latido":
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.punto_control":
        nuevo["punto_control"] = {"fecha": ev["fecha"], **d}
        nuevo["ultimo_latido"] = ev["fecha"]
    elif t == "tarea.entregada":
        nuevo["entrega"] = {"fecha": ev["fecha"], **d}
    elif t == "tarea.devuelta":
        nuevo["devoluciones"] = nuevo.get("devoluciones", 0) + 1
    elif t == "decision.tomada":
        nuevo.setdefault("decisiones", []).append(
            {"fecha": ev["fecha"], "evento": ev["id"], "autor": ev["autor"], **d})
    elif t == "evidencia.verificada":
        nuevo.setdefault("evidencias", []).append(
            {"fecha": ev["fecha"], "evento": ev["id"], "autor": ev["autor"], **d})
    return nuevo


def _resumen(datos: dict) -> dict:
    return {"motivo": datos["motivo"]} if "motivo" in datos else {}
