from __future__ import annotations

from datetime import datetime

from ..models import ExecutionStep, ExecutionStatus, JointLimit, RobotState
from ..state import app_state
from .serial_adapter import SerialAdapterError, serial_adapter


class ControlService:
    JOINT_LIMITS = {
        "base": JointLimit(name="base", min_angle=0, max_angle=180, default_angle=90),
        "shoulder": JointLimit(name="shoulder", min_angle=0, max_angle=180, default_angle=90),
        "gripper": JointLimit(name="gripper", min_angle=0, max_angle=90, default_angle=45),
    }

    ACTION_TO_PRESET = {
        "home": "HOME",
        "open_gripper": "OPEN",
        "close_gripper": "CLOSE",
        "lift_pose": "LIFT",
        "cycle_motion": "CYCLE",
        "wave": "WAVE",
        "demo_sequence": "DEMO",
        "park": "PARK",
    }

    ZONE_TO_PRESET = {
        "left": "LEFT",
        "center": "CENTER",
        "right": "RIGHT",
    }

    def get_joint_limits(self) -> dict[str, JointLimit]:
        return self.JOINT_LIMITS

    def list_ports(self):
        return serial_adapter.list_ports()

    def validate_joint_move(self, joint_name: str, angle: float) -> tuple[bool, str | None]:
        if joint_name not in self.JOINT_LIMITS:
            return False, f"Unknown joint: {joint_name}"
        low = self.JOINT_LIMITS[joint_name].min_angle
        high = self.JOINT_LIMITS[joint_name].max_angle
        if not (low <= angle <= high):
            return False, f"Requested angle {angle} is outside allowed range [{low}, {high}]"
        return True, None

    def connect_hardware(self, port: str, baud_rate: int = 115200) -> RobotState:
        app_state.add_log("hardware", "Connecting to Arduino", context={"port": port, "baud_rate": baud_rate})
        payload = serial_adapter.connect(port, baud_rate)
        state = app_state.update_robot_state(
            hardware_connected=True,
            hardware_port=port,
            baud_rate=baud_rate,
            firmware_ready=True,
            last_error=None,
            last_serial_message=payload.get("raw"),
            telemetry_source="arduino",
        )
        return self._apply_hardware_status(payload, state_override=state)

    def disconnect_hardware(self) -> RobotState:
        serial_adapter.disconnect()
        state = app_state.update_robot_state(
            hardware_connected=False,
            hardware_port=None,
            firmware_ready=False,
            telemetry_source="simulator",
            last_serial_message=None,
            last_seen_at=None,
            controller_state="idle",
            last_error=None,
        )
        app_state.add_log("hardware", "Arduino disconnected")
        return state

    def refresh_status(self) -> RobotState:
        state = app_state.get_robot_state()
        if state.hardware_connected and serial_adapter.is_connected:
            try:
                payload = serial_adapter.get_status()
                return self._apply_hardware_status(payload)
            except SerialAdapterError as exc:
                app_state.add_log("hardware", "Failed to refresh Arduino status", level="warning", context={"error": str(exc)})
                return app_state.update_robot_state(last_error=str(exc), controller_state="error")
        return state

    def apply_joint_pose(self, joints: dict[str, float]) -> list[ExecutionStep]:
        steps: list[ExecutionStep] = []
        for joint_name, angle in joints.items():
            ok, error = self.validate_joint_move(joint_name, float(angle))
            if not ok:
                raise ValueError(error)
            steps.extend(self.execute_action("move_joint", {"joint_name": joint_name, "angle": angle}))
        return steps

    def execute_manual_preset(self, preset_name: str) -> list[ExecutionStep]:
        normalized = preset_name.strip().upper()
        reverse = {value: key for key, value in self.ACTION_TO_PRESET.items()}
        if normalized in reverse:
            return self.execute_action(reverse[normalized], {})
        if normalized in {"LEFT", "CENTER", "RIGHT"}:
            return self.execute_action("move_zone", {"zone": normalized.lower()})
        raise ValueError(f"Unsupported preset '{preset_name}'")

    def execute_action(self, action_name: str, parameters: dict) -> list[ExecutionStep]:
        mode = app_state.get_robot_state().mode
        app_state.add_log("control", "Received action for execution", context={"action": action_name, "mode": mode, "parameters": parameters})

        steps: list[ExecutionStep] = []

        def add_step(name: str, status: ExecutionStatus, details: str) -> None:
            now = datetime.utcnow()
            steps.append(
                ExecutionStep(
                    step_name=name,
                    status=status,
                    details=details,
                    started_at=now,
                    finished_at=now,
                )
            )

        add_step("mode_check", "completed", f"Execution mode '{mode}' accepted")

        if action_name == "get_status":
            state = self.refresh_status()
            add_step("state_refresh", "completed", f"Robot state refreshed from {state.telemetry_source}")
            return steps

        if mode == "hardware":
            return self._execute_hardware_action(action_name, parameters, steps)

        return self._execute_simulated_action(action_name, parameters, steps, mode)

    def _execute_simulated_action(self, action_name: str, parameters: dict, steps: list[ExecutionStep], mode: str) -> list[ExecutionStep]:
        def add(name: str, status: ExecutionStatus, details: str) -> None:
            now = datetime.utcnow()
            steps.append(ExecutionStep(step_name=name, status=status, details=details, started_at=now, finished_at=now))

        add("controller_dispatch", "completed", f"Action '{action_name}' dispatched to {mode} controller pipeline")
        self._apply_simulated_state(action_name, parameters)
        add("motion_plan", "completed", f"Simulated motion plan generated for '{action_name}'")
        add("execution_complete", "completed", f"Action '{action_name}' completed in {mode} mode")
        app_state.add_log("control", "Action execution completed", context={"action": action_name, "mode": mode})
        return steps

    def _execute_hardware_action(self, action_name: str, parameters: dict, steps: list[ExecutionStep]) -> list[ExecutionStep]:
        def add(name: str, status: ExecutionStatus, details: str) -> None:
            now = datetime.utcnow()
            steps.append(ExecutionStep(step_name=name, status=status, details=details, started_at=now, finished_at=now))

        state = app_state.get_robot_state()
        if not (state.hardware_connected and serial_adapter.is_connected):
            add("hardware_check", "blocked", "Hardware mode selected, but Arduino is not connected")
            app_state.update_robot_state(last_error="Hardware mode selected, but Arduino is not connected")
            app_state.add_log("control", "Hardware mode blocked because Arduino is not connected", level="warning")
            return steps

        add("controller_dispatch", "completed", f"Action '{action_name}' dispatched to serial adapter")

        try:
            if action_name == "move_joint":
                payload = serial_adapter.set_joint(parameters["joint_name"], float(parameters["angle"]))
            elif action_name == "move_zone":
                payload = serial_adapter.preset(self.ZONE_TO_PRESET[parameters.get("zone", "center")])
            elif action_name == "stop":
                payload = serial_adapter.stop()
            else:
                preset_name = self.ACTION_TO_PRESET.get(action_name)
                if not preset_name:
                    raise SerialAdapterError(f"Action '{action_name}' is not mapped to a hardware preset")
                payload = serial_adapter.preset(preset_name)

            add("serial_exchange", "completed", payload.get("raw", "Arduino responded"))
            self._apply_hardware_status(serial_adapter.get_status())
            add("execution_complete", "completed", f"Action '{action_name}' acknowledged by Arduino")
            app_state.add_log("control", "Hardware action execution completed", context={"action": action_name, "response": payload})
            return steps
        except SerialAdapterError as exc:
            add("serial_exchange", "failed", str(exc))
            app_state.update_robot_state(last_error=str(exc), controller_state="error")
            app_state.add_log("control", "Hardware action failed", level="error", context={"action": action_name, "error": str(exc)})
            return steps

    def _apply_simulated_state(self, action_name: str, parameters: dict) -> RobotState:
        current = app_state.get_robot_state()
        changes = {
            "telemetry_source": "simulator",
            "controller_state": "idle",
            "last_error": None,
            "last_seen_at": datetime.utcnow(),
        }
        joints_patch: dict[str, float] = {}

        if action_name == "home":
            changes.update(active_pose="home", current_task="home_sequence", gripper_state="partially_open")
            joints_patch = {name: limit.default_angle for name, limit in self.JOINT_LIMITS.items()}
        elif action_name == "open_gripper":
            changes.update(gripper_state="open", current_task="gripper_open", active_pose=current.active_pose)
            joints_patch = {"gripper": self.JOINT_LIMITS["gripper"].max_angle}
        elif action_name == "close_gripper":
            changes.update(gripper_state="closed", current_task="gripper_close", active_pose=current.active_pose)
            joints_patch = {"gripper": self.JOINT_LIMITS["gripper"].min_angle}
        elif action_name == "lift_pose":
            changes.update(active_pose="lift", current_task="lift_pose")
            joints_patch = {"base": 90, "shoulder": 130, "gripper": 45}
        elif action_name == "cycle_motion":
            changes.update(active_pose="cycle", current_task="cycle_motion", controller_state="busy")
            joints_patch = {"base": 45, "shoulder": 45}
        elif action_name == "wave":
            changes.update(active_pose="wave", current_task="wave_demo")
            joints_patch = {"base": 120, "shoulder": 130}
        elif action_name == "demo_sequence":
            changes.update(active_pose="demo", current_task="demo_sequence", gripper_state="open")
            joints_patch = {"base": 120, "shoulder": 125, "gripper": 90}
        elif action_name == "park":
            changes.update(active_pose="park", current_task="park", gripper_state="partially_open")
            joints_patch = {"base": 90, "shoulder": 40, "gripper": 20}
        elif action_name == "move_zone":
            zone = parameters.get("zone", "center")
            base_angle = {"left": 45, "center": 90, "right": 135}[zone]
            changes.update(active_pose=f"zone:{zone}", current_task=f"move_to_{zone}")
            joints_patch = {"base": base_angle, "shoulder": 110, "gripper": 45}
        elif action_name == "move_joint":
            changes.update(active_pose=f"joint:{parameters['joint_name']}", current_task=f"move_{parameters['joint_name']}")
            joints_patch = {parameters["joint_name"]: float(parameters["angle"])}
            if parameters["joint_name"] == "gripper":
                grip = float(parameters["angle"])
                if grip <= 8:
                    changes["gripper_state"] = "closed"
                elif grip >= 75:
                    changes["gripper_state"] = "open"
                else:
                    changes["gripper_state"] = "partially_open"
        elif action_name == "stop":
            changes.update(current_task="stopped", active_pose="stopped", controller_state="idle")

        state = app_state.update_robot_state(**changes)
        if joints_patch:
            state = app_state.set_joints(joints_patch)
        return state

    def _apply_hardware_status(self, payload: dict, state_override: RobotState | None = None) -> RobotState:
        current = state_override or app_state.get_robot_state()
        joints = current.joints.copy()
        for joint_name in self.JOINT_LIMITS:
            if joint_name in payload:
                try:
                    joints[joint_name] = round(float(payload[joint_name]), 2)
                except ValueError:
                    pass
        grip_state = payload.get("grip_state") or current.gripper_state
        pose = payload.get("pose") or current.active_pose
        controller_state = payload.get("state") or payload.get("controller_state") or current.controller_state
        state = app_state.update_robot_state(
            hardware_connected=True,
            controller_connected=True,
            hardware_port=serial_adapter.port or current.hardware_port,
            baud_rate=serial_adapter.baud_rate or current.baud_rate,
            firmware_ready=True,
            telemetry_source="arduino",
            last_seen_at=serial_adapter.last_seen_at or datetime.utcnow(),
            controller_state=controller_state,
            gripper_state=grip_state,
            active_pose=pose,
            current_task=pose,
            last_error=None,
            last_serial_message=payload.get("raw"),
        )
        state = app_state.set_joints(joints)
        return state


control_service = ControlService()
