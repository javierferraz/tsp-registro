"""Tests del parte diario (S-1266). Datos sintéticos; la pasada con el registro real está en la
nota de la tarjeta."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from org import Registro
from org.parte import datos, generar, render

AHORA = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)


def tarj(estado, app="", movs=(), check=None, actualizada="2026-11-22T10:00:00Z"):
    return {"estado": estado, "datos": {"app": app, "check": check, "actualizada": actualizada,
                                        "movimientos": [{"a": a, "de": "x", "fecha": f}
                                                        for a, f in movs]}}


def ejec(estado, repo="2606-clever-cat", actualizada="2026-11-22T10:00:00Z",
         verificacion=None, creada="2026-11-22T09:00:00Z", minutos=10):
    return {"estado": estado, "datos": {"repo": repo, "actualizada": actualizada,
                                        "creada": creada, "verificacion": verificacion,
                                        "minutos": minutos}}


INFORME = {"fecha": "2026-11-23", "comparacion": {}, "cartera": {}}


class Parte(unittest.TestCase):
    def test_sin_nada_lo_dice_en_una_linea(self):
        p = datos({}, {}, {}, [], INFORME, AHORA)
        self.assertEqual(p["decide"], [])
        h = render(p)
        self.assertIn("Nada necesita tu decisión.", h)
        self.assertIn("Ningún problema abierto.", h)
        self.assertIn("Nada terminado en las últimas 24 h.", h)
        self.assertIn("Ventas: sin dato", h)  # dice lo que no sabe

    def test_lo_de_javier_agrupado_por_tipo_y_lo_mas_viejo_primero(self):
        t = {f"S-{n}": tarj("info", movs=[("info", f"2026-11-{n:02d}T10:00:00Z")])
             for n in (10, 2, 20)}
        t["S-9"] = tarj("validar", check="decision", movs=[("validar", "2026-11-01T10:00:00Z")])
        t["S-8"] = tarj("validar", check="ojos")  # no es suyo: va a «Sin verificar»
        e = {"T-1": ejec("blocked")}
        p = datos(t, e, {}, [], INFORME, AHORA)
        self.assertEqual(len(p["decide"]), 3)
        self.assertEqual([i for i, _ in p["decide"][0]["enlaces"]], ["S-2", "S-10", "S-20"])
        self.assertIn("1 terminada(s) que piden tu decisión", p["decide"][1]["texto"])
        self.assertTrue(p["decide"][2]["lion"])
        h = render(p)
        self.assertIn('href="http://localhost/2606-thestromboliproject/tablero/#S-2"', h)
        self.assertNotIn("#T-1", h)  # Lion no tiene enlace (sería una ruta del disco)
        self.assertIn("(5)", h)

    def test_registro_atrasado_o_sin_informe_va_arriba_en_problemas(self):
        p = datos({}, {}, {}, [], {**INFORME, "fecha": "2026-11-20"}, AHORA)
        self.assertEqual(p["problemas"][0]["nivel"], "alto")
        self.assertIn("2026-11-20", p["problemas"][0]["texto"])
        p = datos({}, {}, {}, [], None, AHORA)
        self.assertIn("No hay informe diario", p["problemas"][0]["texto"])

    def test_verde_falso_del_vigia_fichas_sale_como_problema(self):
        inf = {"x": {"datos": {"vigia": "fichas", "fecha": "2026-11-17",
                               "cifras": {"sin_revisar_por_error": 9}}}}
        p = datos({}, {}, inf, [], INFORME, AHORA)
        self.assertTrue(any("9 app(s) sin revisar" in f["texto"] for f in p["problemas"]))

    def test_hecho_solo_ultimas_24h_y_agrupado_por_app(self):
        t = {"S-1": tarj("hecha", "Clever Cat", [("hecha", "2026-11-22T20:00:00Z")]),
             "S-2": tarj("hecha", "Clever Cat", [("hecha", "2026-11-22T21:00:00Z")]),
             "S-3": tarj("hecha", "Tidy Koala", [("hecha", "2026-11-10T21:00:00Z")])}
        p = datos(t, {}, {}, [], INFORME, AHORA)
        self.assertEqual(p["hecho"][0]["texto"], "2 tarjeta(s) terminadas: 2 en Clever Cat.")

    def test_tope_por_seccion(self):
        t = {f"S-{n}": tarj("curso", actualizada="2026-11-01T00:00:00Z") for n in range(20)}
        h = render(datos(t, {}, {}, [], INFORME, AHORA))
        self.assertIn("…", h)  # los parados se cortan a 8 identificadores

    def test_escapa_y_no_mete_textos(self):
        t = {"S-1": tarj("curso", app="<b>x</b>")}
        h = render(datos(t, {}, {}, [], INFORME, AHORA))
        self.assertNotIn("<b>x</b>", h)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", h)

    def test_generar_escribe_fuera_de_git(self):
        with tempfile.TemporaryDirectory() as d:
            reg = Registro(d)
            ruta = generar(reg, AHORA)
            self.assertEqual(ruta, Path(d) / "estado" / "parte.html")
            self.assertIn("No hay informe diario", ruta.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
