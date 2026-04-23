# AIO Integrations

This document summarizes the integration surfaces that already exist in the repository.

## Web Interface

Files:

- [frontend/src/App.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/App.jsx:1)
- [frontend/src/VisionControl.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/VisionControl.jsx:1)
- [frontend/src/vision.js](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/vision.js:1)

The web client is the richest interface today.

It covers:

- local chat
- hardware connection
- manual control
- browser vision
- LM Studio runtime setup

Use the web client when you want direct inspection of robot state, manual testing, or camera-based interaction inside the browser.

## LM Studio

Files:

- [backend/chat_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/chat_service.py:1)
- [backend/lmstudio_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/lmstudio_service.py:1)

LM Studio currently provides:

- local chat completions
- tool-calling runtime for the web client
- model lifecycle helpers for the frontend
- a local inference backend for the Telegram bot

The current repository assumes an OpenAI-compatible local endpoint.

## Telegram

File:

- [telegram_bot.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/telegram_bot.py:1)

The Telegram bot is useful when:

- the web UI is not convenient
- you want a lightweight remote interface
- you want text, voice, and photo interaction from a phone

Current features:

- text messages
- voice messages through local transcription
- photo messages through the same local LLM path
- robot tool access through the backend
- hardware connect buttons through `/hardware` or `/ports`

Important constraints:

- the backend must already be running
- the selected local model must support image input if you want photo reasoning
- voice transcription depends on `faster-whisper`

## MCP

File:

- [mcp_server.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/mcp_server.py:1)

The MCP bridge exists so external assistants can use AIO as a tool provider.

That means:

- Codex can call robot tools through MCP
- Claude or any other MCP-capable client can do the same
- voice, local reasoning, and robot control can be composed outside the web UI

The bridge is intentionally thin. It forwards actions to the backend instead of owning robot state itself.

## Arduino Firmware

File:

- [arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino:1)

The firmware is still part of the system boundary.

Today it is the concrete endpoint for:

- `PING`
- `STATUS`
- `SET`
- `PRESET`
- `STOP`

If another robot uses another firmware or transport, that replacement should happen under the control layer, not in the higher-level clients.

## Why These Integrations Matter

The repository is already more than a single UI.

It has:

- one shared backend contract
- multiple user-facing clients
- one external agent bridge
- one reference firmware path

That is the foundation needed to turn AIO into a broader open-source robotics agent system rather than a one-off application.
