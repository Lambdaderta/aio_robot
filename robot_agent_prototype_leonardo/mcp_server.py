from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request


JSONRPC_VERSION = "2.0"
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
}


class BackendAPIError(RuntimeError):
    pass


class ToolExecutionError(RuntimeError):
    pass


class UnknownToolError(KeyError):
    pass


class BackendAPIClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("AIO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def get_json(self, path: str) -> dict[str, Any]:
        req = request.Request(self._url(path), headers={"Content-Type": "application/json"}, method="GET")
        return self._read_json(req)

    def post_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._url(path),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_json(req)

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _read_json(self, req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("detail") or payload.get("error", {}).get("message") or detail
            except Exception:
                pass
            raise BackendAPIError(str(detail)) from exc
        except error.URLError as exc:
            raise BackendAPIError(f"Could not reach AIO backend at {self.base_url}: {exc.reason}") from exc


class MacSpeaker:
    def __init__(self) -> None:
        self._binary = shutil.which("say")

    def speak(self, text: str, voice: str | None = None, rate: int | None = None, wait: bool = False) -> dict[str, Any]:
        phrase = str(text or "").strip()
        if not phrase:
            raise ToolExecutionError("Text is empty")
        if self._binary is None:
            raise ToolExecutionError("macOS 'say' command was not found on this machine")

        command = [self._binary]
        if voice:
            command.extend(["-v", voice])
        if rate is not None:
            command.extend(["-r", str(int(rate))])
        command.append(phrase)

        try:
            if wait:
                subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except (ValueError, subprocess.SubprocessError) as exc:
            raise ToolExecutionError(f"Could not start speech output: {exc}") from exc

        return {
            "ok": True,
            "voice": voice or "system-default",
            "rate": int(rate) if rate is not None else None,
            "wait": bool(wait),
            "text": phrase,
        }


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class AIOMCPServer:
    def __init__(self, backend: BackendAPIClient | None = None, speaker: MacSpeaker | None = None) -> None:
        self.backend = backend or BackendAPIClient()
        self.speaker = speaker or MacSpeaker()
        self.initialized = False
        self.tools = {
            tool.name: tool
            for tool in (
                ToolDefinition(
                    name="aio_get_status",
                    description="Read the current robot state, joint angles, backend limits, presets, and recent logs.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_list_ports",
                    description="List serial ports that the AIO backend can use for Arduino connection.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_connect_hardware",
                    description="Connect the AIO backend to Arduino hardware over serial.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "port": {"type": "string", "description": "Serial device path, for example /dev/cu.usbmodem1401."},
                            "baud_rate": {"type": "integer", "default": 115200},
                        },
                        "required": ["port"],
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition(
                    name="aio_disconnect_hardware",
                    description="Disconnect Arduino hardware from the AIO backend.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_move_joint",
                    description="Move one calibrated robot joint. Base uses logical angles from -90 to 90, with 0 as the upright center.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "joint_name": {"type": "string", "enum": ["base", "shoulder", "gripper"]},
                            "angle": {"type": "number"},
                        },
                        "required": ["joint_name", "angle"],
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition(
                    name="aio_apply_pose",
                    description="Apply a small pose update to one or more calibrated joints.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "base": {"type": "number"},
                            "shoulder": {"type": "number"},
                            "gripper": {"type": "number"},
                        },
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition(
                    name="aio_run_preset",
                    description="Run one of the backend-calibrated presets.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "preset": {"type": "string", "enum": ["HOME", "LIFT", "PARK", "LEFT", "CENTER", "RIGHT"]}
                        },
                        "required": ["preset"],
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition(
                    name="aio_stop_robot",
                    description="Send STOP to the robot controller immediately.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_get_lmstudio_runtime",
                    description="Read LM Studio runtime status through the AIO backend.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_start_lmstudio",
                    description="Start the LM Studio server through the AIO backend.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_list_models",
                    description="List models currently exposed by the AIO chat runtime.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                ToolDefinition(
                    name="aio_load_model",
                    description="Load a specific LM Studio model and set it as the chat runtime model inside AIO.",
                    input_schema={
                        "type": "object",
                        "properties": {"model": {"type": "string"}},
                        "required": ["model"],
                        "additionalProperties": False,
                    },
                ),
                ToolDefinition(
                    name="aio_speak_text",
                    description="Speak text aloud on macOS using the built-in 'say' command.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "voice": {"type": "string"},
                            "rate": {"type": "integer", "default": 180},
                            "wait": {"type": "boolean", "default": False},
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                ),
            )
        }

    def handle_message(self, message: Any) -> Any:
        if isinstance(message, list):
            responses = [response for item in message if (response := self.handle_message(item)) is not None]
            return responses
        if not isinstance(message, dict):
            return None

        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            return self._success(
                message_id,
                {
                    "protocolVersion": self._select_protocol_version(params.get("protocolVersion")),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "aio-mcp", "version": "0.1.0"},
                    "instructions": (
                        "Use AIO tools to check robot state, control the hand through the existing backend, "
                        "manage LM Studio, and optionally speak short answers aloud with aio_speak_text."
                    ),
                },
            )

        if method == "notifications/initialized":
            self.initialized = True
            return None

        if message_id is None:
            return None

        if method == "ping":
            return self._success(message_id, {})

        if method == "tools/list":
            return self._success(
                message_id,
                {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.input_schema,
                        }
                        for tool in self.tools.values()
                    ]
                },
            )

        if method == "tools/call":
            return self._handle_tool_call(message_id, params)

        return self._error(message_id, -32601, f"Method not found: {method}")

    def _handle_tool_call(self, message_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(tool_name, str) or not tool_name:
            return self._error(message_id, -32602, "Tool name is required")
        if not isinstance(arguments, dict):
            return self._error(message_id, -32602, "Tool arguments must be an object")

        try:
            summary, payload = self._call_tool(tool_name, arguments)
        except UnknownToolError:
            return self._error(message_id, -32601, f"Unknown tool: {tool_name}")
        except BackendAPIError as exc:
            return self._success(message_id, self._tool_error_result(str(exc), {"tool": tool_name}))
        except ToolExecutionError as exc:
            return self._success(message_id, self._tool_error_result(str(exc), {"tool": tool_name}))

        return self._success(message_id, self._tool_success_result(summary, payload))

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if tool_name == "aio_get_status":
            payload = self.backend.get_json("/api/status")
            state = payload.get("robot_state") or {}
            joints = state.get("joints") or {}
            summary = (
                f"Robot state loaded. hardware_connected={state.get('hardware_connected')} "
                f"base={joints.get('base')} shoulder={joints.get('shoulder')} gripper={joints.get('gripper')}."
            )
            return summary, payload

        if tool_name == "aio_list_ports":
            payload = self.backend.get_json("/api/hardware/ports")
            ports = payload.get("ports") or []
            summary = f"Found {len(ports)} serial port(s)."
            return summary, payload

        if tool_name == "aio_connect_hardware":
            port = self._require_string(arguments, "port")
            baud_rate = int(arguments.get("baud_rate", 115200))
            payload = self.backend.post_json("/api/hardware/connect", {"port": port, "baud_rate": baud_rate})
            summary = f"Hardware connect request sent for {port} at {baud_rate} baud."
            return summary, payload

        if tool_name == "aio_disconnect_hardware":
            payload = self.backend.post_json("/api/hardware/disconnect")
            return "Hardware disconnected.", payload

        if tool_name == "aio_move_joint":
            joint_name = self._require_string(arguments, "joint_name")
            angle = self._require_number(arguments, "angle")
            payload = self.backend.post_json("/api/manual/joint", {"joint_name": joint_name, "angle": angle})
            return f"Joint {joint_name} moved to {angle} degrees.", payload

        if tool_name == "aio_apply_pose":
            joints = {name: float(arguments[name]) for name in ("base", "shoulder", "gripper") if name in arguments}
            if not joints:
                raise ToolExecutionError("At least one of base, shoulder, or gripper must be provided")
            payload = self.backend.post_json("/api/manual/pose", {"joints": joints})
            return "Pose update sent to the robot.", payload

        if tool_name == "aio_run_preset":
            preset = self._require_string(arguments, "preset").upper()
            payload = self.backend.post_json(f"/api/manual/preset/{parse.quote(preset)}")
            return f"Preset {preset} executed.", payload

        if tool_name == "aio_stop_robot":
            payload = self.backend.post_json("/api/manual/stop")
            return "STOP sent to the robot.", payload

        if tool_name == "aio_get_lmstudio_runtime":
            payload = self.backend.get_json("/api/chat/runtime")
            runtime = payload if "server_running" in payload else payload.get("runtime", payload)
            summary = (
                f"LM Studio runtime loaded. server_running={runtime.get('server_running')} "
                f"loaded_models={len(runtime.get('loaded_models') or [])}."
            )
            return summary, payload

        if tool_name == "aio_start_lmstudio":
            payload = self.backend.post_json("/api/chat/runtime/start")
            return "LM Studio server start requested.", payload

        if tool_name == "aio_list_models":
            payload = self.backend.get_json("/api/chat/models")
            models = payload.get("models") or []
            return f"Found {len(models)} model(s) in the chat runtime.", payload

        if tool_name == "aio_load_model":
            model = self._require_string(arguments, "model")
            payload = self.backend.post_json("/api/chat/runtime/load", {"model": model})
            return f"Model {model} load requested.", payload

        if tool_name == "aio_speak_text":
            text = self._require_string(arguments, "text")
            voice = self._optional_string(arguments, "voice")
            rate = int(arguments["rate"]) if "rate" in arguments and arguments["rate"] is not None else None
            wait = bool(arguments.get("wait", False))
            payload = self.speaker.speak(text=text, voice=voice, rate=rate, wait=wait)
            return "Speech output started.", payload

        raise UnknownToolError(tool_name)

    @staticmethod
    def _require_string(arguments: dict[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ToolExecutionError(f"Argument '{key}' must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_string(arguments: dict[str, Any], key: str) -> str | None:
        value = arguments.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ToolExecutionError(f"Argument '{key}' must be a non-empty string when provided")
        return value.strip()

    @staticmethod
    def _require_number(arguments: dict[str, Any], key: str) -> float:
        value = arguments.get(key)
        if not isinstance(value, (int, float)):
            raise ToolExecutionError(f"Argument '{key}' must be a number")
        return float(value)

    @staticmethod
    def _tool_success_result(summary: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": summary}],
            "structuredContent": payload,
        }

    @staticmethod
    def _tool_error_result(message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": message}],
            "structuredContent": payload or {},
            "isError": True,
        }

    @staticmethod
    def _success(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": message_id,
            "result": result,
        }

    @staticmethod
    def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": message_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    @staticmethod
    def _select_protocol_version(requested: Any) -> str:
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
            return requested
        return LATEST_PROTOCOL_VERSION


def _write_message(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> int:
    server = AIOMCPServer()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            error_payload = AIOMCPServer._error(None, -32700, f"Parse error: {exc.msg}")
            _write_message(error_payload)
            continue

        response = server.handle_message(message)
        if response is None:
            continue
        if isinstance(response, list):
            for item in response:
                _write_message(item)
            continue
        _write_message(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
