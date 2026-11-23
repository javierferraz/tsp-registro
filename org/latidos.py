"""Latidos de los procesos programados y quién avisa si faltan (02 §8.2; PAA2 P5-2, S-1273).

Cada proceso de `politica/procesos.json` tiene un latido en el registro (`proceso:<nombre>`):

- **propio**: lo escribe el proceso al acabar, bien o mal (`org latido-proceso`);
- **github** / **programador**: lo copia el registro diario del PC desde la última ejecución del
  workflow (`gh run list`) o de la tarea programada de Windows (`Get-ScheduledTaskInfo`), sin
  tocar esos procesos. Llevan `via: registro-diario`: si el diario no corre, solo se avisa de él.

Dos vigilantes, en dos sitios:

1. **El vigilante** (`org vigilar`, workflow `latidos.yml` de GitHub, cada 6 h): mira todos los
   procesos y escribe `proceso.alerta` cuando uno falta (más viejo que `cada_horas` +
   `margen_horas`) o su último resultado es «falla». Una alerta por cada último latido visto: no
   repite el aviso cada 6 horas. Deja su propio latido.
2. **El centinela** (`org centinela`, dentro del registro diario del PC): sencillo, solo mira que
   el vigilante de GitHub siga latiendo. Si no, avisa él.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import avisos
from .estado import _FMT

VIGILANTE = "vigilante-github"
VIA = "registro-diario"
_SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ahora(ahora):
    return ahora or datetime.now(timezone.utc)


def _iso(s: str) -> datetime:
    """Fechas de GitHub (…Z), de PowerShell (…+01:00) o del registro (…ffffffZ)."""
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.strip().replace("Z", "+00:00"))
    d = datetime.fromisoformat(s)  # 3.10 no admite más de 6 decimales ni la Z
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def politica(raiz: Path) -> dict:
    ruta = Path(raiz) / "politica" / "procesos.json"
    if not ruta.is_file():
        return {}
    return json.loads(ruta.read_text(encoding="utf-8"))["procesos"]


def latir(reg, nombre: str, resultado: str, identidad: str, papel: str = "motor",
          detalle: str | None = None, ahora=None) -> dict:
    """El latido propio de un proceso que acaba (bien o mal)."""
    ahora = _ahora(ahora)
    datos = {"resultado": resultado, "ejecutado": ahora.strftime(_FMT), "origen": "propio"}
    if detalle:
        datos["detalle"] = detalle[:200]
    r = reg.escribir("proceso.latido", ("proceso", nombre), papel, identidad,
                     f"latido:{nombre}:{ahora:%Y%m%dT%H%M%S%f}", datos, ahora=ahora)
    return {"proceso": nombre, "resultado": resultado, "id": r.id}


# --- Copiar latidos de GitHub y del Programador de tareas (en el PC) ------------------------

def _gh_ultimo(conf: dict) -> dict | None:
    r = subprocess.run([os.environ.get("ORG_GH", "gh"), "run", "list", "-R", conf["repo"],
                        "--workflow", conf["workflow"], "-L", "5", "--json",
                        "databaseId,conclusion,createdAt,status"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       creationflags=_SIN_VENTANA)
    if r.returncode:
        raise OSError((r.stderr or r.stdout).strip()[:200])
    hechos = [x for x in json.loads(r.stdout or "[]") if x.get("status") == "completed"]
    if not hechos:
        return None
    x = max(hechos, key=lambda x: x["createdAt"])
    ok = x.get("conclusion") in ("success", "skipped", "neutral")
    return {"resultado": "ok" if ok else "falla", "ejecutado": x["createdAt"],
            "detalle": f"run {x['databaseId']}: {x.get('conclusion')}", "clave": x["databaseId"]}


def _programador_ultimo(conf: dict) -> dict | None:
    ruta, _, nombre = conf["tarea"].rpartition("\\")
    ps = (f"$i = Get-ScheduledTaskInfo -TaskPath '{ruta}\\' -TaskName '{nombre}' "
          "-ErrorAction Stop; "
          "if ($i.LastRunTime.Year -lt 2000) { '{}' } else { @{ultima = "
          "$i.LastRunTime.ToUniversalTime().ToString('o'); resultado = $i.LastTaskResult} "
          "| ConvertTo-Json -Compress }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60,
                       creationflags=_SIN_VENTANA)
    if r.returncode:
        raise OSError((r.stderr or r.stdout).strip()[:200])
    d = json.loads(r.stdout or "{}")
    if not d:
        return None
    # 0 = bien; 267009 (0x41301) = sigue corriendo ahora mismo: aún no ha fallado
    ok = d["resultado"] in (0, 267009)
    return {"resultado": "ok" if ok else "falla", "ejecutado": d["ultima"],
            "detalle": f"resultado {d['resultado']}", "clave": d["ultima"]}


LECTORES = {"github": _gh_ultimo, "programador": _programador_ultimo}


def importar(reg, procesos: dict, identidad: str = "importador-pc", lectores=None,
             ahora=None) -> dict:
    """Copia el último latido de los procesos que no lo escriben ellos. Sin repetir: la clave
    es la ejecución (id del run o fecha de la última ejecución)."""
    lectores = lectores or LECTORES
    out = {"copiados": [], "iguales": 0, "errores": []}
    for nombre, conf in sorted(procesos.items()):
        leer = lectores.get(conf.get("fuente"))
        if not leer:
            continue
        try:
            u = leer(conf)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
            out["errores"].append(f"{nombre}: {e}")
            continue
        if not u:
            continue
        datos = {"resultado": u["resultado"], "ejecutado": _iso(u["ejecutado"]).strftime(_FMT),
                 "origen": conf["fuente"], "detalle": u["detalle"]}
        r = reg.escribir("proceso.latido", ("proceso", nombre), "importador", identidad,
                         f"latido:{nombre}:{u['clave']}", datos, ahora=ahora)
        if r.nuevo:
            out["copiados"].append(nombre)
        else:
            out["iguales"] += 1
    return out


# --- Vigilar ------------------------------------------------------------------------------

def revisar(reg, procesos: dict, ahora=None) -> list[dict]:
    """El estado de cada proceso: al día, falta, falla o sin datos (si depende de otro que falta)."""
    ahora = _ahora(ahora)
    ents = reg.entidades("proceso")
    filas = {}
    for nombre, conf in sorted(procesos.items()):
        u = (ents.get(nombre) or {}).get("ultimo")
        limite = conf["cada_horas"] + conf.get("margen_horas", 0)
        fila = {"proceso": nombre, "donde": conf.get("donde"), "esperado_horas": limite,
                "ultimo": u["ejecutado"] if u else None,
                "horas": round((ahora - _iso(u["ejecutado"])).total_seconds() / 3600, 1) if u else None,
                "via": conf.get("via")}
        if u and u["resultado"] == "falla":
            fila["clase"] = "falla"
        elif u and fila["horas"] <= limite:
            fila["clase"] = "al_dia"
        elif not u and ahora - _iso(conf["alta"]) <= timedelta(hours=limite):
            fila["clase"] = "al_dia"  # recién dado de alta: aún no le toca
        else:
            fila["clase"] = "falta"
        filas[nombre] = fila
    for f in filas.values():  # si quien copia sus latidos no corre, no se sabe nada de él
        via = filas.get(f["via"] or "")
        if f["clase"] == "falta" and via and via["clase"] != "al_dia":
            f["clase"] = "sin_datos"
    return list(filas.values())


def vigilar(reg, procesos: dict, *, identidad: str = VIGILANTE, escribir=False, enviar=False,
            solo=None, ahora=None) -> dict:
    """Escribe una alerta (y avisa) por cada proceso que falta o falla, una vez por cada último
    latido visto. `solo`: limitar a esos procesos (el centinela)."""
    ahora = _ahora(ahora)
    filas = revisar(reg, procesos, ahora)
    out = {"procesos": filas, "alertas": [], "avisos": []}
    for f in filas:
        if f["clase"] not in ("falta", "falla") or (solo and f["proceso"] not in solo):
            continue
        datos = {"clase": f["clase"], "esperado_horas": int(f["esperado_horas"]),
                 "vigilante": identidad}
        if f["ultimo"]:
            datos["ultimo"] = f["ultimo"]
        clave = f"alerta:{f['proceso']}:{f['clase']}:{f['ultimo'] or 'nunca'}"
        nueva = True
        if escribir:
            nueva = reg.escribir("proceso.alerta", ("proceso", f["proceso"]), "motor", identidad,
                                 clave, datos, ahora=ahora).nuevo
        if not nueva:
            continue
        sin = [x["proceso"] for x in filas if x["clase"] == "sin_datos" and x["via"] == f["proceso"]]
        texto = (f"🟡 Falta el latido de {f['proceso']} ({f['donde']}): "
                 + (f"el último es de hace {f['horas']} h" if f["ultimo"] else "no ha latido nunca")
                 + f"; se espera cada {f['esperado_horas']} h como mucho."
                 if f["clase"] == "falta" else
                 f"🔴 Ha fallado {f['proceso']} ({f['donde']}) en su última ejecución "
                 f"({f['ultimo']}).")
        if sin:
            texto += f" Sin datos de lo que depende de él: {', '.join(sin)}."
        out["alertas"].append({"proceso": f["proceso"], "clase": f["clase"]})
        out["avisos"].append(texto)
        if escribir:
            avisos.avisar(reg, texto, enviar)
    return out


def centinela(reg, procesos: dict, *, escribir=True, enviar=False, ahora=None) -> dict:
    """El segundo vigilante, desde el PC: solo que el de GitHub siga latiendo."""
    if VIGILANTE not in procesos:
        return {"procesos": [], "alertas": [], "avisos": []}
    return vigilar(reg, procesos, identidad="centinela-pc", escribir=escribir, enviar=enviar,
                   solo={VIGILANTE}, ahora=ahora)


def informe_md(r: dict) -> str:
    marca = {"al_dia": "✅", "falta": "🟡", "falla": "🔴", "sin_datos": "⚪"}
    L = ["| Proceso | Dónde | Último | Horas | Tope | Estado |", "|---|---|---|---|---|---|"]
    for f in r["procesos"]:
        L.append(f"| {f['proceso']} | {f['donde']} | {(f['ultimo'] or '—')[:16]} | "
                 f"{f['horas'] if f['horas'] is not None else '—'} | {f['esperado_horas']} | "
                 f"{marca[f['clase']]} {f['clase']} |")
    return "\n".join(L) + "\n"
