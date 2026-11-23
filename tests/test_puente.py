"""Tests del puente tablero ⇄ registro y de la migración por etapas (PAA2 P4-3, S-1271).

Lo que tiene que cumplirse:
- un movimiento del tablero se traduce al camino legal del ciclo, en orden y con quién lo hizo
  (Traduccion*); Empezar desde el navegador es la aprobación «sin firma»;
- Terminada no la escribe nadie más que el validador: mover ahí lo lanza (Terminada*), y un
  agente que cierra sin Por validar es una discrepancia que devuelve la tarjeta a su sitio;
- el registro va por delante → la tarjeta se mueve sola (Proyeccion*), y la vuelta atrás
  (exportar) proyecta todo sin escribir nada en el registro;
- una etapa se supera con un ciclo limpio, una discrepancia lo reinicia y la siguiente empieza
  sola (Etapas*);
- el validador en modo tablero: commits ya en main, o cierre de Javier en el navegador si no
  hay commits (ValidadorTablero*).
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from org import Registro
from org import puente as pt
from org import validador as val
from org.__main__ import main

from test_validador import App, git

CENTRAL = pt.CENTRAL
CONF_ETAPAS = [{"repos": [CENTRAL]}, {"repos": ["2606-clever-cat"]}, {"repos": ["*"]}]
ESCRITORES = ["navegador", "vigia-fichas"]


def iso(segundos: float = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=segundos)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def nota(cid, n, de, a, quien="claude", segundos=5):
    humano = quien == "javier"
    return {"id": f"{cid}-note-{n}", "at": iso(segundos), "by": quien, "kind": "transition",
            "from": de, "to": a, "text": "texto libre que no debe llegar al registro",
            "actor": {"kind": "human" if humano else "agent", "id": quien}}


def tarjeta(cid, col, app="", notas=(), commits=()):
    return {"id": cid, "col": col, "app": app, "title": "Título privado", "body": "cuerpo",
            "notes": list(notas),
            "evidence": {"commits": [{"sha": c} for c in commits]} if commits else {}}


def repo_de(card):
    return {"": CENTRAL, "Clever Cat": "2606-clever-cat"}.get(card.get("app") or "", "otro")


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg = Registro(self.tmp / "registro")
        pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES, {})  # etapa 1: el central

    def tearDown(self):
        self._tmp.cleanup()

    def pasada(self, *cards, exportar=False):
        plan = pt.planear(self.reg, {"cards": list(cards)}, repo_de,
                          pt.repos_gestionados(self.reg), exportar=exportar)
        etapa = next((e for e in pt.etapas(self.reg) if e["estado"] == "en_ciclo"), None)
        res = pt.aplicar(self.reg, plan, etapa)
        self.assertEqual(res["rechazados"], [], res)
        return plan

    def estado(self, cid):
        return (self.reg.ver("tarea", cid) or {}).get("estado")

    def tipos(self, cid):
        return [h["tipo"] for h in self.reg.ver("tarea", cid)["historial"]]


class Caminos(unittest.TestCase):
    def test_caminos_mas_cortos_con_los_papeles_del_puente(self):
        self.assertEqual([p[0] for p in pt.camino("propuesta", "en_curso")],
                         ["tarea.aprobada", "tarea.lista", "tarea.asignada"])
        self.assertEqual([p[0] for p in pt.camino("entregada", "en_curso")],
                         ["tarea.bloqueada", "tarea.lista", "tarea.asignada"])
        self.assertEqual(pt.camino("cerrada", "propuesta")[0][0], "tarea.reabierta")
        self.assertEqual(pt.camino("en_curso", "en_curso"), [])

    def test_cerrar_no_tiene_camino_sin_el_validador(self):
        self.assertIsNone(pt.camino("entregada", "cerrada"))
        self.assertIsNone(pt.camino("en_curso", "cerrada"))


class Traduccion(Base):
    def test_alta_de_una_tarjeta_abierta_sin_texto_libre(self):
        self.pasada(tarjeta("S-1", "validar", commits=["abc123def4567"]))
        t = self.reg.ver("tarea", "S-1")
        self.assertEqual(t["estado"], "entregada")
        self.assertEqual((t["titulo"], t["repo"], t["tipo_trabajo"]), ("S-1", CENTRAL, "tablero"))
        self.assertEqual(t["entrega"]["commits"], ["abc123def456"])
        todo = json.dumps(t, ensure_ascii=False)
        self.assertNotIn("privado", todo)
        self.assertNotIn("texto libre", todo)

    def test_empezar_desde_el_navegador_es_aprobacion_sin_firma(self):
        self.pasada(tarjeta("S-2", "propuesta"))
        self.pasada(tarjeta("S-2", "curso", notas=[
            nota("S-2", 1, "propuesta", "adelante", "javier", 5),
            nota("S-2", 2, "adelante", "curso", "claude", 6)]))
        t = self.reg.ver("tarea", "S-2")
        self.assertEqual(t["estado"], "en_curso")
        apr = [h for h in t["historial"] if h["tipo"] == "tarea.aprobada"][0]
        self.assertEqual(apr["autor"]["papel"], "planificador")
        self.assertEqual(apr["motivo"], "Javier en el tablero (sin firma)")
        self.assertEqual(t["ejecucion"]["ejecutor"], "claude")

    def test_repetir_la_pasada_no_escribe_nada(self):
        c = tarjeta("S-3", "curso")
        self.pasada(c)
        n = len([e for e in self.reg.eventos_de("tarea") if e["entidad"]["id"] == "S-3"])
        plan = self.pasada(c)
        self.assertEqual((plan["eventos"], plan["proyecciones"]), ([], []))
        self.assertEqual(len([e for e in self.reg.eventos_de("tarea") if e["entidad"]["id"] == "S-3"]), n)

    def test_pregunta_sobre_una_propuesta_no_la_aprueba(self):
        self.pasada(tarjeta("S-4", "pedida"))
        plan = self.pasada(tarjeta("S-4", "info", notas=[nota("S-4", 1, "pedida", "info")]))
        self.assertEqual(plan["eventos"], [])
        self.assertEqual(self.estado("S-4"), "propuesta")

    def test_reabrir_una_terminada(self):
        self.pasada(tarjeta("S-5", "descartada", notas=[nota("S-5", 1, "propuesta",
                                                             "descartada", segundos=-60)]))
        self.assertIsNone(self.estado("S-5"))  # cerrada antes de migrar: histórico
        self.pasada(tarjeta("S-6", "propuesta"))
        self.pasada(tarjeta("S-6", "descartada", notas=[nota("S-6", 1, "propuesta",
                                                             "descartada", "javier", 5)]))
        self.assertEqual(self.estado("S-6"), "descartada")
        self.pasada(tarjeta("S-6", "curso", notas=[nota("S-6", 1, "propuesta", "descartada",
                                                        "javier", 5),
                                                   nota("S-6", 2, "descartada", "curso",
                                                        "javier", 8)]))
        self.assertEqual(self.estado("S-6"), "en_curso")
        self.assertIn("tarea.reabierta", self.tipos("S-6"))

    def test_lo_que_no_esta_migrado_no_se_toca(self):
        plan = self.pasada(tarjeta("S-7", "curso", app="Clever Cat"))
        self.assertEqual(plan["eventos"], [])
        self.assertIsNone(self.estado("S-7"))


class Terminada(Base):
    def test_javier_a_terminada_lanza_el_validador_y_espera_en_por_validar(self):
        self.pasada(tarjeta("S-10", "validar", commits=["abc"]))
        plan = self.pasada(tarjeta("S-10", "hecha", notas=[
            nota("S-10", 1, "validar", "hecha", "javier")]))
        self.assertEqual(plan["despachos"], ["S-10"])
        self.assertEqual(plan["discrepancias"], [])
        self.assertEqual(plan["proyecciones"][0]["a"], "validar")
        self.assertIn("validador", plan["proyecciones"][0]["nota"])
        self.assertEqual(self.estado("S-10"), "entregada")

    def test_javier_cierra_desde_en_curso_pasa_por_entregada(self):
        self.pasada(tarjeta("S-11", "curso"))
        plan = self.pasada(tarjeta("S-11", "hecha", notas=[
            nota("S-11", 1, "curso", "hecha", "javier")]))
        self.assertEqual(plan["despachos"], ["S-11"])
        self.assertEqual(self.estado("S-11"), "entregada")

    def test_un_agente_que_cierra_sin_validar_es_discrepancia(self):
        self.pasada(tarjeta("S-12", "curso"))
        plan = self.pasada(tarjeta("S-12", "hecha", notas=[
            nota("S-12", 1, "curso", "hecha", "claude")]))
        self.assertEqual(plan["despachos"], [])
        self.assertEqual(plan["discrepancias"][0]["clase"], "terminada sin pasar por Por validar")
        self.assertEqual(plan["proyecciones"][0]["a"], "curso")
        self.assertEqual(self.estado("S-12"), "en_curso")
        etapa = pt.etapas(self.reg)[0]
        self.assertEqual(len(etapa["discrepancias"]), 1)
        self.assertEqual(etapa["discrepancias"][0]["tarjeta"], "S-12")


class Proyeccion(Base):
    def test_el_validador_cierra_y_la_tarjeta_va_a_terminada(self):
        self.pasada(tarjeta("S-20", "validar", commits=["abc"]))
        for tipo, d in (("tarea.verificada", {}), ("tarea.integrada", {}),
                        ("tarea.cerrada", {})):
            self.reg.escribir(tipo, ("tarea", "S-20"), "validador", "validador-prueba",
                              f"S-20:{tipo}", d)
        plan = self.pasada(tarjeta("S-20", "validar", commits=["abc"]))
        self.assertEqual(plan["eventos"], [])
        self.assertEqual((plan["proyecciones"][0]["a"], plan["proyecciones"][0]["nota"]),
                         ("hecha", "Cerrada por el validador (el registro manda)."))

    def test_vuelta_atras_proyecta_todo_sin_escribir(self):
        self.pasada(tarjeta("S-21", "curso"), tarjeta("S-22", "validar"))
        n = len(list((self.tmp / "registro" / "eventos").rglob("*.json")))
        plan = pt.planear(self.reg, {"cards": [
            tarjeta("S-21", "propuesta", notas=[nota("S-21", 1, "curso", "propuesta")]),
            tarjeta("S-22", "validar"), tarjeta("S-23", "curso")]}, repo_de,
            pt.repos_gestionados(self.reg), exportar=True)
        self.assertEqual(plan["eventos"], [])
        self.assertEqual([(p["tarjeta"], p["a"]) for p in plan["proyecciones"]],
                         [("S-21", "curso")])
        self.assertEqual(len(list((self.tmp / "registro" / "eventos").rglob("*.json"))), n)


class Etapas(Base):
    def test_ciclo_limpio_supera_la_etapa_y_empieza_la_siguiente(self):
        r = pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES, {"navegador": [iso(5)]})
        self.assertEqual((r["estado"], r["ciclo"]["faltan"]), ("en_ciclo", ["vigia-fichas"]))
        r = pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES,
                       {"navegador": [iso(5)], "vigia-fichas": [iso(6)]})
        self.assertEqual((r["estado"], r["siguiente"]), ("superada", 2))
        self.assertEqual(pt.repos_gestionados(self.reg), {CENTRAL, "2606-clever-cat"})

    def test_una_discrepancia_reinicia_el_ciclo(self):
        self.pasada(tarjeta("S-30", "curso"))
        self.pasada(tarjeta("S-30", "hecha", notas=[nota("S-30", 1, "curso", "hecha")]))
        disc = pt.etapas(self.reg)[0]["discrepancias"][0]["fecha"]
        r = pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES,
                       {"navegador": [iso(-60)], "vigia-fichas": [iso(-60)]})
        self.assertEqual(r["estado"], "en_ciclo")
        self.assertEqual(r["ciclo"]["desde"], disc)  # el ciclo cuenta desde la discrepancia
        self.assertEqual(sorted(r["ciclo"]["faltan"]), ESCRITORES)

    def test_tras_la_ultima_etapa_termina_y_revertir_apaga(self):
        for _ in range(3):
            pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES,
                       {e: [iso(30)] for e in ESCRITORES})
        self.assertEqual(pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES, {})["estado"],
                         "terminada")
        self.reg.escribir("migracion.revertida", ("migracion", "tablero-3"), "planificador",
                          "p", "rev", {"motivo": "prueba"})
        self.assertEqual(pt.avanzar(self.reg, CONF_ETAPAS, ESCRITORES, {})["estado"],
                         "revertida")
        self.assertNotIn("*", pt.repos_gestionados(self.reg))


class Orden(unittest.TestCase):
    def test_org_puente_de_principio_a_fin(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "central" / "tablero").mkdir(parents=True)
            board = d / "central" / "tablero" / "board.json"
            conf = d / "central" / "tablero" / "migracion.json"
            board.write_text(json.dumps({"cards": [tarjeta("S-40", "curso"),
                                                   tarjeta("S-41", "curso", app="X Y")]}),
                             encoding="utf-8")
            conf.write_text(json.dumps({"activa": True, "etapas": CONF_ETAPAS,
                                        "escritores": {e: {} for e in ESCRITORES}}),
                            encoding="utf-8")
            base = ["--raiz", str(d / "registro"), "puente", "--tablero", str(board),
                    "--config", str(conf), "--sin-subir"]
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(base + ["--ensayo"]), 0)
            self.assertEqual(json.loads(out.getvalue())["plan"]["eventos"], [])  # sin etapa
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(base), 0)
            r = json.loads(out.getvalue())
            self.assertEqual(r["inicio"]["estado"], "iniciada")
            self.assertEqual(r["plan"]["creadas"], ["S-40"])
            self.assertEqual(Registro(d / "registro").ver("tarea", "S-40")["estado"], "en_curso")


class ValidadorTablero(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg = Registro(self.tmp / "registro")
        self.app = App(self.tmp / "central")
        git(self.app.dir, "checkout", "-q", "main")

    def tearDown(self):
        self._tmp.cleanup()

    def entregar(self, commits=()):
        T = ("tarea", "S-50")
        for tipo, papel, datos in [
                ("tarea.propuesta", "direccion",
                 {"titulo": "S-50", "repo": CENTRAL, "tipo_trabajo": "tablero"}),
                ("tarea.aprobada", "planificador", {}), ("tarea.lista", "planificador", {}),
                ("tarea.asignada", "planificador", {"ejecutor": "claude"}),
                ("tarea.entregada", "trabajador",
                 {"resumen": "S-50 a Por validar",
                  **({"commits": list(commits)} if commits else {})})]:
            self.reg.escribir(tipo, T, papel, pt.IDENTIDAD, f"S-50:{tipo}", datos)

    def validar(self):
        return val.validar(self.reg, "S-50", "validador-prueba", fuente=str(self.app.dir),
                           evidencias=self.tmp / "ev", subir=False)

    def test_commits_ya_en_main_se_verifican_integran_y_cierran(self):
        self.app.poner({"doc.md": "hecho\n"})
        c = self.app.commit("trabajo fusionado")
        self.entregar([c[:12]])
        r = self.validar()
        self.assertEqual(r["veredicto"], "verificada", r["fallos"])
        self.assertEqual(r["politica"], "validador/tablero@1")
        t = self.reg.ver("tarea", "S-50")
        self.assertEqual(t["estado"], "cerrada")
        self.assertIn("pruebas", " ".join(t["veredicto"].get("no_verificado", [])))

    def test_commit_sin_fusionar_se_devuelve(self):
        git(self.app.dir, "checkout", "-q", "trabajo")
        self.app.poner({"doc.md": "a medias\n"})
        c = self.app.commit("sin fusionar")
        git(self.app.dir, "checkout", "-q", "main")
        self.entregar([c])
        r = self.validar()
        self.assertEqual(r["veredicto"], "devuelta")
        self.assertIn("falta fusionarlo", r["motivo"])

    def test_sin_commits_vale_el_cierre_de_javier_en_el_navegador(self):
        self.entregar()
        self.app.poner({"tablero/board.json": json.dumps({"events": [
            {"type": "card.transition", "cardId": "S-50", "summary": "validar -> hecha",
             "actor": {"kind": "human", "id": "javier"}, "at": iso(5)}]})})
        self.app.commit("tablero")
        r = self.validar()
        self.assertEqual(r["veredicto"], "verificada", r)
        self.assertEqual(self.reg.ver("tarea", "S-50")["estado"], "cerrada")

    def test_sin_commits_ni_cierre_de_javier_se_bloquea(self):
        self.entregar()
        self.app.poner({"tablero/board.json": json.dumps({"events": [
            {"type": "card.transition", "cardId": "S-50", "summary": "validar -> hecha",
             "actor": {"kind": "agent", "id": "claude"}, "at": iso(5)}]})})
        self.app.commit("tablero")
        r = self.validar()
        self.assertEqual(r["veredicto"], "bloqueada")
        self.assertEqual(self.reg.ver("tarea", "S-50")["estado"], "bloqueada")


if __name__ == "__main__":
    unittest.main()
