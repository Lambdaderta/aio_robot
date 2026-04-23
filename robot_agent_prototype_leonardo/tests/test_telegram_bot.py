from __future__ import annotations

import unittest

from robot_agent_prototype_leonardo.telegram_bot import AIOTelegramBot, TelegramAssistantRuntime


class FakeBackend:
    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict]] = []

    def resolve_runtime(self):
        return (
            {
                "base_url": "http://127.0.0.1:1234/v1",
                "model": "google/gemma-4-e4b",
                "temperature": 0.2,
                "tools_enabled": True,
                "system_prompt": "You are AIO.",
            },
            [
                {
                    "type": "function",
                    "function": {
                        "name": "get_robot_status",
                        "description": "Read robot status.",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                }
            ],
        )

    def call_tool(self, name: str, arguments: dict):
        self.tool_calls.append((name, arguments))
        return {"ok": True, "robot_state": {"arm_online": True}}


class FakeLMClient:
    def __init__(self, responses, payload_log) -> None:
        self.responses = list(responses)
        self.payload_log = payload_log

    def chat_completion(self, payload):
        self.payload_log.append(payload)
        return self.responses.pop(0)


class TelegramAssistantRuntimeTests(unittest.TestCase):
    def test_runtime_executes_tool_call_roundtrip(self) -> None:
        payload_log = []
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_robot_status", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Рука подключена и готова к работе.",
                        }
                    }
                ]
            },
        ]
        backend = FakeBackend()
        runtime = TelegramAssistantRuntime(
            backend=backend,
            lm_client_factory=lambda _base_url: FakeLMClient(responses, payload_log),
        )

        reply = runtime.reply_to_turn("chat-1", "Покажи статус")

        self.assertEqual("Рука подключена и готова к работе.", reply)
        self.assertEqual([("get_robot_status", {})], backend.tool_calls)
        self.assertEqual("Покажи статус", runtime.histories["chat-1"][0]["content"])
        self.assertEqual("Рука подключена и готова к работе.", runtime.histories["chat-1"][1]["content"])

    def test_runtime_passes_photo_as_image_url_content(self) -> None:
        payload_log = []
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "На фото виден манипулятор AIO.",
                        }
                    }
                ]
            }
        ]
        runtime = TelegramAssistantRuntime(
            backend=FakeBackend(),
            lm_client_factory=lambda _base_url: FakeLMClient(responses, payload_log),
        )

        reply = runtime.reply_to_turn("chat-2", "Что на фото?", image_bytes=b"\xff\xd8\xff", image_mime="image/jpeg")

        self.assertEqual("На фото виден манипулятор AIO.", reply)
        user_message = payload_log[0]["messages"][-1]
        self.assertEqual("user", user_message["role"])
        self.assertEqual("text", user_message["content"][0]["type"])
        self.assertEqual("image_url", user_message["content"][1]["type"])
        self.assertTrue(user_message["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_hardware_keyboard_contains_connect_and_disconnect_actions(self) -> None:
        keyboard = AIOTelegramBot._build_hardware_menu_keyboard(
            [{"device": "/dev/cu.usbmodem1401", "description": "Arduino Leonardo"}],
            {"hardware_connected": True},
        )

        flat_buttons = [button for row in keyboard["inline_keyboard"] for button in row]
        callback_data = {button["callback_data"] for button in flat_buttons}

        self.assertIn("hw:connect:/dev/cu.usbmodem1401", callback_data)
        self.assertIn("hw:refresh", callback_data)
        self.assertIn("hw:status", callback_data)
        self.assertIn("hw:disconnect", callback_data)


if __name__ == "__main__":
    unittest.main()
