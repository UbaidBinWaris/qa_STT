#!/usr/bin/env python3
import os
import subprocess
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(SERVER_DIR, "venv")
VENV_PYTHON = os.path.join(VENV_DIR, "bin", "python")


def bootstrap():
    if not os.path.exists(VENV_DIR):
        print(f"Creating virtual environment in {VENV_DIR}")
        subprocess.check_call([sys.executable, "-m", "venv", VENV_DIR])

    if os.path.abspath(sys.executable) == os.path.abspath(VENV_PYTHON):
        return

    print("Installing dependencies...")
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "--upgrade", "-q",
                           "pip", "setuptools", "wheel"])
    subprocess.check_call([VENV_PYTHON, "-m", "pip", "install", "-r",
                           os.path.join(SERVER_DIR, "requirements.txt")])
    os.execv(VENV_PYTHON, [VENV_PYTHON] + sys.argv)


def main():
    bootstrap()
    os.chdir(SERVER_DIR)
    # Keep model downloads inside the repo.
    os.environ.setdefault("HF_HOME", os.path.join(SERVER_DIR, "models_cache"))
    os.environ.setdefault("TORCH_HOME", os.path.join(SERVER_DIR, "models_cache"))

    print("\nSales Call QA — http://localhost:8000\n")
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
