"""Comparación diaria registro ↔ tablero (PAA2 P2-2, S-1265). Solo informa: el tablero manda.

Tres bloques:
- **Fidelidad:** ¿el estado calculado del registro dice lo mismo que el tablero ahora? Tras
  importar debería salir vacío; lo que salga es un fallo del importador o un evento rechazado.
- **Anomalías:** lo que el tablero acepta y el ciclo de PAA2 (02 §3.3) no: saltos de columna
  fuera del ciclo, cambios de columna sin nota de transición, revisiones que bajan (una copia
  vieja pisando el tablero, Regla 0) y tarjetas que desaparecen. Salen de la historia del
  registro, no del tablero: es justo lo que el tablero no guarda.
- **Lion frente al tablero** y **vigías callados** (último informe más viejo de lo esperado).
"""
from __future__ import annotations

from datetime import date, datetime

from .importar import sin_movimientos

# Columna del tablero → estado del ciclo de PAA2 (02 §3.3), comprimido: «lista» va con
# «aprobada» y «verificada → integrada → cerrada» van juntas en «hecha».
COLUMNA_A_ESTADO = {
    "pedida": "propuesta", "propuesta": "propuesta", "adelante": "aprobada",
    "curso": "en_curso", "info": "bloqueada", "validar": "entregada", "hecha": "cerrada",
    "descartada": "descartada",
}
PERMITIDOS = {  # estado → estados a los que puede ir en el ciclo comprimido
    "propuesta": {"aprobada", "descartada"},
    "aprobada": {"en_curso", "descartada"},
    "en_curso": {"entregada", "bloqueada", "aprobada"},          # pausa o caducada → lista
    "bloqueada": {"aprobada", "en_curso"},
    "entregada": {"cerrada", "aprobada", "en_curso", "bloqueada"},  # devuelta → lista
    "cerrada": set(), "descartada": set(),
}
# Días que puede pasar un vigía sin informe nuevo antes de contarlo como callado.
CADENCIA_DIAS = {"soporte": 2, "contenidos": 8, "fichas": 8, "tablero": 8, "releases": 5}


def clase_salto(de: str, a: str) -> str | None:
    """None si el salto encaja en el ciclo (o no cambia de estado); si no, su clase."""
    e1, e2 = COLUMNA_A_ESTADO.get(de), COLUMNA_A_ESTADO.get(a)
    if e1 is None or e2 is None:
        return "columna desconocida"
    if e1 == e2 or e2 in PERMITIDOS[e1]:
        return None
    if e1 in ("cerrada", "descartada"):
        return "reabierta"
    if e2 == "cerrada":
        return "cerrada sin pasar por validar"
    if e2 == "en_curso" and e1 == "propuesta":
        return "en curso sin pasar por Empezar"
    return "otro salto fuera del ciclo"


def fidelidad(entidades: dict[str, dict], fuente: dict[str, dict]) -> dict:
    """Registro (estado calculado) frente a la fuente, campo a campo (sin `movimientos`)."""
    vivas = {i: e for i, e in entidades.items() if e["estado"] != "desaparecida"}
    faltan = sorted(set(fuente) - set(vivas))
    sobran = sorted(set(vivas) - set(fuente))
    distintas = []
    for i in sorted(set(fuente) & set(vivas)):
        a, b = sin_movimientos(vivas[i].get("datos") or {}), sin_movimientos(fuente[i])
        if a != b:
            campos = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            distintas.append({"id": i, "campos": {k: [a.get(k), b.get(k)] for k in campos}})
    return {"faltan_en_registro": faltan, "sobran_en_registro": sobran, "distintas": distintas}


def anomalias(eventos: list[dict], desde: str | None = None) -> list[dict]:
    """Anomalías en la historia de las tarjetas del registro, con su fecha. `desde` (AAAA-MM-DD)
    deja solo las de ese día en adelante."""
    out = []
    previo: dict[str, dict] = {}
    for ev in eventos:
        cid, d = ev["entidad"]["id"], ev["datos"]
        if ev["tipo"] == "tarjeta.desaparecida":
            out.append({"id": cid, "clase": "desaparecida del tablero", "fecha": ev["fecha"][:19],
                        "detalle": f"última revisión vista r{d['revision']}"})
            previo.pop(cid, None)
            continue
        movs = d.get("movimientos") or []
        for m in movs:
            c = clase_salto(m["de"], m["a"])
            if c:
                out.append({"id": cid, "clase": c, "fecha": m.get("fecha", ""),
                            "detalle": f"{m['de']} → {m['a']} ({m.get('por') or '?'})"})
        ant = previo.get(cid)
        if ant is not None:
            if d["revision"] < ant["revision"]:
                out.append({"id": cid, "clase": "revisión que baja", "fecha": d["actualizada"],
                            "detalle": f"r{ant['revision']} → r{d['revision']} (¿copia vieja?)"})
            if d["estado"] != ant["estado"] and not any(m["a"] == d["estado"] for m in movs):
                out.append({"id": cid, "clase": "cambio de columna sin nota",
                            "fecha": d["actualizada"],
                            "detalle": f"{ant['estado']} → {d['estado']}"})
        previo[cid] = d
    if desde:
        out = [a for a in out if a["fecha"][:10] >= desde]
    return sorted(out, key=lambda a: (a["fecha"], a["id"]))


def lion_frente_a_tablero(ejecuciones: dict[str, dict], tarjetas: dict[str, dict]) -> list[dict]:
    """Tareas de Lion enlazadas a una tarjeta cuyo estado no casa con la columna."""
    out = []
    for tid, e in sorted(ejecuciones.items()):
        d = e.get("datos") or {}
        cid = d.get("tarjeta")
        if not cid:
            continue
        t = tarjetas.get(cid)
        if t is None:
            out.append({"tarea": tid, "tarjeta": cid, "lion": d["estado"], "tablero": None,
                        "que": "la tarjeta no está en el tablero"})
            continue
        col = t["estado"]
        if d["estado"] == "done" and col in ("pedida", "propuesta", "adelante", "curso"):
            out.append({"tarea": tid, "tarjeta": cid, "lion": "done", "tablero": col,
                        "que": "Lion la da por terminada y la tarjeta no ha llegado a validar"})
        elif d["estado"] in ("running", "verifying") and col in ("hecha", "descartada"):
            out.append({"tarea": tid, "tarjeta": cid, "lion": d["estado"], "tablero": col,
                        "que": "Lion sigue con ella y la tarjeta ya está cerrada"})
    return out


def vigias_callados(informes: dict[str, dict], hoy: date) -> list[dict]:
    """Último informe de cada vigía y si lleva más días de los esperados sin uno nuevo."""
    ultimo: dict[str, str] = {}
    for e in informes.values():
        d = e.get("datos") or {}
        if d.get("vigia") and d.get("fecha", "") > ultimo.get(d["vigia"], ""):
            ultimo[d["vigia"]] = d["fecha"]
    out = []
    for vigia, dias in CADENCIA_DIAS.items():
        f = ultimo.get(vigia)
        edad = (hoy - datetime.strptime(f, "%Y-%m-%d").date()).days if f else None
        out.append({"vigia": vigia, "ultimo": f, "dias": edad, "esperado": dias,
                    "callado": edad is None or edad > dias})
    return out


def comparar(reg, tarjetas: dict, ejecuciones: dict, hoy: date,
             desde: str | None = None) -> dict:
    ents_t = reg.entidades("tarjeta")
    ents_e = reg.entidades("ejecucion")
    todas = anomalias(reg.eventos_de("tarjeta"))
    recientes = [a for a in todas if a["fecha"][:10] >= desde] if desde else todas
    por_clase: dict[str, int] = {}
    for a in todas:
        por_clase[a["clase"]] = por_clase.get(a["clase"], 0) + 1
    columnas: dict[str, int] = {}
    for d in tarjetas.values():
        columnas[d["estado"]] = columnas.get(d["estado"], 0) + 1
    return {
        "tablero": {"tarjetas": len(tarjetas), "por_columna": dict(sorted(columnas.items()))},
        "fidelidad_tablero": fidelidad(ents_t, tarjetas),
        "fidelidad_lion": fidelidad(ents_e, ejecuciones),
        "anomalias": {"desde": desde, "recientes": recientes,
                      "historico_por_clase": dict(sorted(por_clase.items(),
                                                         key=lambda x: -x[1]))},
        "lion_frente_a_tablero": lion_frente_a_tablero(ents_e, tarjetas),
        "vigias": vigias_callados(reg.entidades("informe"), hoy),
    }


# --- informe en Markdown ----------------------------------------------------------------------
_MAX = 40


def _cortar(filas: list[str], total: int) -> list[str]:
    if total > len(filas):
        filas.append(f"| … | y {total - len(filas)} más (ver el .json del mismo día) | | |")
    return filas


def informe_md(fecha: str, importado: dict | None, comp: dict, cartera: dict | None) -> str:
    fid_t, fid_l = comp["fidelidad_tablero"], comp["fidelidad_lion"]
    an = comp["anomalias"]
    calla = [v for v in comp["vigias"] if v["callado"]]
    l = [f"# Registro frente a tablero — {fecha}", "",
         "Generado por `org` (PAA2 fase 2, modo observación, S-1265). **El tablero manda**: esto "
         "solo informa y no cambia nada. Sin textos de tarjetas ni de clientes: solo ids, "
         "columnas, fechas y cifras.", "", "## Resumen", ""]
    if importado is not None:
        tipos = ", ".join(f"{k} {v}" for k, v in importado["por_tipo"].items()) or "ninguno"
        l.append(f"- **Importado:** {importado['eventos']} evento(s) ({tipos}); "
                 f"{len(importado['rechazados'])} rechazado(s).")
    t = comp["tablero"]
    l += [f"- **Tablero:** {t['tarjetas']} tarjetas ("
          + ", ".join(f"{k} {v}" for k, v in t["por_columna"].items()) + ").",
          f"- **Fidelidad registro ↔ tablero:** {len(fid_t['faltan_en_registro'])} faltan, "
          f"{len(fid_t['sobran_en_registro'])} sobran, {len(fid_t['distintas'])} distintas. "
          f"Lion: {len(fid_l['faltan_en_registro'])} / {len(fid_l['sobran_en_registro'])} / "
          f"{len(fid_l['distintas'])}.",
          f"- **Anomalías** {'desde ' + an['desde'] if an['desde'] else 'en toda la historia'}: "
          f"{len(an['recientes'])}. Histórico: "
          + (", ".join(f"{k} {v}" for k, v in an["historico_por_clase"].items()) or "ninguna")
          + ".",
          f"- **Lion frente al tablero:** {len(comp['lion_frente_a_tablero'])} desacuerdo(s).",
          "- **Vigías callados:** " + (", ".join(
              f"{v['vigia']} ({'sin informe' if v['dias'] is None else str(v['dias']) + ' días'})"
              for v in calla) or "ninguno") + "."]
    if cartera is not None:
        c = cartera["cartera"]
        l.append(f"- **Cartera (manifest):** {c['apps']} entradas: {c['publicadas']} publicadas, "
                 f"{c['en_desarrollo']} en desarrollo, puentes {', '.join(c['puentes']) or '—'}. "
                 "Hallazgos por lista: " + ", ".join(
                     f"{n} {len(x.get('hallazgos', []))}" for n, x in cartera["listas"].items())
                 + ".")

    l += ["", "## 1. Fidelidad", ""]
    for nombre, fid in (("Tablero", fid_t), ("Lion", fid_l)):
        if not (fid["faltan_en_registro"] or fid["sobran_en_registro"] or fid["distintas"]):
            l.append(f"- {nombre}: el registro dice lo mismo que la fuente.")
            continue
        for k, txt in (("faltan_en_registro", "faltan en el registro"),
                       ("sobran_en_registro", "en el registro y ya no en la fuente")):
            if fid[k]:
                l.append(f"- {nombre}, {txt} ({len(fid[k])}): " + ", ".join(fid[k][:_MAX])
                         + (" …" if len(fid[k]) > _MAX else ""))
        for d in fid["distintas"][:_MAX]:
            l.append(f"- {nombre}, {d['id']} distinta en: " + ", ".join(
                f"{k} ({v[0]} ≠ {v[1]})" for k, v in d["campos"].items()))

    l += ["", "## 2. Anomalías (lo que el tablero acepta y el ciclo de PAA2 no)", "",
          "Ciclo de 02 §3.3 comprimido: Backlog/Propuestas → Empezar → En curso → Por validar → "
          "Terminada, con vuelta atrás a Empezar desde En curso o Por validar, y Pendiente como "
          "«bloqueada».", ""]
    if an["recientes"]:
        filas = ["| Fecha | Tarjeta | Qué | Detalle |", "|---|---|---|---|"]
        filas += [f"| {a['fecha'][:16]} | {a['id']} | {a['clase']} | {a['detalle']} |"
                  for a in an["recientes"][-_MAX:]]
        l += _cortar(filas, len(an["recientes"]) + 2)
    else:
        l.append("Ninguna en el periodo.")

    l += ["", "## 3. Lion frente al tablero", ""]
    if comp["lion_frente_a_tablero"]:
        l += [f"- {x['tarea']} ({x['tarjeta']}): Lion «{x['lion']}», tablero «{x['tablero']}»: "
              f"{x['que']}." for x in comp["lion_frente_a_tablero"]]
    else:
        l.append("Sin desacuerdos (pocas tareas de Lion llevan tarjeta: el enlace es débil).")

    l += ["", "## 4. Vigías", "", "| Vigía | Último informe | Días | Esperado | |",
          "|---|---|---|---|---|"]
    l += [f"| {v['vigia']} | {v['ultimo'] or '—'} | {'—' if v['dias'] is None else v['dias']} | "
          f"≤ {v['esperado']} | {'callado' if v['callado'] else 'al día'} |" for v in comp["vigias"]]

    if cartera is not None:
        l += ["", "## 5. Cartera frente a las cuatro listas", "",
              "La cartera sale de `updates/manifest.json` (origin/main), la única fuente fiable. "
              "Ninguna lista se corrige aquí.", "",
              "| Lista | Apps | Dice publicadas | Hallazgos | Fuera del manifest |",
              "|---|---|---|---|---|"]
        for n, x in cartera["listas"].items():
            if "error" in x:
                l.append(f"| {n} | — | — | {x['error']} | — |")
                continue
            l.append(f"| {n} | {x['apps']} | {x['dice_publicadas'] if x['dice_publicadas'] is not None else '—'} "
                     f"| {len(x['hallazgos'])} | {len(x['no_estan_en_el_manifest'])} |")
        l.append(f"| **manifest** | {cartera['cartera']['apps']} | "
                 f"{cartera['cartera']['publicadas']} | — | — |")
        for n, x in cartera["listas"].items():
            if not (x.get("hallazgos") or x.get("no_estan_en_el_manifest")):
                continue
            l += ["", f"**{n}**", ""]
            for h in x["hallazgos"][:_MAX]:
                extra = "".join(f"; {k}: {h[k]}" for k in ("lista", "manifest") if k in h)
                l.append(f"- {h['app']}: {h['que']}{extra}")
            if x["no_estan_en_el_manifest"]:
                l.append("- Fuera del manifest (en desarrollo, ideas o retiradas; informativo): "
                         + ", ".join(x["no_estan_en_el_manifest"]))
    return "\n".join(l) + "\n"
