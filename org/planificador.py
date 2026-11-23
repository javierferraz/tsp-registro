"""Planificador por reglas, sin modelo (02 §5 y §5.0; PAA2 P5-1, S-1272).

Cada vez que corre:

1. Caduca las tareas en curso sin latido (`trabajo.caducar`) y devuelve a `lista` las que estaban
   aprobadas, caducadas o devueltas.
2. Valora cada candidata con las siete notas de la dirección (`tarea.valorada`). Si no tiene, usa
   unas por defecto sacadas de la tarjeta del tablero y lo dice (`sin_valorar`).
3. Consolida las que tienen la misma huella: solo la más antigua es candidata, las otras quedan
   «consolidadas en» ella.
4. Quita las que no pueden empezar y dice por qué (pausa, dependencia abierta, recurso ocupado,
   solape de ficheros, acabará esperando a Javier, no cabe en el sitio).
5. Ordena: primero las clases que siempre pasan delante; dentro, la prioridad fijada a mano;
   después valor entre coste; desempate por antigüedad.
6. Aplica los topes (capacidad que da quien llama y tope de tareas en curso). **No hacer nada es
   un resultado válido.**

`planificar` solo mira y devuelve el plan (cada puesto con el porqué de su nota). `ejecutar` lo
escribe: lista, caducadas y, si se pide, las asignaciones. Antes de asignar, la tarea se **reserva**
(`tarea:<id>`, `recursos.py`): con el planificador corriendo en dos sitios, gana la reserva que
entró antes en la historia compartida y el otro la deja y sigue con la siguiente. Después de ganar,
se vuelve a comprobar que la tarea sigue en `lista` (la otra máquina pudo asignarla y soltar).
"""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

from . import recursos, trabajo
from .esquemas import NOTAS, Rechazo
from .puente import IDENTIDAD as PUENTE
from .estado import _FMT

IDENTIDAD = "planificador"
ESPERA_JAVIER_TOPE = 10         # 02 §9 ter: tareas esperándole que frenan el arranque
MAX_EN_CURSO = 8                # WIP del tablero (vigilante.json: curso total 8)
REVISAR_DIAS = 30               # 02 §5.0: en lista sin moverse
RESERVA_MINUTOS = 5             # la asignación ya protege; la reserva solo cubre la carrera
ESTADOS_QUE_PASAN_A_LISTA = ("aprobada", "caducada", "devuelta")
ESTADOS_CANDIDATAS = ("lista",) + ESTADOS_QUE_PASAN_A_LISTA
HECHAS = ("cerrada", "integrada")           # una dependencia así ya no frena
TIPOS_EN_GITHUB = ("documentacion", "contenido")  # lo que el planificador de GitHub puede asignar
PRIORIDAD_TABLERO = {"alta": 5, "media": 3, "baja": 2}
TAMANO = {"S": 1, "M": 3, "L": 5}


def _ahora(ahora):
    return ahora or datetime.now(timezone.utc)


def _fecha(s: str) -> datetime:
    return datetime.strptime(s, _FMT).replace(tzinfo=timezone.utc)


def _asignada_por(t: dict) -> str | None:
    return next((h["autor"]["identidad"] for h in reversed(t.get("historial") or [])
                 if h.get("tipo") == "tarea.asignada"), None)


def _tarjeta(reg_tarjetas: dict, tid: str) -> dict:
    """Los datos del espejo de la tarjeta del tablero (la tarea y la tarjeta comparten id)."""
    return (reg_tarjetas.get(tid) or {}).get("datos") or {}


def valorar(t: dict, tarjeta: dict) -> dict:
    """Las siete notas con su motivo. La valoración de la dirección manda; sin ella, notas por
    defecto derivadas de la tarjeta, y `sin_valorar` queda a la vista."""
    v = t.get("valoracion")
    if v:
        notas = {k: dict(v["notas"][k]) for k in NOTAS}
        return {"notas": notas, "sin_valorar": False, "clase": v.get("clase"),
                "huella": v.get("huella"), "recursos": v.get("recursos", []),
                "espera_javier": bool(v.get("espera_javier")), "fija": v.get("fija")}
    etiquetas = set(tarjeta.get("etiquetas") or [])
    prio = tarjeta.get("prioridad")
    tam = t.get("tamano") or "M"
    esfuerzo = TAMANO.get(tam, 3)
    n = {
        "importancia": (PRIORIDAD_TABLERO.get(prio, 3),
                        f"por defecto: prioridad del tablero «{prio or 'sin prioridad'}»"),
        "urgencia": (4 if "fallo" in etiquetas else 3,
                     "por defecto: etiqueta fallo" if "fallo" in etiquetas
                     else "por defecto: sin señal de urgencia"),
        "riesgo": (4 if "seguridad" in etiquetas else 3,
                   "por defecto: etiqueta seguridad" if "seguridad" in etiquetas
                   else "por defecto: sin señal de riesgo"),
        "esfuerzo": (esfuerzo, f"por defecto: tamaño {tam}"),
        "coste": (esfuerzo, f"por defecto: igual que el esfuerzo (tamaño {tam})"),
        "probabilidad": (3, "por defecto: sin dato"),
        "comprobable": ({"maquina": 4, "ojos": 3, "decision": 2}.get(tarjeta.get("check"), 3),
                        f"por defecto: comprobación «{tarjeta.get('check') or 'sin dato'}»"),
    }
    return {"notas": {k: {"n": a, "motivo": b} for k, (a, b) in n.items()},
            "sin_valorar": True, "clase": None, "huella": None, "recursos": [],
            "espera_javier": tarjeta.get("check") == "decision", "fija": None}


def puntuar(notas: dict) -> float:
    """Valor entre coste (02 §5.0): (importancia + urgencia + riesgo + probabilidad + comprobable)
    entre (esfuerzo + coste). Va de 0,5 a 12,5."""
    valor = sum(notas[k]["n"] for k in ("importancia", "urgencia", "riesgo", "probabilidad",
                                        "comprobable"))
    return round(valor / (notas["esfuerzo"]["n"] + notas["coste"]["n"]), 3)


def _porque(puesto: dict) -> str:
    n = puesto["notas"]
    partes = []
    if puesto["clase"]:
        partes.append(f"clase prioritaria «{puesto['clase']}»: pasa delante de todo")
    if puesto["fija"]:
        partes.append(f"prioridad fijada a mano en {puesto['fija']['valor']} "
                      f"({puesto['fija']['motivo']})")
    partes.append(
        f"valor {sum(n[k]['n'] for k in ('importancia', 'urgencia', 'riesgo', 'probabilidad', 'comprobable'))}"
        f" entre coste {n['esfuerzo']['n'] + n['coste']['n']} = {puesto['puntuacion']}")
    if puesto["sin_valorar"]:
        partes.append("sin valoración de la dirección: notas por defecto")
    return "; ".join(partes)


def _dependencias(t: dict, tarjeta: dict) -> list[str]:
    return sorted(set(t.get("dependencias") or []) | set(tarjeta.get("bloqueada_por") or []))


def _solapan(a: list[str], b: list[str]) -> bool:
    """Dos alcances (listas de ficheros o carpetas) se pisan si uno es prefijo del otro."""
    for x in a:
        for y in b:
            x2, y2 = x.rstrip("/"), y.rstrip("/")
            if x2 == y2 or x2.startswith(y2 + "/") or y2.startswith(x2 + "/"):
                return True
    return False


def _modelo(t: dict) -> str:
    """Sonnet si el qué ya está decidido y solo queda ejecutar; Opus si hay que decidir; empate,
    Opus (tablero/README.md §Un agente por tarjeta)."""
    if t.get("modelo"):
        return t["modelo"]
    if t.get("tamano") == "S" and t.get("tipo_trabajo") in ("contenido", "documentacion", "web"):
        return "sonnet"
    return "opus"


def planificar(reg, *, ahora=None, capacidad=3, max_en_curso=MAX_EN_CURSO, sitio="pc",
               tope_espera=ESPERA_JAVIER_TOPE, identidad=IDENTIDAD) -> dict:
    """El plan, sin escribir nada. `capacidad`: cuántas tareas puede arrancar ahora quien llama
    (el PC la saca de su memoria y su cuota; no se fija un número aquí: 02 §5.2)."""
    ahora = _ahora(ahora)
    tareas = reg.entidades("tarea")
    tarjetas = reg.entidades("tarjeta")
    pausas = sorted(i for i, p in reg.entidades("pausa").items() if p["estado"] == "activa")
    reservas = recursos.vivas(reg, None, ahora)
    en_curso = {i: t for i, t in tareas.items() if t["estado"] == "en_curso"}
    limite = trabajo.CADUCIDAD

    # Solo caducan las que asignó un planificador: las que puso en curso el puente (la tarjeta del
    # tablero está En curso) no tienen trabajador que lata, y caducarlas movería la tarjeta.
    por_caducar = [i for i, t in en_curso.items()
                   if t.get("ultimo_latido") and ahora - _fecha(t["ultimo_latido"]) >= limite
                   and _asignada_por(t) != PUENTE]
    # Las que van a volver a `lista` en esta pasada cuentan como candidatas.
    candidatas = {i: t for i, t in tareas.items()
                  if t["estado"] in ESTADOS_CANDIDATAS or i in por_caducar}

    # Esperando a Javier: lo que está bloqueado esperando su decisión, y lo entregado cuya
    # tarjeta pide su decisión.
    espera = sorted(i for i, t in tareas.items() if t["estado"] == "bloqueada" or (
        t["estado"] == "entregada" and _tarjeta(tarjetas, i).get("check") == "decision"))
    frena = len(espera) >= tope_espera

    puestos, no_arranca, a_revisar = [], [], []
    for i, t in candidatas.items():
        tj = _tarjeta(tarjetas, i)
        val = valorar(t, tj)
        hist = t.get("historial") or []
        desde = _fecha(next((h["fecha"] for h in reversed(hist) if h["estado"] == "lista"),
                            t["creada"]))
        if t["estado"] == "lista" and ahora - desde > timedelta(days=REVISAR_DIAS):
            a_revisar.append(i)
        puesto = {"tarea": i, "estado": t["estado"] if i not in por_caducar else "caducada",
                  "repo": t.get("repo"), "creada": t["creada"], "tipo_trabajo": t.get("tipo_trabajo"),
                  "alcance": t.get("alcance") or [], "tamano": t.get("tamano"),
                  "publica": bool(t.get("publica")), "modelo": _modelo(t), **val,
                  "dependencias": _dependencias(t, tj)}
        puesto["puntuacion"] = puntuar(val["notas"])
        puesto["por_que"] = _porque(puesto)
        puestos.append(puesto)

    # Consolidar por huella: la más antigua queda; las otras se cuentan en ella.
    consolidadas, fuera = [], set()
    por_huella: dict[str, list] = {}
    for p in puestos:
        if p["huella"]:
            por_huella.setdefault(p["huella"], []).append(p)
    for h, grupo in sorted(por_huella.items()):
        if len(grupo) > 1:
            grupo.sort(key=lambda p: (p["creada"], p["tarea"]))
            principal, otras = grupo[0], grupo[1:]
            consolidadas.append({"huella": h, "principal": principal["tarea"],
                                 "otras": [p["tarea"] for p in otras]})
            fuera |= {p["tarea"] for p in otras}
            for p in otras:
                no_arranca.append({"tarea": p["tarea"],
                                   "motivo": f"misma huella «{h}» que {principal['tarea']}: "
                                             f"consolidada en ella"})

    def clave(p):
        return (0 if p["clase"] else 1, -(p["fija"]["valor"] if p["fija"] else 0),
                -p["puntuacion"], p["creada"], p["tarea"])

    orden = sorted((p for p in puestos if p["tarea"] not in fuera), key=clave)
    for n, p in enumerate(orden, 1):
        p["posicion"] = n

    cerradas = {i for i, t in tareas.items() if t["estado"] in HECHAS}
    descartadas = {i for i, t in tareas.items() if t["estado"] == "descartada"}
    ocupados = {r["recurso"]: r for r in reservas}
    alcances = [(i, t.get("alcance") or []) for i, t in en_curso.items()
                if i not in por_caducar and t.get("alcance")]
    huecos = max(0, min(capacidad, max_en_curso - (len(en_curso) - len(por_caducar))))
    arrancar, elegibles = [], []
    for p in orden:
        motivo = None
        pendientes = [d for d in p["dependencias"] if d not in cerradas | descartadas]
        propia = {r["recurso"] for r in reservas if r.get("tarea") == p["tarea"]}
        ocupado = [x for x in p["recursos"] if x in ocupados and x not in propia]
        if pausas:
            motivo = f"hay pausa activa ({', '.join(pausas)})"
        elif p["estado"] not in ("lista", "aprobada", "caducada", "devuelta"):
            motivo = f"estado {p['estado']}"
        elif sitio == "github" and p["tipo_trabajo"] not in TIPOS_EN_GITHUB:
            motivo = (f"el planificador de GitHub solo asigna {', '.join(TIPOS_EN_GITHUB)}; "
                      f"esta es {p['tipo_trabajo']}")
        elif pendientes:
            motivo = f"espera a {', '.join(pendientes)}"
        elif ocupado:
            motivo = f"recurso ocupado: {', '.join(ocupado)}"
        elif p["alcance"] and any(_solapan(p["alcance"], a) for _, a in alcances):
            otra = next(i for i, a in alcances if _solapan(p["alcance"], a))
            motivo = f"toca los mismos ficheros que {otra}"
        elif (p["espera_javier"] or p["publica"]) and frena:
            motivo = (f"acabará esperando a Javier y ya le esperan {len(espera)} "
                      f"(tope {tope_espera})")
        elif len(arrancar) >= huecos:
            motivo = (f"no cabe: capacidad {capacidad}, en curso {len(en_curso)} de {max_en_curso}"
                      if huecos else "no cabe: sin hueco")
        if motivo:
            no_arranca.append({"tarea": p["tarea"], "motivo": motivo})
            if motivo.startswith("no cabe"):
                elegibles.append(p["tarea"])  # de reserva, si una de las primeras se pierde
            continue
        elegibles.append(p["tarea"])
        arrancar.append(p["tarea"])
        alcances.append((p["tarea"], p["alcance"]))

    return {
        "fecha": ahora.strftime(_FMT), "sitio": sitio, "identidad": identidad,
        "capacidad": capacidad, "max_en_curso": max_en_curso, "en_curso": len(en_curso),
        "pausas": pausas,
        "espera_javier": {"n": len(espera), "tope": tope_espera, "frena": frena,
                          "tareas": espera[:10]},
        "caducar": por_caducar,
        "a_lista": sorted(i for i, t in candidatas.items()
                          if t["estado"] in ESTADOS_QUE_PASAN_A_LISTA),
        "orden": [{k: p[k] for k in ("posicion", "tarea", "estado", "clase", "fija", "puntuacion",
                                     "sin_valorar", "por_que", "modelo", "notas")}
                  for p in orden],
        "arrancar": [{"tarea": i, "modelo": next(p["modelo"] for p in orden if p["tarea"] == i)}
                     for i in arrancar],
        "elegibles": elegibles,
        "no_arranca": no_arranca, "consolidadas": consolidadas,
        "a_revisar": sorted(a_revisar),
    }


def recurso_tarea(tid: str) -> str:
    """El recurso con nombre que se reserva al asignar (minúsculas: lo pide el esquema)."""
    return f"tarea:{tid.lower()}"


def _clave_lista(t: dict) -> str:
    return f"plan:{t['id']}:lista:{len(t.get('historial') or [])}"


def _n_asignacion(t: dict) -> int:
    return len(t.get("ejecuciones_anteriores", [])) + (1 if t.get("ejecucion") else 0) + 1


def ejecutar(reg, plan: dict, *, identidad=IDENTIDAD, asignar=False, ejecutor=None,
             maquina=None, subir=True, ahora=None) -> dict:
    """Escribe el plan. Sin `asignar`, solo caducadas y `lista` (no cambia ninguna columna del
    tablero). Con `asignar` (hace falta un `ejecutor` que de verdad vaya a trabajar: si no, la
    tarea caduca a los 20 min), reserva cada tarea antes de asignarla."""
    ahora = _ahora(ahora)
    out: dict = {"caducadas": [], "a_lista": [], "asignadas": [], "perdidas": [], "omitidas": []}
    if plan["pausas"]:
        out["omitidas"].append(f"pausa activa: {', '.join(plan['pausas'])}")
        return out
    for tid in plan["caducar"]:
        t = reg.ver("tarea", tid)
        if t and t["estado"] == "en_curso":
            reg.escribir("tarea.caducada", ("tarea", tid), "planificador", identidad,
                         f"{tid}:caducada:{t.get('ultimo_latido')}",
                         {"motivo": f"sin latido desde {t.get('ultimo_latido')}"}, ahora=ahora)
            out["caducadas"].append(tid)
    for tid in plan["a_lista"] + [i for i in out["caducadas"] if i not in plan["a_lista"]]:
        t = reg.ver("tarea", tid)
        if t and t["estado"] in ESTADOS_QUE_PASAN_A_LISTA:
            reg.escribir("tarea.lista", ("tarea", tid), "planificador", identidad,
                         _clave_lista(t), {"motivo": "planificador: lista para empezar"},
                         ahora=ahora)
            out["a_lista"].append(tid)
    if subir and (out["caducadas"] or out["a_lista"]):
        out["subida"] = trabajo._subir(reg)
    if not asignar:
        return out
    if not ejecutor:
        raise Rechazo("asignar necesita --ejecutor: quien vaya a trabajar la tarea")
    # Dos planificadores con el mismo nombre en máquinas distintas no pueden valer por uno solo:
    # la reserva da por buena a la misma identidad. Se distingue por máquina.
    identidad = f"{identidad}@{maquina or socket.gethostname().lower()}"
    objetivo = len(plan["arrancar"])
    modelos = {p["tarea"]: p["modelo"] for p in plan["orden"]}
    for tid in plan["elegibles"]:
        if len(out["asignadas"]) >= objetivo:
            break
        if subir:
            reg.traer()
        t = reg.ver("tarea", tid)
        if not t or t["estado"] != "lista":
            out["perdidas"].append({"tarea": tid, "motivo": f"ya está {t['estado'] if t else '—'}"})
            continue
        r = recursos.reservar(reg, recurso_tarea(tid), identidad, "planificador", RESERVA_MINUTOS,
                              tid, maquina, ahora=ahora, subir=subir)
        if not r["ok"]:
            out["perdidas"].append({"tarea": tid, "motivo": f"reserva {r['estado']}",
                                    "gana": r.get("titular")})
            continue
        t = reg.ver("tarea", tid)  # la otra máquina pudo asignarla y soltar antes de nuestra subida
        if not t or t["estado"] != "lista":
            recursos.liberar(reg, r["reserva"], identidad, "planificador",
                             "ya no está en lista", ahora=ahora, subir=subir)
            out["perdidas"].append({"tarea": tid, "motivo": "ya no estaba en lista"})
            continue
        datos = {"ejecutor": ejecutor, "modelo": modelos.get(tid, "opus"),
                 **({"maquina": maquina} if maquina else {})}
        try:
            reg.escribir("tarea.asignada", ("tarea", tid), "planificador", identidad,
                         f"plan:{tid}:asignada:{_n_asignacion(t)}", datos, ahora=ahora)
            estado_subida = trabajo._subir(reg) if subir else ""
        except Rechazo as e:
            out["perdidas"].append({"tarea": tid, "motivo": str(e)})
            continue
        out["asignadas"].append({"tarea": tid, **datos, "reserva": r["reserva"]})
        if str(estado_subida).startswith("sin subir"):
            # Asignada solo aquí: otra máquina la vería en lista al caducar la reserva. No se
            # asigna nada más hasta que salga la subida.
            out["error"] = f"{tid} asignada solo en local ({estado_subida}); parado"
            break
    return out


def informe_md(plan: dict, hecho: dict | None = None) -> str:
    """El plan en español llano: por qué cada tarea va donde va y por qué las demás no."""
    L = [f"# Plan del planificador ({plan['fecha'][:16]}Z, {plan['sitio']})", ""]
    L.append(f"- En curso: {plan['en_curso']} de {plan['max_en_curso']}; capacidad ahora: "
             f"{plan['capacidad']}.")
    e = plan["espera_javier"]
    L.append(f"- Esperando a Javier: {e['n']} (tope {e['tope']}"
             f"{', frena lo que acabará esperándole' if e['frena'] else ''}).")
    if plan["pausas"]:
        L.append(f"- **Pausa activa** ({', '.join(plan['pausas'])}): no se arranca nada.")
    L += ["", "## Arrancaría" if hecho is None else "## Plan de arranque", ""]
    if plan["arrancar"]:
        L += [f"- **{a['tarea']}** ({a['modelo']})" for a in plan["arrancar"]]
    else:
        L.append("Nada. No hacer nada es un resultado válido.")
    L += ["", "## Orden y porqué", ""]
    for p in plan["orden"][:15]:
        L.append(f"{p['posicion']}. **{p['tarea']}** — {p['por_que']}")
    if len(plan["orden"]) > 15:
        L.append(f"… y {len(plan['orden']) - 15} más.")
    if plan["no_arranca"]:
        L += ["", "## No arranca", ""]
        L += [f"- {n['tarea']}: {n['motivo']}" for n in plan["no_arranca"][:15]]
    if plan["consolidadas"]:
        L += ["", "## Consolidadas por huella", ""]
        L += [f"- {c['huella']}: {c['principal']} ← {', '.join(c['otras'])}"
              for c in plan["consolidadas"]]
    if plan["a_revisar"]:
        L += ["", f"## A revisar (más de {REVISAR_DIAS} días en lista)", "",
              ", ".join(plan["a_revisar"])]
    if hecho:
        L += ["", "## Escrito", ""]
        L += [f"- {k}: {v}" for k, v in hecho.items() if v]
    return "\n".join(L) + "\n"
