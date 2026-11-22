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
    except Rechazo as ex:
        print(f"rechazado: {ex}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
