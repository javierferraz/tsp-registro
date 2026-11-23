"""Tests del motor de eventos (PAA2 P5-2, S-1273).

Criterio de la sesión: «una regla que entra en bucle se corta sola» (Bucles*). Además, cada una de
las 5 reglas hace lo suyo, no repite y lleva su contador de utilidad.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from org import motor as mt
from org.motor import Accion, Regla

from test_org import CICLO, Base

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def en(minutos: float) -> datetime:
    return T0 + timedelta(minutes=minutos)


class Motor(Base):
    def tarea(self, tid="T-0001", hasta="tarea.asignada", **datos):
        """Lleva una tarea por el ciclo hasta `hasta` (incluido)."""
        for tipo, papel, d in CICLO:
            if tipo == "tarea.propuesta":
                d = {**d, **datos}
            self.reg.escribir(tipo, ("tarea", tid), papel, f"p-{papel}", f"{tid}:{tipo}", d,
                              ahora=en(0))
            if tipo == hasta:
                return

    def correr(self, minuto=60, **kw):
        kw.setdefault("escribir", True)
        return mt.correr(self.reg, ahora=en(minuto), **kw)

    def disparos(self, regla=None):
        return [e for e in self.reg.eventos_de("regla")
                if e["tipo"] == "regla.disparada" and regla in (None, e["entidad"]["id"])]


class Reglas(Motor):
    def test_entrega_de_interfaz_sin_medida_crea_la_tarea_de_medida(self):
        self.tarea(hasta="tarea.entregada", tipo_trabajo="interfaz")
        r = self.correr()
        self.assertEqual([d["regla"] for d in r["disparos"]], ["medida-visual"])
        nueva = self.reg.ver("tarea", "T-0001-medida-1")
        self.assertEqual(nueva["estado"], "propuesta")
        self.assertEqual(nueva["tipo_trabajo"], "interfaz")
        # la acción va encadenada al evento que la originó
        ev = [e for e in self.reg.eventos_de("tarea")
              if e["entidad"]["id"] == "T-0001-medida-1"][0]
        entrega = [e for e in self.reg.eventos_de("tarea") if e["tipo"] == "tarea.entregada"][0]
        self.assertEqual(ev["correlacion"], entrega["id"])
        self.assertEqual(ev["autor"]["papel"], "motor")
        # no repite: otra vuelta no hace nada
        self.assertEqual(self.correr(minuto=120)["disparos"], [])
        self.assertEqual(len(self.disparos()), 1)

    def test_entrega_sin_interfaz_o_ya_medida_no_hace_nada(self):
        self.tarea(hasta="tarea.entregada")  # código
        self.tarea(tid="T-0002", hasta="tarea.entregada", tipo_trabajo="interfaz")
        self.reg.escribir("evidencia.verificada", ("tarea", "T-0002"), "laboratorio", "lab",
                          "T-0002:ev", {"tipo": "medida visual", "ruta": "a.png", "hash": "x"},
                          ahora=en(1))
        self.assertEqual(self.correr()["disparos"], [])

    def test_tres_devoluciones_bloquea_y_avisa(self):
        self.tarea(hasta="tarea.asignada")
        e = ("tarea", "T-0001")
        for i in range(3):
            self.reg.escribir("tarea.entregada", e, "trabajador", "lion", f"e{i}",
                              {"resumen": "x"}, ahora=en(i * 10 + 1))
            self.reg.escribir("tarea.devuelta", e, "validador", "val", f"d{i}",
                              {"motivo": "no"}, ahora=en(i * 10 + 2))
            if i < 2:
                self.reg.escribir("tarea.lista", e, "planificador", "plan", f"l{i}", {},
                                  ahora=en(i * 10 + 3))
                self.reg.escribir("tarea.asignada", e, "planificador", "plan", f"a{i}",
                                  {"ejecutor": "lion"}, ahora=en(i * 10 + 4))
        r = self.correr()
        self.assertEqual([d["regla"] for d in r["disparos"]], ["tres-devoluciones"])
        self.assertEqual(self.reg.ver("tarea", "T-0001")["estado"], "bloqueada")
        self.assertIn("bloqueada", r["avisos"][0])

    def test_ejecucion_que_supera_su_presupuesto_se_pausa(self):
        self.tarea(hasta="tarea.asignada", presupuesto={"minutos": 30})
        self.reg.escribir("tarea.latido", ("tarea", "T-0001"), "trabajador", "lion", "l1", {},
                          ahora=en(20))
        self.assertEqual(self.correr(minuto=25)["disparos"], [])  # aún dentro
        self.reg.escribir("tarea.latido", ("tarea", "T-0001"), "trabajador", "lion", "l2", {},
                          ahora=en(45))
        r = self.correr(minuto=50)
        self.assertEqual([d["regla"] for d in r["disparos"]], ["presupuesto"])
        t = self.reg.ver("tarea", "T-0001")
        self.assertEqual(t["estado"], "en_pausa")
        self.assertIn("45 min de 30", t["historial"][-1]["motivo"])

    def test_integridad_pausa_su_app(self):
        self.reg.escribir("incidencia.abierta", ("incidencia", "I-1"), "validador", "val", "i1",
                          {"origen": "integridad", "gravedad": "alta", "descripcion": "x",
                           "app": "2608-silent-frog"}, ahora=en(0))
        r = self.correr()
        self.assertEqual([d["regla"] for d in r["disparos"]], ["integridad"])
        p = self.reg.ver("pausa", "motor-I-1")
        self.assertEqual((p["estado"], p["nivel"], p["ambito"]), ("activa", "suave", "app:silent-frog"))

    def test_alerta_de_latido_abre_incidencia(self):
        self.reg.escribir("proceso.alerta", ("proceso", "tsp-parte-diario"), "motor", "vig",
                          "a1", {"clase": "falta", "esperado_horas": 48}, ahora=en(0))
        self.correr()
        inc = list(self.reg.entidades("incidencia").values())
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["huella"], "proceso:tsp-parte-diario:falta")
        self.assertEqual(inc[0]["origen"], "latido")

    def test_sin_escribir_no_toca_nada(self):
        self.tarea(hasta="tarea.entregada", tipo_trabajo="interfaz")
        antes = self.ficheros()
        r = self.correr(escribir=False)
        self.assertEqual(len(r["disparos"]), 1)
        self.assertEqual(self.ficheros(), antes)

    def test_contador_de_utilidad(self):
        self.tarea(hasta="tarea.entregada", tipo_trabajo="interfaz")
        self.tarea(tid="T-0002", hasta="tarea.entregada", tipo_trabajo="interfaz")
        self.correr()
        self.reg.escribir("tarea.descartada", ("tarea", "T-0002-medida-1"), "javier", "j",
                          "desc", {"motivo": "no hacía falta"}, ahora=en(70))
        u = {x["regla"]: x for x in mt.utilidad(self.reg)}
        self.assertEqual(len(u), 5)
        m = u["medida-visual"]
        self.assertEqual((m["disparos"], m["tareas_creadas"], m["sirvieron"], m["inutiles"],
                          m["pendientes"]), (2, 2, 0, 1, 1))
        self.assertEqual(u["presupuesto"]["disparos"], 0)


def _rebote(reg, ev):
    """Regla mala de prueba: cada incidencia abre otra (un bucle)."""
    n = int(ev["entidad"]["id"].split("-")[1])
    return [Accion("incidencia.abierta", ("incidencia", f"I-{n + 1}"),
                   {"origen": "bucle", "gravedad": "baja", "descripcion": "rebote"})]


def _eco(reg, ev):
    """Regla de prueba: cada latido de proceso abre una incidencia sobre ese proceso."""
    return [Accion("incidencia.abierta", ("incidencia", f"eco-{ev['entidad']['id']}-{ev['id'][:6]}"),
                   {"origen": "eco", "gravedad": "baja", "descripcion": "eco"})]


def _toca_siempre_lo_mismo(reg, ev):
    return [Accion("proceso.alerta", ("proceso", "siempre-el-mismo"),
                   {"clase": "falta", "esperado_horas": 1})]


class Bucles(Motor):
    def test_regla_en_bucle_se_corta_en_el_quinto_eslabon_y_avisa(self):
        mala = Regla("rebote", 1, "prueba", ("incidencia.abierta",), "bucle", _rebote)
        self.reg.escribir("incidencia.abierta", ("incidencia", "I-1"), "javier", "j", "i1",
                          {"origen": "a mano", "gravedad": "baja", "descripcion": "origen"},
                          ahora=en(0))
        r = self.correr(reglas=(mala,))
        # I-1 (eslabón 1) → I-2 … → I-5 (eslabón 5); la sexta no se crea
        self.assertEqual(sorted(self.reg.entidades("incidencia")), [f"I-{i}" for i in range(1, 6)])
        self.assertEqual([p["limite"] for p in r["paradas"]], ["cadena"])
        self.assertEqual(self.reg.ver("regla", "rebote")["estado"], "parada")
        self.assertTrue(any("parada" in a for a in r["avisos"]))
        log = (self.raiz / ".indice" / "avisos.log").read_text(encoding="utf-8")
        self.assertIn("Regla «rebote» parada", log)
        # parada, ya no dispara aunque haya eventos nuevos
        self.reg.escribir("incidencia.abierta", ("incidencia", "I-100"), "javier", "j", "i100",
                          {"origen": "a mano", "gravedad": "baja", "descripcion": "otra"},
                          ahora=en(70))
        r = self.correr(minuto=80, reglas=(mala,))
        self.assertEqual(r["disparos"], [])
        self.assertIsNone(self.reg.ver("incidencia", "I-101"))
        # solo Javier (o dirección) la reactiva
        self.reg.escribir("regla.reactivada", ("regla", "rebote"), "javier", "j", "re",
                          {"motivo": "arreglada"}, ahora=en(90))
        self.assertEqual(self.reg.ver("regla", "rebote")["estado"], "activa")
        # reactivada, lo de antes no la vuelve a parar ni repite el aviso
        avisos = len((self.raiz / ".indice" / "avisos.log").read_text(encoding="utf-8").splitlines())
        r = self.correr(minuto=100, reglas=(mala,))
        self.assertEqual((r["paradas"], r["avisos"]), ([], []))
        self.assertEqual(self.reg.ver("regla", "rebote")["estado"], "activa")
        self.assertEqual(len((self.raiz / ".indice" / "avisos.log").read_text(
            encoding="utf-8").splitlines()), avisos)

    def test_tope_de_disparos_por_dia(self):
        eco = Regla("eco", 1, "prueba", ("proceso.latido",), "eco", _eco)
        for i in range(mt.TOPE_DIA + 2):
            self.reg.escribir("proceso.latido", ("proceso", f"p{i}"), "motor", "m", f"l{i}",
                              {"resultado": "ok", "ejecutado": "2026-11-23T09:00:00.000000Z",
                               "origen": "propio"}, ahora=en(i))
        r = self.correr(reglas=(eco,))
        self.assertEqual(len(r["disparos"]), mt.TOPE_DIA)
        self.assertEqual([p["limite"] for p in r["paradas"]], ["tope_dia"])
        self.assertEqual(self.reg.ver("regla", "eco")["estado"], "parada")

    def test_misma_entidad_dos_veces_en_24_horas(self):
        pesada = Regla("pesada", 1, "prueba", ("proceso.latido",), "pesada",
                       _toca_siempre_lo_mismo)
        for i in range(2):
            self.reg.escribir("proceso.latido", ("proceso", "x"), "motor", "m", f"l{i}",
                              {"resultado": "ok", "ejecutado": "2026-11-23T09:00:00.000000Z",
                               "origen": "propio"}, ahora=en(i))
        r = self.correr(reglas=(pesada,))
        self.assertEqual(len(r["disparos"]), 1)
        self.assertEqual([p["limite"] for p in r["paradas"]], ["entidad"])

    def test_dos_motores_no_disparan_dos_veces_lo_mismo(self):
        self.tarea(hasta="tarea.entregada", tipo_trabajo="interfaz")
        self.correr()
        self.correr(minuto=61)
        self.assertEqual(len(self.disparos("medida-visual")), 1)


if __name__ == "__main__":
    unittest.main()
