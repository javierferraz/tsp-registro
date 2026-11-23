"""Tests del planificador por reglas (PAA2 P5-1, S-1272).

Los dos criterios de la sesión:
- con una cola de prueba, el orden es el esperado y explicable (Orden*);
- corriendo en dos sitios a la vez no se reparten la misma tarea (DosSitios).
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from org import Rechazo, Registro, planificador as pl, recursos
from org.planificador import recurso_tarea as recursos_tarea
from org.__main__ import main

from test_org import Base

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


def en(minutos: float) -> datetime:
    return T0 + timedelta(minutes=minutos)


def notas(imp=3, urg=3, riesgo=3, esf=3, coste=3, prob=3, comp=3, motivo="prueba"):
    v = dict(importancia=imp, urgencia=urg, riesgo=riesgo, esfuerzo=esf, coste=coste,
             probabilidad=prob, comprobable=comp)
    return {k: {"n": n, "motivo": motivo} for k, n in v.items()}


class Cola(Base):
    def nueva(self, reg=None, tid="T-1", estado="lista", valoracion=None, minuto=0, **datos):
        """Crea una tarea y la lleva a `estado` (propuesta, aprobada o lista)."""
        reg = reg or self.reg
        e = ("tarea", tid)
        d = {"titulo": tid, "repo": "2606-thestromboliproject", "tipo_trabajo": "codigo",
             "tamano": "S", **datos}
        reg.escribir("tarea.propuesta", e, "direccion", "dir", f"{tid}:p", d,
                     version=2, ahora=en(minuto))
        if valoracion:
            self.valorar(tid, valoracion, reg, minuto)
        if estado in ("aprobada", "lista"):
            reg.escribir("tarea.aprobada", e, "javier", "j", f"{tid}:a", {}, ahora=en(minuto))
        if estado == "lista":
            reg.escribir("tarea.lista", e, "planificador", "plan", f"{tid}:l", {},
                         ahora=en(minuto))

    def valorar(self, tid, v, reg=None, minuto=0):
        (reg or self.reg).escribir("tarea.valorada", ("tarea", tid), "direccion", "dir",
                                   f"{tid}:v:{minuto}", v,
                                   ahora=en(minuto))

    def plan(self, **kw):
        kw.setdefault("ahora", en(60))
        return pl.planificar(self.reg, **kw)

    def ids(self, plan, clave="orden"):
        return [p["tarea"] for p in plan[clave]]


class Orden(Cola):
    def test_cola_de_prueba_orden_esperado_y_explicable(self):
        # B: la mejor cuenta; A: peor cuenta pero más antigua; C: igual que A y más nueva.
        self.nueva(tid="A", valoracion={"notas": notas(imp=3)}, minuto=0)
        self.nueva(tid="B", valoracion={"notas": notas(imp=5, urg=5, esf=1, coste=1)}, minuto=1)
        self.nueva(tid="C", valoracion={"notas": notas(imp=3)}, minuto=2)
        # D: clase prioritaria con la peor cuenta posible: aun así, primera.
        self.nueva(tid="D", minuto=3, valoracion={
            "notas": notas(imp=1, urg=1, riesgo=1, prob=1, comp=1, esf=5, coste=5),
            "clase": "enlace_roto"})
        p = self.plan(capacidad=2)
        self.assertEqual(self.ids(p), ["D", "B", "A", "C"])
        self.assertEqual([x["posicion"] for x in p["orden"]], [1, 2, 3, 4])
        # explicable: cada puesto dice por qué, con el valor y el coste
        d, b = p["orden"][0], p["orden"][1]
        self.assertIn("clase prioritaria «enlace_roto»", d["por_que"])
        self.assertIn("valor 19 entre coste 2 = 9.5", b["por_que"])
        self.assertEqual(self.ids(p, "arrancar"), ["D", "B"])  # la capacidad es 2

    def test_la_cuenta_es_valor_entre_coste(self):
        self.assertEqual(pl.puntuar(notas(imp=5, urg=5, riesgo=5, prob=5, comp=5,
                                          esf=1, coste=1)), 12.5)
        self.assertEqual(pl.puntuar(notas()), 2.5)  # 15 entre 6

    def test_dentro_de_las_clases_la_fijada_a_mano_y_luego_la_cuenta(self):
        self.nueva(tid="A", minuto=0, valoracion={"notas": notas(), "clase": "integridad"})
        self.nueva(tid="B", minuto=1, valoracion={
            "notas": notas(imp=1), "clase": "no_abre", "fija": {"valor": 50, "motivo": "lo dice Javier"}})
        self.nueva(tid="C", minuto=2, valoracion={"notas": notas(imp=5)})
        self.assertEqual(self.ids(self.plan()), ["B", "A", "C"])
        self.assertIn("fijada a mano en 50 (lo dice Javier)", self.plan()["orden"][0]["por_que"])

    def test_las_cuatro_clases_pasan_delante_de_una_valoracion_perfecta(self):
        perfecta = {"notas": notas(5, 5, 5, 1, 1, 5, 5)}
        self.nueva(tid="Z", valoracion=perfecta, minuto=0)
        for i, clase in enumerate(("no_abre", "enlace_roto", "integridad", "queja_repetida")):
            self.nueva(tid=f"K{i}", minuto=i + 1,
                       valoracion={"notas": notas(1, 1, 1, 5, 5, 1, 1), "clase": clase})
        self.assertEqual(self.ids(self.plan())[-1], "Z")

    def test_empate_se_resuelve_por_antiguedad(self):
        for i, tid in enumerate(("C", "A", "B")):
            self.nueva(tid=tid, minuto=i, valoracion={"notas": notas()})
        self.assertEqual(self.ids(self.plan()), ["C", "A", "B"])

    def test_sin_valoracion_usa_la_tarjeta_y_lo_dice(self):
        self.nueva(tid="T-9")
        self.reg.escribir("tarjeta.importada", ("tarjeta", "T-9"), "importador", "imp", "T-9:i", {
            "estado": "adelante", "revision": 1, "creada": "2026-11-20T10:00:00Z",
            "actualizada": "2026-11-20T10:00:00Z", "huella_texto": "x" * 16, "notas": 0,
            "etiquetas": ["fallo", "seguridad"], "prioridad": "alta", "check": "maquina"},
            ahora=en(0))
        p = self.plan()["orden"][0]
        self.assertTrue(p["sin_valorar"])
        self.assertIn("sin valoración de la dirección", p["por_que"])
        self.assertEqual(p["notas"]["importancia"]["n"], 5)
        self.assertEqual(p["notas"]["urgencia"]["n"], 4)
        self.assertEqual(p["notas"]["riesgo"]["n"], 4)
        self.assertIn("prioridad del tablero «alta»", p["notas"]["importancia"]["motivo"])

    def test_una_valoracion_posterior_sustituye_a_la_anterior(self):
        self.nueva(tid="A", valoracion={"notas": notas(imp=1)})
        self.nueva(tid="B", valoracion={"notas": notas(imp=3)}, minuto=1)
        self.valorar("A", {"notas": notas(imp=5)}, minuto=2)
        self.assertEqual(self.ids(self.plan()), ["A", "B"])


class NoArranca(Cola):
    def motivo(self, plan, tid):
        return next(n["motivo"] for n in plan["no_arranca"] if n["tarea"] == tid)

    def test_consolida_misma_huella_y_deja_la_mas_antigua(self):
        v = {"notas": notas(), "huella": "silent-frog:ajustes:texto-cortado"}
        for i, tid in enumerate(("C", "A", "B")):
            self.nueva(tid=tid, valoracion=v, minuto=i)
        self.nueva(tid="X", minuto=5, valoracion={"notas": notas()})
        p = self.plan(capacidad=5)
        self.assertEqual(self.ids(p), ["C", "X"])
        self.assertEqual(p["consolidadas"], [{"huella": "silent-frog:ajustes:texto-cortado",
                                              "principal": "C", "otras": ["A", "B"]}])
        self.assertIn("consolidada en ella", self.motivo(p, "A"))

    def test_dependencia_abierta_cerrada_y_desconocida(self):
        self.nueva(tid="D1")  # abierta (lista)
        self.nueva(tid="N", dependencias=["D1"], minuto=1)
        self.nueva(tid="U", dependencias=["NO-EXISTE"], minuto=1)
        p = self.plan()
        self.assertEqual(self.motivo(p, "N"), "espera a D1")
        self.assertEqual(self.motivo(p, "U"), "espera a NO-EXISTE")
        for tipo, papel, d in (("tarea.asignada", "planificador", {"ejecutor": "w"}),
                               ("tarea.entregada", "trabajador", {"resumen": "ok"}),
                               ("tarea.verificada", "validador", {}),
                               ("tarea.integrada", "validador", {}),
                               ("tarea.cerrada", "validador", {})):
            self.reg.escribir(tipo, ("tarea", "D1"), papel, f"i-{papel}-{tipo}", f"D1:{tipo}", d,
                              ahora=en(2))
        self.assertIn("N", self.ids(self.plan(), "arrancar"))

    def test_recurso_ocupado_por_otro(self):
        self.nueva(tid="R", valoracion={"notas": notas(), "recursos": ["ftp"]})
        recursos.reservar(self.reg, "ftp", "otro-trabajador", ahora=en(55), subir=False)
        self.assertEqual(self.motivo(self.plan(), "R"), "recurso ocupado: ftp")

    def test_pausa_activa_no_arranca_nada(self):
        self.nueva(tid="A")
        self.reg.escribir("pausa.activada", ("pausa", "tg-1"), "javier", "telegram", "p1",
                          {"nivel": "suave", "origen": "telegram"}, ahora=en(1))
        p = self.plan()
        self.assertEqual(p["arrancar"], [])
        self.assertIn("pausa activa", self.motivo(p, "A"))

    def test_lo_que_acabara_esperando_a_javier_solo_se_frena_pasado_el_tope(self):
        self.nueva(tid="E", valoracion={"notas": notas(), "espera_javier": True})
        self.nueva(tid="P", minuto=1, publica=True, valoracion={"notas": notas()})
        self.nueva(tid="N", minuto=2, valoracion={"notas": notas()})
        self.assertEqual(self.ids(self.plan(), "arrancar"), ["E", "P", "N"])
        for i in range(3):  # tres bloqueadas esperando una decisión suya
            self.nueva(tid=f"B{i}", minuto=10 + i)
            self.reg.escribir("tarea.asignada", ("tarea", f"B{i}"), "planificador", "plan",
                              f"B{i}:as", {"ejecutor": "w"}, ahora=en(20))
            self.reg.escribir("tarea.bloqueada", ("tarea", f"B{i}"), "trabajador", "w",
                              f"B{i}:b", {"motivo": "decide Javier"}, ahora=en(21))
        p = self.plan(tope_espera=3)
        self.assertTrue(p["espera_javier"]["frena"])
        self.assertEqual(self.ids(p, "arrancar"), ["N"])  # lo que no espera a Javier sigue
        self.assertIn("acabará esperando a Javier y ya le esperan 3", self.motivo(p, "E"))
        self.assertIn("acabará esperando a Javier", self.motivo(p, "P"))

    def test_tope_de_en_curso_y_capacidad(self):
        for i in range(3):
            self.nueva(tid=f"W{i}", minuto=i)
            self.reg.escribir("tarea.asignada", ("tarea", f"W{i}"), "planificador", "plan",
                              f"W{i}:as", {"ejecutor": "w"}, ahora=en(5))
        for i in range(4):
            self.nueva(tid=f"L{i}", minuto=10 + i)
        p = self.plan(capacidad=10, max_en_curso=5, ahora=en(15))
        self.assertEqual(len(p["arrancar"]), 2)  # 5 - 3 en curso
        self.assertTrue(self.motivo(p, "L3").startswith("no cabe"))
        self.assertEqual(self.plan(capacidad=0)["arrancar"], [])  # no hacer nada es válido

    def test_no_empieza_a_la_vez_lo_que_toca_los_mismos_ficheros(self):
        self.nueva(tid="A", alcance=["org/estado.py", "docs/"], valoracion={"notas": notas(imp=5)})
        self.nueva(tid="B", minuto=1, alcance=["docs/guia/x.md"], valoracion={"notas": notas()})
        self.nueva(tid="C", minuto=2, alcance=["tests/"], valoracion={"notas": notas()})
        p = self.plan(capacidad=5)
        self.assertEqual(self.ids(p, "arrancar"), ["A", "C"])
        self.assertEqual(self.motivo(p, "B"), "toca los mismos ficheros que A")

    def test_el_planificador_de_github_solo_asigna_documentacion_y_contenido(self):
        self.nueva(tid="K", tipo_trabajo="codigo")
        self.nueva(tid="D", tipo_trabajo="documentacion", minuto=1)
        p = self.plan(sitio="github")
        self.assertEqual(self.ids(p, "arrancar"), ["D"])
        self.assertIn("solo asigna", self.motivo(p, "K"))

    def test_modelo_sonnet_si_el_que_esta_decidido_y_opus_si_hay_que_decidir(self):
        self.nueva(tid="S", tipo_trabajo="contenido", tamano="S")
        self.nueva(tid="O", tipo_trabajo="codigo", tamano="S", minuto=1)
        self.nueva(tid="F", tipo_trabajo="codigo", tamano="S", modelo="sonnet", minuto=2)
        m = {a["tarea"]: a["modelo"] for a in self.plan(capacidad=5)["arrancar"]}
        self.assertEqual(m, {"S": "sonnet", "O": "opus", "F": "sonnet"})

    def test_en_lista_mas_de_30_dias_sale_a_revisar(self):
        self.nueva(tid="V")
        self.nueva(tid="N", minuto=60 * 24 * 29)
        p = pl.planificar(self.reg, ahora=T0 + timedelta(days=31))
        self.assertEqual(p["a_revisar"], ["V"])


class Escritura(Cola):
    def asignada(self, tid, identidad, minuto):
        self.reg.escribir("tarea.asignada", ("tarea", tid), "planificador", identidad,
                          f"{tid}:as:{identidad}", {"ejecutor": "w1"}, ahora=en(minuto))

    def test_caduca_la_de_un_trabajador_y_vuelve_a_lista(self):
        self.nueva(tid="A")
        self.asignada("A", "planificador-pc", 1)
        p = self.plan(ahora=en(30))  # 29 min sin latido
        self.assertEqual(p["caducar"], ["A"])
        self.assertEqual(self.ids(p, "arrancar"), ["A"])
        r = pl.ejecutar(self.reg, p, subir=False, ahora=en(30))
        self.assertEqual((r["caducadas"], r["a_lista"]), (["A"], ["A"]))
        self.assertEqual(self.reg.ver("tarea", "A")["estado"], "lista")

    def test_no_caduca_la_que_puso_en_curso_el_puente_del_tablero(self):
        self.nueva(tid="A")
        self.asignada("A", "puente-tablero", 1)
        p = self.plan(ahora=en(300))
        self.assertEqual(p["caducar"], [])
        self.assertNotIn("A", self.ids(p))
        pl.ejecutar(self.reg, p, subir=False, ahora=en(300))
        self.assertEqual(self.reg.ver("tarea", "A")["estado"], "en_curso")

    def test_escribir_pasa_a_lista_y_no_asigna(self):
        self.nueva(tid="A", estado="aprobada")
        p = self.plan()
        r = pl.ejecutar(self.reg, p, subir=False, ahora=en(61))
        self.assertEqual(r["a_lista"], ["A"])
        self.assertEqual(r["asignadas"], [])
        self.assertEqual(self.reg.ver("tarea", "A")["estado"], "lista")
        pl.ejecutar(self.reg, self.plan(ahora=en(62)), subir=False, ahora=en(62))  # idempotente
        self.assertEqual(self.reg.rechazados(), [])

    def test_asignar_pide_ejecutor_y_deja_modelo_y_reserva(self):
        self.nueva(tid="A")
        p = self.plan()
        with self.assertRaises(Rechazo):
            pl.ejecutar(self.reg, p, asignar=True, subir=False)
        r = pl.ejecutar(self.reg, p, asignar=True, ejecutor="lion", maquina="pc", subir=False,
                        ahora=en(61))
        a = self.reg.ver("tarea", "A")
        self.assertEqual(a["estado"], "en_curso")
        self.assertEqual(a["ejecucion"]["ejecutor"], "lion")
        self.assertEqual(a["ejecucion"]["modelo"], "opus")
        self.assertEqual(r["asignadas"][0]["tarea"], "A")
        self.assertEqual(recursos.titular(self.reg, recursos_tarea("A"), en(62))["titular"],
                         "planificador")

    def test_con_pausa_no_escribe_nada(self):
        self.nueva(tid="A", estado="aprobada")
        self.reg.escribir("pausa.activada", ("pausa", "p"), "javier", "tg", "p",
                          {"nivel": "suave", "origen": "telegram"}, ahora=en(1))
        antes = len(self.ficheros())
        r = pl.ejecutar(self.reg, self.plan(), asignar=True, ejecutor="lion", subir=False)
        self.assertEqual((r["a_lista"], r["asignadas"]), ([], []))
        self.assertEqual(len(self.ficheros()), antes)


class Esquema(Cola):
    def test_solo_direccion_javier_y_motor_valoran(self):
        self.nueva(tid="A")
        for papel in ("trabajador", "planificador", "validador"):
            with self.assertRaises(Rechazo):
                self.reg.escribir("tarea.valorada", ("tarea", "A"), papel, "x", f"v-{papel}",
                                  {"notas": notas()})

    def test_notas_de_1_a_5_siete_y_con_motivo(self):
        self.nueva(tid="A")
        malos = [{"notas": notas(imp=6)}, {"notas": notas(imp=0)},
                 {"notas": {k: v for k, v in notas().items() if k != "coste"}},
                 {"notas": notas(motivo="")}, {"notas": notas(), "clase": "otra"},
                 {"notas": notas(), "huella": "Con Mayúsculas"},
                 {"notas": notas(), "fija": {"valor": 0, "motivo": "x"}}]
        for i, d in enumerate(malos):
            with self.assertRaises(Rechazo, msg=str(d)[:60]):
                self.reg.escribir("tarea.valorada", ("tarea", "A"), "direccion", "d", f"m{i}", d)

    def test_no_se_valora_una_tarea_ya_en_curso(self):
        self.nueva(tid="A")
        self.reg.escribir("tarea.asignada", ("tarea", "A"), "planificador", "p", "A:as",
                          {"ejecutor": "w"})
        with self.assertRaises(Exception):
            self.reg.escribir("tarea.valorada", ("tarea", "A"), "direccion", "d", "A:v",
                              {"notas": notas()})


class Linea(Cola):
    def test_linea_de_ordenes(self):
        self.nueva(tid="A", valoracion={"notas": notas(), "clase": "no_abre"})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            c = main(["--raiz", str(self.raiz), "planificar", "--sin-traer", "--capacidad", "1"])
        self.assertEqual(c, 0, err.getvalue())
        j = json.loads(out.getvalue())
        self.assertEqual(j["plan"]["arrancar"], [{"tarea": "A", "modelo": "opus"}])
        self.assertIsNone(j["escrito"])  # sin --escribir ni --asignar solo mira
        self.assertEqual(self.reg.ver("tarea", "A")["estado"], "lista")
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            c = main(["--raiz", str(self.raiz), "planificar", "--sin-traer", "--md",
                      "--asignar", "--ejecutor", "lion", "--sin-subir"])
        self.assertEqual(c, 0, err.getvalue())
        self.assertIn("clase prioritaria «no_abre»", out.getvalue())
        self.assertEqual(self.reg.ver("tarea", "A")["estado"], "en_curso")


    def test_guardar_deja_el_plan_para_compararlo_despues(self):
        self.nueva(tid="A")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            c = main(["--raiz", str(self.raiz), "planificar", "--sin-traer", "--guardar",
                      "--sin-subir", "--sitio", "pc"])
        self.assertEqual(c, 0)
        f = list((self.raiz / "informes" / "planificador").glob("*-pc.md"))
        self.assertEqual(len(f), 1)
        self.assertIn("**A**", f[0].read_text(encoding="utf-8"))


@unittest.skipUnless(shutil.which("git"), "sin git")
class DosSitios(Cola):
    """Dos clones de un mismo remoto: el planificador del PC (A, self.reg) y el de GitHub (B)."""

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

    def asignadas(self, reg):
        reg.traer()
        return {i: t["ejecucion"]["ejecutor"] for i, t in reg.entidades("tarea").items()
                if t["estado"] == "en_curso"}

    def test_dos_planificadores_con_la_misma_cola_no_se_reparten_la_misma_tarea(self):
        for i in range(4):
            self.nueva(tid=f"T{i}", minuto=i, valoracion={"notas": notas()})
        self.reg.subir()
        self.b.traer()
        # Los dos miran lo mismo, antes de que ninguno escriba: ven la misma cola y el mismo plan.
        pa = pl.planificar(self.reg, ahora=en(60), capacidad=2, identidad="plan-pc")
        pb = pl.planificar(self.b, ahora=en(60), capacidad=2, identidad="plan-gh")
        self.assertEqual(self.ids(pa, "arrancar"), self.ids(pb, "arrancar"))  # el choque es real
        self.assertEqual(self.ids(pa, "arrancar"), ["T0", "T1"])
        ra = pl.ejecutar(self.reg, pa, identidad="plan-pc", asignar=True, ejecutor="lion",
                         ahora=en(61))
        rb = pl.ejecutar(self.b, pb, identidad="plan-gh", asignar=True, ejecutor="github",
                         ahora=en(62))
        a, b = {x["tarea"] for x in ra["asignadas"]}, {x["tarea"] for x in rb["asignadas"]}
        self.assertEqual(a, {"T0", "T1"})
        self.assertEqual(a & b, set())              # ninguna tarea dos veces
        self.assertEqual(b, {"T2", "T3"})           # y B siguió con las siguientes
        self.assertTrue(all(p["motivo"].startswith("reserva ocupado") or
                            p["motivo"].startswith("ya está") for p in rb["perdidas"]))
        for reg in (self.reg, self.b):              # las dos máquinas ven lo mismo y sin choques
            ver = self.asignadas(reg)
            self.assertEqual(ver, {"T0": "lion", "T1": "lion", "T2": "github", "T3": "github"})
            self.assertEqual(reg.rechazados(), [])

    def test_la_carrera_de_verdad_gana_la_que_entro_antes_y_la_otra_se_retira(self):
        """B pide la reserva de la tarea antes (fecha anterior) pero sube después: gana A."""
        self.nueva(tid="T0", valoracion={"notas": notas()})
        self.reg.subir()
        self.b.traer()
        pb = recursos.pedir(self.b, recursos_tarea("T0"), "plan-gh", "planificador", 5, ahora=en(60))
        pa = recursos.pedir(self.reg, recursos_tarea("T0"), "plan-pc", "planificador", 5, ahora=en(61))
        a = recursos.confirmar(self.reg, pa["reserva"], "plan-pc", "planificador", ahora=en(61))
        b = recursos.confirmar(self.b, pb["reserva"], "plan-gh", "planificador", ahora=en(62))
        self.assertEqual((a["ok"], b["ok"]), (True, False))
        # B (retirada) no asigna; solo A lo hace, y la tarea queda con un único ejecutor.
        self.reg.escribir("tarea.asignada", ("tarea", "T0"), "planificador", "plan-pc",
                          "plan:T0:asignada:1", {"ejecutor": "lion"}, ahora=en(63))
        self.reg.subir()
        self.assertEqual(self.asignadas(self.b), {"T0": "lion"})
        self.assertEqual(self.b.rechazados(), [])

    def test_si_la_otra_ya_la_asigno_y_solto_no_se_vuelve_a_asignar(self):
        self.nueva(tid="T0", valoracion={"notas": notas()})
        self.reg.subir()
        self.b.traer()
        pb = pl.planificar(self.b, ahora=en(60), capacidad=1, identidad="plan-gh")  # plan viejo
        ra = pl.ejecutar(self.reg, pl.planificar(self.reg, ahora=en(60), capacidad=1,
                                                 identidad="plan-pc"),
                         identidad="plan-pc", asignar=True, ejecutor="lion", ahora=en(61))
        for r in recursos.vivas(self.reg, recursos_tarea("T0"), en(61)):
            recursos.liberar(self.reg, r["id"], "plan-pc", "planificador", ahora=en(62))
        rb = pl.ejecutar(self.b, pb, identidad="plan-gh", asignar=True, ejecutor="github",
                         ahora=en(63))
        self.assertEqual(len(ra["asignadas"]), 1)
        self.assertEqual(rb["asignadas"], [])
        self.assertEqual(rb["perdidas"][0]["motivo"], "ya está en_curso")
        self.assertEqual(self.asignadas(self.reg), {"T0": "lion"})


if __name__ == "__main__":
    unittest.main()
