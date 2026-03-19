from __future__ import annotations

from ..control.service import control_service
from ..models import Capability, SkillCall
from ..state import app_state


class RobotArmSkill:
    def get_capabilities(self) -> list[Capability]:
        return [
            Capability(name="get_status", description="Return current manipulator state", mode_support=["demo", "sim", "hardware"]),
            Capability(name="home", description="Move to home position", mode_support=["demo", "sim", "hardware"]),
            Capability(name="open_gripper", description="Open the gripper", mode_support=["demo", "sim", "hardware"]),
            Capability(name="close_gripper", description="Close the gripper", mode_support=["demo", "sim", "hardware"]),
            Capability(name="lift_pose", description="Move arm to a safe upper pose", mode_support=["demo", "sim", "hardware"]),
            Capability(name="cycle_motion", description="Run a continuous smooth cycle for base and shoulder", mode_support=["demo", "sim", "hardware"]),
            Capability(name="wave", description="Run a greeting motion", mode_support=["demo", "sim", "hardware"]),
            Capability(name="demo_sequence", description="Run a multi-step demonstration", mode_support=["demo", "sim", "hardware"]),
            Capability(name="park", description="Park manipulator safely", mode_support=["demo", "sim", "hardware"]),
            Capability(name="move_zone", description="Align with a target workspace zone", mode_support=["demo", "sim", "hardware"]),
            Capability(name="move_joint", description="Move one joint to a specific angle", mode_support=["demo", "sim", "hardware"]),
            Capability(name="stop", description="Emergency stop / stop current task", mode_support=["demo", "sim", "hardware"]),
        ]

    def validate_action(self, skill_call: SkillCall) -> tuple[bool, str | None]:
        action = skill_call.action_name
        params = skill_call.parameters
        state = app_state.get_robot_state()

        if state.safety_lock and action != "get_status":
            return False, "Safety lock is active. Only status inspection is allowed."

        if action == "move_joint":
            joint_name = params.get("joint_name")
            angle = params.get("angle")
            if joint_name is None or angle is None:
                return False, "Joint move requires 'joint_name' and 'angle'."
            return control_service.validate_joint_move(joint_name, float(angle))

        if action == "move_zone":
            zone = params.get("zone")
            if zone not in {"left", "center", "right"}:
                return False, "Zone must be one of: left, center, right."

        return True, None

    def handle_action(self, skill_call: SkillCall):
        valid, error = self.validate_action(skill_call)
        if not valid:
            app_state.update_robot_state(last_error=error)
            app_state.add_log("robot_arm_skill", "Action blocked by validation", level="warning", context={"action": skill_call.action_name, "error": error})
            return []

        app_state.add_log("robot_arm_skill", "Executing action", context={"action": skill_call.action_name, "parameters": skill_call.parameters})
        return control_service.execute_action(skill_call.action_name, skill_call.parameters)


robot_arm_skill = RobotArmSkill()
