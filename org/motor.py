"""Motor de eventos (02 §8.4; PAA2 P5-2, S-1273). Una tabla de reglas, no un agente.

Cada regla tiene id, versión, dueño, los eventos que la disparan y una acción que mira el estado
actual y devuelve los eventos que hay que escribir (o nada). El motor recorre los eventos
aceptados de las últimas 72 h y, por cada par (regla, evento) que aún no ha tratado, escribe las
acciones (correlación = el evento que las origina) y un `regla.disparada` con el rastro.

**Contra los bucles** (02 §8.4 y §9 ter), si se alcanza cualquiera de estos topes, la regla se
para (`regla.parada`) y avisa; no vuelve a disparar hasta que Javier la reactive:
- una cadena no pasa de 5 eslabones (se cuentan siguiendo `correlacion`);
- una misma regla no salta dos veces sobre la misma entidad (la que toca) en 24 horas;
- como mucho 10 disparos por regla y día.

**Contador de utilidad** (05 §7): por regla, cuántas veces ha disparado, qué ha creado y, de las
tareas creadas, cuántas sirvieron (verificadas, integradas o cerradas) y cuántas no (descartadas).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import avisos
from .esquemas import Rechazo
from .estado import _FMT
from .pausa import slug

IDENTIDAD = "motor"
CADENA_MAX = 5                      # 02 §9 ter: cadena máxima de eventos
ENTIDAD_HORAS = 24                  # 02 §8.4: una regla, una entidad, una vez cada 24 h
TOPE_DIA = 10                       # disparos por regla y día (02 §8.4 no da número)
DEVOLUCIONES_BLOQUEO = 3            # 02 §9 ter: devoluciones antes de bloquear
VENTANA = timedelta(hours=72)       # el motor corre cada 6 h; el cron de GitHub llega hasta 10,5 tarde
PASADAS_MAX = CADENA_MAX + 2        # lo que escribe una pasada puede disparar otra regla
SIRVIO = ("verificada", "integrada", "cerrada")


def _fecha(s: str) -> datetime:
    return datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class Accion:
    tipo: str
    entidad: tuple[str, str]
    datos: dict
    version: int = 1


@dataclass(frozen=True)
class Regla:
    id: str
    version: int
    dueno: str
    eventos: tuple
    que_hace: str
    accion: Callable            # (reg, evento) -> list[Accion] (vacía: no aplica)
    aviso: Callable | None = None  # (evento, acciones) -> texto del aviso
    extra: dict = field(default_factory=dict)


# --- Las cinco primeras reglas ------------------------------------------------------------

def _medida_visual(reg, ev):
    """Entrega de algo que toca interfaz sin medida visual del laboratorio → tarea de medida."""
    tid = ev["entidad"]["id"]
    t = reg.ver("tarea", tid)
    if not t or t["estado"] != "entregada":
        return []
    if t.get("tipo_trabajo") != "interfaz" and not t.get("toca_interfaz"):
        return []
    if any(e["autor"]["papel"] == "laboratorio" for e in t.get("evidencias", [])):
        return []
    n = sum(1 for h in t.get("historial", []) if h["estado"] == "entregada")
    nueva = f"{tid}-medida-{n}"
    if reg.ver("tarea", nueva):
        return []
    return [Accion("tarea.propuesta", ("tarea", nueva), {
        "titulo": f"Medida visual de la entrega {n} de {tid}", "repo": t["repo"],
        "tipo_trabajo": "interfaz", "tamano": "S", "dependencias": [],
        "criterios": [f"evidencia.verificada del laboratorio (medida visual) sobre la entrega "
                      f"{n} de {tid}"]}, version=2)]


def _tres_devoluciones(reg, ev):
    tid = ev["entidad"]["id"]
    t = reg.ver("tarea", tid)
    if not t or t["estado"] != "devuelta" or t.get("devoluciones", 0) < DEVOLUCIONES_BLOQUEO:
        return []
    return [Accion("tarea.bloqueada", ("tarea", tid), {
        "motivo": f"devuelta {t['devoluciones']} veces: bloqueada hasta que alguien decida "
                  f"(02 §9 ter)"})]


def _presupuesto(reg, ev):
    tid = ev["entidad"]["id"]
    t = reg.ver("tarea", tid)
    tope = ((t or {}).get("presupuesto") or {}).get("minutos")
    if not t or t["estado"] != "en_curso" or not isinstance(tope, int) or not t.get("ejecucion"):
        return []
    usados = (_fecha(ev["fecha"]) - _fecha(t["ejecucion"]["inicio"])).total_seconds() / 60
    if usados <= tope:
        return []
    return [Accion("tarea.pausada", ("tarea", tid), {
        "motivo": f"supera su presupuesto: {round(usados)} min de {tope}"})]


def _integridad(reg, ev):
    iid = ev["entidad"]["id"]
    inc = reg.ver("incidencia", iid)
    if not inc or inc.get("origen") != "integridad" or reg.ver("pausa", f"motor-{iid}"):
        return []
    app = slug(inc.get("app"))
    return [Accion("pausa.activada", ("pausa", f"motor-{iid}"), {
        "nivel": "suave", "origen": "motor", "ambito": f"app:{app}" if app else "general",
        "motivo": f"falla una prueba de integridad (incidencia {iid}); solo Javier reanuda"})]


def _latido_perdido(reg, ev):
    p = ev["entidad"]["id"]
    d = ev["datos"]
    nueva = f"latido-{p}-{ev['id'][:8]}"
    if reg.ver("incidencia", nueva):
        return []
    que = ("no da señales" if d["clase"] == "falta" else "ha fallado")
    return [Accion("incidencia.abierta", ("incidencia", nueva), {
        "origen": "latido", "gravedad": "media",
        "descripcion": f"El proceso {p} {que} (último latido: {d.get('ultimo') or 'ninguno'}; "
                       f"se espera cada {d['esperado_horas']} h).",
        "huella": f"proceso:{p}:{d['clase']}"[:80]})]


REGLAS: tuple[Regla, ...] = (
    Regla("medida-visual", 1, "laboratorio", ("tarea.entregada",),
          "Entrega que toca interfaz sin medida visual → tarea de medida visual", _medida_visual),
    Regla("tres-devoluciones", 1, "validador", ("tarea.devuelta",),
          "Tarea devuelta 3 veces → bloqueada", _tres_devoluciones,
          lambda ev, a: f"🟠 {ev['entidad']['id']} devuelta {DEVOLUCIONES_BLOQUEO} veces: "
                        f"bloqueada (motor, regla tres-devoluciones)."),
    Regla("presupuesto", 1, "planificador", ("tarea.latido", "tarea.punto_control"),
          "Ejecución que supera su presupuesto → pausada y aviso", _presupuesto,
          lambda ev, a: f"🟠 {ev['entidad']['id']} pausada: {a[0].datos['motivo']} "
                        f"(motor, regla presupuesto)."),
    Regla("integridad", 1, "javier", ("incidencia.abierta",),
          "Falla una prueba de integridad → pausa suave de su app (o general) y aviso",
          _integridad,
          lambda ev, a: f"🔴 Pausa suave ({a[0].datos['ambito']}): falla una prueba de "
                        f"integridad (incidencia {ev['entidad']['id']}). Solo tú reanudas."),
    Regla("latido-perdido", 1, "vigilante", ("proceso.alerta",),
          "Falta o falla el latido de un proceso → incidencia con su huella", _latido_perdido),
)


# --- El recorrido -------------------------------------------------------------------------

def _eventos(reg, tipos, desde: str) -> list[dict]:
    marcas = ",".join("?" * len(tipos))
    return reg._leer(lambda db: [json.loads(js) for (js,) in db.execute(
        f"SELECT json FROM eventos WHERE tipo IN ({marcas}) AND fecha >= ? ORDER BY fecha, id",
        (*tipos, desde))])


def _correlaciones(reg) -> dict[str, str | None]:
    return reg._leer(lambda db: dict(db.execute("SELECT id, correlacion FROM eventos")))


def _reactivada(regla: dict | None) -> str:
    """Fecha de la última reactivación de la regla ("" si nunca)."""
    return max((h["fecha"] for h in (regla or {}).get("historial", [])
                if h["tipo"] == "regla.reactivada"), default="")


def eslabon(correl: dict, ev_id: str) -> int:
    """Cuántos eslabones lleva la cadena que acaba en este evento (él incluido)."""
    n, vistos = 1, {ev_id}
    while correl.get(ev_id):
        ev_id = correl[ev_id]
        if ev_id in vistos:  # no debería pasar: la correlación apunta siempre atrás
            break
        vistos.add(ev_id)
        n += 1
    return n


def correr(reg, *, escribir=False, enviar=False, ahora=None, reglas=REGLAS,
           identidad=IDENTIDAD) -> dict:
    """Una vuelta del motor. Sin `escribir`, dice qué haría (sin tocar nada)."""
    ahora = ahora or datetime.now(timezone.utc)
    out = {"fecha": ahora.strftime(_FMT), "disparos": [], "paradas": [], "omitidas": [],
           "errores": [], "avisos": []}
    tipos = sorted({t for r in reglas for t in r.eventos})
    hoy = ahora.strftime("%Y-%m-%d")
    for _ in range(PASADAS_MAX if escribir else 1):
        estado = reg.entidades("regla")
        previos = [e for e in reg.eventos_de("regla") if e["tipo"] == "regla.disparada"]
        hechos = {(e["entidad"]["id"], e["datos"]["evento"]) for e in previos}
        correl = _correlaciones(reg)
        nuevos = 0
        for ev in _eventos(reg, tipos, (ahora - VENTANA).strftime(_FMT)):
            for r in (r for r in reglas if ev["tipo"] in r.eventos):
                if (r.id, ev["id"]) in hechos or any(
                        d["regla"] == r.id and d["evento"] == ev["id"] for d in out["disparos"]):
                    continue
                if (estado.get(r.id) or {}).get("estado") == "parada":
                    out["omitidas"].append({"regla": r.id, "evento": ev["id"], "motivo": "parada"})
                    continue
                if ev["fecha"] < _reactivada(estado.get(r.id)):
                    continue  # lo de antes de reactivarla ya lo vio Javier: no la vuelve a parar
                try:
                    acciones = r.accion(reg, ev)
                except Rechazo as e:
                    out["errores"].append(f"{r.id} sobre {ev['id']}: {e}")
                    continue
                if not acciones:
                    continue
                # «La misma entidad» (02 §8.4) = la que la regla toca: la de su primera acción.
                sobre = f"{acciones[0].entidad[0]}:{acciones[0].entidad[1]}"
                n = eslabon(correl, ev["id"]) + 1
                # los de esta vuelta que aún no se han releído del registro (sin contar dos veces)
                suyos = [e for e in previos if e["entidad"]["id"] == r.id] + [
                    {"datos": {"sobre": d["sobre"]}, "fecha": d["fecha"]}
                    for d in out["disparos"] if d["regla"] == r.id and (r.id, d["evento"]) not in hechos]
                limite = motivo = None
                if n > CADENA_MAX:
                    limite, motivo = "cadena", f"la cadena llegaría a {n} eslabones (tope {CADENA_MAX})"
                elif any(e["datos"]["sobre"] == sobre and
                         ahora - _fecha(e["fecha"]) < timedelta(hours=ENTIDAD_HORAS) for e in suyos):
                    limite, motivo = "entidad", (f"saltaría dos veces sobre {sobre} en "
                                                 f"{ENTIDAD_HORAS} h")
                elif sum(1 for e in suyos if e["fecha"] >= hoy) >= TOPE_DIA:
                    limite, motivo = "tope_dia", f"llega a {TOPE_DIA} disparos hoy"
                if limite:
                    out["paradas"].append({"regla": r.id, "limite": limite, "motivo": motivo,
                                           "evento": ev["id"]})
                    texto = (f"🛑 Regla «{r.id}» parada: {motivo} (evento {ev['id']}). No vuelve "
                             f"a disparar hasta que la reactives.")
                    if escribir:
                        estado[r.id] = {"estado": "parada"}
                        if reg.escribir("regla.parada", ("regla", r.id), "motor", identidad,
                                        f"motor:{r.id}:parada:{ev['id']}",
                                        {"motivo": motivo, "limite": limite, "evento": ev["id"]},
                                        correlacion=ev["id"], ahora=ahora).nuevo:
                            nuevos += 1
                            out["avisos"].append(texto)
                            avisos.avisar(reg, texto, enviar)
                    continue
                hechas = []
                for k, a in enumerate(acciones):
                    if not escribir:
                        hechas.append(a)
                        continue
                    try:
                        reg.escribir(a.tipo, a.entidad, "motor", identidad,
                                     f"motor:{r.id}:{ev['id']}:{k}", a.datos,
                                     correlacion=ev["id"], version=a.version, ahora=ahora)
                        hechas.append(a)
                    except Rechazo as e:
                        out["errores"].append(f"{r.id}: {a.tipo} {a.entidad[1]}: {e}")
                registro = {"regla": r.id, "version": r.version, "evento": ev["id"],
                            "sobre": sobre, "eslabon": n, "fecha": ahora.strftime(_FMT),
                            "acciones": [{"tipo": a.tipo, "entidad": f"{a.entidad[0]}:{a.entidad[1]}"}
                                         for a in hechas]}
                if not hechas:
                    continue
                out["disparos"].append(registro)
                if escribir:
                    reg.escribir("regla.disparada", ("regla", r.id), "motor", identidad,
                                 f"motor:{r.id}:{ev['id']}",
                                 {k: registro[k] for k in ("version", "evento", "sobre",
                                                           "eslabon", "acciones")},
                                 correlacion=ev["id"], ahora=ahora)
                    nuevos += 1 + len(hechas)
                    if r.aviso:
                        texto = r.aviso(ev, hechas)
                        out["avisos"].append(texto)
                        avisos.avisar(reg, texto, enviar)
        if not nuevos:
            break
    return out


def utilidad(reg, reglas=REGLAS) -> list[dict]:
    """El contador por regla: disparos, lo creado y, de las tareas creadas, cuántas sirvieron."""
    estado = reg.entidades("regla")
    tareas = reg.entidades("tarea")
    disparos: dict[str, list] = {}
    for e in reg.eventos_de("regla"):
        if e["tipo"] == "regla.disparada":
            disparos.setdefault(e["entidad"]["id"], []).append(e)
    out = []
    for r in reglas:
        ds = disparos.get(r.id, [])
        creadas = [a["entidad"].split(":", 1)[1] for d in ds for a in d["datos"]["acciones"]
                   if a["tipo"] == "tarea.propuesta"]
        estados = [tareas.get(t, {}).get("estado") for t in creadas]
        out.append({"regla": r.id, "version": r.version, "dueno": r.dueno, "que_hace": r.que_hace,
                    "estado": (estado.get(r.id) or {}).get("estado", "activa"),
                    "disparos": len(ds), "acciones": sum(len(d["datos"]["acciones"]) for d in ds),
                    "tareas_creadas": len(creadas),
                    "sirvieron": sum(1 for s in estados if s in SIRVIO),
                    "inutiles": sum(1 for s in estados if s == "descartada"),
                    "pendientes": sum(1 for s in estados if s not in SIRVIO + ("descartada",))})
    return out
