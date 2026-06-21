# LLM PvZ Player

[中文文档](README_CN.md)

An AI automation project for **Plants vs. Zombies**, powered by Large Language Models (LLM) or Vision-Language Models (VLM).

By reading game memory for precise state (sun, card cooldowns, plant layout, zombie positions, etc.) and injecting code to execute high-level semantic actions (plant/shovel/cob cannon/seed selection/win level), this project turns PvZ into a text-based game — letting the model focus on strategy rather than pixel-level coordinate calculations. Works with both text-only LLMs (relying on memory state) and VLMs (combining screenshots with visual understanding).

The PvZ control implementation is heavily indebted to these two open-source projects, which essentially turned all standard PvZ levels into a text-based game:
- [lmintlcx/pvztoolkit](https://github.com/lmintlcx/pvztoolkit) — PvZ memory data structures, offset tables, and game function addresses
- [vector-wlc/AsmVsZombies](https://github.com/vector-wlc/AsmVsZombies) — Inline assembly script injection and mouse click invocation

---

## Core Capabilities

- **Window Capture**: Continuously captures the target window's client area as real-time visual input for VLM. Supports both screen capture (mss) and background capture (PrintWindow).
- **GUI Operation Agent**: Parses VLM output into standardized mouse/keyboard actions (click, drag, key press, type, wait, etc.) and executes them via `pyautogui` / `pydirectinput`.
- **Goal-Driven**: User sets a high-level goal; the Agent autonomously plans, executes, observes feedback, and iterates until the goal is achieved.
- **Human-in-the-Loop**: Users can issue instant commands via WebUI at any time; the Agent incorporates human input into subsequent reasoning.
- **Pause & Resume**: Freezes the game before VLM inference and resumes after AI outputs actions, preventing decision lag in real-time games.
- **PvZ Memory Reading**: Directly reads sun, card cooldowns, plant layout, zombie positions/HP, and other precise data from the PvZ process memory, injecting them into the VLM prompt.
- **PvZ Code Injection**: Injects x86 machine code to call game internal functions directly, implementing high-level actions (plant, shovel, cob cannon, seed selection, win level) with 100% reliability.
- **Guide System**: Models can query plant/zombie guides on-demand via `view_guide` action. Guide files are organized in `guide/` directory and auto-listed during seed selection.
- **Context Compression**: Automatically summarizes old history when tokens approach the threshold; models can also proactively output `<compact>` tags to organize memory.
- **Action Logging**: Each turn's screenshot, reasoning chain, actions, and execution results are automatically saved locally for review.

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Agent Main Loop                       │
│                                                              │
│  ┌─────────┐   ┌──────────┐   ┌─────────┐   ┌────────────┐  │
│  │  Window  │──▶│   Pause  │──▶│   VLM   │──▶│   Action   │  │
│  │ Capture  │   │ Control  │   │ Inference│   │ Execution  │  │
│  └─────────┘   └──────────┘   └─────────┘   └────────────┘  │
│       │              │              │              │         │
│       ▼              ▼              ▼              ▼         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │          PvZ Memory Reading + Code Injection         │   │
│  │  (Sun/Cards/Plants/Zombies → Structured Text → Prompt)│   │
│  │  (Plant/Shovel/Cob/Seeds/Win → Injected Func Calls)  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                WebUI (FastAPI + Vue 3)                │   │
│  │  Live View │ Reasoning │ Action Log │ Prompt Viewer  │   │
│  │  User Command Input                                    │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

### 1. Vision Input Layer
- [vision/capture.py](src/vlm_game_agent/vision/capture.py): Window capturer based on `mss`, supporting client area/full window, scaling, cropping, and format conversion.
- Optionally brings the window to the foreground before capture (via `AttachThreadInput + SetForegroundWindow`).
- Background capture mode uses `PrintWindow` API — works even when the window is occluded.

### 2. Agent Decision Layer
- [agent/core.py](src/vlm_game_agent/agent/core.py): Agent main loop — capture → pause → VLM → execute → resume.
- [agent/prompt.py](src/vlm_game_agent/agent/prompt.py): System prompt templates and tool schema definitions. Injects `<game_state>` structured state and plant type table in PvZ mode.
- [agent/parser.py](src/vlm_game_agent/agent/parser.py): Parses `<tool_call>` XML blocks from VLM output into structured actions. Supports multiple actions per turn. Detects `<compact>` proactive compression tags.
- [agent/llm.py](src/vlm_game_agent/agent/llm.py): VLM client wrapper using curl subprocess for llama.cpp-compatible API (best compatibility), with built-in retry and chain-of-thought extraction.

#### Memory System
- [agent/memory.py](src/vlm_game_agent/agent/memory.py): Loads all `.md` files from `memories/` folder, merges and injects into the system prompt. Users can add any `.md` file to extend Agent knowledge.

#### Context Compression
- [agent/compressor.py](src/vlm_game_agent/agent/compressor.py): Triggers compression when VLM-reported `prompt_tokens` reach the threshold (default 70%), summarizing old messages into `[历史摘要]` while keeping the system prompt and recent messages.
- Models can also proactively output `<compact>` tags to organize memory.

### 3. Pause Controller Layer
- [pause/](src/vlm_game_agent/pause/): Unified "freeze → think → execute → resume" atomic pause flow, supporting three strategies:
  - **Soft pause**: Sends game built-in pause hotkey (e.g., Esc).
  - **Focus pause**: Switches focus away from the game window, leveraging auto-pause on focus loss.
  - **Hard pause**: Suspends the game process via `NtSuspendProcess`.
- **PvZ Injection Freeze**: In PvZ mode, Esc soft pause is disabled (to avoid pause menu polluting screenshots), replaced by code injection that freezes the game main loop while keeping a clean running screenshot.

### 4. Action Execution Layer
- [agent/executor.py](src/vlm_game_agent/agent/executor.py): Generic GUI action executor, mapping `computer_use` actions to `pyautogui` operations. Coordinates mapped from relative `[0, 1000]` to absolute window client pixels.
- [pvz/executor.py](src/vlm_game_agent/pvz/executor.py): PvZ-specific action executor, prioritizing code injection (`MouseClick` calling game internal functions), with mouse operations as unreliable fallback.
  - `place_plant`: Plant (click card + click cell via MouseClick)
  - `shovel`: Shovel plant (click shovel + click cell)
  - `use_cob_cannon`: Use Cob Cannon (click cannon + click target)
  - `click_card`: Select card (without placing)
  - `select_seeds`: Select seeds and start game
  - `win_level`: Win current level directly (skip mini-games)
  - `view_guide`: Query plant/zombie guide files on-demand
- **Action Verification**: After planting, briefly waits and re-reads memory to verify the expected plant appeared at the target position.
- **Failure Interruption**: When a PvZ action fails, subsequent actions in the same turn are skipped.

### 5. PvZ Memory Reading & Injection
- [pvz/memory.py](src/vlm_game_agent/pvz/memory.py): Low-level memory reading via `ctypes ReadProcessMemory`, with automatic game version detection (supports 1.0.0.1051 EN).
- [pvz/offsets.py](src/vlm_game_agent/pvz/offsets.py): Version offset tables and name mappings (plants/zombies/items/scenes/UI states).
- [pvz/reader.py](src/vlm_game_agent/pvz/reader.py): High-level game state reader, formatting as emoji-annotated structured text for VLM prompt injection.
- [pvz/injector.py](src/vlm_game_agent/pvz/injector.py): Code injector using `VirtualAllocEx + WriteProcessMemory + CreateRemoteThread` to inject x86 machine code that calls game internal functions (PutPlant / ShovelPlant / MouseClick / FadeOutLevel / ChooseCard / Rock, etc.). Also implements hack toggles: auto-collect sun, freeze main loop, unlock sun cap, plant anywhere.

### 6. WebUI
- [webui/server.py](src/vlm_game_agent/webui/server.py): FastAPI server with `/ws` WebSocket endpoint.
- [webui/manager.py](src/vlm_game_agent/webui/manager.py): WebSocket connection manager for broadcasting frames, logs, action streams, and prompts; receiving user commands.
- [webui/static/index.html](src/vlm_game_agent/webui/static/index.html): Vue 3 (CDN) single-page frontend, no build step required.

### 7. Action Logger
- [agent/action_logger.py](src/vlm_game_agent/agent/action_logger.py): Saves per-turn screenshots (PNG), reasoning chains, raw VLM output, parsed actions, and execution results into `log.md`.

---

## Quick Start

### Requirements

- Windows 10/11 (PvZ memory reading and injection depend on Windows APIs)
- Python >= 3.10
- PvZ game version: 1.0.0.1051 EN (other versions require custom offset tables)

### Installation

```bash
git clone <repo-url>
cd AIGamePlayer

python -m venv venv
venv\Scripts\activate

pip install -e .
```

### Configuration

Copy `.env.example` to `.env` and modify as needed:

```bash
# VLM API (defaults to local LM Studio)
VLM_BASE_URL=http://127.0.0.1:1234/v1
VLM_MODEL=qwen3.6-27b
VLM_API_KEY=sk-no-key-required

# PvZ memory reading (requires admin privileges + PvZ running)
PVZ_MEMORY_ENABLED=true

# Quick start (skip interactive input)
WINDOW_TITLE=Plants vs. Zombies
TASK=Complete level 1
```

### Launch

```bash
# Option 1: Batch file (Windows)
start_agent.bat

# Option 2: Direct run
python examples\agent_demo.py
```

After launch:
- Terminal prints the WebUI address (default `http://localhost:8080`)
- Press `F12` global hotkey to stop the Agent at any time (no window focus needed)
- Move mouse to screen top-left corner quickly to trigger pyautogui FAILSAFE emergency stop

### Memory Files

Create `.md` files in `memories/` to write game knowledge — the Agent auto-loads them at startup and keeps them in context:

- `memories.md` — Game mechanics, general experience, lessons learned
- `coordinates.md` — Key position coordinates
- Any custom `.md` file

### Guide Files

Create `.md` files in `guide/` (supports subdirectories) to document plant/zombie attributes and strategies. During seed selection, the Agent auto-lists available guides. The model can query guides on-demand via `view_guide` action.

---

## Project Structure

```
AIGamePlayer/
├── src/vlm_game_agent/
│   ├── agent/                # Agent decision layer
│   │   ├── core.py           # Main loop: capture→pause→VLM→execute→resume
│   │   ├── prompt.py         # System prompts & tool schemas
│   │   ├── parser.py         # VLM output parsing (<tool_call> / <compact>)
│   │   ├── llm.py            # VLM client (curl subprocess)
│   │   ├── executor.py       # Generic GUI action executor
│   │   ├── memory.py         # Memory file loading
│   │   ├── compressor.py     # Context compression
│   │   └── action_logger.py  # Action logging
│   ├── pause/                # Pause control layer
│   │   ├── controller.py     # Strategy dispatch
│   │   ├── soft_pause.py     # Soft pause (hotkey)
│   │   ├── focus_pause.py    # Focus pause
│   │   └── hard_pause.py     # Hard pause (suspend process)
│   ├── pvz/                  # PvZ memory reading & injection
│   │   ├── memory.py         # Low-level memory reading
│   │   ├── offsets.py        # Version offset tables
│   │   ├── reader.py         # Game state reading & formatting
│   │   ├── executor.py       # PvZ-specific action executor
│   │   └── injector.py       # Code injector
│   ├── vision/capture.py     # Window capture
│   ├── webui/                # WebUI server
│   │   ├── server.py         # FastAPI app
│   │   ├── manager.py        # WebSocket connection management
│   │   └── static/index.html # Vue 3 frontend
│   └── config/settings.py    # pydantic-settings config
├── examples/                 # Examples & diagnostic scripts
│   ├── agent_demo.py         # Full Agent integration (main entry)
│   ├── webui_demo.py         # WebUI streaming demo
│   ├── pause_demo.py         # Pause strategy test
│   ├── test_zombie_position.py  # Zombie positioning verification
│   └── verify_grid_formula.py  # Grid coordinate formula verification
├── memories/                 # Memory files (user-extensible)
├── guide/                    # Guide files (plant/zombie docs)
├── action_logs/              # Action logs (auto-generated)
├── .env.example              # Config template
├── pyproject.toml            # Project dependencies
├── start_agent.bat           # Windows launch script
├── README.md                 # English README (this file)
└── README_CN.md              # Chinese README
```

---

## Tech Stack

| Module | Solution | Reason |
|--------|----------|--------|
| Window Capture | `mss` + `pygetwindow` + `pywin32` | `mss` pure-memory capture is extremely fast; `pywin32` for client area coordinates and foreground activation. |
| GUI Automation | `pyautogui` + `pydirectinput` + `pyperclip` | `pyautogui` for general GUI ops; `pyperclip` for CJK input (clipboard + Ctrl+V). |
| VLM Calls | `curl` subprocess | Best protocol compatibility with llama.cpp; Python HTTP libs have protocol differences. |
| Pause Control | `pynput` + `pywin32` + `ctypes` | `pynput` for global hotkeys; `ctypes` for `NtSuspendProcess`. |
| PvZ Memory Read | `ctypes` ReadProcessMemory | Direct game process memory reading for precise state. |
| PvZ Code Injection | `ctypes` VirtualAllocEx + CreateRemoteThread | Inject x86 machine code to call game internal functions. |
| Config | `pydantic-settings` | Type-safe, supports `.env` and environment variable overrides. |
| Logging | `loguru` | Zero-config structured logging with file rotation. |
| WebUI Backend | `FastAPI` + `websockets` | Native WebSocket support for real-time frame and reasoning streaming. |
| WebUI Frontend | `Vue 3` (CDN) | No build step, HTML direct import. |

---

## Key Design Decisions

### Why curl instead of Python HTTP libraries for VLM calls?
Python HTTP libraries (urllib/httpx/openai SDK) have protocol compatibility issues with some llama.cpp versions (streaming response parsing, keep-alive behavior differences). curl is the only stable communication method, and passing the request body via stdin bypasses Windows command-line length limits (Base64 images can exceed 8192 characters).

### Why injection freeze instead of Esc soft pause for PvZ?
Esc soft pause brings up the pause menu/exit confirmation dialog, which enters screenshots and confuses the VLM (the model may get stuck in a "cancel pause" loop). Injection freeze directly blocks the game main loop, keeping a clean running screenshot consistent with memory state.

### Why MouseClick instead of direct PutPlant for PvZ actions?
Directly calling `PutPlant` bypasses UI logic (no sun deduction, no cooldown reset, no occupancy check), causing many side effects that need manual patching. `MouseClick` lets the game handle all UI logic itself — zero side effects, identical to human operation.

### Why verify after planting?
`place_plant` success only means the clicks completed, not that the plant was actually placed (insufficient sun, card on cooldown, cell occupied). After execution, briefly waiting and re-reading memory verifies the expected plant appeared at the target position, avoiding "false success" misleading subsequent decisions.

### Why doesn't the wait action sleep immediately?
The `wait` action used to `time.sleep(seconds)` inside `execute()`, then the main loop's `_compute_wait_time()` slept again — doubling the wait time. After the fix, `wait` only participates in the final wait calculation, taking the maximum of action-aware delay, base delay, and model-requested wait — sleeping only once.

---

## Acknowledgments

The PvZ control implementation (memory reading, data structures, offset tables, code injection) in this project is almost entirely based on code and knowledge from these two open-source projects:

- **[lmintlcx/pvztoolkit](https://github.com/lmintlcx/pvztoolkit)** — Provided complete PvZ memory data structure definitions, version offset tables, and game internal function addresses. The addresses, structures, and hack logic in this project's `pvz/memory.py`, `pvz/offsets.py`, `pvz/reader.py`, and `pvz/injector.py` all come from pvztoolkit's `data.cpp` and `code.cpp`.
- **[vector-wlc/AsmVsZombies](https://github.com/vector-wlc/AsmVsZombies)** — Provided implementation examples for inline assembly script injection, including `MouseDown`/`MouseUp`, `ShovelPlant`, `ReleaseMouse`, `ChooseCard`/`Rock` calls. The shellcode injection in this project's `pvz/injector.py` and action execution flow in `pvz/executor.py` both reference AsmVsZombies' `avz_asm.cpp`.

These two projects essentially turned all standard PvZ levels into a text-based game, enabling VLM Agents to make strategic decisions based on precise memory data rather than unreliable visual coordinate estimation. Huge thanks to these projects and their authors.

---

## Roadmap

- [x] Basic window capture and screenshot module
- [x] VLM call and action parsing pipeline
- [x] Prompt template design and implementation
- [x] Short-term memory and context compression
- [x] WebUI remote monitoring and command input
- [x] Memory folder auto-loading and persistence
- [x] Pause control layer (soft / focus / hard)
- [x] PvZ memory reading: sun/cards/plants/zombies precise state
- [x] PvZ code injection: plant/shovel/cob cannon/seed selection/win level
- [x] PvZ injection-based main loop freeze (replacing Esc soft pause)
- [x] Action execution result verification (post-plant memory check)
- [x] WebUI action log with execution results (success/failure)
- [x] WebUI system/user prompt separated display
- [x] Guide system (on-demand plant/zombie guide queries)
- [x] Text-only LLM mode (no image input)
- [ ] MCP interface for external AI application integration
- [ ] Support for more game versions (requires offset tables)
