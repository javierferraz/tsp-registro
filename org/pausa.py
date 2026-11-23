"""Interruptor general de pausa (02 §9; PAA2 P5-2, S-1273).

Una pausa es un evento `pausa.activada` con un **nivel** y un **ámbito**. Hay pausa para algo
mientras quede alguna activa cuyo ámbito lo alcance:

| Nivel | Efecto |
|---|---|
| suave | no arranca nada nuevo; lo que corre termina |
| dura | además, lo que corre guarda punto de control y para |
| emergencia | además, se desactivan los workflows que publican (politica/pausa.json) |

Ámbitos: `general` (o sin ámbito), `app:<slug>`, `proyecto:<id>`, `tipo:<tipo de trabajo>`,
`ejecucion:<tarea>`. «Para todo lo de Clever Cat» (`app:clever-cat`) no detiene lo demás.

Quién la respeta:
- el planificador no arranca lo que alcanza una pausa (cualquier nivel);
- el trabajador se entera en cada `org latido`: con dura o emergencia sale con 4 y dice que pare
  (guarda punto de control y escribe `tarea.pausada`);
- los workflows y los hooks preguntan con `org pausa comprobar` (salida 0, 10, 11 o 12);
- la emergencia la aplica `org pausa emergencia --hacer` donde haya `gh` con permiso (el PC).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

NIVELES = {"suave": 1, "dura": 2, "emergencia": 3}
SALIDA = {None: 0, "suave": 10, "dura": 11, "emergencia": 12}
PARA_LO_QUE_CORRE = ("dura", "emergencia")
ACCION = {None: "libre: sigue",
          "suave": "no arranques nada nuevo; lo que ya corre termina",
          "dura": "guarda punto de control y para",
          "emergencia": "guarda punto de control y para; publicar está desactivado"}


def slug(texto: str | None) -> str | None:
    """«2608-silent-frog», «javierferraz/2608-silent-frog» o «Silent Frog» → «silent-frog»."""
    if not texto:
        return None
    base = texto.replace("\\", "/").rstrip("/").split("/")[-1].lower()
    base = re.sub(r"^[0-9]{4}-", "", base)
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-") or None


def activas(reg) -> list[dict]:
    out = []
    for i, p in sorted(reg.entidades("pausa").items()):
        if p["estado"] == "activa":
            out.append({"id": i, "nivel": p["nivel"], "ambito": p.get("ambito") or "general",
                        "origen": p.get("origen"), "motivo": p.get("motivo"),
                        "desde": p["creada"]})
    return out


def que_es(tid: str | None = None, tarea: dict | None = None, tarjeta: dict | None = None,
           **extra) -> dict:
    """Lo que una pausa puede alcanzar de una tarea: su app (por repo o por la tarjeta), su
    proyecto u objetivo, su tipo de trabajo y la propia ejecución."""
    t, tj = tarea or {}, tarjeta or {}
    apps = {slug(t.get("repo")), slug(tj.get("app")), slug(extra.get("app"))} - {None}
    return {"apps": apps, "proyecto": extra.get("proyecto") or t.get("proyecto") or t.get("objetivo"),
            "tipo": extra.get("tipo") or t.get("tipo_trabajo"), "tarea": tid}


def alcanza(ambito: str, que: dict) -> bool:
    if ambito in ("general", "", None):
        return True
    clase, _, valor = ambito.partition(":")
    if clase == "app":
        return valor in que["apps"]
    if clase == "proyecto":
        return valor == que["proyecto"]
    if clase == "tipo":
        return valor == que["tipo"]
    if clase == "ejecucion":
        return valor == que["tarea"]
    return True  # un ámbito que no se entiende para: mejor de más que de menos


def nivel(pausas: list[dict]) -> str | None:
    return max((p["nivel"] for p in pausas), key=NIVELES.get, default=None)


def aplicables(pausas: list[dict], que: dict) -> list[dict]:
    return [p for p in pausas if alcanza(p["ambito"], que)]


def comprobar(reg, tarea: str | None = None, **extra) -> dict:
    """La respuesta para un workflow, un hook o un trabajador: qué nivel le alcanza y qué hacer."""
    t = reg.ver("tarea", tarea) if tarea else None
    tj = ((reg.ver("tarjeta", tarea) or {}).get("datos") or {}) if tarea else {}
    pausas = aplicables(activas(reg), que_es(tarea, t, tj, **extra))
    n = nivel(pausas)
    return {"nivel": n, "salida": SALIDA[n], "accion": ACCION[n],
            "pausas": [{k: p[k] for k in ("id", "nivel", "ambito")} for p in pausas]}


# --- Emergencia: desactivar los workflows que publican -----------------------------------

def _politica(raiz: Path) -> list[dict]:
    ruta = raiz / "politica" / "pausa.json"
    if not ruta.is_file():
        return []
    return json.loads(ruta.read_text(encoding="utf-8")).get("emergencia_desactiva", [])


def emergencia(reg, hacer: bool = False) -> dict:
    """Pone los workflows que publican de acuerdo con las pausas: con una emergencia activa (de
    cualquier ámbito: la fábrica publica todas las apps), desactivados; sin ella, vuelve a
    activar los que desactivó esta misma máquina. Lo que hizo se guarda en
    `.indice/emergencia.json` (local). Sin `hacer`, solo dice qué haría."""
    gh = os.environ.get("ORG_GH", "gh")
    hay = any(p["nivel"] == "emergencia" for p in activas(reg))
    estado_f = reg.raiz / ".indice" / "emergencia.json"
    hechos = json.loads(estado_f.read_text(encoding="utf-8")) if estado_f.is_file() else []
    clave = lambda w: f"{w['repo']}:{w['workflow']}"  # noqa: E731
    if hay:
        pasos = [("disable", w) for w in _politica(reg.raiz) if clave(w) not in hechos]
    else:
        pasos = [("enable", {"repo": c.split(":")[0], "workflow": c.split(":", 1)[1]})
                 for c in hechos]
    out = {"emergencia": hay, "pasos": [f"gh workflow {a} {w['workflow']} -R {w['repo']}"
                                        for a, w in pasos], "hecho": [], "fallos": []}
    if not hacer:
        return out
    for accion, w in pasos:
        r = subprocess.run([gh, "workflow", accion, w["workflow"], "-R", w["repo"]],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode:
            out["fallos"].append(f"{clave(w)}: {(r.stderr or r.stdout).strip()[:200]}")
            continue
        out["hecho"].append(f"{accion} {clave(w)}")
        if accion == "disable":
            hechos.append(clave(w))
        else:
            hechos.remove(clave(w))
    estado_f.parent.mkdir(parents=True, exist_ok=True)
    estado_f.write_text(json.dumps(hechos), encoding="utf-8")
    return out
