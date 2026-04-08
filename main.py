import sys
from getpass import getpass
from langchain_core.messages import HumanMessage
from agent import build_hardware_agent
from tools import DEVICE_MANAGER

def main():
    print("=========================================")
    print("   LangGraph Hardware Debugging Agent    ")
    print("=========================================\\n")
    
    print("Please specify how to connect to the hardware device.")
    print("Format examples:")
    print(" - SSH:    ssh username@192.168.1.100")
    print("           (It will prompt for password if needed, or use key auth)")
    print(" - Serial: serial COM3 115200")
    print(" - local:  (Bypass connection, run on current host - not supported currently)\\n")
    
    conn_str = input("Connection string: ").strip()
    
    if not conn_str:
        print("No connection string provided. Exiting.")
        sys.exit(1)
        
    parts = conn_str.split()
    cmd_type = parts[0].lower()
    
    if cmd_type == "ssh":
        # Parse user@host or just host
        if len(parts) < 2:
            print("Invalid SSH format. Expected: ssh user@ip [port]")
            sys.exit(1)
            
        target = parts[1]
        port = int(parts[2]) if len(parts) > 2 else 22
        
        if "@" in target:
            username, host = target.split("@", 1)
        else:
            host = target
            username = input("Username: ")
            
        password = getpass(f"Password for {username}@{host} (leave blank for key auth): ")
        if not password:
            password = None
            
        try:
            print(f"Connecting to {host}:{port} via SSH...")
            msg = DEVICE_MANAGER.connect_ssh(host=host, username=username, password=password, port=port)
            print(msg)
        except Exception as e:
            print(f"Failed to connect via SSH: {e}")
            sys.exit(1)
            
    elif cmd_type == "serial":
        if len(parts) < 2:
            print("Invalid Serial format. Expected: serial [COM_PORT] [BAUDRATE]")
            sys.exit(1)
            
        port = parts[1]
        baudrate = int(parts[2]) if len(parts) > 2 else 115200
        
        try:
            print(f"Connecting to Serial {port} at {baudrate} baud...")
            msg = DEVICE_MANAGER.connect_serial(port=port, baudrate=baudrate)
            print(msg)
        except Exception as e:
            print(f"Failed to connect via Serial: {e}")
            sys.exit(1)
    else:
        print("Unknown connection type. Use 'ssh' or 'serial'.")
        sys.exit(1)
    
    # Initialize the LangGraph agent
    try:
        app = build_hardware_agent()
    except Exception as e:
        print(f"Failed to initialize Agent: {e}")
        print("Please check your .env file or environment variables for DEEPSEEK_API_KEY.")
        sys.exit(1)
        
    print("\\n[Agent is ready. Describe your hardware issue, or type 'exit' to quit]")
    
    # Initialize loop state
    messages = []
    
    while True:
        try:
            user_input = input("\\nUser: ")
            if user_input.lower() in ["exit", "quit", "q"]:
                break
            if not user_input.strip():
                continue
                
            messages.append(HumanMessage(content=user_input))
            
            # Print streaming output
            print("\\nAgent Thinking...")
            for event in app.stream({"messages": messages}):
                for node_name, node_state in event.items():
                    if node_name == "agent":
                        msg = node_state["messages"][-1]
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                print(f"-> Agent executing tool: {tc['name']} with args: {tc['args']}")
                        else:
                            print(f"Agent: {msg.content}")
                    elif node_name in ["tools", "invalid_tools"]:
                        print("-> Tool execution finished. Agent processing results...")
                        
                # Update our final state from the event stream to preserve context
                # The node_state will contain the most recent state
                if node_name == "agent" or node_name == "tools":
                    # We just need to make sure our next iteration incorporates these messages.
                    # LangGraph naturally appends messages if the schema has operator.add
                    # We will update our 'messages' list with the newly appended messages
                    pass
            
            # We must pull the latest messages from the graph state so the next loop continues
            # Because we aren't using a persistent checkpointer, we can just grab the final node's output
            # Actually, the stream yields dictionaries containing {"node_name": {"messages": [...]}}
            # We should just keep the new messages generated during the stream
            new_msgs = node_state["messages"]
            # But the node_state["messages"] only returns the diff or the full state depending on how it's handled.
            # In LangGraph TypedDict with Annotated operator.add, it only returns the diff. 
            # So we extend our local messages array.
            if isinstance(new_msgs, list):
                messages.extend(new_msgs)
            else:
                messages.append(new_msgs)
            
        except KeyboardInterrupt:
            print("\\nExiting...")
            break
        except Exception as e:
            print(f"An error occurred: {e}")
            
    DEVICE_MANAGER.close()

if __name__ == "__main__":
    main()
