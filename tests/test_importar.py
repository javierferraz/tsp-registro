"""Tests del importador, la comparación diaria y la cartera (S-1265). Datos sintéticos; la
pasada contra el tablero, Lion y el manifest reales está en el informe de la tarjeta."""
from __future__ import annotations

import copy
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from org import Rechazo, Registro
from org import cartera as ca
from org.__main__ import main
from org.comparar import anomalias, clase_salto, comparar, informe_md, vigias_callados
from org.importar import _cifras_fichas, importar, leer_lion, leer_tablero

SECRETO = "Texto privado de Paula con su correo cliente@ejemplo.com"


def nota_mov(de, a, at, by="claude"):
    return {"id": "n", "at": at, "by": by, "kind": "transition", "text": SECRETO,
            "from": de, "to": a}


def tarjeta(cid, col, rev=1, updated="2026-11-20T10:00:00Z", notes=None, **extra):
    c = {"id": cid, "uuid": "u-" + cid, "col": col, "title": f"{SECRETO} {cid}",
         "body": SECRETO, "app": "", "labels": ["desarrollo"], "priority": "", "images": [],
         "notes": notes or [], "created": "2026-11-01T09:00:00Z", "updated": updated,
         "revision": rev}
    c.update(extra)
    return c


BOARD = {"version": 2, "revision": 10, "cards": [
    tarjeta("S-1", "hecha", 4, notes=[nota_mov("curso", "validar", "2026-11-19T10:00:00Z"),
                                      nota_mov("validar", "hecha", "2026-11-20T10:00:00Z",
                                               "javier")]),
    tarjeta("S-2", "curso", 2, notes=[{"id": "x", "at": "2026-11-20T09:00:00Z", "by": "claude",
                                       "text": SECRETO}]),
    tarjeta("S-3", "propuesta"),
]}


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.raiz = self.dir / "registro"
        self.raiz.mkdir()
        self.reg = Registro(self.raiz)
        self.board = self.dir / "board.json"
        self.poner_board(BOARD)

    def tearDown(self):
        self._tmp.cleanup()

    def poner_board(self, b):
        self.board.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")

    def importar(self, ejec=None, informes=None):
        return importar(self.reg, leer_tablero(self.board), ejec or {}, informes or {})

    def eventos(self):
        return [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((self.raiz / "eventos").rglob("*.json"))]


class TestImportador(Base):
    def test_sin_texto_libre_en_el_registro(self):
        self.importar()
        for p in (self.raiz / "eventos").rglob("*.json"):
            texto = p.read_text(encoding="utf-8")
            self.assertNotIn("Paula", texto)
            self.assertNotIn("cliente@", texto)

    def test_primera_pasada_y_repetir_no_hace_nada(self):
        r = self.importar()
        self.assertEqual(r["por_tipo"], {"tarjeta.importada": 3})
        self.assertEqual(r["rechazados"], [])
        self.assertEqual(self.importar()["eventos"], 0)
        self.assertEqual(len(self.eventos()), 3)
        s1 = self.reg.ver("tarjeta", "S-1")
        self.assertEqual(s1["estado"], "hecha")
        self.assertEqual(len(s1["datos"]["movimientos"]), 2)

    def test_cambio_de_columna_trae_solo_el_movimiento_nuevo(self):
        self.importar()
        b = copy.deepcopy(BOARD)
        b["cards"][1] = tarjeta("S-2", "validar", 3, "2026-11-21T10:00:00Z",
                                notes=[nota_mov("curso", "validar", "2026-11-21T10:00:00Z")])
        self.poner_board(b)
        r = self.importar()
        self.assertEqual(r["por_tipo"], {"tarjeta.cambiada": 1})
        ev = [e for e in self.eventos() if e["tipo"] == "tarjeta.cambiada"][0]
        self.assertEqual(ev["tipo"], "tarjeta.cambiada")
        self.assertEqual(ev["autor"]["papel"], "importador")
        self.assertEqual([m["a"] for m in ev["datos"]["movimientos"]], ["validar"])
        comp = comparar(self.reg, leer_tablero(self.board), {}, date(2026, 11, 22))
        fid = comp["fidelidad_tablero"]
        self.assertEqual((fid["faltan_en_registro"], fid["sobran_en_registro"],
                          fid["distintas"]), ([], [], []))

    def test_comparar_antes_de_importar_ve_la_diferencia(self):
        self.importar()
        b = copy.deepcopy(BOARD)
        b["cards"][2] = tarjeta("S-3", "adelante", 2, "2026-11-21T10:00:00Z")
        b["cards"].append(tarjeta("S-4", "propuesta"))
        self.poner_board(b)
        fid = comparar(self.reg, leer_tablero(self.board), {}, date(2026, 11, 22))[
            "fidelidad_tablero"]
        self.assertEqual(fid["faltan_en_registro"], ["S-4"])
        self.assertEqual(fid["distintas"][0]["id"], "S-3")
        self.assertEqual(fid["distintas"][0]["campos"]["estado"], ["propuesta", "adelante"])

    def test_anomalias(self):
        b = copy.deepcopy(BOARD)
        b["cards"].append(tarjeta("S-5", "hecha", notes=[
            nota_mov("curso", "hecha", "2026-11-18T10:00:00Z")]))
        self.poner_board(b)
        self.importar()
        b2 = copy.deepcopy(b)
        b2["cards"] = [c for c in b2["cards"] if c["id"] != "S-3"]           # desaparece
        b2["cards"][0] = tarjeta("S-1", "hecha", 2, "2026-11-21T10:00:00Z",  # r4 → r2
                                 notes=BOARD["cards"][0]["notes"])
        b2["cards"][1] = tarjeta("S-2", "info", 3, "2026-11-21T11:00:00Z")   # sin nota
        self.poner_board(b2)
        self.importar()
        clases = {(a["id"], a["clase"]) for a in anomalias(self.reg.eventos_de("tarjeta"))}
        self.assertIn(("S-5", "cerrada sin pasar por validar"), clases)
        self.assertIn(("S-3", "desaparecida del tablero"), clases)
        self.assertIn(("S-1", "revisión que baja"), clases)
        self.assertIn(("S-2", "cambio de columna sin nota"), clases)
        self.assertNotIn(("S-1", "cerrada sin pasar por validar"), clases)
        # vuelve a aparecer: deja de contar como desaparecida y no da error
        self.poner_board(b)
        self.assertEqual(self.importar()["rechazados"], [])
        self.assertEqual(self.reg.ver("tarjeta", "S-3")["estado"], "propuesta")

    def test_desde_corta_por_fecha(self):
        b = copy.deepcopy(BOARD)
        b["cards"].append(tarjeta("S-5", "hecha", notes=[
            nota_mov("curso", "hecha", "2026-11-10T10:00:00Z")]))
        self.poner_board(b)
        self.importar()
        self.assertEqual(anomalias(self.reg.eventos_de("tarjeta"), desde="2026-11-15"), [])
        self.assertEqual(len(anomalias(self.reg.eventos_de("tarjeta"))), 1)

    def test_clases_de_salto(self):
        self.assertIsNone(clase_salto("pedida", "propuesta"))
        self.assertIsNone(clase_salto("propuesta", "adelante"))
        self.assertIsNone(clase_salto("validar", "hecha"))
        self.assertIsNone(clase_salto("validar", "adelante"))
        self.assertEqual(clase_salto("propuesta", "curso"), "en curso sin pasar por Empezar")
        self.assertEqual(clase_salto("hecha", "curso"), "reabierta")
        self.assertEqual(clase_salto("adelante", "validar"), "otro salto fuera del ciclo")

    def test_papeles(self):
        with self.assertRaises(Rechazo):  # el importador no escribe el ciclo de tareas
            self.reg.escribir("tarea.propuesta", ("tarea", "T-1"), "importador", "x", "k",
                              {"titulo": "t", "repo": "r"})
        with self.assertRaises(Rechazo):  # y nadie más escribe el espejo
            self.reg.escribir("tarjeta.importada", ("tarjeta", "S-9"), "trabajador", "x", "k2",
                              leer_tablero(self.board)["S-3"])

    def test_recalcular_da_lo_mismo(self):
        self.importar()
        b = copy.deepcopy(BOARD)
        b["cards"][2] = tarjeta("S-3", "descartada", 2, "2026-11-21T10:00:00Z")
        self.poner_board(b)
        self.importar()
        self.reg.volcar_estado()
        antes = {p.name: p.read_bytes() for p in (self.raiz / "estado").glob("*.json")}
        self.reg.recalcular()
        despues = {p.name: p.read_bytes() for p in (self.raiz / "estado").glob("*.json")}
        self.assertEqual(antes, despues)


class TestLion(Base):
    def poner_tarea(self, tid, status, card=None, report=True):
        d = self.dir / "tasks" / tid
        d.mkdir(parents=True)
        (d / "task.json").write_text(json.dumps({
            "id": tid, "created": 1795384244827, "updated": 1795384934163, "status": status,
            "card": card, "repo": "C:\\xampp\\htdocs\\2610-roaring-lion", "size": "S",
            "model": "sonnet", "effort": "high", "attempts": 1, "prompt": SECRETO,
            "title": SECRETO, "goal": SECRETO, "originText": SECRETO,
            "verified": {"outcome": "pass"}}), encoding="utf-8")
        if report:
            (d / "report.json").write_text(json.dumps({
                "task": tid, "criteria": [{"id": "CA1", "status": "pass", "evidence": SECRETO},
                                          {"id": "CA2", "status": "not_run"}],
                "git": {"branch": "t1", "commits": ["a", "b"], "pushed": True},
                "for_javier_to_check": SECRETO}), encoding="utf-8")

    def test_lion(self):
        self.poner_tarea("T-2026-11-22-01", "done", "S-2")
        self.poner_tarea("T-2026-11-22-02", "running", report=False)
        (self.dir / "tasks" / "roto").mkdir()
        lion = leer_lion(self.dir / "tasks")
        self.assertEqual(sorted(lion), ["T-2026-11-22-01", "T-2026-11-22-02"])
        t = lion["T-2026-11-22-01"]
        self.assertEqual(t["criterios"], {"pass": 1, "not_run": 1})
        self.assertEqual((t["repo"], t["commits"], t["minutos"]), ("2610-roaring-lion", 2, 11))
        self.assertNotIn("Paula", json.dumps(lion, ensure_ascii=False))
        self.assertEqual(self.importar(lion)["por_tipo"],
                         {"ejecucion.importada": 2, "tarjeta.importada": 3})
        comp = comparar(self.reg, leer_tablero(self.board), lion, date(2026, 11, 22))
        self.assertEqual([x["tarjeta"] for x in comp["lion_frente_a_tablero"]], ["S-2"])


class TestVigias(unittest.TestCase):
    def test_fichas(self):
        texto = ("# Vigía Fichas — 2026-11-17\n\nCompara…\n\n- **0** funcionalidades no "
                 "mencionadas · **2** textos obsoletos · **0** promesas de más\n- 0 app(s) "
                 "revisadas · 9 sin revisar por error · 9 sin cambios · 7 fuera del chequeo\n")
        fecha, cifras = _cifras_fichas(texto)
        self.assertEqual(fecha, "2026-11-17")
        self.assertEqual(cifras["textos_obsoletos"], 2)
        self.assertEqual(cifras["sin_revisar_por_error"], 9)

    def test_callados(self):
        inf = {"a": {"datos": {"vigia": "soporte", "fecha": "2026-11-22"}},
               "b": {"datos": {"vigia": "fichas", "fecha": "2026-11-01"}}}
        v = {x["vigia"]: x for x in vigias_callados(inf, date(2026, 11, 22))}
        self.assertFalse(v["soporte"]["callado"])
        self.assertTrue(v["fichas"]["callado"])
        self.assertTrue(v["releases"]["callado"])  # nunca ha dejado informe


MANIFEST = {"tools": [
    {"tool": "clever-cat", "display_name": "Clever Cat Notepad", "version": "1.8.6",
     "channel": "stable", "notes_url": "https://github.com/x/2606-clever-cat/blob/m/C.md",
     "platforms": {"windows-x64": {"status": "stable", "version": "1.8.6",
                                   "installer_url": "https://h/releases/clever-cat/v1.8.6/a.exe"},
                   "linux-x64": {"status": "experimental", "version": "1.8.5"}}},
    {"tool": "writer-tiger", "display_name": "Writer Tiger Document Editor", "version": "1.2.4",
     "channel": "stable", "platforms": {"windows-x64": {
         "status": "stable", "version": "1.2.4",
         "installer_url": "https://h/releases/neat-bluejay/v1.2.4/a.exe"}}},
    {"tool": "quiet-panda", "display_name": "Quiet Panda", "version": "0.1.0", "channel": "dev",
     "platforms": {}},
]}
CATALOG_JS = """  const TOOLS = [
    {
      id: "clever-cat",
      name: "Clever Cat Notepad",
      type: "free",
      eligible: true,
      offer: "free",
    },
    { id: "quiet-panda", name: "Quiet Panda", type: "soon", eligible: true, offer: "none" },
    { id: "writer-tiger", name: "W", type: "free", eligible: false, offer: "none" },
  ];
"""
MATRIX = """| Herramienta | Windows | Linux | macOS | Notas |
|---|---|---|---|---|
| 🐱 Clever Cat | **N4** *(v1.5.0)* | **N3** *(v1.8.5 — deb)* | N1 | x |
| 🐢 Steady Turtle | **N0** *(en dev)* | N0 | N0 | x |
"""
CONTEXT = """## 8. Estado actual

| Herramienta | Estado | Repo |
|---|---|---|
| 🦊 Crafty Fox | 🗄️ Histórico — rebrandeada | — |
| 🐱 Clever Cat | ✅ 🐧 **Linux 1.8.4 publicada** | `2606-clever-cat/` |
| Resto de candidatas | 🔲 Por decidir | — |

## 9. Otra cosa
"""
CATALOGO = {"publicadas": {}, "desarrollo": {"clever-cat": {}}, "ideas": {"quick-mouse": {}}}


class TestCartera(unittest.TestCase):
    def setUp(self):
        self.car = ca.generar(MANIFEST)
        self.listas = {"catalog.js": ca.lista_catalog_js(CATALOG_JS),
                       "catalogo-apps.json": ca.lista_catalogo_apps(json.dumps(CATALOGO)),
                       "app-matrix.md": ca.lista_app_matrix(MATRIX),
                       "CONTEXT.md §8": ca.lista_context_8(CONTEXT)}

    def test_generar(self):
        self.assertEqual(self.car["clever-cat"]["estado"], "publicada")
        self.assertEqual((self.car["clever-cat"]["windows"], self.car["clever-cat"]["linux"]),
                         ("1.8.6", "1.8.5"))
        self.assertEqual(self.car["writer-tiger"]["estado"], "puente")
        self.assertEqual(self.car["writer-tiger"]["renombrada_a"], "neat-bluejay")
        self.assertEqual(self.car["quiet-panda"]["estado"], "en_desarrollo")

    def test_lectores(self):
        self.assertEqual(self.listas["catalog.js"]["clever-cat"]["eligible"], True)
        self.assertEqual(len(self.listas["catalog.js"]), 3)
        self.assertEqual(self.listas["app-matrix.md"]["clever-cat"],
                         {"windows": "1.5.0", "linux": "1.8.5"})
        self.assertEqual(self.listas["CONTEXT.md §8"],
                         {"clever-cat": {"windows": None, "linux": "1.8.4"}})

    def test_comparar(self):
        r = ca.comparar_listas(self.car, self.listas)["listas"]
        que = lambda n: {(h["app"], h["que"]) for h in r[n]["hallazgos"]}
        self.assertIn(("quiet-panda", "la lista la da por publicada y el manifest no"),
                      que("catalog.js"))
        self.assertIn(("writer-tiger", "nombre antiguo (ahora neat-bluejay): no es una app aparte"),
                      que("catalog.js"))
        self.assertIn(("clever-cat", "publicada en el manifest y la lista no la da por publicada"),
                      que("catalogo-apps.json"))
        self.assertIn(("clever-cat", "versión windows"), que("app-matrix.md"))
        self.assertNotIn(("clever-cat", "versión linux"), que("app-matrix.md"))
        self.assertIn(("clever-cat", "versión linux"), que("CONTEXT.md §8"))
        self.assertEqual(r["app-matrix.md"]["no_estan_en_el_manifest"], ["steady-turtle"])
        self.assertEqual(r["catalogo-apps.json"]["no_estan_en_el_manifest"], ["quick-mouse"])


class TestDiario(Base):
    """De punta a punta: un repo central falso con git, `org diario` y la subida."""

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                              text=True, encoding="utf-8")

    def setUp(self):
        super().setUp()
        c = self.central = self.dir / "central"
        for ruta, texto in {
                "tablero/board.json": json.dumps(BOARD),
                "updates/manifest.json": json.dumps(MANIFEST),
                "site/assets/catalog.js": CATALOG_JS,
                "docs/catalogo-apps.json": json.dumps(CATALOGO),
                "docs/portability/app-matrix.md": MATRIX, "CONTEXT.md": CONTEXT,
                "docs/soporte/ultimo.json": json.dumps({"fecha": "2026-11-22", "items": [
                    {"de": "cliente@ejemplo.com", "texto": SECRETO}], "aplazados": 0}),
                "docs/distribucion/tandas/2026-11-21.json": json.dumps(
                    {"fecha": "2026-11-21", "estado": "publicada", "apps": [{}]})}.items():
            (c / ruta).parent.mkdir(parents=True, exist_ok=True)
            (c / ruta).write_text(texto, encoding="utf-8")
        self.git(c.parent, "init", "-q", "-b", "main", str(c))
        for k, v in (("user.email", "o@p"), ("user.name", "o")):
            self.git(c, "config", k, v)
        self.git(c, "add", ".")
        self.git(c, "commit", "-q", "-m", "central")
        self.git(self.raiz, "init", "-q", "-b", "main")
        for k, v in (("user.email", "o@p"), ("user.name", "o")):
            self.git(self.raiz, "config", k, v)

    def org(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--raiz", str(self.raiz), *args])
        return code, out.getvalue(), err.getvalue()

    def comunes(self):
        return ["--central", str(self.central), "--ref", "HEAD", "--sin-fetch",
                "--lion", str(self.dir / "no-hay-lion")]

    def test_diario(self):
        code, out, err = self.org("diario", *self.comunes(), "--fecha", "2026-11-22")
        self.assertEqual(code, 0, err)
        md = (self.raiz / "informes" / "2026-11-22.md").read_text(encoding="utf-8")
        for titulo in ("## 1. Fidelidad", "## 2. Anomalías", "## 4. Vigías",
                       "## 5. Cartera frente a las cuatro listas"):
            self.assertIn(titulo, md)
        self.assertIn("Tablero: el registro dice lo mismo que la fuente.", md)
        datos = json.loads((self.raiz / "informes" / "2026-11-22.json").read_text("utf-8"))
        self.assertEqual(datos["importado"]["por_tipo"],
                         {"informe.importado": 2, "tarjeta.importada": 3})
        todo = md + json.dumps(datos, ensure_ascii=False) + "".join(
            p.read_text(encoding="utf-8") for p in (self.raiz / "eventos").rglob("*.json"))
        self.assertNotIn("cliente@", todo)
        self.assertNotIn("Paula", todo)
        self.assertIn("sin remoto", out)  # subir: commit local de eventos e informes
        en_git = self.git(self.raiz, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
        self.assertIn("informes/2026-11-22.md", en_git)
        self.assertEqual(sum(p.startswith("eventos/") for p in en_git), 5)
        # el día siguiente, sin cambios: ningún evento nuevo y el informe se sube igual
        code, out, _ = self.org("diario", *self.comunes(), "--fecha", "2026-11-23")
        self.assertEqual(code, 0)
        self.assertIn("0 evento(s)", out)
        self.assertIn("informes/2026-11-23.md",
                      self.git(self.raiz, "ls-tree", "-r", "--name-only", "HEAD").stdout)

    def test_comparar_no_escribe_eventos(self):
        code, out, _ = self.org("comparar", *self.comunes())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["fidelidad_tablero"]["faltan_en_registro"],
                         ["S-1", "S-2", "S-3"])
        self.assertFalse((self.raiz / "eventos").exists())

    def test_informe_md_sin_cartera(self):
        comp = comparar(self.reg, leer_tablero(self.board), {}, date(2026, 11, 22))
        self.assertIn("faltan en el registro (3)", informe_md("2026-11-22", None, comp, None))


if __name__ == "__main__":
    unittest.main()
