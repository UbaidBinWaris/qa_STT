#!/usr/bin/env python3
"""Run the upload and security suites against a running server.

The security suite ends by deliberately tripping the login rate limiter, so it
runs last and the server should be restarted before running these again.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "server"))

import config  # noqa: E402

config.load_env()

SUITES = ["test_limits.py", "test_upload.py", "test_security.py"]


def main():
    if not os.environ.get("APP_PASSWORD"):
        print("APP_PASSWORD is not set (needed to authenticate). Add it to .env.")
        return 1

    failed = []
    for suite in SUITES:
        print(f"\n{'=' * 70}\n {suite}\n{'=' * 70}")
        result = subprocess.run([sys.executable, os.path.join(HERE, suite)])
        if result.returncode != 0:
            failed.append(suite)

    print(f"\n{'=' * 70}")
    print("FAILED: " + ", ".join(failed) if failed else "All suites passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
