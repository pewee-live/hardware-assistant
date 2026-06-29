# Hardware Debugging Assistant (硬件调试 Agent)

这是一个基于 [LangGraph](https://langchain-ai.github.io/langgraph/) 和 **DeepSeek** 大模型的智能硬件调试助手。
用户可以通过自然语言描述他们遇到的硬件问题，Agent 将会自动连接至设备（支持 SSH 或串口），并在目标设备上执行命令、分析输出结果，从而帮助用户定位并解决问题。

![1](https://raw.githubusercontent.com/pewee-live/sshhelper/refs/heads/master/pics/1.png)

## 功能特性

1. **自然语言交互**：只需告诉 Agent “我的网卡不见了”或“检查一下系统负载”，Agent 就会自动想办法帮你查找原因。
2. **多协议连接支持**：
   - **SSH**：支持用户名/密码，或基于 Key 的无密码登录。
   - **串口 (Serial)**：支持直接连接诸如 CH340, CP210x 等芯片的普通 COM 口。
3. **多会话真并发**：内置连接管理器，支持将 SSH/串口连接状态与 Web 会话 (Session) 绑定。Agent 任务是会话级后台任务，切走查看别的会话**不会中断**正在跑的任务——你可以同时让多个设备并行排错，随时切回去查看进度。侧边栏会显示哪些会话正在后台工作，切回来时自动回放你错过的命令输出与思考过程，连接互不串扰。
4. **自主决策能力**：借助于 LangGraph 的带有工具调用的状态图 (StateGraph)，Agent 会执行指令，获取输出；如果信息不足，会继续执行更多诊断命令（类似 ReAct 模式）。
5. **LLM 模型管理**：采用兼容 OpenAI 接口标准的大语言模型（如 DeepSeek、OpenAI 原生模型、Ollama 等）。默认行为为 `deepseek-chat`。
6. **智能密码交互逻辑**：当远端设备（SSH/串口）提示输入密码（如执行 `sudo` 等特权指令）时，Agent 的执行通道能够自动挂起并在本地控制台或网页端安全地请求用户输入密码，随后静默回传给设备。
7. **人工干预 (Human-in-the-loop)**：当命令卡在敏感词防火墙之外的交互提示（如 `conda install` 的 `[y]/n`、`apt` 的 `Y/n`、`fdisk` 的菜单选择，或任何未预料的卡死）时，Agent 会自动挂起并弹出干预窗口，把最近的终端输出交给你判断，由你决定发送什么输入、中止命令，还是继续等待。CLI 模式同样支持在本地终端交互。
8. **Token 预算驱动的上下文管理**：历史压缩不再按消息条数粗暴触发，而是依据大模型实际报告的 prompt token 用量——只有接近上下文窗口上限（默认 80%）时才压缩。压缩时优先保留原始目标与最近窗口、对冗长的命令输出做无损截断，仅在必要时对增量内容做摘要，杜绝「摘要套摘要」的信息衰减，大幅提升长会话质量。窗口大小可通过 `MODEL_CONTEXT_WINDOW` 环境变量配置。
9. **闭环指令验证**：约束模型在对系统进行状态更改（如安装软件、修改系统配置）后，强制去执行相关的二次验证操作（如检查进程状态或获取版本号），自动防范执行失败导致的伪成功反馈。
10. **高级 Web UI**：内置基于 FastAPI 和 WebSockets 的图形化页面，提供暗黑主题玻璃拟态界面、终端打字机效果输出以及直观的思考过程展示。
11. **交互死锁防火墙**：在底层流处理与 Agent 认知级别双重设防，自动拦截或处理诸如 `htop`、`vim`、`less` 等会导致 PTY 终端永久挂起的命令。
12. **非法调用自我修正 (Self-Correction)**：新增对模型输出错误 JSON 或格式破坏的识别隔离节点 `invalid_tools`，原生捕获非法请求并流转回主代理，强制模型重新反思修正，彻底避免因上下文状态缺失导致的 API Error 400 中断异常。
13. **连接断线自动重连**：网络抖动、休眠或 WiFi 切换导致 WebSocket 断开时，前端会以指数退避自动重连并回放错过的输出。由于 Agent 任务运行在与连接解耦的后台，断线不会中断任何正在进行的诊断。
14. **会话导出**：任意会话可一键导出为 Markdown 调试报告（含命令、输出、结论与 token/成本统计），也可导出 JSON，便于团队分享与归档「上次是怎么修好的」。
15. **设备记忆 / 画像**：对同一台设备的身份信息（OS、内核、架构、CPU、内存、存储、网络）做持久化记忆。Agent 首次探明后会主动保存，后续会话直接复用，省去重复跑 uname/lscpu/free 的开销与 token。
16. **会话管理**：支持会话重命名与删除（悬停会话条目出现操作按钮），侧边栏不再随时间堆积成难以翻找的列表。
17. **成本可见**：每个会话实时统计累计 token 用量（输入/输出）与估算费用，按可配置的模型定价计算，在界面顶部以徽章展示，长会话的花费一目了然。

---

## 技术架构说明

本项目的核心工作流通过 LangGraph 进行状态流转，结构大纲如下：

```mermaid
stateDiagram-v2
    [*] --> agent
    agent --> tools: LLM判断需要执行工具时
    agent --> invalid_tools: LLM输出格式错误的工具调用时 
    tools --> agent: 将终端输出信息返回大模型
    invalid_tools --> agent: 将格式报错返回大模型要求修正
    agent --> [*]: 对话完成或给出最终排查结果 (END)
```

本系统主要利用如下技术栈：
- **LangChain/LangGraph**：用于定义包含状态、条件路由的工作流图，实现循环调用的 Agent。
- **paramiko**：用于通过 SSH 连接设备，包含通过 PTY 环境获取和发送控制台数据的轮询功能。
- **pyserial**：用于通过串口 (Serial) 读写设备。
- **langchain-openai**：由于 DeepSeek 官方兼容 OpenAI API 标准，因此可以直接利用该模块调用。

### 目录结构

```
ssh-helper/
├── agent.py           # 定义 LangGraph 状态图与 Agent 节点、工具节点的路由逻辑
├── llm.py             # 配置与初始化 DeepSeek 大模型
├── tools.py           # 定义硬件命令执行框架与 PTY 死锁拦截防火墙
├── web_server.py      # 【推荐】Web 服务端入口，提供网页端全双工实时流式交互
├── static/            # 前端 Web UI 资源 (index.html, style.css, app.js)
├── main.py            # 【旧版】CLI 纯命令行终端交互入口
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

复制环境变量模板并填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件，修改如下字段：
```env
OPENAI_API_KEY=your_actual_api_key_here
# 如果使用的是特定平台（如DeepSeek、Ollama），你可以取消注释并修改 BASE_URL 和 MODEL：
# OPENAI_BASE_URL=https://api.deepseek.com/v1
# OPENAI_MODEL=deepseek-chat

# 上下文窗口（按所用模型设置，影响历史压缩时机）：
# MODEL_CONTEXT_WINDOW=64000   # deepseek-chat
# MODEL_CONTEXT_WINDOW=128000  # gpt-4o / gpt-4.1
# MODEL_CONTEXT_WINDOW=200000  # claude / gemini
# MODEL_CONTEXT_BUDGET=0.8     # 达到窗口的多少比例时开始压缩（默认 0.8）

# 成本统计（token 始终统计，价格仅用于估算费用）：
# PRICE_INPUT_PER_1M=0.27       # 默认按 DeepSeek-chat cache-miss 计价
# PRICE_OUTPUT_PER_1M=1.10
# COST_CURRENCY=USD
```

### 3. 开始使用

你可以选择通过 **Web 可视化界面** 或者 **传统终端命令** 的方式启动助手。

#### 方案 A：Web 界面可视化调试（推荐 ✨）

运行 Web 服务：
```bash
python web_server.py
```
终端提示启动成功后，打开浏览器访问 👉 `http://localhost:8000/`

在页面左侧的侧边栏输入设备的 SSH 或 Serial 连接信息点击连接，然后在右侧输入你的硬件排错问题，例如：“网卡不见了，帮我查一下硬件层和驱动层的原因”。当碰到特权命令，页面中央会弹出输入密码的浮窗，输入即可放行指令。当命令卡在需要人工确认的交互提示（如 `conda`、`apt` 的 yes/no、`fdisk` 菜单等），会弹出干预窗口让你决定发送什么、中止还是继续等待。系统支持多会话真并发——每个会话的 Agent 任务都在后台独立运行，侧边栏会显示哪些会话正在工作，你可以放心切走去别的设备排错，随时切回来查看进度，错过的输出会自动回放。

#### 方案 B：传统 CLI 命令行模式

如果你偏好纯无头终端，可以直接运行：
```bash
python main.py
```
按照终端提示输入 `ssh root@192.168.1.50 22` 或 `serial COM3 115200` 即可连接并开始问答。

#### 方案 C：使用 Docker 部署运行（全平台支持）

本项目现已完美接入 Docker，支持通过 Github Actions 打包并推送到 DockerHub（同时兼容 `amd64` / `arm64` 架构）。这使得你在软路由、NAS、树莓派等设备上可以一键无缝部署。

1. **直接拉取并运行已有镜像**（请将 `<your_dockerhub_username>` 替换为实际拉取的用户名）：
```bash
docker run -d --name ssh-helper \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_super_secret_api_key_here \
  peweelive/sshhelper:latest
```
运行后访问：`http://localhost:8000/`

2. **如果需要使用本地串口 (Serial) 功能**：在启动时需要增加设备映射隧道 (`--device`)，以便让容器内部可以接触到底层宿主机的 USB 串口！举个例子（Linux宿主机下）：
```bash
docker run -d --name ssh-helper \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_key \
  --device=/dev/ttyUSB0 \
  peweelive/sshhelper:latest
```
*(注：由于 Docker 引擎的隔离限制机制，Windows系统运行的 Docker Desktop 不支持原生的串口/USB 透传。需要串口功能的 Windows 用户请参考 方案A 原生运行。)*

---

## 扩展与自定义

- **增加工具**：如果你想赋予它更多的能力（如上传文件、特定脚本执行），只需在 `tools.py` 添加使用 `@tool` 装饰器的函数，并更新 `agent.py` 中的 `tools` 列表配置。
- **修改 Agent 行为**：修改 `agent.py` 中的 `SYSTEM_PROMPT`，你可以针对特定的开发板告诉它预先需要知道的特定指令。
