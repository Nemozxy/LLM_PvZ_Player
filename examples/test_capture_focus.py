"""窗口捕获焦点测试 - 专门验证后台窗口是否能正确切到前台并截图."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.vision import CaptureConfig, WindowCapture


def main() -> None:
    cap = WindowCapture(
        config=CaptureConfig(
            scale=0.5,           # 缩小一半，节省磁盘空间
            output_format="PNG",
            activate_delay=0.2,  # 切前台后等 0.2s，给窗口重绘留出时间
        )
    )

    # 1. 列出窗口
    print("=" * 50)
    print("当前可见窗口：")
    for i, title in enumerate(cap.list_windows(), 1):
        print(f"  {i}. {title}")
    print("=" * 50)

    keyword = input("\n请输入要捕获的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入，退出。")
        return

    # 2. 定位窗口
    cap.find_window(keyword)
    print(f"\n窗口当前尺寸: {cap.window_size}")
    print("注意：现在请手动把这个窗口**放到后台**（切换到其他窗口盖住它）")
    input("按回车键开始连续截图测试...")

    # 3. 连续截图 5 次，每次都会自动切前台
    output_dir = ROOT / "capture_test"
    output_dir.mkdir(exist_ok=True)

    print(f"\n开始连续截图 5 次，结果保存到: {output_dir}")
    print("-" * 50)

    for i in range(1, 6):
        print(f"\n[{i}/5] 截图中...")
        # 关键：capture() 内部会自动 _ensure_foreground() + sleep(activate_delay)
        img = cap.capture()
        path = output_dir / f"shot_{i:02d}_{img.width}x{img.height}.png"
        img.save(path)
        print(f"      已保存: {path.name}")

        if i < 5:
            print("      等待 1 秒，你可以在此期间切换到其他窗口盖住它...")
            time.sleep(1)

    print("\n" + "=" * 50)
    print("测试完成。请检查 capture_test/ 目录下的图片：")
    print("- 如果每张图都是目标窗口的内容 → 切前台逻辑正常")
    print("- 如果某张图截到了其他窗口 → 切前台失败，请反馈")
    print("=" * 50)

    cap.close()


if __name__ == "__main__":
    main()
