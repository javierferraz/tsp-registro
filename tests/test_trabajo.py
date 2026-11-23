"""Tests del contrato de trabajo ampliado y de la reserva de recursos (PAA2 P4-1, S-1269).

Los dos criterios de la sesión:
- dos trabajadores que reclaman el mismo recurso: uno gana y el otro se retira
  (Reservas*, sobre todo ReservasEntreMaquinas.test_gana_la_que_entra_antes…);
- una tarea cortada a mitad se recupera desde su último punto de control
  (Relevo.test_tarea_cortada_se_retoma_desde_su_punto_de_control y su versión entre máquinas).
"""
from __future__ import annotations

import io
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from org import Conflicto, Rechazo, Registro, recursos, trabajo
from org.__main__ import main

from test_org import CICLO, TAREA, Base

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
INFORME = {
    "resumen": "hecho",
    "criterios": [{"n": 1, "estado": "ok", "evidencia": "tests.log"},
                  {"n": 2, "estado": "no_ejecutado", "motivo": "sin pantalla"}],
    "ficheros": ["org/a.py"], "commits": ["abc123"],
    "decisiones": [{"clase": "A", "elegida": "sqlite", "descartada": "json",
                    "motivo": "stdlib"}],
    "descubierto": [], "coste": {"minutos": 40, "tokens": 120000},
}


def en(minutos: float) -> datetime:
    return T0 + timedelta(minutes=minutos)


class Contrato(Base):
    def en_curso(self, ejecutor="w1", reg=None, tid="T-0001"):
        reg = reg or self.reg
        for tipo, papel, datos in CICLO[:4]:
            if tipo == "tarea.asignada":
                datos = {**datos, "ejecutor": ejecutor}
            reg.escribir(tipo, ("tarea", tid), papel, f"p-{papel}", f"{tid}:{tipo}", datos,
                         ahora=T0 - timedelta(seconds=1))  # asignada justo antes de T0

    def entregar(self, informe, quien="w1"):
        return self.reg.escribir("tarea.entregada", ("tarea", "T-0001"), "trabajador", quien,
                                 "T-0001:entregada", informe, version=2, ahora=en(5))


class Informe(Contrato):
    def test_informe_v2_completo(self):
        self.en_curso()
        self.entregar(INFORME)
        t = self.reg.ver("tarea", "T-0001")
        self.assertEqual(t["estado"], "entregada")
        self.assertEqual(t["entrega"]["coste"], {"minutos": 40, "tokens": 120000})
        self.assertEqual(t["ficheros"], ["org/a.py"])

    def test_informe_v2_sin_campos_libres_ni_huecos(self):
        self.en_curso()
        casos = [
            ({**INFORME, "notas": "x"}, "campo no previsto"),
            ({k: v for k, v in INFORME.items() if k != "coste"}, "coste: obligatorio"),
            ({**INFORME, "criterios": [{"n": 1, "estado": "ok"}]}, "sin evidencia"),
            ({**INFORME, "criterios": [{"n": 1, "estado": "falla"}]}, "sin motivo"),
            ({**INFORME, "criterios": [{"n": 1, "estado": "hecho", "evidencia": "x"}]},
             "no es uno de"),
            ({**INFORME, "criterios": [INFORME["criterios"][0]] * 2}, "sale dos veces"),
            ({**INFORME, "criterios": [{"n": 1, "estado": "ok", "evidencia": "x", "a": 1}]},
             r"criterios\[0\]\.a: campo no previsto"),
            ({**INFORME, "decisiones": [{"clase": "C", "elegida": "x", "motivo": "y"}]},
             "clase C"),
            ({**INFORME, "decisiones": [{"clase": "A", "elegida": "x"}]},
             r"decisiones\[0\]\.motivo: obligatorio"),
            ({**INFORME, "ficheros": ["a", 3]}, r"ficheros\[1\]: tiene que ser str"),
            ({**INFORME, "coste": {"minutos": "40"}}, "coste.minutos: tiene que ser entero"),
        ]
        for datos, motivo in casos:
            with self.subTest(motivo=motivo), self.assertRaisesRegex(Rechazo, motivo):
                self.entregar(datos)
        self.assertEqual(self.reg.ver("tarea", "T-0001")["estado"], "en_curso")

    def test_la_v1_sigue_admitida(self):
        self.en_curso()
        self.reg.escribir("tarea.punto_control", ("tarea", "T-0001"), "trabajador", "otro",
                          "pc1", {"resumen": "mitad"}, ahora=en(1))
        self.reg.escribir("tarea.entregada", ("tarea", "T-0001"), "trabajador", "otro",
                          "ent1", {"resumen": "hecho"}, ahora=en(2))
        self.assertEqual(self.reg.ver("tarea", "T-0001")["estado"], "entregada")


class Latido(Contrato):
    def test_un_latido_por_minuto_son_tres_eventos_en_media_hora(self):
        self.en_curso()  # la asignación cuenta como primer latido (T0)
        escritos = [trabajo.latido(self.reg, "T-0001", "w1", [f"f{m}.py"], ahora=en(m),
                                   subir=False)["escrito"] for m in range(1, 31)]
        self.assertEqual([m for m, e in zip(range(1, 31), escritos) if e], [10, 20, 30])
        lat = [e for e in self.reg.eventos_de("tarea") if e["tipo"] == "tarea.latido"]
        self.assertEqual(len(lat), 3)
        # Los ficheros acumulados entre latidos salen en el siguiente, sin perder ninguno.
        self.assertEqual(lat[0]["datos"]["ficheros"], sorted(f"f{m}.py" for m in range(1, 11)))
        self.assertEqual(self.reg.ver("tarea", "T-0001")["ficheros"],
                         sorted(f"f{m}.py" for m in range(1, 31)))

    def test_cinco_trabajadores_un_dia_entero_quedan_cerca_de_500(self):
        """El presupuesto (02 §3.1 ter): 7 tareas latiendo cada 5 min durante 14 h. Sin agrupar
        serían 1.176 latidos; agrupados, 6 por hora y tarea (588), y en cuanto el día pasa de
        500 eventos, uno cada 12 min (cada 15 al latir cada 5): se queda en unos 570. El tope
        es blando a propósito (README, D-24)."""
        for i in range(7):
            self.en_curso(ejecutor=f"w{i}", tid=f"T-{i}")
        for m in range(5, 14 * 60 + 1, 5):
            for i in range(7):
                trabajo.latido(self.reg, f"T-{i}", f"w{i}", ahora=en(m), subir=False)
        v = trabajo.volumen(self.reg, T0)
        self.assertTrue(v["superado"])
        self.assertLess(v["latidos"], 7 * 14 * 6)
        self.assertLess(v["hoy"], 580)
        print(f"\n  [volumen] 7 tareas x 14 h: {v['latidos']} latidos, {v['hoy']} eventos",
              end=" ")

    def saturado(self):
        viejo = trabajo.PRESUPUESTO_DIARIO
        trabajo.PRESUPUESTO_DIARIO = 3
        self.addCleanup(setattr, trabajo, "PRESUPUESTO_DIARIO", viejo)

    def test_dia_saturado_espacia_a_12_minutos(self):
        self.en_curso()
        self.saturado()
        r = [trabajo.latido(self.reg, "T-0001", "w1", ahora=en(m), subir=False)
             for m in (10, 11, 13)]
        self.assertTrue(r[0]["saturado"])
        self.assertEqual([x["escrito"] for x in r], [False, False, True])

    def test_quien_late_cada_5_minutos_nunca_caduca(self):
        """Revisión en frío: con el día saturado, un trabajador que late a la cadencia del
        contrato (≤ 5 min, con algo de retraso) no llega nunca a los 20 min sin latido."""
        self.en_curso()
        self.saturado()
        m = 0.0
        while m < 180:
            m += 7  # cada 5 min con 2 de retraso; el planificador mira justo antes del latido
            self.assertEqual(trabajo.caducar(self.reg, "plan", ahora=en(m)), [], f"min {m}")
            trabajo.latido(self.reg, "T-0001", "w1", ahora=en(m), subir=False)
        self.assertEqual(trabajo.contrato(self.reg, "T-0001")["latir_cada_min"], 5)

    def test_latido_de_tarea_que_no_esta_en_curso(self):
        self.esc(*CICLO[0])
        with self.assertRaisesRegex(Rechazo, "no está en curso"):
            trabajo.latido(self.reg, "T-0001", "w1", subir=False)


class Relevo(Contrato):
    def test_tarea_cortada_se_retoma_desde_su_punto_de_control(self):
        self.en_curso("w1")
        recursos.reservar(self.reg, "repo:silent-frog:main", "w1", tarea="T-0001",
                          ahora=en(1), subir=False)
        trabajo.latido(self.reg, "T-0001", "w1", ["a.py"], ahora=en(2), subir=False)
        trabajo.punto_control(self.reg, "T-0001", "w1", "criterios 1 y 2", "criterio 3",
                              ["b.py"], rama="s1", commit="c0ffee", a_medias=["b.py"],
                              ahora=en(3), subir=False)
        # w1 se corta aquí. A los 20 min sin latido, el planificador la caduca.
        self.assertEqual(trabajo.caducar(self.reg, "plan", ahora=en(22)), [])
        cad = trabajo.caducar(self.reg, "plan", ahora=en(24))
        self.assertEqual([c["tarea"] for c in cad], ["T-0001"])
        self.assertEqual(trabajo.caducar(self.reg, "plan", ahora=en(25)), [])  # una vez
        self.reg.escribir("tarea.lista", ("tarea", "T-0001"), "planificador", "plan",
                          "T-0001:lista:2", {"motivo": "relevo"}, ahora=en(25))
        self.reg.escribir("tarea.asignada", ("tarea", "T-0001"), "planificador", "plan",
                          "T-0001:asignada:2", {"ejecutor": "w2", "rama": "s1"}, ahora=en(26))

        c = trabajo.contrato(self.reg, "T-0001", ahora=en(26))
        self.assertEqual(c["ejecucion"]["ejecutor"], "w2")
        self.assertEqual(c["relevo"]["ejecuciones_anteriores"], 1)
        pc = c["relevo"]["punto_control"]
        self.assertEqual((pc["hecho"], pc["sigue"], pc["autor"], pc["commit"]),
                         ("criterios 1 y 2", "criterio 3", "w1", "c0ffee"))
        self.assertEqual(pc["ficheros"], ["a.py", "b.py"])  # incluye el del latido
        self.assertEqual(c["relevo"]["rama"], "s1")
        self.assertEqual(c["recursos"][0]["recurso"], "repo:silent-frog:main")
        self.assertIn("criterios", c["informe_v2"])

        # w1 vuelve tarde: ya no puede escribir sobre la tarea.
        with self.assertRaisesRegex(Conflicto, "la lleva w2"):
            trabajo.punto_control(self.reg, "T-0001", "w1", "x", "y", ahora=en(27),
                                  subir=False)
        trabajo.latido(self.reg, "T-0001", "w2", ["c.py"], ahora=en(40), subir=False)
        self.entregar_como("w2")
        t = self.reg.ver("tarea", "T-0001")
        self.assertEqual(t["estado"], "entregada")
        self.assertEqual(t["ficheros"], ["a.py", "b.py", "c.py", "org/a.py"])

    def entregar_como(self, quien):
        self.reg.escribir("tarea.entregada", ("tarea", "T-0001"), "trabajador", quien,
                          "T-0001:entregada", INFORME, version=2, ahora=en(45))


class Reservas(Base):
    R = "manifest"

    def res(self, quien, m=0, **kw):
        return recursos.reservar(self.reg, self.R, quien, ahora=en(m), subir=False, **kw)

    def test_misma_maquina_el_segundo_queda_fuera_sin_escribir(self):
        a = self.res("w1")
        n = len(self.ficheros())
        b = self.res("w2", 1)
        self.assertTrue(a["ok"])
        self.assertEqual((b["ok"], b["estado"], b["titular"]), (False, "ocupado", "w1"))
        self.assertEqual(len(self.ficheros()), n)
        self.assertEqual(self.res("w1", 2)["reserva"], a["reserva"])  # repetir: la misma

    def test_caduca_y_no_se_resucita(self):
        a = self.res("w1", minutos=30)
        self.assertFalse(self.res("w2", 29)["ok"])
        b = self.res("w2", 31)
        self.assertTrue(b["ok"])
        with self.assertRaisesRegex(Conflicto, "caducó"):
            recursos.renovar(self.reg, a["reserva"], "w1", ahora=en(32), subir=False)
        self.assertEqual(recursos.titular(self.reg, self.R, en(32))["titular"], "w2")

    def test_renovar_y_liberar(self):
        a = self.res("w1", minutos=30)
        recursos.renovar(self.reg, a["reserva"], "w1", minutos=60, ahora=en(20), subir=False)
        self.assertFalse(self.res("w2", 45)["ok"])
        recursos.liberar(self.reg, a["reserva"], "w1", ahora=en(46), subir=False)
        self.assertTrue(self.res("w2", 47)["ok"])

    def test_otro_trabajador_no_suelta_una_reserva_ajena(self):
        a = self.res("w1")
        with self.assertRaisesRegex(Conflicto, "es de w1"):
            recursos.liberar(self.reg, a["reserva"], "w2", ahora=en(1), subir=False)
        recursos.liberar(self.reg, a["reserva"], "plan", "planificador", ahora=en(2),
                         subir=False)  # el planificador sí

    def test_nombre_y_plazo(self):
        for recurso, minutos, motivo in (("Repo Frog", 30, "minúsculas"),
                                         ("ftp", 0, "de 1 a"), ("ftp", 2000, "de 1 a")):
            with self.subTest(recurso=recurso, minutos=minutos), \
                    self.assertRaisesRegex(Rechazo, motivo):
                recursos.reservar(self.reg, recurso, "w1", minutos=minutos, subir=False)

    def test_linea_de_ordenes(self):
        def org(*args):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return main(["--raiz", str(self.raiz), *args])
        self.assertEqual(org("reservar", "ftp", "--identidad", "w1", "--sin-subir"), 0)
        self.assertEqual(org("reservar", "ftp", "--identidad", "w2", "--sin-subir"), 3)
        self.assertEqual(org("recursos"), 0)
        self.assertEqual(org("reservar", "ftp", "--sin-subir"), 1)  # sin identidad


@unittest.skipUnless(shutil.which("git"), "sin git")
class EntreMaquinas(Contrato):
    """Dos clones de un mismo remoto: A (self.reg) y B (self.b)."""

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.raiz), *args], check=True,
                              capture_output=True, text=True)

    def setUp(self):
        super().setUp()
        remoto = Path(self._tmp.name) / "remoto.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
        self.git("init", "-q", "-b", "main")
        (self.raiz / ".gitignore").write_text(".indice/\nestado/\n", encoding="utf-8")
        self.git("add", ".gitignore")
        self.git("-c", "user.email=o@p", "-c", "user.name=o", "commit", "-q", "-m", "inicio")
        self.git("remote", "add", "origin", str(remoto))
        self.git("push", "-q", "-u", "origin", "main")
        otra = Path(self._tmp.name) / "otra"
        subprocess.run(["git", "clone", "-q", str(remoto), str(otra)], check=True)
        for raiz in (self.raiz, otra):
            self.git("config", "user.email", "org@prueba", cwd=raiz)
            self.git("config", "user.name", "org", cwd=raiz)
            self.git("config", "pull.rebase", "true", cwd=raiz)
        self.b = Registro(otra)
        self.remoto = remoto

    def commits_remotos(self):
        return int(self.git("rev-list", "--count", "main", cwd=self.remoto).stdout)

    def test_gana_la_que_entra_antes_aunque_tenga_fecha_posterior(self):
        """B pide primero (su fecha es anterior) pero sube después: gana A, que subió antes,
        y B se retira. Con la fecha como criterio, los dos creerían haber ganado."""
        pb = recursos.pedir(self.b, "firma", "wB", ahora=en(0))
        pa = recursos.pedir(self.reg, "firma", "wA", ahora=en(1))
        a = recursos.confirmar(self.reg, pa["reserva"], "wA", ahora=en(1))
        b = recursos.confirmar(self.b, pb["reserva"], "wB", ahora=en(2))
        self.assertEqual((a["ok"], a["estado"]), (True, "ganada"))
        self.assertEqual((b["ok"], b["estado"], b["titular"]), (False, "retirada", "wA"))
        self.reg.traer()
        for reg in (self.reg, self.b):  # las dos máquinas ven lo mismo
            self.assertEqual(recursos.titular(reg, "firma", en(3))["titular"], "wA")
            self.assertEqual(reg.ver("reserva", pb["reserva"])["estado"], "retirada")
            self.assertEqual(reg.rechazados(), [])

    def test_quien_llega_tarde_lo_ve_ocupado_sin_escribir(self):
        a = recursos.reservar(self.reg, "ftp", "wA", ahora=en(0))
        antes = self.commits_remotos()
        b = recursos.reservar(self.b, "ftp", "wB", ahora=en(1))
        self.assertTrue(a["ok"])
        self.assertEqual((b["ok"], b["estado"], b["titular"]), (False, "ocupado", "wA"))
        self.assertEqual(self.commits_remotos(), antes)
        recursos.liberar(self.reg, a["reserva"], "wA", ahora=en(2))
        self.assertTrue(recursos.reservar(self.b, "ftp", "wB", ahora=en(3))["ok"])

    def test_relevo_entre_maquinas(self):
        """w1 trabaja en A y se corta; el planificador en B la caduca y se la da a w2 en B,
        que sigue desde el punto de control que subió A."""
        self.en_curso("w1")
        trabajo.punto_control(self.reg, "T-0001", "w1", "mitad", "la otra mitad", ["a.py"],
                              rama="s1", ahora=en(5))  # se sube
        self.b.traer()
        self.assertEqual(trabajo.caducar(self.b, "plan", ahora=en(20)), [])
        self.assertEqual(len(trabajo.caducar(self.b, "plan", ahora=en(26))), 1)
        self.b.escribir("tarea.lista", ("tarea", "T-0001"), "planificador", "plan",
                        "T-0001:lista:2", {}, ahora=en(26))
        self.b.escribir("tarea.asignada", ("tarea", "T-0001"), "planificador", "plan",
                        "T-0001:asignada:2", {"ejecutor": "w2"}, ahora=en(27))
        c = trabajo.contrato(self.b, "T-0001", ahora=en(27))
        self.assertEqual(c["relevo"]["punto_control"]["sigue"], "la otra mitad")
        self.assertEqual(c["relevo"]["rama"], "s1")

    def test_latido_agrupado_sube_solo_cuando_se_escribe(self):
        self.en_curso("w1")
        self.reg.subir()
        antes = self.commits_remotos()
        r1 = trabajo.latido(self.reg, "T-0001", "w1", ["a.py"], ahora=en(3))
        self.assertFalse(r1["escrito"])
        self.assertEqual(self.commits_remotos(), antes)
        r2 = trabajo.latido(self.reg, "T-0001", "w1", ahora=en(11))
        self.assertTrue(r2["escrito"])
        self.assertEqual(r2["subida"], "1 evento(s) subidos")
        self.assertEqual(self.commits_remotos(), antes + 1)
        self.b.traer()
        self.assertEqual(self.b.ver("tarea", "T-0001")["ficheros"], ["a.py"])


if __name__ == "__main__":
    unittest.main()
