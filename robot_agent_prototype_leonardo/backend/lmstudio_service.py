from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class LMStudioServiceError(RuntimeError):
    pass


class LMStudioService:
    def __init__(self) -> None:
        self._preferred_path = Path.home() / ".lmstudio" / "bin" / "lms"

    def _resolve_binary(self) -> str:
        if self._preferred_path.exists():
            return str(self._preferred_path)
        candidate = shutil.which("lms")
        if candidate:
            return candidate
        raise LMStudioServiceError("LM Studio CLI was not found. Install LM Studio and run lms bootstrap if needed.")

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        binary = self._resolve_binary()
        try:
            return subprocess.run(
                [binary, *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise LMStudioServiceError(detail) from exc
        except subprocess.TimeoutExpired as exc:
            raise LMStudioServiceError(f"LM Studio command timed out: {' '.join(args)}") from exc

    def get_runtime(self) -> dict:
        status_proc = self._run("server", "status")
        server_output = f"{status_proc.stdout}\n{status_proc.stderr}".strip().lower()
        server_running = "running" in server_output

        local_models = self._parse_json_output(self._run("ls", "--json").stdout)
        loaded_models = self._parse_json_output(self._run("ps", "--json").stdout)

        return {
            "server_running": server_running,
            "local_models": local_models,
            "loaded_models": loaded_models,
        }

    def start_server(self) -> dict:
        self._run("server", "start", timeout=60)
        return self.get_runtime()

    def load_model(self, model: str) -> dict:
        model_key = model.strip()
        if not model_key:
            raise LMStudioServiceError("Model identifier is empty")
        self.start_server()
        self._run("load", model_key, "-y", timeout=180)
        return self.get_runtime()

    @staticmethod
    def _parse_json_output(output: str):
        try:
            return json.loads(output or "[]")
        except json.JSONDecodeError as exc:
            raise LMStudioServiceError("LM Studio returned invalid JSON") from exc


lmstudio_service = LMStudioService()
