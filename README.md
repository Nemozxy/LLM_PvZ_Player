# LLM PvZ Player

一个专门针对 **《植物大战僵尸》(Plants vs. Zombies)** 的 AI 自动化项目，可接入大语言模型（LLM）或视觉语言模型（VLM）让 AI 自主游玩游戏。

项目通过内存读取获取精确游戏状态（阳光、卡片冷却、植物阵型、僵尸位置等），通过代码注入执行高层语义动作（种植/铲除/玉米炮/选卡/通关），把 PvZ 常规关卡变成纯文本游戏，让模型专注于策略决策而非像素级坐标计算。既可使用纯文本 LLM（仅依赖内存状态），也可使用 VLM（结合截图视觉理解）。

PvZ 操控部分的实现极大受益于以下两个开源项目，它们几乎把整个 PvZ 常规关卡变成了纯文本游戏：
- [lmintlcx/pvztoolkit](https://github.com/lmintlcx/pvztoolkit) — PvZ 内存数据结构、偏移量表与游戏函数地址
- [vector-wlc/AsmVsZombies](https://github.com/vector-wlc/AsmVsZombies) — 内联汇编脚本注入与鼠标点击调用

---

## 核心能力

- **窗口捕获**：持续截取指定窗口客户区画面，作为 VLM 的实时视觉输入。支持屏幕截图（mss）与后台截图（PrintWindow）两种模式。
- **GUI 操作 Agent**：将 VLM 输出解析为标准化的鼠标/键盘动作（点击、拖拽、按键、输入、等待等），通过 `pyautogui` / `pydirectinput` 精确执行。
- **目标驱动**：用户设定一个高层目标，Agent 自主规划、执行、观察反馈，直到目标达成。
- **人机协作**：执行过程中用户可随时通过 WebUI 下发即时指令，Agent 会将人工输入纳入后续推理。
- **思考-执行同步（Pause & Resume）**：在 VLM 推理前暂停游戏画面，待 AI 输出操作后再恢复游戏并执行，避免实时游戏中因推理延迟导致决策落后。
- **PvZ 内存读取**：从 PvZ 进程内存直接读取阳光、卡片冷却、植物阵型、僵尸位置血量等精确数据，注入到 VLM Prompt 中。
- **PvZ 代码注入**：通过注入 x86 机器码直接调用游戏内部函数，实现种植、铲除、玉米炮、选卡、直接通关等高层动作，100% 可靠。
- **上下文压缩**：当 token 接近阈值时自动摘要旧历史；模型也可主动输出 `<compact>` 标签整理记忆。
- **操作日志**：每轮截图、思维链、动作、执行结果自动保存到本地，便于复盘分析。

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        Agent 主循环                          │
│                                                              │
│  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌────────────┐  │
│  │ 窗口捕获 │──▶│ 时停控制 │──▶│ VLM 推理 │──▶│ 动作执行   │  │
│  └─────────┘   └──────────┘   └─────────┘   └────────────┘  │
│       │              │              │              │         │
│       │              │              │              │         │
│       ▼              ▼              ▼              ▼         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              PvZ 内存读取 + 代码注入                  │   │
│  │  (阳光/卡片/植物/僵尸 → 结构化文本 → VLM Prompt)      │   │
│  │  (种植/铲除/玉米炮/选卡/通关 → 注入函数调用)          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    WebUI (FastAPI + Vue 3)            │   │
│  │  实时画面 │ 思考流 │ 动作流水(含成败) │ Prompt 折叠    │   │
│  │  用户指令下发                                          │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 1. 视觉输入层（Vision Input）
- [vision/capture.py](src/vlm_game_agent/vision/capture.py)：基于 `mss` 的窗口截图器，支持客户区/完整窗口两种范围、缩放、裁剪、格式转换。
- 截图前可选自动将窗口切到前台（`AttachThreadInput + SetForegroundWindow`），绕过 Windows 前台限制。
- 后台截图模式使用 `PrintWindow` API，窗口被遮挡也能截到内容（部分硬件加速游戏可能黑屏）。

### 2. Agent 决策层（Agent Core）
- [agent/core.py](src/vlm_game_agent/agent/core.py)：Agent 主循环，协调截图→暂停→VLM→执行→恢复的闭环。
- [agent/prompt.py](src/vlm_game_agent/agent/prompt.py)：系统提示词模板与工具 schema 定义。PvZ 模式下注入 `<game_state>` 结构化状态与植物类型表。
- [agent/parser.py](src/vlm_game_agent/agent/parser.py)：从 VLM 输出中解析 `<tool_call>` XML 块为结构化动作，支持单轮多动作。同时检测 `<compact>` 主动压缩标记。
- [agent/llm.py](src/vlm_game_agent/agent/llm.py)：VLM 客户端封装，使用 curl subprocess 调用 llama.cpp 兼容 API（兼容性最佳），内置重试与思维链提取。

#### 记忆系统（Memory）
- [agent/memory.py](src/vlm_game_agent/agent/memory.py)：加载 `memories/` 文件夹下所有 `.md` 文件，合并后注入系统提示。用户可新增任意 `.md` 文件扩展 Agent 知识。

#### 上下文压缩（Context Compression）
- [agent/compressor.py](src/vlm_game_agent/agent/compressor.py)：当 VLM 返回的 `prompt_tokens` 达到阈值（默认 70%）时触发压缩，将旧消息摘要为 `[历史摘要]`，保留 system 与最近若干条消息。
- 模型也可主动输出 `<compact>` 标签整理记忆，Agent 检测后清空历史只保留摘要。

### 3. 时停控制层（Pause Controller）
- [pause/](src/vlm_game_agent/pause/)：统一封装"冻结 → 思考 → 执行 → 恢复"的原子性时停流程，支持三种策略热切换：
  - **软暂停（soft）**：发送游戏内置暂停快捷键（如 Esc）。适合支持快捷键暂停的游戏。
  - **失焦暂停（focus）**：将焦点切出游戏窗口，利用游戏失焦自动暂停机制。
  - **硬暂停（hard）**：通过 `NtSuspendProcess` 挂起游戏进程，强制冻结状态。
- **PvZ 注入式冻结**：PvZ 模式下禁用 Esc 软暂停（避免暂停菜单污染截图），改用代码注入冻结游戏主循环，截图保持纯净的运行画面。

### 4. 动作执行层（Action Execution）
- [agent/executor.py](src/vlm_game_agent/agent/executor.py)：通用 GUI 动作执行器，将 `computer_use` 动作映射为 `pyautogui` 操作。坐标从相对 `[0, 1000]` 映射到窗口客户区绝对像素。
- [pvz/executor.py](src/vlm_game_agent/pvz/executor.py)：PvZ 专属动作执行器，优先使用代码注入（`MouseClick` 调用游戏内部函数），鼠标操作仅作为不可靠后备。
  - `place_plant`：种植植物（点卡片 + 点格子，走 MouseClick 让游戏处理全部 UI 逻辑）
  - `shovel`：铲除植物（点铲子 + 点格子）
  - `use_cob_cannon`：使用玉米加农炮（点炮台 + 点落点）
  - `click_card`：选中卡片（暂不放置）
  - `select_seeds`：选卡界面选卡并开始游戏（逐张选卡 + 随机填满 + 开始）
  - `win_level`：直接通关当前关卡（跳过实时小游戏）
- **动作验证**：种植后短暂等待并重新读取内存，验证目标位置是否出现预期植物，避免"假成功"。
- **失败中断**：PvZ 动作执行失败时中断本轮后续动作，避免错误连锁。

### 5. PvZ 内存读取与注入（PvZ Integration）
- [pvz/memory.py](src/vlm_game_agent/pvz/memory.py)：底层内存读取，基于 `ctypes ReadProcessMemory`，自动检测游戏版本（支持 1.0.0.1051 EN）。
- [pvz/offsets.py](src/vlm_game_agent/pvz/offsets.py)：版本偏移量表与名称映射（植物/僵尸/物品/场景/UI 状态）。
- [pvz/reader.py](src/vlm_game_agent/pvz/reader.py)：高层游戏状态读取，格式化为带 emoji 的结构化文本注入 VLM Prompt：
  - ☀ 阳光、🌊 波次、📋 卡片冷却、🌱 植物阵型、🧟 僵尸情报、📦 场地物品、🚜 割草机
- [pvz/injector.py](src/vlm_game_agent/pvz/injector.py)：代码注入器，通过 `VirtualAllocEx + WriteProcessMemory + CreateRemoteThread` 注入 x86 机器码，直接调用游戏内部函数（PutPlant / ShovelPlant / MouseClick / FadeOutLevel / ChooseCard / Rock 等）。同时实现 hack 开关：自动收集阳光、冻结主循环、解锁阳光上限、任意位置种植。

### 6. 远程监看层（WebUI）
- [webui/server.py](src/vlm_game_agent/webui/server.py)：FastAPI 服务端，提供首页与 `/ws` WebSocket 端点。
- [webui/manager.py](src/vlm_game_agent/webui/manager.py)：WebSocket 连接管理器，广播画面、日志、动作流水、Prompt，接收用户指令。
- [webui/static/index.html](src/vlm_game_agent/webui/static/index.html)：Vue 3 (CDN) 单页前端，无需构建步骤。
  - 左侧实时画面，右侧日志面板
  - 系统提示词（🤖 橙色）与用户提示词（👤 蓝色）分离显示，默认折叠
  - 动作流水显示翻译后的中文 + 执行结果（✅ 成功 / ❌ 失败 + 原因）
  - 底部输入框下发即时指令

### 7. 操作日志（Action Logger）
- [agent/action_logger.py](src/vlm_game_agent/agent/action_logger.py)：每轮保存截图（PNG）、思维链、VLM 原始输出、解析动作、执行结果，汇总到 `log.md`，便于复盘分析。

---

## 工作流程

1. **初始化**：加载 `.env` 配置，定位目标窗口，初始化截图器、时停控制器、VLM 客户端、记忆系统、WebUI、PvZ 内存读取（可选）。
2. **目标设定**：用户输入高层目标（如"通过第一关"）。
3. **循环执行**：
   - 截图 → 读取 PvZ 游戏状态（与截图同一时刻）→ 冻结游戏（注入式主循环冻结）
   - 构建消息：系统提示 + 历史 + 最新截图 + `<game_state>` 状态文本
   - 上下文压缩检查（达到阈值则压缩）
   - 推送本轮 Prompt 到 WebUI（系统提示词 + 用户提示词分离显示）
   - VLM 推理，输出 `<tool_call>` 动作块
   - 恢复游戏
   - 逐个解析并执行动作，推送动作 + 执行结果到 WebUI
   - PvZ 动作失败则中断本轮后续动作
   - 等待画面变化（动作感知延迟 + 基础延迟 + 模型 wait，三者取最大值）
   - 记录操作日志
4. **人工介入**：用户通过 WebUI 输入框下发指令，Agent 注入到历史并据此调整行为。
5. **任务结束**：目标达成或按 F12 停止，Agent 恢复所有 hack、关闭注入器、保存日志。

---

## 快速开始

### 环境要求

- Windows 10/11（PvZ 内存读取与注入依赖 Windows API）
- Python ≥ 3.10
- PvZ 游戏版本：1.0.0.1051 EN（其他版本需自行补充偏移量表）

### 安装

```bash
git clone <repo-url>
cd AIGamePlayer

python -m venv venv
venv\Scripts\activate

pip install -e .
```

### 配置

复制 `.env.example` 为 `.env`，按需修改：

```bash
# VLM API（默认指向本地 LM Studio）
VLM_BASE_URL=http://127.0.0.1:1234/v1
VLM_MODEL=qwen3.6-27b
VLM_API_KEY=sk-no-key-required

# PvZ 内存读取（需管理员权限运行 + PvZ 已启动）
PVZ_MEMORY_ENABLED=true

# 快捷启动（跳过交互输入）
WINDOW_TITLE=Plants vs. Zombies
TASK=通过第一关
```

### 启动

```bash
# 方式 1：批处理（Windows）
start_agent.bat

# 方式 2：直接运行
python examples\agent_demo.py
```

启动后：
- 终端会打印 WebUI 地址（默认 `http://localhost:8080`）
- 按 `F12` 全局热键随时停止 Agent（无需窗口焦点）
- 鼠标快速移到屏幕左上角可触发 pyautogui FAILSAFE 紧急停止

### 记忆文件

在 `memories/` 目录下新建 `.md` 文件编写游戏知识，Agent 启动时自动加载并常驻上下文：

- `memories.md` —— 游戏机制、通用经验、失败教训
- `coordinates.md` —— 关键位置坐标
- 任意自定义 `.md` 文件

---

## 项目结构

```
AIGamePlayer/
├── src/vlm_game_agent/
│   ├── agent/                # Agent 决策层
│   │   ├── core.py           # 主循环：截图→暂停→VLM→执行→恢复
│   │   ├── prompt.py         # 系统提示词与工具 schema
│   │   ├── parser.py         # VLM 输出解析（<tool_call> / <compact>）
│   │   ├── llm.py            # VLM 客户端（curl subprocess）
│   │   ├── executor.py       # 通用 GUI 动作执行器
│   │   ├── memory.py         # 记忆文件加载
│   │   ├── compressor.py     # 上下文压缩
│   │   └── action_logger.py  # 操作日志记录
│   ├── pause/                # 时停控制层
│   │   ├── controller.py     # 策略调度
│   │   ├── soft_pause.py     # 软暂停（快捷键）
│   │   ├── focus_pause.py    # 失焦暂停
│   │   └── hard_pause.py     # 硬暂停（挂起进程）
│   ├── pvz/                  # PvZ 内存读取与注入
│   │   ├── memory.py         # 底层内存读取
│   │   ├── offsets.py        # 版本偏移量表
│   │   ├── reader.py         # 游戏状态读取与格式化
│   │   ├── executor.py       # PvZ 专属动作执行器
│   │   └── injector.py       # 代码注入器
│   ├── vision/capture.py     # 窗口截图
│   ├── webui/                # WebUI 服务端
│   │   ├── server.py         # FastAPI 应用
│   │   ├── manager.py        # WebSocket 连接管理
│   │   └── static/index.html # Vue 3 前端
│   └── config/settings.py    # pydantic-settings 配置
├── examples/                 # 示例与诊断脚本
│   ├── agent_demo.py         # 完整 Agent 集成示例（主入口）
│   ├── webui_demo.py         # WebUI 推流示例
│   ├── pause_demo.py         # 时停策略测试
│   ├── test_zombie_position.py  # 僵尸定位验证
│   └── verify_grid_formula.py  # 网格坐标公式验证
├── memories/                 # 记忆文件（用户扩展）
├── action_logs/              # 操作日志（自动生成）
├── .env.example              # 配置模板
├── pyproject.toml            # 项目依赖
├── start_agent.bat           # Windows 启动脚本
└── README.md
```

---

## 技术栈

| 模块 | 方案 | 理由 |
|------|------|------|
| 窗口捕获 | `mss` + `pygetwindow` + `pywin32` | `mss` 纯内存截图性能极高；`pywin32` 用于客户区坐标计算与前台激活。 |
| GUI 自动化 | `pyautogui` + `pydirectinput` + `pyperclip` | `pyautogui` 通用 GUI 操作；`pyperclip` 支持中文输入（剪贴板 + Ctrl+V）。 |
| VLM 调用 | `curl` subprocess | 与 llama.cpp 协议兼容性最佳，Python HTTP 库存在协议差异问题。 |
| 时停控制 | `pynput` + `pywin32` + `ctypes` | `pynput` 发送全局快捷键；`ctypes` 调用 `NtSuspendProcess`。 |
| PvZ 内存读取 | `ctypes` ReadProcessMemory | 直接读取游戏进程内存，获取精确状态。 |
| PvZ 代码注入 | `ctypes` VirtualAllocEx + CreateRemoteThread | 注入 x86 机器码调用游戏内部函数。 |
| 配置管理 | `pydantic-settings` | 类型安全、支持 `.env` 与环境变量覆盖。 |
| 日志 | `loguru` | 零配置、结构化日志与文件轮转。 |
| WebUI 后端 | `FastAPI` + `websockets` | 原生支持 WebSocket，实时推送画面与思考流。 |
| WebUI 前端 | `Vue 3` (CDN) | 无需构建步骤，HTML 直接引入。 |

---

## 关键设计决策

### 为什么用 curl 而非 Python HTTP 库调用 VLM？
Python HTTP 库（urllib/httpx/openai SDK）与部分 llama.cpp 版本存在协议兼容性问题（如流式响应解析、keep-alive 行为差异）。curl 是唯一稳定的通信方式，且通过 stdin 传入请求体可绕过 Windows 命令行长度限制（Base64 图片可能超过 8192 字符）。

### 为什么 PvZ 用注入式冻结而非 Esc 软暂停？
Esc 软暂停会弹出暂停菜单/退出确认框，这些 UI 元素会进入截图，干扰 VLM 判断（模型可能陷入"取消暂停"死循环）。注入式冻结直接阻塞游戏主循环，画面保持纯净的运行状态，截图与内存状态一致。

### 为什么 PvZ 动作走 MouseClick 而非直接调 PutPlant？
直接调用 `PutPlant` 绕过 UI 逻辑（不扣阳光、不重置冷却、不检测占用），会产生大量副作用需要手动修补。`MouseClick` 让游戏自己处理全部 UI 逻辑，零副作用，与真人操作效果一致。

### 为什么种植后要做内存验证？
`place_plant` 执行成功仅代表点击完成，不代表植物真的种下去了（可能阳光不够、卡片冷却、格子被占）。执行后短暂等待并重新读取内存，验证目标位置是否出现预期植物，避免"假成功"误导后续决策。

### 为什么 wait 动作不立即 sleep？
`wait` 动作原本在 `execute()` 内立即 `time.sleep(seconds)`，主循环末尾又 `_compute_wait_time()` 再睡一次，导致等待时间双倍叠加。修复后 `wait` 只参与最终等待计算，与动作感知延迟、基础延迟三者取最大值，只睡一次。

---

## 致谢

本项目 PvZ 操控部分（内存读取、数据结构、偏移量表、代码注入）的实现，几乎全部借鉴了以下两个开源项目的代码与知识：

- **[lmintlcx/pvztoolkit](https://github.com/lmintlcx/pvztoolkit)** — 提供了完整的 PvZ 内存数据结构定义、版本偏移量表与游戏内部函数地址。本项目 `pvz/memory.py`、`pvz/offsets.py`、`pvz/reader.py`、`pvz/injector.py` 中的地址、结构体与 hack 逻辑均来自 pvztoolkit 的 `data.cpp` 与 `code.cpp`。
- **[vector-wlc/AsmVsZombies](https://github.com/vector-wlc/AsmVsZombies)** — 提供了内联汇编脚本注入的实现范例，包括 `MouseDown`/`MouseUp` 调用、`ShovelPlant` 调用、`ReleaseMouse` 调用、选卡界面 `ChooseCard`/`Rock` 调用等。本项目 `pvz/injector.py` 中的 shellcode 注入与 `pvz/executor.py` 中的动作执行流程均参考了 AsmVsZombies 的 `avz_asm.cpp`。

这两个项目几乎做到了把整个 PvZ 常规关卡变成纯文本游戏，使得 VLM Agent 可以通过精确的内存数据做策略决策，而非依赖不可靠的视觉坐标估算。非常感谢这两个项目及其作者。

---

## 后续计划

- [x] 实现基础窗口捕获与截图模块
- [x] 搭建 VLM 调用与操作解析链路
- [x] 设计并落地提示词模板
- [x] 实现短期记忆与上下文压缩机制
- [x] 开发 WebUI 远程监看与指令下发功能
- [x] 实现记忆文件夹的自动加载与持久化
- [x] 实现时停控制层（软暂停 / 失焦暂停 / 硬暂停）
- [x] PvZ 内存读取：阳光/卡片/植物/僵尸等精确状态
- [x] PvZ 代码注入：种植/铲除/玉米炮/选卡/通关等高层动作
- [x] PvZ 注入式主循环冻结（替代 Esc 软暂停）
- [x] 动作执行结果验证（种植后内存校验）
- [x] WebUI 动作流水显示执行结果（成功/失败）
- [x] WebUI 系统提示词与用户提示词分离显示
- [ ] 开放 MCP 接口，支持外部 AI 应用接入 Agent
- [ ] 支持更多游戏版本（需补充偏移量表）
- [ ] ~~技能系统（Skills）~~（暂缓）
