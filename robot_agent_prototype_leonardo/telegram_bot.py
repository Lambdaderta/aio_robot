from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib import error, request

try:
    from .env_loader import load_local_env
except ImportError:  # pragma: no cover
    from env_loader import load_local_env


MAX_TOOL_ROUNDS = 3
MAX_HISTORY_MESSAGES = 16
TELEGRAM_POLL_TIMEOUT_SEC = 25
TOOL_BLOCK_PATTERNS = (
    re.compile(r"```tool\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL),
    re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.IGNORECASE | re.DOTALL),
)
TELEGRAM_SYSTEM_PROMPT_APPEND = """
You are replying inside a Telegram bot for AIO.

Extra rules for Telegram:
- There is no browser UI in this channel.
- The user can send text, voice messages, and photos directly here.
- Do not call UI-only tools such as opening or closing a camera tab.
- Keep answers compact and natural for chat.
""".strip()


class TelegramBotError(RuntimeError):
    pass


class LMStudioClientError(RuntimeError):
    pass


class AssistantBackendError(RuntimeError):
    pass


class VoiceTranscriptionError(RuntimeError):
    pass


class VoiceSynthesisError(RuntimeError):
    pass


def _normalize_message_content(value: Any) -> str:
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


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    value = (text or "").strip()
    if not value:
        return [""]
    if len(value) <= limit:
        return [value]

    chunks: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def _data_url(mime_type: str, file_bytes: bytes) -> str:
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class LMStudioClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("error", {}).get("message") or payload.get("detail") or detail
            except Exception:
                pass
            raise LMStudioClientError(str(detail)) from exc
        except error.URLError as exc:
            raise LMStudioClientError(f"Could not reach LM Studio at {self.base_url}: {exc.reason}") from exc


class AssistantBackendClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("AIO_BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")

    def resolve_runtime(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        chat_config_payload = self.get_json("/api/chat/config")
        config = dict(chat_config_payload.get("config") or {})
        if not config.get("model"):
            runtime = self.get_json("/api/chat/runtime")
            loaded_models = runtime.get("loaded_models") or []
            if loaded_models:
                first = loaded_models[0]
                config["model"] = (
                    str(first.get("identifier") or "")
                    or str(first.get("modelKey") or "")
                    or str(first.get("path") or "")
                ).strip()
        if not config.get("model"):
            raise AssistantBackendError("LM Studio model is not configured. Load a model in AIO first.")
        tools_payload = self.get_json("/api/assistant/tools")
        return config, list(tools_payload.get("tools") or [])

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.post_json("/api/assistant/tools/call", {"name": name, "arguments": arguments or {}})
        return dict(payload.get("result") or {})

    def get_json(self, path: str) -> dict[str, Any]:
        req = request.Request(self._url(path), headers={"Content-Type": "application/json"}, method="GET")
        return self._read_json(req)

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
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
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(detail)
                detail = payload.get("detail") or payload.get("error", {}).get("message") or detail
            except Exception:
                pass
            raise AssistantBackendError(str(detail)) from exc
        except error.URLError as exc:
            raise AssistantBackendError(f"Could not reach AIO backend at {self.base_url}: {exc.reason}") from exc


class TelegramBotAPI:
    def __init__(self, token: str) -> None:
        self.token = token.strip()
        if not self.token:
            raise TelegramBotError("Telegram bot token is empty")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.file_base_url = f"https://api.telegram.org/file/bot{self.token}"

    def get_updates(self, offset: int | None = None, timeout: int = TELEGRAM_POLL_TIMEOUT_SEC) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._post_json("getUpdates", payload)
        return list(result.get("result") or [])

    def send_message(self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None) -> None:
        for chunk in _chunk_text(text):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            self._post_json("sendMessage", payload)

    def send_chat_action(self, chat_id: int, action: str) -> None:
        self._post_json("sendChatAction", {"chat_id": chat_id, "action": action})

    def send_audio(self, chat_id: int, audio_path: Path, caption: str | None = None) -> None:
        mime_type = mimetypes.guess_type(audio_path.name)[0] or "audio/mp4"
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        self._post_multipart(
            "sendAudio",
            fields=fields,
            files=[("audio", audio_path.name, audio_path.read_bytes(), mime_type)],
        )

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post_json("answerCallbackQuery", payload)

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._post_json("editMessageText", payload)

    def get_file(self, file_id: str) -> dict[str, Any]:
        payload = self._post_json("getFile", {"file_id": file_id})
        return dict(payload.get("result") or {})

    def download_file(self, file_path: str) -> bytes:
        url = f"{self.file_base_url}/{file_path.lstrip('/')}"
        req = request.Request(url, method="GET")
        try:
            with request.urlopen(req, timeout=120) as response:
                return response.read()
        except error.URLError as exc:
            raise TelegramBotError(f"Could not download Telegram file: {exc.reason}") from exc

    def _post_json(self, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/{method_name}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read_json(req)

    def _post_multipart(self, method_name: str, fields: dict[str, str], files: list[tuple[str, str, bytes, str]]) -> dict[str, Any]:
        boundary = f"----aio{int(time.time() * 1000)}"
        body = bytearray()
        for field_name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode("utf-8"))
            body.extend(str(value).encode("utf-8"))
            body.extend(b"\r\n")
        for field_name, filename, file_bytes, mime_type in files:
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode("utf-8")
            )
            body.extend(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
            body.extend(file_bytes)
            body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        req = request.Request(
            f"{self.base_url}/{method_name}",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        return self._read_json(req)

    @staticmethod
    def _read_json(req: request.Request) -> dict[str, Any]:
        try:
            with request.urlopen(req, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TelegramBotError(detail or str(exc)) from exc
        except error.URLError as exc:
            raise TelegramBotError(f"Could not reach Telegram Bot API: {exc.reason}") from exc

        if not payload.get("ok", False):
            raise TelegramBotError(str(payload.get("description") or "Telegram Bot API returned an error"))
        return payload


class WhisperTranscriber:
    def __init__(self, model_name: str | None = None, language: str | None = None) -> None:
        self.model_name = model_name or os.environ.get("AIO_STT_MODEL") or "tiny"
        self.language = language or os.environ.get("AIO_STT_LANGUAGE")
        self._model = None

    def transcribe(self, audio_path: Path) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VoiceTranscriptionError(
                "Voice transcription requires faster-whisper. Reinstall Python dependencies for AIO."
            ) from exc

        if self._model is None:
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

        segments, _info = self._model.transcribe(str(audio_path), language=self.language, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments if segment.text and segment.text.strip()).strip()
        if not text:
            raise VoiceTranscriptionError("Could not recognize speech from the voice message")
        return text


class MacVoiceReplySynthesizer:
    def __init__(self, voice: str | None = None, rate: int | None = None) -> None:
        self.voice = voice or os.environ.get("AIO_TTS_VOICE")
        self.rate = int(rate or os.environ.get("AIO_TTS_RATE") or 180)
        self.say_binary = shutil.which("say")
        self.afconvert_binary = shutil.which("afconvert")

    def synthesize(self, text: str) -> Path:
        phrase = (text or "").strip()
        if not phrase:
            raise VoiceSynthesisError("Assistant reply is empty")
        if not self.say_binary or not self.afconvert_binary:
            raise VoiceSynthesisError("macOS speech tools 'say' and 'afconvert' are required for audio replies")

        temp_dir = Path(tempfile.mkdtemp(prefix="aio_tg_voice_"))
        aiff_path = temp_dir / "reply.aiff"
        m4a_path = temp_dir / "reply.m4a"

        say_command = [self.say_binary, "-o", str(aiff_path), "-r", str(self.rate)]
        if self.voice:
            say_command.extend(["-v", self.voice])
        say_command.append(phrase)

        convert_command = [self.afconvert_binary, "-f", "m4af", "-d", "aac", str(aiff_path), str(m4a_path)]

        try:
            subprocess.run(say_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(convert_command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.SubprocessError as exc:
            raise VoiceSynthesisError(f"Could not synthesize Telegram audio reply: {exc}") from exc
        finally:
            if aiff_path.exists():
                aiff_path.unlink(missing_ok=True)

        return m4a_path


class TelegramAssistantRuntime:
    def __init__(self, backend: AssistantBackendClient | None = None, lm_client_factory=None) -> None:
        self.backend = backend or AssistantBackendClient()
        self.lm_client_factory = lm_client_factory or LMStudioClient
        self.histories: dict[str, list[dict[str, Any]]] = {}

    def reset_chat(self, chat_key: str) -> None:
        self.histories.pop(chat_key, None)

    def reply_to_turn(
        self,
        chat_key: str,
        user_text: str,
        image_bytes: bytes | None = None,
        image_mime: str | None = None,
    ) -> str:
        config, tools = self.backend.resolve_runtime()
        lm_client = self.lm_client_factory(str(config["base_url"]))
        system_prompt = self._build_system_prompt(str(config.get("system_prompt") or ""))
        history = list(self.histories.get(chat_key, []))

        persistent_user_summary = self._build_persistent_user_summary(user_text, image_bytes is not None)
        working_messages = history + [self._build_user_message(user_text, image_bytes=image_bytes, image_mime=image_mime)]

        for _ in range(MAX_TOOL_ROUNDS):
            payload: dict[str, Any] = {
                "model": str(config["model"]),
                "messages": [{"role": "system", "content": system_prompt}, *working_messages],
                "temperature": float(config.get("temperature", 0.2)),
            }
            if config.get("tools_enabled", True) and tools:
                payload["tools"] = tools
                payload["tool_choice"] = "auto"

            completion = lm_client.chat_completion(payload)
            choice = (completion.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            assistant_content = _normalize_message_content(message.get("content"))
            tool_calls = self._extract_tool_calls(message)

            assistant_payload: dict[str, Any] = {"role": "assistant", "content": assistant_content}
            if tool_calls:
                assistant_payload["tool_calls"] = tool_calls
            working_messages.append(assistant_payload)

            if not tool_calls:
                final_text = assistant_content.strip() or "Done."
                self._remember_turn(chat_key, persistent_user_summary, final_text)
                return final_text

            for tool_call in tool_calls:
                result = self.backend.call_tool(tool_call["function"]["name"], tool_call["parsed_arguments"])
                working_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "name": tool_call["function"]["name"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        final_text = "I stopped after several tool rounds to keep the robot stable."
        self._remember_turn(chat_key, persistent_user_summary, final_text)
        return final_text

    @staticmethod
    def _build_system_prompt(base_prompt: str) -> str:
        prompt = base_prompt.strip()
        if prompt:
            return f"{prompt}\n\n{TELEGRAM_SYSTEM_PROMPT_APPEND}"
        return TELEGRAM_SYSTEM_PROMPT_APPEND

    @staticmethod
    def _build_persistent_user_summary(user_text: str, has_image: bool) -> str:
        text = user_text.strip() or "User sent a message."
        if has_image:
            return f"{text}\n[User attached a photo]"
        return text

    @staticmethod
    def _build_user_message(user_text: str, image_bytes: bytes | None = None, image_mime: str | None = None) -> dict[str, Any]:
        prompt_text = user_text.strip()
        if image_bytes is None:
            return {"role": "user", "content": prompt_text}

        if not prompt_text:
            prompt_text = "Describe the image and help the user."

        return {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": _data_url(image_mime or "image/jpeg", image_bytes)}},
            ],
        }

    def _remember_turn(self, chat_key: str, user_text: str, assistant_text: str) -> None:
        history = list(self.histories.get(chat_key, []))
        history.extend(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
        )
        self.histories[chat_key] = history[-MAX_HISTORY_MESSAGES:]

    def _extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls_payload = message.get("tool_calls") or []
        normalized = self._normalize_tool_calls(tool_calls_payload)
        if normalized:
            return normalized

        content = _normalize_message_content(message.get("content"))
        extracted: list[dict[str, Any]] = []
        for pattern in TOOL_BLOCK_PATTERNS:
            for match in pattern.findall(content):
                try:
                    payload = json.loads(match)
                except Exception:
                    continue
                tool_name = str(payload.get("name") or payload.get("tool") or "").strip()
                arguments = payload.get("arguments") or {}
                if not tool_name or not isinstance(arguments, dict):
                    continue
                extracted.append(
                    {
                        "id": f"fallback_{int(time.time() * 1000)}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                        "parsed_arguments": arguments,
                    }
                )
        return extracted

    @staticmethod
    def _normalize_tool_calls(tool_calls_payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for tool_call in tool_calls_payload:
            function = tool_call.get("function") or {}
            name = str(function.get("name") or "").strip()
            raw_arguments = function.get("arguments") or "{}"
            if not name:
                continue
            try:
                parsed_arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments)
            except Exception:
                continue
            if not isinstance(parsed_arguments, dict):
                continue
            normalized.append(
                {
                    "id": str(tool_call.get("id") or f"tool_{int(time.time() * 1000)}"),
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(parsed_arguments, ensure_ascii=False),
                    },
                    "parsed_arguments": parsed_arguments,
                }
            )
        return normalized


class AIOTelegramBot:
    def __init__(self) -> None:
        token = os.environ.get("AIO_TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise TelegramBotError("Set AIO_TELEGRAM_BOT_TOKEN in the environment before starting the Telegram bot")

        self.telegram = TelegramBotAPI(token)
        self.backend = AssistantBackendClient()
        self.runtime = TelegramAssistantRuntime(backend=self.backend)
        self.transcriber = WhisperTranscriber()
        self.synthesizer = MacVoiceReplySynthesizer()
        self.send_voice_for_voice_messages = os.environ.get("AIO_TELEGRAM_REPLY_VOICE_TO_VOICE", "1") != "0"
        self.always_send_voice = os.environ.get("AIO_TELEGRAM_ALWAYS_SEND_AUDIO", "0") == "1"
        self.skip_backlog_on_start = os.environ.get("AIO_TELEGRAM_SKIP_BACKLOG", "1") != "0"

    def run_forever(self) -> None:
        offset: int | None = None
        if self.skip_backlog_on_start:
            updates = self.telegram.get_updates(timeout=0)
            if updates:
                offset = int(updates[-1]["update_id"]) + 1

        print("AIO Telegram bot started.", file=sys.stderr)
        while True:
            try:
                updates = self.telegram.get_updates(offset=offset, timeout=TELEGRAM_POLL_TIMEOUT_SEC)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    self._process_update(update)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[aio-telegram] {exc}", file=sys.stderr)
                time.sleep(2)

    def _process_update(self, update: dict[str, Any]) -> None:
        callback_query = update.get("callback_query")
        if callback_query:
            self._process_callback_query(dict(callback_query))
            return

        message = update.get("message") or {}
        if not message:
            return

        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_key = str(chat_id)
        try:
            text = str(message.get("text") or "").strip()
            if text.startswith("/start"):
                self.runtime.reset_chat(chat_key)
                self.telegram.send_message(
                    chat_id,
                    "AIO bot is ready. Send text, a voice message, or a photo. Use /reset to clear the conversation.",
                )
                return
            if text.startswith("/reset"):
                self.runtime.reset_chat(chat_key)
                self.telegram.send_message(chat_id, "Conversation cleared.")
                return
            if text.startswith("/hardware") or text.startswith("/ports"):
                self._send_hardware_menu(chat_id)
                return

            user_text = text
            image_bytes: bytes | None = None
            image_mime: str | None = None
            wants_voice_reply = self.always_send_voice

            if message.get("voice"):
                voice = dict(message["voice"])
                self.telegram.send_chat_action(chat_id, "typing")
                user_text = self._transcribe_telegram_file(voice["file_id"], suggested_suffix=".ogg")
                wants_voice_reply = wants_voice_reply or self.send_voice_for_voice_messages
            elif message.get("photo"):
                photo = list(message["photo"])
                photo_item = dict(photo[-1])
                image_bytes = self._download_telegram_file(photo_item["file_id"])
                image_mime = "image/jpeg"
                user_text = str(message.get("caption") or "").strip()
            elif not text:
                self.telegram.send_message(chat_id, "Send text, a voice message, or a photo.")
                return

            self.telegram.send_chat_action(chat_id, "typing")
            reply_text = self.runtime.reply_to_turn(
                chat_key,
                user_text=user_text,
                image_bytes=image_bytes,
                image_mime=image_mime,
            )
            self.telegram.send_message(chat_id, reply_text)

            if wants_voice_reply:
                self._send_voice_reply(chat_id, reply_text)
        except Exception as exc:
            print(f"[aio-telegram] chat {chat_id}: {exc}", file=sys.stderr)
            self.telegram.send_message(chat_id, f"AIO bot error: {exc}")

    def _process_callback_query(self, callback_query: dict[str, Any]) -> None:
        query_id = str(callback_query.get("id") or "")
        data = str(callback_query.get("data") or "")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = message.get("message_id")

        if not query_id or chat_id is None:
            return

        try:
            if not data.startswith("hw:"):
                self.telegram.answer_callback_query(query_id, "Unknown action")
                return

            action, payload = self._parse_hardware_callback(data)

            if action == "refresh":
                self.telegram.answer_callback_query(query_id, "Hardware list refreshed")
                self._send_hardware_menu(chat_id, message_id=message_id)
                return

            if action == "disconnect":
                self.backend.post_json("/api/hardware/disconnect", {})
                self.telegram.answer_callback_query(query_id, "Arduino disconnected")
                self._send_hardware_menu(chat_id, message_id=message_id)
                return

            if action == "status":
                self.telegram.answer_callback_query(query_id, "Status refreshed")
                self._send_hardware_menu(chat_id, message_id=message_id)
                return

            if action == "connect":
                port = payload
                if not port:
                    raise TelegramBotError("Port is empty")
                self.backend.post_json("/api/hardware/connect", {"port": port, "baud_rate": 115200})
                self.telegram.answer_callback_query(query_id, f"Connected to {port}")
                self._send_hardware_menu(chat_id, message_id=message_id)
                return

            self.telegram.answer_callback_query(query_id, "Unsupported action")
        except Exception as exc:
            print(f"[aio-telegram] callback chat {chat_id}: {exc}", file=sys.stderr)
            self.telegram.answer_callback_query(query_id, str(exc)[:180])
            if chat_id is not None:
                self._send_hardware_menu(chat_id, message_id=message_id)

    @staticmethod
    def _parse_hardware_callback(data: str) -> tuple[str, str]:
        parts = data.split(":", 2)
        if len(parts) == 2:
            return parts[1], ""
        if len(parts) >= 3:
            return parts[1], parts[2]
        return "", ""

    def _send_hardware_menu(self, chat_id: int, message_id: int | None = None) -> None:
        status_payload = self.backend.get_json("/api/status")
        ports_payload = self.backend.get_json("/api/hardware/ports")

        robot_state = dict(status_payload.get("robot_state") or {})
        ports = list(ports_payload.get("ports") or [])

        text = self._build_hardware_menu_text(robot_state, ports)
        keyboard = self._build_hardware_menu_keyboard(ports, robot_state)

        if message_id is not None:
            self.telegram.edit_message_text(chat_id, message_id, text, reply_markup=keyboard)
            return
        self.telegram.send_message(chat_id, text, reply_markup=keyboard)

    @staticmethod
    def _build_hardware_menu_text(robot_state: dict[str, Any], ports: list[dict[str, Any]]) -> str:
        connected = bool(robot_state.get("hardware_connected"))
        port = robot_state.get("hardware_port") or "not connected"
        controller_state = robot_state.get("controller_state") or "idle"
        lines = [
            "AIO hardware control",
            "",
            f"Connected: {'yes' if connected else 'no'}",
            f"Port: {port}",
            f"Controller state: {controller_state}",
            "",
            "Available ports:",
        ]
        if not ports:
            lines.append("- no serial ports found")
        else:
            for item in ports:
                device = str(item.get("device") or "unknown")
                description = str(item.get("description") or "").strip()
                lines.append(f"- {device}" + (f" ({description})" if description else ""))
        return "\n".join(lines)

    @staticmethod
    def _build_hardware_menu_keyboard(ports: list[dict[str, Any]], robot_state: dict[str, Any]) -> dict[str, Any]:
        rows: list[list[dict[str, str]]] = []
        for item in ports:
            device = str(item.get("device") or "").strip()
            if not device:
                continue
            rows.append([{"text": f"Connect {device}", "callback_data": f"hw:connect:{device}"}])

        utility_row = [{"text": "Refresh", "callback_data": "hw:refresh"}, {"text": "Status", "callback_data": "hw:status"}]
        rows.append(utility_row)

        if robot_state.get("hardware_connected"):
            rows.append([{"text": "Disconnect", "callback_data": "hw:disconnect"}])

        return {"inline_keyboard": rows}

    def _download_telegram_file(self, file_id: str) -> bytes:
        file_info = self.telegram.get_file(file_id)
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            raise TelegramBotError("Telegram did not return a downloadable file path")
        return self.telegram.download_file(file_path)

    def _transcribe_telegram_file(self, file_id: str, suggested_suffix: str) -> str:
        file_info = self.telegram.get_file(file_id)
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            raise TelegramBotError("Telegram did not return a downloadable file path")

        file_bytes = self.telegram.download_file(file_path)
        temp_dir = Path(tempfile.mkdtemp(prefix="aio_tg_stt_"))
        audio_path = temp_dir / f"input{suggested_suffix}"
        try:
            audio_path.write_bytes(file_bytes)
            return self.transcriber.transcribe(audio_path)
        finally:
            if audio_path.exists():
                audio_path.unlink(missing_ok=True)
            temp_dir.rmdir()

    def _send_voice_reply(self, chat_id: int, reply_text: str) -> None:
        try:
            audio_path = self.synthesizer.synthesize(reply_text)
        except VoiceSynthesisError as exc:
            print(f"[aio-telegram] voice reply skipped: {exc}", file=sys.stderr)
            return

        try:
            self.telegram.send_chat_action(chat_id, "upload_voice")
            self.telegram.send_audio(chat_id, audio_path)
        finally:
            parent = audio_path.parent
            audio_path.unlink(missing_ok=True)
            parent.rmdir()


def main() -> int:
    load_local_env()
    bot = AIOTelegramBot()
    bot.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
