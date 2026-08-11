#!/usr/bin/env python3
"""Expose the local server through an ngrok tunnel, password-protected."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

import config
import start_server

config.load_env()

PORT = start_server.PORT
NGROK_API = "http://127.0.0.1:4040/api/tunnels"

INSTALL_HINT = """ngrok is not installed. Install it with one of:

  sudo snap install ngrok
  # or
  curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \\
    | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \\
    && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \\
    | sudo tee /etc/apt/sources.list.d/ngrok.list \\
    && sudo apt update && sudo apt install ngrok
"""


def require_ngrok() -> str:
    path = shutil.which("ngrok")
    if not path:
        print(INSTALL_HINT, flush=True)
        sys.exit(1)
    return path


def require_token() -> str:
    token = os.environ.get("NGROK_AUTHTOKEN")
    if not token:
        print("NGROK_AUTHTOKEN is not set. Add it to .env:", flush=True)
        print('  NGROK_AUTHTOKEN="your_token_here"', flush=True)
        sys.exit(1)
    return token


def require_password() -> str:
    """The tunnel makes call recordings and transcripts reachable from the public
    internet, so a password is mandatory here even though local use is open."""
    pw = os.environ.get("APP_PASSWORD")
    if pw:
        return pw

    suggestion = secrets.token_urlsafe(12)
    print("\nAPP_PASSWORD is not set — refusing to expose call data unprotected.")
    print("Add a password to .env and restart the server:")
    print(f'  APP_PASSWORD="{suggestion}"\n', flush=True)
    sys.exit(1)


def server_running() -> bool:
    return start_server.port_busy()


def server_has_auth() -> bool:
    """A server started without APP_PASSWORD would serve the tunnel unprotected."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/api/auth-status", timeout=5
        ) as resp:
            return json.load(resp).get("auth_required", False)
    except Exception:
        return False


def public_url(timeout: float = 20.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(NGROK_API, timeout=2) as resp:
                tunnels = json.load(resp).get("tunnels", [])
            for t in tunnels:
                if t.get("proto") == "https":
                    return t["public_url"]
            if tunnels:
                return tunnels[0]["public_url"]
        except Exception:
            pass
        time.sleep(1)
    return None


def main():
    # Without this, a SIGTERM (pkill, a supervisor, a closed terminal) skips the
    # cleanup below and leaves ngrok running — the tunnel would stay open after
    # you thought you had closed it.
    import signal

    def on_term(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)

    ngrok = require_ngrok()
    token = require_token()
    password = require_password()

    if not server_running():
        print(f"No server on port {PORT}. Start it first with: npm run dev", flush=True)
        sys.exit(1)

    if not server_has_auth():
        print("\nThe running server has no password set, so the tunnel would be")
        print("publicly readable. Restart it with the password applied:\n")
        print(f'  APP_PASSWORD="{password}" npm run dev\n')
        print("(or put APP_PASSWORD in .env, which npm run dev reads automatically)", flush=True)
        sys.exit(1)

    subprocess.run([ngrok, "config", "add-authtoken", token],
                   check=True, capture_output=True)

    cmd = [ngrok, "http", str(PORT), "--log", "stdout", "--log-format", "json"]
    # Free accounts get one static domain, so the URL is already stable. Pinning it
    # explicitly guarantees the shared link never drifts if that ever changes.
    domain = os.environ.get("NGROK_DOMAIN")
    if domain:
        cmd += ["--url", domain if domain.startswith("http") else f"https://{domain}"]

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = public_url(timeout=20)

    try:
        if not url:
            print("Tunnel did not come up. Check `ngrok http 8000` manually.", flush=True)
            proc.terminate()
            sys.exit(1)

        print("\n" + "=" * 58)
        print(f"  Public URL : {url}")
        print(f"  Password   : {password}")
        print("=" * 58)
        print("\nAnyone with the URL still needs the password. Share both privately,")
        print("and stop the tunnel (Ctrl-C) when you are done — the recordings")
        print("contain customer names and phone numbers.")
        print("\nNote: free-plan ngrok shows visitors a 'You are about to visit' page")
        print("once, and routes this region's traffic through Mumbai, India — neither")
        print("is changeable here. `npm run tunnel` (Cloudflare) avoids both and")
        print("routes via Karachi instead.")
        print("\nInspect traffic at http://127.0.0.1:4040\n", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        print("\nClosing tunnel.", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
