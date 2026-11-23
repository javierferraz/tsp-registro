"""Validador central (02 §10, 03 §2 y §10; PAA2 P4-2, S-1270).

Un programa sin modelo que decide si una tarea entregada pasa a «verificada». **No se fía del
informe:** vuelve a ejecutar las pruebas del repo, lee cada evidencia y comprueba que el informe
casa con el diff. Es el único que escribe «verificada» (y con su sello, ver `sello.py`).

Qué hace, en orden, para una tarea en `entregada` con informe v2:

1. **Quien implementa no verifica.** Si la identidad del validador es la del ejecutor o la de
   quien entregó, se niega sin escribir nada.
2. **Copia limpia** del repo de la tarea en una carpeta temporal (`git fetch` desde la fuente:
   la carpeta local del repo o su remoto). La fuente no se toca.
3. **Cabeza y base.** La cabeza es la punta de la rama entregada (o el commit que contiene a los
   demás). La base, la rama principal del repo. Cada commit declarado tiene que existir, estar en
   la cabeza y no estar ya en la base. Los ficheros declarados tienen que ser los del diff.
4. **Pruebas.** Las de `.claude/checks.json` **de la base** (el trabajador no puede rebajarlas en
   su rama), ejecutadas sobre la cabeza, con un entorno sin secretos. Su salida va a un fichero
   cuyo hash queda en el registro (`evidencia.verificada`).
5. **Criterios.** Cada criterio de la tarea tiene que estar en el informe. Un «ok» necesita una
   evidencia que una máquina pueda leer: `check:<nombre>` (una prueba de las anteriores, en
   verde), `url:<https://…>` (responde), `registro:<id de evento>` (una evidencia del validador
   o del laboratorio) o la ruta de un fichero de la cabeza, con `#sha256=<hex>` si se quiere fijar
   su contenido. Lo demás no cuenta (decisiones D-6). «no_ejecutado» se acepta y queda a la vista
   como «no verificado».
6. **Web:** cada URL de la tarea responde y contiene lo esperado.
7. **Verificador distinto del autor** para lo que toca interfaz o publica (y para M y L): una
   `revision.emitida` en «ok» sobre esa misma cabeza, de una identidad que no hizo la tarea. Si
   toca interfaz, además la medida visual del laboratorio (`evidencia.verificada` de tipo
   `medida_visual`).

Veredicto: `tarea.verificada` (v2) si no hay fallos; `tarea.devuelta` (v2) con la lista si los
hay; `tarea.bloqueada` si no se puede verificar (repo sin pruebas, tipo sin validador) o tras 3
devoluciones (02 §9 ter). Cada veredicto lleva la política aplicada y el coste en segundos (y el
run de GitHub). `org gasto` cuenta después los minutos facturables de GitHub.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .esquemas import Rechazo

POLITICA_VERSION = 1
MAX_DEVOLUCIONES = 3          # 02 §9 ter: devoluciones antes de bloquear
TIPOS_VALIDABLES = ("codigo", "interfaz", "web", "tablero")
CENTRAL = "2606-thestromboliproject"  # donde vive tablero/board.json
FUENTES = os.environ.get("ORG_FUENTES", "C:/xampp/htdocs")
REMOTO = "https://github.com/javierferraz/{repo}.git"
# Nada de esto llega a las pruebas: el código entregado se ejecuta y no tiene que ver secretos.
_RE_SECRETO = re.compile(r"(TOKEN|SECRET|SELLO|KEY|PASSWORD|CLAVE)", re.I)
_RE_HASH = re.compile(r"^(?P<ruta>.+?)#sha256=(?P<hex>[0-9a-f]{64})$")


def politica(tarea: dict) -> dict:
    tipo = tarea.get("tipo_trabajo") or "codigo"
    if tipo == "tablero":  # P4-3: tareas que nacen de una tarjeta (puente.py)
        return {"nombre": f"validador/tablero@{POLITICA_VERSION}", "tipo": tipo,
                "revision": False, "laboratorio": False, "urls": []}
    interfaz = tipo == "interfaz" or bool(tarea.get("toca_interfaz"))
    publica = tipo == "web" or bool(tarea.get("publica"))
    return {"nombre": f"validador/{tipo}@{POLITICA_VERSION}", "tipo": tipo,
            "revision": interfaz or publica or tarea.get("tamano") in ("M", "L"),
            "laboratorio": interfaz, "urls": tarea.get("urls", [])}


# --- git ---------------------------------------------------------------------------------
def _git(cwd, *args, check=True, binario=False):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                       text=not binario, stdin=subprocess.DEVNULL,
                       **({} if binario else {"encoding": "utf-8", "errors": "replace"}))
    if check and r.returncode:
        err = r.stderr if not binario else r.stderr.decode("utf-8", "replace")
        raise Rechazo(f"git {' '.join(args)}: {err.strip()}")
    return r


def _rev(cwd, ref) -> str | None:
    r = _git(cwd, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def _ancestro(cwd, a, b) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


def fuente_de(repo: str, fuente: str | None) -> str:
    if fuente:
        return fuente
    local = Path(FUENTES) / repo
    return str(local) if (local / ".git").exists() else REMOTO.format(repo=repo)


def copia_limpia(fuente: str, destino: Path) -> None:
    """Un repo nuevo con las ramas de la fuente en `f/` (las locales) y `fr/` (las remotas que
    la fuente conozca: un checkout de Actions solo tiene esas). La fuente no se toca."""
    destino.mkdir(parents=True)
    _git(destino, "init", "-q")
    _git(destino, "fetch", "-q", "--no-tags", fuente, "+refs/heads/*:refs/remotes/f/*",
         "+refs/remotes/origin/*:refs/remotes/fr/*")


def _ref(cwd, nombre: str) -> str | None:
    for ref in (f"f/{nombre}", f"fr/{nombre}", nombre):
        sha = _rev(cwd, ref)
        if sha:
            return sha
    return None


# --- comprobaciones ------------------------------------------------------------------------
class _Juicio:
    def __init__(self):
        self.comprobaciones: list[dict] = []
        self.fallos: list[str] = []
        self.no_verificado: list[str] = []

    def ok(self, nombre, detalle=None):
        self.comprobaciones.append({"nombre": nombre, "resultado": "ok",
                                    **({"detalle": detalle[:300]} if detalle else {})})

    def falla(self, nombre, detalle):
        self.comprobaciones.append({"nombre": nombre, "resultado": "falla",
                                    "detalle": detalle[:300]})
        self.fallos.append(f"{nombre}: {detalle}"[:300])

    def sin_verificar(self, nombre, detalle):
        self.comprobaciones.append({"nombre": nombre, "resultado": "no_verificado",
                                    "detalle": detalle[:300]})
        self.no_verificado.append(f"{nombre}: {detalle}"[:300])


def _entorno_limpio() -> dict:
    return {k: v for k, v in os.environ.items() if not _RE_SECRETO.search(k)}


# En GitHub, el código entregado corre como otro usuario sin privilegios (lo crea el workflow):
# no puede escribir la decisión ni la salida de las pruebas, ni leer la memoria del runner, y sus
# procesos mueren al acabar. En local no hay aislamiento (límite de 02 §3.1 bis).
AISLAR = "ORG_AISLAR_USUARIO"


def _aislado(cmd: list[str]) -> list[str]:
    u = os.environ.get(AISLAR)
    if not u:
        return cmd
    return ["sudo", "-u", u, "--", "env", f"PATH={os.environ.get('PATH', '')}",
            f"HOME=/home/{u}", *cmd]


def _ceder(copia: Path, a_otro: bool) -> None:
    """Da la copia al usuario aislado (y se la devuelve después, matando lo que dejara vivo)."""
    u = os.environ.get(AISLAR)
    if not u:
        return
    if a_otro:
        copia.parent.chmod(0o755)
        subprocess.run(["sudo", "chown", "-R", u, str(copia)], check=True,
                       stdin=subprocess.DEVNULL)
    else:
        subprocess.run(["sudo", "pkill", "-KILL", "-u", u], stdin=subprocess.DEVNULL)
        subprocess.run(["sudo", "chown", "-R", str(os.getuid()), str(copia)], check=True,
                       stdin=subprocess.DEVNULL)


def ejecutar_checks(checks: list[dict], cwd: Path, log: Path) -> dict[str, tuple[bool, str]]:
    """{nombre: (en verde, resumen)}. La salida completa va a `log`."""
    out = {}
    with log.open("w", encoding="utf-8", newline="\n") as f:
        for c in checks:
            nombre = c.get("name") or " ".join(c.get("cmd", []))[:60]
            cmd = list(c.get("cmd") or [])
            if not cmd:
                out[nombre] = (False, "sin orden")
                continue
            if cmd[0] in ("python", "python3", "py"):
                cmd[0] = sys.executable
            t0 = time.monotonic()
            try:
                r = subprocess.run(_aislado(cmd), cwd=cwd / c.get("cwd", "."), capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=c.get("timeout_s", 600), env=_entorno_limpio(),
                                   stdin=subprocess.DEVNULL)
                verde, salida, codigo = r.returncode == 0, r.stdout + r.stderr, r.returncode
            except subprocess.TimeoutExpired as e:
                verde, salida, codigo = False, f"{e.stdout or ''}{e.stderr or ''}", "timeout"
            except OSError as e:
                verde, salida, codigo = False, str(e), "no arranca"
            seg = time.monotonic() - t0
            f.write(f"### {nombre} · salida {codigo} · {seg:.1f} s\n{salida}\n")
            ultima = next((l for l in reversed(salida.strip().splitlines()) if l.strip()), "")
            out[nombre] = (verde, f"salida {codigo}, {seg:.0f} s: {ultima.strip()}"[:200])
    return out


def _abrir_url(url: str, timeout=20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "tsp-registro-validador"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https de la tarea)
            return r.status, r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


def _url(juicio, nombre, url, contiene, abrir) -> bool:
    if not url.startswith(("https://", "http://")):
        juicio.falla(nombre, f"no es una URL: {url}")
        return False
    estado, cuerpo = abrir(url)
    if not 200 <= estado < 400:
        juicio.falla(nombre, f"{url} responde {estado or 'nada'}")
        return False
    if contiene and contiene not in cuerpo:
        juicio.falla(nombre, f"{url} no contiene «{contiene}»")
        return False
    juicio.ok(nombre, f"{url} responde {estado}")
    return True


# --- el validador --------------------------------------------------------------------------
def _sha256(datos: bytes) -> str:
    return "sha256:" + hashlib.sha256(datos).hexdigest()


def _juzgar_tablero(t: dict, copia: Path, res: dict, j: _Juicio, central: Path | None) -> dict:
    """Modo tablero (P4-3, S-1271): la tarea es una tarjeta y su trabajo ya se fusionó (en el repo
    central se fusiona antes de Por validar, PAA M8). Con commits: cada uno existe y está en la
    rama principal; las pruebas pasaron al fusionar y no se repiten. Sin commits (decisiones,
    gestiones): solo vale que Javier la haya pasado a Terminada en el navegador (sin firma)."""
    base = _ref(copia, "main") or _ref(copia, "master")
    if not base:
        res["bloqueo"] = "no encuentro la rama principal del repo (main o master)"
        return res
    res["cabeza"] = base
    commits = (t.get("entrega") or {}).get("commits") or []
    if commits:
        for c in commits:
            sha = _rev(copia, c)
            if not sha:
                j.falla("commits", f"el commit {c} no existe en el repo")
            elif not _ancestro(copia, sha, base):
                j.falla("commits", f"{c} no está en la rama principal: falta fusionarlo")
        if not j.fallos:
            j.ok("commits", f"{len(commits)} commit(s) ya en la rama principal {base[:12]}")
        j.sin_verificar("pruebas", "modo tablero: pasaron al fusionar en la rama principal; "
                                   "el validador no las repite")
        return res
    origen = central or copia
    rama = _ref(origen, "main") or _ref(origen, "master")
    texto = _git(origen, "show", f"{rama}:tablero/board.json", check=False) if rama else None
    if texto is None or texto.returncode:
        res["bloqueo"] = "sin commits y sin acceso a tablero/board.json para ver quién la cerró"
        return res
    desde = max([t.get("creada", "")] + [h["fecha"] for h in t.get("historial", [])
                                         if h.get("tipo") == "tarea.reabierta"]
                + [(t.get("veredicto") or {}).get("fecha", "")])
    hito = datetime.strptime(desde, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    for e in reversed(json.loads(texto.stdout).get("events") or []):
        if (e.get("cardId") == t["id"] and e.get("type") == "card.transition"
                and str(e.get("summary", "")).endswith("-> hecha")
                and (e.get("actor") or {}).get("kind") == "human"):
            try:
                cuando = datetime.strptime(e["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if cuando >= hito:
                j.ok("cierre", f"sin commits: Javier la pasó a Terminada en el navegador "
                               f"({e['at']}, sin firma)")
                return res
    res["bloqueo"] = ("sin commits: no hay nada que comprobar; la cierra Javier desde el "
                      "navegador (o con su firma)")
    return res


def juzgar(t: dict, copia: Path, carpeta_evidencias: Path, abrir=_abrir_url,
           central: Path | None = None) -> dict:
    """El juicio de una entrega sobre una copia limpia ya preparada. No escribe en el registro."""
    pol = politica(t)
    j = _Juicio()
    ent = t.get("entrega") or {}
    res = {"politica": pol["nombre"], "juicio": j, "cabeza": "", "log": None,
           "bloqueo": None}
    if pol["tipo"] not in TIPOS_VALIDABLES:
        res["bloqueo"] = f"el validador aún no sabe validar trabajo de tipo «{pol['tipo']}»"
        return res
    if pol["tipo"] == "tablero":
        return _juzgar_tablero(t, copia, res, j, central)
    if "criterios" not in ent:
        j.falla("informe", "informe de entrega v1: el validador solo acepta el v2 "
                           "(criterios con evidencia, ficheros, commits)")
        return res

    # Base y cabeza
    base = _ref(copia, t["rama_base"]) if t.get("rama_base") else (
        _ref(copia, "main") or _ref(copia, "master"))
    if not base:
        res["bloqueo"] = "no encuentro la rama principal del repo (main o master)"
        return res
    commits = ent.get("commits", [])
    resueltos = {}
    for c in commits:
        sha = _rev(copia, c)
        if not sha:
            j.falla("commits", f"el commit {c} no existe en el repo")
        else:
            resueltos[c] = sha
    cabeza = None
    if ent.get("rama"):
        cabeza = _ref(copia, ent["rama"])
        if not cabeza:
            j.falla("rama", f"la rama {ent['rama']} no está en el repo (¿sin subir?)")
    elif resueltos:
        shas = list(resueltos.values())
        cabeza = next((c for c in shas if all(_ancestro(copia, o, c) for o in shas)), None)
        if not cabeza:
            j.falla("commits", "ningún commit declarado contiene a los demás: no sé cuál es "
                               "la cabeza (declara la rama)")
    if not commits:
        j.falla("commits", "el informe no declara ningún commit")
    if not cabeza:
        return res
    res["cabeza"] = cabeza
    for c, sha in resueltos.items():
        if not _ancestro(copia, sha, cabeza):
            j.falla("commits", f"{c} no está en la cabeza entregada")
        elif _ancestro(copia, sha, base):
            j.falla("commits", f"{c} ya estaba en la rama principal: no es trabajo de la entrega")
    if not j.fallos:
        j.ok("commits", f"{len(resueltos)} commit(s) en la cabeza {cabeza[:12]}")

    # Ficheros declarados frente al diff
    mb = _git(copia, "merge-base", base, cabeza, check=False).stdout.strip() or base
    cambiados = set(_git(copia, "diff", "--name-only", "--no-renames", mb, cabeza)
                    .stdout.split("\n")) - {""}
    declarados = set(ent.get("ficheros", []))
    sin_declarar, de_mas = sorted(cambiados - declarados), sorted(declarados - cambiados)
    if sin_declarar:
        j.falla("ficheros", f"cambiados y no declarados: {', '.join(sin_declarar[:8])}")
    if de_mas:
        j.falla("ficheros", f"declarados y sin cambios en el diff: {', '.join(de_mas[:8])}")
    if not sin_declarar and not de_mas:
        j.ok("ficheros", f"{len(cambiados)} fichero(s), los del diff")

    # Pruebas de la base, sobre la cabeza
    checks_json = _git(copia, "show", f"{base}:.claude/checks.json", check=False)
    if checks_json.returncode:
        res["bloqueo"] = "el repo no tiene .claude/checks.json en su rama principal: no hay " \
                         "pruebas que repetir"
        return res
    try:
        checks = json.loads(checks_json.stdout).get("checks") or []
    except ValueError as e:
        res["bloqueo"] = f".claude/checks.json de la base ilegible: {e}"
        return res
    if not checks:
        res["bloqueo"] = ".claude/checks.json de la base no tiene pruebas"
        return res
    _git(copia, "checkout", "-q", "--detach", "--force", cabeza)
    carpeta_evidencias.mkdir(parents=True, exist_ok=True)
    log = carpeta_evidencias / f"{t['id']}-{cabeza[:12]}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.log"
    _ceder(copia, True)
    try:
        resultados = ejecutar_checks(checks, copia, log)
    finally:
        _ceder(copia, False)
    res["log"] = log
    for nombre, (verde, resumen) in resultados.items():
        (j.ok if verde else j.falla)(f"prueba «{nombre}»", resumen)

    # Criterios del informe
    previstos = len(t.get("criterios") or [])
    vistos = {c["n"] for c in ent["criterios"]}
    faltan = [n for n in range(1, previstos + 1) if n not in vistos]
    if faltan:
        j.falla("criterios", f"el informe no dice nada de los criterios {faltan}")
    evidencias_registro = {e["evento"]: e for e in t.get("evidencias", [])}
    for c in ent["criterios"]:
        nombre = f"criterio {c['n']}"
        if c["estado"] == "falla":
            j.falla(nombre, f"el propio informe dice que falla: {c.get('motivo', '')}")
            continue
        if c["estado"] == "no_ejecutado":
            j.sin_verificar(nombre, c.get("motivo", "no ejecutado"))
            continue
        ev = c.get("evidencia", "")
        if ev.startswith("check:"):
            r = resultados.get(ev[6:])
            if r is None:
                j.falla(nombre, f"cita la prueba «{ev[6:]}», que no está en checks.json")
            elif not r[0]:
                j.falla(nombre, f"su prueba «{ev[6:]}» falla al repetirla")
            else:
                j.ok(nombre, f"prueba «{ev[6:]}» en verde al repetirla")
        elif ev.startswith(("url:", "https://", "http://")):
            _url(j, nombre, ev[4:] if ev.startswith("url:") else ev, None, abrir)
        elif ev.startswith("registro:"):
            e = evidencias_registro.get(ev[9:])
            if e is None:
                j.falla(nombre, f"cita la evidencia {ev[9:]}, que no está en el registro")
            else:
                j.ok(nombre, f"evidencia {e['tipo']} de {e['autor']['identidad']}")
        else:
            m = _RE_HASH.match(ev)
            ruta = m["ruta"] if m else ev
            if not ruta or " " in ruta.strip() or not re.search(r"[\w-]\.\w+$|/", ruta):
                j.falla(nombre, f"evidencia que una máquina no puede leer: «{ev[:80]}»")
                continue
            blob = _git(copia, "show", f"{cabeza}:{ruta.lstrip('./')}", check=False,
                        binario=True)
            if blob.returncode:
                j.falla(nombre, f"cita {ruta}, que no existe en la cabeza entregada")
            elif m and _sha256(blob.stdout) != f"sha256:{m['hex']}":
                j.falla(nombre, f"{ruta} no tiene el contenido declarado (hash distinto)")
            else:
                j.ok(nombre, f"{ruta} existe en la cabeza" + (" con su hash" if m else ""))

    # Web: cada URL responde y contiene lo esperado
    for i, u in enumerate(pol["urls"], 1):
        _url(j, f"url {i}", u["url"], u.get("contiene"), abrir)

    # Verificador distinto del autor
    if pol["revision"]:
        revs = t.get("revisiones", [])
        buenas = [r for r in revs if r["veredicto"] == "ok"
                  and len(r["cabeza"]) >= 7 and cabeza.startswith(r["cabeza"])]
        if buenas:
            j.ok("revisión independiente", f"«ok» de {buenas[-1]['autor']['identidad']}")
        elif revs:
            r = revs[-1]
            j.falla("revisión independiente",
                    f"la última es «{r['veredicto']}» sobre {r['cabeza'][:12]}, no «ok» sobre "
                    f"la cabeza {cabeza[:12]}")
        else:
            j.falla("revisión independiente", "toca interfaz, publica o es M/L: hace falta una "
                                              "revisión de otra ejecución (org revisar)")
    if pol["laboratorio"]:
        lab = [e for e in t.get("evidencias", []) if e["autor"]["papel"] == "laboratorio"
               and e["tipo"] == "medida_visual" and e["fecha"] >= ent.get("fecha", "")]
        if lab:
            j.ok("medida visual", f"del laboratorio ({lab[-1]['autor']['identidad']})")
        else:
            j.falla("medida visual", "toca interfaz: falta la medida visual del laboratorio")
    return res


def _entregada(reg, tarea_id: str, identidad: str) -> dict:
    t = reg.ver("tarea", tarea_id)
    if t is None:
        raise Rechazo(f"no existe tarea:{tarea_id}")
    if t["estado"] != "entregada":
        raise Rechazo(f"tarea {tarea_id}: está «{t['estado']}», no «entregada»")
    autores = {(t.get("ejecucion") or {}).get("ejecutor"), t.get("entregada_por")} - {None}
    if identidad in autores:
        raise Rechazo(f"{identidad} hizo la tarea {tarea_id}: quien implementa no verifica")
    return t


def validar(reg, tarea_id: str, identidad: str, *, fuente: str | None = None,
            evidencias: Path | None = None, ensayo=False, subir=True, abrir=_abrir_url) -> dict:
    """Valida la entrega y, salvo en ensayo, escribe evidencia y veredicto con papel validador."""
    d = decidir(reg, tarea_id, identidad, fuente=fuente, evidencias=evidencias, abrir=abrir)
    if ensayo:
        return d
    return registrar(reg, d, identidad, subir=subir)


def decidir(reg, tarea_id: str, identidad: str, *, fuente: str | None = None,
            evidencias: Path | None = None, abrir=_abrir_url) -> dict:
    """El juicio, sin escribir nada. En GitHub corre en un job sin secretos: el código entregado
    se ejecuta aquí y no puede ver la clave del sello (la usa `registrar`, en otro job)."""
    t0 = time.monotonic()
    t = _entregada(reg, tarea_id, identidad)
    carpeta = Path(evidencias or (reg.raiz / ".indice" / "evidencias"))
    tmp = Path(tempfile.mkdtemp(prefix="org-validar-"))
    try:
        copia = tmp / "repo"
        try:
            copia_limpia(fuente_de(t["repo"], fuente), copia)
            central = None
            if (t.get("tipo_trabajo") == "tablero" and t["repo"] != CENTRAL
                    and not (t.get("entrega") or {}).get("commits")):
                central = tmp / "central"  # quién la cerró está en el tablero del central
                copia_limpia(fuente_de(CENTRAL, None), central)
            res = juzgar(t, copia, carpeta, abrir, central)
        except Rechazo as e:
            res = {"politica": politica(t)["nombre"], "juicio": _Juicio(), "cabeza": "",
                   "log": None, "bloqueo": f"no pude preparar la copia del repo: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    j = res["juicio"]
    devoluciones = t.get("devoluciones", 0)
    if res["bloqueo"]:
        veredicto = "bloqueada"
    elif j.fallos:
        veredicto = "bloqueada" if devoluciones >= MAX_DEVOLUCIONES else "devuelta"
    else:
        veredicto = "verificada"
    coste = {"segundos": int(round(time.monotonic() - t0)),
             "runner": os.environ.get("RUNNER_OS", "local")}
    if os.environ.get("GITHUB_RUN_ID"):
        coste["run"] = os.environ["GITHUB_RUN_ID"]
    datos = {"politica": res["politica"], "cabeza": res["cabeza"] or "-",
             "comprobaciones": j.comprobaciones, "coste": coste}
    if j.no_verificado:
        datos["no_verificado"] = j.no_verificado
    if veredicto == "devuelta":
        datos["motivo"] = "; ".join(j.fallos)[:1000]
    out = {"tarea": tarea_id, "veredicto": veredicto, **datos, "fallos": j.fallos,
           "bloqueo": res["bloqueo"], "devoluciones": devoluciones,
           "entrega": (t.get("entrega") or {}).get("fecha")}
    if res["log"]:
        out["log"] = str(res["log"])
        out["log_hash"] = _sha256(res["log"].read_bytes())
    return out


_DATOS = ("politica", "cabeza", "comprobaciones", "coste", "no_verificado", "motivo")


def registrar(reg, d: dict, identidad: str, *, log: Path | None = None, subir=True) -> dict:
    """Escribe evidencia y veredicto de una decisión de `decidir` (papel validador, con sello).
    No ejecuta nada de la entrega. Se niega si la tarea ha cambiado desde que se juzgó (otra
    entrega, otra devolución) o si la salida de las pruebas no es la que se juzgó."""
    tarea_id, veredicto = d["tarea"], d["veredicto"]
    t = _entregada(reg, tarea_id, identidad)
    if ((t.get("entrega") or {}).get("fecha"), t.get("devoluciones", 0)) != (
            d.get("entrega"), d.get("devoluciones", 0)):
        raise Rechazo(f"tarea {tarea_id}: ha cambiado desde que se juzgó; vuelve a validarla")
    log = Path(log or d["log"]) if (log or d.get("log")) else None
    if log is not None and _sha256(log.read_bytes()) != d.get("log_hash"):
        raise Rechazo("la salida de las pruebas no es la que se juzgó (hash distinto)")
    datos = {k: d[k] for k in _DATOS if k in d}
    if veredicto != "devuelta":
        datos.pop("motivo", None)
    clave = f"{tarea_id}:validador:{d['cabeza'][:12]}:{d.get('devoluciones', 0)}"
    if log is not None:
        ruta = (f"actions:{os.environ['GITHUB_RUN_ID']}/{log.name}"
                if os.environ.get("GITHUB_RUN_ID") else log.as_posix())
        reg.escribir("evidencia.verificada", ("tarea", tarea_id), "validador", identidad,
                     f"{clave}:log", {"tipo": "salida_de_pruebas", "ruta": ruta,
                                      "hash": d["log_hash"],
                                      "descripcion": f"pruebas repetidas sobre {d['cabeza'][:12]}"})
    if veredicto == "verificada":
        r = reg.escribir("tarea.verificada", ("tarea", tarea_id), "validador", identidad,
                         f"{clave}:verificada", datos, version=2)
        if d["politica"].startswith("validador/tablero@"):  # ya está en la rama principal
            reg.escribir("tarea.integrada", ("tarea", tarea_id), "validador", identidad,
                         f"{clave}:integrada",
                         {"commit": d["cabeza"]} if d["cabeza"] != "-" else {})
            r = reg.escribir("tarea.cerrada", ("tarea", tarea_id), "validador", identidad,
                             f"{clave}:cerrada", {"motivo": "verificada en modo tablero"})
    elif veredicto == "devuelta":
        r = reg.escribir("tarea.devuelta", ("tarea", tarea_id), "validador", identidad,
                         f"{clave}:devuelta", datos, version=2)
    else:
        motivo = d.get("bloqueo") or (f"{d.get('devoluciones', 0)} devoluciones y sigue "
                                      "fallando: " + "; ".join(d.get("fallos", [])))
        r = reg.escribir("tarea.bloqueada", ("tarea", tarea_id), "validador", identidad,
                         f"{clave}:bloqueada", {"motivo": motivo[:1000]})
    out = {**d, "evento": r.id}
    if subir:
        out["subida"] = reg.subir()
    return out


def autoprueba(repo_dir: Path, identidad: str) -> dict:
    """Valida, en un registro temporal, una entrega sintética del último commit de este repo
    (sus pruebas, en verde). Sirve para medir en GitHub cuánto cuesta una validación real sin
    tocar el registro de verdad ni necesitar el sello."""
    from .registro import Registro

    repo_dir = Path(repo_dir)
    cabeza = _git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    base = _git(repo_dir, "rev-parse", "HEAD~1").stdout.strip()
    ficheros = sorted(set(_git(repo_dir, "diff", "--name-only", "--no-renames", base, cabeza)
                          .stdout.split("\n")) - {""})
    tmp = Path(tempfile.mkdtemp(prefix="org-autoprueba-"))
    try:
        reg = Registro(tmp / "registro")
        tid, T = "AUTO-1", ("tarea", "AUTO-1")
        reg.escribir("tarea.propuesta", T, "direccion", "autoprueba", f"{tid}:p", {
            "titulo": "autoprueba del validador", "repo": repo_dir.name,
            "tipo_trabajo": "codigo", "tamano": "S", "rama_base": base,
            "criterios": ["las pruebas del repo pasan"]}, version=2)
        reg.escribir("tarea.aprobada", T, "javier", "autoprueba", f"{tid}:a", {})
        reg.escribir("tarea.lista", T, "planificador", "autoprueba", f"{tid}:l", {})
        reg.escribir("tarea.asignada", T, "planificador", "autoprueba", f"{tid}:as",
                     {"ejecutor": "autoprueba-trabajador"})
        checks = json.loads(_git(repo_dir, "show", f"{base}:.claude/checks.json").stdout)
        primero = checks["checks"][0]["name"]
        reg.escribir("tarea.entregada", T, "trabajador", "autoprueba-trabajador", f"{tid}:e", {
            "resumen": "último commit del repo", "commits": [cabeza], "ficheros": ficheros,
            "criterios": [{"n": 1, "estado": "ok", "evidencia": f"check:{primero}"}],
            "decisiones": [], "descubierto": [], "coste": {"minutos": 0}}, version=2)
        return validar(reg, tid, identidad, fuente=str(repo_dir), subir=False,
                       evidencias=tmp / "evidencias")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- minutos de GitHub ---------------------------------------------------------------------
MULTIPLICADOR = {"linux": 1, "windows": 2, "macos": 10}  # minuto facturable por SO (GitHub)


def _so(etiquetas: list[str]) -> str:
    texto = " ".join(etiquetas).lower()
    return "windows" if "windows" in texto else "macos" if "macos" in texto else "linux"


def minutos_de_jobs(jobs: list[dict]) -> dict:
    """Minutos de un run a partir de sus jobs: GitHub redondea cada job al minuto y multiplica
    por sistema. Devuelve {segundos, minutos (redondeados), facturables (con multiplicador)}."""
    seg = mins = fact = 0
    for jb in jobs:
        if not jb.get("started_at") or not jb.get("completed_at"):
            continue
        a = datetime.fromisoformat(jb["started_at"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(jb["completed_at"].replace("Z", "+00:00"))
        s = max(0, int((b - a).total_seconds()))
        m = math.ceil(s / 60) if s else 0
        seg, mins = seg + s, mins + m
        fact += m * MULTIPLICADOR[_so(jb.get("labels", []))]
    return {"segundos": seg, "minutos": mins, "facturables": fact}


def gasto(repo: str, workflow: str, gh: str = "gh", limite: int = 100) -> dict:
    """Minutos que ha gastado el validador en GitHub, por run y por mes (lo lee de la API)."""
    def api(ruta):
        r = subprocess.run([gh, "api", ruta], capture_output=True, text=True, encoding="utf-8",
                           stdin=subprocess.DEVNULL)
        if r.returncode:
            raise Rechazo(f"gh api {ruta}: {(r.stderr or r.stdout).strip()}")
        return json.loads(r.stdout)

    runs = api(f"repos/{repo}/actions/workflows/{workflow}/runs?per_page={limite}")
    filas, meses = [], {}
    for run in runs.get("workflow_runs", []):
        if run.get("status") != "completed":
            continue
        jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs").get("jobs", [])
        m = minutos_de_jobs(jobs)
        mes = (run.get("run_started_at") or run["created_at"])[:7]
        filas.append({"run": run["id"], "fecha": run["created_at"], "evento": run["event"],
                      "conclusion": run.get("conclusion"), **m})
        acc = meses.setdefault(mes, {"runs": 0, "segundos": 0, "minutos": 0, "facturables": 0})
        acc["runs"] += 1
        for k in ("segundos", "minutos", "facturables"):
            acc[k] += m[k]
    return {"repo": repo, "workflow": workflow, "runs": filas, "por_mes": meses,
            "media_facturable_por_run": (round(sum(f["facturables"] for f in filas)
                                               / len(filas), 2) if filas else None)}


# --- revisión independiente ----------------------------------------------------------------
def revisar(reg, tarea_id: str, identidad: str, veredicto: str, cabeza: str,
            hallazgos: int | None = None, ruta: str | None = None, subir=True) -> dict:
    """Registra el veredicto de una revisión hecha por otra ejecución (papel revisor). El estado
    rechaza la de quien hizo la tarea."""
    datos = {"veredicto": veredicto, "cabeza": cabeza}
    if hallazgos is not None:
        datos["hallazgos"] = hallazgos
    if ruta:
        datos["ruta"] = ruta
        p = Path(ruta)
        if p.is_file():
            datos["hash"] = _sha256(p.read_bytes())
    r = reg.escribir("revision.emitida", ("tarea", tarea_id), "revisor", identidad,
                     f"{tarea_id}:revision:{identidad}:{cabeza[:12]}:{veredicto}", datos)
    out = {"nuevo": r.nuevo, "id": r.id, **datos}
    if subir and r.nuevo:
        out["subida"] = reg.subir()
    return out
