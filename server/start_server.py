#!/usr/bin/env python3
"""Launcher: stops any previous instance, ensures deps, starts the warmed server."""
import os
import shutil
import socket
import subprocess
import sys
import time

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SERVER_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")
PORT = int(os.environ.get("PORT", "8000"))
PID_FILE = os.path.join(SERVER_DIR, ".server.pid")


def port_busy() -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _terminate(pid: int) -> bool:
    """SIGTERM, then SIGKILL if it does not exit. Returns True if we killed it."""
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False

    for _ in range(20):
        time.sleep(0.25)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


def stop_previous():
    """Stop any prior instance so the port and its VRAM are released."""
    stopped = False

    if os.path.exists(PID_FILE):
        try:
            pid = int(open(PID_FILE).read().strip())
            if pid != os.getpid():
                stopped = _terminate(pid)
        except (ValueError, OSError):
            pass
        os.remove(PID_FILE)

    # Fallback for instances started directly with uvicorn, plus anything else
    # still holding the port.
    if subprocess.run(["pkill", "-f", "uvicorn app:app"], capture_output=True).returncode == 0:
        stopped = True
    if shutil.which("fuser"):
        if subprocess.run(["fuser", "-k", f"{PORT}/tcp"], capture_output=True).returncode == 0:
            stopped = True

    if stopped:
        print(f"Stopped previous server on port {PORT}.", flush=True)
        time.sleep(1)

    for _ in range(20):
        if not port_busy():
            return
        time.sleep(0.5)

    print(f"Port {PORT} is still in use by another process. Free it and retry.", flush=True)
    sys.exit(1)


def write_pid():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    import atexit

    atexit.register(lambda: os.path.exists(PID_FILE) and os.remove(PID_FILE))


def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found — audio conversion will fail. Install it with:", flush=True)
        print("  sudo apt install ffmpeg")
        sys.exit(1)


# NeMo pins protobuf~=5.29.5, but the onnx build in this stack fails to import
# below 5.31. pip's resolver cannot satisfy both, so this one is forced in
# afterwards with --no-deps. Without it, `import nemo.collections.asr` raises
# a protobuf VersionError and no model ever loads.
OVERRIDES = ["protobuf>=5.31.1"]


REQUIRED_IMPORTS = [
    "fastapi", "uvicorn", "requests", "soundfile", "numpy", "torch",
    "nemo.collections.asr", "multipart",
]


def deps_ok() -> bool:
    """Cheap import check. Reinstalling on every launch is both slow and unsafe
    here: pip would happily 'satisfy' NeMo's own pins by downgrading numpy,
    ml_dtypes and protobuf back to versions that cannot import."""
    code = "; ".join(f"import {m}" for m in REQUIRED_IMPORTS)
    result = subprocess.run([VENV_PYTHON, "-c", code], capture_output=True, text=True)
    if result.returncode != 0:
        last = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown"
        print(f"Dependency check failed: {last}", flush=True)
    return result.returncode == 0


def install_deps():
    print("Installing dependencies (first run takes several minutes)...", flush=True)
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "-q",
                           "pip", "setuptools", "wheel"])
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "-r",
                           os.path.join(SERVER_DIR, "requirements.txt")])
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--no-deps",
                           "--upgrade", *OVERRIDES])
    print("Dependencies installed.", flush=True)


def bootstrap():
    if not os.path.exists(VENV_DIR):
        print(f"Creating virtual environment in {VENV_DIR}", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    if os.path.abspath(sys.executable) == os.path.abspath(VENV_PYTHON):
        return

    if "--reinstall" in sys.argv or not deps_ok():
        install_deps()
        if not deps_ok():
            print("Dependencies are still broken after install. Aborting.", flush=True)
            sys.exit(1)

    sys.stdout.flush()
    os.execv(VENV_PYTHON, [VENV_PYTHON] + [a for a in sys.argv if a != "--reinstall"])


def main():
    check_ffmpeg()
    stop_previous()
    bootstrap()

    os.chdir(SERVER_DIR)
    # Keep model downloads inside the repo.
    cache = os.path.join(SERVER_DIR, "models_cache")
    os.environ.setdefault("HF_HOME", cache)
    os.environ.setdefault("TORCH_HOME", cache)
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    write_pid()

    print("\nSales Call QA — starting up", flush=True)
    print("Loading models and running warm-up inference; first launch downloads ~3 GB.\n")

    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
