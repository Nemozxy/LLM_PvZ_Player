"""僵尸定位测试脚本.

实时读取 PvZ 内存中的僵尸位置，显示原始坐标和估算列号，
用于验证僵尸定位的准确性。

使用方法:
    1. 启动 PvZ 游戏，进入有僵尸的战斗场景
    2. 运行此脚本
    3. 对比脚本输出的位置信息与游戏截图中的僵尸实际位置
"""

import time
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vlm_game_agent.pvz.memory import PvZMemory
from vlm_game_agent.pvz.reader import PvZStateReader


def clear_screen():
    """清屏."""
    print("\033[2J\033[H", end="")


def format_zombie_detail(z) -> str:
    """格式化单个僵尸的详细信息."""
    tags = []
    if z.is_eat:
        tags.append("啃食中")
    if z.freeze_countdown > 0:
        tags.append(f"冻结{z.freeze_countdown}cs")
    elif z.slow_countdown > 0:
        tags.append(f"减速{z.slow_countdown}cs")
    if z.fixation_countdown > 0:
        tags.append(f"定身{z.fixation_countdown}cs")

    tag_str = f" [{', '.join(tags)}]" if tags else ""

    return (
        f"  行{z.row} 列≈{z.col_estimate:.1f} | "
        f"原始x={z.abscissa:.1f} 速度={z.speed:.2f} | "
        f"{z.name}{tag_str}"
    )


def main():
    """主函数."""
    print("=" * 60)
    print("PvZ 僵尸定位测试")
    print("=" * 60)
    print()

    # 连接 PvZ 内存
    print("正在连接 PvZ 内存...")
    mem = PvZMemory()
    if not mem.connect():
        print("连接失败！请确保 PvZ 游戏正在运行。")
        return

    print(f"连接成功: {mem.version_name}")
    print()

    reader = PvZStateReader(mem)

    print("开始实时监控 (Ctrl+C 退出)")
    print("-" * 60)
    print("说明:")
    print("  - 行: 0-4 对应游戏里从上到下 5 行")
    print("  - 列≈: 估算列号，0 是最左边，8-9 是最右边")
    print("  - 原始x: 游戏内横坐标，越小越靠近房子")
    print("  - 请对比游戏截图验证位置是否准确")
    print("-" * 60)
    print()

    try:
        while True:
            clear_screen()
            print("=" * 60)
            print("PvZ 僵尸定位测试 (Ctrl+C 退出)")
            print("=" * 60)
            print()

            # 读取状态
            state = reader.read_state()

            # 显示基础信息
            print(f"阳光: {state.sun} | 波次: {state.wave + 1}/{state.total_wave}")
            print()

            # 显示僵尸信息
            if not state.zombies:
                print("🧟 僵尸: (无)")
            else:
                print(f"🧟 僵尸: {len(state.zombies)} 个")
                print()

                # 按行分组显示
                zombies_by_row: dict[int, list] = {}
                for z in state.zombies:
                    zombies_by_row.setdefault(z.row, []).append(z)

                for row in sorted(zombies_by_row.keys()):
                    print(f"  【行 {row}】")
                    row_zombies = sorted(zombies_by_row[row], key=lambda z: -z.abscissa)
                    for z in row_zombies:
                        print(format_zombie_detail(z))
                    print()

            # 显示列号参考
            print("-" * 60)
            print("列号参考 (估算公式: col = (abscissa - 40) / 80):")
            print("  列0: x≈40-120   列1: x≈120-200  列2: x≈200-280")
            print("  列3: x≈280-360  列4: x≈360-440  列5: x≈440-520")
            print("  列6: x≈520-600  列7: x≈600-680  列8: x≈680-760")
            print("  列9: x≈760+     (屏幕右侧外)")
            print("-" * 60)
            print()
            print(f"刷新时间: {time.strftime('%H:%M:%S')}")

            time.sleep(5)

    except KeyboardInterrupt:
        print("\n\n已退出测试。")


if __name__ == "__main__":
    main()
