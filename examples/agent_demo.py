"""[feat] Agent 集成示例 - 完整的 截图→VLM→执行 闭环."""

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from loguru import logger
from pynput import keyboard

from vlm_game_agent.agent import GameAgent
from vlm_game_agent.agent.action_logger import ActionLogger
from vlm_game_agent.agent.compressor import ContextCompressor
from vlm_game_agent.agent.llm import VLMClient
from vlm_game_agent.agent.memory import MemoryManager
from vlm_game_agent.config.settings import Settings
from vlm_game_agent.pause import PauseController
from vlm_game_agent.pvz import PvZMemory
from vlm_game_agent.vision import CaptureConfig, WindowCapture
from vlm_game_agent.webui.server import WebUIServer


def parse_stop_hotkey(key_str: str) -> keyboard.Key:
    """解析停止热键字符串."""
    key_map = {
        "f1": keyboard.Key.f1, "f2": keyboard.Key.f2, "f3": keyboard.Key.f3,
        "f4": keyboard.Key.f4, "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
        "f7": keyboard.Key.f7, "f8": keyboard.Key.f8, "f9": keyboard.Key.f9,
        "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
        "esc": keyboard.Key.esc, "pause": keyboard.Key.pause,
        "space": keyboard.Key.space, "enter": keyboard.Key.enter,
    }
    return key_map.get(key_str.lower(), keyboard.Key.f12)


def setup_pause_controller(
    pause: PauseController,
    strategy: str,
    pause_hotkey: str = "esc",
    resume_hotkey: str = "esc",
) -> None:
    """根据配置设置暂停策略."""
    if strategy == "soft":
        pause.set_soft(pause_key=pause_hotkey, resume_key=resume_hotkey)
    elif strategy == "hard":
        pause.set_hard()
    elif strategy == "focus":
        pause.set_focus()
    else:
        pause.set_hard()


def main() -> None:
    settings = Settings()
    pvz_soft_pause_disabled = settings.pvz_memory_enabled and settings.pause_strategy == "soft"
    effective_pause_before_think = settings.agent_pause_before_think and not pvz_soft_pause_disabled
    use_soft_pause = settings.pause_strategy == "soft" and effective_pause_before_think

    if pvz_soft_pause_disabled:
        print("PvZ 内存模式下已禁用 soft/esc 思考前暂停，避免暂停菜单污染截图。")

    # 1. 截图器
    # 只有真正启用软暂停时才避免截图前切前台；PvZ 内存模式会禁用 soft/esc 暂停，
    # 因此需要恢复截图前切前台，保证截到运行中的游戏画面而不是旧菜单。
    cap = WindowCapture(
        config=CaptureConfig(
            scale=settings.capture_scale,
            output_format=settings.capture_format,
            target_fps=settings.capture_fps,
            capture_area=settings.capture_area,
            ensure_foreground=not use_soft_pause,
        )
    )

    print("当前可见窗口：")
    for title in cap.list_windows():
        print(f"  - {title}")
    print()

    # 窗口标题：配置优先，否则交互输入
    keyword = settings.window_title.strip()
    if not keyword:
        keyword = input("请输入要控制的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入标题，退出。")
        return

    cap.find_window(keyword)

    # 2. 时停控制
    pause = PauseController()
    pause.bind_window(cap._window)
    setup_pause_controller(
        pause,
        settings.pause_strategy,
        pause_hotkey=settings.pause_hotkey,
        resume_hotkey=settings.resume_hotkey,
    )

    # 3. VLM 客户端
    vlm = VLMClient(
        base_url=settings.vlm_base_url,
        model=settings.vlm_model,
        api_key=settings.vlm_api_key,
        max_output_tokens=settings.vlm_max_output_tokens,
        temperature=settings.vlm_temperature,
    )

    # 4. 记忆系统
    memory = MemoryManager(settings.memory_dir)

    # 5. WebUI
    webui_manager = None
    if settings.webui_enabled:
        server = WebUIServer()
        webui_manager = server.get_manager()
        print(f"\nWebUI 已启动: http://localhost:{settings.webui_port}")
        config = uvicorn.Config(
            server.app,
            host=settings.webui_host,
            port=settings.webui_port,
            loop="asyncio",
            log_level="warning",
        )
        threading.Thread(
            target=lambda: uvicorn.Server(config).run(), daemon=True
        ).start()

    # 6. 上下文压缩器
    compress_base_url = settings.vlm_compress_base_url or settings.vlm_base_url
    compress_model = settings.vlm_compress_model or settings.vlm_model
    compress_api_key = settings.vlm_compress_api_key or settings.vlm_api_key
    compressor = ContextCompressor(
        base_url=compress_base_url,
        model=compress_model,
        api_key=compress_api_key,
        max_tokens=settings.vlm_context_size,
        compress_threshold=settings.agent_context_compress_threshold,
    )

    # 7. 操作日志记录器
    action_logger = None
    if settings.action_log_enabled:
        action_logger = ActionLogger(settings.action_log_dir)
        print(f"操作日志: {settings.action_log_dir}")

    # 8. PvZ 内存读取（可选，需要 PvZ 游戏运行中）
    pvz_memory = None
    if settings.pvz_memory_enabled:
        pvz_memory = PvZMemory()
        if pvz_memory.connect():
            print(f"PvZ 内存读取已启用: {pvz_memory.version_name}")
        else:
            print("PvZ 内存读取连接失败（将使用纯视觉模式）")
            pvz_memory = None

    # 9. 创建 Agent
    stop_hotkey = parse_stop_hotkey(settings.agent_stop_hotkey)
    agent = GameAgent(
        capture=cap,
        pause=pause,
        vlm=vlm,
        memory=memory,
        webui=webui_manager,
        max_history_turns=settings.agent_max_history_turns,
        pause_before_think=effective_pause_before_think,
        action_delay=settings.agent_action_delay,
        delay_click=settings.agent_delay_click,
        delay_drag=settings.agent_delay_drag,
        delay_key=settings.agent_delay_key,
        delay_type=settings.agent_delay_type,
        delay_idle=settings.agent_delay_idle,
        compressor=compressor,
        action_logger=action_logger,
        stop_hotkey=stop_hotkey,
        pvz_memory=pvz_memory,
        include_images_in_history=settings.agent_include_images_in_history,
        include_reasoning_in_history=settings.agent_include_reasoning_in_history,
    )

    # 7. 任务目标：配置优先，否则交互输入
    task = settings.task.strip()
    if not task:
        task = input("\n请输入任务目标（例如：打开设置菜单）: ").strip()
    if not task:
        task = "探索当前界面，告诉我你看到了什么"

    print(f"\n任务: {task}")
    print(f"按 {settings.agent_stop_hotkey.upper()} 全局热键随时停止 Agent（无需窗口焦点）\n")

    try:
        agent.run(task)
    except KeyboardInterrupt:
        print("\n正在停止...")
        agent.stop()
    finally:
        cap.close()
        print("Agent 已退出")


if __name__ == "__main__":
    main()
