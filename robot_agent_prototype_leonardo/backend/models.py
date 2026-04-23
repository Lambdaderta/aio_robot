from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GripperState = Literal["open", "closed", "partially_open"]
ExecutionStatus = Literal["queued", "running", "completed", "failed", "blocked"]
TelemetrySource = Literal["offline", "arduino"]
ChatRole = Literal["user", "assistant", "tool"]


class ConnectHardwareRequest(BaseModel):
    port: str
    baud_rate: int = 115200


class ServoSetRequest(BaseModel):
    joint_name: str
    angle: float


class JointPoseRequest(BaseModel):
    joints: dict[str, float] = Field(default_factory=dict)


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


class RobotState(BaseModel):
    arm_online: bool = False
    controller_connected: bool = False
    hardware_connected: bool = False
    hardware_port: str | None = None
    baud_rate: int = 115200
    firmware_ready: bool = False
    telemetry_source: TelemetrySource = "offline"
    last_seen_at: datetime | None = None
    controller_state: str = "idle"
    gripper_state: GripperState = "partially_open"
    active_pose: str = "unknown"
    current_task: str | None = None
    safety_lock: bool = False
    last_error: str | None = None
    last_serial_message: str | None = None
    joints: dict[str, float] = Field(default_factory=lambda: {
        "base": 0.0,
        "shoulder": 90.0,
        "gripper": 45.0,
    })


class AssistantToolFunction(BaseModel):
    name: str
    arguments: str = "{}"


class AssistantToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: AssistantToolFunction


class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    content: str = ""
    created_at: datetime
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[AssistantToolCall] = Field(default_factory=list)


class ChatSession(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessage] = Field(default_factory=list)


class ChatSessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ChatRuntimeConfig(BaseModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = ""
    temperature: float = 0.2
    tools_enabled: bool = True
    system_prompt: str = ""


class UpdateChatRuntimeConfigRequest(BaseModel):
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None
    tools_enabled: bool | None = None
    system_prompt: str | None = None


class CreateChatSessionRequest(BaseModel):
    title: str | None = None


class ChatUserMessageRequest(BaseModel):
    content: str


class ChatModelInfo(BaseModel):
    id: str
    owned_by: str | None = None
    object: str = "model"


class LoadChatModelRequest(BaseModel):
    model: str


class AssistantToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
