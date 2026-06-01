import time
import paramiko
import serial
from typing import Optional
from getpass import getpass
import os
from datetime import datetime
from langchain_core.tools import tool

# Dictionary/List of password prompt keywords (English and Chinese)
# Users can add more keywords here as needed
PASSWORD_PROMPT_KEYWORDS = [
    "password:",
    "Password:",
    "password for",
    "Password for",
    "密码",
    "Enter PIN",
    "Username for",
    "username for",
    "Username:",
    "username:"
]

class ConnectionManager:
    """Manages the active hardware connection (SSH or Serial)."""
    
    def __init__(self):
        self.conn_type: Optional[str] = None
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.serial_client: Optional[serial.Serial] = None
        self.active_channel: Optional[paramiko.Channel] = None
        
        # Callbacks that can be overridden by the Web server
        self.on_output = lambda text: print(text, end='', flush=True)
        self.on_password_request = lambda prompt: getpass(prompt)
        self.on_state_change = lambda state: None
        
    def connect_ssh(self, host: str, username: str, password: Optional[str] = None, port: int = 22):
        self.conn_type = "ssh"
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh_client.connect(hostname=host, port=port, username=username, password=password, timeout=10)
        return f"Successfully connected to SSH at {host}:{port}"

    def connect_serial(self, port: str, baudrate: int = 115200):
        self.conn_type = "serial"
        self.serial_client = serial.Serial(port=port, baudrate=baudrate, timeout=3)
        return f"Successfully connected to Serial port {port} at {baudrate} baud."

    def disconnect(self):
        self.interrupt()
        if getattr(self, 'conn_type', None) == "ssh" and getattr(self, 'ssh_client', None):
            self.ssh_client.close()
            self.ssh_client = None
        elif getattr(self, 'conn_type', None) == "serial" and getattr(self, 'serial_client', None):
            self.serial_client.close()
            self.serial_client = None
        self.conn_type = None
        return "Disconnected successfully."

    def interrupt(self):
        """Interrupts the currently running command on SSH or Serial by sending Ctrl+C (\x03)."""
        if getattr(self, 'active_channel', None):
            try:
                # Send standard Ctrl+C (SIGINT) to the remote PTY
                self.active_channel.sendall(b'\x03')
                self.active_channel.close()
            except Exception as e:
                print(f"Error during SSH command interrupt: {e}")
            finally:
                self.active_channel = None
                
        if self.conn_type == "serial" and self.serial_client:
            try:
                self.serial_client.write(b'\x03')
            except Exception as e:
                print(f"Error during Serial command interrupt: {e}")

    def _log_command(self, command: str):
        try:
            os.makedirs("data", exist_ok=True)
            with open("data/command_history.log", "a", encoding="utf-8") as f:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if self.conn_type == "ssh" and self.ssh_client:
                    try:
                        host = self.ssh_client.get_transport().getpeername()[0]
                    except:
                        host = "Unknown"
                    target = f"SSH:{host}"
                elif self.conn_type == "serial" and self.serial_client:
                    target = f"Serial:{self.serial_client.port}"
                else:
                    target = "Unknown"
                f.write(f"[{timestamp}] [{target}] {command}\n")
        except Exception as e:
            print(f"Failed to log command: {e}")

    def execute(self, command: str) -> str:
        # --- Safety Firewall ---
        # Prevent the LLM from blindly running full-screen interactive CLI apps
        # that would trap our PTY terminal in an infinite display loop.
        import re
        interactive_tools = {"htop", "vi", "vim", "nano", "ncdu", "tmux", "screen", "minicom"}
        blocked_apps = []
        for token in command.split():
            clean_token = re.sub(r'["\'`()&|;<>~]', '', token)
            base_name = os.path.basename(clean_token)
            if base_name in interactive_tools:
                blocked_apps.append(base_name)
                
        if blocked_apps:
            blocked_str = ", ".join(set(blocked_apps))
            return f"Error: Command rejected by safety firewall. '{blocked_str}' is an interactive/full-screen program which causes terminal deadlocks. Please use non-interactive alternatives (e.g., 'cat', 'sed -i', 'top -b -n 1')."
            
        if "sensors-detect" in command and "--auto" not in command:
            return "Error: Command rejected by safety firewall. 'sensors-detect' is interactive and will wait for user input indefinitely, causing the agent to hang. Please use 'sensors-detect --auto' instead."

        self._log_command(command)

        # Notify UI of state change
        self.on_state_change("Executing...")

        try:
            if self.conn_type == "ssh" and self.ssh_client:
                # get_pty=True allows commands like sudo to prompt for a password interactively
                stdin, stdout, stderr = self.ssh_client.exec_command(command, get_pty=True)
                channel = stdout.channel
                self.active_channel = channel
                
                output = ""
                buffer = ""
                
                self.on_output(f"\n--- Executing SSH Command: {command} ---\n")
                
                while True:
                    got_data = False
                    
                    if channel.recv_ready():
                        chunk_bytes = channel.recv(1024)
                        if chunk_bytes:
                            chunk = chunk_bytes.decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            got_data = True
                            self.on_output(chunk)
                    
                    if channel.recv_stderr_ready():
                        chunk_bytes = channel.recv_stderr(1024)
                        if chunk_bytes:
                            chunk = chunk_bytes.decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            got_data = True
                            self.on_output(chunk)
                    
                    # If command has exited and there is no more data to read, we are done
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        break
                    
                    if not got_data:
                        # Stream is paused, check if the last line waits for a password
                        last_line = buffer.split('\n')[-1]
                        if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                            pwd = self.on_password_request("\n[Agent] Remote system is asking for input (password/username). Please enter it: ")
                            # Send password directly into the channel to bypass python file buffering
                            channel.sendall((pwd + '\n').encode('utf-8'))
                            # Clear buffer so we don't prompt again for the same line
                            buffer = ""
                        elif "(END)" in last_line:
                            # Pager has reached the end and is waiting for 'q'
                            channel.sendall(b'q\n')
                            buffer = ""
                            self.on_output("\n[Agent] Detected interactive pager, automatically sending 'q' to exit...\n")
                        else:
                            time.sleep(0.1)
                            
                exit_status = channel.recv_exit_status()
                self.on_output("\n--- Command Finished ---\n")
                
                result = f"Exit Status: {exit_status}\n"
                if output:
                    result += f"OUTPUT:\n{output}\n"
                if not output.strip():
                    result += "Command executed successfully, but produced no output."
                return result
                
            elif self.conn_type == "serial" and self.serial_client:
                # Clear buffer before sending
                self.serial_client.reset_input_buffer()
                # Send command
                cmd_bytes = f"{command}\r\n".encode('utf-8')
                self.serial_client.write(cmd_bytes)
                # Read continuously until 2 seconds of inactivity
                self.on_output(f"\n--- Executing Serial Command: {command} ---\n")
                output = ""
                buffer = ""
                idle_time = 0.0
                
                while idle_time < 2.0:
                    if self.serial_client.in_waiting > 0:
                        idle_time = 0.0
                        try:
                            chunk = self.serial_client.read(self.serial_client.in_waiting).decode('utf-8', errors='replace')
                            output += chunk
                            buffer += chunk
                            self.on_output(chunk)
                        except Exception as e:
                            err_msg = f"\n[Error reading partial output: {e}]\n"
                            output += err_msg
                            self.on_output(err_msg)
                    else:
                        last_line = buffer.split('\n')[-1]
                        if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                            pwd = self.on_password_request("\n[Agent] Serial device is asking for input (password/username). Please enter it: ")
                            self.serial_client.write(f"{pwd}\r\n".encode('utf-8'))
                            buffer = ""
                            idle_time = 0.0
                        elif "(END)" in last_line:
                            # Pager has reached the end
                            self.serial_client.write(b'q\r\n')
                            buffer = ""
                            idle_time = 0.0
                            self.on_output("\n[Agent] Detected interactive pager, automatically sending 'q' to exit...\n")
                        
                        time.sleep(0.1)
                        idle_time += 0.1
                
                self.on_output("\n--- Command Finished ---\n")
                return f"OUTPUT:\n{output}\n" if output.strip() else "Command executed successfully, but produced no output."
            else:
                return "Error: No active connection. Please ensure the agent is connected first."
        finally:
            self.active_channel = None

    def close(self):
        if self.ssh_client:
            self.ssh_client.close()
        if self.serial_client:
            self.serial_client.close()

# Global connection manager instance for the tools to use
DEVICE_MANAGER = ConnectionManager()



@tool
def execute_device_command(command: str) -> str:
    """
    Executes a shell or terminal command on the connected hardware device (via SSH or Serial).
    Use this tool to run commands to diagnose issues, check logs, or configure the device.
    
    Args:
        command: The shell command to run (e.g., 'dmesg', 'lsmod', 'cat /var/log/syslog').
        
    Returns:
        The standard output and standard error from the command execution.
    """
    print(f"\\n[TOOL EXECUTING COMMAND]: {command}")
    try:
        return DEVICE_MANAGER.execute(command)
    except Exception as e:
        return f"Error executing command: {str(e)}"
