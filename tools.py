import time
import paramiko
import serial
from typing import Optional
from getpass import getpass
import os
from datetime import datetime
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig


# Password / credential prompt keywords (English and Chinese).
# When the terminal's last line contains one of these, the manager immediately
# asks the human for the secret and forwards it.
PASSWORD_PROMPT_KEYWORDS = [
    "password:",
    "Password:",
    "password for",
    "Password for",
    "\u5bc6\u7801",  # "密码" (password) in Chinese
    "Enter PIN",
    "Username for",
    "username for",
    "Username:",
    "username:",
]

# Substrings that indicate a yes/no style confirmation, a menu choice, or a
# "press enter" prompt -- the command will hang forever unless something is
# typed. Used to fast-trigger human intervention.
INTERACTIVE_PROMPT_PATTERNS = [
    "[y/n]", "[y]/n", "[yes/no]", "(yes/no)", "(y/n)", "(y/n]",
    "(yes/no/files)", "[yes/no/files]",
    "[default=", "[default:", "press enter", "hit enter", "press return",
    "do you accept", "do you want to continue", "do you want to",
    "are you sure", "proceed?", "proceed (", "continue?",
    "[o/n]", "(o/n)", "go ahead", "y or n",
]

# If a command produces no output for this many seconds and we did not recognise
# a specific prompt, we hand control to the human.
INTERVENTION_IDLE_TIMEOUT = 15.0
# After a "keep waiting" decision, do not bother the human again for this long.
INTERVENTION_COOLDOWN = 45.0
# Minimum idle seconds before reacting to a recognised prompt line, so we do not
# fire on a transient line that happens to end with '?' right before the process
# exits naturally.
PROMPT_DETECT_MIN_IDLE = 0.3


def _cli_intervention(context, session_id=None):
    """Default (CLI) intervention handler: ask on the local terminal."""
    print("\n[Manual Intervention Required] Recent terminal output:")
    print(context or "(no output)")
    print("Type the input to send, or 'abort' to cancel, or 'wait' to keep waiting.")
    try:
        val = input("Your choice: ").strip()
    except Exception:
        return {"action": "wait"}
    low = val.lower()
    if low in ("abort", "a"):
        return {"action": "abort"}
    if low in ("wait", "w", ""):
        return {"action": "wait"}
    return {"action": "send", "input": val}


class Connection:
    """Represents a single hardware connection (SSH or Serial)."""
    def __init__(self):
        self.conn_type: Optional[str] = None
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.serial_client: Optional[serial.Serial] = None
        self.active_channel: Optional[paramiko.Channel] = None

    def close(self):
        if self.ssh_client:
            try:
                self.ssh_client.close()
            except Exception:
                pass
            self.ssh_client = None
        if self.serial_client:
            try:
                self.serial_client.close()
            except Exception:
                pass
            self.serial_client = None
        self.conn_type = None
        self.active_channel = None


class ConnectionManager:
    """Manages session-associated hardware connections (SSH or Serial)."""

    def __init__(self):
        # Dict of session_id -> Connection
        self.connections = {}
        # Default connection for CLI / backward compatibility
        self._default_connection = Connection()

        # Callbacks that can be overridden by the Web server
        self.on_output = lambda text, session_id=None: print(text, end='', flush=True)
        self.on_password_request = lambda prompt, session_id=None: getpass(prompt)
        self.on_state_change = lambda state, session_id=None: None
        # Human-in-the-loop intervention when a command stalls waiting for input.
        # Must return {"action": "send"|"abort"|"wait", "input": "<text>"}.
        self.on_intervention = _cli_intervention

    def get_connection(self, session_id: Optional[str] = None) -> Connection:
        if not session_id:
            return self._default_connection
        if session_id not in self.connections:
            self.connections[session_id] = Connection()
        return self.connections[session_id]

    @property
    def conn_type(self) -> Optional[str]:
        return self._default_connection.conn_type

    @conn_type.setter
    def conn_type(self, value: Optional[str]):
        self._default_connection.conn_type = value

    @property
    def ssh_client(self) -> Optional[paramiko.SSHClient]:
        return self._default_connection.ssh_client

    @ssh_client.setter
    def ssh_client(self, value: Optional[paramiko.SSHClient]):
        self._default_connection.ssh_client = value

    @property
    def serial_client(self) -> Optional[serial.Serial]:
        return self._default_connection.serial_client

    @serial_client.setter
    def serial_client(self, value: Optional[serial.Serial]):
        self._default_connection.serial_client = value

    @property
    def active_channel(self) -> Optional[paramiko.Channel]:
        return self._default_connection.active_channel

    @active_channel.setter
    def active_channel(self, value: Optional[paramiko.Channel]):
        self._default_connection.active_channel = value

    def connect_ssh(self, host: str, username: str, password: Optional[str] = None, port: int = 22, session_id: Optional[str] = None):
        conn = self.get_connection(session_id)
        conn.conn_type = "ssh"
        conn.ssh_client = paramiko.SSHClient()
        conn.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        conn.ssh_client.connect(hostname=host, port=port, username=username, password=password, timeout=10)
        return f"Successfully connected to SSH at {host}:{port}"

    def connect_serial(self, port: str, baudrate: int = 115200, session_id: Optional[str] = None):
        conn = self.get_connection(session_id)
        conn.conn_type = "serial"
        conn.serial_client = serial.Serial(port=port, baudrate=baudrate, timeout=3)
        return f"Successfully connected to Serial port {port} at {baudrate} baud."

    def disconnect(self, session_id: Optional[str] = None):
        self.interrupt(session_id)
        conn = self.get_connection(session_id)
        conn.close()
        return "Disconnected successfully."

    def interrupt(self, session_id: Optional[str] = None):
        """Interrupts the currently running command on SSH or Serial by sending Ctrl+C (\\x03)."""
        conn = self.get_connection(session_id)
        if getattr(conn, 'active_channel', None):
            try:
                conn.active_channel.sendall(b'\x03')
                conn.active_channel.close()
            except Exception as e:
                print(f"Error during SSH command interrupt: {e}")
            finally:
                conn.active_channel = None

        if conn.conn_type == "serial" and conn.serial_client:
            try:
                conn.serial_client.write(b'\x03')
            except Exception as e:
                print(f"Error during Serial command interrupt: {e}")

    def _log_command(self, command: str, session_id: Optional[str] = None):
        try:
            os.makedirs("data", exist_ok=True)
            with open("data/command_history.log", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn = self.get_connection(session_id)
                if conn.conn_type == "ssh" and conn.ssh_client:
                    try:
                        host = conn.ssh_client.get_transport().getpeername()[0]
                    except Exception:
                        host = "Unknown"
                    target = f"SSH:{host}"
                elif conn.conn_type == "serial" and conn.serial_client:
                    target = f"Serial:{conn.serial_client.port}"
                else:
                    target = "Unknown"
                f.write(f"[{timestamp}] [{target}] {command}\n")
        except Exception as e:
            print(f"Failed to log command: {e}")

    # --- Human-in-the-loop intervention helpers -------------------------------

    @staticmethod
    def _last_line(buffer: str) -> str:
        return buffer.split('\n')[-1].strip()

    @staticmethod
    def _looks_like_prompt(line: str) -> bool:
        if not line:
            return False
        low = line.lower()
        if line.endswith("?") and len(line) <= 80:
            return True
        if line.endswith(":") and len(line) <= 60:
            return True
        for pat in INTERACTIVE_PROMPT_PATTERNS:
            if pat.lower() in low:
                return True
        return False

    def _request_intervention(self, context: str, session_id: Optional[str]) -> dict:
        try:
            result = self.on_intervention(context, session_id)
        except Exception:
            return {"action": "wait"}
        if isinstance(result, str):
            return {"action": "send", "input": result}
        if isinstance(result, dict):
            result.setdefault("action", "wait")
            result.setdefault("input", "")
            return result
        return {"action": "wait"}

    # --- Command execution ----------------------------------------------------

    def execute(self, command: str, session_id: Optional[str] = None) -> str:
        # --- Safety Firewall ---
        # Prevent the LLM from blindly running full-screen interactive CLI apps
        # that would trap our PTY terminal in an infinite display loop.
        import re
        interactive_tools = {"htop", "vi", "vim", "nano", "ncdu", "tmux", "screen", "minicom"}
        blocked_apps = []
        for token in command.split():
            clean_token = re.sub(r"['\"`()&|;<>~]", '', token)
            base_name = os.path.basename(clean_token)
            if base_name in interactive_tools:
                blocked_apps.append(base_name)

        if blocked_apps:
            blocked_str = ", ".join(set(blocked_apps))
            return f"Error: Command rejected by safety firewall. '{blocked_str}' is an interactive/full-screen program which causes terminal deadlocks. Please use non-interactive alternatives (e.g., 'cat', 'sed -i', 'top -b -n 1')."

        if "sensors-detect" in command and "--auto" not in command:
            return "Error: Command rejected by safety firewall. 'sensors-detect' is interactive and will wait for user input indefinitely, causing the agent to hang. Please use 'sensors-detect --auto' instead."

        self._log_command(command, session_id)
        self.on_state_change("Executing...", session_id)

        conn = self.get_connection(session_id)

        try:
            if conn.conn_type == "ssh" and conn.ssh_client:
                # get_pty=True allows commands like sudo to prompt for a password interactively
                stdin, stdout, stderr = conn.ssh_client.exec_command(command, get_pty=True)
                channel = stdout.channel
                conn.active_channel = channel

                output = ""
                buffer = ""
                idle_seconds = 0.0
                last_intervention = 0.0

                self.on_output(f"\n--- Executing SSH Command: {command} ---\n", session_id)

                while True:
                    got_data = False

                    if channel.recv_ready():
                        chunk_bytes = channel.recv(1024)
                        if chunk_bytes:
                            chunk = chunk_bytes.decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            got_data = True
                            idle_seconds = 0.0
                            self.on_output(chunk, session_id)

                    if channel.recv_stderr_ready():
                        chunk_bytes = channel.recv_stderr(1024)
                        if chunk_bytes:
                            chunk = chunk_bytes.decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            got_data = True
                            idle_seconds = 0.0
                            self.on_output(chunk, session_id)

                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break

                    if got_data:
                        continue

                    last_line = self._last_line(buffer)

                    # Known password prompt -> ask for credentials
                    if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                        pwd = self.on_password_request(
                            "\n[Agent] Remote system is asking for a password/username. Please enter it: ",
                            session_id,
                        )
                        channel.sendall((pwd + "\n").encode("utf-8"))
                        buffer = ""
                        idle_seconds = 0.0
                        continue

                    # Known interactive pager stuck at (END)
                    if "(END)" in last_line:
                        channel.sendall(b"q\n")
                        buffer = ""
                        idle_seconds = 0.0
                        self.on_output("\n[Agent] Detected interactive pager, automatically sending 'q' to exit...\n", session_id)
                        continue

                    # Recognised yes/no style prompt -> fast human intervention
                    if idle_seconds >= PROMPT_DETECT_MIN_IDLE and self._looks_like_prompt(last_line):
                        decision = self._request_intervention(last_line, session_id)
                        if decision["action"] == "abort":
                            try:
                                channel.sendall(b"\x03")
                            except Exception:
                                pass
                            self.on_output("\n[Agent] Command aborted by user.\n", session_id)
                            break
                        elif decision["action"] == "send":
                            channel.sendall((decision.get("input", "") + "\n").encode("utf-8"))
                            self.on_output(f"\n[Agent] Sent user input: {decision.get('input', '')!r}\n", session_id)
                        buffer = ""
                        idle_seconds = 0.0
                        last_intervention = time.time()
                        continue

                    # Generic stall fallback -> human intervention
                    if idle_seconds >= INTERVENTION_IDLE_TIMEOUT and (time.time() - last_intervention) >= INTERVENTION_COOLDOWN:
                        context = "\n".join(buffer.strip().split("\n")[-8:]) or "(no output yet)"
                        decision = self._request_intervention(context, session_id)
                        if decision["action"] == "abort":
                            try:
                                channel.sendall(b"\x03")
                            except Exception:
                                pass
                            self.on_output("\n[Agent] Command aborted by user.\n", session_id)
                            break
                        elif decision["action"] == "send":
                            channel.sendall((decision.get("input", "") + "\n").encode("utf-8"))
                            self.on_output(f"\n[Agent] Sent user input: {decision.get('input', '')!r}\n", session_id)
                        buffer = ""
                        idle_seconds = 0.0
                        last_intervention = time.time()
                        continue

                    time.sleep(0.1)
                    idle_seconds += 0.1

                exit_status = channel.recv_exit_status()
                self.on_output("\n--- Command Finished ---\n", session_id)

                result = f"Exit Status: {exit_status}\n"
                if output:
                    result += f"OUTPUT:\n{output}\n"
                if not output.strip():
                    result += "Command executed successfully, but produced no output."
                return result

            elif conn.conn_type == "serial" and conn.serial_client:
                # Clear buffer before sending
                conn.serial_client.reset_input_buffer()
                cmd_bytes = f"{command}\r\n".encode('utf-8')
                conn.serial_client.write(cmd_bytes)

                self.on_output(f"\n--- Executing Serial Command: {command} ---\n", session_id)
                output = ""
                buffer = ""
                idle_time = 0.0
                last_intervention = 0.0
                SERIAL_DONE_TIMEOUT = 2.0

                while True:
                    if conn.serial_client.in_waiting > 0:
                        idle_time = 0.0
                        try:
                            chunk = conn.serial_client.read(conn.serial_client.in_waiting).decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            self.on_output(chunk, session_id)
                        except Exception as e:
                            err_msg = f"\n[Error reading partial output: {e}]\n"
                            output += err_msg
                            self.on_output(err_msg, session_id)
                        continue

                    last_line = self._last_line(buffer)

                    if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                        pwd = self.on_password_request(
                            "\n[Agent] Serial device is asking for a password/username. Please enter it: ",
                            session_id,
                        )
                        conn.serial_client.write(f"{pwd}\r\n".encode("utf-8"))
                        buffer = ""
                        idle_time = 0.0
                        continue

                    if "(END)" in last_line:
                        conn.serial_client.write(b"q\r\n")
                        buffer = ""
                        idle_time = 0.0
                        self.on_output("\n[Agent] Detected interactive pager, automatically sending 'q' to exit...\n", session_id)
                        continue

                    if idle_time >= PROMPT_DETECT_MIN_IDLE and self._looks_like_prompt(last_line):
                        decision = self._request_intervention(last_line, session_id)
                        if decision["action"] == "abort":
                            try:
                                conn.serial_client.write(b"\x03")
                            except Exception:
                                pass
                            self.on_output("\n[Agent] Command aborted by user.\n", session_id)
                            break
                        elif decision["action"] == "send":
                            conn.serial_client.write((decision.get("input", "") + "\r\n").encode("utf-8"))
                            self.on_output(f"\n[Agent] Sent user input: {decision.get('input', '')!r}\n", session_id)
                        buffer = ""
                        idle_time = 0.0
                        last_intervention = time.time()
                        continue

                    if idle_time >= SERIAL_DONE_TIMEOUT:
                        break

                    time.sleep(0.1)
                    idle_time += 0.1

                self.on_output("\n--- Command Finished ---\n", session_id)
                return f"OUTPUT:\n{output}\n" if output.strip() else "Command executed successfully, but produced no output."
            else:
                return "Error: No active connection. Please ensure the agent is connected first."
        finally:
            conn.active_channel = None

    def close(self):
        self._default_connection.close()
        for conn in self.connections.values():
            conn.close()
        self.connections.clear()


# Global connection manager instance for the tools to use
DEVICE_MANAGER = ConnectionManager()


@tool
def execute_device_command(command: str, config: RunnableConfig) -> str:
    """
    Executes a shell or terminal command on the connected hardware device (via SSH or Serial).
    Use this tool to run commands to diagnose issues, check logs, or configure the device.

    Args:
        command: The shell command to run (e.g., 'dmesg', 'lsmod', 'cat /var/log/syslog').

    Returns:
        The standard output and standard error from the command execution.
    """
    print(f"\n[TOOL EXECUTING COMMAND]: {command}")
    session_id = config.get("configurable", {}).get("session_id")
    try:
        return DEVICE_MANAGER.execute(command, session_id=session_id)
    except Exception as e:
        return f"Error executing command: {str(e)}"