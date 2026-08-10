#!/usr/bin/env python3
"""Stop the running server and release its VRAM."""
import sys

import start_server


def main():
    if not start_server.port_busy():
        print(f"No server running on port {start_server.PORT}.")
        return
    start_server.stop_previous()
    print("Server stopped.")


if __name__ == "__main__":
    sys.exit(main())
