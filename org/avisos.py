"""Avisos a Javier (S-1273): siempre una línea en `.indice/avisos.log` (local) y, si se pide,
Telegram por TSPBot.

- En GitHub: secretos TSPBOT_TOKEN y TSPBOT_CHAT_ID como variables de entorno (`telegram.enviar`).
- En el PC: `scripts/tsp_aviso.py` del repo central, que lee sus credenciales él solo; aquí no se
  leen. Se lanza sin ventana.

Nunca lanza: un aviso que falla no puede tumbar el motor ni el vigilante.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CENTRAL = os.environ.get("ORG_CENTRAL", "C:/xampp/htdocs/2606-thestromboliproject")


def avisar(reg, texto: str, enviar: bool = False) -> bool:
    log = reg.raiz / ".indice" / "avisos.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {texto}\n")
    if not enviar:
        return False
    try:
        if os.environ.get("TSPBOT_TOKEN"):
            from .telegram import enviar as tg
            return tg(texto, [])
        script = Path(CENTRAL) / "scripts" / "tsp_aviso.py"
        if not script.is_file():
            print("org: sin TSPBOT_TOKEN ni tsp_aviso.py; aviso solo en el log", file=sys.stderr)
            return False
        r = subprocess.run([sys.executable, str(script), texto], capture_output=True,
                           timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001
        print(f"org: aviso fallido ({type(e).__name__})", file=sys.stderr)
        return False
