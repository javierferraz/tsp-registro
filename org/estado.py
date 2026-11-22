"""Estado calculado: aplica un evento ya validado al estado de su entidad.

Es una función pura sobre un diccionario, para que escribir un evento y recalcular desde cero
pasen por el mismo código y den lo mismo.
"""
from __future__ import annotations

import copy

from .esquemas import Conflicto, Tipo


def aplicar(actual: dict | None, ev: dict, tipo: Tipo) -> dict:
    """Devuelve el estado nuevo de la entidad. `actual` es None si aún no existe."""
    ent_id = ev["entidad"]["id"]
    if tipo.desde is None:
        if actual is not None:
            raise Conflicto(f"{ev['entidad']['tipo']} {ent_id} ya existe")
        nuevo = {"id": ent_id, "estado": tipo.hasta, "creada": ev["fecha"], "historial": []}
        nuevo.update(copy.deepcopy(ev["datos"]))
    else:
        if actual is None:
            raise Conflicto(f"{ev['entidad']['tipo']} {ent_id} no existe")
        if actual["estado"] not in tipo.desde:
            raise Conflicto(f"{ev['tipo']} no vale desde el estado «{actual['estado']}» "
                          f"(vale desde: {sorted(tipo.desde)})")
        nuevo = copy.deepcopy(actual)
        if tipo.hasta is not None:
            nuevo["estado"] = tipo.hasta

    nuevo["actualizada"] = ev["fecha"]
    if tipo.hasta is not None:
        nuevo["historial"].append({"fecha": ev["fecha"], "estado": tipo.hasta,
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
