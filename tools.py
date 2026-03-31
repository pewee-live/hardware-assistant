import time
import paramiko
import serial
from typing import Optional
from getpass import getpass
from langchain_core.tools import tool

# Dictionary/List of password prompt keywords (English and Chinese)
# Users can add more keywords here as needed
PASSWORD_PROMPT_KEYWORDS = [
    "password:",
    "Password:",
    "password for",
    "Password for",
    "密码",
    "Enter PIN"
]

class ConnectionManager:
    """Manages the active hardware connection (SSH or Serial)."""
    
    def __init__(self):
        self.conn_type: Optional[str] = None
        self.ssh_client: Optional[paramiko.SSHClient] = None
        self.serial_client: Optional[serial.Serial] = None
        
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

    def execute(self, command: str) -> str:
        if self.conn_type == "ssh" and self.ssh_client:
            # get_pty=True allows commands like sudo to prompt for a password interactively
            stdin, stdout, stderr = self.ssh_client.exec_command(command, get_pty=True)
            channel = stdout.channel
            
            output = ""
            buffer = ""
            
            print(f"\n--- Executing SSH Command: {command} ---")
            
            while True:
                got_data = False
                
                if channel.recv_ready():
                    chunk_bytes = channel.recv(1024)
                    if chunk_bytes:
                        chunk = chunk_bytes.decode('utf-8', errors='replace')
                        output += chunk
                        buffer += chunk
                        got_data = True
                        print(chunk, end='', flush=True)
                
                if channel.recv_stderr_ready():
                    chunk_bytes = channel.recv_stderr(1024)
                    if chunk_bytes:
                        chunk = chunk_bytes.decode('utf-8', errors='replace')
                        output += chunk
                        buffer += chunk
                        got_data = True
                        print(chunk, end='', flush=True)
                
                # If command has exited and there is no more data to read, we are done
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                
                if not got_data:
                    # Stream is paused, check if the last line waits for a password
                    last_line = buffer.split('\n')[-1]
                    if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                        pwd = getpass("\n[Agent] Remote system is asking for a password. Please enter it: ")
                        # Send password directly into the channel to bypass python file buffering
                        channel.sendall((pwd + '\n').encode('utf-8'))
                        # Clear buffer so we don't prompt again for the same line
                        buffer = ""
                    else:
                        time.sleep(0.1)
                        
            exit_status = channel.recv_exit_status()
            print("\n--- Command Finished ---")
            
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
            print(f"\n--- Executing Serial Command: {command} ---")
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
                        print(chunk, end='', flush=True)
                    except Exception as e:
                        err_msg = f"\n[Error reading partial output: {e}]\n"
                        output += err_msg
                        print(err_msg, end='', flush=True)
                else:
                    last_line = buffer.split('\n')[-1]
                    if any(kw in last_line for kw in PASSWORD_PROMPT_KEYWORDS):
                        pwd = getpass("\n[Agent] Serial device is asking for a password. Please enter it: ")
                        self.serial_client.write(f"{pwd}\r\n".encode('utf-8'))
                        buffer = ""
                        idle_time = 0.0
                    
                    time.sleep(0.1)
                    idle_time += 0.1
            
            print("\n--- Command Finished ---")
            return f"OUTPUT:\n{output}\n" if output.strip() else "Command executed successfully, but produced no output."
        else:
            return "Error: No active connection. Please ensure the agent is connected first."

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
