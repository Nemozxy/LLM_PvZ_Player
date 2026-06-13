"""[feat] 时停控制示例 - 测试暂停/恢复三种策略."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.vision import WindowCapture
from vlm_game_agent.pause import PauseController


def main() -> None:
    cap = WindowCapture()

    # 1. 列出窗口
    print("当前可见窗口：")
    for title in cap.list_windows():
        print(f"  - {title}")
    print()

    keyword = input("请输入要控制的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入标题，退出。")
        return

    cap.find_window(keyword)

    # 2. 选择暂停策略
    print("\n选择暂停策略：")
    print("  1. soft   - 软暂停（发送快捷键，如 Esc）")
    print("  2. focus  - 失焦暂停（切出焦点让游戏自动暂停）")
    print("  3. hard   - 硬暂停（挂起进程，强制冻结）")
    choice = input("请输入编号 (1/2/3，默认 1): ").strip() or "1"

    controller = PauseController()
    controller.bind_window(cap._window)

    if choice == "1":
        pause_key = input("暂停快捷键（默认 esc）: ").strip() or "esc"
        controller.set_soft(pause_key=pause_key)
    elif choice == "2":
        controller.set_focus()
    elif choice == "3":
        controller.set_hard()
    else:
        print("无效选择，退出。")
        return

    # 3. 演示 pause -> 截图 -> resume 流程
    print("\n=== 演示：暂停 -> 截图 -> 恢复 ===")
    print("3 秒后开始...")
    time.sleep(3)

    controller.pause()
    print("【已暂停】游戏应已冻结")

    # 模拟 AI 思考时间
    think_time = 2
    print(f"模拟 AI 思考 {think_time} 秒...")
    time.sleep(think_time)

    # 思考期间截图
    img = cap.capture()
    print(f"思考期间截图尺寸: {img.size}")

    controller.resume()
    print("【已恢复】游戏应继续运行")

    # 4. 使用 with 语句演示
    print("\n=== 演示：with 语句自动暂停/恢复 ===")
    print("3 秒后开始...")
    time.sleep(3)

    with controller:
        print("【with 内】游戏已暂停，2 秒后自动恢复")
        time.sleep(2)

    print("【with 结束】游戏已恢复")

    cap.close()


if __name__ == "__main__":
    main()
