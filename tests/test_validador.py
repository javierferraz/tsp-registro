"""Tests del validador central y del sello de papel (PAA2 P4-2, S-1270).

Los criterios de la sesión:
- una entrega con evidencia falsa es rechazada (Evidencia*: fichero que no existe, hash
  distinto, pruebas que fallan al repetirlas, pruebas rebajadas en la rama, commit inventado,
  ficheros que no casan, texto libre);
- un trabajador no puede crear «verificada» ni «aprobado por Javier» (Sello*: declarando el
  papel, con una clave ajena, a mano, con un commit sin firmar, copiando o editando un evento
  sellado);
- coste en minutos medido (Gasto: minutos por job, redondeo y multiplicador por sistema).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from org import Conflicto, Rechazo, Registro
from org import sello
from org import validador as val
from org.esquemas import SinSello

T = ("tarea", "T-1")
VALIDADOR = "validador-prueba"
CHECK = ("import sys; sys.exit(0 if open('ok.txt', encoding='utf-8').read().strip() == 'si' "
         "else 1)")


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=prueba",
                           "-c", "user.email=prueba@prueba", *args], check=True,
                          capture_output=True, text=True, env=env)


class App:
    """Un repo de app de juguete: main con sus pruebas y una rama «trabajo» con la entrega."""

    def __init__(self, raiz: Path):
        self.dir = raiz
        raiz.mkdir()
        git(raiz, "init", "-q", "-b", "main")
        self.poner({".claude/checks.json": json.dumps({"checks": [
            {"name": "pruebas", "cmd": ["python", "-c", CHECK], "cwd": ".", "timeout_s": 60}]}),
            "ok.txt": "si\n", "app.py": "print('hola')\n"})
        self.commit("inicio")
        git(raiz, "checkout", "-q", "-b", "trabajo")

    def poner(self, ficheros: dict):
        for ruta, texto in ficheros.items():
            p = self.dir / ruta
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(texto, encoding="utf-8", newline="\n")
            git(self.dir, "add", ruta)

    def commit(self, msg) -> str:
        git(self.dir, "commit", "-q", "-m", msg)
        return git(self.dir, "rev-parse", "HEAD").stdout.strip()


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg = Registro(self.tmp / "registro")
        self.app = App(self.tmp / "app")

    def tearDown(self):
        self._tmp.cleanup()

    def tarea(self, **extra):
        datos = {"titulo": "cambiar el saludo", "repo": "app", "tipo_trabajo": "codigo",
                 "tamano": "S", "criterios": ["saluda distinto", "las pruebas pasan"], **extra}
        for tipo, papel, d, v in [("tarea.propuesta", "direccion", datos, 2),
                                  ("tarea.aprobada", "javier", {}, 1),
                                  ("tarea.lista", "planificador", {}, 1),
                                  ("tarea.asignada", "planificador", {"ejecutor": "w1"}, 1)]:
            self.reg.escribir(tipo, T, papel, f"p-{papel}", f"T-1:{tipo}:{self.vuelta()}", d,
                              version=v)

    def vuelta(self):
        t = self.reg.ver(*T)
        return f"{(t or {}).get('devoluciones', 0)}"

    def trabajo(self, ficheros=None, msg="saludo nuevo") -> str:
        self.app.poner(ficheros or {"app.py": "print('buenas')\n",
                                    "docs/prueba.txt": "salida: buenas\n"})
        return self.app.commit(msg)

    def entregar(self, commits, ficheros=("app.py", "docs/prueba.txt"), criterios=None,
                 quien="w1", rama="trabajo"):
        informe = {"resumen": "hecho", "commits": list(commits), "ficheros": list(ficheros),
                   "criterios": criterios or [
                       {"n": 1, "estado": "ok", "evidencia": "docs/prueba.txt"},
                       {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}],
                   "decisiones": [], "descubierto": [], "coste": {"minutos": 3}}
        if rama:
            informe["rama"] = rama
        self.reg.escribir("tarea.entregada", T, "trabajador", quien,
                          f"T-1:entregada:{self.vuelta()}", informe, version=2)

    def validar(self, **kw):
        kw.setdefault("subir", False)
        return val.validar(self.reg, "T-1", VALIDADOR, fuente=str(self.app.dir),
                           evidencias=self.tmp / "evidencias", **kw)


class EntregaHonesta(Base):
    def test_verificada_con_pruebas_repetidas_y_evidencia_en_el_registro(self):
        self.tarea()
        c = self.trabajo()
        self.entregar([c])
        r = self.validar()
        self.assertEqual(r["veredicto"], "verificada", r["fallos"])
        t = self.reg.ver(*T)
        self.assertEqual(t["estado"], "verificada")
        self.assertEqual(t["veredicto"]["cabeza"], c)
        self.assertEqual(t["veredicto"]["politica"], "validador/codigo@1")
        self.assertIn("segundos", t["veredicto"]["coste"])
        ev = t["evidencias"][-1]
        self.assertEqual((ev["tipo"], ev["autor"]["papel"]), ("salida_de_pruebas", "validador"))
        log = Path(r["log"])
        self.assertEqual(ev["hash"], "sha256:" + hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertIn("### pruebas · salida 0", log.read_text(encoding="utf-8"))

    def test_ensayo_no_escribe(self):
        self.tarea()
        self.entregar([self.trabajo()])
        r = self.validar(ensayo=True)
        self.assertEqual(r["veredicto"], "verificada")
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")

    def test_en_dos_pasos_juzga_sin_escribir_y_registra_sin_ejecutar(self):
        """Así corre en GitHub: un job sin secretos juzga y otro, con el sello, registra."""
        self.tarea()
        self.entregar([self.trabajo()])
        d = json.loads(json.dumps(val.decidir(self.reg, "T-1", VALIDADOR,
                                              fuente=str(self.app.dir),
                                              evidencias=self.tmp / "evidencias")))
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        log = Path(d["log"])
        original = log.read_bytes()
        log.write_bytes(original + b"retocado\n")
        with self.assertRaisesRegex(Rechazo, "no es la que se juzgó"):
            val.registrar(self.reg, d, VALIDADOR, subir=False)
        with self.assertRaisesRegex(Rechazo, "quien implementa no verifica"):
            val.registrar(self.reg, d, "w1", subir=False)
        log.write_bytes(original)
        val.registrar(self.reg, d, VALIDADOR, subir=False)
        self.assertEqual(self.reg.ver(*T)["estado"], "verificada")

    def test_registrar_una_decision_vieja_no_vale(self):
        self.tarea()
        c = self.trabajo()
        self.entregar([c], ficheros=("app.py",))
        vieja = val.decidir(self.reg, "T-1", VALIDADOR, fuente=str(self.app.dir),
                            evidencias=self.tmp / "evidencias")
        self.assertEqual(vieja["veredicto"], "devuelta")
        val.registrar(self.reg, vieja, VALIDADOR, subir=False)
        self.reg.escribir("tarea.lista", T, "planificador", "plan", "relista", {})
        self.reg.escribir("tarea.asignada", T, "planificador", "plan", "reasig", {"ejecutor": "w1"})
        self.entregar([c])
        with self.assertRaisesRegex(Rechazo, "ha cambiado desde que se juzgó"):
            val.registrar(self.reg, vieja, VALIDADOR, subir=False)

    def test_no_ejecutado_queda_a_la_vista(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "no_ejecutado", "motivo": "sin pantalla"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        r = self.validar()
        self.assertEqual(r["veredicto"], "verificada")
        self.assertEqual(self.reg.ver(*T)["veredicto"]["no_verificado"],
                         ["criterio 1: sin pantalla"])

    def test_sin_rama_la_cabeza_es_el_commit_que_contiene_a_los_demas(self):
        self.tarea()
        a = self.trabajo({"app.py": "print('a')\n"}, "a")
        b = self.trabajo({"app.py": "print('buenas')\n", "docs/prueba.txt": "x\n"}, "b")
        self.entregar([b, a], rama=None)
        r = self.validar()
        self.assertEqual((r["veredicto"], r["cabeza"]), ("verificada", b), r["fallos"])


class EvidenciaFalsa(Base):
    """Una entrega con evidencia falsa es rechazada: el validador no se fía del informe."""

    def devuelta(self, contiene: str, **entrega):
        r = self.validar()
        self.assertEqual(r["veredicto"], "devuelta", r)
        self.assertTrue(any(contiene in f for f in r["fallos"]), r["fallos"])
        t = self.reg.ver(*T)
        self.assertEqual((t["estado"], t["devoluciones"]), ("devuelta", 1))
        self.assertIn(contiene, t["veredicto"]["motivo"])
        return r

    def test_cita_un_fichero_que_no_existe(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "ok", "evidencia": "docs/captura-final.png"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        self.devuelta("docs/captura-final.png, que no existe")

    def test_hash_que_no_casa(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "ok", "evidencia": f"docs/prueba.txt#sha256={sha('otra cosa')}"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        self.devuelta("hash distinto")

    def test_hash_que_casa_pasa(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "ok",
             "evidencia": f"docs/prueba.txt#sha256={sha('salida: buenas' + chr(10))}"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        self.assertEqual(self.validar()["veredicto"], "verificada")

    def test_dice_que_las_pruebas_pasan_y_fallan_al_repetirlas(self):
        self.tarea()
        c = self.trabajo({"app.py": "print('buenas')\n", "docs/prueba.txt": "todo verde\n",
                          "ok.txt": "no\n"})
        self.entregar([c], ficheros=("app.py", "docs/prueba.txt", "ok.txt"))
        r = self.devuelta("falla al repetirla")
        self.assertTrue(any(c["resultado"] == "falla" and "pruebas" in c["nombre"]
                            for c in r["comprobaciones"]))

    def test_rebajar_las_pruebas_en_su_rama_no_vale(self):
        """Las pruebas salen de la rama principal, no de la entregada."""
        self.tarea()
        flojo = json.dumps({"checks": [{"name": "pruebas", "cmd": ["python", "-c", "pass"]}]})
        c = self.trabajo({"app.py": "x\n", "docs/prueba.txt": "x\n", "ok.txt": "no\n",
                          ".claude/checks.json": flojo})
        self.entregar([c], ficheros=("app.py", "docs/prueba.txt", "ok.txt",
                                     ".claude/checks.json"))
        self.devuelta("falla al repetirla")

    def test_commit_inventado(self):
        self.tarea()
        c = self.trabajo()
        self.entregar([c, "deadbeefdeadbeef"])
        self.devuelta("deadbeefdeadbeef no existe")

    def test_commit_que_ya_estaba_en_main(self):
        self.tarea()
        inicio = git(self.app.dir, "rev-parse", "main").stdout.strip()
        self.entregar([self.trabajo(), inicio])
        self.devuelta("ya estaba en la rama principal")

    def test_ficheros_que_no_casan_con_el_diff(self):
        self.tarea()
        self.entregar([self.trabajo()], ficheros=("app.py", "README.md"))
        r = self.devuelta("cambiados y no declarados: docs/prueba.txt")
        self.assertTrue(any("README.md" in f for f in r["fallos"]))

    def test_evidencia_en_texto_libre_no_cuenta(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "ok", "evidencia": "lo he probado y va bien"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        self.devuelta("no puede leer")

    def test_cita_una_prueba_que_no_existe(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 1, "estado": "ok", "evidencia": "docs/prueba.txt"},
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas de integración"}])
        self.devuelta("que no está en checks.json")

    def test_se_deja_criterios_sin_contar(self):
        self.tarea()
        self.entregar([self.trabajo()], criterios=[
            {"n": 2, "estado": "ok", "evidencia": "check:pruebas"}])
        self.devuelta("criterios [1]")

    def test_rama_sin_subir(self):
        self.tarea()
        self.entregar([self.trabajo()], rama="no-existe")
        self.devuelta("no está en el repo")

    def test_tres_devoluciones_bloquean(self):
        self.tarea()
        c = self.trabajo()
        for vuelta in range(val.MAX_DEVOLUCIONES + 1):
            self.entregar([c], ficheros=("app.py",))
            r = self.validar()
            if vuelta < val.MAX_DEVOLUCIONES:
                self.assertEqual(r["veredicto"], "devuelta")
                self.reg.escribir("tarea.lista", T, "planificador", "plan",
                                  f"T-1:relista:{vuelta}", {})
                self.reg.escribir("tarea.asignada", T, "planificador", "plan",
                                  f"T-1:reasignada:{vuelta}", {"ejecutor": "w1"})
        self.assertEqual(r["veredicto"], "bloqueada")
        self.assertEqual(self.reg.ver(*T)["estado"], "bloqueada")


class NoVerificable(Base):
    def test_repo_sin_pruebas_bloquea(self):
        git(self.app.dir, "checkout", "-q", "main")
        git(self.app.dir, "rm", "-q", ".claude/checks.json")
        self.app.commit("sin pruebas")
        git(self.app.dir, "checkout", "-q", "trabajo")
        git(self.app.dir, "rebase", "-q", "main")
        self.tarea()
        self.entregar([self.trabajo()])
        r = self.validar()
        self.assertEqual(r["veredicto"], "bloqueada")
        self.assertIn("checks.json", self.reg.ver(*T)["historial"][-1]["motivo"])

    def test_tipo_que_aun_no_sabe_validar(self):
        self.tarea(tipo_trabajo="contenido")
        self.entregar([self.trabajo()])
        self.assertEqual(self.validar()["veredicto"], "bloqueada")


class QuienImplementaNoVerifica(Base):
    def test_el_ejecutor_no_puede_validar(self):
        self.tarea()
        self.entregar([self.trabajo()])
        with self.assertRaisesRegex(Rechazo, "quien implementa no verifica"):
            val.validar(self.reg, "T-1", "w1", fuente=str(self.app.dir), subir=False)
        with self.assertRaisesRegex(Conflicto, "quien implementa no verifica"):
            self.reg.escribir("tarea.verificada", T, "validador", "w1", "a-mano", {})
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")

    def test_interfaz_exige_revision_ajena_sobre_la_cabeza_y_medida_visual(self):
        self.tarea(tipo_trabajo="interfaz")
        c = self.trabajo()
        self.entregar([c])
        r = self.validar(ensayo=True)
        self.assertEqual(r["veredicto"], "devuelta")
        self.assertTrue(any("revisión independiente" in f for f in r["fallos"]))
        self.assertTrue(any("medida visual" in f for f in r["fallos"]))
        with self.assertRaisesRegex(Conflicto, "quien implementa no verifica"):
            val.revisar(self.reg, "T-1", "w1", "ok", c, subir=False)
        val.revisar(self.reg, "T-1", "revisor-frio", "ok", "0123456789ab", subir=False)
        r = self.validar(ensayo=True)
        self.assertTrue(any("no «ok» sobre la cabeza" in f for f in r["fallos"]), r["fallos"])
        val.revisar(self.reg, "T-1", "revisor-frio", "ok", c[:12], subir=False)
        self.reg.escribir("evidencia.verificada", T, "laboratorio", "lab-linux", "lab-1",
                          {"tipo": "medida_visual", "ruta": "actions:1/medida.json",
                           "hash": "sha256:00"})
        r = self.validar()
        self.assertEqual(r["veredicto"], "verificada", r["fallos"])

    def test_m_y_l_tambien_exigen_revision(self):
        self.tarea(tamano="M")
        self.entregar([self.trabajo()])
        self.assertEqual(self.validar(ensayo=True)["veredicto"], "devuelta")


class Web(Base):
    def test_url_en_vivo_responde_y_contiene_lo_esperado(self):
        self.tarea(tipo_trabajo="web", urls=[
            {"url": "https://ejemplo.test/tools/frog/", "contiene": "Silent Frog 1.6"}])
        c = self.trabajo()
        self.entregar([c])
        val.revisar(self.reg, "T-1", "revisor-frio", "ok", c, subir=False)
        paginas = {"https://ejemplo.test/tools/frog/": (200, "<h1>Silent Frog 1.5</h1>")}
        r = self.validar(ensayo=True, abrir=lambda u: paginas.get(u, (404, "")))
        self.assertEqual(r["veredicto"], "devuelta")
        self.assertTrue(any("no contiene «Silent Frog 1.6»" in f for f in r["fallos"]))
        paginas["https://ejemplo.test/tools/frog/"] = (200, "<h1>Silent Frog 1.6</h1>")
        r = self.validar(abrir=lambda u: paginas.get(u, (404, "")))
        self.assertEqual(r["veredicto"], "verificada", r["fallos"])

    def test_publicar_sin_revision_ajena_no_pasa(self):
        self.tarea(tipo_trabajo="web")
        self.entregar([self.trabajo()])
        r = self.validar(ensayo=True)
        self.assertTrue(any("revisión independiente" in f for f in r["fallos"]))


class Entorno(unittest.TestCase):
    def test_las_pruebas_no_ven_secretos(self):
        with mock.patch.dict(os.environ, {"ORG_SELLO": "x", "GITHUB_TOKEN": "y", "HOME_X": "z"}):
            env = val._entorno_limpio()
        self.assertNotIn("ORG_SELLO", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertEqual(env["HOME_X"], "z")


class Gasto(unittest.TestCase):
    def test_redondeo_por_job_y_multiplicador(self):
        jobs = [{"started_at": "2026-11-23T10:00:00Z", "completed_at": "2026-11-23T10:01:30Z",
                 "labels": ["ubuntu-latest"]},
                {"started_at": "2026-11-23T10:00:00Z", "completed_at": "2026-11-23T10:00:20Z",
                 "labels": ["windows-latest"]},
                {"started_at": None, "completed_at": None, "labels": []}]
        self.assertEqual(val.minutos_de_jobs(jobs),
                         {"segundos": 110, "minutos": 3, "facturables": 4})


# --- Sello ---------------------------------------------------------------------------------
def clave(dir_: Path, nombre: str) -> tuple[Path, str]:
    p = dir_ / nombre
    subprocess.run([sello.ssh_keygen(), "-q", "-t", "ed25519", "-N", "", "-C", nombre, "-f", str(p)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    return p, " ".join(p.with_suffix(".pub").read_text().split()[:2])


@unittest.skipUnless(Path(sello.ssh_keygen()).is_file(), "sin ssh-keygen")
class Sello(unittest.TestCase):
    """Un trabajador no puede crear «verificada» ni «aprobado por Javier»."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.claves = self.tmp / "claves"
        self.claves.mkdir()
        self.k_val, pub_val = clave(self.claves, "validador")
        self.k_jav, pub_jav = clave(self.claves, "javier")
        self.k_otra, _ = clave(self.claves, "intrusa")
        self.raiz = self.tmp / "registro"
        self.raiz.mkdir()
        remoto = self.tmp / "remoto.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
        g = lambda *a: git(self.raiz, *a)  # noqa: E731
        g("init", "-q", "-b", "main")
        g("config", "user.email", "org@prueba")
        g("config", "user.name", "org")
        (self.raiz / ".gitignore").write_text(".indice/\nestado/\n", encoding="utf-8")
        (self.raiz / "politica").mkdir()
        (self.raiz / "politica/firmantes").write_text(
            f'# papel namespaces clave\nvalidador namespaces="git" {pub_val}\n'
            f'javier namespaces="git" {pub_jav}\n', encoding="utf-8")
        g("add", ".gitignore", "politica/firmantes")
        g("commit", "-q", "-m", "inicio")
        g("remote", "add", "origin", str(remoto))
        g("push", "-q", "-u", "origin", "main")
        self.remoto = remoto
        self.reg = Registro(self.raiz)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop("ORG_SELLO", None)
        self.hasta_entregada()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def con(self, k):
        os.environ["ORG_SELLO"] = str(k) if k else ""
        if not k:
            os.environ.pop("ORG_SELLO")

    def hasta_entregada(self):
        self.con(self.k_jav)
        self.reg.escribir("tarea.propuesta", T, "direccion", "dir", "p",
                          {"titulo": "x", "repo": "app"})
        self.reg.escribir("tarea.aprobada", T, "javier", "telegram", "a", {})
        self.reg.subir()
        self.con(None)
        self.reg.escribir("tarea.lista", T, "planificador", "plan", "l", {})
        self.reg.escribir("tarea.asignada", T, "planificador", "plan", "as", {"ejecutor": "w1"})
        self.reg.escribir("tarea.entregada", T, "trabajador", "w1", "e", {"resumen": "hecho"})
        self.reg.subir()

    def a_mano(self, tipo, papel, ident="w1", segundos=5, datos=None):
        fecha = datetime.now(timezone.utc) + timedelta(seconds=segundos)
        ev = {"id": os.urandom(8).hex(), "tipo": tipo, "version_esquema": 1,
              "fecha": fecha.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
              "autor": {"papel": papel, "identidad": ident},
              "entidad": {"tipo": "tarea", "id": "T-1"}, "correlacion": None,
              "clave_idempotencia": f"a-mano-{os.urandom(4).hex()}", "datos": datos or {}}
        rel = f"eventos/{fecha:%Y/%m/%d}/{fecha:%Y%m%dT%H%M%SZ}-{ev['id']}.json"
        (self.raiz / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.raiz / rel).write_text(json.dumps(ev), encoding="utf-8")
        return rel

    def test_con_sello_la_aprobacion_de_javier_cuenta(self):
        t = self.reg.ver(*T)
        self.assertEqual(t["estado"], "entregada")
        self.assertEqual(self.reg.rechazados(), [])

    def test_declarar_el_papel_sin_la_clave_no_basta(self):
        with self.assertRaisesRegex(SinSello, "solo escribe con su sello"):
            self.reg.escribir("tarea.verificada", T, "validador", "w2", "v", {})
        self.con(self.k_otra)  # una clave que no está en politica/firmantes
        with self.assertRaisesRegex(SinSello, "no hay clave local"):
            self.reg.escribir("tarea.verificada", T, "validador", "w2", "v", {})
        self.con(self.k_jav)  # la de otro papel
        with self.assertRaisesRegex(SinSello, "la clave local es de «javier»"):
            self.reg.escribir("tarea.verificada", T, "validador", "w2", "v", {})
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")

    def test_aprobado_por_javier_sin_su_clave(self):
        self.reg.escribir("tarea.propuesta", ("tarea", "T-2"), "direccion", "dir", "p2",
                          {"titulo": "y", "repo": "app"})
        with self.assertRaisesRegex(SinSello, "papel javier solo escribe con su sello"):
            self.reg.escribir("tarea.aprobada", ("tarea", "T-2"), "javier", "w1", "a2", {})
        self.assertEqual(self.reg.ver("tarea", "T-2")["estado"], "propuesta")

    def test_escrito_a_mano_y_commit_sin_firmar_no_cuenta(self):
        rel = self.a_mano("tarea.verificada", "validador", "w2")
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        git(self.raiz, "add", rel)
        git(self.raiz, "commit", "-q", "-m", "me verifico")
        for paso in (lambda: None, self.reg.recalcular):
            paso()
            self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
            r = self.reg.rechazados()
            self.assertEqual([x["clase"] for x in r], ["sello"])
            self.assertIn("sin firma válida", r[0]["motivo"])

    def test_firmado_con_una_clave_ajena_no_cuenta(self):
        rel = self.a_mano("tarea.verificada", "validador", "w2")
        git(self.raiz, "add", rel)
        git(self.raiz, *sello.args_firma(str(self.k_otra)), "commit", "-q", "-m",
            "firmado con otra")
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        rel = self.a_mano("tarea.verificada", "validador", "w2", segundos=6)
        git(self.raiz, "add", rel)
        git(self.raiz, *sello.args_firma(str(self.k_jav)), "commit", "-q", "-m",
            "firmado con la de javier")
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        self.assertTrue(any("firmado por «javier», no por el papel validador" in x["motivo"]
                            for x in self.reg.rechazados()))

    def test_subir_sin_clave_local_lo_que_no_lleva_sello(self):
        """El PC no tiene clave: un proceso nuevo que sube eventos sin sello no revienta
        (fallaba con AttributeError en la primera llamada, S-1271)."""
        self.reg.escribir("tarea.propuesta", ("tarea", "T-3"), "direccion", "dir", "p3",
                          {"titulo": "z", "repo": "app"})
        self.assertIn("subidos", Registro(self.raiz).subir())

    def test_subir_se_niega_sin_sello(self):
        self.a_mano("tarea.verificada", "validador", "w2")
        with self.assertRaisesRegex(SinSello, "sin su sello"):
            self.reg.subir()

    def test_el_validador_con_su_clave_sube_firmado_y_la_otra_maquina_lo_acepta(self):
        self.con(self.k_val)
        self.reg.escribir("tarea.verificada", T, "validador", "validador-gh", "v", {})
        self.reg.subir()
        self.assertEqual(self.reg.ver(*T)["estado"], "verificada")
        otra = self.tmp / "otra"
        subprocess.run(["git", "clone", "-q", str(self.remoto), str(otra)], check=True)
        self.con(None)
        b = Registro(otra)
        self.assertEqual(b.ver(*T)["estado"], "verificada")
        self.assertEqual(b.rechazados(), [])
        # Copiarlo con otro nombre, o editarlo después, lo deja sin sello.
        fich = next(p for p in (otra / "eventos").rglob("*.json")
                    if json.loads(p.read_text(encoding="utf-8"))["tipo"] == "tarea.verificada")
        p = fich.read_text(encoding="utf-8")
        fich.write_text(p.replace('"v"', '"v2"'), encoding="utf-8")
        git(otra, "commit", "-q", "-am", "retoco")
        self.assertTrue(any("tocado después" in x["motivo"] for x in b.rechazados()))
        self.assertEqual(b.ver(*T)["estado"], "entregada")

    def test_rechazado_por_sello_se_acepta_cuando_llega_firmado(self):
        """El propio validador: tras un recalcular antes de subir, su evento sale rechazado; al
        subirlo firmado, el índice lo vuelve a mirar y cuenta."""
        self.con(self.k_val)
        self.reg.escribir("tarea.verificada", T, "validador", "validador-gh", "v", {})
        self.reg.recalcular()
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        self.reg.subir()
        self.assertEqual(self.reg.ver(*T)["estado"], "verificada")
        self.assertEqual(self.reg.rechazados(), [])

    def test_lo_que_entro_antes_del_corte_vale_sin_firma(self):
        rel = self.a_mano("tarea.verificada", "validador", "w2")
        git(self.raiz, "add", rel)
        git(self.raiz, "commit", "-q", "-m", "de antes del sello")
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        cabeza = git(self.raiz, "rev-parse", "HEAD").stdout.strip()
        f = self.raiz / "politica/firmantes"
        f.write_text(f.read_text(encoding="utf-8") + f"desde {cabeza}\n", encoding="utf-8")
        self.assertEqual(self.reg.ver(*T)["estado"], "verificada")
        # Lo que entra después del corte sigue necesitando su firma.
        self.reg.escribir("tarea.propuesta", ("tarea", "T-2"), "direccion", "dir", "p2",
                          {"titulo": "y", "repo": "app"})
        self.reg.subir()
        rel = self.a_mano("tarea.aprobada", "javier", "w1", segundos=8)
        evp = self.raiz / rel
        ev = json.loads(evp.read_text(encoding="utf-8"))
        ev["entidad"]["id"] = "T-2"
        evp.write_text(json.dumps(ev), encoding="utf-8")
        git(self.raiz, "add", rel)
        git(self.raiz, "commit", "-q", "-m", "me apruebo")
        self.assertEqual(self.reg.ver("tarea", "T-2")["estado"], "propuesta")

    def test_la_pausa_no_necesita_sello(self):
        self.reg.escribir("pausa.activada", ("pausa", "P-1"), "javier", "telegram", "pa",
                          {"nivel": "suave", "origen": "telegram"})
        self.assertEqual(self.reg.ver("pausa", "P-1")["estado"], "activa")

    def test_cambiar_la_lista_de_firmantes_recalcula(self):
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
        (self.raiz / "politica/firmantes").write_text("# sin claves\n", encoding="utf-8")
        # Sin la aprobación de Javier la tarea se queda en propuesta y lo de después choca.
        self.assertEqual(self.reg.ver(*T)["estado"], "propuesta")
        self.assertIn("sello", {x["clase"] for x in self.reg.rechazados()})


if __name__ == "__main__":
    unittest.main()
