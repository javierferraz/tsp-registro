"""Esquemas de evento: cabecera común, tipos, papeles que pueden escribirlos y transiciones.

Fuente: 2606-thestromboliproject/docs/organizacion/02-arquitectura.md §3.1 bis, §3.1 ter,
§3.3 y §9 bis. Si este fichero y ese documento discrepan, se corrige uno de los dos a la vez.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime


class Rechazo(Exception):
    """El evento no se acepta: esquema, papel o transición."""


class Conflicto(Rechazo):
    """El evento es correcto, pero no encaja en el estado (transición, clave o id repetidos).
    Pasa al juntar eventos de dos máquinas que no se habían visto: queda registrado y a la vista
    en rechazados, y no impide subir."""


# Papeles (02 §3.1 bis y §9 bis). La identidad concreta va aparte, en autor.identidad.
PAPELES = (
    "javier",        # su sesión de GitHub o la puerta con su confirmación
    "direccion",     # crea tareas a partir de un objetivo
    "planificador",  # asignación, reserva, caducidad
    "trabajador",    # latido, punto de control, entrega, decisión A/B, incidencia descubierta
    "validador",     # verificada, devuelta, integrada, evidencia verificada
    "laboratorio",   # lo mismo que el validador, para pruebas de interfaz
    "fabrica",       # ficha de release (P6-1; aún sin tipos)
    "motor",         # motor de eventos: crea tareas e incidencias por regla
)

ESTADOS_TAREA = (
    "propuesta", "aprobada", "lista", "en_curso", "en_pausa", "bloqueada", "caducada",
    "entregada", "devuelta", "verificada", "integrada", "cerrada", "descartada",
)

# Una especificación de campo es un tipo de Python o una tupla de valores admitidos.
Campos = dict


@dataclass(frozen=True)
class Version:
    obligatorios: Campos
    opcionales: Campos = field(default_factory=dict)


@dataclass(frozen=True)
class Tipo:
    entidad: str                  # tarea, objetivo, incidencia
    papeles: frozenset            # quién puede escribirlo
    desde: frozenset | None       # estados de origen; None = el evento crea la entidad
    hasta: str | None             # estado nuevo; None = no cambia el estado
    versiones: dict               # {version_esquema: Version}


def _t(entidad, papeles, desde, hasta, versiones):
    return Tipo(entidad, frozenset(papeles), None if desde is None else frozenset(desde),
                hasta, versiones)


_TAREA_V1 = Version(
    obligatorios={"titulo": str, "repo": str},
    opcionales={"tipo_trabajo": str, "objetivo": str, "alcance": list, "criterios": list,
                "no_objetivos": list, "dependencias": list, "tamano": ("S", "M", "L"),
                "modelo": str, "presupuesto": dict},
)
_MOTIVO = Version(obligatorios={"motivo": str})
_MOTIVO_OPC = Version(obligatorios={}, opcionales={"motivo": str})

TIPOS: dict[str, Tipo] = {
    # --- Objetivos -------------------------------------------------------------------
    "objetivo.creado": _t("objetivo", {"javier"}, None, "aprobado", {1: Version(
        obligatorios={"texto": str, "tipo": ("puntual", "permanente")},
        opcionales={"limites": dict, "prioridad": int})}),

    # --- Ciclo de vida de una tarea (02 §3.3) ----------------------------------------
    "tarea.propuesta": _t("tarea", {"javier", "direccion", "planificador", "motor"},
                          None, "propuesta", {1: _TAREA_V1}),
    "tarea.aprobada": _t("tarea", {"javier", "planificador"},
                         {"propuesta"}, "aprobada", {1: _MOTIVO_OPC}),
    "tarea.descartada": _t("tarea", {"javier", "direccion"},
                           {"propuesta", "aprobada", "lista"}, "descartada", {1: _MOTIVO}),
    "tarea.lista": _t("tarea", {"planificador"},
                      {"aprobada", "caducada", "devuelta", "en_pausa", "bloqueada"}, "lista",
                      {1: _MOTIVO_OPC}),
    "tarea.asignada": _t("tarea", {"planificador"}, {"lista"}, "en_curso", {1: Version(
        obligatorios={"ejecutor": str},
        opcionales={"maquina": str, "rama": str, "modelo": str})}),
    "tarea.pausada": _t("tarea", {"trabajador", "planificador", "javier"},
                        {"en_curso"}, "en_pausa", {1: _MOTIVO_OPC}),
    "tarea.bloqueada": _t("tarea", {"trabajador", "planificador", "validador"},
                          {"en_curso", "entregada"}, "bloqueada", {1: _MOTIVO}),
    "tarea.caducada": _t("tarea", {"planificador"}, {"en_curso"}, "caducada", {1: _MOTIVO_OPC}),
    "tarea.entregada": _t("tarea", {"trabajador"}, {"en_curso"}, "entregada", {1: Version(
        obligatorios={"resumen": str},
        opcionales={"rama": str, "commits": list, "evidencias": list})}),
    "tarea.verificada": _t("tarea", {"validador", "laboratorio"},
                           {"entregada"}, "verificada", {1: _MOTIVO_OPC}),
    "tarea.devuelta": _t("tarea", {"validador", "laboratorio"},
                         {"entregada", "verificada"}, "devuelta", {1: _MOTIVO}),
    "tarea.integrada": _t("tarea", {"validador", "laboratorio"},
                          {"verificada"}, "integrada", {1: Version(
                              obligatorios={}, opcionales={"commit": str, "release": str})}),
    "tarea.cerrada": _t("tarea", {"validador", "javier"}, {"integrada"}, "cerrada",
                        {1: _MOTIVO_OPC}),

    # --- Registros sobre una tarea que no cambian su estado ---------------------------
    "tarea.latido": _t("tarea", {"trabajador"}, {"en_curso"}, None,
                       {1: Version(obligatorios={})}),
    "tarea.punto_control": _t("tarea", {"trabajador"}, {"en_curso"}, None,
                              {1: Version(obligatorios={"resumen": str},
                                          opcionales={"ruta": str})}),
    "decision.tomada": _t("tarea", {"trabajador", "javier"},
                          set(ESTADOS_TAREA) - {"cerrada", "descartada"}, None, {1: Version(
                              obligatorios={"clase": ("A", "B", "C"), "elegida": str,
                                            "motivo": str},
                              opcionales={"opciones": list, "descartada": str,
                                          "vetable": bool})}),
    "evidencia.verificada": _t("tarea", {"validador", "laboratorio"},
                               {"entregada", "verificada", "integrada"}, None, {1: Version(
                                   obligatorios={"tipo": str, "ruta": str, "hash": str},
                                   opcionales={"descripcion": str})}),

    # --- Incidencias -------------------------------------------------------------------
    "incidencia.abierta": _t("incidencia",
                             {"trabajador", "validador", "laboratorio", "motor", "javier"},
                             None, "abierta", {1: Version(
                                 obligatorios={"origen": str, "gravedad": ("baja", "media",
                                                                           "alta", "critica"),
                                               "descripcion": str},
                                 opcionales={"app": str, "version": str, "huella": str,
                                             "tareas": list})}),
}

# Reglas que dependen de los datos, no solo del tipo.
def _regla_decision_c(ev):
    if ev["datos"].get("clase") == "C" and ev["autor"]["papel"] != "javier":
        raise Rechazo("una decisión de clase C solo la escribe el papel javier")


REGLAS_EXTRA = {"decision.tomada": [_regla_decision_c]}

CABECERA = ("id", "tipo", "version_esquema", "fecha", "autor", "entidad", "correlacion",
            "clave_idempotencia", "datos")
_RE_ID = re.compile(r"^[0-9a-f]{16}$")
_RE_ENTIDAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RE_FECHA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")


def _comprobar_campo(nombre, valor, spec):
    if isinstance(spec, tuple):
        if valor not in spec:
            raise Rechazo(f"datos.{nombre}: «{valor}» no es uno de {list(spec)}")
    elif spec is int:
        if not isinstance(valor, int) or isinstance(valor, bool):
            raise Rechazo(f"datos.{nombre}: tiene que ser entero")
    elif not isinstance(valor, spec):
        raise Rechazo(f"datos.{nombre}: tiene que ser {spec.__name__}")
    if spec is str and not valor.strip():
        raise Rechazo(f"datos.{nombre}: vacío")


def validar(ev: dict) -> Tipo:
    """Comprueba cabecera, versión, papel y datos. No mira el estado (eso es estado.aplicar)."""
    if not isinstance(ev, dict):
        raise Rechazo("el evento no es un objeto JSON")
    faltan = [k for k in CABECERA if k not in ev]
    sobran = [k for k in ev if k not in CABECERA]
    if faltan or sobran:
        raise Rechazo(f"cabecera: faltan {faltan} / sobran {sobran}")
    if not isinstance(ev["id"], str) or not _RE_ID.match(ev["id"]):
        raise Rechazo("id: 16 caracteres hexadecimales")
    tipo = TIPOS.get(ev["tipo"]) if isinstance(ev["tipo"], str) else None
    if tipo is None:
        raise Rechazo(f"tipo desconocido: {ev['tipo']!r}")
    v = ev["version_esquema"]
    version = tipo.versiones.get(v) if type(v) is int else None
    if version is None:
        raise Rechazo(f"{ev['tipo']}: versión de esquema {ev['version_esquema']!r} no admitida "
                      f"(admitidas: {sorted(tipo.versiones)})")
    if not isinstance(ev["fecha"], str) or not _RE_FECHA.match(ev["fecha"]):
        raise Rechazo("fecha: formato AAAA-MM-DDTHH:MM:SS.ffffffZ (UTC)")
    try:
        datetime.strptime(ev["fecha"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        raise Rechazo(f"fecha imposible: {ev['fecha']}") from None
    autor = ev["autor"]
    if not isinstance(autor, dict) or set(autor) != {"papel", "identidad"}:
        raise Rechazo("autor: {papel, identidad}")
    if not isinstance(autor["papel"], str) or autor["papel"] not in PAPELES:
        raise Rechazo(f"papel desconocido: {autor['papel']!r}")
    if not isinstance(autor["identidad"], str) or not autor["identidad"].strip():
        raise Rechazo("autor.identidad vacía")
    if autor["papel"] not in tipo.papeles:
        raise Rechazo(f"el papel {autor['papel']} no puede escribir {ev['tipo']} "
                      f"(pueden: {sorted(tipo.papeles)})")
    ent = ev["entidad"]
    if not isinstance(ent, dict) or set(ent) != {"tipo", "id"}:
        raise Rechazo("entidad: {tipo, id}")
    if ent["tipo"] != tipo.entidad:
        raise Rechazo(f"{ev['tipo']} va sobre una entidad {tipo.entidad}, no {ent['tipo']}")
    if not isinstance(ent["id"], str) or not _RE_ENTIDAD.match(ent["id"]):
        raise Rechazo("entidad.id: letras, cifras, . _ - (hasta 64)")
    if ev["correlacion"] is not None and (not isinstance(ev["correlacion"], str)
                                          or not _RE_ID.match(ev["correlacion"])):
        raise Rechazo("correlacion: null o el id de otro evento")
    clave = ev["clave_idempotencia"]
    if not isinstance(clave, str) or not clave.strip() or len(clave) > 200:
        raise Rechazo("clave_idempotencia: texto de 1 a 200 caracteres")
    datos = ev["datos"]
    if not isinstance(datos, dict):
        raise Rechazo("datos: objeto")
    for k, spec in version.obligatorios.items():
        if k not in datos:
            raise Rechazo(f"datos.{k}: obligatorio en {ev['tipo']}")
        _comprobar_campo(k, datos[k], spec)
    for k, v in datos.items():
        spec = version.obligatorios.get(k, version.opcionales.get(k))
        if spec is None:
            raise Rechazo(f"datos.{k}: campo no previsto en {ev['tipo']} v{ev['version_esquema']}")
        _comprobar_campo(k, v, spec)
    for regla in REGLAS_EXTRA.get(ev["tipo"], ()):
        regla(ev)
    return tipo


def huella(ev: dict) -> str:
    """Lo que tiene que coincidir para que dos eventos con la misma clave sean «el mismo»."""
    base = {k: ev[k] for k in ("tipo", "version_esquema", "autor", "entidad", "correlacion",
                               "datos")}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()
