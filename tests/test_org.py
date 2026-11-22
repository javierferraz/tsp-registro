"""Tests de org. Solo biblioteca estándar: py -m unittest discover -s tests (o pytest)."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from org import Rechazo, Registro
from org.__main__ import main

T = ("tarea", "T-0001")
TAREA = {"titulo": "Tarea de prueba", "repo": "2606-thestromboliproject",
         "criterios": ["se puede cerrar"], "tamano": "S"}

# (tipo, papel, datos) desde nada hasta «cerrada», pasando por el validador.
CICLO = [
    ("tarea.propuesta", "direccion", TAREA),
    ("tarea.aprobada", "javier", {}),
    ("tarea.lista", "planificador", {}),
    ("tarea.asignada", "planificador", {"ejecutor": "lion", "maquina": "pc", "rama": "t-0001"}),
    ("tarea.latido", "trabajador", {}),
    ("tarea.punto_control", "trabajador", {"resumen": "mitad hecha"}),
    ("decision.tomada", "trabajador", {"clase": "A", "elegida": "sqlite", "motivo": "stdlib",
                                       "descartada": "ficheros sueltos"}),
    ("tarea.entregada", "trabajador", {"resumen": "hecho", "commits": ["abc123"]}),
    ("evidencia.verificada", "validador", {"tipo": "salida de prueba", "ruta": "tests.log",
                                           "hash": "sha256:00"}),
    ("tarea.verificada", "validador", {}),
    ("tarea.integrada", "validador", {"commit": "def456"}),
    ("tarea.cerrada", "validador", {}),
]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self._tmp.name) / "registro"
        self.raiz.mkdir()
        self.reg = Registro(self.raiz)

    def tearDown(self):
        self._tmp.cleanup()

    def esc(self, tipo, papel, datos=None, entidad=T, clave=None, **kw):
        return self.reg.escribir(tipo, entidad, papel, f"prueba-{papel}",
                                 clave or f"{entidad[1]}:{tipo}", datos, **kw)

    def hasta(self, n):
        for tipo, papel, datos in CICLO[:n]:
            self.esc(tipo, papel, datos)

    def ficheros(self):
        return sorted(p.relative_to(self.raiz).as_posix()
                      for p in (self.raiz / "eventos").rglob("*.json"))


class CicloDeVida(Base):
    def test_de_propuesta_a_cerrada(self):
        self.hasta(len(CICLO))
        t = self.reg.ver(*T)
        self.assertEqual(t["estado"], "cerrada")
        self.assertEqual([h["estado"] for h in t["historial"]],
                         ["propuesta", "aprobada", "lista", "en_curso", "entregada",
                          "verificada", "integrada", "cerrada"])
        self.assertEqual(t["decisiones"][0]["elegida"], "sqlite")
        self.assertEqual(t["evidencias"][0]["ruta"], "tests.log")
        self.assertEqual(t["ejecucion"]["ejecutor"], "lion")
        self.assertEqual(len(self.ficheros()), len(CICLO))

    def test_nombre_de_fichero_por_fecha_e_id(self):
        r = self.esc(*CICLO[0])
        ev = json.loads((self.raiz / r.fichero).read_text(encoding="utf-8"))
        self.assertRegex(r.fichero, r"^eventos/\d{4}/\d{2}/\d{2}/\d{8}T\d{6}Z-[0-9a-f]{16}\.json$")
        self.assertEqual(ev["id"], r.id)
        self.assertEqual(set(ev), {"id", "tipo", "version_esquema", "fecha", "autor", "entidad",
                                   "correlacion", "clave_idempotencia", "datos"})

    def test_devuelta_vuelve_a_lista_y_cuenta(self):
        self.hasta(8)  # entregada
        self.esc("tarea.devuelta", "validador", {"motivo": "falta captura"})
        self.esc("tarea.lista", "planificador", {}, clave="T-0001:lista-2")
        t = self.reg.ver(*T)
        self.assertEqual((t["estado"], t["devoluciones"]), ("lista", 1))

    def test_transicion_imposible(self):
        self.esc(*CICLO[0])
        with self.assertRaisesRegex(Rechazo, "no vale desde el estado «propuesta»"):
            self.esc("tarea.entregada", "trabajador", {"resumen": "x"})
        with self.assertRaisesRegex(Rechazo, "ya existe"):
            self.esc("tarea.propuesta", "direccion", TAREA, clave="otra")
        with self.assertRaisesRegex(Rechazo, "no existe"):
            self.esc("tarea.aprobada", "javier", {}, entidad=("tarea", "T-9999"))
        self.assertEqual(len(self.ficheros()), 1)


class RechazoPorPapel(Base):
    def test_verificada_con_identidad_de_trabajador(self):
        self.hasta(8)  # entregada
        antes = self.ficheros()
        with self.assertRaisesRegex(Rechazo, "el papel trabajador no puede escribir "
                                             "tarea.verificada"):
            self.esc("tarea.verificada", "trabajador", {})
        self.assertEqual(self.ficheros(), antes)
        self.assertEqual(self.reg.ver(*T)["estado"], "entregada")

    def test_verificada_de_trabajador_por_la_linea_de_ordenes(self):
        self.hasta(8)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            codigo = main(["--raiz", str(self.raiz), "escribir", "tarea.verificada", "tarea:T-0001",
                           "--clave", "k", "--papel", "trabajador", "--identidad", "claude-S-1"])
        self.assertEqual(codigo, 1)
        self.assertIn("rechazado: el papel trabajador no puede escribir tarea.verificada",
                      err.getvalue())

    def test_otros_rechazos_por_papel(self):
        casos = [("objetivo.creado", "direccion", ("objetivo", "O-1"),
                  {"texto": "x", "tipo": "puntual"}),
                 ("tarea.propuesta", "trabajador", T, TAREA)]
        for tipo, papel, ent, datos in casos:
            with self.subTest(tipo=tipo, papel=papel), self.assertRaises(Rechazo):
                self.esc(tipo, papel, datos, entidad=ent)
        self.hasta(5)  # en curso
        with self.assertRaisesRegex(Rechazo, "clase C solo la escribe el papel javier"):
            self.esc("decision.tomada", "trabajador", {"clase": "C", "elegida": "x",
                                                       "motivo": "y"})
        self.esc("decision.tomada", "javier", {"clase": "C", "elegida": "x", "motivo": "y"})

    def test_evento_hecho_a_mano_no_cuenta(self):
        """Un fichero escrito sin org (un trabajador que se da por verificado) no entra."""
        self.hasta(8)
        fecha = datetime.now(timezone.utc) + timedelta(seconds=5)
        ev = {"id": "f" * 16, "tipo": "tarea.verificada", "version_esquema": 1,
              "fecha": fecha.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
              "autor": {"papel": "trabajador", "identidad": "claude-S-1"},
              "entidad": {"tipo": "tarea", "id": "T-0001"}, "correlacion": None,
              "clave_idempotencia": "a-mano", "datos": {}}
        ruta = self.raiz / f"eventos/{fecha:%Y/%m/%d}/{fecha:%Y%m%dT%H%M%SZ}-{ev['id']}.json"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(ev), encoding="utf-8")
        for paso in (lambda: None, self.reg.recalcular):
            paso()
            self.assertEqual(self.reg.ver(*T)["estado"], "entregada")
            self.assertEqual(len(self.reg.rechazados()), 1)
            self.assertIn("trabajador no puede", self.reg.rechazados()[0]["motivo"])


class Idempotencia(Base):
    def test_repetir_no_hace_nada(self):
        a = self.esc(*CICLO[0])
        b = self.esc(*CICLO[0])
        self.assertTrue(a.nuevo)
        self.assertFalse(b.nuevo)
        self.assertEqual((a.id, a.fichero), (b.id, b.fichero))
        self.assertEqual(len(self.ficheros()), 1)

    def test_repetir_tras_borrar_el_indice(self):
        a = self.esc(*CICLO[0])
        self.reg.recalcular()
        b = self.esc(*CICLO[0])
        self.assertFalse(b.nuevo)
        self.assertEqual(a.id, b.id)

    def test_misma_clave_otro_contenido(self):
        self.esc(*CICLO[0])
        with self.assertRaisesRegex(Rechazo, "ya la usó otro evento distinto"):
            self.esc("tarea.propuesta", "direccion", {**TAREA, "titulo": "otro"})
        self.assertEqual(len(self.ficheros()), 1)

    def test_clave_duplicada_desde_otra_maquina(self):
        """Dos máquinas registran lo mismo sin verse: al juntarse, cuenta el primero."""
        otra = Registro(Path(self._tmp.name) / "otra")
        self.esc(*CICLO[0])
        r = otra.escribir("tarea.propuesta", T, "direccion", "prueba-direccion",
                          "T-0001:tarea.propuesta", TAREA,
                          ahora=datetime.now(timezone.utc) + timedelta(seconds=2))
        destino = self.raiz / r.fichero
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(otra.raiz / r.fichero, destino)
        self.assertEqual(self.reg.ver(*T)["estado"], "propuesta")
        self.assertIn("clave de idempotencia repetida", self.reg.rechazados()[0]["motivo"])
        self.assertEqual(self.reg.rechazados()[0]["clase"], "conflicto")


class Recalcular(Base):
    def test_desde_cero_da_lo_mismo(self):
        self.hasta(len(CICLO))
        self.esc("incidencia.abierta", "laboratorio",
                 {"origen": "prueba", "gravedad": "media", "descripcion": "texto cortado",
                  "app": "silent-frog"}, entidad=("incidencia", "I-1"))
        self.esc("objetivo.creado", "javier", {"texto": "probar el registro",
                                               "tipo": "puntual"}, entidad=("objetivo", "O-1"))
        self.reg.volcar_estado()
        antes = {p.name: p.read_bytes() for p in (self.raiz / "estado").glob("*.json")
                 if p.name != "resumen.json"}
        shutil.rmtree(self.raiz / ".indice")
        shutil.rmtree(self.raiz / "estado")
        resumen = self.reg.recalcular()
        despues = {p.name: p.read_bytes() for p in (self.raiz / "estado").glob("*.json")
                   if p.name != "resumen.json"}
        self.assertEqual(antes, despues)
        self.assertEqual(set(antes), {"tareas.json", "incidencias.json", "objetivos.json",
                                      "rechazados.json"})
        self.assertEqual(resumen["eventos"], len(CICLO) + 2)
        self.assertEqual(resumen["por_estado"]["tarea"], {"cerrada": 1})
        tareas = json.loads((self.raiz / "estado/tareas.json").read_text(encoding="utf-8"))
        self.assertEqual(tareas["T-0001"]["estado"], "cerrada")

    def test_reloj_atrasado_no_desordena(self):
        """Si el reloj de quien escribe va por detrás, la fecha se adelanta a la del último
        evento de la entidad y el recálculo da el mismo estado."""
        self.esc(*CICLO[0])
        atrasado = datetime.now(timezone.utc) - timedelta(hours=1)
        r = self.reg.escribir("tarea.aprobada", T, "javier", "j", "k-aprobada", {},
                              ahora=atrasado)
        self.assertTrue(r.nuevo)
        self.reg.recalcular()
        self.assertEqual(self.reg.ver(*T)["estado"], "aprobada")
        self.assertEqual(self.reg.rechazados(), [])

    def test_llegan_eventos_anteriores_de_otra_maquina(self):
        """Un pull trae eventos más antiguos que los locales: el índice se rehace solo."""
        otra = Registro(Path(self._tmp.name) / "otra")
        pasado = datetime.now(timezone.utc) - timedelta(hours=1)
        r = otra.escribir("tarea.propuesta", ("tarea", "T-2"), "direccion", "d", "k-t2", TAREA,
                          ahora=pasado)
        self.hasta(3)
        self.assertIsNone(self.reg.ver("tarea", "T-2"))
        destino = self.raiz / r.fichero
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(otra.raiz / r.fichero, destino)
        self.assertEqual(self.reg.ver("tarea", "T-2")["estado"], "propuesta")
        self.assertEqual(self.reg.ver(*T)["estado"], "lista")


def poner(raiz: Path, ev, nombre=None):
    """Deja un fichero en eventos/ sin pasar por org (lo que haría un pull o una mano)."""
    ruta = raiz / "eventos/2026/11/22" / (nombre or f"20261122T120000Z-{'e' * 16}.json")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(ev if isinstance(ev, str) else json.dumps(ev), encoding="utf-8")
    return ruta


class FicherosRaros(Base):
    def base_ev(self, **cambios):
        ev = {"id": "e" * 16, "tipo": "tarea.propuesta", "version_esquema": 1,
              "fecha": "2026-11-22T12:00:00.000000Z",
              "autor": {"papel": "direccion", "identidad": "x"},
              "entidad": {"tipo": "tarea", "id": "T-9"}, "correlacion": None,
              "clave_idempotencia": "k-raro", "datos": TAREA}
        ev.update(cambios)
        return ev

    def test_un_fichero_malo_no_tumba_el_registro(self):
        self.hasta(2)
        malos = ["{no es json", "[1, 2]",
                 self.base_ev(fecha="2026-13-45T00:00:00.000000Z"),
                 self.base_ev(tipo=["x"]), self.base_ev(version_esquema=[1]),
                 self.base_ev(version_esquema=True), self.base_ev(autor={"papel": ["x"],
                                                                          "identidad": "x"}),
                 self.base_ev(fecha="２０２6-11-22T12:00:00.000000Z"),
                 self.base_ev(datos={**TAREA, "tamano": ["S"]})]
        for i, ev in enumerate(malos):
            with self.subTest(caso=i):
                ruta = poner(self.raiz, ev, f"20261122T120000Z-{i:016x}.json")
                self.assertEqual(self.reg.ver(*T)["estado"], "aprobada")
                self.assertIsNone(self.reg.ver("tarea", "T-9"))
                r = self.reg.rechazados()
                self.assertEqual([x["clase"] for x in r], ["esquema"])
                self.assertTrue(self.esc("objetivo.creado", "javier", {"texto": "x",
                                         "tipo": "puntual"}, entidad=("objetivo", f"O-{i}")).nuevo)
                ruta.unlink()
        self.reg.recalcular()
        self.assertEqual(self.reg.rechazados(), [])

    def test_fichero_ya_indexado_editado_o_borrado(self):
        """El índice ve que un fichero conocido ha cambiado y se rehace: sale lo mismo que al
        recalcular desde cero."""
        self.hasta(2)
        ultimo = self.raiz / self.esc(*CICLO[2]).fichero
        ev = json.loads(ultimo.read_text(encoding="utf-8"))
        ev["autor"]["papel"] = "trabajador"  # alguien lo «retoca» a mano
        ultimo.write_text(json.dumps(ev), encoding="utf-8")
        self.assertEqual(self.reg.ver(*T)["estado"], "aprobada")
        self.assertEqual(self.reg.rechazados()[0]["clase"], "esquema")
        ultimo.unlink()
        self.assertEqual(self.reg.ver(*T)["estado"], "aprobada")
        self.assertEqual(self.reg.rechazados(), [])


class Esquema(Base):
    def test_version_no_admitida(self):
        with self.assertRaisesRegex(Rechazo, "versión de esquema 2 no admitida"):
            self.esc(*CICLO[0], version=2)

    def test_datos(self):
        malos = [({**TAREA, "inventado": 1}, "campo no previsto"),
                 ({"repo": "x"}, "datos.titulo: obligatorio"),
                 ({**TAREA, "tamano": "XL"}, "no es uno de"),
                 ({**TAREA, "criterios": "uno"}, "tiene que ser list"),
                 ({**TAREA, "titulo": "  "}, "vacío")]
        for datos, motivo in malos:
            with self.subTest(motivo=motivo), self.assertRaisesRegex(Rechazo, motivo):
                self.esc("tarea.propuesta", "direccion", datos)
        with self.assertRaisesRegex(Rechazo, "tipo desconocido"):
            self.esc("tarea.inventada", "javier", {})
        with self.assertRaisesRegex(Rechazo, "va sobre una entidad tarea"):
            self.esc("tarea.propuesta", "direccion", TAREA, entidad=("objetivo", "O-1"))

    def test_correlacion(self):
        a = self.esc(*CICLO[0])
        b = self.esc("tarea.aprobada", "javier", {}, correlacion=a.id)
        self.assertTrue(b.nuevo)
        with self.assertRaisesRegex(Rechazo, "correlación con un evento que no existe"):
            self.esc("tarea.lista", "planificador", {}, correlacion="0" * 16)


@unittest.skipUnless(shutil.which("git"), "sin git")
class Subir(Base):
    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.raiz), *args], check=True,
                              capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        remoto = Path(self._tmp.name) / "remoto.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "org@prueba")
        self.git("config", "user.name", "org")
        (self.raiz / ".gitignore").write_text(".indice/\nestado/\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("commit", "-q", "-m", "inicio")
        self.git("remote", "add", "origin", str(remoto))
        self.git("push", "-q", "-u", "origin", "main")
        self.remoto = remoto

    def test_sube_solo_eventos_y_no_si_se_tocan(self):
        self.hasta(2)
        self.assertEqual(self.reg.subir(), "2 evento(s) subidos")
        enviados = self.git("ls-tree", "-r", "--name-only", "main", cwd=self.remoto).stdout.split()
        self.assertEqual(sorted(f for f in enviados if f.startswith("eventos/")), self.ficheros())
        primero = self.raiz / self.ficheros()[0]
        primero.write_text(primero.read_text(encoding="utf-8").replace("Tarea", "Otra"),
                           encoding="utf-8")
        with self.assertRaisesRegex(Rechazo, "modificados o borrados"):
            self.reg.subir()

    def test_conflicto_entre_maquinas_no_atasca_la_subida(self):
        """Dos máquinas mueven la misma tarea sin verse. El evento que queda detrás es un
        conflicto: se sube igual, y las dos máquinas acaban con el mismo estado."""
        self.hasta(3)  # lista
        self.reg.subir()
        otra = Path(self._tmp.name) / "otra"
        subprocess.run(["git", "clone", "-q", str(self.remoto), str(otra)], check=True)
        self.git("config", "user.email", "org@prueba", cwd=otra)
        self.git("config", "user.name", "org", cwd=otra)
        reg_b = Registro(otra)
        self.esc(*CICLO[3])  # A asigna
        self.reg.subir()
        reg_b.escribir("tarea.descartada", T, "javier", "j", "B-descarta", {"motivo": "no"},
                       ahora=datetime.now(timezone.utc) + timedelta(seconds=5))
        self.git("pull", "-q", "--rebase", cwd=otra)  # B trae la asignación antes de subir
        self.assertEqual(reg_b.subir(), "1 evento(s) subidos")
        self.git("pull", "-q", "--rebase")
        for reg in (self.reg, reg_b):
            self.assertEqual(reg.ver(*T)["estado"], "en_curso")
            self.assertEqual([r["clase"] for r in reg.rechazados()], ["conflicto"])


if __name__ == "__main__":
    unittest.main()
