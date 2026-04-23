# AIO Roadmap

This roadmap is intentionally practical.

It reflects what the repository already contains, what is partially implemented, and what is planned to be published later.

## Current State

Already implemented in this repository:

- reference backend for a calibrated arm on Arduino Leonardo
- web UI for chat, hardware control, and browser vision
- LM Studio integration for local LLM control
- Telegram bot
- MCP bridge
- tests for the core software layers

## Near-Term Open-Source Priorities

### 1. Documentation Maturity

Goals:

- publish clear architecture docs
- document the robot adapter boundary
- document integrations and setup
- keep release docs aligned with the actual codebase

### 2. Hardware Release Assets

Planned publication:

- STL files
- CAD exports
- build notes
- BOM
- wiring and power notes

These are not fully linked in the repository yet, but the software release is being structured to make that future hardware release fit naturally.

### 3. Adapter Generalization

Goals:

- support more than one robot transport
- separate reference-arm assumptions from the core assistant stack
- make it easier to swap Arduino Leonardo for another backend controller or protocol

### 4. Perception Improvements

Goals:

- strengthen the current vision path
- improve calibration workflows
- add more robust scene understanding
- make future perception modules pluggable rather than hard-coded

### 5. Future VLA Release

Planned direction:

- release training code
- release evaluation tooling
- release integration code that lets the assistant call a VLA policy as a skill
- release the resulting model or clear reproduction instructions when feasible

The repository is being documented now with that future shape in mind.

## Long-Term Direction

The long-term target for AIO is not only a robotic arm project.

The intended direction is:

- a local-first robotics assistant stack
- usable through multiple clients
- able to call different classes of models as skills
- able to control different robots through adapter layers
- eventually able to orchestrate conventional tools, perception models, and VLA policies together

## What Will Probably Stay Stable

These ideas are likely to remain core:

- local model support
- tool-based control as a stable abstraction
- MCP support for external agent clients
- one backend as the control authority
- robot-specific logic isolated behind adapters

## What Will Evolve

These parts are expected to change:

- the reference robot hardware
- the set of supported joints and adapters
- the perception stack
- the range of supported local models
- the future policy and VLA integration layer

## Release Philosophy

The project is being prepared for open-source release in layers:

1. software runtime and architecture
2. adapter and integration guides
3. hardware assets
4. future trained models and learning code

That release order keeps the software useful now while leaving room to publish the rest of the stack in a clean and documented way later.
