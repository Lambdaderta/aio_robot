from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .agent import pseudo_agent
from .control.serial_adapter import SerialAdapterError
from .control.service import control_service
from .models import (
    ChatRequest,
    ChatResponse,
    ConnectHardwareRequest,
    JointPoseRequest,
    ServoSetRequest,
    SetModeRequest,
    SkillCall,
)
from .skills.robot_arm import robot_arm_skill
from .state import app_state

app = FastAPI(title="Robot Agent Prototype API", version="0.3.0")

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
        "capabilities": robot_arm_skill.get_capabilities(),
        "logs": app_state.list_logs(30),
        "joint_limits": control_service.get_joint_limits(),
    }


@app.get("/api/logs")
def get_logs(limit: int = 50):
    return {"logs": app_state.list_logs(limit)}


@app.get("/api/capabilities")
def get_capabilities():
    return {"capabilities": robot_arm_skill.get_capabilities()}


@app.post("/api/mode")
def set_mode(payload: SetModeRequest):
    state = app_state.update_robot_state(mode=payload.mode, last_error=None)
    app_state.add_log("system", "Execution mode changed", context={"mode": payload.mode})
    return {"robot_state": state}


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
    skill_call = SkillCall(
        skill_name="robot_arm",
        action_name="move_joint",
        parameters={"joint_name": payload.joint_name, "angle": payload.angle},
    )

    valid, error = robot_arm_skill.validate_action(skill_call)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    steps = robot_arm_skill.handle_action(skill_call)
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


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    outcome = pseudo_agent.handle_message(payload.message)
    return ChatResponse(
        agent_response=outcome.response,
        parsed_intent=outcome.parsed_intent,
        skill_call=outcome.skill_call,
        execution_steps=outcome.execution_steps,
        robot_state=app_state.get_robot_state(),
        logs=app_state.list_logs(15),
    )
