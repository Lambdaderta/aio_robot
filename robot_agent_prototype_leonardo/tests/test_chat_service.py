from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from robot_agent_prototype_leonardo.backend.chat_service import ChatService, LMStudioClient
from robot_agent_prototype_leonardo.backend.models import JointLimit, RobotState, UpdateChatRuntimeConfigRequest
from robot_agent_prototype_leonardo.backend.state import app_state


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ChatService()
        self.original_state = app_state.get_robot_state()
        app_state.update_robot_state(
            arm_online=True,
            controller_connected=True,
            hardware_connected=True,
            hardware_port="/dev/cu.usbmodem-test",
            baud_rate=115200,
            telemetry_source="arduino",
            controller_state="idle",
            joints={"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
            last_error=None,
            last_serial_message=None,
        )
        self.service.update_config(UpdateChatRuntimeConfigRequest(model="google/gemma-4-e4b"))

    def tearDown(self) -> None:
        app_state.update_robot_state(**self.original_state.model_dump())

    def test_fenced_tool_block_is_extracted_when_native_tool_calls_are_missing(self) -> None:
        message = self.service._build_assistant_message(
            {
                "content": '```tool\n{"name":"move_joint","arguments":{"joint_name":"base","angle":100}}\n```',
            }
        )

        self.assertEqual(1, len(message.tool_calls))
        self.assertEqual("move_joint", message.tool_calls[0].function.name)
        self.assertEqual(
            {"joint_name": "base", "angle": 100},
            json.loads(message.tool_calls[0].function.arguments),
        )

    def test_large_direct_move_is_blocked_before_dispatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "too large for safe direct control"):
            self.service._dispatch_tool("move_joint", {"joint_name": "base", "angle": 140})

    def test_unsupported_preset_is_rejected_by_allowlist(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe preset allowlist"):
            self.service._dispatch_tool("run_preset", {"preset": "DEMO"})

    def test_open_camera_tool_returns_ui_action(self) -> None:
        result = self.service._dispatch_tool("open_camera", {})

        self.assertEqual({"ok": True, "ui_action": "open_camera"}, result)

    def test_external_tool_specs_hide_ui_only_tools(self) -> None:
        tools = self.service.get_tool_specs(include_ui_tools=False)
        names = {tool["function"]["name"] for tool in tools}

        self.assertNotIn("open_camera", names)
        self.assertNotIn("close_camera", names)
        self.assertIn("move_joint", names)

    def test_external_tool_dispatch_rejects_ui_only_tools(self) -> None:
        with self.assertRaisesRegex(ValueError, "UI-only"):
            self.service.dispatch_external_tool("open_camera", {})

    def test_post_user_message_runs_tool_roundtrip(self) -> None:
        first_completion = {
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
        }
        second_completion = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Robot status checked and the arm is online.",
                        "tool_calls": [],
                    }
                }
            ]
        }
        fake_state = RobotState(
            arm_online=True,
            controller_connected=True,
            hardware_connected=True,
            hardware_port="/dev/cu.usbmodem-test",
            baud_rate=115200,
            telemetry_source="arduino",
            controller_state="idle",
            joints={"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
        )
        fake_limits = {
            "base": JointLimit(name="base", min_angle=-90, max_angle=90, default_angle=0),
            "shoulder": JointLimit(name="shoulder", min_angle=0, max_angle=180, default_angle=90),
            "gripper": JointLimit(name="gripper", min_angle=0, max_angle=90, default_angle=45),
        }

        session = self.service.create_session("Smoke")

        with patch.object(LMStudioClient, "chat_completion", side_effect=[first_completion, second_completion]) as chat_completion:
            with patch("robot_agent_prototype_leonardo.backend.chat_service.control_service.refresh_status", return_value=fake_state):
                with patch("robot_agent_prototype_leonardo.backend.chat_service.control_service.get_joint_limits", return_value=fake_limits):
                    with patch(
                        "robot_agent_prototype_leonardo.backend.chat_service.control_service.get_supported_presets",
                        return_value=("HOME", "LIFT", "PARK"),
                    ):
                        updated = self.service.post_user_message(session.id, "Покажи статус руки")

        self.assertEqual(2, chat_completion.call_count)
        self.assertEqual(["user", "assistant", "tool", "assistant"], [item.role for item in updated.messages])
        self.assertEqual("get_robot_status", updated.messages[2].name)
        self.assertIn("online", updated.messages[-1].content)


if __name__ == "__main__":
    unittest.main()
