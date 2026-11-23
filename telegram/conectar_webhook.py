"""Conecta TSPBot al Worker (S-1268). Lo ejecuta Javier, una vez, tras desplegar el Worker.

    py telegram/conectar_webhook.py <url-del-worker>      conecta
    py telegram/conectar_webhook.py --quitar              vuelve a solo avisar

Genera un secreto nuevo, lo guarda en el Worker (`wrangler secret put WEBHOOK_SECRET`, con la
sesión de wrangler de Javier) y se lo da a Telegram (setWebhook). No lo imprime en ningún
momento. El token del bot sale de ~/.credentials/telegram.json (clave «tspbot»), como en
scripts/tsp_aviso.py del repo central. Solo se reciben pulsaciones de botones.
"""
from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def _token() -> str:
    d = json.loads((Path.home() / ".credentials" / "telegram.json").read_text(encoding="utf-8-sig"))
    return d["tspbot"]["token"]


def _llamar(metodo: str, campos: dict) -> dict:
    datos = urllib.parse.urlencode(campos).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{_token()}/{metodo}", data=datos,
                                timeout=20) as r:
        return json.load(r)


def main(argv: list[str]) -> int:
    if argv == ["--quitar"]:
        print(_llamar("deleteWebhook", {}))
        return 0
    if len(argv) != 1 or not argv[0].startswith("https://"):
        print(__doc__)
        return 2
    secreto = secrets.token_urlsafe(48)  # solo A-Z a-z 0-9 _ -, como pide Telegram
    npx = shutil.which("npx") or "npx"
    r = subprocess.run([npx, "wrangler", "secret", "put", "WEBHOOK_SECRET"], cwd=AQUI,
                       input=secreto + "\n", text=True, capture_output=True)
    if r.returncode:
        print("wrangler secret put falló; no se ha tocado Telegram:\n" + (r.stderr or r.stdout))
        return 1
    print(_llamar("setWebhook", {"url": argv[0], "secret_token": secreto,
                                 "allowed_updates": json.dumps(["callback_query"])}))
    info = _llamar("getWebhookInfo", {}).get("result", {})
    print({"url": info.get("url"), "pendientes": info.get("pending_update_count"),
           "ultimo_error": info.get("last_error_message")})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
