import asyncio
import os
import threading
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from langchain_core.messages import HumanMessage
from agent import build_hardware_agent
from tools import DEVICE_MANAGER

app = FastAPI()

from fastapi.responses import FileResponse

# Mount the static directory for the frontend files
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")

class ConnectRequest(BaseModel):
    conn_type: str
    host: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    port: Optional[int] = 22
    serial_port: Optional[str] = None
    baudrate: Optional[int] = 115200

class PasswordSubmit(BaseModel):
    password: str

# Global state
agent_app = None
messages = []
active_ws: Optional[WebSocket] = None
main_loop: Optional[asyncio.AbstractEventLoop] = None

# Threading mechanism for intercepting password prompts
password_event = threading.Event()
password_value = ""

def web_on_output(text: str):
    """Callback to stream terminal prints to the web interface"""
    if active_ws and main_loop:
        # Pushed from a background worker thread to the async main loop
        asyncio.run_coroutine_threadsafe(
            active_ws.send_json({"type": "log", "content": text}),
            main_loop
        )
    else:
        print(text, end='', flush=True)

def web_on_password_request(prompt: str) -> str:
    """Callback to request a password from the web user and block until submitted"""
    global password_value
    
    if active_ws and main_loop:
        # Notify the UI to show the password prompt modal
        asyncio.run_coroutine_threadsafe(
            active_ws.send_json({"type": "password_request", "prompt": prompt}),
            main_loop
        )
        # Block the current tool execution thread until the API sets the event
        password_event.clear()
        password_event.wait()
        return password_value
    else:
        # Fallback to CLI getpass if the web is disjointed
        from getpass import getpass
        return getpass(prompt)

# Override default CLI behavior
DEVICE_MANAGER.on_output = web_on_output
DEVICE_MANAGER.on_password_request = web_on_password_request

@app.on_event("startup")
def startup_event():
    global main_loop, agent_app
    main_loop = asyncio.get_running_loop()
    try:
        agent_app = build_hardware_agent()
        print("LangGraph Agent loaded successfully.")
    except Exception as e:
        print(f"Agent failed to build: {e}")
        print("Ensure DEEPSEEK_API_KEY is configured in .env")

@app.get("/api/status")
async def get_status():
    status = {"connected": False}
    if DEVICE_MANAGER.conn_type == "ssh" and getattr(DEVICE_MANAGER, 'ssh_client', None):
        try:
            host = DEVICE_MANAGER.ssh_client.get_transport().getpeername()[0]
        except:
            host = "Unknown"
        status.update({"connected": True, "conn_type": "ssh", "message": f"Connected to SSH at {host}"})
    elif DEVICE_MANAGER.conn_type == "serial" and getattr(DEVICE_MANAGER, 'serial_client', None):
        status.update({"connected": True, "conn_type": "serial", "message": f"Connected to Serial port {DEVICE_MANAGER.serial_client.port}"})
    
    # Restore chat history
    history = []
    for m in messages:
        if isinstance(m, HumanMessage):
            history.append({"type": "user_message", "content": m.content})
        elif hasattr(m, 'tool_calls') and m.tool_calls:
            for tc in m.tool_calls:
                history.append({"type": "tool_call", "name": tc["name"], "args": tc["args"]})
        elif getattr(m, 'content', None):
            history.append({"type": "agent_message", "content": m.content})
    status["history"] = history
    return status

@app.post("/api/connect")
async def connect(req: ConnectRequest):
    try:
        if req.conn_type == "ssh":
            msg = DEVICE_MANAGER.connect_ssh(req.host, req.username, req.password, req.port)
        elif req.conn_type == "serial":
            msg = DEVICE_MANAGER.connect_serial(req.serial_port, req.baudrate)
        else:
            return {"status": "error", "message": "Unknown conn_type"}
        return {"status": "success", "message": msg}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/disconnect")
async def disconnect_hardware():
    try:
        DEVICE_MANAGER.disconnect()
        return {"status": "success", "message": "Disconnected"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/password")
async def submit_password(req: PasswordSubmit):
    """Receives the password from the web modal and unblocks the executing action."""
    global password_value
    password_value = req.password
    password_event.set()
    return {"status": "ok"}

@app.websocket("/ws/chat")
async def websocket_endpoint(ws: WebSocket):
    global active_ws, messages
    await ws.accept()
    active_ws = ws
    
    try:
        while True:
            data = await ws.receive_text()
            # Clear messages if restart is desired? For now just append
            messages.append(HumanMessage(content=data))
            
            await ws.send_json({"type": "status", "content": "Agent Thinking..."})
            await ws.send_json({"type": "user_message", "content": data})
            
            try:
                # astream handles async execution, running tools in background threads
                async for event in agent_app.astream({"messages": messages}):
                    for node_name, node_state in event.items():
                        if node_name == "agent":
                            msg = node_state["messages"][-1]
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    await ws.send_json({
                                        "type": "tool_call", 
                                        "name": tc["name"], 
                                        "args": tc["args"]
                                    })
                            else:
                                await ws.send_json({
                                    "type": "agent_message", 
                                    "content": msg.content
                                })
                        elif node_name in ["tools", "invalid_tools"]:
                            await ws.send_json({"type": "status", "content": "Tool execution finished. Agent processing results..."})
                        
                        # Accumulate context
                        new_msgs = node_state["messages"]
                        if isinstance(new_msgs, list):
                            # In LangGraph with operator.add, it typically passes the diff
                            messages.extend(new_msgs)
                        else:
                            messages.append(new_msgs)
                            
                await ws.send_json({"type": "status", "content": "Ready"})
            except Exception as e:
                await ws.send_json({"type": "error", "content": f"Graph Execution Error: {str(e)}"})

    except WebSocketDisconnect:
        if active_ws == ws:
            active_ws = None
            
if __name__ == "__main__":
    import uvicorn
    print("Starting Web Server at http://localhost:8000/static/index.html")
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=True)
