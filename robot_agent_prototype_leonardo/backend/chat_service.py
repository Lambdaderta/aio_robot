from __future__ import annotations

import json
import re
from datetime import datetime
from threading import Lock
from urllib import error, request
from uuid import uuid4

from .control.service import control_service
from .lmstudio_service import LMStudioServiceError, lmstudio_service
from .models import (
    AssistantToolCall,
    AssistantToolFunction,
    ChatMessage,
    ChatModelInfo,
    ChatRuntimeConfig,
    ChatSession,
    ChatSessionSummary,
    UpdateChatRuntimeConfigRequest,
)
from .state import app_state

DEFAULT_SYSTEM_PROMPT = """
You are AIO, a cautious local robot assistant for a robotic system.

Rules:
- Prefer short, direct answers.
- Use tools for robot motion, robot status, and presets.
- You can also open or close the camera preview in the UI when the user asks for webcam or vision tracking.
- Keep movements small and safe.
- Never claim that a movement happened unless you got a tool result.
- If the user asks for a robot action, use a tool instead of describing the action.
- Base uses logical angles from -90 to 90, and 0 means the upright center position.
- The available presets are backend-calibrated poses, not old firmware presets.

Native tool calling is preferred. If the model cannot emit a native tool call, emit exactly one fenced block in this format:
```tool
{"name":"move_joint","arguments":{"joint_name":"base","angle":15}}
```
""".strip()

SAFE_PRESETS = ("HOME", "LIFT", "PARK", "LEFT", "CENTER", "RIGHT")
MAX_DIRECT_JOINT_DELTA = 35.0
MAX_TOOL_ROUNDS = 3
TOOL_BLOCK_PATTERNS = (
    re.compile(r"```tool\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL),
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.IGNORECASE | re.DOTALL),
)


def _now() -> datetime:
    return datetime.utcnow()


def _normalize_message_content(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(value)


class LMStudioClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _post_json(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._url(path),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_json(req)

    def _get_json(self, path: str) -> dict:
        req = request.Request(self._url(path), headers={"Content-Type": "application/json"}, method="GET")
        return self._read_json(req)

    def _read_json(self, req: request.Request) -> dict:
        try:
            with request.urlopen(req, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("error", {}).get("message") or payload.get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(detail) from exc
        except error.URLError as exc:
            raise RuntimeError(f"Could not reach LM Studio at {self.base_url}: {exc.reason}") from exc

    def chat_completion(self, payload: dict) -> dict:
        return self._post_json("/chat/completions", payload)

    def list_models(self) -> list[ChatModelInfo]:
        payload = self._get_json("/models")
        models = payload.get("data") or []
        return [
            ChatModelInfo(
                id=str(item.get("id", "")),
                owned_by=item.get("owned_by"),
                object=str(item.get("object", "model")),
            )
            for item in models
            if item.get("id")
        ]


class ChatService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, ChatSession] = {}
        self._config = ChatRuntimeConfig(system_prompt=DEFAULT_SYSTEM_PROMPT)

    def get_config(self) -> ChatRuntimeConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def update_config(self, payload: UpdateChatRuntimeConfigRequest) -> ChatRuntimeConfig:
        with self._lock:
            current = self._config.model_dump()
            for field_name, value in payload.model_dump(exclude_none=True).items():
                current[field_name] = value
            self._config = ChatRuntimeConfig(**current)
            return self._config.model_copy(deep=True)

    def list_sessions(self) -> list[ChatSessionSummary]:
        with self._lock:
            sessions = [self._build_summary(session) for session in self._sessions.values()]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def create_session(self, title: str | None = None) -> ChatSession:
        now = _now()
        session = ChatSession(
            id=str(uuid4()),
            title=(title or "New chat").strip() or "New chat",
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[session.id] = session
        return session.model_copy(deep=True)

    def get_session(self, session_id: str) -> ChatSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            return session.model_copy(deep=True)

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            del self._sessions[session_id]

    def list_models(self) -> list[ChatModelInfo]:
        config = self.get_config()
        client = LMStudioClient(config.base_url)
        return client.list_models()

    def ensure_runtime_model(self, preferred_model: str | None = None) -> str:
        model = (preferred_model or self.get_config().model or "").strip()
        if model:
            with self._lock:
                current = self._config.model_dump()
                if current.get("model") != model:
                    current["model"] = model
                    self._config = ChatRuntimeConfig(**current)
            return model

        try:
            runtime = lmstudio_service.get_runtime()
        except LMStudioServiceError as exc:
            raise ValueError("Configure or load an LM Studio model first") from exc

        loaded_models = runtime.get("loaded_models") or []
        if not loaded_models:
            raise ValueError("Configure or load an LM Studio model first")

        loaded_model = loaded_models[0]
        resolved_model = (
            str(loaded_model.get("identifier") or "")
            or str(loaded_model.get("modelKey") or "")
            or str(loaded_model.get("path") or "")
        ).strip()
        if not resolved_model:
            raise ValueError("LM Studio reported a loaded model, but no usable identifier was returned")

        with self._lock:
            current = self._config.model_dump()
            current["model"] = resolved_model
            self._config = ChatRuntimeConfig(**current)

        return resolved_model

    def get_tool_specs(self, include_ui_tools: bool = True) -> list[dict]:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_robot_status",
                    "description": "Read the current robot state, hardware status, joint angles, and presets.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_joint",
                    "description": "Move one joint by sending a single safe angle to the robot. Base uses logical angles from -90 to 90 and 0 is upright center.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "joint_name": {"type": "string", "enum": ["base", "shoulder", "gripper"]},
                            "angle": {"type": "number"},
                        },
                        "required": ["joint_name", "angle"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "apply_pose",
                    "description": "Move multiple joints to a small, safe pose update.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "joints": {
                                "type": "object",
                                "properties": {
                                    "base": {"type": "number"},
                                    "shoulder": {"type": "number"},
                                    "gripper": {"type": "number"},
                                },
                                "additionalProperties": False,
                            }
                        },
                        "required": ["joints"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_preset",
                    "description": "Run a backend-calibrated safe pose on the robot.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "preset": {"type": "string", "enum": list(SAFE_PRESETS)},
                        },
                        "required": ["preset"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "stop_robot",
                    "description": "Immediately send STOP to the robot.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
        ]
        if include_ui_tools:
            tools[4:4] = [
                {
                    "type": "function",
                    "function": {
                        "name": "open_camera",
                        "description": "Open the vision tab and start the camera preview in the UI.",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "close_camera",
                        "description": "Stop the camera preview in the UI.",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                },
            ]
        return tools

    def post_user_message(self, session_id: str, content: str) -> ChatSession:
        message_text = content.strip()
        if not message_text:
            raise ValueError("Message content is empty")

        config = self.get_config()
        resolved_model = self.ensure_runtime_model(config.model)
        if config.model != resolved_model:
            config = self.get_config()

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            user_message = ChatMessage(
                id=str(uuid4()),
                role="user",
                content=message_text,
                created_at=_now(),
            )
            session.messages.append(user_message)
            if session.title == "New chat" and len(session.messages) == 1:
                session.title = self._summarize_title(message_text)
            session.updated_at = user_message.created_at

        self._run_model_loop(session_id, config)
        return self.get_session(session_id)

    def _run_model_loop(self, session_id: str, config: ChatRuntimeConfig) -> None:
        client = LMStudioClient(config.base_url)

        for _ in range(MAX_TOOL_ROUNDS):
            session = self.get_session(session_id)
            payload = {
                "model": config.model,
                "messages": self._build_openai_messages(session, config),
                "temperature": config.temperature,
            }
            if config.tools_enabled:
                payload["tools"] = self.get_tool_specs()
                payload["tool_choice"] = "auto"

            completion = client.chat_completion(payload)
            choice = (completion.get("choices") or [{}])[0]
            response_message = choice.get("message") or {}
            assistant_message = self._build_assistant_message(response_message)
            self._append_message(session_id, assistant_message)

            if not assistant_message.tool_calls or not config.tools_enabled:
                return

            tool_messages = [self._execute_tool_call(tool_call) for tool_call in assistant_message.tool_calls]
            for tool_message in tool_messages:
                self._append_message(session_id, tool_message)

        closing_message = ChatMessage(
            id=str(uuid4()),
            role="assistant",
            content="I stopped after several tool rounds to keep the robot safe.",
            created_at=_now(),
        )
        self._append_message(session_id, closing_message)

    def _build_openai_messages(self, session: ChatSession, config: ChatRuntimeConfig) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": config.system_prompt}]
        for item in session.messages:
            if item.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.tool_call_id,
                        "name": item.name,
                        "content": item.content,
                    }
                )
                continue

            payload = {"role": item.role, "content": item.content}
            if item.tool_calls:
                payload["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in item.tool_calls
                ]
            messages.append(payload)
        return messages

    def _build_assistant_message(self, payload: dict) -> ChatMessage:
        content = _normalize_message_content(payload.get("content"))
        tool_calls = self._normalize_tool_calls(payload.get("tool_calls") or [])
        if not tool_calls and content:
            tool_calls = self._extract_tool_calls_from_content(content)

        return ChatMessage(
            id=str(uuid4()),
            role="assistant",
            content=content,
            created_at=_now(),
            tool_calls=tool_calls,
        )

    def _normalize_tool_calls(self, payload: list[dict]) -> list[AssistantToolCall]:
        normalized: list[AssistantToolCall] = []
        for item in payload:
            function = item.get("function") or {}
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            normalized.append(
                AssistantToolCall(
                    id=str(item.get("id") or uuid4()),
                    type="function",
                    function=AssistantToolFunction(
                        name=name,
                        arguments=str(function.get("arguments") or "{}"),
                    ),
                )
            )
        return normalized

    def _extract_tool_calls_from_content(self, content: str) -> list[AssistantToolCall]:
        extracted: list[AssistantToolCall] = []
        for pattern in TOOL_BLOCK_PATTERNS:
            for match in pattern.findall(content):
                try:
                    payload = json.loads(match)
                except Exception:
                    continue
                name = str(payload.get("name") or payload.get("tool") or "").strip()
                arguments = payload.get("arguments") or {}
                if not name:
                    continue
                extracted.append(
                    AssistantToolCall(
                        id=f"fallback_{uuid4().hex[:10]}",
                        type="function",
                        function=AssistantToolFunction(
                            name=name,
                            arguments=json.dumps(arguments, ensure_ascii=False),
                        ),
                    )
                )
        return extracted

    def _execute_tool_call(self, tool_call: AssistantToolCall) -> ChatMessage:
        try:
            arguments = json.loads(tool_call.function.arguments or "{}")
        except Exception as exc:
            result = {"ok": False, "error": f"Invalid tool arguments: {exc}"}
        else:
            try:
                result = self._dispatch_tool(tool_call.function.name, arguments)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}

        return ChatMessage(
            id=str(uuid4()),
            role="tool",
            name=tool_call.function.name,
            tool_call_id=tool_call.id,
            content=json.dumps(result, ensure_ascii=False),
            created_at=_now(),
        )

    def _dispatch_tool(self, tool_name: str, arguments: dict) -> dict:
        if tool_name == "get_robot_status":
            return self._tool_get_robot_status()
        if tool_name == "move_joint":
            return self._tool_move_joint(arguments)
        if tool_name == "apply_pose":
            return self._tool_apply_pose(arguments)
        if tool_name == "run_preset":
            return self._tool_run_preset(arguments)
        if tool_name == "open_camera":
            return self._tool_open_camera()
        if tool_name == "close_camera":
            return self._tool_close_camera()
        if tool_name == "stop_robot":
            return self._tool_stop_robot()
        raise ValueError(f"Unknown tool '{tool_name}'")

    def dispatch_external_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        normalized_arguments = arguments or {}
        if not isinstance(normalized_arguments, dict):
            raise ValueError("Tool arguments must be an object")
        if tool_name in {"open_camera", "close_camera"}:
            raise ValueError(f"Tool '{tool_name}' is UI-only and is not available for external assistants")
        return self._dispatch_tool(tool_name, normalized_arguments)

    def _tool_get_robot_status(self) -> dict:
        state = control_service.refresh_status()
        return {
            "ok": True,
            "robot_state": state.model_dump(mode="json"),
            "joint_limits": {name: item.model_dump(mode="json") for name, item in control_service.get_joint_limits().items()},
            "supported_presets": list(control_service.get_supported_presets()),
        }

    def _tool_move_joint(self, arguments: dict) -> dict:
        joint_name = str(arguments.get("joint_name", "")).strip()
        angle = float(arguments.get("angle"))
        self._guard_small_move(joint_name, angle)

        valid, error_text = control_service.validate_joint_move(joint_name, angle)
        if not valid and error_text:
            raise ValueError(error_text)

        steps = control_service.execute_action("move_joint", {"joint_name": joint_name, "angle": angle})
        return self._tool_result_payload(steps)

    def _tool_apply_pose(self, arguments: dict) -> dict:
        joints = arguments.get("joints") or {}
        if not isinstance(joints, dict) or not joints:
            raise ValueError("Tool apply_pose requires a non-empty joints object")

        normalized: dict[str, float] = {}
        for joint_name, angle in joints.items():
            joint_key = str(joint_name).strip()
            target = float(angle)
            self._guard_small_move(joint_key, target)
            normalized[joint_key] = target

        steps = control_service.apply_joint_pose(normalized)
        return self._tool_result_payload(steps)

    def _tool_run_preset(self, arguments: dict) -> dict:
        preset = str(arguments.get("preset", "")).strip().upper()
        if preset not in SAFE_PRESETS:
            raise ValueError(f"Preset '{preset}' is not in the safe preset allowlist")
        steps = control_service.execute_manual_preset(preset)
        return self._tool_result_payload(steps)

    def _tool_open_camera(self) -> dict:
        return {"ok": True, "ui_action": "open_camera"}

    def _tool_close_camera(self) -> dict:
        return {"ok": True, "ui_action": "close_camera"}

    def _tool_stop_robot(self) -> dict:
        steps = control_service.execute_action("stop", {})
        return self._tool_result_payload(steps)

    def _tool_result_payload(self, steps) -> dict:
        return {
            "ok": not any(step.status in {"blocked", "failed"} for step in steps),
            "steps": [step.model_dump(mode="json") for step in steps],
            "robot_state": app_state.get_robot_state().model_dump(mode="json"),
        }

    def _guard_small_move(self, joint_name: str, target_angle: float) -> None:
        current_angle = app_state.get_robot_state().joints.get(joint_name)
        if current_angle is None:
            raise ValueError(f"Unknown joint '{joint_name}'")
        delta = abs(float(current_angle) - float(target_angle))
        if delta > MAX_DIRECT_JOINT_DELTA:
            raise ValueError(
                f"Requested move for {joint_name} is too large for safe direct control: "
                f"{delta:.1f} degrees. Keep direct tool moves within {MAX_DIRECT_JOINT_DELTA:.0f} degrees."
            )

    def _append_message(self, session_id: str, message: ChatMessage) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            session.messages.append(message)
            session.updated_at = message.created_at

    def _build_summary(self, session: ChatSession) -> ChatSessionSummary:
        return ChatSessionSummary(
            id=session.id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=len(session.messages),
        )

    @staticmethod
    def _summarize_title(content: str) -> str:
        words = content.strip().split()
        if not words:
            return "New chat"
        title = " ".join(words[:6])
        return title[:48]


chat_service = ChatService()
