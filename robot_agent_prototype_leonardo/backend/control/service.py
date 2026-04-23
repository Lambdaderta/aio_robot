from __future__ import annotations

from datetime import datetime

from ..models import ExecutionStep, ExecutionStatus, JointLimit, RobotState
from ..state import app_state
from .serial_adapter import SerialAdapterError, serial_adapter


class ControlService:
    BASE_CENTER_RAW_ANGLE = 30.0
    RAW_JOINT_LIMITS = {
        "base": JointLimit(name="base", min_angle=0, max_angle=180, default_angle=90),
        "shoulder": JointLimit(name="shoulder", min_angle=0, max_angle=180, default_angle=90),
        "gripper": JointLimit(name="gripper", min_angle=0, max_angle=90, default_angle=45),
    }
    LOGICAL_DEFAULTS = {
        "base": 0.0,
        "shoulder": 90.0,
        "gripper": 45.0,
    }
    LOGICAL_PRESETS = {
        "HOME": {"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
        "LIFT": {"base": 0.0, "shoulder": 120.0, "gripper": 45.0},
        "PARK": {"base": 0.0, "shoulder": 70.0, "gripper": 45.0},
        "LEFT": {"base": -35.0, "shoulder": 90.0, "gripper": 45.0},
        "CENTER": {"base": 0.0, "shoulder": 90.0, "gripper": 45.0},
        "RIGHT": {"base": 35.0, "shoulder": 90.0, "gripper": 45.0},
    }
    JOINT_COMMAND_OFFSETS = {
        "base": 0.0,
        "shoulder": 0.0,
        "gripper": 0.0,
    }

    SUPPORTED_PRESETS = tuple(LOGICAL_PRESETS.keys())

    def get_joint_limits(self) -> dict[str, JointLimit]:
        limits: dict[str, JointLimit] = {}
        for joint_name, raw_limits in self.RAW_JOINT_LIMITS.items():
            logical_min = self._from_hardware_angle(joint_name, raw_limits.min_angle)
            logical_max = self._from_hardware_angle(joint_name, raw_limits.max_angle)
            low = min(logical_min, logical_max)
            high = max(logical_min, logical_max)
            limits[joint_name] = JointLimit(
                name=joint_name,
                min_angle=round(low, 2),
                max_angle=round(high, 2),
                default_angle=float(self.LOGICAL_DEFAULTS[joint_name]),
            )
        return limits

    def get_supported_presets(self) -> tuple[str, ...]:
        return self.SUPPORTED_PRESETS

    def list_ports(self):
        return serial_adapter.list_ports()

    def validate_joint_move(self, joint_name: str, angle: float) -> tuple[bool, str | None]:
        if joint_name not in self.RAW_JOINT_LIMITS:
            return False, f"Unknown joint: {joint_name}"
        hardware_angle = self._to_hardware_angle(joint_name, angle)
        low = self.RAW_JOINT_LIMITS[joint_name].min_angle
        high = self.RAW_JOINT_LIMITS[joint_name].max_angle
        if not (low <= hardware_angle <= high):
            logical_limits = self.get_joint_limits()[joint_name]
            return (
                False,
                f"Requested angle {angle} is outside calibrated range "
                f"[{logical_limits.min_angle}, {logical_limits.max_angle}] "
                f"for {joint_name} and would map to hardware angle {round(hardware_angle, 2)}.",
            )
        return True, None

    def connect_hardware(self, port: str, baud_rate: int = 115200) -> RobotState:
        app_state.add_log("hardware", "Connecting to Arduino", context={"port": port, "baud_rate": baud_rate})
        payload = serial_adapter.connect(port, baud_rate)
        state = app_state.update_robot_state(
            arm_online=True,
            controller_connected=True,
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
            arm_online=False,
            controller_connected=False,
            hardware_connected=False,
            hardware_port=None,
            firmware_ready=False,
            telemetry_source="offline",
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
                return app_state.update_robot_state(last_error=str(exc), controller_state="error", arm_online=False)
        return state

    def apply_joint_pose(self, joints: dict[str, float]) -> list[ExecutionStep]:
        steps: list[ExecutionStep] = []
        normalized: dict[str, float] = {}
        for joint_name, angle in joints.items():
            normalized_angle = float(angle)
            ok, error = self.validate_joint_move(joint_name, normalized_angle)
            if not ok:
                raise ValueError(error)
            normalized[joint_name] = normalized_angle

        for joint_name, angle in normalized.items():
            action_steps = self.execute_action("move_joint", {"joint_name": joint_name, "angle": angle})
            steps.extend(action_steps)
            if any(step.status in {"blocked", "failed"} for step in action_steps):
                break
        return steps

    def execute_manual_preset(self, preset_name: str) -> list[ExecutionStep]:
        normalized = preset_name.strip().upper()
        if normalized in self.LOGICAL_PRESETS:
            return self.apply_joint_pose(self.LOGICAL_PRESETS[normalized])
        raise ValueError(f"Unsupported preset '{preset_name}'")

    def execute_action(self, action_name: str, parameters: dict) -> list[ExecutionStep]:
        app_state.add_log("control", "Received hardware action", context={"action": action_name, "parameters": parameters})

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

        if action_name == "get_status":
            state = self.refresh_status()
            add_step("state_refresh", "completed", f"Robot state refreshed from {state.telemetry_source}")
            return steps

        state = app_state.get_robot_state()
        if not (state.hardware_connected and serial_adapter.is_connected):
            add_step("hardware_check", "blocked", "Arduino is not connected; no simulated execution is available")
            app_state.update_robot_state(last_error="Arduino is not connected")
            app_state.add_log("control", "Hardware action blocked because Arduino is not connected", level="warning")
            return steps

        add_step("hardware_check", "completed", "Arduino connection is active")
        add_step("controller_dispatch", "completed", f"Action '{action_name}' dispatched to serial adapter")

        try:
            if action_name == "move_joint":
                joint_name = parameters["joint_name"]
                logical_angle = float(parameters["angle"])
                hardware_angle = self._to_hardware_angle(joint_name, logical_angle)
                payload = serial_adapter.set_joint(joint_name, hardware_angle)
            elif action_name == "stop":
                payload = serial_adapter.stop()
            else:
                raise SerialAdapterError(f"Action '{action_name}' is not supported by the current hardware API")

            add_step("serial_exchange", "completed", payload.get("raw", "Arduino responded"))
            self._apply_hardware_status(serial_adapter.get_status())
            add_step("execution_complete", "completed", f"Action '{action_name}' acknowledged by Arduino")
            app_state.add_log("control", "Hardware action execution completed", context={"action": action_name, "response": payload})
            return steps
        except SerialAdapterError as exc:
            add_step("serial_exchange", "failed", str(exc))
            app_state.update_robot_state(last_error=str(exc), controller_state="error", arm_online=False)
            app_state.add_log("control", "Hardware action failed", level="error", context={"action": action_name, "error": str(exc)})
            return steps

    def _apply_hardware_status(self, payload: dict, state_override: RobotState | None = None) -> RobotState:
        current = state_override or app_state.get_robot_state()
        joints = current.joints.copy()
        for joint_name in self.RAW_JOINT_LIMITS:
            if joint_name in payload:
                try:
                    joints[joint_name] = round(self._from_hardware_angle(joint_name, float(payload[joint_name])), 2)
                except ValueError:
                    pass
        grip_state = payload.get("grip_state") or current.gripper_state
        pose = payload.get("pose") or current.active_pose
        controller_state = payload.get("state") or payload.get("controller_state") or current.controller_state
        state = app_state.update_robot_state(
            arm_online=True,
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

    def _to_hardware_angle(self, joint_name: str, logical_angle: float) -> float:
        if joint_name == "base":
            logical = float(logical_angle)
            center = self.BASE_CENTER_RAW_ANGLE
            if logical <= 0:
                return round(center + logical * (center / 90.0), 2)
            return round(center + logical * ((180.0 - center) / 90.0), 2)

        offset = self.JOINT_COMMAND_OFFSETS.get(joint_name, 0.0)
        return round(float(logical_angle) + offset, 2)

    def _from_hardware_angle(self, joint_name: str, hardware_angle: float) -> float:
        if joint_name == "base":
            raw = float(hardware_angle)
            center = self.BASE_CENTER_RAW_ANGLE
            if raw <= center:
                return round((raw - center) * (90.0 / center), 2)
            return round((raw - center) * (90.0 / (180.0 - center)), 2)

        offset = self.JOINT_COMMAND_OFFSETS.get(joint_name, 0.0)
        return round(float(hardware_angle) - offset, 2)


control_service = ControlService()
