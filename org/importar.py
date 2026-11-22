"""Importador de la fase 2 (PAA2 P2-2, S-1265): copia en el registro lo que dicen el tablero,
las carpetas de tarea de Roaring Lion y los informes de los vigías. **Solo lee** las fuentes:
nunca escribe en `board.json`, en Lion ni en el repo central.

Qué se copia y qué no (decisión D-8 del README): estructura, fechas, cifras y huellas. Nunca
texto libre (títulos, cuerpos, notas, prompts, correos), porque puede llevar datos personales o
textos de clientes.

Cada pasada escribe solo lo que ha cambiado desde la anterior: la entidad del registro se compara
con la fuente y, si difiere, entra un evento `*.cambiada` con la foto nueva.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PAPEL = "importador"
_RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RE_TARJETA = re.compile(r"^S-\d+$")


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _limpio(d: dict) -> dict:
    """Quita los campos vacíos: los opcionales vacíos no se escriben (esquemas.py los rechaza)."""
    return {k: v for k, v in d.items() if v is not None and v != ""}


# Lista blanca por patrón (D-8, D-15): un campo de texto solo entra si parece un identificador
# corto; si no casa (prosa, frases, correos), se descarta. Así ningún texto libre llega al registro
# aunque la fuente cambie de forma.
_RE_TOKEN = re.compile(r"^[a-z0-9._-]{1,32}$")                       # estados, modelos, esfuerzo
_RE_NOMBRE = re.compile(r"^[A-Za-z0-9ÁÉÍÓÚÑáéíóúñ][A-Za-z0-9ÁÉÍÓÚÑáéíóúñ ._-]{0,31}$")  # app, etiqueta
_RE_CARPETA = re.compile(r"^[\w.-]{1,64}$")                          # carpeta de un repo
_RE_RAMA = re.compile(r"^[\w./-]{1,64}$")                            # nombre de rama de git
AUTORES = ("claude", "javier")  # papeles del tablero (Javier = papel «javier» de 02), no personas


def _si(valor, patron):
    return valor if isinstance(valor, str) and patron.match(valor) else None


def sin_movimientos(datos: dict) -> dict:
    return {k: v for k, v in datos.items() if k != "movimientos"}


# --- fuentes ---------------------------------------------------------------------------------
def leer_tablero(ruta: str | Path) -> dict[str, dict]:
    """{id: datos de tarjeta.* v1} desde tablero/board.json. `movimientos` = todas las notas de
    transición (de, a, fecha, por); el importador recorta las ya vistas."""
    b = json.loads(Path(ruta).read_text(encoding="utf-8"))
    out = {}
    for c in b.get("cards", []):
        cid = c.get("id")
        if not isinstance(cid, str) or not _RE_ID.match(cid):
            continue
        notas = c.get("notes") or []
        movs = [{"de": n["from"], "a": n["to"], "fecha": n.get("at", ""),
                 "por": n.get("by") if n.get("by") in AUTORES else "otro"}
                for n in notas if n.get("kind") == "transition" and n.get("from") and n.get("to")]
        commits = [str(e["sha"])[:12] for e in ((c.get("evidence") or {}).get("commits") or [])
                   if isinstance(e, dict) and e.get("sha")]
        out[cid] = _limpio({
            "estado": _si(c.get("col"), _RE_TOKEN) or "?",
            "revision": int(c.get("revision") or 0),
            "creada": c.get("created") or "?",
            "actualizada": c.get("updated") or c.get("created") or "?",
            "huella_texto": _sha(f"{c.get('title', '')}\n{c.get('body', '')}")[:16],
            "notas": len(notas),
            "app": _si(c.get("app"), _RE_NOMBRE),
            "etiquetas": sorted(x for x in (c.get("labels") or []) if _si(x, _RE_NOMBRE)) or None,
            "prioridad": _si(c.get("priority"), _RE_TOKEN),
            "check": _si(c.get("check"), _RE_TOKEN),
            "modelo": _si(c.get("model"), _RE_TOKEN),
            "bloqueada_por": [x for x in (c.get("blockedBy") or []) if _si(x, _RE_TARJETA)] or None,
            "commits": commits or None,
            "movimientos": movs or None,
        })
    return out


def _iso_ms(ms) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def leer_lion(carpeta: str | Path) -> dict[str, dict]:
    """{T-…: datos de ejecucion.* v1} desde las carpetas de tarea de Lion (task.json y, si hay,
    report.json). Del prompt, el objetivo y los criterios no se copia nada: solo cuántos
    criterios pasaron, fallaron o no se ejecutaron."""
    carpeta = Path(carpeta)
    out = {}
    if not carpeta.is_dir():
        return out
    for d in sorted(p for p in carpeta.iterdir() if p.is_dir()):
        try:
            t = json.loads((d / "task.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tid = t.get("id") or d.name
        if not isinstance(tid, str) or not _RE_ID.match(tid):
            continue
        r = None
        try:
            r = json.loads((d / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        r = r if isinstance(r, dict) else None
        creada, act = _iso_ms(t.get("created")), _iso_ms(t.get("updated"))
        tarjeta = t.get("card") or (r or {}).get("card")
        git = (r or {}).get("git") or {}
        criterios = {}
        for c in (r or {}).get("criteria") or []:
            s = c.get("status") if isinstance(c, dict) else None
            if s in ("pass", "fail", "not_run"):
                criterios[s] = criterios.get(s, 0) + 1
        minutos = None
        if isinstance(t.get("created"), (int, float)) and isinstance(t.get("updated"), (int, float)):
            minutos = max(0, round((t["updated"] - t["created"]) / 60000))
        out[tid] = _limpio({
            "estado": _si(t.get("status"), _RE_TOKEN) or "?",
            "creada": creada or "?",
            "actualizada": act or creada or "?",
            "tarjeta": tarjeta if isinstance(tarjeta, str) and _RE_TARJETA.match(tarjeta) else None,
            "repo": _si(Path(str(t["repo"]).replace("\\", "/")).name, _RE_CARPETA) if t.get("repo")
            else None,
            "tamano": t.get("size") if t.get("size") in ("S", "M", "L") else None,
            "modelo": _si(t.get("model"), _RE_TOKEN),
            "esfuerzo": _si(t.get("effort"), _RE_TOKEN),
            "intentos": t.get("attempts") if type(t.get("attempts")) is int else None,
            "minutos": minutos,
            "verificacion": _si((t.get("verified") or {}).get("outcome"), _RE_TOKEN),
            "criterios": criterios or None,
            "commits": len(git.get("commits") or []) if r else None,
            "rama": _si(git.get("branch"), _RE_RAMA),
            "subida": git.get("pushed") if isinstance(git.get("pushed"), bool) else None,
            "con_informe": r is not None,
        })
    return out


# Informes de los vigías en el repo central. Cada lector devuelve (fecha, cifras); solo cifras.
def _cifras_soporte(d):
    return d.get("fecha"), {"correos": len(d.get("items") or []),
                            "aplazados": d.get("aplazados") or 0, "con_error": bool(d.get("error"))}


def _cifras_contenidos(d):
    res, seo = d.get("resumen") or {}, d.get("seo") or {}
    cif = {"seo_fallos": len(seo.get("fallos") or []), "seo_avisos": len(seo.get("avisos") or []),
           "paginas_revisadas": seo.get("paginas_revisadas"),
           "paginas_sin_visitas": len(d.get("sin_visitas") or []),
           "descargas_semana": sum(n for _, n in (d.get("descargas") or {}).get("semana") or [])}
    for k in ("pageviews", "visitors", "visits"):
        if isinstance((res.get(k) or {}).get("ahora"), int):
            cif[k] = res[k]["ahora"]
    return d.get("fecha"), {k: v for k, v in cif.items() if v is not None}


def _cifras_tablero(d):
    foto = d.get("foto") or {}
    return foto.get("fecha"), {
        "nivel": d.get("nivel"), "tarjetas": foto.get("tarjetas"),
        "duplicados": len((d.get("duplicados") or {}).get("pares") or []),
        "estancadas": len((d.get("estancamiento") or {}).get("tarjetas") or []),
        "sin_evidencia": len((d.get("evidencia") or {}).get("sin_evidencia") or []),
        "problemas_integridad": len((d.get("integridad") or {}).get("problemas") or []),
        "excesos_wip": len((d.get("wip") or {}).get("excesos") or [])}


def _cifras_releases(d):
    return d.get("fecha"), {"apps": len(d.get("apps") or []), "estado": d.get("estado")}


_RE_FICHAS_FECHA = re.compile(r"^# Vig[ií]a Fichas\s+[—-]\s+(\d{4}-\d{2}-\d{2})", re.M)
_RE_FICHAS_CIFRA = re.compile(r"\*{0,2}(\d+)\*{0,2}\s+([a-záéíóúñ() ]+?)\s*(?=·|$)", re.M)


CIFRAS_FICHAS = {"funcionalidades_no_mencionadas", "textos_obsoletos", "promesas_de_mas",
                 "app_revisadas", "sin_revisar_por_error", "sin_cambios", "fuera_del_chequeo"}


def _cifras_fichas(texto: str):
    """El Vigía Fichas solo deja Markdown: se leen las dos líneas de cifras de la cabecera."""
    m = _RE_FICHAS_FECHA.search(texto)
    cifras = {}
    for linea in texto.splitlines()[:8]:
        if linea.startswith("- "):
            for n, nombre in _RE_FICHAS_CIFRA.findall(linea[2:]):
                clave = re.sub(r"[^a-z0-9]+", "_", nombre.replace("(s)", "").strip()
                               .translate(str.maketrans("áéíóúñ", "aeioun"))).strip("_")
                if clave in CIFRAS_FICHAS:
                    cifras[clave] = int(n)
    return (m.group(1) if m else None), cifras


VIGIAS = {  # nombre: (ruta en el repo central, lector, es JSON)
    "soporte": ("docs/soporte/ultimo.json", _cifras_soporte, True),
    "contenidos": ("docs/web/vigia-contenidos/ultimo.json", _cifras_contenidos, True),
    "fichas": ("docs/web/vigia-fichas/ultimo.md", _cifras_fichas, False),
    "tablero": ("tablero/vigilante/ultimo.json", _cifras_tablero, True),
    "releases": ("docs/distribucion/tandas/", _cifras_releases, True),  # la tanda más reciente
}


class Central:
    """Lee ficheros del repo central en una referencia de git (por defecto origin/main: los
    vigías escriben en main desde GitHub y la copia local puede ir atrasada, a4)."""

    def __init__(self, raiz: str | Path, ref: str = "origin/main"):
        self.raiz, self.ref = Path(raiz), ref

    def _git(self, *args) -> str:
        r = subprocess.run(["git", "-C", str(self.raiz), *args], capture_output=True,
                           text=True, encoding="utf-8")
        if r.returncode:
            raise OSError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def actualizar(self) -> None:
        self._git("fetch", "-q", "origin")

    def leer(self, ruta: str) -> str:
        return self._git("show", f"{self.ref}:{ruta}")

    def listar(self, carpeta: str) -> list[str]:
        return self._git("ls-tree", "--name-only", f"{self.ref}", carpeta.rstrip("/") + "/"
                         ).splitlines()


def leer_vigias(central: Central) -> dict[str, dict]:
    """{<vigía>-<fecha>: datos de informe.* v1}. Un vigía que no se pueda leer se salta."""
    out = {}
    for vigia, (ruta, lector, es_json) in VIGIAS.items():
        try:
            if ruta.endswith("/"):
                tandas = sorted(p for p in central.listar(ruta) if re.search(r"\d{4}-\d{2}-\d{2}\.json$", p))
                if not tandas:
                    continue
                ruta = tandas[-1]
            texto = central.leer(ruta)
            fecha, cifras = lector(json.loads(texto) if es_json else texto)
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if not isinstance(fecha, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", fecha):
            continue
        out[f"{vigia}-{fecha}"] = _limpio({
            "vigia": vigia, "fecha": fecha, "ruta": ruta, "sha256": _sha(texto),
            "cifras": {k: v for k, v in cifras.items() if v is not None} or None})
    return out


# --- importar --------------------------------------------------------------------------------
def planear(reg, tarjetas: dict, ejecuciones: dict, informes: dict) -> list[dict]:
    """Los eventos que pondrían el registro al día con las fuentes (sin escribir nada)."""
    lote = []

    def clave(tipo, ent_id, datos, previo=""):
        return f"{tipo}:{ent_id}:{previo}{_sha(json.dumps(datos, sort_keys=True))[:16]}"

    previas = reg.entidades("tarjeta")
    for cid, datos in tarjetas.items():
        prev = previas.get(cid)
        if prev is None:
            lote.append({"tipo": "tarjeta.importada", "entidad": ("tarjeta", cid),
                         "clave": f"tarjeta.importada:{cid}", "datos": datos})
            continue
        anterior = prev.get("datos") or {}
        if prev["estado"] != "desaparecida" and \
                sin_movimientos(anterior) == sin_movimientos(datos):
            continue
        visto = anterior.get("actualizada", "")
        nuevos = [m for m in datos.get("movimientos") or [] if m.get("fecha", "") > visto]
        d = sin_movimientos(datos) | ({"movimientos": nuevos} if nuevos else {})
        lote.append({"tipo": "tarjeta.cambiada", "entidad": ("tarjeta", cid),
                     "clave": clave("tarjeta.cambiada", cid, d,
                                    f"n{len(prev['historial'])}-"), "datos": d})
    for cid, prev in previas.items():
        if cid not in tarjetas and prev["estado"] != "desaparecida":
            rev = int((prev.get("datos") or {}).get("revision", 0))
            lote.append({"tipo": "tarjeta.desaparecida", "entidad": ("tarjeta", cid),
                         "clave": f"tarjeta.desaparecida:{cid}:n{len(prev['historial'])}",
                         "datos": {"revision": rev}})

    for tipo_ent, fuente, nuevo, cambio in (
            ("ejecucion", ejecuciones, "ejecucion.importada", "ejecucion.cambiada"),
            ("informe", informes, "informe.importado", "informe.actualizado")):
        previas = reg.entidades(tipo_ent)
        for eid, datos in fuente.items():
            prev = previas.get(eid)
            if prev is None:
                lote.append({"tipo": nuevo, "entidad": (tipo_ent, eid),
                             "clave": f"{nuevo}:{eid}", "datos": datos})
            elif (prev.get("datos") or {}) != datos:
                lote.append({"tipo": cambio, "entidad": (tipo_ent, eid),
                             "clave": clave(cambio, eid, datos, f"n{len(prev['historial'])}-"),
                             "datos": datos})
    return lote


def importar(reg, tarjetas: dict, ejecuciones: dict, informes: dict,
             identidad: str = "importador-pc") -> dict:
    """Escribe los eventos de `planear` y devuelve cuántos de cada tipo y los rechazados."""
    lote = planear(reg, tarjetas, ejecuciones, informes)
    resultados = reg.escribir_lote(lote, PAPEL, identidad) if lote else []
    por_tipo: dict[str, int] = {}
    rechazos = []
    for e, r in zip(lote, resultados):
        if isinstance(r, Exception):
            rechazos.append({"entidad": f"{e['entidad'][0]}:{e['entidad'][1]}", "tipo": e["tipo"],
                             "motivo": str(r)})
        elif r.nuevo:
            por_tipo[e["tipo"]] = por_tipo.get(e["tipo"], 0) + 1
    return {"eventos": sum(por_tipo.values()), "por_tipo": dict(sorted(por_tipo.items())),
            "rechazados": rechazos}
