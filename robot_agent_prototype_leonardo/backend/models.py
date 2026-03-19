from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ExecutionMode = Literal["demo", "sim", "hardware"]
GripperState = Literal["open", "closed", "partially_open"]
ExecutionStatus = Literal["queued", "running", "completed", "failed", "blocked"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class SetModeRequest(BaseModel):
    mode: ExecutionMode


class ConnectHardwareRequest(BaseModel):
    port: str
    baud_rate: int = 115200


class ServoSetRequest(BaseModel):
    joint_name: str
    angle: float


class JointPoseRequest(BaseModel):
    joints: dict[str, float] = Field(default_factory=dict)


class PresetRequest(BaseModel):
    preset: str


class ExecutionStep(BaseModel):
    step_name: str
    status: ExecutionStatus
    details: str
    started_at: datetime
    finished_at: datetime | None = None


class LogEntry(BaseModel):
    timestamp: datetime
    level: Literal["info", "warning", "error"] = "info"
    source: str
    message: str
    context: dict[str, Any] = Field(default_factory=dict)


class JointLimit(BaseModel):
    name: str
    min_angle: float
    max_angle: float
    default_angle: float
    hardware_enabled: bool = True


class HardwarePort(BaseModel):
    device: str
    description: str
    hwid: str | None = None


class HardwareConnection(BaseModel):
    connected: bool = False
    port: str | None = None
    baud_rate: int = 115200
    firmware_ready: bool = False
    last_response: str | None = None
    last_seen_at: datetime | None = None
    controller_state: str = "idle"


class RobotState(BaseModel):
    arm_online: bool = True
    controller_connected: bool = True
    hardware_connected: bool = False
    hardware_port: str | None = None
    baud_rate: int = 115200
    firmware_ready: bool = False
    telemetry_source: Literal["simulator", "arduino"] = "simulator"
    last_seen_at: datetime | None = None
    controller_state: str = "idle"
    gripper_state: GripperState = "partially_open"
    active_pose: str = "idle"
    current_task: str | None = None
    mode: ExecutionMode = "demo"
    safety_lock: bool = False
    last_error: str | None = None
    last_serial_message: str | None = None
    joints: dict[str, float] = Field(default_factory=lambda: {
        "base": 90.0,
        "shoulder": 90.0,
        "gripper": 45.0,
    })
    zones: dict[str, str] = Field(default_factory=lambda: {
        "left": "available",
        "center": "available",
        "right": "available",
    })


class ParsedIntent(BaseModel):
    intent_name: str
    confidence: float
    entities: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    is_safe: bool = True
    safety_message: str | None = None


class SkillCall(BaseModel):
    skill_name: str
    action_name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    user_visible_text: str
    internal_reasoning_summary: str
    selected_skill: str | None = None
    selected_action: str | None = None
    suggested_next_action: str | None = None


class ChatResponse(BaseModel):
    agent_response: AgentResponse
    parsed_intent: ParsedIntent
    skill_call: SkillCall | None = None
    execution_steps: list[ExecutionStep] = Field(default_factory=list)
    robot_state: RobotState
    logs: list[LogEntry] = Field(default_factory=list)


class Capability(BaseModel):
    name: str
    description: str
    mode_support: list[ExecutionMode]
