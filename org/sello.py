"""Sello de papel (PAA2 P4-2, S-1270): un trabajador no puede crear «verificada» ni «aprobado por
Javier».

Los eventos de los papeles **validador, laboratorio y javier** (salvo la pausa, que puede pedir
cualquiera, 02 §9 bis) solo cuentan si el commit que añadió su fichero está firmado con una clave
SSH de ese papel. Las claves públicas están en `politica/firmantes` (formato *allowed signers* de
git: `<papel> namespaces="git" ssh-ed25519 AAAA…`); las privadas, solo en los secretos de GitHub
(`ORG_SELLO_VALIDADOR`, `ORG_SELLO_JAVIER`…), que ningún trabajador ve. Declarar
`--papel validador` ya no basta: sin la clave, el evento queda en rechazados (clase «sello»).

- **Se activa con el fichero.** Sin `politica/firmantes` no se exige nada (los registros de
  prueba). Cuando exista en el registro real (lo crea Javier con las claves), sin una clave
  para un papel sus eventos nuevos no cuentan.
- **Un fichero, un commit.** El fichero tiene que entrar en un solo commit firmado y no volver a
  tocarse: copiar un evento sellado a otro nombre, o editarlo después, lo deja sin sello.
- **Para escribir con sello:** `ORG_SELLO=<ruta de la clave privada>`. `org` comprueba que su
  pública está en `politica/firmantes` para ese papel y `org subir` firma el commit (y el rebase).
- **Lo de antes cuenta.** Una línea `desde <sha>` en `politica/firmantes` marca el corte: los
  eventos que entraron en ese commit o antes (y no se han tocado después) valen sin firma. Así
  activar el sello no anula las aprobaciones ya registradas.

Límite reconocido (02 §3.1 bis): en el PC todas las sesiones son el usuario de Javier. Quien edite
`politica/firmantes` o el propio `org` puede saltárselo; eso queda en la historia de git y lo
cubre la rama principal protegida (pendiente, la activa Javier). Lo que esto sí impide: que un
trabajador que usa `org`, escribe un fichero a mano o hace un commit sin firmar consiga un
«verificada» o un «aprobado».
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

PAPELES_SELLADOS = frozenset({"validador", "laboratorio", "javier"})
EXENTOS = ("pausa.",)          # 02 §9 bis: cualquiera puede pedir pausa
RUTA_FIRMANTES = "politica/firmantes"
VARIABLE_CLAVE = "ORG_SELLO"
HISTORICO = "*histórico*"     # firmante de lo que entró antes del corte (`desde <sha>`)


def exige(ev) -> bool:
    """¿Este evento necesita sello? Solo mira papel y tipo (lo demás lo valida esquemas)."""
    try:
        papel, tipo = ev["autor"]["papel"], ev["tipo"]
    except (KeyError, TypeError):
        return False
    return (isinstance(tipo, str) and papel in PAPELES_SELLADOS
            and not tipo.startswith(EXENTOS))


def activo(raiz: Path) -> bool:
    return (Path(raiz) / RUTA_FIRMANTES).is_file()


def huella_politica(raiz: Path) -> str:
    """Cambia si cambia la lista de firmantes: entonces se recalcula el índice entero."""
    p = Path(raiz) / RUTA_FIRMANTES
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "-"


def firmantes(raiz: Path) -> dict[str, set[str]]:
    """{papel: {«tipo clave»}} de politica/firmantes. Las líneas # y vacías no cuentan."""
    out: dict[str, set[str]] = {}
    p = Path(raiz) / RUTA_FIRMANTES
    if not p.is_file():
        return out
    for linea in p.read_text(encoding="utf-8").splitlines():
        partes = linea.split()
        if not partes or partes[0].startswith("#"):
            continue
        claves = [i for i, x in enumerate(partes) if x.startswith(("ssh-", "ecdsa-", "sk-"))]
        if not claves or claves[0] + 1 >= len(partes):
            continue
        i = claves[0]
        for papel in partes[0].split(","):
            out.setdefault(papel, set()).add(f"{partes[i]} {partes[i + 1]}")
    return out


def corte(raiz: Path) -> str | None:
    """El sha de la línea `desde <sha>` de politica/firmantes, o None."""
    p = Path(raiz) / RUTA_FIRMANTES
    if not p.is_file():
        return None
    for linea in p.read_text(encoding="utf-8").splitlines():
        partes = linea.split()
        if len(partes) == 2 and partes[0] == "desde":
            return partes[1]
    return None


def ssh_keygen() -> str:
    """El de PATH, o el de Git para Windows, o el de Windows: la tarea programada (wscript) no
    tiene el PATH de Git Bash."""
    for c in (shutil.which("ssh-keygen"), r"C:\Program Files\Git\usr\bin\ssh-keygen.exe",
              r"C:\Windows\System32\OpenSSH\ssh-keygen.exe"):
        if c and Path(c).is_file():
            return c
    return "ssh-keygen"


def clave_local() -> str | None:
    return os.environ.get(VARIABLE_CLAVE) or None


def papel_de_clave(raiz: Path, clave: str | None = None) -> str | None:
    """El papel cuya pública en politica/firmantes casa con la clave privada local, o None."""
    clave = clave or clave_local()
    if not clave or not Path(clave).is_file():
        return None
    r = subprocess.run([ssh_keygen(), "-y", "-f", clave], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        return None
    pub = " ".join(r.stdout.split()[:2])
    for papel, claves in firmantes(raiz).items():
        if pub in claves:
            return papel
    return None


def args_firma(clave: str) -> list[str]:
    """Opciones de git para firmar commits (también los que rehace un pull --rebase)."""
    return ["-c", "gpg.format=ssh", "-c", f"user.signingkey={clave}", "-c", "commit.gpgsign=true",
            "-c", f"gpg.ssh.program={ssh_keygen()}"]


def comprobar(raiz: Path, ficheros: list[str]) -> dict[str, tuple[str, str | None, str | None]]:
    """{fichero: (señal, papel firmante o None, motivo si no vale o None)}.

    La señal resume lo que se ha mirado en git (commits que lo tocan y su firma): si no cambia,
    el veredicto tampoco. Una sola llamada a git por los días de los ficheros pedidos."""
    raiz = Path(raiz)
    if not ficheros:
        return {}
    dias = sorted({str(Path(f).parent.as_posix()) for f in ficheros})
    allowed = (raiz / RUTA_FIRMANTES).resolve()
    r = subprocess.run(
        ["git", "-C", str(raiz), "-c", f"gpg.ssh.allowedSignersFile={allowed}",
         "-c", f"gpg.ssh.program={ssh_keygen()}", "log", "--format=@%H %G? %GS",
         "--name-status", "--no-renames", "HEAD", "--", *dias],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL)
    toques: dict[str, list[tuple[str, str, str, str]]] = {}
    if r.returncode == 0:
        commit = g = quien = ""
        for linea in r.stdout.splitlines():
            if linea.startswith("@"):
                partes = linea[1:].split(" ", 2)
                commit, g = partes[0], partes[1] if len(partes) > 1 else "N"
                quien = partes[2].strip() if len(partes) > 2 else ""
            elif "\t" in linea:
                estado, ruta = linea.split("\t", 1)
                toques.setdefault(ruta.strip(), []).append((commit, g, quien, estado[:1]))
    sucios = set()
    st = subprocess.run(["git", "-C", str(raiz), "status", "--porcelain", "--", *dias],
                        capture_output=True, text=True, encoding="utf-8",
                        stdin=subprocess.DEVNULL)
    if st.returncode == 0:
        sucios = {l[3:].strip() for l in st.stdout.splitlines() if l[:2].strip() not in ("", "??")}
    anteriores = set()
    if corte(raiz):
        rl = subprocess.run(["git", "-C", str(raiz), "rev-list", corte(raiz), "--", *dias],
                            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        anteriores = set(rl.stdout.split()) if rl.returncode == 0 else set()
    out = {}
    for f in ficheros:
        t = toques.get(f, [])
        senal = "|".join(f"{c[:12]}{g}{e}" for c, g, _, e in t) or "-"
        if f in sucios:
            out[f] = (senal + "*", None, "modificado después de su commit")
        elif t and all(c in anteriores for c, _, _, _ in t):
            out[f] = (senal + "<", HISTORICO, None)
        elif not t:
            out[f] = (senal, None, "sin commit firmado (escrito a mano o aún sin subir)")
        elif len(t) > 1 or t[0][3] != "A":
            out[f] = (senal, None, "el fichero se ha tocado después de entrar")
        elif t[0][1] != "G":
            out[f] = (senal, None, f"commit {t[0][0][:12]} sin firma válida ({t[0][1]})")
        else:
            out[f] = (senal, t[0][2] or None, None)
    return out
