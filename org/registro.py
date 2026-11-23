"""El registro: un fichero por evento en eventos/, índice SQLite desechable y estado calculado.

`org` es el único escritor. Ningún agente crea ni edita ficheros en eventos/ a mano: un fichero
que no pase las comprobaciones queda en `rechazados` y no cuenta para el estado.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import sello
from .esquemas import Conflicto, Rechazo, SinSello, huella, validar
from .estado import aplicar

FMT_FECHA = "%Y-%m-%dT%H:%M:%S.%fZ"
_ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS eventos (
  id TEXT PRIMARY KEY, fichero TEXT UNIQUE NOT NULL, fecha TEXT NOT NULL, tipo TEXT NOT NULL,
  papel TEXT NOT NULL, identidad TEXT NOT NULL, entidad_tipo TEXT NOT NULL,
  entidad_id TEXT NOT NULL, correlacion TEXT, clave TEXT UNIQUE NOT NULL, huella TEXT NOT NULL,
  json TEXT NOT NULL, firma TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS eventos_entidad ON eventos (entidad_tipo, entidad_id);
CREATE TABLE IF NOT EXISTS entidades (
  tipo TEXT NOT NULL, id TEXT NOT NULL, estado TEXT NOT NULL, json TEXT NOT NULL,
  PRIMARY KEY (tipo, id));
CREATE TABLE IF NOT EXISTS rechazados (
  fichero TEXT PRIMARY KEY, clase TEXT NOT NULL, motivo TEXT NOT NULL, firma TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS latidos_pendientes (
  tarea TEXT NOT NULL, fichero TEXT NOT NULL, PRIMARY KEY (tarea, fichero));
"""


@dataclass
class Resultado:
    nuevo: bool      # False = la clave ya estaba con el mismo contenido (no se ha hecho nada)
    id: str
    fichero: str


def _fmt(dt: datetime) -> str:
    return dt.strftime(FMT_FECHA)


def _parse(s: str) -> datetime:
    return datetime.strptime(s, FMT_FECHA).replace(tzinfo=timezone.utc)


def ruta_relativa(ev: dict) -> str:
    """eventos/AAAA/MM/DD/AAAAMMDDTHHMMSSZ-<id>.json, derivada de la fecha y el id."""
    f = _parse(ev["fecha"])
    return f"eventos/{f:%Y/%m/%d}/{f:%Y%m%dT%H%M%SZ}-{ev['id']}.json"


def _orden(ev) -> tuple:
    return (str(ev.get("fecha", "")), str(ev.get("id", ""))) if isinstance(ev, dict) else ("", "")


class Registro:
    def __init__(self, raiz: str | Path):
        self.raiz = Path(raiz)
        self.eventos = self.raiz / "eventos"
        self.dir_estado = self.raiz / "estado"
        self.ruta_indice = self.raiz / ".indice" / "org-v2.sqlite"  # esquema nuevo, fichero nuevo

    # --- índice -------------------------------------------------------------------------
    def _conectar(self) -> sqlite3.Connection:
        self.ruta_indice.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.ruta_indice, isolation_level=None, timeout=30)
        db.executescript(_ESQUEMA_SQL)
        return db

    def _ficheros(self) -> dict[str, str]:
        """{ruta relativa: firma (tamaño y fecha de modificación)} de los eventos en disco."""
        if not self.eventos.is_dir():
            return {}
        out = {}
        for p in self.eventos.rglob("*.json"):
            st = p.stat()
            out[p.relative_to(self.raiz).as_posix()] = f"{st.st_size}:{st.st_mtime_ns}"
        return dict(sorted(out.items()))

    def _sincronizar(self, db: sqlite3.Connection) -> None:
        """Lleva al índice los ficheros de eventos que aún no conoce (los suyos o los de un pull).
        Si uno ya indexado ha cambiado o desaparecido, o llega uno anterior a lo ya aplicado,
        rehace el índice entero: así el estado incremental es el mismo que el recalculado."""
        en_disco = self._ficheros()
        vistos = dict(db.execute(
            "SELECT fichero, firma FROM eventos UNION ALL SELECT fichero, firma FROM rechazados"))
        # Sello (S-1270): si cambia la lista de firmantes, o el commit de un evento rechazado
        # por falta de sello (p. ej. el validador ya lo ha subido firmado), se recalcula todo.
        politica = sello.huella_politica(self.raiz)
        fila = db.execute("SELECT v FROM meta WHERE k='politica'").fetchone()
        if fila and fila[0] != politica:
            return self._rehacer(db)
        sin_sello = dict(db.execute("SELECT fichero, firma FROM rechazados WHERE clase='sello'"))
        if sin_sello:
            ahora = sello.comprobar(self.raiz, sorted(sin_sello))
            for rel, firma in sin_sello.items():
                base, _, senal = firma.partition("|")
                if rel in en_disco and ahora[rel][0] != senal:
                    return self._rehacer(db)
                vistos[rel] = base
        if any(en_disco.get(rel) != firma for rel, firma in vistos.items()):
            return self._rehacer(db)
        if not fila:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('politica', ?)", (politica,))
        leidos = []
        for rel, firma in en_disco.items():
            if rel in vistos:
                continue
            try:
                leidos.append((rel, firma, json.loads(
                    (self.raiz / rel).read_text(encoding="utf-8"))))
            except (OSError, ValueError) as e:
                self._rechazar(db, rel, firma, Rechazo(f"JSON ilegible: {e}"))
        if not leidos:
            return
        leidos.sort(key=lambda x: _orden(x[2]))
        fila = db.execute("SELECT v FROM meta WHERE k='ultimo'").fetchone()
        if fila and _orden(leidos[0][2]) <= tuple(json.loads(fila[0])):
            return self._rehacer(db)  # ha llegado algo anterior a lo ya aplicado (otra máquina)
        sellos = {}
        if sello.activo(self.raiz):
            sellos = sello.comprobar(self.raiz, [rel for rel, _, ev in leidos if sello.exige(ev)])
        for rel, firma, ev in leidos:
            self._incorporar(db, rel, firma, ev, sellos.get(rel))

    def _rehacer(self, db) -> None:
        for tabla in ("eventos", "entidades", "rechazados", "meta"):
            db.execute(f"DELETE FROM {tabla}")
        self._sincronizar(db)

    @staticmethod
    def _rechazar(db, rel, firma, e: Exception) -> None:
        clase = ("conflicto" if isinstance(e, Conflicto) else
                 "sello" if isinstance(e, SinSello) else "esquema")
        motivo = str(e) if isinstance(e, Rechazo) else f"evento ilegible ({type(e).__name__}: {e})"
        db.execute("INSERT INTO rechazados VALUES (?, ?, ?, ?)", (rel, clase, motivo, firma))

    def _incorporar(self, db, rel: str, firma: str, ev: dict, sellado=None) -> None:
        try:
            tipo = validar(ev)
            if rel != ruta_relativa(ev):
                raise Rechazo(f"el fichero debería llamarse {ruta_relativa(ev)}")
            if sellado is not None:  # (señal, papel firmante, motivo): sello.comprobar
                senal, firmante, motivo = sellado
                papel = ev["autor"]["papel"]
                if motivo is None and firmante not in (papel, sello.HISTORICO):
                    motivo = f"firmado por «{firmante}», no por el papel {papel}"
                if motivo:
                    firma = f"{firma}|{senal}"
                    raise SinSello(f"el papel {papel} necesita su sello: {motivo}")
            otro = db.execute("SELECT id FROM eventos WHERE clave=?",
                              (ev["clave_idempotencia"],)).fetchone()
            if otro:
                raise Conflicto(f"clave de idempotencia repetida (ya la usó {otro[0]})")
            if db.execute("SELECT 1 FROM eventos WHERE id=?", (ev["id"],)).fetchone():
                raise Conflicto("id repetido")
            if ev["correlacion"] and not db.execute(
                    "SELECT 1 FROM eventos WHERE id=?", (ev["correlacion"],)).fetchone():
                raise Conflicto(f"correlación con un evento que no existe: {ev['correlacion']}")
            actual = self._entidad(db, ev["entidad"]["tipo"], ev["entidad"]["id"])
            nuevo = aplicar(actual, ev, tipo)
        except Exception as e:  # cualquier fichero raro queda rechazado, nunca tumba el índice
            self._rechazar(db, rel, firma, e)
            return
        self._guardar(db, rel, firma, ev, nuevo)

    def _guardar(self, db, rel, firma, ev, nuevo) -> None:
        db.execute("INSERT INTO eventos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            ev["id"], rel, ev["fecha"], ev["tipo"], ev["autor"]["papel"],
            ev["autor"]["identidad"], ev["entidad"]["tipo"], ev["entidad"]["id"],
            ev["correlacion"], ev["clave_idempotencia"], huella(ev),
            json.dumps(ev, ensure_ascii=False), firma))
        db.execute("INSERT OR REPLACE INTO entidades VALUES (?,?,?,?)", (
            ev["entidad"]["tipo"], ev["entidad"]["id"], nuevo["estado"],
            json.dumps(nuevo, ensure_ascii=False)))
        fila = db.execute("SELECT v FROM meta WHERE k='ultimo'").fetchone()
        if not fila or _orden(ev) > tuple(json.loads(fila[0])):
            db.execute("INSERT OR REPLACE INTO meta VALUES ('ultimo', ?)",
                       (json.dumps(list(_orden(ev))),))

    @staticmethod
    def _entidad(db, tipo: str, ent_id: str) -> dict | None:
        fila = db.execute("SELECT json FROM entidades WHERE tipo=? AND id=?",
                          (tipo, ent_id)).fetchone()
        return json.loads(fila[0]) if fila else None

    # --- escribir -----------------------------------------------------------------------
    def escribir(self, tipo: str, entidad: tuple[str, str], papel: str, identidad: str,
                 clave: str, datos: dict | None = None, correlacion: str | None = None,
                 version: int = 1, ahora: datetime | None = None) -> Resultado:
        db = self._conectar()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._sincronizar(db)
            r = self._escribir_en(db, tipo, entidad, papel, identidad, clave, datos,
                                  correlacion, version, ahora)
            db.execute("COMMIT")
            return r
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def escribir_lote(self, eventos: list[dict], papel: str, identidad: str,
                      ahora: datetime | None = None) -> list[Resultado | Rechazo]:
        """Varios eventos con una sola pasada por eventos/ (el importador escribe cientos).
        Cada elemento: {tipo, entidad, clave, datos} y, si hace falta, su propio `papel` y
        `version` (el puente, S-1271). Un rechazo no para el lote: se devuelve en su posición y
        los demás siguen."""
        db = self._conectar()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._sincronizar(db)
            out: list[Resultado | Rechazo] = []
            for e in eventos:
                try:
                    out.append(self._escribir_en(db, e["tipo"], e["entidad"],
                                                 e.get("papel", papel), identidad, e["clave"],
                                                 e.get("datos"), None, e.get("version", 1),
                                                 ahora))
                except Rechazo as ex:
                    out.append(ex)
            db.execute("COMMIT")
            return out
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _escribir_en(self, db, tipo, entidad, papel, identidad, clave, datos, correlacion,
                     version, ahora) -> Resultado:
        ev = {"id": secrets.token_hex(8), "tipo": tipo, "version_esquema": version,
              "fecha": None, "autor": {"papel": papel, "identidad": identidad},
              "entidad": {"tipo": entidad[0], "id": entidad[1]}, "correlacion": correlacion,
              "clave_idempotencia": clave, "datos": datos or {}}
        fila = db.execute("SELECT id, huella, fichero FROM eventos WHERE clave=?",
                          (clave,)).fetchone()
        if fila:
            if fila[1] == huella(ev):
                return Resultado(False, fila[0], fila[2])
            raise Rechazo(f"la clave «{clave}» ya la usó otro evento distinto ({fila[0]})")
        ev["fecha"] = _fmt(self._fecha(db, ev, ahora))
        tipo_def = validar(ev)
        if sello.exige(ev) and sello.activo(self.raiz):
            tiene = self._papel_sello()
            if tiene != papel:
                raise SinSello(
                    f"el papel {papel} solo escribe con su sello ({sello.VARIABLE_CLAVE} = clave "
                    f"de ese papel en {sello.RUTA_FIRMANTES}); "
                    + (f"la clave local es de «{tiene}»" if tiene else "no hay clave local"))
        if correlacion and not db.execute("SELECT 1 FROM eventos WHERE id=?",
                                          (correlacion,)).fetchone():
            raise Rechazo(f"correlación con un evento que no existe: {correlacion}")
        actual = self._entidad(db, entidad[0], entidad[1])
        nuevo = aplicar(actual, ev, tipo_def)
        rel = ruta_relativa(ev)
        firma = self._escribir_fichero(rel, ev)
        self._guardar(db, rel, firma, ev, nuevo)
        return Resultado(True, ev["id"], rel)

    def _papel_sello(self) -> str | None:
        """Papel de la clave de ORG_SELLO según politica/firmantes (una vez por proceso)."""
        clave = sello.clave_local()
        if not hasattr(self, "_sello_cache") or self._sello_cache[0] != clave:
            self._sello_cache = (clave, sello.papel_de_clave(self.raiz, clave))
        return self._sello_cache[1]

    def _fecha(self, db, ev, ahora) -> datetime:
        """Ahora, pero nunca antes que el último evento de la entidad ni que el correlacionado:
        así el orden por fecha respeta la causa aunque los relojes de dos máquinas difieran."""
        t = ahora or datetime.now(timezone.utc)
        previas = [r[0] for r in db.execute(
            "SELECT MAX(fecha) FROM eventos WHERE entidad_tipo=? AND entidad_id=?",
            (ev["entidad"]["tipo"], ev["entidad"]["id"])) if r[0]]
        if ev["correlacion"]:
            previas += [r[0] for r in db.execute("SELECT fecha FROM eventos WHERE id=?",
                                                 (ev["correlacion"],))]
        for p in previas:
            t = max(t, _parse(p) + timedelta(microseconds=1))
        return t

    def _escribir_fichero(self, rel: str, ev: dict) -> str:
        ruta = self.raiz / rel
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                       newline="\n")
        if ruta.exists():  # nunca se pisa un evento
            tmp.unlink()
            raise Rechazo(f"ya existe {rel}")
        os.replace(tmp, ruta)
        st = ruta.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"

    def _tx(self, fn):
        """fn(db) dentro de una transacción con el índice al día: lo que lee y lo que escribe
        (con _escribir_en) va junto. Si falla, el índice no cambia."""
        db = self._conectar()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._sincronizar(db)
            r = fn(db)
            db.execute("COMMIT")
            return r
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    # --- leer ---------------------------------------------------------------------------
    def contar_eventos(self, desde: str) -> dict[str, int]:
        """{tipo: n} de los eventos aceptados con fecha >= desde (AAAA-MM-DD o fecha completa)."""
        return self._leer(lambda db: dict(db.execute(
            "SELECT tipo, COUNT(*) FROM eventos WHERE fecha >= ? GROUP BY tipo", (desde,))))

    def _leer(self, fn):
        db = self._conectar()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._sincronizar(db)
            r = fn(db)
            db.execute("COMMIT")
            return r
        finally:
            db.close()

    def ver(self, tipo: str, ent_id: str) -> dict | None:
        return self._leer(lambda db: self._entidad(db, tipo, ent_id))

    def rechazados(self) -> list[dict]:
        return self._leer(self._lista_rechazados)

    def entidades(self, tipo: str) -> dict[str, dict]:
        """{id: estado calculado} de todas las entidades de un tipo."""
        return self._leer(lambda db: {i: json.loads(js) for i, js in db.execute(
            "SELECT id, json FROM entidades WHERE tipo=? ORDER BY id", (tipo,))})

    def eventos_de(self, entidad_tipo: str) -> list[dict]:
        """Eventos aceptados sobre un tipo de entidad, en orden (fecha, id)."""
        return self._leer(lambda db: [json.loads(js) for (js,) in db.execute(
            "SELECT json FROM eventos WHERE entidad_tipo=? ORDER BY fecha, id",
            (entidad_tipo,))])

    @staticmethod
    def _lista_rechazados(db) -> list[dict]:
        return [{"fichero": f, "clase": c, "motivo": m} for f, c, m in db.execute(
            "SELECT fichero, clase, motivo FROM rechazados ORDER BY fichero")]

    def volcar_estado(self) -> dict:
        """Escribe estado/ desde el índice: un JSON por tipo de entidad, rechazados y resumen."""
        def leer(db):
            ents = {}
            for tipo, ent_id, js in db.execute(
                    "SELECT tipo, id, json FROM entidades ORDER BY tipo, id"):
                ents.setdefault(tipo, {})[ent_id] = json.loads(js)
            n = db.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
            return ents, n, self._lista_rechazados(db)
        ents, n, rech = self._leer(leer)
        resumen = {"eventos": n, "rechazados": len(rech),
                   "por_estado": {t: _contar(e) for t, e in ents.items()}}
        self.dir_estado.mkdir(parents=True, exist_ok=True)
        salida = {f"{t}s.json": e for t, e in ents.items()}
        salida.update({"rechazados.json": rech, "resumen.json": resumen})
        for viejo in self.dir_estado.glob("*.json"):
            if viejo.name not in salida:
                viejo.unlink()
        for nombre, contenido in salida.items():
            tmp = self.dir_estado / (nombre + ".tmp")
            tmp.write_text(json.dumps(contenido, ensure_ascii=False, indent=1, sort_keys=True)
                           + "\n", encoding="utf-8", newline="\n")
            os.replace(tmp, self.dir_estado / nombre)
        return resumen

    def recalcular(self) -> dict:
        """Borra índice y estado y los vuelve a calcular desde los ficheros de eventos."""
        if self.ruta_indice.parent.exists():
            shutil.rmtree(self.ruta_indice.parent)
        if self.dir_estado.exists():
            shutil.rmtree(self.dir_estado)
        return self.volcar_estado()

    # --- git ----------------------------------------------------------------------------
    def _git(self, *args, check=True, entrada=None):
        r = subprocess.run(["git", "-C", str(self.raiz), *args], capture_output=True,
                           text=True, encoding="utf-8", input=entrada)
        if check and r.returncode:
            raise Rechazo(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
        return r

    def con_remoto(self) -> bool:
        r = self._git("remote", check=False)
        return r.returncode == 0 and bool(r.stdout.strip())

    def traer(self) -> bool:
        """pull --rebase si hay remoto, sin fallar: es solo para ver antes lo de otras máquinas.
        False si no se pudo (sin remoto, sin red o con cambios que lo impiden)."""
        return self.con_remoto() and self._git("pull", "-q", "--rebase", check=False).returncode == 0

    def orden_historia(self, ficheros: list[str]) -> dict[str, int]:
        """{fichero: posición del commit que lo añadió en la historia de HEAD}. Tras subir, HEAD
        es la historia compartida: todas las máquinas ven el mismo orden. Lo que no está en
        ningún commit no sale (va detrás de todo). Sin git, vacío."""
        if not ficheros:
            return {}
        r = self._git("log", "--reverse", "--diff-filter=A", "--format=@%H", "--name-only",
                      "HEAD", "--", *ficheros, check=False)
        if r.returncode:
            return {}
        out, n = {}, -1
        for linea in r.stdout.splitlines():
            if linea.startswith("@"):
                n += 1
            elif linea.strip() and linea not in out:
                out[linea.strip()] = n
        return out

    def subir(self, reintentos: int = 2) -> str:
        """Commit de los eventos nuevos (solo esos ficheros) y de los informes diarios
        (`informes/`, generados por `org diario`), y push con pull --rebase si choca."""
        git = self._git
        lineas = git("status", "--porcelain", "--untracked-files=all", "--", "eventos",
                     "informes").stdout.splitlines()
        tocados = [l[3:] for l in lineas
                   if l[3:].startswith("eventos/") and l[:2] not in ("??", "A ")]
        if tocados:
            raise Rechazo(f"eventos ya registrados modificados o borrados: {tocados}")
        nuevos = [l[3:] for l in lineas]
        # Los conflictos (eventos correctos que chocan al juntar dos máquinas) sí se suben: todas
        # las máquinas los rechazan igual y quedan a la vista. Los mal formados, no.
        malos = {r["fichero"] for r in self.rechazados() if r["clase"] == "esquema"} & set(nuevos)
        if malos:
            raise Rechazo(f"no se suben eventos mal formados: {sorted(malos)}")
        # Sello (S-1270): un evento de papel sellado solo sube firmado con la clave de su papel.
        firma = []
        if sello.activo(self.raiz):
            sellados = set()
            for n in nuevos:
                if n.startswith("eventos/"):
                    try:
                        ev = json.loads((self.raiz / n).read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if sello.exige(ev):
                        sellados.add(ev["autor"]["papel"])
            tiene = self._papel_sello()
            if sellados - {tiene}:
                raise SinSello(f"no se suben eventos de {sorted(sellados - {tiene})} sin su "
                               f"sello ({sello.VARIABLE_CLAVE})")
            if tiene:
                firma = sello.args_firma(sello.clave_local())
        if firma:
            git = lambda *a, **k: self._git(*firma, *a, **k)  # noqa: E731
        if nuevos:
            # Las rutas van por la entrada estándar: con miles de eventos (la primera
            # importación) la línea de órdenes pasa del límite de Windows (WinError 206).
            lista = "\n".join(nuevos) + "\n"
            git("add", "--pathspec-from-file=-", entrada=lista)
            n_ev = sum(1 for n in nuevos if n.startswith("eventos/"))
            extra = f" y {len(nuevos) - n_ev} informe(s)" if len(nuevos) > n_ev else ""
            git("commit", "-m", f"org: {n_ev} evento(s){extra}", "--pathspec-from-file=-",
                entrada=lista)
        if not git("remote", check=False).stdout.strip():
            return f"{len(nuevos)} evento(s) en commit local; sin remoto"
        for intento in range(reintentos + 1):
            if git("push", check=False).returncode == 0:
                return f"{len(nuevos)} evento(s) subidos"
            git("pull", "--rebase")
        raise Rechazo(f"push fallido tras {reintentos} reintentos")


def _contar(ents: dict) -> dict:
    c: dict[str, int] = {}
    for e in ents.values():
        c[e["estado"]] = c.get(e["estado"], 0) + 1
    return dict(sorted(c.items()))
