from __future__ import annotations

import unittest

from robot_agent_prototype_leonardo.mcp_server import AIOMCPServer, BackendAPIError


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def get_json(self, path: str) -> dict:
        self.calls.append(("GET", path, None))
        if path == "/api/status":
            return {
                "robot_state": {
                    "hardware_connected": True,
                    "joints": {"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
                }
            }
        if path == "/api/chat/models":
            return {"models": [{"id": "google/gemma-4-e4b"}]}
        raise BackendAPIError(f"Unexpected GET path: {path}")

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        self.calls.append(("POST", path, payload))
        if path == "/api/manual/joint":
            return {"ok": True, "robot_state": {"joints": {"base": payload["angle"]}}}
        if path == "/api/chat/runtime/load":
            return {"ok": True, "loaded_models": [{"identifier": payload["model"]}]}
        raise BackendAPIError(f"Unexpected POST path: {path}")


class FakeSpeaker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def speak(self, text: str, voice: str | None = None, rate: int | None = None, wait: bool = False) -> dict:
        payload = {"text": text, "voice": voice, "rate": rate, "wait": wait}
        self.calls.append(payload)
        return {"ok": True, **payload}


class FailingBackend:
    def get_json(self, path: str) -> dict:
        raise BackendAPIError("backend is offline")

    def post_json(self, path: str, payload: dict | None = None) -> dict:
        raise BackendAPIError("backend is offline")


class MCPServerTests(unittest.TestCase):
    def test_initialize_returns_tool_capability(self) -> None:
        server = AIOMCPServer(backend=FakeBackend(), speaker=FakeSpeaker())

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test"}},
            }
        )

        self.assertEqual("2025-11-25", response["result"]["protocolVersion"])
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual("aio-mcp", response["result"]["serverInfo"]["name"])

    def test_tools_list_exposes_robot_and_voice_tools(self) -> None:
        server = AIOMCPServer(backend=FakeBackend(), speaker=FakeSpeaker())

        response = server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        tool_names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("aio_get_status", tool_names)
        self.assertIn("aio_move_joint", tool_names)
        self.assertIn("aio_speak_text", tool_names)

    def test_move_joint_tool_routes_to_backend(self) -> None:
        backend = FakeBackend()
        server = AIOMCPServer(backend=backend, speaker=FakeSpeaker())

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "aio_move_joint", "arguments": {"joint_name": "base", "angle": 15}},
            }
        )

        self.assertEqual(("POST", "/api/manual/joint", {"joint_name": "base", "angle": 15.0}), backend.calls[-1])
        self.assertEqual("Joint base moved to 15.0 degrees.", response["result"]["content"][0]["text"])
        self.assertFalse(response["result"].get("isError", False))

    def test_backend_failures_are_returned_as_tool_errors(self) -> None:
        server = AIOMCPServer(backend=FailingBackend(), speaker=FakeSpeaker())

        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "aio_get_status", "arguments": {}}}
        )

        self.assertTrue(response["result"]["isError"])
        self.assertIn("backend is offline", response["result"]["content"][0]["text"])

    def test_speak_text_tool_uses_speaker(self) -> None:
        speaker = FakeSpeaker()
        server = AIOMCPServer(backend=FakeBackend(), speaker=speaker)

        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "aio_speak_text",
                    "arguments": {"text": "Привет", "voice": "Milena", "rate": 170, "wait": False},
                },
            }
        )

        self.assertEqual(
            {"text": "Привет", "voice": "Milena", "rate": 170, "wait": False},
            speaker.calls[-1],
        )
        self.assertEqual("Speech output started.", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
