from __future__ import annotations

import argparse
import os
import platform
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "frontend"
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8000
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT = 5173
MIN_PYTHON = (3, 10)


def run_command(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"→ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def npm_executable() -> str:
    return "npm.cmd" if platform.system() == "Windows" else "npm"


def node_executable() -> str:
    return "node.exe" if platform.system() == "Windows" else "node"


def setup() -> None:
    python = sys.executable
    run_command([python, "-m", "pip", "install", "-r", "requirements.txt"], cwd=ROOT)
    run_command([npm_executable(), "install"], cwd=FRONTEND_DIR)
    print("\nSetup completed.")


def start_backend(reload_enabled: bool = False) -> subprocess.Popen:
    python = sys.executable
    cmd = [
        python,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        BACKEND_HOST,
        "--port",
        str(BACKEND_PORT),
    ]
    if reload_enabled:
        cmd.append("--reload")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(cmd, cwd=ROOT, env=env)


def start_frontend() -> subprocess.Popen:
    vite_cli = FRONTEND_DIR / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_cli.exists():
        raise FileNotFoundError(
            "Vite CLI was not found. Run `python run.py --setup` or `npm install` in the frontend directory."
        )

    cmd = [
        node_executable(),
        str(vite_cli),
        "--host",
        FRONTEND_HOST,
        "--port",
        str(FRONTEND_PORT),
    ]
    return subprocess.Popen(cmd, cwd=FRONTEND_DIR)


def terminate(processes: list[subprocess.Popen]) -> None:
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    deadline = time.time() + 5
    while time.time() < deadline:
        if all(proc.poll() is not None for proc in processes):
            return
        time.sleep(0.1)

    for proc in processes:
        if proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Robot Agent Prototype")
    parser.add_argument("--setup", action="store_true", help="Install Python and frontend dependencies")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the frontend automatically")
    parser.add_argument("--reload-backend", action="store_true", help="Enable uvicorn autoreload for backend development")
    args = parser.parse_args()

    if sys.version_info < MIN_PYTHON:
        required = ".".join(str(part) for part in MIN_PYTHON)
        current = ".".join(str(part) for part in sys.version_info[:3])
        print(f"Python {required}+ is required. Current interpreter: {current}")
        print("Recreate the virtual environment with Python 3.10+ and try again.")
        raise SystemExit(1)

    if args.setup:
        setup()
        return

    frontend_node_modules = FRONTEND_DIR / "node_modules"
    if not frontend_node_modules.exists():
        print("Frontend dependencies are missing. Running npm install first...")
        run_command([npm_executable(), "install"], cwd=FRONTEND_DIR)

    processes: list[subprocess.Popen] = []

    def handle_exit(signum, frame):
        print("\nStopping services...")
        terminate(processes)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    print("Starting backend...")
    backend = start_backend(reload_enabled=args.reload_backend)
    processes.append(backend)

    time.sleep(1.5)

    print("Starting frontend...")
    frontend = start_frontend()
    processes.append(frontend)

    frontend_url = f"http://{FRONTEND_HOST}:{FRONTEND_PORT}"
    backend_url = f"http://{BACKEND_HOST}:{BACKEND_PORT}"

    print(f"\nFrontend: {frontend_url}")
    print(f"Backend : {backend_url}")
    print("Press Ctrl+C to stop both services.")

    if not args.no_browser:
        try:
            webbrowser.open(frontend_url)
        except Exception:
            pass

    try:
        while True:
            if any(proc.poll() is not None for proc in processes):
                print("One of the services exited. Stopping the rest...")
                terminate(processes)
                break
            time.sleep(0.5)
    finally:
        terminate(processes)


if __name__ == "__main__":
    main()
