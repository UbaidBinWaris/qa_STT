#!/usr/bin/env python3
"""Expose the local server through a Cloudflare quick tunnel.

Unlike ngrok's free tier, trycloudflare.com serves no interstitial warning page,
so shared links open straight into the login screen. No account or token needed.
"""
import os
import re
import shutil
import signal
import subprocess
import sys

import config
import start_server

config.load_env()

PORT = start_server.PORT
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

INSTALL_HINT = """cloudflared is not installed. Install it with:

  curl -sSL -o cloudflared \\
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \\
    && chmod +x cloudflared && mkdir -p ~/.local/bin && mv cloudflared ~/.local/bin/

Then make sure ~/.local/bin is on your PATH.
"""


def main():
    def on_term(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)

    binary = shutil.which("cloudflared")
    if not binary:
        print(INSTALL_HINT, flush=True)
        sys.exit(1)

    password = os.environ.get("APP_PASSWORD")
    if not password:
        print("APP_PASSWORD is not set — refusing to expose call data unprotected.")
        print("Add it to .env and restart the server.", flush=True)
        sys.exit(1)

    if not start_server.port_busy():
        print(f"No server on port {PORT}. Start it first with: npm run dev", flush=True)
        sys.exit(1)

    if not _server_has_auth():
        print("\nThe running server has no password set, so the tunnel would be")
        print("publicly readable. Restart it with: npm run dev", flush=True)
        sys.exit(1)

    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )

    try:
        url = None
        for line in proc.stdout:
            if url is None:
                match = URL_RE.search(line)
                if match:
                    url = match.group(0)
                    print("\n" + "=" * 58)
                    print(f"  Public URL : {url}")
                    print(f"  Password   : {password}")
                    print("=" * 58)
                    print("\nNo interstitial — the link opens straight at the login page.")
                    print("Share the URL and password privately, and stop the tunnel")
                    print("(Ctrl-C) when you are done.\n", flush=True)
        proc.wait()
    except KeyboardInterrupt:
        print("\nClosing tunnel.", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _server_has_auth() -> bool:
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{PORT}/api/auth-status", timeout=5
        ) as resp:
            return json.load(resp).get("auth_required", False)
    except Exception:
        return False


if __name__ == "__main__":
    main()
