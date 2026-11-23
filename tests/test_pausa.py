"""Tests del interruptor de pausa (PAA2 P5-2, S-1273).

Criterio de la sesión: «la pausa dura detiene lo que corre» (PausaDura*): un trabajador de
verdad, en otro proceso, latiendo; se activa una pausa dura y para solo, con punto de control.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from org import Rechazo, pausa as pz, planificador as pl, trabajo
from org.__main__ import main

from test_org import CICLO, Base

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
RAIZ_CODIGO = Path(__file__).resolve().parents[1]


def en(minutos: float) -> datetime:
    return T0 + timedelta(minutes=minutos)


class Pausas(Base):
    def tarea(self, tid="T-0001", hasta="tarea.asignada", **datos):
        for tipo, papel, d in CICLO:
            if tipo == "tarea.propuesta":
                d = {**d, **datos}
            self.reg.escribir(tipo, ("tarea", tid), papel, f"p-{papel}", f"{tid}:{tipo}", d,
                              ahora=en(0))
            if tipo == hasta:
                return

    def pausar(self, pid="P-1", nivel="dura", ambito="general", papel="javier"):
        self.reg.escribir("pausa.activada", ("pausa", pid), papel, f"q-{papel}", f"pausa:{pid}",
                          {"nivel": nivel, "origen": "prueba", "ambito": ambito}, ahora=en(1))

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = main(["--raiz", str(self.raiz), *args])
        return rc, out.getvalue()


class Ambitos(Pausas):
    def test_cada_ambito_alcanza_solo_lo_suyo(self):
        que = pz.que_es("T-1", {"repo": "2608-silent-frog", "tipo_trabajo": "interfaz",
                                "objetivo": "O-7"}, {"app": "Silent Frog"})
        si = ("general", "app:silent-frog", "tipo:interfaz", "proyecto:O-7", "ejecucion:T-1")
        no = ("app:clever-cat", "tipo:codigo", "proyecto:O-8", "ejecucion:T-2")
        for a in si:
            self.assertTrue(pz.alcanza(a, que), a)
        for a in no:
            self.assertFalse(pz.alcanza(a, que), a)

    def test_ambito_mal_escrito_se_rechaza(self):
        for malo in ("app:", "todo", "tipo:inventado", "app:Clever Cat"):
            with self.assertRaises(Rechazo, msg=malo):
                self.pausar(pid=f"P-{malo}", ambito=malo)

    def test_cualquiera_puede_pedir_la_pausa_solo_javier_la_retira(self):
        self.pausar(papel="trabajador")
        with self.assertRaises(Rechazo):
            self.reg.escribir("pausa.retirada", ("pausa", "P-1"), "trabajador", "w", "r", {},
                              ahora=en(2))
        self.reg.escribir("pausa.retirada", ("pausa", "P-1"), "javier", "j", "r", {},
                          ahora=en(2))
        self.assertEqual(pz.activas(self.reg), [])

    def test_comprobar_da_el_nivel_mas_alto_y_su_salida(self):
        self.tarea(repo="2608-silent-frog")
        self.assertEqual(pz.comprobar(self.reg, "T-0001")["salida"], 0)
        self.pausar("P-1", "suave", "general")
        self.pausar("P-2", "emergencia", "app:clever-cat")
        r = pz.comprobar(self.reg, "T-0001")
        self.assertEqual((r["nivel"], r["salida"]), ("suave", 10))
        self.assertEqual(pz.comprobar(self.reg, app="clever-cat")["salida"], 12)
        rc, out = self.cli("pausa", "comprobar", "--app", "clever-cat", "--sin-traer")
        self.assertEqual(rc, 12)
        self.assertIn("emergencia", out)

    def test_planificador_solo_frena_lo_que_alcanza_el_ambito(self):
        for tid, repo in (("A", "2608-silent-frog"), ("B", "2606-clever-cat")):
            self.tarea(tid=tid, hasta="tarea.lista", repo=repo, tipo_trabajo="codigo")
        self.pausar("P-1", "suave", "app:silent-frog")
        plan = pl.planificar(self.reg, capacidad=3, ahora=en(60))
        bloq = {b["tarea"]: b["motivo"] for b in plan["no_arranca"]}
        self.assertIn("pausa activa (P-1)", bloq.get("A", ""))
        self.assertEqual([a["tarea"] for a in plan["arrancar"]], ["B"])
        self.assertEqual(plan["pausa_general"], [])
        # una general lo frena todo, como antes
        self.pausar("P-2", "suave", "general")
        plan = pl.planificar(self.reg, capacidad=3, ahora=en(61))
        self.assertEqual(plan["arrancar"], [])
        self.assertEqual(plan["pausa_general"], ["P-2"])


class PausaDura(Pausas):
    def test_latido_dice_que_pare_con_dura_y_no_con_suave(self):
        self.tarea()
        self.assertFalse(trabajo.latido(self.reg, "T-0001", "lion", ahora=en(5),
                                        subir=False)["parar"])
        self.pausar("P-1", "suave")
        self.assertFalse(trabajo.latido(self.reg, "T-0001", "lion", ahora=en(6),
                                        subir=False)["parar"])
        self.pausar("P-2", "dura", "ejecucion:T-0001")
        r = trabajo.latido(self.reg, "T-0001", "lion", ahora=en(7), subir=False)
        self.assertTrue(r["parar"])
        self.assertEqual(r["pausa"]["nivel"], "dura")
        rc, _ = self.cli("latido", "T-0001", "--identidad", "lion", "--sin-subir")
        self.assertEqual(rc, 4)

    def test_la_pausa_dura_detiene_lo_que_corre(self):
        """Un trabajador en otro proceso late cada 0,2 s. Javier activa una pausa dura por la
        línea de órdenes; el trabajador sale solo, tras guardar punto de control."""
        self.tarea()
        trabajador = Path(self._tmp.name) / "trabajador.py"
        trabajador.write_text(TRABAJADOR, encoding="utf-8")
        p = subprocess.Popen([sys.executable, str(trabajador), str(self.raiz), "T-0001"],
                             cwd=RAIZ_CODIGO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            time.sleep(3)
            self.assertIsNone(p.poll(), "el trabajador paró antes de la pausa")
            rc, _ = self.cli("pausa", "activar", "--nivel", "dura", "--identidad", "javier-prueba",
                             "--motivo", "prueba", "--sin-subir")
            self.assertEqual(rc, 0)
            salida, _ = p.communicate(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
        self.assertEqual(p.returncode, 0, salida)
        self.assertIn("parado por pausa", salida)
        t = self.reg.ver("tarea", "T-0001")
        self.assertEqual(t["estado"], "en_pausa")
        self.assertEqual(t["punto_control"]["sigue"], "retomar donde lo dejé")
        self.assertIn("pausa dura", t["historial"][-1]["motivo"])


TRABAJADOR = r'''
import subprocess, sys, time
raiz, tarea = sys.argv[1], sys.argv[2]
org = [sys.executable, "-m", "org", "--raiz", raiz]
sin = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0), "capture_output": True}
vueltas = 0
while vueltas < 300:
    vueltas += 1
    r = subprocess.run(org + ["latido", tarea, "--identidad", "lion", "--sin-subir"], **sin)
    if r.returncode == 4:
        subprocess.run(org + ["punto-control", tarea, "--hecho", "la mitad", "--sigue",
                              "retomar donde lo dejé", "--identidad", "lion", "--sin-subir"],
                       check=True, **sin)
        subprocess.run(org + ["escribir", "tarea.pausada", "tarea:" + tarea, "--papel",
                              "trabajador", "--identidad", "lion", "--clave", "pausada-1",
                              "--datos", '{"motivo": "pausa dura"}'], check=True, **sin)
        print("parado por pausa en la vuelta", vueltas)
        sys.exit(0)
    if r.returncode != 0:
        print("latido falló", r.returncode, r.stdout, r.stderr)
        sys.exit(1)
    time.sleep(0.2)
print("nunca se pidió parar")
sys.exit(2)
'''


class Emergencia(Pausas):
    def setUp(self):
        super().setUp()
        (self.raiz / "politica").mkdir()
        (self.raiz / "politica" / "pausa.json").write_text(json.dumps({"emergencia_desactiva": [
            {"repo": "o/web", "workflow": "deploy.yml"},
            {"repo": "o/fab", "workflow": "tanda.yml"}]}), encoding="utf-8")

    def test_emergencia_desactiva_y_al_retirarla_reactiva(self):
        llamadas = []

        def gh(args, **kw):
            llamadas.append(" ".join(args[1:]))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch("org.pausa.subprocess.run", side_effect=gh):
            self.assertEqual(pz.emergencia(self.reg, hacer=True)["pasos"], [])  # sin pausa
            self.pausar("P-1", "emergencia")
            ensayo = pz.emergencia(self.reg)  # sin hacer: solo dice
            self.assertEqual(len(ensayo["pasos"]), 2)
            self.assertEqual(llamadas, [])
            pz.emergencia(self.reg, hacer=True)
            self.assertEqual(llamadas, ["workflow disable deploy.yml -R o/web",
                                        "workflow disable tanda.yml -R o/fab"])
            pz.emergencia(self.reg, hacer=True)  # ya hecho: no repite
            self.assertEqual(len(llamadas), 2)
            self.reg.escribir("pausa.retirada", ("pausa", "P-1"), "javier", "j", "r", {},
                              ahora=en(5))
            pz.emergencia(self.reg, hacer=True)
        self.assertEqual(llamadas[2:], ["workflow enable deploy.yml -R o/web",
                                        "workflow enable tanda.yml -R o/fab"])

    def test_si_gh_falla_lo_dice_y_lo_reintenta_la_proxima_vez(self):
        self.pausar("P-1", "emergencia")
        with mock.patch("org.pausa.subprocess.run", return_value=subprocess.CompletedProcess(
                [], 1, "", "HTTP 403")):
            r = pz.emergencia(self.reg, hacer=True)
        self.assertEqual(len(r["fallos"]), 2)
        self.assertEqual(len(pz.emergencia(self.reg)["pasos"]), 2)


if __name__ == "__main__":
    unittest.main()
