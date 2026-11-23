"""Reserva de recursos entre máquinas (02 §5.1; PAA2 P4-1, S-1269).

Recursos con nombre (`repo:<slug>:main`, `manifest`, `ftp`, `firma`, `maquina-linux:pantalla`,
`cuota:opus`). Cada petición es una entidad `reserva` (R-…) con su plazo; caduca sola.

**Gana siempre la más antigua tras sincronizar**, y «antigua» es la que **entró antes en la
historia compartida** (el orden de los commits tras subir), no la de fecha más temprana:

- Con la fecha, una máquina con el reloj atrasado, o que sube tarde, puede tener la reserva
  «más antigua» y llegar cuando la otra ya ha comprobado que estaba sola: las dos creerían
  haber ganado.
- Con la historia, quien sube primero queda delante para siempre. El que llega después tiene
  que traerse lo del otro para poder subir (`pull --rebase`), y al volver a leer ya lo ve
  delante: se retira. Todas las máquinas acaban viendo el mismo orden.

Paso a paso (`reservar`): traer lo nuevo; si el índice ya dice que otro la tiene, «ocupado» sin
escribir nada; si no, escribir la petición, subir, volver a leer y, si hay otra viva delante en
la historia, escribir la retirada. En la misma máquina, el índice (con su transacción) ya impide
dos peticiones vivas del mismo recurso.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from .esquemas import Rechazo
from .estado import _FMT
from .registro import ruta_relativa

RESERVA_MINUTOS = 30  # plazo por defecto; se renueva con `renovar`


def _ahora(ahora):
    return ahora or datetime.now(timezone.utc)


def _viva(r: dict, recurso: str | None, fecha: str) -> bool:
    return (r["estado"] == "activa" and r["caduca"] > fecha
            and (recurso is None or r["recurso"] == recurso))


def vivas(reg, recurso: str | None, ahora=None) -> list[dict]:
    """Reservas activas y sin caducar (de un recurso, o de todos con None)."""
    fecha = _ahora(ahora).strftime(_FMT)
    return [r for r in reg.entidades("reserva").values() if _viva(r, recurso, fecha)]


def _fichero(r: dict) -> str:
    return ruta_relativa({"fecha": r["creada"], "id": r["evento"]})


def ordenar(reg, reservas: list[dict]) -> list[dict]:
    """Por orden de entrada en la historia; lo no subido, detrás; desempate por (fecha, id)."""
    pos = reg.orden_historia([_fichero(r) for r in reservas])
    return sorted(reservas, key=lambda r: (pos.get(_fichero(r), float("inf")),
                                           r["creada"], r["id"]))


def titular(reg, recurso: str, ahora=None) -> dict | None:
    v = vivas(reg, recurso, ahora)
    return ordenar(reg, v)[0] if v else None


def pedir(reg, recurso: str, identidad: str, papel="trabajador", minutos=RESERVA_MINUTOS,
          tarea=None, maquina=None, ahora=None) -> dict:
    """Escribe la petición si el índice no conoce otra viva del recurso. No toca git."""
    ahora = _ahora(ahora)
    fecha = ahora.strftime(_FMT)

    def paso(db):
        activas = [json.loads(js) for (js,) in db.execute(
            "SELECT json FROM entidades WHERE tipo='reserva' AND estado='activa'")]
        vivas_ = [r for r in activas if _viva(r, recurso, fecha)]
        mia = next((r for r in vivas_ if r["titular"] == identidad), None)
        if mia:
            return {"estado": "ya", "reserva": mia["id"]}
        if vivas_:
            return {"estado": "ocupado"}
        rid = "R-" + secrets.token_hex(6)
        datos = {"recurso": recurso, "minutos": minutos}
        datos.update({k: v for k, v in (("tarea", tarea), ("maquina", maquina)) if v})
        reg._escribir_en(db, "reserva.pedida", ("reserva", rid), papel, identidad,
                         f"{rid}:pedida", datos, None, 1, ahora)
        return {"estado": "pedida", "reserva": rid}

    return reg._tx(paso)


def _retirar(reg, rid, identidad, papel, motivo, gana=None, ahora=None):
    datos = {"motivo": motivo, **({"gana": gana} if gana else {})}
    return reg.escribir("reserva.retirada", ("reserva", rid), papel, identidad,
                        f"{rid}:retirada", datos, ahora=ahora)


def _subir(reg):
    try:
        reg.subir()
    except Rechazo:
        pass  # la retirada sale con la siguiente subida; mientras, el otro ya va delante


def confirmar(reg, rid: str, identidad: str, papel="trabajador", ahora=None,
              subir=True) -> dict:
    """Sube la petición y vuelve a leer: si otra viva del mismo recurso entró antes, se retira."""
    if subir:
        try:
            reg.subir()
        except Rechazo as e:  # sin subir no hay reserva: nadie más la ve
            _retirar(reg, rid, identidad, papel, "sin confirmar: no se pudo subir", ahora=ahora)
            return {"ok": False, "estado": "sin_confirmar", "reserva": rid, "motivo": str(e)}
    r = reg.ver("reserva", rid)
    if r["estado"] != "activa":
        return {"ok": False, "estado": r["estado"], "reserva": rid}
    gana = titular(reg, r["recurso"], ahora)
    if gana and gana["id"] == rid:
        return {"ok": True, "estado": "ganada", "reserva": rid, "recurso": r["recurso"],
                "caduca": r["caduca"]}
    motivo = "gana la reserva que entró antes" if gana else "caducada antes de confirmarse"
    _retirar(reg, rid, identidad, papel, motivo, gana and gana["id"], ahora)
    if subir:
        _subir(reg)
    return {"ok": False, "estado": "retirada", "reserva": rid, "recurso": r["recurso"],
            "gana": gana and gana["id"], "titular": gana and gana["titular"]}


def reservar(reg, recurso: str, identidad: str, papel="trabajador", minutos=RESERVA_MINUTOS,
             tarea=None, maquina=None, ahora=None, subir=True) -> dict:
    """La orden completa. ok=True solo si la reserva ha sobrevivido a la subida."""
    if subir:
        reg.traer()
    p = pedir(reg, recurso, identidad, papel, minutos, tarea, maquina, ahora)
    if p["estado"] == "ocupado":
        t = titular(reg, recurso, ahora)
        return {"ok": False, "estado": "ocupado", "recurso": recurso,
                "gana": t and t["id"], "titular": t and t["titular"],
                "caduca": t and t["caduca"]}
    return confirmar(reg, p["reserva"], identidad, papel, ahora, subir)


def liberar(reg, rid: str, identidad: str, papel="trabajador", motivo=None, ahora=None,
            subir=True) -> dict:
    r = reg.escribir("reserva.liberada", ("reserva", rid), papel, identidad, f"{rid}:liberada",
                     {"motivo": motivo} if motivo else {}, ahora=ahora)
    if subir:
        _subir(reg)
    return {"ok": True, "reserva": rid, "id": r.id}


def renovar(reg, rid: str, identidad: str, papel="trabajador", minutos=RESERVA_MINUTOS,
            ahora=None, subir=True) -> dict:
    ahora = _ahora(ahora)
    reg.escribir("reserva.renovada", ("reserva", rid), papel, identidad,
                 f"{rid}:renovada:{ahora:%Y%m%dT%H%M%S%f}", {"minutos": minutos}, ahora=ahora)
    if subir:
        _subir(reg)
    return {"ok": True, "reserva": rid, "caduca": reg.ver("reserva", rid)["caduca"]}


def resumen(reg, ahora=None) -> list[dict]:
    """Quién tiene cada recurso ahora y cuántas peticiones vivas esperan detrás."""
    por_recurso: dict[str, list] = {}
    for r in vivas(reg, None, ahora):
        por_recurso.setdefault(r["recurso"], []).append(r)
    out = []
    for recurso, rs in sorted(por_recurso.items()):
        primero = ordenar(reg, rs)[0]
        out.append({"recurso": recurso, "titular": primero["titular"],
                    "reserva": primero["id"], "caduca": primero["caduca"],
                    "tarea": primero.get("tarea"), "detras": len(rs) - 1})
    return out
