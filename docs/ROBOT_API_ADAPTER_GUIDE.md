# Adapting AIO To Another Robot API

This guide explains how to make AIO control a different robot without rewriting the assistant stack.

The short version is:

- do not start by rewriting the web UI
- do not start by rewriting Telegram
- do not start by rewriting MCP
- do not start by changing the assistant tool model

The correct place to adapt is the robot control boundary.

## What Should Stay The Same

These parts are already generic enough and should usually stay unchanged:

- [backend/main.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/main.py:1)
- [backend/chat_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/chat_service.py:1)
- [mcp_server.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/mcp_server.py:1)
- [telegram_bot.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/telegram_bot.py:1)

Those files mostly depend on logical joint names, logical limits, and shared tool semantics. They should not need transport-specific rewrites.

## What Is Robot-Specific Today

These files define the current Leonardo arm adapter:

- [backend/control/service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/service.py:1)
- [backend/control/serial_adapter.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/serial_adapter.py:1)
- [arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/arduino/robot_arm_serial_controller/robot_arm_serial_controller.ino:1)

If you want to support another robot, this is the layer to replace or adapt.

## Current Adapter Contract

The rest of the stack expects the control layer to provide:

- a way to list available hardware targets
- a way to connect and disconnect
- a way to refresh state
- a way to validate a logical joint move
- a way to execute a logical joint move
- a way to apply a logical pose
- a way to expose supported presets

In practical terms, the current backend uses:

- `list_ports()`
- `connect_hardware()`
- `disconnect_hardware()`
- `refresh_status()`
- `validate_joint_move()`
- `apply_joint_pose()`
- `execute_action()`
- `get_joint_limits()`
- `get_supported_presets()`

## Step 1: Define Your Logical Robot Model

Before touching transport code, decide what AIO should call your robot joints.

For example:

- `base`
- `shoulder`
- `elbow`
- `wrist`
- `gripper`

Keep this logical naming stable across:

- backend limits
- tool schemas
- frontend controls
- Telegram robot actions
- MCP robot actions

If your robot uses a completely different structure, first decide what the assistant-facing abstraction should be. The assistant should speak in logical actions, not in hardware-specific packet formats.

## Step 2: Replace Joint Limits And Presets

Start in [backend/control/service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/service.py:1).

The main places to change are:

- `RAW_JOINT_LIMITS`
- `LOGICAL_DEFAULTS`
- `LOGICAL_PRESETS`
- any calibration constants such as `BASE_CENTER_RAW_ANGLE`
- any robot-specific command offsets

What to update:

1. Rename joints to match your robot.
2. Replace the min and max ranges with your hardware ranges.
3. Replace default angles with safe neutral positions.
4. Replace presets with poses that make sense for your robot.
5. Remove unused joints from the current arm model.
6. Add new joints only if you are ready to support them across the full stack.

## Step 3: Replace The Transport Layer

The current implementation talks to Arduino over serial through [serial_adapter.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/control/serial_adapter.py:1).

For another robot, you might instead need:

- HTTP
- WebSocket
- ROS bridge
- CAN bus wrapper
- vendor SDK
- another serial protocol

You have two clean options:

### Option A: Keep `ControlService`, replace only the adapter

Do this if your robot still fits the current control model and only the transport changes.

In that case:

- keep `ControlService` as the main logical layer
- replace `SerialAdapter` with your own adapter methods
- keep the same backend endpoints

This is the easiest path.

### Option B: Replace both `ControlService` and the adapter

Do this if:

- the joint model is very different
- your robot reports richer state
- your motion model is not angle-based
- you need trajectory, velocity, or Cartesian commands

In that case, keep the public backend semantics stable, but rewrite the internal control layer.

## Step 4: Map Hardware State Back Into AIO State

The backend needs to report a normalized [RobotState](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/models.py:48).

At minimum you should keep these fields meaningful:

- `hardware_connected`
- `controller_connected`
- `telemetry_source`
- `controller_state`
- `last_error`
- `joints`

If your robot reports richer state, add it carefully in a backwards-compatible way rather than breaking the existing API.

## Step 5: Keep Assistant Tools Stable

The current assistant tools are defined in [backend/chat_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/backend/chat_service.py:1).

Those tool names are already used by:

- the web chat
- Telegram
- the external assistant tool endpoint

If possible, keep these tool names stable:

- `get_robot_status`
- `move_joint`
- `apply_pose`
- `run_preset`
- `stop_robot`

If you change the internal robot implementation but preserve these tool semantics, the rest of the system continues to work with much less effort.

## Step 6: Update Frontend Controls Only After The Backend Model Is Stable

The frontend currently assumes:

- `base`
- `shoulder`
- `gripper`

Once the backend joint model is final, update:

- [frontend/src/App.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/App.jsx:1)
- [frontend/src/VisionControl.jsx](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/VisionControl.jsx:1)
- [frontend/src/vision.js](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/frontend/src/vision.js:1)

Do not start here. Frontend changes should follow the adapter changes, not lead them.

## Step 7: Revisit Telegram And MCP Only If Joint Names Changed

Telegram and MCP mostly rely on backend tools and backend endpoints.

You usually only need to revisit them if:

- tool descriptions should mention new joints
- you added or removed presets
- you changed what counts as a safe direct move

## Step 8: Update Tests

Before treating the new adapter as done, update:

- [tests/test_control_service.py](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/tests/test_control_service.py:1)
- any tool-layer tests affected by new presets or safety rules

At minimum, test:

- logical-to-hardware mapping
- hardware-to-logical mapping
- limit validation
- preset execution path
- blocked behavior when hardware is offline

## Minimal Migration Checklist

If you want the shortest practical path, follow this order:

1. Decide logical joint names.
2. Replace limits and defaults in `ControlService`.
3. Replace the serial transport with your own transport.
4. Make `refresh_status()` return normalized AIO state.
5. Make one safe joint move work end to end.
6. Make one preset work end to end.
7. Update tests.
8. Only then update frontend labels and any vision assumptions.

## What Not To Do

- Do not expose raw vendor packets directly to the LLM.
- Do not let Telegram or MCP open their own hardware connections.
- Do not make transport code live in three different clients.
- Do not change assistant tools first and hardware second.
- Do not add new joints everywhere before the backend control layer is stable.

## Recommended End State

The healthiest long-term architecture is:

- one shared backend contract
- one robot adapter per hardware family
- one assistant tool model across all clients
- multiple user interfaces on top

That is the path that turns AIO from a single-arm prototype into a reusable robotics agent platform.
