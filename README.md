# AIO

AIO is a local-first open-source agent system for robots.

The long-term goal of the project is to provide one extensible stack that can:

- talk to a user through normal chat, voice, and external integrations
- call tools and use local models as skills
- control a physical robot through a replaceable robot adapter layer
- grow into a more capable perception-and-action system, including future VLA support

The current repository already contains a working reference implementation for a robotic arm based on Arduino Leonardo, together with LM Studio integration, a Telegram bot, and an MCP server for external clients such as Codex or Claude.

## Open-Source Scope

What is already in this repository:

- FastAPI backend for robot control, runtime management, and assistant tools
- React/Vite web client with chat, control, hardware, and vision tabs
- Telegram bot that can replace the web chat in a messaging workflow
- stdio MCP server for external agent clients
- Arduino sketch and reference control flow for the current arm
- tests for the control layer, chat tool loop, MCP server, and Telegram runtime

What is planned to be published later:

- CAD, STL, and hardware assembly assets
- mechanical and electrical build notes in final form
- future VLA training code
- future VLA model weights or release instructions
- additional robot adapters beyond the current Leonardo arm

## Repository Layout

- [robot_agent_prototype_leonardo/README.md](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/README.md:1) — current reference implementation and runtime guide
- [docs/ARCHITECTURE.md](/Users/lambda/projects/aio_robot/docs/ARCHITECTURE.md:1) — system architecture and control flow
- [docs/INTEGRATIONS.md](/Users/lambda/projects/aio_robot/docs/INTEGRATIONS.md:1) — web UI, Telegram, MCP, and LM Studio integration notes
- [docs/ROBOT_API_ADAPTER_GUIDE.md](/Users/lambda/projects/aio_robot/docs/ROBOT_API_ADAPTER_GUIDE.md:1) — how to adapt AIO to another robot API
- [docs/ROADMAP.md](/Users/lambda/projects/aio_robot/docs/ROADMAP.md:1) — current direction and planned open-source releases
- [CONTRIBUTING.md](/Users/lambda/projects/aio_robot/CONTRIBUTING.md:1) — development workflow and contribution expectations

## Current Reference Implementation

Today the active runtime lives in `robot_agent_prototype_leonardo/`.

That reference stack includes:

- a calibrated control service for `base`, `shoulder`, and `gripper`
- a browser vision path for mapping human arm motion to robot motion
- a local LLM path through LM Studio with tool calling
- a Telegram interface for text, photo, and voice messages
- an MCP bridge for external agent clients

The implementation is still a reference robot, not the final universal robotics platform. The important part is that the architecture is already split in a way that can be adapted to other robots without rewriting the whole assistant stack.

## Design Direction

AIO is being developed around a few simple ideas:

- local-first runtime where possible
- model-agnostic interfaces instead of coupling the system to one provider
- robot adapters that translate from AIO logical actions into hardware-specific commands
- one assistant layer that can be reused across web, Telegram, MCP, and future clients
- incremental path from rule-based tooling to future perception-action policies and VLA models

## Quick Start

```bash
cd robot_agent_prototype_leonardo
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..
python run.py
```

Useful entry points after startup:

- frontend: `http://127.0.0.1:5173`
- backend: `http://127.0.0.1:8000`
- backend docs: `http://127.0.0.1:8000/docs`

If you want the Telegram bot too:

```bash
cd robot_agent_prototype_leonardo
source .venv/bin/activate
python run.py --telegram-bot
```

## Safety Note

This repository controls real hardware.

Even though the current code includes limits, calibrated presets, and guarded tool calls, this is still an experimental robotics system. Any new robot adapter, new transport layer, or new autonomous policy should be tested first with conservative limits and without load.

## License

The repository already includes [LICENSE](/Users/lambda/projects/aio_robot/LICENSE:1). If the release later expands with CAD, hardware, datasets, or trained models, those assets may need their own packaging and documentation rules, but the software stack is already structured for public release.
