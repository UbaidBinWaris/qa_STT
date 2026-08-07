#!/usr/bin/env python3
import os
import sys
import subprocess
import time

def setup_virtualenv_and_install():
    print("==========================================================")
    print(" Checking Virtual Environment & Dependencies for STT Server")
    print("==========================================================")
    
    server_dir = os.path.dirname(os.path.abspath(__file__))
    venv_dir = os.path.join(server_dir, "venv")
    venv_python = os.path.join(venv_dir, "bin", "python")
    venv_pip = os.path.join(venv_dir, "bin", "pip")

    # Create virtual environment if it doesn't exist
    if not os.path.exists(venv_dir):
        print(f"Creating virtual environment in: {venv_dir}")
        subprocess.check_call([sys.executable, "-m", "venv", venv_dir])
    
    # If running outside the venv, re-execute script inside the virtual environment
    if sys.executable != venv_python and os.path.exists(venv_python):
        print("Re-executing startup script inside virtual environment...")
        requirements_file = os.path.join(server_dir, "requirements.txt")
        
        # Install requirements inside venv
        print("Installing/verifying dependencies in virtual environment...")
        try:
            subprocess.check_call([venv_pip, "install", "--upgrade", "pip", "setuptools", "wheel"])
            subprocess.check_call([venv_pip, "install", "-r", requirements_file])
            print("Dependencies successfully installed in virtual environment!")
        except Exception as e:
            print(f"Notice during dependency installation: {e}")
            
        os.execv(venv_python, [venv_python] + sys.argv)

def main():
    setup_virtualenv_and_install()
    
    print("\n==========================================================")
    print(" Starting NVIDIA Parakeet STT Server & Model Warmup")
    print(" Endpoint: http://localhost:8000")
    print("==========================================================\n")
    
    server_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(server_dir)
    
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, log_level="info")

if __name__ == "__main__":
    main()
