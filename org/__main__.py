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
    tg.add_argument("--enviar", action="store_true", help="manda la respuesta por TSPBot")

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
            except Rechazo as ex:  # quien pulsó tiene que saber que no se registró
                if a.enviar:
                    tg.enviar(f"⚠ No se registró la pulsación: {ex}", [])
                raise
            if a.enviar:
                res["enviado"] = tg.enviar(res["texto"], res["botones"])
            _salida(res)
        elif a.orden in ("importar", "comparar", "cartera", "diario"):
            return _fase2(reg, a)
    except Rechazo as ex:
        print(f"rechazado: {ex}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
