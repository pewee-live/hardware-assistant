# Hardware Debugging Assistant (硬件调试 Agent)

这是一个基于 [LangGraph](https://langchain-ai.github.io/langgraph/) 和 **DeepSeek** 大模型的智能硬件调试助手。
用户可以通过自然语言描述他们遇到的硬件问题，Agent 将会自动连接至设备（支持 SSH 或串口），并在目标设备上执行命令、分析输出结果，从而帮助用户定位并解决问题。

## 功能特性

1. **自然语言交互**：只需告诉 Agent “我的网卡不见了”或“检查一下系统负载”，Agent 就会自动想办法帮你查找原因。
2. **多协议连接支持**：
   - **SSH**：支持用户名/密码，或基于 Key 的无密码登录。
   - **串口 (Serial)**：支持直接连接诸如 CH340, CP210x 等芯片的普通 COM 口。
3. **自主决策能力**：借助于 LangGraph 的带有工具调用的状态图 (StateGraph)，Agent 会执行指令，获取输出；如果信息不足，会继续执行更多诊断命令（类似 ReAct 模式）。
4. **LLM 模型管理**：默认采用兼容 OpenAI 接口标准的 DeepSeek (`deepseek-chat`)。

---

## 技术架构说明

本项目主要利用如下技术栈：
- **LangChain/LangGraph**：用于定义包含状态、条件路由的工作流图，实现循环调用的 Agent。
- **paramiko**：用于通过 SSH 连接设备。
- **pyserial**：用于通过串口 (Serial) 读写设备。
- **langchain-openai**：由于 DeepSeek 官方兼容 OpenAI API 标准，因此可以直接利用该模块调用。

### 目录结构

```
ssh-helper/
├── agent.py           # 定义 LangGraph 状态图与 Agent 节点、工具节点的路由逻辑
├── llm.py             # 配置与初始化 DeepSeek 大模型
├── tools.py           # 定义统一的硬件命令执行工具 (execute_device_command) 与连接管理器
├── main.py            # CLI 入口，处理连接初始化及聊天交互循环
├── requirements.txt   # Python 依赖清单
├── .env.example       # 环境变量配置模板
└── README.md          # 帮助文档
```

---

## 快速运行

### 1. 安装依赖

确保你的 Python 环境是 `3.8+`，然后在项目根目录下运行：

```bash
pip install -r requirements.txt
```

### 2. 配置大模型 API

复制环境变量模板并填入你的 DeepSeek API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件，修改如下字段：
```env
DEEPSEEK_API_KEY=your_actual_api_key_here
```

### 3. 开始使用

运行主程序：

```bash
python main.py
```

按照提示，你可以选择如何连接到你的硬件设备：
- 连接到 SSH 设备：
  ```
  Connection string: ssh root@192.168.1.50 22
  ```
- 连接到 串口 (Windows 为 COM口，Linux 为 /dev/ttyUSB0)：
  ```
  Connection string: serial COM3 115200
  ```

连接成功后，输入你想排查的问题即可。例如：“我的设备好像不能连网了，你帮我看一下怎么回事”。

---

## 扩展与自定义

- **增加工具**：如果你想赋予它更多的能力（如上传文件、特定脚本执行），只需在 `tools.py` 添加使用 `@tool` 装饰器的函数，并更新 `agent.py` 中的 `tools` 列表配置。
- **修改 Agent 行为**：修改 `agent.py` 中的 `SYSTEM_PROMPT`，你可以针对特定的开发板告诉它预先需要知道的特定指令。
