"""El parte diario (PAA2 P3-1, S-1266): una sola página HTML generada del registro.

Reglas de 06 §5.1: lo que le toca a Javier arriba; cabe en una pantalla; dice lo que no sabe;
si no ha pasado nada, lo dice en una línea; cada sección tiene un tope.

Solo lee el registro (estado calculado e informe diario de `org diario`). No escribe eventos.
Sin títulos ni textos: el registro no los guarda (D-8); cada tarjeta enlaza al tablero, que es
donde está el detalle. Corre al lado del parte de Telegram, que no cambia.
"""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

TABLERO = "http://localhost/2606-thestromboliproject/tablero/#"
TOPE = 5  # filas por lista; el resto, en una línea «y N más»
ENLACES = 8  # identificadores por fila agrupada
VENTANA = timedelta(hours=24)
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
MUESTRA_PEQUENA = 100  # visitantes por debajo de esto: «muestra pequeña»


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _dias(desde: datetime | None, ahora: datetime) -> int | None:
    return None if desde is None else max(0, (ahora - desde).days)


def _hace(d: int | None) -> str:
    if d is None:
        return "fecha desconocida"
    return "hoy" if d == 0 else ("hace 1 día" if d == 1 else f"hace {d} días")


def _dd(d: int | None) -> str:
    return "?" if d is None else f"{d} d"


CHECK = {"ojos": "piden mirar", "decision": "piden decisión", "maquina": "comprobables por máquina"}


def _agrupa(c: Counter, tope: int = 4) -> str:
    """«4 en Clear Parrot, 2 sin app» (los `tope` primeros y «otras N»)."""
    partes = [f"{n} en {k}" if k else f"{n} sin app" for k, n in c.most_common(tope)]
    resto = sum(c.values()) - sum(n for _, n in c.most_common(tope))
    if resto:
        partes.append(f"{resto} en otras")
    return ", ".join(partes)


def _ultimo_a(t: dict, col: str) -> datetime | None:
    movs = [m for m in t["datos"].get("movimientos", []) if m.get("a") == col]
    return _dt(movs[-1]["fecha"]) if movs else _dt(t["datos"].get("actualizada"))


def _movidas(tarjetas: dict, col: str, desde: datetime) -> list[tuple[str, dict]]:
    return [(i, t) for i, t in tarjetas.items()
            if any(m.get("a") == col and (_dt(m.get("fecha")) or desde) > desde
                   for m in t["datos"].get("movimientos", []))]


def ultimo_informe(raiz: Path) -> tuple[dict | None, datetime | None]:
    """El último informe de `org diario` y cuándo se escribió (la última comprobación)."""
    fichs = sorted((raiz / "informes").glob("????-??-??.json"))
    if not fichs:
        return None, None
    hora = datetime.fromtimestamp(fichs[-1].stat().st_mtime, timezone.utc)
    return json.loads(fichs[-1].read_text(encoding="utf-8")), hora


def datos(tarjetas: dict, ejecuciones: dict, informes: dict, rechazados: list,
          informe: dict | None, ahora: datetime, comprobado: datetime | None = None) -> dict:
    """Las secciones del parte como listas de filas {texto, id?, nivel?}. Puro: testeable."""
    hoy = ahora.date()
    desde = ahora - VENTANA
    semana = ahora - timedelta(days=7)

    def tarj(i, t, texto):
        return {"id": i, "app": t["datos"].get("app") or "", "texto": texto}

    # 1 · Lo que le toca a Javier
    def grupo(texto, items, lion=False):
        """Una fila por tipo (06 §5.1, «15 cosas iguales son una línea»), lo más viejo antes."""
        items.sort(key=lambda x: -(x[1] if x[1] is not None else -1))
        return {"texto": texto.format(n=len(items), viejo=_dd(items[0][1])), "lion": lion,
                "enlaces": [(i, _dd(d)) for i, d in items]}

    decide = []
    preg = [(i, _dias(_ultimo_a(t, "info"), ahora)) for i, t in tarjetas.items()
            if t["estado"] == "info"]
    if preg:
        decide.append(grupo("{n} pregunta(s) para ti sin responder (la más vieja, {viejo}):", preg))
    dec = [(i, _dias(_ultimo_a(t, "validar"), ahora)) for i, t in tarjetas.items()
           if t["estado"] == "validar" and t["datos"].get("check") == "decision"]
    if dec:
        decide.append(grupo("{n} terminada(s) que piden tu decisión para cerrarse "
                            "(la más vieja, {viejo}):", dec))
    blq = [(i, _dias(_dt(e["datos"].get("actualizada")), ahora)) for i, e in ejecuciones.items()
           if e["estado"] == "blocked"]
    if blq:
        repos = Counter(ejecuciones[i]["datos"].get("repo", "") for i, _ in blq)
        decide.append(grupo("{n} tarea(s) de Lion paradas esperando una decisión tuya ("
                            + _agrupa(repos) + "; la más vieja, {viejo}):", blq, lion=True))

    # 2 · Problemas
    prob = []
    fecha_inf = (informe or {}).get("fecha")
    if not informe:
        prob.append({"texto": "No hay informe diario del registro: el parte no sabe si el "
                              "registro está al día.", "nivel": "alto"})
    elif fecha_inf != hoy.isoformat():
        prob.append({"texto": f"El registro no se ha puesto al día hoy (último informe: "
                              f"{fecha_inf}). Todo lo de abajo puede ir atrasado.",
                     "nivel": "alto"})
    if rechazados:
        prob.append({"texto": f"{len(rechazados)} evento(s) del registro no pasan las "
                              "comprobaciones.", "nivel": "alto"})
    comp = (informe or {}).get("comparacion", {})
    for clave, nombre in (("fidelidad_tablero", "el tablero"), ("fidelidad_lion", "Lion")):
        f = comp.get(clave, {})
        n = sum(len(f.get(k, [])) for k in ("faltan_en_registro", "sobran_en_registro",
                                              "distintas"))
        if n:
            prob.append({"texto": f"El registro no cuadra con {nombre} en {n} cosa(s)."})
    for v in comp.get("vigias", []):
        if v.get("callado"):
            prob.append({"texto": f"Vigía {v['vigia']}: sin informe desde {v['ultimo']} "
                                  f"({v['dias']} días; lo normal es ≤ {v['esperado']})."})
    ultimo = {}
    for inf in informes.values():
        d = inf["datos"]
        if d.get("fecha", "") >= ultimo.get(d.get("vigia"), {}).get("fecha", ""):
            ultimo[d.get("vigia")] = d
    fichas = ultimo.get("fichas", {}).get("cifras", {})
    if fichas.get("sin_revisar_por_error"):
        prob.append({"texto": f"Vigía Fichas ({ultimo['fichas']['fecha']}): "
                              f"{fichas['sin_revisar_por_error']} app(s) sin revisar por un "
                              "error. Su «sin cambios» no vale para esas."})
    if ultimo.get("soporte", {}).get("cifras", {}).get("con_error"):
        prob.append({"texto": "El Vigía Soporte terminó con error en su última pasada."})
    for i, e in sorted(ejecuciones.items()):
        a = _dt(e["datos"].get("actualizada"))
        if e["estado"] == "failed" and a and a > semana:
            prob.append({"id": i, "app": e["datos"].get("repo", ""), "lion": True,
                         "texto": "tarea de Lion fallida " + _hace(_dias(a, ahora))})
    an = comp.get("anomalias", {}).get("recientes", [])
    if an:
        ids = sorted({x["id"] for x in an})
        prob.append({"texto": f"{len(an)} salto(s) de columna fuera del ciclo desde ayer: "
                              + ", ".join(ids) + "."})
    if comp.get("lion_frente_a_tablero"):
        prob.append({"texto": f"{len(comp['lion_frente_a_tablero'])} tarea(s) de Lion no "
                              "casan con la columna de su tarjeta."})
    parado = [(i, t) for i, t in sorted(tarjetas.items()) if t["estado"] == "curso"
              and (_dias(_dt(t["datos"].get("actualizada")), ahora) or 0) > 7]
    if parado:
        prob.append({"texto": f"{len(parado)} tarjeta(s) en curso sin moverse hace más de "
                              "7 días: " + ", ".join(i for i, _ in parado[:8])
                              + ("…" if len(parado) > 8 else "") + "."})
    listas = (informe or {}).get("cartera", {}).get("listas", {})
    mal = {k: len(v.get("hallazgos", [])) for k, v in listas.items() if v.get("hallazgos")}
    if mal:
        prob.append({"texto": f"{len(mal)} de {len(listas)} listas de apps no cuadran con el "
                              f"manifest ({sum(mal.values())} diferencias de versión o "
                              "estado). Conocido; no se corrige solo."})

    # 3 · Hecho en las últimas 24 h
    hecho = []
    cerr = _movidas(tarjetas, "hecha", desde)
    if cerr:
        hecho.append({"texto": f"{len(cerr)} tarjeta(s) terminadas: "
                               + _agrupa(Counter(t["datos"].get("app") or "" for _, t in cerr))
                               + "."})
    entr = [(i, t) for i, t in _movidas(tarjetas, "validar", desde) if t["estado"] == "validar"]
    if entr:
        hecho.append({"texto": f"{len(entr)} entregada(s) a «Por validar»: "
                               + _agrupa(Counter(t["datos"].get("app") or "" for _, t in entr))
                               + "."})
    lion_ok = [e for e in ejecuciones.values() if e["estado"] == "done"
               and (_dt(e["datos"].get("actualizada")) or desde) > desde]
    if lion_ok:
        hecho.append({"texto": f"{len(lion_ok)} tarea(s) de Lion terminadas: "
                               + _agrupa(Counter(e["datos"].get("repo", "") for e in lion_ok))
                               + "."})
    fichas_c = (informe or {}).get("cartera", {}).get("fichas", {})
    pub = [f"{f.get('nombre', s)} {f.get('version', '')}" for s, f in sorted(fichas_c.items())
           if f.get("estado") == "publicada"
           and (f.get("fecha_release") or "") >= (hoy - timedelta(days=1)).isoformat()]
    if pub:
        hecho.append({"texto": f"Publicada(s) desde ayer: {', '.join(pub)}."})

    # 4 · En marcha
    marcha = []
    curso = [t for t in tarjetas.values() if t["estado"] == "curso"]
    if curso:
        marcha.append({"texto": f"{len(curso)} tarjeta(s) en curso: "
                                + _agrupa(Counter(t["datos"].get("app") or "" for t in curso))
                                + "."})
    cola = sum(1 for t in tarjetas.values() if t["estado"] == "adelante")
    if cola:
        marcha.append({"texto": f"{cola} en «Empezar», esperando a un agente."})
    lion_v = Counter(e["estado"] for e in ejecuciones.values()
                     if e["estado"] in ("running", "verifying"))
    if lion_v:
        marcha.append({"texto": f"Lion: {lion_v['running']} trabajando, {lion_v['verifying']} "
                                "comprobando."})

    # 5 · Negocio (lo que hay, con su fecha) y gasto (lo que no hay)
    cont = ultimo.get("contenidos", {})
    cc = cont.get("cifras", {})
    negocio = []
    if cc:
        vis = cc.get("visitors")
        aviso = " · muestra pequeña" if isinstance(vis, int) and vis < MUESTRA_PEQUENA else ""
        negocio.append({"texto": f"Visitantes {vis} · descargas {cc.get('descargas_semana')} "
                                 f"(semana del informe del {cont.get('fecha')}){aviso}."})
    else:
        negocio.append({"texto": "Visitantes y descargas: sin informe del Vigía Contenidos."})
    negocio.append({"texto": "Ventas: sin dato (no hay lector automático)."})
    semana_lion = [e for e in ejecuciones.values()
                   if (_dt(e["datos"].get("creada")) or semana) > semana]
    minutos = sum(e["datos"].get("minutos") or 0 for e in semana_lion)
    gasto = [{"texto": "Coste del modelo y minutos de GitHub: sin dato (nadie los apunta "
                       "todavía en el registro)."},
             {"texto": f"Lion, 7 días: {len(semana_lion)} tarea(s), unos {minutos} min de "
                       "trabajo (de creada a última actualización)."}]

    # 6 · Sin verificar
    sinv = []
    val = [t for t in tarjetas.values() if t["estado"] == "validar"]
    if val:
        ch = Counter(t["datos"].get("check") or "sin tipo" for t in val)
        viejas = sum(1 for t in val if (_dias(_ultimo_a(t, "validar"), ahora) or 0) > 7)
        sinv.append({"texto": f"{len(val)} en «Por validar» sin cerrar ("
                              + ", ".join(f"{n} {CHECK.get(k, k)}" for k, n in ch.most_common())
                              + f"); {viejas} llevan más de 7 días."})
    nov = [e for e in ejecuciones.values() if e["estado"] == "done"
           and e["datos"].get("verificacion") != "pass"
           and (_dt(e["datos"].get("creada")) or semana) > semana]
    if nov:
        sinv.append({"texto": f"{len(nov)} tarea(s) de Lion de esta semana terminadas sin "
                              "verificación que pase (Lion las marca «no verificable»)."})

    return {"ahora": ahora, "registro": comprobado, "informe": fecha_inf,
            "decide": decide, "problemas": prob, "hecho": hecho, "marcha": marcha,
            "negocio": negocio, "gasto": gasto, "sin_verificar": sinv}


# ---------------------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f7f3ee;--card:#fffdfa;--ink:#2b2622;--muted:#7a6f66;--line:#e6ddd3;
--accent:#c8553d;--warn:#b7791f;--ok:#4f7a4a;--chip:#efe7de}
@media (prefers-color-scheme:dark){:root{--bg:#1d1a17;--card:#262220;--ink:#efe8e1;
--muted:#a3978c;--line:#3a342f;--accent:#e2765e;--warn:#d9a441;--ok:#86b07e;--chip:#332d29}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI",system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:18px 16px 12px}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin-bottom:12px}
h1{font-size:17px;font-weight:600;margin:0}
.sub{color:var(--muted);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:10px}
section{min-width:0;overflow-wrap:anywhere;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
section.top{grid-column:1/-1;border-left:4px solid var(--accent)}
h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
margin:0 0 6px;font-weight:600}
h2 b{color:var(--ink)}
ul{margin:0;padding:0;list-style:none}
li{padding:3px 0;border-top:1px solid var(--line)}
li:first-child{border-top:0}
li.alto{color:var(--accent);font-weight:600}
.vacio{color:var(--ok)}
a.id{white-space:nowrap;font-family:Consolas,monospace;font-size:12.5px;color:var(--accent);text-decoration:none}
a.id:hover{text-decoration:underline}
.idl{white-space:nowrap;font-family:Consolas,monospace;font-size:12.5px;color:var(--muted)}
.dd{color:var(--muted);font-size:11.5px;margin:0 6px 0 1px}
.app{display:inline-block;background:var(--chip);border-radius:4px;padding:0 6px;
font-size:12px;margin:0 4px}
footer{color:var(--muted);font-size:11.5px;margin-top:10px}
"""


def _fila(f: dict) -> str:
    e = html.escape
    cab = ""
    if f.get("id"):
        cab = (f'<span class="idl">{e(f["id"])}</span>' if f.get("lion") else
               f'<a class="id" href="{e(TABLERO + f["id"])}">{e(f["id"])}</a>')
    app = f'<span class="app">{e(f["app"])}</span>' if f.get("app") else (" " if cab else "")
    cls = ' class="alto"' if f.get("nivel") == "alto" else ""
    enl = ""
    if f.get("enlaces"):
        ver = f["enlaces"][:ENLACES]
        enl = " " + " ".join(
            (f'<span class="idl">{e(i)}</span>' if f.get("lion") else
             f'<a class="id" href="{e(TABLERO + i)}">{e(i)}</a>')
            + f'<span class="dd">·{e(d)}</span>' for i, d in ver)
        if len(f["enlaces"]) > ENLACES:
            enl += f' <span class="dd">y {len(f["enlaces"]) - ENLACES} más</span>'
    return f"<li{cls}>{cab}{app}{e(f['texto'])}{enl}</li>"


def _seccion(titulo: str, filas: list, vacio: str, top: bool = False) -> str:
    total = sum(len(f.get("enlaces", [])) or 1 for f in filas)
    n = f" <b>({total})</b>" if filas and top else ""
    cuerpo = "".join(_fila(f) for f in filas[:TOPE])
    if len(filas) > TOPE:
        cuerpo += f"<li>y {len(filas) - TOPE} más.</li>"
    if not filas:
        cuerpo = f'<li class="vacio">{html.escape(vacio)}</li>'
    cls = ' class="top"' if top else ""
    return f"<section{cls}><h2>{html.escape(titulo)}{n}</h2><ul>{cuerpo}</ul></section>"


def _fecha_larga(d: datetime) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def render(p: dict) -> str:
    loc = p["ahora"].astimezone()
    reg = p["registro"].astimezone().strftime("%d-%m %H:%M") if p["registro"] else "nunca"
    secciones = [
        _seccion("Necesita tu decisión", p["decide"], "Nada necesita tu decisión.", top=True),
        _seccion("Problemas", p["problemas"], "Ningún problema abierto."),
        _seccion("Hecho en las últimas 24 h", p["hecho"], "Nada terminado en las últimas 24 h."),
        _seccion("En marcha", p["marcha"], "Nada en marcha."),
        _seccion("Sin verificar", p["sin_verificar"], "Nada pendiente de verificar."),
        _seccion("Negocio y gasto", p["negocio"] + p["gasto"], ""),
    ]
    return (
        "<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Parte diario TSP</title>"
        f"<style>{CSS}</style></head><body><main>"
        f"<header><h1>The Stromboli Project · {_fecha_larga(loc)}</h1>"
        f"<span class=\"sub\">generado a las {loc:%H:%M} · registro comprobado el {reg} · "
        f"últimas 24 h</span></header>"
        f"<div class=\"grid\">{''.join(secciones)}</div>"
        "<footer>Parte generado del registro central (PAA2, en observación): el tablero "
        "manda y el parte de Telegram sigue igual. Sin títulos: el registro no los guarda; "
        f"cada tarjeta se abre en el tablero. Cada lista muestra {TOPE} y cuenta el resto."
        "</footer></main></body></html>\n")


def generar(reg, ahora: datetime | None = None) -> Path:
    """Escribe estado/parte.html (fuera de git, se regenera) y devuelve su ruta."""
    ahora = ahora or datetime.now(timezone.utc)
    informe, comprobado = ultimo_informe(reg.raiz)
    p = datos(reg.entidades("tarjeta"), reg.entidades("ejecucion"),
              reg.entidades("informe"), reg.rechazados(), informe, ahora, comprobado)
    reg.dir_estado.mkdir(parents=True, exist_ok=True)
    destino = reg.dir_estado / "parte.html"
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(render(p), encoding="utf-8", newline="\n")
    tmp.replace(destino)
    return destino
