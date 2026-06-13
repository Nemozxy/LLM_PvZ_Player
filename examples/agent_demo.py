"""[feat] Agent 集成示例 - 完整的 截图→VLM→执行 闭环."""

import asyncio
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from loguru import logger

from vlm_game_agent.agent import GameAgent
from vlm_game_agent.agent.llm import VLMClient
from vlm_game_agent.agent.memory import MemoryManager
from vlm_game_agent.pause import PauseController
from vlm_game_agent.vision import CaptureConfig, WindowCapture
from vlm_game_agent.webui.server import WebUIServer


async def main() -> None:
    # 1. 截图器
    cap = WindowCapture(
        config=CaptureConfig(
            scale=1.0,
            output_format="PNG",
            target_fps=1.0,
            capture_area="client",
        )
    )

    print("当前可见窗口：")
    for title in cap.list_windows():
        print(f"  - {title}")
    print()

    keyword = input("请输入要控制的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入标题，退出。")
        return

    cap.find_window(keyword)

    # 2. 时停控制（硬暂停：挂起进程，冻结画面且不干扰游戏状态）
    pause = PauseController()
    pause.bind_window(cap._window)
    pause.set_hard()

    # 3. VLM 客户端（连接本地 LM Studio）
    vlm = VLMClient(
        base_url="http://127.0.0.1:1234/v1",
        model="qwen3.6-35b-a3b-apex",
        api_key="sk-no-key-required",
        max_tokens=4096,
        temperature=0.3,
    )

    # 4. 记忆系统
    memory = MemoryManager()

    # 5. WebUI（可选，设为 None 可关闭）
    use_webui = input("是否启动 WebUI 监控? (y/n, 默认 y): ").strip().lower() != "n"
    webui_manager = None
    if use_webui:
        server = WebUIServer()
        webui_manager = server.get_manager()
        host = "0.0.0.0"
        port = 8080
        print(f"\nWebUI 已启动: http://localhost:{port}")
        # 在后台线程运行 FastAPI
        config = uvicorn.Config(server.app, host=host, port=port, loop="asyncio", log_level="warning")
        threading.Thread(target=lambda: uvicorn.Server(config).run(), daemon=True).start()

    # 6. 创建 Agent
    agent = GameAgent(
        capture=cap,
        pause=pause,
        vlm=vlm,
        memory=memory,
        webui=webui_manager,
        max_history_turns=6,
        pause_before_think=True,
    )

    # 7. 输入任务目标
    task = input("\n请输入任务目标（例如：打开设置菜单）: ").strip()
    if not task:
        task = "探索当前界面，告诉我你看到了什么"

    print(f"\n任务: {task}")
    print("按 F12 全局热键随时停止 Agent（无需窗口焦点）\n")

    try:
        # run 是同步阻塞的，直接在主线程运行
        agent.run(task)
    except KeyboardInterrupt:
        print("\n正在停止...")
        agent.stop()
    finally:
        cap.close()
        print("Agent 已退出")


if __name__ == "__main__":
    asyncio.run(main())
