from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime
from threading import Lock

from .models import LogEntry, RobotState


class AppState:
    def __init__(self) -> None:
        self._robot_state = RobotState()
        self._logs: deque[LogEntry] = deque(maxlen=300)
        self._lock = Lock()
        self.add_log("system", "Robot agent prototype initialized")

    def get_robot_state(self) -> RobotState:
        with self._lock:
            return self._robot_state.model_copy(deep=True)

    def update_robot_state(self, **changes) -> RobotState:
        with self._lock:
            current = self._robot_state.model_dump()
            current.update(changes)
            self._robot_state = RobotState(**current)
            return self._robot_state.model_copy(deep=True)

    def set_joint(self, joint_name: str, angle: float) -> RobotState:
        with self._lock:
            joints = deepcopy(self._robot_state.joints)
            joints[joint_name] = round(float(angle), 2)
            self._robot_state = self._robot_state.model_copy(update={"joints": joints})
            return self._robot_state.model_copy(deep=True)

    def set_joints(self, joints_patch: dict[str, float]) -> RobotState:
        with self._lock:
            joints = deepcopy(self._robot_state.joints)
            for joint_name, angle in joints_patch.items():
                joints[joint_name] = round(float(angle), 2)
            self._robot_state = self._robot_state.model_copy(update={"joints": joints})
            return self._robot_state.model_copy(deep=True)

    def add_log(self, source: str, message: str, level: str = "info", context: dict | None = None) -> LogEntry:
        entry = LogEntry(
            timestamp=datetime.utcnow(),
            source=source,
            message=message,
            level=level,
            context=context or {},
        )
        with self._lock:
            self._logs.appendleft(entry)
        return entry

    def list_logs(self, limit: int = 50) -> list[LogEntry]:
        with self._lock:
            return list(self._logs)[:limit]


app_state = AppState()
