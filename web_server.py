import asyncio
import os
import json
import uuid
import threading
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages import messages_from_dict, messages_to_dict
from agent import build_hardware_agent
from llm import get_llm
from tools import DEVICE_MANAGER

app = FastAPI()

# Mount the static directory for the frontend files
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")


class ConnectRequest(BaseModel):
    session_id: str
    conn_type: str
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    port: Optional[int] = 22
    serial_port: Optional[str] = None
    baudrate: Optional[int] = 115200


class PasswordSubmit(BaseModel):
    password: str


# Session Management
class SessionManager:
    def __init__(self, data_dir="data/sessions"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def list_sessions(self):
        sessions = []
        for filename in os.listdir(self.data_dir):
            if filename.endswith(".json"):
                with open(
                    os.path.join(self.data_dir, filename), "r", encoding="utf-8"
                ) as f:
                    try:
                        data = json.load(f)
                        sessions.append(
                            {
                                "session_id": data.get("session_id"),
                                "name": data.get("name", "Unknown Session"),
                                "conn_type": data.get("conn_type"),
                                "host": data.get("connection_params", {}).get("host"),
                                "username": data.get("connection_params", {}).get(
                                    "username"
                                ),
                                "serial_port": data.get("connection_params", {}).get(
                                    "serial_port"
                                ),
                                "updated_at": data.get("updated_at"),
                            }
                        )
                    except:
                        pass
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)

    def create_session(self, name="New Session"):
        session_id = str(uuid.uuid4())
        data = {
            "session_id": session_id,
            "name": name,
            "conn_type": None,
            "connection_params": {},
            "messages": [],
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }
        self.save_session(session_id, data)
        return session_id

    def load_session(self, session_id):
        filepath = os.path.join(self.data_dir, f"{session_id}.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def save_session(self, session_id, data):
        data["updated_at"] = datetime.now().isoformat()
        filepath = os.path.join(self.data_dir, f"{session_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


SESSION_MANAGER = SessionManager()

# Global state
agent_app = None
active_ws: Optional[WebSocket] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None
active_sessions = {}  # session_id -> messages list
active_agent_tasks = {}  # session_id -> active asyncio.Task running the agent graph
active_session_id = None  # Tracks the currently connected session for callbacks

# Threading mechanism for intercepting password prompts
password_event = threading.Event()
password_value = ""


def web_on_output(text: str):
    """Callback to stream terminal prints to the web interface"""
    if active_ws and main_loop:
        asyncio.run_coroutine_threadsafe(
            active_ws.send_json({"type": "log", "content": text}), main_loop
        )
    else:
        print(text, end="", flush=True)


def web_on_password_request(prompt: str) -> str:
    """Callback to request a password from the web user and block until submitted"""
    global password_value

    if active_ws and main_loop:
        asyncio.run_coroutine_threadsafe(
            active_ws.send_json({"type": "password_request", "prompt": prompt}),
            main_loop,
        )
        password_event.clear()
        password_event.wait()
        return password_value
    else:
        from getpass import getpass

        return getpass(prompt)


def web_on_state_change(state: str):
    """Callback to update the agent's current busy action status in the UI"""
    if active_ws and main_loop:
        asyncio.run_coroutine_threadsafe(
            active_ws.send_json({"type": "status", "content": state}), main_loop
        )


# Override default CLI behavior
DEVICE_MANAGER.on_output = web_on_output
DEVICE_MANAGER.on_password_request = web_on_password_request
DEVICE_MANAGER.on_state_change = web_on_state_change


class InterruptRequest(BaseModel):
    session_id: str


@app.on_event("startup")
def startup_event():
    global main_loop, agent_app
    main_loop = asyncio.get_running_loop()
    try:
        agent_app = build_hardware_agent()
        print("LangGraph Agent loaded successfully.")
    except Exception as e:
        print(f"Agent failed to build: {e}")


@app.get("/api/sessions")
async def list_sessions():
    return {"status": "success", "sessions": SESSION_MANAGER.list_sessions()}


@app.post("/api/sessions")
async def create_session():
    session_id = SESSION_MANAGER.create_session()
    active_sessions[session_id] = []
    return {"status": "success", "session_id": session_id}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    data = SESSION_MANAGER.load_session(session_id)
    if data:
        # Load messages into memory if not already there
        if session_id not in active_sessions:
            try:
                active_sessions[session_id] = messages_from_dict(
                    data.get("messages", [])
                )
            except Exception as e:
                print("Error loading messages:", e)
                active_sessions[session_id] = []

        # Serialize history for frontend display
        history = []
        for m in active_sessions[session_id]:
            if isinstance(m, HumanMessage):
                history.append({"type": "user_message", "content": m.content})
            elif hasattr(m, "tool_calls") and m.tool_calls:
                for tc in m.tool_calls:
                    history.append(
                        {"type": "tool_call", "name": tc["name"], "args": tc["args"]}
                    )
            elif getattr(m, "content", None):
                history.append({"type": "agent_message", "content": m.content})

        data["history"] = history
        return {"status": "success", "session": data}
    return {"status": "error", "message": "Session not found"}


@app.get("/api/status")
async def get_status():
    status = {"connected": False, "active_session_id": active_session_id}
    if DEVICE_MANAGER.conn_type == "ssh" and getattr(
        DEVICE_MANAGER, "ssh_client", None
    ):
        try:
            host = DEVICE_MANAGER.ssh_client.get_transport().getpeername()[0]
        except:
            host = "Unknown"
        status.update(
            {
                "connected": True,
                "conn_type": "ssh",
                "message": f"Connected to SSH at {host}",
            }
        )
    elif DEVICE_MANAGER.conn_type == "serial" and getattr(
        DEVICE_MANAGER, "serial_client", None
    ):
        status.update(
            {
                "connected": True,
                "conn_type": "serial",
                "message": f"Connected to Serial port {DEVICE_MANAGER.serial_client.port}",
            }
        )

    return status


@app.post("/api/connect")
async def connect(req: ConnectRequest):
    global active_session_id
    try:
        session_data = SESSION_MANAGER.load_session(req.session_id)
        if not session_data:
            return {"status": "error", "message": "Session not found"}

        if req.conn_type == "ssh":
            msg = DEVICE_MANAGER.connect_ssh(
                req.host, req.username, req.password, req.port
            )
            session_data["conn_type"] = "ssh"
            session_data["connection_params"] = {
                "host": req.host,
                "username": req.username,
                "port": req.port,
            }
            if session_data["name"] == "New Session":
                session_data["name"] = f"SSH: {req.host}"
        elif req.conn_type == "serial":
            msg = DEVICE_MANAGER.connect_serial(req.serial_port, req.baudrate)
            session_data["conn_type"] = "serial"
            session_data["connection_params"] = {
                "serial_port": req.serial_port,
                "baudrate": req.baudrate,
            }
            if session_data["name"] == "New Session":
                session_data["name"] = f"Serial: {req.serial_port}"
        else:
            return {"status": "error", "message": "Unknown conn_type"}

        SESSION_MANAGER.save_session(req.session_id, session_data)
        active_session_id = req.session_id
        return {"status": "success", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/disconnect")
async def disconnect_hardware():
    global active_session_id
    try:
        DEVICE_MANAGER.disconnect()
        active_session_id = None
        return {"status": "success", "message": "Disconnected"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/password")
async def submit_password(req: PasswordSubmit):
    global password_value
    password_value = req.password
    password_event.set()
    return {"status": "ok"}


@app.post("/api/interrupt")
async def interrupt_execution(req: InterruptRequest):
    session_id = req.session_id
    interrupted = False
    
    # 1. Cancel the active async graph task
    if session_id in active_agent_tasks:
        task = active_agent_tasks[session_id]
        task.cancel()
        interrupted = True
        
    # 2. Terminate any running SSH/Serial hardware command
    DEVICE_MANAGER.interrupt()
    
    # Ensure any blocking password prompts are released
    password_event.set()
    
    return {"status": "success", "interrupted": interrupted}


async def summarize_context(messages, max_turns=20):
    if len(messages) <= max_turns:
        return messages

    keep_count = 6
    messages_to_summarize = messages[:-keep_count]
    messages_to_keep = messages[-keep_count:]

    llm = get_llm()
    summary_prompt = "Please summarize the following conversation history concisely, retaining all important technical context, commands executed, and current state. This summary will be used as context for the continuation of the conversation."

    text_to_summarize = "\n".join(
        [
            f"{type(m).__name__}: {m.content}"
            for m in messages_to_summarize
            if getattr(m, "content", None)
        ]
    )

    prompt = [
        SystemMessage(content=summary_prompt),
        HumanMessage(content=text_to_summarize),
    ]
    summary_response = await llm.ainvoke(prompt)

    summary_msg = SystemMessage(
        content=f"Previous Conversation Summary:\n{summary_response.content}"
    )
    return [summary_msg] + messages_to_keep


async def run_agent_workflow(session_id: str, ws: WebSocket, messages: list):
    try:
        config = {"recursion_limit": 500}
        async for event in agent_app.astream(
            {"messages": messages}, config=config
        ):
            for node_name, node_state in event.items():
                if node_name == "agent":
                    msg = node_state["messages"][-1]
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            await ws.send_json(
                                {
                                    "type": "tool_call",
                                    "name": tc["name"],
                                    "args": tc["args"],
                                }
                            )
                    else:
                        await ws.send_json(
                            {"type": "agent_message", "content": msg.content}
                        )
                elif node_name in ["tools", "invalid_tools"]:
                    await ws.send_json(
                        {
                            "type": "status",
                            "content": "Thinking...",
                        }
                    )

                new_msgs = node_state["messages"]
                if isinstance(new_msgs, list):
                    messages.extend(new_msgs)
                else:
                    messages.append(new_msgs)

        await ws.send_json({"type": "status", "content": "Ready"})

        # Save session
        session_data = SESSION_MANAGER.load_session(session_id)
        if session_data:
            session_data["messages"] = messages_to_dict(messages)
            SESSION_MANAGER.save_session(session_id, session_data)

    except asyncio.CancelledError:
        # Gracefully handle task cancellation (user clicked Stop)
        await ws.send_json({"type": "status", "content": "Ready"})
        await ws.send_json({"type": "agent_message", "content": "⚠️ Execution interrupted by user."})
        session_data = SESSION_MANAGER.load_session(session_id)
        if session_data:
            session_data["messages"] = messages_to_dict(messages)
            SESSION_MANAGER.save_session(session_id, session_data)
        raise
    except Exception as e:
        await ws.send_json(
            {"type": "error", "content": f"Graph Execution Error: {str(e)}"}
        )


@app.websocket("/ws/chat")
async def websocket_endpoint(ws: WebSocket, session_id: str = Query(...)):
    global active_ws
    await ws.accept()
    active_ws = ws

    if session_id not in active_sessions:
        data = SESSION_MANAGER.load_session(session_id)
        if data:
            try:
                active_sessions[session_id] = messages_from_dict(
                    data.get("messages", [])
                )
            except Exception:
                active_sessions[session_id] = []
        else:
            active_sessions[session_id] = []

    messages = active_sessions[session_id]

    try:
        while True:
            data = await ws.receive_text()
            messages.append(HumanMessage(content=data))

            await ws.send_json({"type": "status", "content": "Thinking..."})
            await ws.send_json({"type": "user_message", "content": data})

            # Context Summarization
            messages = await summarize_context(messages, max_turns=20)
            active_sessions[session_id] = messages

            # Run agent as a cancelable async task
            task = asyncio.create_task(run_agent_workflow(session_id, ws, messages))
            active_agent_tasks[session_id] = task
            try:
                await task
            except asyncio.CancelledError:
                print(f"Session {session_id} run task was cancelled.")
            finally:
                active_agent_tasks.pop(session_id, None)

    except WebSocketDisconnect:
        if active_ws == ws:
            active_ws = None


if __name__ == "__main__":
    import uvicorn

    print("Starting Web Server at http://localhost:8000/")
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=True)
