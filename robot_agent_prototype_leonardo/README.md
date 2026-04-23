# AIO Reference Implementation: Arduino Leonardo Arm

This directory contains the current reference implementation of AIO.

It is the concrete stack that powers the existing robotic arm setup, but it is also the working baseline for a more general local agent system that can later support other robots, other transports, and future perception-and-action models.

## What This Implementation Includes

- FastAPI backend for robot state, hardware connection, chat runtime, and assistant tools
- React/Vite frontend for chat, hardware control, vision control, and LM Studio setup
- LM Studio integration for local LLM inference and tool calling
- Telegram bot that reuses the same assistant backend and robot tools
- stdio MCP server for external clients such as Codex or Claude
- Arduino Leonardo serial protocol and reference firmware

## Current Capabilities

- connect to Arduino over serial
- control `base`, `shoulder`, and `gripper` in calibrated logical angles
- use backend-calibrated presets such as `HOME`, `LIFT`, `PARK`, `LEFT`, `CENTER`, and `RIGHT`
- route local LLM tool calls into real robot actions
- accept web chat input, Telegram text, Telegram photos, and Telegram voice messages
- expose robot actions and runtime controls through MCP
- run browser-based arm tracking for `base` and `shoulder`

## Runtime Components

The current implementation is split into five practical layers:

1. `backend/`
   The control layer, FastAPI API, shared tool layer, chat runtime config, and LM Studio process helpers.
2. `frontend/`
   The web client for direct human interaction, manual control, and browser vision.
3. `mcp_server.py`
   A stdio MCP bridge for external agent clients.
4. `telegram_bot.py`
   A Telegram interface that reuses the same backend and safe tool dispatch layer.
5. `arduino/robot_arm_serial_controller/`
   The current firmware for the reference robotic arm.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..
python run.py
```

After startup:

- frontend: `http://127.0.0.1:5173`
- backend: `http://127.0.0.1:8000`
- API docs: `http://127.0.0.1:8000/docs`

## Running With Telegram

The project supports a local `.env` file. A template lives in [.env.example](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/.env.example:1).

To run the Telegram bot next to the backend and frontend:

```bash
source .venv/bin/activate
python run.py --telegram-bot
```

Useful Telegram commands:

- `/start` — start a fresh conversation
- `/reset` — clear Telegram-side assistant history for the current chat
- `/hardware` or `/ports` — list serial ports and use buttons to connect or disconnect Arduino

## Running With MCP

From the project directory:

```bash
source .venv/bin/activate
python mcp_server.py
```

From the repository root:

```bash
source robot_agent_prototype_leonardo/.venv/bin/activate
python -m robot_agent_prototype_leonardo.mcp_server
```

The MCP bridge talks to the existing backend instead of opening serial on its own. This keeps one control authority for the robot and avoids multiple processes fighting over the same Arduino connection.

## LM Studio

The currently tested model in this repository is:

- `google/gemma-4-e4b`

Start the LM Studio server and load the model:

```bash
~/.lmstudio/bin/lms server start
~/.lmstudio/bin/lms load google/gemma-4-e4b
```

The frontend expects:

- Base URL: `http://127.0.0.1:1234/v1`
- Model identifier: `google/gemma-4-e4b`

Notes:

- Telegram photo support depends on the loaded model supporting image input.
- Telegram voice transcription depends on `faster-whisper`.
- Voice replies depend on macOS `say` and `afconvert`.

## Reference Robot Model

The current control service is calibrated for a specific reference arm.

Important details:

- `base` lives in logical angles `-90..90`
- logical `base=0` is the upright center position
- `shoulder=90` is the current upright reference
- all UI controls, assistant tools, Telegram actions, and MCP robot controls work in logical angles
- raw Arduino angles are translated inside [backend/control/service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/service.py:1)

Current calibration assumptions:

- hardware `base=30` maps to logical `0`
- hardware `base=0` maps to logical `-90`
- hardware `base=180` maps to logical `90`

## Hardware Bring-Up Checklist

When using the current Leonardo arm:

1. Connect the board over USB.
2. Open the web UI or Telegram `/hardware`.
3. Select the serial port and connect at `115200`.
4. Confirm that `base=0` corresponds to the upright center.
5. Confirm that `shoulder=90` matches the expected neutral pose.
6. Test a small `base` move before trying presets or autonomous control.
7. Start LM Studio and load a model before using chat, Telegram, or MCP assistants.

## Tests

Run the test suite from the repository root:

```bash
source robot_agent_prototype_leonardo/.venv/bin/activate
python -m unittest discover -s robot_agent_prototype_leonardo/tests -v
```

Or from this directory:

```bash
source .venv/bin/activate
PYTHONPATH=.. python -m unittest discover -s tests -v
```

The current tests cover:

- logical-to-hardware angle mapping
- backend tool guardrails
- chat tool-call extraction and tool loop
- MCP initialize, tools/list, and tool dispatch
- Telegram assistant runtime tool loop and image message handling

## Key Files

- [backend/main.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/main.py:1) — HTTP API surface
- [backend/control/service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/service.py:1) — logical robot control model
- [backend/control/serial_adapter.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/serial_adapter.py:1) — Leonardo serial transport
- [backend/chat_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/chat_service.py:1) — local LLM tool loop used by the web app
- [frontend/src/App.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/App.jsx:1) — main web client
- [frontend/src/VisionControl.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/VisionControl.jsx:1) — vision tab and send loop
- [mcp_server.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/mcp_server.py:1) — external agent bridge
- [telegram_bot.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/telegram_bot.py:1) — Telegram runtime

## Adapting This Stack To Another Robot

The fastest way to make AIO work with another robot is not to rewrite the assistant layer. The right place to adapt is the robot control interface.

Use:

- [docs/ROBOT_API_ADAPTER_GUIDE.md](/Users/lambda/projects/aio_robot/docs/ROBOT_API_ADAPTER_GUIDE.md:1)
- [docs/ARCHITECTURE.md](/Users/lambda/projects/aio_robot/docs/ARCHITECTURE.md:1)

Those guides explain which files define the current adapter, what needs to change, and how to keep the rest of the stack unchanged.

## Known Limits Of The Reference Implementation

- the current firmware and control model are still tied to the Leonardo arm
- browser vision currently maps only `base` and `shoulder`
- the assistant layer is tool-based, not a trained VLA policy
- real hardware tests are still manual and are not part of CI
- Telegram photo support depends on the selected local model

This is a reference implementation, not the final scope of AIO. The current value of the repository is that the assistant layer, hardware layer, and external integrations are already separated enough to evolve toward a broader robotics platform.
