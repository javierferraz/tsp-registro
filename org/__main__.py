"""org — el único escritor del registro central.

Uso:  py -m org <orden> ...   (py -m org -h para la lista)
Papel e identidad del autor: --papel/--identidad o las variables ORG_PAPEL/ORG_IDENTIDAD.
Raíz del registro: --raiz o ORG_RAIZ; por defecto, la carpeta de este repo.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .esquemas import TIPOS, Rechazo
from .registro import Registro

RAIZ_REPO = Path(__file__).resolve().parent.parent
CENTRAL = "C:/xampp/htdocs/2606-thestromboliproject"
LION = str(Path(os.environ.get("LOCALAPPDATA", "")) / "RoaringLion" / "tasks")


def _fase2(reg: Registro, a) -> int:
    from datetime import datetime, timedelta, timezone

    from . import cartera as cart
    from .comparar import comparar, informe_md
    from .importar import Central, importar, leer_lion, leer_tablero, leer_vigias

    central = Central(a.central, a.ref)
    if not a.sin_fetch:
        central.actualizar()
    tarjetas = leer_tablero(Path(a.central) / "tablero" / "board.json")
    lion = leer_lion(a.lion)

    def la_cartera():
        manifest = json.loads(central.leer("updates/manifest.json"))
        c = cart.generar(manifest, Path(a.central).parent)
        return {"fichas": c, **cart.comparar_listas(c, cart.leer_listas(central.leer))}

    if a.orden == "cartera":
        _salida(la_cartera())
        return 0
    if a.orden == "comparar":
        hoy = datetime.now(timezone.utc).date()
        comp = comparar(reg, tarjetas, lion, hoy, a.desde)
        if a.md:
            print(informe_md(hoy.isoformat(), None, comp, None), end="")
        else:
            _salida(comp)
        return 0
    imp = importar(reg, tarjetas, lion, leer_vigias(central), a.identidad)
    if a.orden == "importar":
        _salida(imp)
        return 1 if imp["rechazados"] else 0
    # diario
    fecha = a.fecha or datetime.now(timezone.utc).date().isoformat()
    hoy = datetime.strptime(fecha, "%Y-%m-%d").date()
    desde = (hoy - timedelta(days=1)).isoformat()
    comp = comparar(reg, tarjetas, lion, hoy, desde)
    c = la_cartera()
    carpeta = reg.raiz / "informes"
    carpeta.mkdir(exist_ok=True)
    (carpeta / f"{fecha}.md").write_text(informe_md(fecha, imp, comp, c), encoding="utf-8",
                                         newline="\n")
    (carpeta / f"{fecha}.json").write_text(json.dumps(
        {"fecha": fecha, "importado": imp, "comparacion": comp, "cartera": c},
        ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    reg.volcar_estado()
    from .parte import generar
    try:  # el parte (S-1266): estado/parte.html, fuera de git; si falla, el diario sigue
        parte = "parte al día"
        generar(reg)
    except Exception as ex:  # noqa: BLE001
        parte = f"parte sin generar ({type(ex).__name__}: {ex})"
    subida = "sin subir" if a.sin_subir else reg.subir()
    linea = (f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} diario {fecha}: "
             f"{imp['eventos']} evento(s), {len(imp['rechazados'])} rechazado(s); {subida}; "
             f"{parte}")
    log = reg.raiz / ".indice" / "diario.log"
    log.parent.mkdir(exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")
    print(linea)
    return 1 if imp["rechazados"] else 0


def _fase4(reg: Registro, a) -> int:
    from . import recursos, trabajo

    if a.orden in ("recursos", "volumen", "contrato"):
        _salida(recursos.resumen(reg) if a.orden == "recursos" else
                trabajo.volumen(reg) if a.orden == "volumen" else trabajo.contrato(reg, a.tarea))
        return 0
    if not a.identidad:
        raise Rechazo("falta --identidad (o ORG_IDENTIDAD)")
    subir = not a.sin_subir
    if a.orden == "latido":
        r = trabajo.latido(reg, a.tarea, a.identidad, a.ficheros, subir=subir)
    elif a.orden == "punto-control":
        r = trabajo.punto_control(reg, a.tarea, a.identidad, a.hecho, a.sigue, a.ficheros,
                                  a.rama, a.commit, a.a_medias, subir=subir)
    elif a.orden == "caducar":
        r = trabajo.caducar(reg, a.identidad, a.minutos)
        if r and subir:
            r = {"caducadas": r, "subida": trabajo._subir(reg)}
    elif a.orden == "reservar":
        r = recursos.reservar(reg, a.recurso, a.identidad, a.papel, a.minutos, a.tarea,
                              a.maquina, subir=subir)
        _salida(r)
        return 0 if r["ok"] else 3
    elif a.orden == "liberar":
        r = recursos.liberar(reg, a.reserva, a.identidad, a.papel, a.motivo, subir=subir)
    else:  # renovar
        r = recursos.renovar(reg, a.reserva, a.identidad, a.papel, a.minutos, subir=subir)
    _salida(r)
    return 0


def _validador(reg: Registro, a) -> int:
    from . import validador as val

    if a.orden == "gasto":
        _salida(val.gasto(a.repo, a.workflow))
        return 0
    if not a.identidad:
        raise Rechazo("falta --identidad (o ORG_IDENTIDAD)")
    if a.orden == "revisar":
        _salida(val.revisar(reg, a.tarea, a.identidad, a.veredicto, a.cabeza, a.hallazgos,
                            a.ruta, subir=not a.sin_subir))
        return 0
    if a.registrar:
        d = json.loads(Path(a.registrar).read_text(encoding="utf-8"))
        r = val.registrar(reg, d, a.identidad, log=Path(a.log) if a.log else None,
                          subir=not a.sin_subir)
    elif a.autoprueba:
        r = val.autoprueba(RAIZ_REPO, a.identidad)
    elif not a.tarea:
        raise Rechazo("falta la tarea (o --autoprueba, o --registrar)")
    else:
        r = val.validar(reg, a.tarea, a.identidad, fuente=a.fuente,
                        evidencias=Path(a.evidencias) if a.evidencias else None,
                        ensayo=a.ensayo, subir=not a.sin_subir)
    if a.salida:
        Path(a.salida).write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
    _salida(r)
    return {"verificada": 0, "devuelta": 1}.get(r["veredicto"], 3)


def _entidad(texto: str) -> tuple[str, str]:
    tipo, sep, ent_id = texto.partition(":")
    if not sep or not tipo or not ent_id:
        raise argparse.ArgumentTypeError("entidad en la forma tipo:id (p. ej. tarea:S-1264)")
    return tipo, ent_id


def _datos(texto: str | None) -> dict:
    if texto is None:
        return {}
    if texto == "-":
        texto = sys.stdin.read()
    elif texto.startswith("@"):
        texto = Path(texto[1:]).read_text(encoding="utf-8")
    valor = json.loads(texto)
    if not isinstance(valor, dict):
        raise Rechazo("--datos tiene que ser un objeto JSON")
    return valor


def _salida(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _puente(reg: Registro, a) -> int:
    from . import puente as pt
    conf = json.loads(Path(a.config).read_text(encoding="utf-8"))
    tablero = json.loads(Path(a.tablero).read_text(encoding="utf-8"))
    if not conf.get("activa", True) and not a.exportar:
        _salida({"estado": "apagado"})
        return 0
    actividad = json.loads(Path(a.actividad).read_text(encoding="utf-8")) if a.actividad else {}
    manifest = Path(a.config).resolve().parents[1] / "updates" / "manifest.json"
    repo_de = pt.resolver(conf.get("apps", {}), json.loads(manifest.read_text(encoding="utf-8"))
                          if manifest.is_file() else None)
    escritores = list(conf.get("escritores", {}))
    out = {}
    if not a.ensayo and not pt.etapas(reg):
        out["inicio"] = pt.avanzar(reg, conf["etapas"], escritores, actividad)
    plan = pt.planear(reg, tablero, repo_de, pt.repos_gestionados(reg), exportar=a.exportar)
    out["plan"] = plan
    if not a.ensayo:
        etapa = next((e for e in pt.etapas(reg) if e["estado"] == "en_ciclo"), None)
        out["aplicado"] = pt.aplicar(reg, plan, etapa)
        out["avance"] = pt.avanzar(reg, conf["etapas"], escritores, actividad)
        if not a.sin_subir and (out["aplicado"]["escritos"] or "inicio" in out
                                or out["avance"]["estado"] in ("superada", "iniciada")):
            out["subida"] = reg.subir()
    for e in plan["eventos"]:  # el JSON no lleva tuplas
        e["entidad"] = list(e["entidad"])
    _salida(out)
    return 0


def main(argv=None) -> int:
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="org", description="Registro central de TSP (PAA2).")
    p.add_argument("--raiz", default=os.environ.get("ORG_RAIZ", str(RAIZ_REPO)))
    sub = p.add_subparsers(dest="orden", required=True)

    e = sub.add_parser("escribir", help="registra un evento (valida esquema, papel y transición)")
    e.add_argument("tipo", help="p. ej. tarea.propuesta (lista: org tipos)")
    e.add_argument("entidad", type=_entidad, help="tipo:id, p. ej. tarea:T-0001")
    e.add_argument("--clave", required=True,
                   help="clave de idempotencia: repetir el mismo evento con ella no hace nada")
    e.add_argument("--datos", help="objeto JSON, @fichero.json o - (entrada estándar)")
    e.add_argument("--correlacion", help="id del evento que originó este")
    e.add_argument("--version", type=int, default=1, help="versión de esquema (por defecto 1)")
    e.add_argument("--papel", default=os.environ.get("ORG_PAPEL"))
    e.add_argument("--identidad", default=os.environ.get("ORG_IDENTIDAD"))

    v = sub.add_parser("ver", help="estado calculado de una entidad")
    v.add_argument("entidad", type=_entidad)
    sub.add_parser("estado", help="pone al día el índice y escribe estado/")
    sub.add_parser("recalcular", help="borra índice y estado/ y los recalcula desde eventos/")
    sub.add_parser("rechazados", help="ficheros de eventos que no pasan las comprobaciones")
    t = sub.add_parser("tipos", help="tipos de evento y quién puede escribirlos")
    t.add_argument("--papel")
    sub.add_parser("subir", help="commit de los eventos nuevos y push (pull --rebase si choca)")
    sub.add_parser("parte", help="genera estado/parte.html desde el registro, sin importar nada")
    tg = sub.add_parser("telegram", help="ejecuta una pulsación de botón de Telegram (S-1268)")
    tg.add_argument("--accion", help="callback_data del botón (lista cerrada)")
    tg.add_argument("--pulsacion", help="id de la pulsación (callback_query.id): clave única")
    tg.add_argument("--panel", action="store_true", help="solo el estado de la pausa y su botón")
    tg.add_argument("--subir", action="store_true", help="org subir antes de avisar")
    tg.add_argument("--enviar", action="store_true", help="manda la respuesta por TSPBot")

    # Fase 4 (S-1269): contrato de trabajo ampliado y reserva de recursos entre máquinas.
    def quien(s, papel):
        s.add_argument("--papel", default=os.environ.get("ORG_PAPEL", papel))
        s.add_argument("--identidad", default=os.environ.get("ORG_IDENTIDAD"))
        s.add_argument("--sin-subir", action="store_true", help="no hace commit ni push")

    s = sub.add_parser("latido", help="latido de una tarea en curso (agrupado: como mucho un "
                                      "evento cada 10 min; lo que haya pendiente se sube)")
    s.add_argument("tarea")
    s.add_argument("--ficheros", nargs="*", default=[], help="ficheros tocados desde el último")
    quien(s, "trabajador")
    s = sub.add_parser("punto-control", help="qué está hecho, qué sigue y qué está a medias")
    s.add_argument("tarea")
    s.add_argument("--hecho", required=True)
    s.add_argument("--sigue", required=True)
    s.add_argument("--ficheros", nargs="*", default=[])
    s.add_argument("--a-medias", nargs="*", default=[])
    s.add_argument("--rama")
    s.add_argument("--commit")
    quien(s, "trabajador")
    s = sub.add_parser("caducar", help="el planificador: tareas en curso sin latido → caducada")
    s.add_argument("--minutos", type=int, help="sin latido desde hace N min (por defecto 20)")
    quien(s, "planificador")
    s = sub.add_parser("contrato", help="lo que recibe el trabajador (con el relevo, si lo hay)")
    s.add_argument("tarea")
    s = sub.add_parser("reservar", help="reserva un recurso; sale con 3 si no se consigue")
    s.add_argument("recurso", help="p. ej. repo:silent-frog:main, manifest, ftp, firma")
    s.add_argument("--minutos", type=int, default=30)
    s.add_argument("--tarea")
    s.add_argument("--maquina")
    quien(s, "trabajador")
    for nombre, ayuda in (("liberar", "suelta una reserva"),
                          ("renovar", "alarga una reserva viva")):
        s = sub.add_parser(nombre, help=ayuda)
        s.add_argument("reserva", help="R-…")
        if nombre == "renovar":
            s.add_argument("--minutos", type=int, default=30)
        else:
            s.add_argument("--motivo")
        quien(s, "trabajador")
    sub.add_parser("recursos", help="quién tiene cada recurso ahora")
    sub.add_parser("volumen", help="eventos de hoy frente al presupuesto de 500")

    # Validador central (S-1270, PAA2 P4-2): repite las pruebas, lee las evidencias y decide.
    s = sub.add_parser("validar", help="valida una tarea entregada: verificada, devuelta o "
                                       "bloqueada (salida 0, 1 o 3)")
    s.add_argument("tarea", nargs="?")
    s.add_argument("--fuente", help="carpeta o URL del repo de la tarea (por defecto, "
                                    "C:/xampp/htdocs/<repo> o su remoto en GitHub)")
    s.add_argument("--evidencias", help="carpeta para la salida de las pruebas")
    s.add_argument("--ensayo", action="store_true", help="decide sin escribir nada")
    s.add_argument("--autoprueba", action="store_true",
                   help="valida el último commit de este repo en un registro temporal (medir)")
    s.add_argument("--salida", help="guarda la decisión en este JSON (con --ensayo, para "
                                    "registrarla en otro paso con --registrar)")
    s.add_argument("--registrar", metavar="DECISION_JSON",
                   help="escribe el veredicto de una decisión ya tomada, sin ejecutar nada")
    s.add_argument("--log", help="con --registrar: la salida de las pruebas (se comprueba su hash)")
    s.add_argument("--identidad", default=os.environ.get("ORG_IDENTIDAD"))
    s.add_argument("--sin-subir", action="store_true")
    s = sub.add_parser("revisar", help="registra la revisión independiente de una entrega "
                                       "(papel revisor; nunca quien la hizo)")
    s.add_argument("tarea")
    s.add_argument("--veredicto", required=True, choices=("ok", "changes", "reject"))
    s.add_argument("--cabeza", required=True, help="commit revisado (la cabeza entregada)")
    s.add_argument("--hallazgos", type=int)
    s.add_argument("--ruta", help="review.json del revisor (se guarda su hash)")
    s.add_argument("--identidad", default=os.environ.get("ORG_IDENTIDAD"))
    s.add_argument("--sin-subir", action="store_true")
    s = sub.add_parser("gasto", help="minutos de GitHub que ha gastado el validador")
    s.add_argument("--repo", default="javierferraz/tsp-registro")
    s.add_argument("--workflow", default="validador.yml")

    # P4-3 (S-1271): el registro manda en los repos migrados; lo llama tablero/puente.py.
    s = sub.add_parser("puente", help="tablero ⇄ registro: traduce movimientos, proyecta el "
                                      "registro y lleva las etapas de la migración")
    s.add_argument("--tablero", required=True, help="tablero/board.json")
    s.add_argument("--config", required=True, help="tablero/migracion.json del repo central")
    s.add_argument("--actividad", help="JSON {escritor: [fechas ISO]} de quién ha escrito")
    s.add_argument("--exportar", action="store_true",
                   help="vuelta atrás: solo proyecta el registro sobre el tablero")
    s.add_argument("--ensayo", action="store_true", help="dice qué haría, sin escribir nada")
    s.add_argument("--sin-subir", action="store_true")

    # Fase 2 (S-1265): importador, comparación diaria y cartera. Solo leen las fuentes.
    for nombre, ayuda in (
            ("importar", "copia en el registro el tablero, las tareas de Lion y los vigías"),
            ("comparar", "registro frente a tablero, sin escribir nada (JSON o --md)"),
            ("cartera", "cartera desde el manifest y su comparación con las cuatro listas"),
            ("diario", "importar + comparar + cartera; deja informes/AAAA-MM-DD.md y .json "
                       "y sube")):
        s = sub.add_parser(nombre, help=ayuda)
        s.add_argument("--central", default=os.environ.get("ORG_CENTRAL", CENTRAL),
                       help="repo central (el tablero se lee de su disco; vigías, manifest y "
                            "listas, de --ref)")
        s.add_argument("--ref", default="origin/main")
        s.add_argument("--lion", default=os.environ.get("ORG_LION", LION),
                       help="carpeta de tareas de Roaring Lion")
        s.add_argument("--sin-fetch", action="store_true", help="no hace git fetch del central")
        s.add_argument("--identidad", default=os.environ.get("ORG_IDENTIDAD", "importador-pc"))
        if nombre == "comparar":
            s.add_argument("--desde", help="anomalías desde AAAA-MM-DD (por defecto, todas)")
            s.add_argument("--md", action="store_true")
        if nombre == "diario":
            s.add_argument("--sin-subir", action="store_true")
            s.add_argument("--fecha", help="fecha del informe (por defecto, hoy en UTC)")

    a = p.parse_args(argv)
    reg = Registro(a.raiz)
    try:
        if a.orden == "escribir":
            if not a.papel or not a.identidad:
                raise Rechazo("faltan --papel y --identidad (o ORG_PAPEL / ORG_IDENTIDAD)")
            r = reg.escribir(a.tipo, a.entidad, a.papel, a.identidad, a.clave,
                             _datos(a.datos), a.correlacion, a.version)
            _salida({"nuevo": r.nuevo, "id": r.id, "fichero": r.fichero})
        elif a.orden == "ver":
            ent = reg.ver(*a.entidad)
            if ent is None:
                print(f"no existe {a.entidad[0]}:{a.entidad[1]}", file=sys.stderr)
                return 1
            _salida(ent)
        elif a.orden == "estado":
            _salida(reg.volcar_estado())
        elif a.orden == "recalcular":
            _salida(reg.recalcular())
        elif a.orden == "rechazados":
            _salida(reg.rechazados())
        elif a.orden == "tipos":
            _salida({n: {"entidad": d.entidad, "papeles": sorted(d.papeles),
                         "desde": None if d.desde is None else sorted(d.desde),
                         "hasta": d.hasta, "versiones": sorted(d.versiones)}
                     for n, d in TIPOS.items() if not a.papel or a.papel in d.papeles})
        elif a.orden == "subir":
            print(reg.subir())
        elif a.orden == "parte":
            from .parte import generar
            print(generar(reg))
        elif a.orden == "telegram":
            from . import telegram as tg
            if not a.panel and not (a.accion and a.pulsacion):
                raise Rechazo("faltan --accion y --pulsacion (o --panel)")
            try:
                res = tg.panel(reg) if a.panel else tg.aplicar(reg, a.accion, a.pulsacion)
                if a.subir and not a.panel:  # el aviso de «registrada» solo tras subir
                    res["subida"] = reg.subir()
            except Rechazo as ex:  # quien pulsó tiene que saber que no se registró
                if a.enviar:
                    tg.enviar(f"⚠ No se registró la pulsación: {ex}", [])
                raise
            if a.enviar:
                res["enviado"] = tg.enviar(res["texto"], res["botones"])
            _salida(res)
        elif a.orden in ("importar", "comparar", "cartera", "diario"):
            return _fase2(reg, a)
        elif a.orden in ("validar", "revisar", "gasto"):
            return _validador(reg, a)
        elif a.orden == "puente":
            return _puente(reg, a)
        else:
            return _fase4(reg, a)
    except Rechazo as ex:
        print(f"rechazado: {ex}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
