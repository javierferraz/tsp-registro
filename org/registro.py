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

from .esquemas import Conflicto, Rechazo, huella, validar
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
        if any(en_disco.get(rel) != firma for rel, firma in vistos.items()):
            return self._rehacer(db)
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
        for rel, firma, ev in leidos:
            self._incorporar(db, rel, firma, ev)

    def _rehacer(self, db) -> None:
        for tabla in ("eventos", "entidades", "rechazados", "meta"):
            db.execute(f"DELETE FROM {tabla}")
        self._sincronizar(db)

    @staticmethod
    def _rechazar(db, rel, firma, e: Exception) -> None:
        clase = "conflicto" if isinstance(e, Conflicto) else "esquema"
        motivo = str(e) if isinstance(e, Rechazo) else f"evento ilegible ({type(e).__name__}: {e})"
        db.execute("INSERT INTO rechazados VALUES (?, ?, ?, ?)", (rel, clase, motivo, firma))

    def _incorporar(self, db, rel: str, firma: str, ev: dict) -> None:
        try:
            tipo = validar(ev)
            if rel != ruta_relativa(ev):
                raise Rechazo(f"el fichero debería llamarse {ruta_relativa(ev)}")
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
        ev = {"id": secrets.token_hex(8), "tipo": tipo, "version_esquema": version,
              "fecha": None, "autor": {"papel": papel, "identidad": identidad},
              "entidad": {"tipo": entidad[0], "id": entidad[1]}, "correlacion": correlacion,
              "clave_idempotencia": clave, "datos": datos or {}}
        db = self._conectar()
        try:
            db.execute("BEGIN IMMEDIATE")
            self._sincronizar(db)
            fila = db.execute("SELECT id, huella, fichero FROM eventos WHERE clave=?",
                              (clave,)).fetchone()
            if fila:
                if fila[1] == huella(ev):
                    db.execute("COMMIT")
                    return Resultado(False, fila[0], fila[2])
                raise Rechazo(f"la clave «{clave}» ya la usó otro evento distinto ({fila[0]})")
            ev["fecha"] = _fmt(self._fecha(db, ev, ahora))
            tipo_def = validar(ev)
            if correlacion and not db.execute("SELECT 1 FROM eventos WHERE id=?",
                                              (correlacion,)).fetchone():
                raise Rechazo(f"correlación con un evento que no existe: {correlacion}")
            actual = self._entidad(db, entidad[0], entidad[1])
            nuevo = aplicar(actual, ev, tipo_def)
            rel = ruta_relativa(ev)
            firma = self._escribir_fichero(rel, ev)
            self._guardar(db, rel, firma, ev, nuevo)
            db.execute("COMMIT")
            return Resultado(True, ev["id"], rel)
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

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

    # --- leer ---------------------------------------------------------------------------
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
    def subir(self, reintentos: int = 2) -> str:
        """Commit de los eventos nuevos (solo esos ficheros) y push con pull --rebase si choca."""
        def git(*args, check=True):
            r = subprocess.run(["git", "-C", str(self.raiz), *args], capture_output=True,
                               text=True, encoding="utf-8")
            if check and r.returncode:
                raise Rechazo(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()}")
            return r
        lineas = git("status", "--porcelain", "--untracked-files=all", "--", "eventos"
                     ).stdout.splitlines()
        tocados = [l[3:] for l in lineas if l[:2] not in ("??", "A ")]
        if tocados:
            raise Rechazo(f"eventos ya registrados modificados o borrados: {tocados}")
        nuevos = [l[3:] for l in lineas]
        # Los conflictos (eventos correctos que chocan al juntar dos máquinas) sí se suben: todas
        # las máquinas los rechazan igual y quedan a la vista. Los mal formados, no.
        malos = {r["fichero"] for r in self.rechazados() if r["clase"] == "esquema"} & set(nuevos)
        if malos:
            raise Rechazo(f"no se suben eventos mal formados: {sorted(malos)}")
        if nuevos:
            git("add", "--", *nuevos)
            git("commit", "-m", f"org: {len(nuevos)} evento(s)", "--", *nuevos)
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
