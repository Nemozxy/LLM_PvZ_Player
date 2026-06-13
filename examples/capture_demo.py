"""[feat] 窗口捕获示例 - 展示如何定位窗口并截图."""

import sys
from pathlib import Path

# 将 src 加入路径（开发阶段临时方案）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.vision import CaptureConfig, WindowCapture


def main() -> None:
    cap = WindowCapture(
        config=CaptureConfig(
            scale=0.5,            # 截图后缩放为 50%，节省 Token
            output_format="JPEG", # 使用 JPEG 进一步压缩体积
            jpeg_quality=80,
        )
    )

    # 1. 列出所有可见窗口，方便用户复制标题
    print("当前可见窗口：")
    for title in cap.list_windows():
        print(f"  - {title}")
    print()

    # 2. 提示用户输入窗口标题关键词
    keyword = input("请输入要捕获的窗口标题（或部分标题）: ").strip()
    if not keyword:
        print("未输入标题，退出。")
        return

    # 3. 定位窗口
    try:
        cap.find_window(keyword)
    except RuntimeError as exc:
        print(f"错误: {exc}")
        return

    # 4. 单次截图并保存
    print("正在截图...")
    img = cap.capture()
    print(f"截图尺寸: {img.size}")

    output_path = ROOT / "capture_demo.jpg"
    img.save(output_path, quality=80)
    print(f"已保存到: {output_path}")

    # 5. 获取 Base64（模拟传给 VLM 的场景）
    b64 = cap.capture_to_base64()
    print(f"Base64 长度: {len(b64)} 字符")

    cap.close()


if __name__ == "__main__":
    main()
