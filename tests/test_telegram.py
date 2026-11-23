"""Tests de los botones de Telegram (S-1268): lista cerrada, pausa y reanudar, idempotencia."""
from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from org import Rechazo, Registro
from org import telegram as tg
from org.__main__ import main


class Telegram(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.reg = Registro(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_lista_cerrada(self):
        self.assertEqual(tg.interpretar("p:suave"), ("pausar", "suave"))
        self.assertEqual(tg.interpretar("p:emergencia"), ("pausar", "emergencia"))
        self.assertEqual(tg.interpretar("r"), ("reanudar", ""))
        self.assertEqual(tg.interpretar("a:T-0001"), ("aprobar", "T-0001"))
        self.assertEqual(tg.interpretar("x:T-0001"), ("rechazar", "T-0001"))
        self.assertEqual(tg.interpretar("d:S-1268"), ("detalle", "S-1268"))
        for malo in ("", "publicar", "p:total", "r ", "a:", "a:../x", "p:suave;r",
                     "hola, pausa todo", "x:" + "a" * 42, None):
            with self.assertRaises(Rechazo, msg=repr(malo)):
                tg.interpretar(malo)

    def test_misma_lista_que_el_worker(self):
        js = (Path(__file__).resolve().parent.parent / "telegram" / "worker.mjs").read_text(
            encoding="utf-8")
        self.assertIn(f"export const ACCION = /{tg._ACCION.pattern}/;", js)
        self.assertIn(f"const PULSACION = /{tg._PULSACION.pattern}/;", js)

    def test_pausa_y_reanudar_se_registran(self):
        r = tg.aplicar(self.reg, "p:suave", "111")
        self.assertEqual(len(r["eventos"]), 1)
        self.assertEqual(tg.pausas_activas(self.reg), ["tg-111"])
        self.assertEqual(r["botones"], [("▶ Reanudar", "r")])
        pausa = self.reg.ver("pausa", "tg-111")
        self.assertEqual((pausa["estado"], pausa["nivel"], pausa["origen"]),
                         ("activa", "suave", "telegram"))
        self.assertEqual(pausa["historial"][0]["autor"], {"papel": "javier",
                                                          "identidad": "telegram"})
        r = tg.aplicar(self.reg, "r", "112")
        self.assertEqual(len(r["eventos"]), 1)
        self.assertEqual(tg.pausas_activas(self.reg), [])
        self.assertEqual(self.reg.ver("pausa", "tg-111")["estado"], "retirada")
        self.assertEqual(r["botones"], [("⏸ Pausar", "p:suave")])
        self.assertEqual(self.reg.rechazados(), [])

    def test_pulsacion_repetida_no_duplica(self):
        tg.aplicar(self.reg, "p:dura", "200")
        r = tg.aplicar(self.reg, "p:dura", "200")  # Telegram o GitHub reintentan
        self.assertEqual(r["eventos"], [])
        r = tg.aplicar(self.reg, "r", "201")
        self.assertEqual(tg.aplicar(self.reg, "r", "201")["eventos"], [])
        self.assertEqual(len(self.reg.eventos_de("pausa")), 2)

    def test_reanudar_sin_pausa_no_escribe(self):
        r = tg.aplicar(self.reg, "r", "300")
        self.assertEqual(r["eventos"], [])
        self.assertIn("No había", r["texto"])

    def test_reanudar_retira_todas(self):
        tg.aplicar(self.reg, "p:suave", "400")
        tg.aplicar(self.reg, "p:emergencia", "401")
        r = tg.aplicar(self.reg, "r", "402")
        self.assertEqual(len(r["eventos"]), 2)
        self.assertEqual(tg.pausas_activas(self.reg), [])

    def test_aprobar_y_rechazar_respetan_el_ciclo(self):
        for t in ("T-1", "T-2"):
            self.reg.escribir("tarea.propuesta", ("tarea", t), "direccion", "d", f"{t}:p",
                              {"titulo": "x", "repo": "r"})
        tg.aplicar(self.reg, "a:T-1", "500")
        tg.aplicar(self.reg, "x:T-2", "501")
        self.assertEqual(self.reg.ver("tarea", "T-1")["estado"], "aprobada")
        self.assertEqual(self.reg.ver("tarea", "T-2")["estado"], "descartada")
        with self.assertRaises(Rechazo):  # ya no está en «propuesta»
            tg.aplicar(self.reg, "a:T-2", "502")

    def test_detalle_no_escribe(self):
        self.reg.escribir("tarea.propuesta", ("tarea", "T-3"), "direccion", "d", "T-3:p",
                          {"titulo": "secreto", "repo": "r"})
        r = tg.aplicar(self.reg, "d:T-3", "600")
        self.assertIn("propuesta", r["texto"])
        self.assertNotIn("secreto", r["texto"])  # sin títulos
        self.assertIn("No hay", tg.aplicar(self.reg, "d:T-9", "601")["texto"])
        self.assertEqual(len(self.reg.eventos_de("tarea")), 1)

    def test_pulsacion_mal_formada(self):
        for malo in ("", "a b", "x" * 41, "../e"):
            with self.assertRaises(Rechazo):
                tg.aplicar(self.reg, "p:suave", malo)

    def test_enviar_sin_credenciales_no_lanza(self):
        with mock.patch.dict("os.environ", {}, clear=True), redirect_stderr(io.StringIO()):
            self.assertFalse(tg.enviar("hola", [("⏸ Pausar", "p:suave")]))

    def test_cli(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()), \
                mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(main(["--raiz", str(self.dir), "telegram", "--accion", "p:suave",
                                   "--pulsacion", "700", "--enviar"]), 0)
            self.assertEqual(main(["--raiz", str(self.dir), "telegram", "--accion", "publicar",
                                   "--pulsacion", "701", "--enviar"]), 1)
            self.assertEqual(main(["--raiz", str(self.dir), "telegram", "--panel"]), 0)
        primero = json.loads(out.getvalue().split("\n}\n")[0] + "}")
        self.assertFalse(primero["enviado"])
        self.assertEqual(len(primero["eventos"]), 1)
        self.assertIn("En pausa", out.getvalue())


if __name__ == "__main__":
    unittest.main()
