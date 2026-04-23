from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .chat_service import chat_service
from .control.serial_adapter import SerialAdapterError
from .control.service import control_service
from .lmstudio_service import LMStudioServiceError, lmstudio_service
from .models import (
    AssistantToolCallRequest,
    ChatUserMessageRequest,
    ConnectHardwareRequest,
    CreateChatSessionRequest,
    JointPoseRequest,
    LoadChatModelRequest,
    ServoSetRequest,
    UpdateChatRuntimeConfigRequest,
)
from .state import app_state

app = FastAPI(title="Robot Arm Hardware API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/status")
def get_status():
    robot_state = control_service.refresh_status()
    return {
        "robot_state": robot_state,
        "logs": app_state.list_logs(30),
        "joint_limits": control_service.get_joint_limits(),
        "supported_presets": control_service.get_supported_presets(),
    }


@app.get("/api/logs")
def get_logs(limit: int = 50):
    return {"logs": app_state.list_logs(limit)}


@app.get("/api/hardware/ports")
def list_hardware_ports():
    return {"ports": control_service.list_ports()}


@app.post("/api/hardware/connect")
def connect_hardware(payload: ConnectHardwareRequest):
    try:
        state = control_service.connect_hardware(payload.port, payload.baud_rate)
    except SerialAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"robot_state": state, "logs": app_state.list_logs(20)}


@app.post("/api/hardware/disconnect")
def disconnect_hardware():
    state = control_service.disconnect_hardware()
    return {"robot_state": state, "logs": app_state.list_logs(20)}


@app.post("/api/manual/joint")
def set_joint(payload: ServoSetRequest):
    valid, error = control_service.validate_joint_move(payload.joint_name, payload.angle)
    if not valid and error:
        raise HTTPException(status_code=400, detail=error)

    steps = control_service.execute_action("move_joint", {"joint_name": payload.joint_name, "angle": payload.angle})
    return {"robot_state": app_state.get_robot_state(), "steps": steps, "logs": app_state.list_logs(20)}


@app.post("/api/manual/pose")
def set_pose(payload: JointPoseRequest):
    try:
        steps = control_service.apply_joint_pose(payload.joints)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"robot_state": app_state.get_robot_state(), "steps": steps, "logs": app_state.list_logs(20)}


@app.post("/api/manual/preset/{preset_name}")
def run_preset(preset_name: str):
    try:
        steps = control_service.execute_manual_preset(preset_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"robot_state": app_state.get_robot_state(), "steps": steps, "logs": app_state.list_logs(20)}


@app.post("/api/manual/stop")
def stop_motion():
    steps = control_service.execute_action("stop", {})
    return {"robot_state": app_state.get_robot_state(), "steps": steps, "logs": app_state.list_logs(20)}


@app.get("/api/chat/config")
def get_chat_config():
    return {
        "config": chat_service.get_config(),
        "tools": chat_service.get_tool_specs(),
    }


@app.get("/api/assistant/tools")
def list_assistant_tools():
    return {
        "tools": chat_service.get_tool_specs(include_ui_tools=False),
    }


@app.post("/api/assistant/tools/call")
def call_assistant_tool(payload: AssistantToolCallRequest):
    try:
        result = chat_service.dispatch_external_tool(payload.name, payload.arguments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"result": result}


@app.post("/api/chat/config")
def update_chat_config(payload: UpdateChatRuntimeConfigRequest):
    try:
        config = chat_service.update_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "config": config,
        "tools": chat_service.get_tool_specs(),
    }


@app.get("/api/chat/models")
def list_chat_models():
    try:
        models = chat_service.list_models()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"models": models}


@app.get("/api/chat/runtime")
def get_chat_runtime():
    try:
        runtime = lmstudio_service.get_runtime()
    except LMStudioServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return runtime


@app.post("/api/chat/runtime/start")
def start_chat_runtime():
    try:
        runtime = lmstudio_service.start_server()
    except LMStudioServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return runtime


@app.post("/api/chat/runtime/load")
def load_chat_model(payload: LoadChatModelRequest):
    try:
        runtime = lmstudio_service.load_model(payload.model)
        chat_service.ensure_runtime_model(payload.model)
    except LMStudioServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return runtime


@app.get("/api/chat/sessions")
def list_chat_sessions():
    return {"sessions": chat_service.list_sessions()}


@app.post("/api/chat/sessions")
def create_chat_session(payload: CreateChatSessionRequest):
    session = chat_service.create_session(payload.title)
    return {
        "session": session,
        "summary": {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": len(session.messages),
        },
    }


@app.get("/api/chat/sessions/{session_id}")
def get_chat_session(session_id: str):
    try:
        session = chat_service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    return {"session": session}


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: str):
    try:
        chat_service.delete_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    return {"ok": True}


@app.post("/api/chat/sessions/{session_id}/messages")
def post_chat_message(session_id: str, payload: ChatUserMessageRequest):
    try:
        session = chat_service.post_user_message(session_id, payload.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "session": session,
        "summary": {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "message_count": len(session.messages),
        },
    }
