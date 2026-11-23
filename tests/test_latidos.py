"""Tests de los latidos de procesos y de los dos vigilantes (PAA2 P5-2, S-1273).

Criterio de la sesión: «un fallo provocado de un proceso genera aviso» (FalloProvocado*).
Los avisos se comprueban en `.indice/avisos.log` (sin Telegram: enviar=False).
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from org import latidos as lt, motor as mt
from org.__main__ import main

from test_org import Base

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
ALTA = "2026-11-23T00:00:00Z"
PROCESOS = {
    "registro-diario": {"donde": "PC", "fuente": "propio", "cada_horas": 24,
                        "margen_horas": 2, "alta": ALTA},
    "vigilante-github": {"donde": "GitHub", "fuente": "propio", "cada_horas": 6,
                         "margen_horas": 2, "alta": ALTA},
    "parte": {"donde": "GitHub", "fuente": "github", "repo": "r", "workflow": "w.yml",
              "cada_horas": 24, "margen_horas": 2, "via": "registro-diario", "alta": ALTA},
}


def en(horas: float) -> datetime:
    return T0 + timedelta(hours=horas)


class Latidos(Base):
    def avisos(self):
        f = self.raiz / ".indice" / "avisos.log"
        return f.read_text(encoding="utf-8").splitlines() if f.is_file() else []

    def latir(self, nombre, resultado="ok", h=0):
        return lt.latir(self.reg, nombre, resultado, f"{nombre}-prueba", ahora=en(h))

    def vigilar(self, h, **kw):
        return lt.vigilar(self.reg, PROCESOS, escribir=True, ahora=en(h), **kw)

    def clase(self, r, nombre):
        return {f["proceso"]: f["clase"] for f in r["procesos"]}[nombre]


class FalloProvocado(Latidos):
    def test_un_proceso_que_falla_genera_aviso(self):
        self.latir("vigilante-github", h=0)
        self.latir("registro-diario", "falla", h=0)
        r = self.vigilar(1)
        self.assertEqual(r["alertas"], [{"proceso": "registro-diario", "clase": "falla"}])
        self.assertEqual(len(self.avisos()), 1)
        self.assertIn("Ha fallado registro-diario", self.avisos()[0])
        self.assertEqual(self.reg.ver("proceso", "registro-diario")["alerta"]["clase"], "falla")
        # el motor convierte la alerta en incidencia (regla latido-perdido)
        mt.correr(self.reg, escribir=True, ahora=en(1.5))
        inc = list(self.reg.entidades("incidencia").values())
        self.assertEqual([i["huella"] for i in inc], ["proceso:registro-diario:falla"])

    def test_un_proceso_que_no_late_genera_aviso_una_sola_vez(self):
        self.latir("vigilante-github", h=0)
        self.latir("registro-diario", h=0)
        self.latir("parte", h=10)
        self.latir("vigilante-github", h=20)
        self.assertEqual(self.vigilar(20)["alertas"], [])  # 20 h < 26 h
        r = self.vigilar(27)
        self.assertEqual(r["alertas"], [{"proceso": "registro-diario", "clase": "falta"}])
        self.assertIn("Falta el latido de registro-diario", self.avisos()[-1])
        self.latir("vigilante-github", h=27)
        self.assertEqual(self.vigilar(33)["alertas"], [])  # mismo último latido: no repite
        self.assertEqual(len(self.avisos()), 1)
        self.latir("registro-diario", h=34)  # vuelve
        self.assertEqual(self.clase(self.vigilar(35), "registro-diario"), "al_dia")

    def test_diario_que_se_cae_deja_latido_de_falla_y_el_vigilante_avisa(self):
        """Fallo provocado de verdad: el diario apunta a un repo central que no existe."""
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = main(["--raiz", str(self.raiz), "diario", "--central",
                       str(Path(self._tmp.name) / "no-existe"), "--sin-fetch", "--sin-subir",
                       "--sin-avisar", "--identidad", "importador-prueba"])
        self.assertNotEqual(rc, 0)
        u = self.reg.ver("proceso", "registro-diario")["ultimo"]
        self.assertEqual(u["resultado"], "falla")
        r = lt.vigilar(self.reg, PROCESOS, escribir=True)
        self.assertIn({"proceso": "registro-diario", "clase": "falla"}, r["alertas"])
        self.assertTrue(any("Ha fallado registro-diario" in a for a in self.avisos()))

    def test_por_la_linea_de_ordenes(self):
        (self.raiz / "politica").mkdir()
        import json
        (self.raiz / "politica" / "procesos.json").write_text(
            json.dumps({"procesos": PROCESOS}), encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--raiz", str(self.raiz), "latido-proceso", "registro-diario",
                                   "--resultado", "falla", "--identidad", "x", "--sin-subir"]), 0)
            self.assertEqual(main(["--raiz", str(self.raiz), "vigilar", "--escribir",
                                   "--sin-traer", "--sin-subir", "--md"]), 0)
        self.assertIn("🔴 falla", out.getvalue())
        self.assertTrue(any("Ha fallado registro-diario" in a for a in self.avisos()))


class Vigilantes(Latidos):
    def test_si_falta_quien_copia_solo_se_avisa_de_el(self):
        self.latir("vigilante-github", h=40)
        r = self.vigilar(40)
        self.assertEqual(self.clase(r, "registro-diario"), "falta")
        self.assertEqual(self.clase(r, "parte"), "sin_datos")
        self.assertEqual([a["proceso"] for a in r["alertas"]], ["registro-diario"])
        self.assertIn("Sin datos de lo que depende de él: parte", self.avisos()[0])

    def test_recien_dado_de_alta_no_avisa_hasta_su_plazo(self):
        # alta a las 00:00: a las 07:00 nadie ha tenido tiempo de latir (el que más corre, 6+2 h)
        r = lt.vigilar(self.reg, PROCESOS, escribir=True, ahora=en(-2))
        self.assertEqual(r["alertas"], [])
        r = lt.vigilar(self.reg, PROCESOS, escribir=True, ahora=en(0))
        self.assertEqual(r["alertas"], [{"proceso": "vigilante-github", "clase": "falta"}])
        self.assertIn("no ha latido nunca", self.avisos()[0])

    def test_centinela_solo_mira_al_vigilante_de_github(self):
        self.latir("vigilante-github", h=0)
        r = lt.centinela(self.reg, PROCESOS, ahora=en(5))
        self.assertEqual(r["alertas"], [])
        r = lt.centinela(self.reg, PROCESOS, ahora=en(9))  # 9 h > 6 + 2
        self.assertEqual(r["alertas"], [{"proceso": "vigilante-github", "clase": "falta"}])
        self.assertEqual(self.reg.ver("proceso", "vigilante-github")["alerta"]["vigilante"],
                         "centinela-pc")
        self.assertIn("vigilante-github", self.avisos()[0])

    def test_importar_copia_sin_repetir(self):
        llamadas = []

        def falso(conf):
            llamadas.append(conf["workflow"])
            return {"resultado": "falla", "ejecutado": "2026-11-23T18:33:17Z",
                    "detalle": "run 7: failure", "clave": 7}

        r = lt.importar(self.reg, PROCESOS, lectores={"github": falso}, ahora=en(10))
        self.assertEqual(r["copiados"], ["parte"])
        r = lt.importar(self.reg, PROCESOS, lectores={"github": falso}, ahora=en(11))
        self.assertEqual((r["copiados"], r["iguales"]), ([], 1))
        u = self.reg.ver("proceso", "parte")["ultimo"]
        self.assertEqual((u["resultado"], u["origen"]), ("falla", "github"))

    def test_importar_no_se_cae_si_una_fuente_falla(self):
        def roto(conf):
            raise OSError("gh no responde")

        r = lt.importar(self.reg, PROCESOS, lectores={"github": roto})
        self.assertEqual(r["copiados"], [])
        self.assertIn("gh no responde", r["errores"][0])

    def test_fechas_de_las_tres_fuentes(self):
        for s in ("2026-11-23T18:33:17Z", "2026-11-23T02:00:01.0000000Z",
                  "2026-11-23T03:00:01+01:00", "2026-11-23T02:00:01.123456Z"):
            self.assertEqual(lt._iso(s).strftime("%Y-%m-%d %H"), "2026-11-23 " +
                             ("18" if s.startswith("2026-11-23T18") else "02"))


if __name__ == "__main__":
    unittest.main()
