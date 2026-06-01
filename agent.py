import operator
from typing import Annotated, TypedDict, Sequence
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode

from llm import get_llm
from tools import execute_device_command


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]


# Define the system prompt
SYSTEM_PROMPT = """You are an expert hardware debugging assistant.
Your goal is to help the user diagnose and fix problems on their development board or hardware device.
You have access to a tool called 'execute_device_command' that allows you to run shell commands on the given device.
The connection is established by the user locally. All you need to do is send standard Linux or board-specific commands and analyze the output to solve the issue.

Steps to debug:
1. When asked a question, determine if you need more information from the device (e.g., running `dmesg`, `ps`, `lsusb`, `cat /var/log/syslog`).
2. Use the 'execute_device_command' tool to run the command. Wait for the result.
3. Analyze the result. If more diagnostic commands are needed, run them.
4. Once you have isolated the root cause, explain the problem to the user in clear, concise language.
5. Provide the exact commands or manual steps the user needs to apply to fix the problem (or apply them yourself utilizing the tool if instructed to make changes).
6. MANDATORY VERIFICATION: Whenever you execute a command to install software, modify configuration, or change system state, you MUST run a follow-up verification command (e.g., `redis-server --version`, `java -version`, `ls -l /path`, or `systemctl status`) to confirm the action was successful. Do not assume success. If the verification fails or indicates the action didn't take effect, you must rethink your approach and apply a fix.
7. AVOID INTERACTIVE PAGERS: Terminal outputs using `less`, `more`, or commands like `systemctl status`, `git log`, `journalctl` will open interactive pagers waiting for user keyboard input (like pressing 'q'), which WILL CAUSE THE AGENT TO HANG FOREVER since it cannot press 'q'. You MUST append ` --no-pager`, or pipe the output to `cat` (e.g., `systemctl status X --no-pager` or `dmesg | cat`) for any command that might paginate. Do not use `less` or `vim`, use `cat` or `sed` instead.
8. AVOID INTERACTIVE PROMPTS: Any command that stops and waits for a "YES/NO" or "Hit Enter" user prompt (like `apt-get install`, `fdisk`, `sensors-detect`) will hang the agent forever. You MUST use non-interactive flags provided by the tools themselves (e.g., `apt-get install -y`, `sensors-detect --auto`, `echo -e "\n" | command`). For commands that might unexpectedly ask for credentials (like `git clone`), use non-interactive flags or prefix with `env GIT_TERMINAL_PROMPT=0 ` to fail fast instead of hanging.
9. AVOID INTERACTIVE FULL-SCREEN PROGRAMS: Never execute interactive full-screen system monitors, text editors, or terminal multiplexers (such as `top`, `htop`, `vi`, `vim`, `nano`, `tmux`, `screen`, `ncdu`, `minicom`, etc.), even if you pipe them to `head` (e.g., `./htop | head -30`) or redirect their output. When executed with a pseudo-terminal (PTY), these interactive tools will intercept signals, block on keyboard inputs, or run in infinite redraw loops, causing the agent to hang indefinitely. You MUST use non-interactive alternatives (such as `top -b -n 1`, `ps aux`, `cat`, or `sed -i` to view or edit files).

Remember to think step-by-step. Don't run risky commands (like rm -rf) without user consent just to test something, prioritize read-only diagnostic commands first.
"""


def build_hardware_agent():
    llm = get_llm()
    # Bind the execute function to the LLM
    tools = [execute_device_command]
    llm_with_tools = llm.bind_tools(tools)

    # We define the node function for the agent
    def agent_node(state: AgentState):
        messages = state["messages"]
        # Make sure system prompt is at the beginning if not already there,
        # but the standard way is to insert it or rely on the user to append it to state.
        # We will prepend it temporarily for this call if it's not the first msg or we'll assume it's there.
        # Actually, let's keep it simple: we just pass state to the LLM.
        # But we want the system prompt.
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)

        response = llm_with_tools.invoke(messages)
        return {"messages": [response]}

    def invalid_tools_node(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        new_messages = []
        if hasattr(last_message, "invalid_tool_calls"):
            for tc in last_message.invalid_tool_calls:
                new_messages.append(ToolMessage(
                    content=f"Error: Invalid tool call. Details: {tc.get('error', 'Malformed arguments')}",
                    name=tc.get("name", "unknown_tool"),
                    tool_call_id=tc.get("id", "unknown_id")
                ))
        return {"messages": new_messages}

    def custom_tools_condition(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        if hasattr(last_message, "tool_calls") and len(last_message.tool_calls) > 0:
            return "tools"
        if hasattr(last_message, "invalid_tool_calls") and len(last_message.invalid_tool_calls) > 0:
            return "invalid_tools"
        return "__end__"

    # Build Graph
    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.add_node("invalid_tools", invalid_tools_node)

    # Add Edges
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        custom_tools_condition,
    )
    workflow.add_edge("tools", "agent")
    workflow.add_edge("invalid_tools", "agent")

    return workflow.compile()
