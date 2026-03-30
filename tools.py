import time
import paramiko
import serial
from typing import Optional
from langchain_core.tools import tool

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
            stdin, stdout, stderr = self.ssh_client.exec_command(command)
            out = stdout.read().decode('utf-8')
            err = stderr.read().decode('utf-8')
            
            result = ""
            if out:
                result += f"STDOUT:\\n{out}\\n"
            if err:
                result += f"STDERR:\\n{err}\\n"
            if not result.strip():
                result = "Command executed successfully, but produced no output."
            return result
            
        elif self.conn_type == "serial" and self.serial_client:
            # Clear buffer before sending
            self.serial_client.reset_input_buffer()
            # Send command
            cmd_bytes = f"{command}\r\n".encode('utf-8')
            self.serial_client.write(cmd_bytes)
            
            # Read response
            time.sleep(0.5) # Wait for device to respond
            response = []
            while self.serial_client.in_waiting > 0:
                try:
                    line = self.serial_client.readline().decode('utf-8', errors='replace')
                    response.append(line.rstrip('\\r\\n'))
                except Exception as e:
                    response.append(f"[Error reading partial output: {e}]")
            
            return "\\n".join(response) if response else "Command executed, but no output was returned."
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
