#!/usr/bin/env python3
"""Start script for running the agent server (no frontend — UI will be on Vercel later)."""

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

BACKEND_READY = [r"Uvicorn running on", r"Application startup complete", r"Started server process"]


def check_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", port))
        return True
    except OSError:
        return False


class ProcessManager:
    def __init__(self, port=8000):
        self.backend_process = None
        self.backend_ready = False
        self.failed = threading.Event()
        self.backend_log = None
        self.port = port

    def monitor_process(self, process, log_file):
        is_ready = False
        try:
            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                line = line.rstrip()
                log_file.write(line + "\n")
                print(f"[backend] {line}")

                if not is_ready and any(re.search(p, line, re.IGNORECASE) for p in BACKEND_READY):
                    is_ready = True
                    self.backend_ready = True
                    print(f"\n{'=' * 50}")
                    print("Brickbot agent is ready!")
                    print(f"API: http://localhost:{self.port}/invocations")
                    print(f"Chat: http://localhost:{self.port}/chat")
                    print(f"{'=' * 50}\n")

            process.wait()
            if process.returncode != 0:
                self.failed.set()
        except Exception as e:
            print(f"Error monitoring backend: {e}")
            self.failed.set()

    def run(self, backend_args=None):
        load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

        if not os.environ.get("DATABRICKS_APP_NAME"):
            if not check_port_available(self.port):
                print(f"ERROR: Port {self.port} is already in use.")
                print(f"  To free it: lsof -ti :{self.port} | xargs kill -9")
                sys.exit(1)

        self.backend_log = open("backend.log", "w", buffering=1)

        try:
            cmd = ["uv", "run", "start-server"]
            if backend_args:
                cmd.extend(backend_args)

            print("Starting Brickbot agent...")
            self.backend_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )

            thread = threading.Thread(
                target=self.monitor_process,
                args=(self.backend_process, self.backend_log),
                daemon=True,
            )
            thread.start()

            while not self.failed.is_set():
                time.sleep(0.1)
                if self.backend_process.poll() is not None:
                    self.failed.set()
                    break

            exit_code = self.backend_process.returncode if self.backend_process else 1
            print(f"\nBackend exited with code {exit_code}")
            return exit_code

        except KeyboardInterrupt:
            print("\nInterrupted")
            return 0

        finally:
            if self.backend_process:
                try:
                    self.backend_process.terminate()
                    self.backend_process.wait(timeout=5)
                except (subprocess.TimeoutExpired, Exception):
                    self.backend_process.kill()
            if self.backend_log:
                self.backend_log.close()


def main():
    parser = argparse.ArgumentParser(description="Start Brickbot agent server")
    args, backend_args = parser.parse_known_args()

    port = 8000
    for i, arg in enumerate(backend_args):
        if arg == "--port" and i + 1 < len(backend_args):
            try:
                port = int(backend_args[i + 1])
            except ValueError:
                pass
            break

    sys.exit(ProcessManager(port=port).run(backend_args))


if __name__ == "__main__":
    main()
