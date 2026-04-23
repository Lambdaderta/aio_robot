# AIO Architecture

## Purpose

AIO is structured as a layered local robotics stack.

The central idea is simple:

- the assistant layer should not be hard-coded to one robot
- the robot control layer should not be hard-coded to one client
- transports, user interfaces, and models should be replaceable without rewriting the whole system

The current repository already follows that split in a practical way.

## High-Level Layers

### 1. Robot Control Layer

Files:

- [backend/control/service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/service.py:1)
- [backend/control/serial_adapter.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/serial_adapter.py:1)

Responsibilities:

- define logical joint limits and logical presets
- map logical angles to hardware angles
- talk to the physical controller
- refresh robot state from hardware telemetry
- keep one place where transport-specific control lives

This is the main adapter boundary for other robots.

### 2. Backend API Layer

File:

- [backend/main.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/main.py:1)

Responsibilities:

- expose HTTP endpoints for robot state and manual control
- expose runtime configuration for the local LLM
- expose a shared assistant-tool API for non-web clients

The backend is the stable shared boundary for:

- the web UI
- Telegram
- MCP clients

### 3. Assistant Tool Layer

File:

- [backend/chat_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/chat_service.py:1)

Responsibilities:

- define tool schemas for the assistant
- run the LM Studio chat loop for the web client
- guard direct tool motions with small-move checks
- keep tool semantics separate from the transport details

The important point is that AIO does not expose raw serial commands to the LLM. The model sees named tools and logical angles, not low-level device protocol.

### 4. Client Layers

Files:

- [frontend/src/App.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/App.jsx:1)
- [frontend/src/VisionControl.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/VisionControl.jsx:1)
- [telegram_bot.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/telegram_bot.py:1)
- [mcp_server.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/mcp_server.py:1)

Responsibilities:

- present the system through different interfaces
- keep the same backend semantics across all clients
- avoid reimplementing hardware logic inside each client

The web client is the richest interface today.

Telegram is useful as a lightweight remote client.

MCP is useful as an agent bridge for external tools such as Codex or Claude.

### 5. Firmware Layer

File:

- [arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino:1)

Responsibilities:

- interpret the current serial protocol
- move the physical servos
- report current state back to the backend

## Current Data Flow

### Manual Web Control

1. The web client sends a request to `/api/manual/joint` or `/api/manual/pose`.
2. The backend validates logical angles.
3. The control service maps logical angles to hardware angles.
4. The serial adapter sends the command to Arduino.
5. The backend refreshes robot state and returns updated telemetry.

### Web Chat

1. The web UI posts a user message to `/api/chat/sessions/{id}/messages`.
2. The backend builds an LM Studio chat payload.
3. The model either replies directly or emits tool calls.
4. The backend executes the tools through the shared control layer.
5. Tool results are appended into the conversation.
6. The model gets another round and produces the final reply.

### Telegram

1. `telegram_bot.py` polls the Telegram Bot API.
2. The bot converts Telegram input into a local assistant turn.
3. Text, photo, or voice is turned into an LM Studio message.
4. Tool calls are executed through `/api/assistant/tools/call`.
5. The bot sends the final answer back as text and optionally audio.

### MCP

1. An external client starts [mcp_server.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/mcp_server.py:1).
2. MCP tools are exposed over stdio JSON-RPC.
3. Tool calls are forwarded to the running AIO backend.
4. The backend remains the single control authority for the robot.

## Why This Architecture Matters

The current system is still tied to one reference arm, but the repository is no longer structured like a single hard-coded prototype.

What is already reusable:

- the assistant-facing tool model
- the FastAPI layer
- the LM Studio integration pattern
- the Telegram client
- the MCP bridge

What is still reference-hardware-specific:

- joint names and calibration values in `ControlService`
- the current serial transport
- the current Arduino protocol
- the current browser vision mapping assumptions

## Safety Model

The stack uses several soft guardrails:

- logical limits per joint
- calibrated presets
- guarded direct moves in the assistant tool layer
- transport isolation through the backend
- one control authority instead of multiple raw serial clients

This is not a certified robotics safety model. It is a practical software safety layer for development and experimentation.

## Extension Points

The cleanest places to extend AIO are:

- robot transport and command mapping in `backend/control/`
- model routing and skill orchestration above the current chat layer
- alternative frontends that reuse the backend API
- alternative perception pipelines that produce the same logical pose space

For adapting AIO to another robot, use [ROBOT_API_ADAPTER_GUIDE.md](/Users/lambda/projects/aio_robot/docs/ROBOT_API_ADAPTER_GUIDE.md:1).
