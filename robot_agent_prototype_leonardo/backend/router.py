from __future__ import annotations

from .models import SkillCall


class SkillRouter:
    def route(self, intent_name: str, entities: dict) -> SkillCall | None:
        intent_to_action = {
            "status": "get_status",
            "home": "home",
            "open_gripper": "open_gripper",
            "close_gripper": "close_gripper",
            "lift": "lift_pose",
            "cycle": "cycle_motion",
            "wave": "wave",
            "demo": "demo_sequence",
            "stop": "stop",
            "park": "park",
            "move_zone": "move_zone",
            "move_joint": "move_joint",
        }
        action = intent_to_action.get(intent_name)
        if not action:
            return None
        return SkillCall(skill_name="robot_arm", action_name=action, parameters=entities)


skill_router = SkillRouter()
