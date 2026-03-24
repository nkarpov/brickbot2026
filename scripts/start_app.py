#!/usr/bin/env python3
"""
Start script: frontend on :8000 (exposed port), backend on :8001 (internal).

The Next.js frontend serves the chat UI and proxies /invocations to the backend.
This fixes refresh/deep-link issues since the frontend owns all routes.
"""

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

BACKEND_READY = [r"Uvicorn running on", r"Application startup complete", r"Started server process"]
FRONTEND_READY = [r"Server is running on http://localhost"]

BACKEND_PORT = 8001
FRONTEND_PORT = int(os.environ.get("PORT", "8000"))


def check_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", port))
        return True
    except OSError:
        return False


class ProcessManager:
    def __init__(self):
        self.backend_process = None
        self.frontend_process = None
        self.backend_ready = False
        self.frontend_ready = False
        self.failed = threading.Event()
        self.backend_log = None
        self.frontend_log = None

    def monitor_process(self, process, name, log_file, patterns):
        is_ready = False
        try:
            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                line = line.rstrip()
                log_file.write(line + "\n")
                print(f"[{name}] {line}")

                if not is_ready and any(re.search(p, line, re.IGNORECASE) for p in patterns):
                    is_ready = True
                    if name == "backend":
                        self.backend_ready = True
                    else:
                        self.frontend_ready = True
                    print(f"  {name} is ready!")

                    if self.backend_ready and self.frontend_ready:
                        print(f"\n{'=' * 50}")
                        print("Brickbot is ready!")
                        print(f"Chat UI: http://localhost:{FRONTEND_PORT}")
                        print(f"Agent API: http://localhost:{BACKEND_PORT}/invocations")
                        print(f"{'=' * 50}\n")

            process.wait()
            if process.returncode != 0:
                self.failed.set()
        except Exception as e:
            print(f"Error monitoring {name}: {e}")
            self.failed.set()

    def clone_frontend_if_needed(self):
        if Path("e2e-chatbot-app-next").exists():
            return True

        print("Cloning e2e-chatbot-app-next...")
        for url in [
            "https://github.com/databricks/app-templates.git",
            "git@github.com:databricks/app-templates.git",
        ]:
            try:
                subprocess.run(
                    ["git", "clone", "--filter=blob:none", "--sparse", url, "temp-app-templates"],
                    check=True, capture_output=True,
                )
                break
            except subprocess.CalledProcessError:
                continue
        else:
            print("ERROR: Failed to clone frontend repository.")
            return False

        subprocess.run(
            ["git", "sparse-checkout", "set", "e2e-chatbot-app-next"],
            cwd="temp-app-templates", check=True,
        )
        Path("temp-app-templates/e2e-chatbot-app-next").rename("e2e-chatbot-app-next")
        shutil.rmtree("temp-app-templates", ignore_errors=True)
        return True

    def run(self, backend_args=None):
        load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

        if not os.environ.get("DATABRICKS_APP_NAME"):
            for port in [FRONTEND_PORT, BACKEND_PORT]:
                if not check_port_available(port):
                    print(f"ERROR: Port {port} is in use. Free it: lsof -ti :{port} | xargs kill -9")
                    sys.exit(1)

        if not self.clone_frontend_if_needed():
            print("ERROR: Frontend not available.")
            sys.exit(1)

        # Tell the frontend where the backend API lives
        os.environ["API_PROXY"] = f"http://localhost:{BACKEND_PORT}/invocations"
        # Frontend listens on the exposed port
        os.environ["PORT"] = str(FRONTEND_PORT)

        self.backend_log = open("backend.log", "w", buffering=1)
        self.frontend_log = open("frontend.log", "w", buffering=1)

        try:
            # Start backend on internal port
            backend_cmd = ["uv", "run", "start-server", "--port", str(BACKEND_PORT)]
            if backend_args:
                backend_cmd.extend(backend_args)

            print(f"Starting backend on :{BACKEND_PORT}...")
            self.backend_process = subprocess.Popen(
                backend_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            threading.Thread(
                target=self.monitor_process,
                args=(self.backend_process, "backend", self.backend_log, BACKEND_READY),
                daemon=True,
            ).start()

            # Build and start frontend on exposed port
            frontend_dir = Path("e2e-chatbot-app-next")
            for cmd, desc in [("npm install", "install"), ("npm run build", "build")]:
                print(f"Running npm {desc}...")
                result = subprocess.run(cmd.split(), cwd=frontend_dir, capture_output=True, text=True)
                if result.returncode != 0:
                    print(f"npm {desc} failed: {result.stderr}")
                    return 1

            print(f"Starting frontend on :{FRONTEND_PORT}...")
            self.frontend_process = subprocess.Popen(
                ["npm", "run", "start"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                cwd=frontend_dir,
            )
            threading.Thread(
                target=self.monitor_process,
                args=(self.frontend_process, "frontend", self.frontend_log, FRONTEND_READY),
                daemon=True,
            ).start()

            # Wait for failure
            while not self.failed.is_set():
                time.sleep(0.1)
                if self.backend_process.poll() is not None:
                    self.failed.set()
                if self.frontend_process.poll() is not None:
                    self.failed.set()

            failed = "backend" if self.backend_process.poll() is not None else "frontend"
            print(f"\n{'=' * 42}\nERROR: {failed} exited\n{'=' * 42}")
            return 1

        except KeyboardInterrupt:
            return 0

        finally:
            for proc in [self.backend_process, self.frontend_process]:
                if proc:
                    try:
                        proc.terminate()
                        proc.wait(timeout=5)
                    except (subprocess.TimeoutExpired, Exception):
                        proc.kill()
            for log in [self.backend_log, self.frontend_log]:
                if log:
                    log.close()


def main():
    parser = argparse.ArgumentParser(description="Start Brickbot (frontend + backend)")
    args, backend_args = parser.parse_known_args()
    sys.exit(ProcessManager().run(backend_args))


if __name__ == "__main__":
    main()
