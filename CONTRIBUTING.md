# Contributing

Thank you for contributing to AIO.

The repository is still evolving, but the contribution workflow should stay simple and predictable.

## Before You Change Code

1. Read [README.md](/Users/lambda/projects/aio_robot/README.md:1).
2. Read the reference implementation guide in [robot_agent_prototype_leonardo/README.md](/Users/lambda/projects/aio_robot/robot_agent_prototype_leonardo/README.md:1).
3. If you are changing robot control behavior, read [docs/ROBOT_API_ADAPTER_GUIDE.md](/Users/lambda/projects/aio_robot/docs/ROBOT_API_ADAPTER_GUIDE.md:1).

## Setup

```bash
cd robot_agent_prototype_leonardo
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm install && cd ..
```

## Running Tests

From the repository root:

```bash
source robot_agent_prototype_leonardo/.venv/bin/activate
python -m unittest discover -s robot_agent_prototype_leonardo/tests -v
```

From the project directory:

```bash
source .venv/bin/activate
PYTHONPATH=.. python -m unittest discover -s tests -v
```

## Contribution Priorities

The most useful contributions right now are:

- documentation improvements that reflect the real code
- bug fixes in the control layer
- cleaner robot adapter boundaries
- tests around transport, mapping, and tool dispatch
- integration improvements that keep the backend as the single control authority

## What To Avoid

- do not commit secrets, tokens, or personal `.env` files
- do not add a second direct hardware control path when the backend already exists
- do not couple client code to raw hardware protocol details
- do not widen the assistant tool surface without a clear safety reason
- do not rewrite unrelated areas while fixing a narrow issue

## Secrets And Local Configuration

Use local `.env` files or local environment variables for secrets such as:

- Telegram bot tokens
- local runtime overrides
- machine-specific paths

The repository already ignores `.env`, but contributors should still double-check before committing.

## Pull Request Expectations

A good contribution should include:

- a focused change set
- updated documentation if the behavior changed
- tests when the change affects logic or interfaces
- clear explanation of what changed and why

## Hardware Changes

If you are contributing robot-specific changes:

- document the target hardware
- document transport assumptions
- document calibration assumptions
- explain whether the change is generic or reference-hardware-specific

This helps keep AIO usable as a broader robotics platform instead of locking it tighter to one build.
