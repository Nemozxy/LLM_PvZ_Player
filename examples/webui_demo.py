"""[feat] WebUI 监控示例 - 截图推流 + 接收指令."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import uvicorn
from loguru import logger

from vlm_game_agent.vision import CaptureConfig, WindowCapture
from vlm_game_agent.webui.server import WebUIServer


async def capture_loop(cap: WindowCapture, server: WebUIServer) -> None:
    """后台协程：持续截图并通过 WebSocket 推流."""
    manager = server.get_manager()
    interval = 1.0 / cap.config.target_fps

    logger.info("[推流] 开始截图推流，目标 FPS: {}", cap.config.target_fps)
    await manager.push_log("推流协程已启动", "info")

    while True:
        t0 = asyncio.get_event_loop().time()
        try:
            b64 = cap.capture_to_base64()
            await manager.push_frame(b64)
        except Exception as exc:
            logger.error("[推流] 截图失败: {}", exc)
            await manager.push_log(f"截图失败: {exc}", "error")

        # 检查用户指令
        cmd = manager.get_command()
        if cmd:
            logger.info("[推流] 收到用户指令: {}", cmd)
            await manager.push_log(f"收到指令: {cmd}", "user")

        # 帧率控制
        elapsed = asyncio.get_event_loop().time() - t0
        sleep_time = interval - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


def main() -> None:
    cap = WindowCapture(
        config=CaptureConfig(
            scale=1.0,              # 原尺寸，不缩放
            output_format="JPEG",
            jpeg_quality=90,        # 高质量 JPEG
            target_fps=2.0,
        )
    )

    # 1. 选择窗口
    print("当前可见窗口：")
    for title in cap.list_windows():
        print(f"  - {title}")
    print()

    keyword = input("请输入要捕获的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入标题，退出。")
        return

    cap.find_window(keyword)

    # 2. 启动 WebUI 服务器
    server = WebUIServer()
    host = "0.0.0.0"
    port = 8080
    print(f"\nWebUI 已启动: http://localhost:{port}")
    print("请在浏览器中打开上述地址查看实时画面。\n")

    # 3. 用 asyncio 同时运行 FastAPI 和截图推流
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # FastAPI 服务
    config = uvicorn.Config(server.app, host=host, port=port, loop="asyncio", log_level="warning")
    server_task = loop.create_task(uvicorn.Server(config).serve())

    # 截图推流
    capture_task = loop.create_task(capture_loop(cap, server))

    try:
        loop.run_until_complete(asyncio.gather(server_task, capture_task))
    except KeyboardInterrupt:
        print("\n正在关闭...")
    finally:
        cap.close()
        loop.close()


if __name__ == "__main__":
    main()
