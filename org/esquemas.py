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


class SinSello(Rechazo):
    """El evento es de un papel sellado (validador, laboratorio, javier) y su commit no lleva la
    firma de ese papel (sello.py). Queda en rechazados con clase «sello»."""


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
    "importador",    # fase 2 (S-1265): copia el tablero, Lion y los vigías; solo tipos espejo
    "revisor",       # revisión independiente (03 §10, S-1270): otra ejecución, nunca el autor
)

# Estado de una entidad espejo: lo dice el origen (columna del tablero, estado de Lion), no
# una transición del ciclo. «*» en `desde` = vale desde cualquier estado existente.
ESTADO_EN_DATOS = "=datos.estado"
CUALQUIERA = "*"
# «+» en `desde` = si la entidad aún no existe, este evento la crea (S-1273: el latido de un
# proceso y la cuenta de una regla no tienen un evento propio de alta).
NUEVA = "+"

ESTADOS_TAREA = (
    "propuesta", "aprobada", "lista", "en_curso", "en_pausa", "bloqueada", "caducada",
    "entregada", "devuelta", "verificada", "integrada", "cerrada", "descartada",
)

# Una especificación de campo es un tipo de Python, una tupla de valores admitidos, una
# Version (objeto anidado con sus campos) o un ListaDe (lista cuyos elementos cumplen otra
# especificación). Lo anidado entra con la v2 del contrato de trabajo (S-1269).
Campos = dict


@dataclass(frozen=True)
class Version:
    obligatorios: Campos
    opcionales: Campos = field(default_factory=dict)


@dataclass(frozen=True)
class ListaDe:
    elemento: object


@dataclass(frozen=True)
class Tipo:
    entidad: str                  # tarea, objetivo, incidencia
    papeles: frozenset            # quién puede escribirlo
    desde: frozenset | None       # estados de origen; None = el evento crea la entidad
    hasta: str | None             # estado nuevo; None = no cambia el estado
    versiones: dict               # {version_esquema: Version}
    espejo: bool = False          # copia de otra fuente: guarda sus datos en entidad["datos"]


def _t(entidad, papeles, desde, hasta, versiones, espejo=False):
    return Tipo(entidad, frozenset(papeles), None if desde is None else frozenset(desde),
                hasta, versiones, espejo)


_TAREA_V1 = Version(
    obligatorios={"titulo": str, "repo": str},
    opcionales={"tipo_trabajo": str, "objetivo": str, "alcance": list, "criterios": list,
                "no_objetivos": list, "dependencias": list, "tamano": ("S", "M", "L"),
                "modelo": str, "presupuesto": dict},
)
_TARJETA_V1 = Version(  # una tarjeta de tablero/board.json
    obligatorios={"estado": str, "revision": int, "creada": str, "actualizada": str,
                  "huella_texto": str, "notas": int},
    opcionales={"app": str, "etiquetas": list, "prioridad": str, "check": str, "modelo": str,
                "bloqueada_por": list, "commits": list, "movimientos": list},
)
_EJECUCION_V1 = Version(  # una carpeta de tarea de Roaring Lion (task.json y report.json)
    obligatorios={"estado": str, "creada": str, "actualizada": str},
    opcionales={"tarjeta": str, "repo": str, "tamano": str, "modelo": str, "esfuerzo": str,
                "intentos": int, "minutos": int, "verificacion": str, "criterios": dict,
                "commits": int, "rama": str, "subida": bool, "con_informe": bool},
)
_INFORME_V1 = Version(  # el último informe de un vigía: solo cifras
    obligatorios={"vigia": str, "fecha": str, "ruta": str, "sha256": str},
    opcionales={"cifras": dict},
)
_MOTIVO = Version(obligatorios={"motivo": str})
_MOTIVO_OPC = Version(obligatorios={}, opcionales={"motivo": str})

# --- Contrato de trabajo ampliado (02 §4 y §8.3; S-1269) ------------------------------
# Un solo esquema de decisión: el de decision.tomada, también dentro del informe de entrega.
_DECISION = Version(
    obligatorios={"clase": ("A", "B", "C"), "elegida": str, "motivo": str},
    opcionales={"opciones": list, "descartada": str, "vetable": bool})
_FICHEROS = ListaDe(str)
_LATIDO_V2 = Version(obligatorios={}, opcionales={"ficheros": _FICHEROS})
_PUNTO_CONTROL_V2 = Version(  # lo que necesita otro trabajador para seguir donde se cortó
    obligatorios={"hecho": str, "sigue": str, "ficheros": _FICHEROS},
    opcionales={"rama": str, "commit": str, "a_medias": _FICHEROS})
_INFORME_V2 = Version(  # el informe de entrega, sin campos libres
    obligatorios={
        "resumen": str,
        "criterios": ListaDe(Version(
            obligatorios={"n": int, "estado": ("ok", "falla", "no_ejecutado")},
            opcionales={"evidencia": str, "motivo": str})),
        "ficheros": _FICHEROS,
        "commits": ListaDe(str),
        "decisiones": ListaDe(_DECISION),
        "descubierto": ListaDe(str),
        "coste": Version(obligatorios={"minutos": int},
                         opcionales={"tokens": int, "turnos": int}),
    },
    opcionales={"rama": str})
_RESERVA_V1 = Version(obligatorios={"recurso": str, "minutos": int},
                      opcionales={"tarea": str, "maquina": str})

# --- Validador central (02 §10, 03 §2 y §10; S-1270) ------------------------------------
# Tipos de trabajo que el validador sabe validar hoy: código y web (más interfaz, que es código
# con medida visual). Contenido y documentación llegarán con sus comprobaciones.
TIPOS_TRABAJO = ("codigo", "interfaz", "web", "contenido", "documentacion")
_TAREA_V2 = Version(
    obligatorios={"titulo": str, "repo": str, "tipo_trabajo": TIPOS_TRABAJO},
    opcionales={**{k: v for k, v in _TAREA_V1.opcionales.items() if k != "tipo_trabajo"},
                "toca_interfaz": bool,  # código que cambia lo que se ve
                "publica": bool,        # la entrega publica algo (web, manifest…)
                "rama_base": str,       # contra qué se compara (por defecto, la del remoto)
                "urls": ListaDe(Version(obligatorios={"url": str},
                                        opcionales={"contiene": str}))})
_COMPROBACION = Version(obligatorios={"nombre": str,
                                      "resultado": ("ok", "falla", "no_verificado")},
                        opcionales={"detalle": str})
_COSTE_VALIDADOR = Version(obligatorios={"segundos": int},
                           opcionales={"runner": str, "run": str, "minutos_facturables": int})
_VEREDICTO = {"politica": str, "cabeza": str, "comprobaciones": ListaDe(_COMPROBACION),
              "coste": _COSTE_VALIDADOR}
_VERIFICADA_V2 = Version(obligatorios=_VEREDICTO,
                         opcionales={"motivo": str, "no_verificado": ListaDe(str)})
_DEVUELTA_V2 = Version(obligatorios={**_VEREDICTO, "motivo": str},
                       opcionales={"no_verificado": ListaDe(str)})
_REVISION_V1 = Version(  # el veredicto de otra ejecución sobre una cabeza concreta
    obligatorios={"veredicto": ("ok", "changes", "reject"), "cabeza": str},
    opcionales={"hallazgos": int, "ruta": str, "hash": str})

# --- Valoración para el planificador (02 §5.0; S-1272) ---------------------------------
# Siete notas de 1 a 5, cada una con su frase de motivo. La pone la dirección (con modelo) al
# crear o revisar la tarea; el planificador, sin modelo, solo la lee.
NOTAS = ("importancia", "urgencia", "riesgo", "esfuerzo", "coste", "probabilidad", "comprobable")
# Clases que pasan siempre delante (02 §5.0): app que no abre o no instala, enlace de compra o
# de descarga roto, fallo de integridad, fallo que varios clientes han reportado.
CLASES_PRIORITARIAS = ("no_abre", "enlace_roto", "integridad", "queja_repetida")
_NOTA = Version(obligatorios={"n": int, "motivo": str})
_VALORACION_V1 = Version(
    obligatorios={"notas": Version(obligatorios={k: _NOTA for k in NOTAS})},
    opcionales={"clase": CLASES_PRIORITARIAS,
                "huella": str,                 # misma app, mismo componente, mismo síntoma
                "recursos": ListaDe(str),      # recursos con nombre que necesita (02 §5.1)
                "espera_javier": bool,         # acabará esperando a Javier (02 §5.2)
                "fija": Version(obligatorios={"valor": int, "motivo": str})})  # a mano

TIPOS: dict[str, Tipo] = {
    # --- Objetivos -------------------------------------------------------------------
    "objetivo.creado": _t("objetivo", {"javier"}, None, "aprobado", {1: Version(
        obligatorios={"texto": str, "tipo": ("puntual", "permanente")},
        opcionales={"limites": dict, "prioridad": int})}),

    # --- Ciclo de vida de una tarea (02 §3.3) ----------------------------------------
    "tarea.propuesta": _t("tarea", {"javier", "direccion", "planificador", "motor"},
                          None, "propuesta", {1: _TAREA_V1, 2: _TAREA_V2}),
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
    # S-1273: el motor pausa la ejecución que pasa de su presupuesto y bloquea la tarea devuelta
    # tres veces (02 §8.4, §9 y §9 ter); por eso bloqueada vale también desde devuelta.
    "tarea.pausada": _t("tarea", {"trabajador", "planificador", "javier", "motor"},
                        {"en_curso"}, "en_pausa", {1: _MOTIVO_OPC}),
    "tarea.bloqueada": _t("tarea", {"trabajador", "planificador", "validador", "motor"},
                          {"en_curso", "entregada", "devuelta"}, "bloqueada", {1: _MOTIVO}),
    "tarea.caducada": _t("tarea", {"planificador"}, {"en_curso"}, "caducada", {1: _MOTIVO_OPC}),
    "tarea.entregada": _t("tarea", {"trabajador"}, {"en_curso"}, "entregada", {1: Version(
        obligatorios={"resumen": str},
        opcionales={"rama": str, "commits": list, "evidencias": list}), 2: _INFORME_V2}),
    "tarea.verificada": _t("tarea", {"validador", "laboratorio"},
                           {"entregada"}, "verificada", {1: _MOTIVO_OPC, 2: _VERIFICADA_V2}),
    "tarea.devuelta": _t("tarea", {"validador", "laboratorio"},
                         {"entregada", "verificada"}, "devuelta", {1: _MOTIVO, 2: _DEVUELTA_V2}),
    "tarea.integrada": _t("tarea", {"validador", "laboratorio"},
                          {"verificada"}, "integrada", {1: Version(
                              obligatorios={}, opcionales={"commit": str, "release": str})}),
    "tarea.cerrada": _t("tarea", {"validador", "javier"}, {"integrada"}, "cerrada",
                        {1: _MOTIVO_OPC}),
    # P4-3 (S-1271): una tarea cerrada o descartada que se vuelve a abrir (en el tablero, una
    # tarjeta que sale de Terminada o Descartada) vuelve a empezar como propuesta.
    "tarea.reabierta": _t("tarea", {"javier", "direccion", "planificador"},
                          {"cerrada", "descartada"}, "propuesta", {1: _MOTIVO}),

    # --- Registros sobre una tarea que no cambian su estado ---------------------------
    "tarea.latido": _t("tarea", {"trabajador"}, {"en_curso"}, None,
                       {1: Version(obligatorios={}), 2: _LATIDO_V2}),
    "tarea.punto_control": _t("tarea", {"trabajador"}, {"en_curso"}, None,
                              {1: Version(obligatorios={"resumen": str},
                                          opcionales={"ruta": str}), 2: _PUNTO_CONTROL_V2}),
    "tarea.valorada": _t("tarea", {"direccion", "javier", "motor"},
                         {"propuesta", "aprobada", "lista", "en_pausa", "bloqueada",
                          "caducada", "devuelta"}, None, {1: _VALORACION_V1}),
    "decision.tomada": _t("tarea", {"trabajador", "javier"},
                          set(ESTADOS_TAREA) - {"cerrada", "descartada"}, None,
                          {1: _DECISION}),
    "evidencia.verificada": _t("tarea", {"validador", "laboratorio"},
                               {"entregada", "verificada", "integrada"}, None, {1: Version(
                                   obligatorios={"tipo": str, "ruta": str, "hash": str},
                                   opcionales={"descripcion": str})}),
    "revision.emitida": _t("tarea", {"revisor"}, {"entregada"}, None, {1: _REVISION_V1}),

    # --- Incidencias -------------------------------------------------------------------
    "incidencia.abierta": _t("incidencia",
                             {"trabajador", "validador", "laboratorio", "motor", "javier"},
                             None, "abierta", {1: Version(
                                 obligatorios={"origen": str, "gravedad": ("baja", "media",
                                                                           "alta", "critica"),
                                               "descripcion": str},
                                 opcionales={"app": str, "version": str, "huella": str,
                                             "tareas": list})}),

    # --- Reserva de recursos entre máquinas (02 §5.1; S-1269) --------------------------
    # Una entidad por petición (R-…), no por recurso: el ganador lo decide el orden de la
    # historia compartida (recursos.py), no el estado calculado.
    "reserva.pedida": _t("reserva", {"trabajador", "planificador"}, None, "activa",
                         {1: _RESERVA_V1}),
    "reserva.renovada": _t("reserva", {"trabajador", "planificador"}, {"activa"}, None,
                           {1: Version(obligatorios={"minutos": int})}),
    "reserva.liberada": _t("reserva", {"trabajador", "planificador", "javier"}, {"activa"},
                           "liberada", {1: _MOTIVO_OPC}),
    "reserva.retirada": _t("reserva", {"trabajador", "planificador"}, {"activa"}, "retirada",
                           {1: Version(obligatorios={"motivo": str},
                                       opcionales={"gana": str})}),

    # --- Interruptor de pausa (02 §9, S-1268) --------------------------------------------
    # Cada pausa es su propia entidad (pausa:<id>): se activa y se retira una vez. Hay pausa
    # mientras quede alguna «activa» cuyo ámbito alcance a lo que se mira (pausa.py, S-1273).
    # Pedirla puede cualquiera (02 §9 bis); retirarla, solo Javier.
    "pausa.activada": _t("pausa", set(PAPELES) - {"importador", "revisor"}, None, "activa",
                         {1: Version(
        obligatorios={"nivel": ("suave", "dura", "emergencia"), "origen": str},
        opcionales={"ambito": str, "motivo": str})}),
    "pausa.retirada": _t("pausa", {"javier"}, {"activa"}, "retirada", {1: _MOTIVO_OPC}),

    # --- Latidos de los procesos programados (02 §8.2; S-1273) ---------------------------
    # Un proceso (proceso:<nombre> de politica/procesos.json) deja su latido al acabar, o se lo
    # copia el registro diario desde GitHub o el Programador de tareas. El vigilante (papel
    # motor) escribe la alerta cuando falta o falla; una sola por cada último latido visto.
    "proceso.latido": _t("proceso", {"importador", "planificador", "motor", "validador",
                                     "fabrica"}, {NUEVA, CUALQUIERA}, None, {1: Version(
        obligatorios={"resultado": ("ok", "falla"), "ejecutado": str,
                      "origen": ("propio", "github", "programador")},
        opcionales={"detalle": str})}),
    "proceso.alerta": _t("proceso", {"motor"}, {NUEVA, CUALQUIERA}, None, {1: Version(
        obligatorios={"clase": ("falta", "falla"), "esperado_horas": int},
        opcionales={"ultimo": str, "vigilante": str})}),

    # --- Motor de eventos (02 §8.4; S-1273) ----------------------------------------------
    # Una entidad por regla (regla:<id>). Cada disparo deja su rastro: de él salen el tope de
    # cadena, el de entidad y día y el contador de utilidad. Una regla parada no dispara hasta
    # que Javier (o la dirección) la reactiva.
    "regla.disparada": _t("regla", {"motor"}, {NUEVA, "activa"}, None, {1: Version(
        obligatorios={"version": int, "evento": str, "sobre": str, "eslabon": int,
                      "acciones": ListaDe(Version(obligatorios={"tipo": str, "entidad": str}))})}),
    "regla.parada": _t("regla", {"motor", "javier", "direccion"}, {NUEVA, "activa"}, "parada",
                       {1: Version(obligatorios={"motivo": str},
                                   opcionales={"limite": ("cadena", "entidad", "tope_dia",
                                                          "utilidad", "a_mano"),
                                               "evento": str})}),
    "regla.reactivada": _t("regla", {"javier", "direccion"}, {"parada"}, "activa",
                           {1: _MOTIVO}),

    # --- Migración por etapas (P4-3, S-1271) ----------------------------------------------
    # Una entidad por etapa (migracion:tablero-1, -2…). Una etapa se supera con un ciclo limpio:
    # todos los escritores han escrito desde su inicio (o desde la última discrepancia) sin
    # ninguna discrepancia registro frente a tablero. La escribe el puente (puente.py).
    "migracion.iniciada": _t("migracion", {"planificador", "javier"}, None, "en_ciclo",
                             {1: Version(obligatorios={"etapa": int, "repos": ListaDe(str)})}),
    "migracion.discrepancia": _t("migracion", {"planificador"}, {"en_ciclo"}, None,
                                 {1: Version(obligatorios={"tarjeta": str, "de": str, "a": str,
                                                           "clase": str},
                                             opcionales={"escritor": str})}),
    "migracion.superada": _t("migracion", {"planificador"}, {"en_ciclo"}, "superada",
                             {1: Version(obligatorios={"escritores": ListaDe(str),
                                                       "ciclo_desde": str})}),
    "migracion.revertida": _t("migracion", {"javier", "planificador"},
                              {"en_ciclo", "superada"}, "revertida", {1: _MOTIVO}),

    # --- Espejo de la fase 2 (S-1265): el registro observa, no decide ------------------
    # Sin texto libre: ni títulos, ni cuerpos, ni notas, ni prompts (pueden llevar datos
    # personales o textos de clientes). Del texto solo se guarda una huella.
    "tarjeta.importada": _t("tarjeta", {"importador"}, None, ESTADO_EN_DATOS,
                            {1: _TARJETA_V1}, espejo=True),
    "tarjeta.cambiada": _t("tarjeta", {"importador"}, {CUALQUIERA}, ESTADO_EN_DATOS,
                           {1: _TARJETA_V1}, espejo=True),
    "tarjeta.desaparecida": _t("tarjeta", {"importador"}, {CUALQUIERA}, "desaparecida",
                               {1: Version(obligatorios={"revision": int})}),
    "ejecucion.importada": _t("ejecucion", {"importador"}, None, ESTADO_EN_DATOS,
                              {1: _EJECUCION_V1}, espejo=True),
    "ejecucion.cambiada": _t("ejecucion", {"importador"}, {CUALQUIERA}, ESTADO_EN_DATOS,
                             {1: _EJECUCION_V1}, espejo=True),
    "informe.importado": _t("informe", {"importador"}, None, "recibido",
                            {1: _INFORME_V1}, espejo=True),
    "informe.actualizado": _t("informe", {"importador"}, {CUALQUIERA}, "recibido",
                              {1: _INFORME_V1}, espejo=True),
}

# Reglas que dependen de los datos, no solo del tipo.
def _regla_decision_c(ev):
    if ev["datos"].get("clase") == "C" and ev["autor"]["papel"] != "javier":
        raise Rechazo("una decisión de clase C solo la escribe el papel javier")


def _regla_informe(ev):
    """Informe v2: cada criterio dice cómo se sabe; lo no comprobado lleva su porqué."""
    if ev["version_esquema"] != 2:
        return
    vistos = set()
    for c in ev["datos"]["criterios"]:
        if c["n"] in vistos:
            raise Rechazo(f"datos.criterios: el criterio {c['n']} sale dos veces")
        vistos.add(c["n"])
        if c["estado"] == "ok" and not c.get("evidencia"):
            raise Rechazo(f"datos.criterios: el {c['n']} está «ok» sin evidencia")
        if c["estado"] != "ok" and not c.get("motivo"):
            raise Rechazo(f"datos.criterios: el {c['n']} está «{c['estado']}» sin motivo")
    if any(d["clase"] == "C" for d in ev["datos"]["decisiones"]):
        raise Rechazo("datos.decisiones: una decisión de clase C no la toma un trabajador")


_RE_RECURSO = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,99}$")
MINUTOS_MAX_RESERVA = 24 * 60


def _regla_reserva(ev):
    d = ev["datos"]
    if "recurso" in d and not _RE_RECURSO.match(d["recurso"]):
        raise Rechazo("datos.recurso: minúsculas, cifras y . _ : / - (p. ej. repo:silent-frog:main)")
    if not 1 <= d["minutos"] <= MINUTOS_MAX_RESERVA:
        raise Rechazo(f"datos.minutos: de 1 a {MINUTOS_MAX_RESERVA}")


_RE_HUELLA = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,79}$")


def _regla_valoracion(ev):
    d = ev["datos"]
    for k, nota in d["notas"].items():
        if not 1 <= nota["n"] <= 5:
            raise Rechazo(f"datos.notas.{k}.n: de 1 a 5")
    if "huella" in d and not _RE_HUELLA.match(d["huella"]):
        raise Rechazo("datos.huella: minúsculas, cifras y . _ : / - (p. ej. silent-frog:ajustes:texto-cortado)")
    if "fija" in d and not 1 <= d["fija"]["valor"] <= 100:
        raise Rechazo("datos.fija.valor: de 1 a 100 (más alto, antes)")


# Ámbito de una pausa (02 §9, parada parcial): toda la organización, una app, un proyecto, un
# tipo de trabajo o una ejecución concreta (la tarea que corre).
_RE_AMBITO = re.compile(r"^(general|app:[a-z0-9][a-z0-9-]{0,63}|proyecto:[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
                        r"|tipo:(" + "|".join(TIPOS_TRABAJO) + r")|ejecucion:[A-Za-z0-9][A-Za-z0-9._-]{0,63})$")


def _regla_pausa(ev):
    if "ambito" in ev["datos"] and not _RE_AMBITO.match(ev["datos"]["ambito"]):
        raise Rechazo("datos.ambito: general, app:<slug>, proyecto:<id>, tipo:<tipo de trabajo> "
                      "o ejecucion:<tarea>")


REGLAS_EXTRA = {"decision.tomada": [_regla_decision_c], "tarea.valorada": [_regla_valoracion], "tarea.entregada": [_regla_informe],
                "reserva.pedida": [_regla_reserva], "reserva.renovada": [_regla_reserva],
                "pausa.activada": [_regla_pausa]}

CABECERA = ("id", "tipo", "version_esquema", "fecha", "autor", "entidad", "correlacion",
            "clave_idempotencia", "datos")
_RE_ID = re.compile(r"^[0-9a-f]{16}$")
_RE_ENTIDAD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RE_FECHA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$")


def _comprobar_campos(prefijo, datos, version: Version, donde: str):
    for k, spec in version.obligatorios.items():
        if k not in datos:
            raise Rechazo(f"{prefijo}{k}: obligatorio en {donde}")
    for k, v in datos.items():
        spec = version.obligatorios.get(k, version.opcionales.get(k))
        if spec is None:
            raise Rechazo(f"{prefijo}{k}: campo no previsto en {donde}")
        _comprobar_campo(f"{prefijo[len('datos.'):]}{k}", v, spec, donde)


def _comprobar_campo(nombre, valor, spec, donde=""):
    if isinstance(spec, Version):
        if not isinstance(valor, dict):
            raise Rechazo(f"datos.{nombre}: tiene que ser un objeto")
        return _comprobar_campos(f"datos.{nombre}.", valor, spec, donde)
    if isinstance(spec, ListaDe):
        if not isinstance(valor, list):
            raise Rechazo(f"datos.{nombre}: tiene que ser una lista")
        for i, elem in enumerate(valor):
            _comprobar_campo(f"{nombre}[{i}]", elem, spec.elemento, donde)
        return
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
    _comprobar_campos("datos.", datos, version, f"{ev['tipo']} v{ev['version_esquema']}")
    for regla in REGLAS_EXTRA.get(ev["tipo"], ()):
        regla(ev)
    return tipo


def describir(spec):
    """Una especificación en JSON legible: para enseñar al trabajador qué tiene que devolver."""
    if isinstance(spec, Version):
        return {**{k: describir(v) for k, v in spec.obligatorios.items()},
                **{f"{k}?": describir(v) for k, v in spec.opcionales.items()}}
    if isinstance(spec, ListaDe):
        return [describir(spec.elemento)]
    if isinstance(spec, tuple):
        return " | ".join(spec)
    return {str: "texto", int: "entero", bool: "sí/no", list: "lista", dict: "objeto"}.get(
        spec, str(spec))


def huella(ev: dict) -> str:
    """Lo que tiene que coincidir para que dos eventos con la misma clave sean «el mismo»."""
    base = {k: ev[k] for k in ("tipo", "version_esquema", "autor", "entidad", "correlacion",
                               "datos")}
    return hashlib.sha256(json.dumps(base, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()
