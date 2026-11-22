"""Cartera de apps generada desde updates/manifest.json (PAA2 P2-2, S-1265) y su comparación
con las cuatro listas que hoy se mantienen a mano (01 §7, a4):

1. `site/assets/catalog.js` (la web)           3. `docs/portability/app-matrix.md`
2. `docs/catalogo-apps.json` (Google Sheets)   4. `CONTEXT.md` §8 (Estado actual)

El manifest es la única fuente fiable (a4); la cartera no corrige ninguna lista, solo dice dónde
difieren. Precio y licencia no están en el manifest: no entran en la cartera.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_RE_VERSION = r"(\d+\.\d+\.\d+)"
_RE_RELEASE = re.compile(r"/releases/([a-z0-9-]+)/")
_RE_REPO = re.compile(r"github\.com/[^/]+/([^/]+)/")


def _slug_de_nombre(nombre: str) -> str | None:
    """«🐱 Clever Cat (ex-…)» → clever-cat: las dos primeras palabras con letras."""
    texto = re.sub(r"\(.*", "", nombre)
    palabras = re.findall(r"[A-Za-z]+", texto)
    return f"{palabras[0]}-{palabras[1]}".lower() if len(palabras) >= 2 else None


def _stack(repo_dir: Path | None) -> str | None:
    if repo_dir is None or not repo_dir.is_dir():
        return None
    if (repo_dir / "src-tauri").is_dir():
        return "tauri"
    for f in ("requirements.txt", "pyproject.toml"):
        p = repo_dir / f
        if p.is_file():
            return "pyside6" if "pyside6" in p.read_text(encoding="utf-8", errors="replace").lower() \
                else "python"
    return None


def generar(manifest: dict, repos: Path | None = None) -> dict[str, dict]:
    """{slug: ficha}. `repos` = carpeta con los repos locales (para el stack); opcional."""
    out = {}
    for t in manifest.get("tools") or []:
        slug = t.get("tool")
        if not slug:
            continue
        plat = t.get("platforms") or {}
        win, lin = plat.get("windows-x64") or {}, plat.get("linux-x64") or {}
        destino = _RE_RELEASE.search(str(win.get("installer_url") or ""))
        repo = _RE_REPO.search(str(t.get("notes_url") or ""))
        ficha = {
            "nombre": " ".join(str(t.get("display_name") or slug).split()[:2]),
            "canal": t.get("channel"),
            "version": t.get("version"),
            "fecha_release": t.get("release_date"),
            "windows": win.get("version") if win.get("status") in ("stable", "dev") else None,
            "linux": lin.get("version"),
            "linux_estado": lin.get("status"),
            "repo": repo.group(1) if repo else None,
        }
        if destino and destino.group(1) != slug:
            ficha["estado"], ficha["renombrada_a"] = "puente", destino.group(1)
        elif t.get("channel") == "stable":
            ficha["estado"] = "publicada"
        else:
            ficha["estado"] = "en_desarrollo"
        if repos is not None and ficha["repo"]:
            ficha["stack"] = _stack(Path(repos) / ficha["repo"])
        out[slug] = ficha
    return dict(sorted(out.items()))


# --- las cuatro listas ------------------------------------------------------------------------
def lista_catalog_js(texto: str) -> dict[str, dict]:
    out = {}
    trozos = re.split(r"\bid:\s*\"", texto)[1:]
    for t in trozos:
        slug = t.split('"', 1)[0]
        fin = re.search(r"\n\s{4}\},?\s*\n|\}\s*,\s*\n", t)
        cuerpo = t[: fin.end()] if fin else t
        m_type = re.search(r"\btype:\s*\"([a-z-]+)\"", cuerpo)
        m_el = re.search(r"\beligible:\s*(true|false)", cuerpo)
        m_off = re.search(r"\boffer:\s*\"([a-z-]+)\"", cuerpo)
        if not m_el:
            continue
        out[slug] = {"type": m_type.group(1) if m_type else None,
                     "eligible": m_el.group(1) == "true",
                     "offer": m_off.group(1) if m_off else None}
    return out


def lista_catalogo_apps(texto: str) -> dict[str, dict]:
    d = json.loads(texto)
    return {slug: {"seccion": sec} for sec in ("publicadas", "desarrollo", "ideas", "descartadas")
            for slug in (d.get(sec) or {})}


def _filas_tabla(lineas: list[str]) -> list[list[str]]:
    filas = []
    for l in lineas:
        if l.startswith("|") and not re.match(r"^\|\s*-", l):
            filas.append([c.strip() for c in l.strip().strip("|").split("|")])
    return filas


def lista_app_matrix(texto: str) -> dict[str, dict]:
    out = {}
    for f in _filas_tabla(texto.splitlines()):
        if len(f) < 3 or f[0].lower().startswith("herramienta"):
            continue
        slug = _slug_de_nombre(f[0])
        if not slug or slug in out:
            continue
        win, lin = re.search(r"v" + _RE_VERSION, f[1]), re.search(r"v" + _RE_VERSION, f[2])
        out[slug] = {"windows": win.group(1) if win else None,
                     "linux": lin.group(1) if lin else None}
    return out


def lista_context_8(texto: str) -> dict[str, dict]:
    m = re.search(r"^## 8\..*?$(.*?)^## 9", texto, re.S | re.M)
    out = {}
    for f in _filas_tabla((m.group(1) if m else "").splitlines()):
        if len(f) < 2 or f[0].lower().startswith("herramienta") or "Histórico" in f[1]:
            continue
        slug = _slug_de_nombre(f[0])
        if not slug or slug in out or slug == "resto-de":
            continue
        win = re.search(r"(?:\bv|Windows )" + _RE_VERSION, f[1])
        lin = re.search(r"Linux " + _RE_VERSION, f[1])
        out[slug] = {"windows": win.group(1) if win else None,
                     "linux": lin.group(1) if lin else None}
    return out


LISTAS = {  # nombre: (ruta en el repo central, lector)
    "catalog.js": ("site/assets/catalog.js", lista_catalog_js),
    "catalogo-apps.json": ("docs/catalogo-apps.json", lista_catalogo_apps),
    "app-matrix.md": ("docs/portability/app-matrix.md", lista_app_matrix),
    "CONTEXT.md §8": ("CONTEXT.md", lista_context_8),
}


def leer_listas(leer) -> dict[str, dict]:
    """`leer(ruta) -> texto` (p. ej. importar.Central.leer). Una lista ilegible queda en None."""
    out = {}
    for nombre, (ruta, lector) in LISTAS.items():
        try:
            out[nombre] = lector(leer(ruta))
        except (OSError, ValueError):
            out[nombre] = None
    return out


def _versiones(slug, ficha, fila, hallazgos):
    for plat in ("windows", "linux"):
        dice, real = fila.get(plat), ficha.get(plat)
        if dice and real and dice != real:
            hallazgos.append({"app": slug, "que": f"versión {plat}",
                              "lista": dice, "manifest": real})


def comparar_listas(cartera: dict[str, dict], listas: dict[str, dict | None]) -> dict:
    publicadas = {s for s, f in cartera.items() if f["estado"] == "publicada"}
    puentes = {s for s, f in cartera.items() if f["estado"] == "puente"}
    out = {"cartera": {"apps": len(cartera), "publicadas": len(publicadas),
                       "en_desarrollo": sum(f["estado"] == "en_desarrollo"
                                            for f in cartera.values()),
                       "puentes": sorted(puentes)},
           "listas": {}}
    for nombre, lista in listas.items():
        if lista is None:
            out["listas"][nombre] = {"error": "no se pudo leer"}
            continue
        h: list[dict] = []
        if nombre == "catalog.js":
            dice_pub = {s for s, x in lista.items() if x["eligible"]}
        elif nombre == "catalogo-apps.json":
            dice_pub = {s for s, x in lista.items() if x["seccion"] == "publicadas"}
        else:
            dice_pub = None
        for s in sorted(publicadas - set(lista)):
            h.append({"app": s, "que": "publicada en el manifest y no está en la lista"})
        if dice_pub is not None:
            for s in sorted((publicadas & set(lista)) - dice_pub):
                h.append({"app": s, "que": "publicada en el manifest y la lista no la da por "
                                           "publicada", "lista": _resumen_fila(lista[s])})
            for s in sorted(dice_pub - publicadas):
                h.append({"app": s, "que": "la lista la da por publicada y el manifest no",
                          "manifest": cartera.get(s, {}).get("estado", "no está")})
        for s in sorted(puentes & set(lista)):
            h.append({"app": s, "que": f"nombre antiguo (ahora {cartera[s]['renombrada_a']}): "
                                       "no es una app aparte"})
        if nombre in ("app-matrix.md", "CONTEXT.md §8"):
            for s in sorted(set(lista) & set(cartera)):
                _versiones(s, cartera[s], lista[s], h)
        fuera = sorted(set(lista) - set(cartera))
        out["listas"][nombre] = {
            "apps": len(lista),
            "dice_publicadas": len(dice_pub) if dice_pub is not None else None,
            "hallazgos": h,
            "no_estan_en_el_manifest": fuera,
        }
    return out


def _resumen_fila(fila: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in fila.items() if v is not None)
