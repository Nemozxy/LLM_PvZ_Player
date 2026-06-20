"""僵尸定位验证脚本 - 调用实际 grid_to_pixel 获取真实坐标。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vlm_game_agent.pvz.memory import PvZMemory
from vlm_game_agent.pvz.injector import PvZCodeInjector


def main():
    print("=" * 60)
    print("PvZ grid_to_pixel 坐标验证")
    print("=" * 60)
    print()

    mem = PvZMemory()
    if not mem.connect():
        print("连接失败！请确保 PvZ 游戏正在运行。")
        return

    print(f"连接成功: {mem.version_name}")
    print()

    injector = PvZCodeInjector(mem)

    print("测试各列中心坐标:")
    print("-" * 50)
    for col in range(10):
        x, y = injector.grid_to_pixel(0, col)
        print(f"  col {col}: x={x}, y={y}")

    print()
    print("反推公式验证 (假设僵尸 abscissa=661.6):")
    print("-" * 50)
    test_abscissa = 661.6

    # 公式1: (abscissa - 40) / 80
    f1 = (test_abscissa - 40) / 80
    print(f"  (abscissa-40)/80 = {f1:.2f} → 显示列 {round(f1, 1)}")

    # 公式2: abscissa / 80 - 1
    f2 = test_abscissa / 80 - 1
    print(f"  abscissa/80-1    = {f2:.2f} → 显示列 {round(f2, 1)}")

    # 公式3: abscissa / 80
    f3 = test_abscissa / 80
    print(f"  abscissa/80      = {f3:.2f} → 显示列 {round(f3, 1)}")

    print()
    print("如果僵尸在 col 8 中间，其 abscissa 应该接近:")
    x8, _ = injector.grid_to_pixel(0, 8)
    print(f"  grid_to_pixel(0, 8) = ({x8}, ...)")
    print(f"  实际 abscissa = {test_abscissa}")
    print(f"  差值 = {abs(x8 - test_abscissa):.1f} 像素")

    print()
    print("结论:")
    print("-" * 50)
    if abs(x8 - test_abscissa) < 30:
        print(f"  ✓ abscissa={test_abscissa} 确实对应 col 8")
        print(f"  ✓ 正确的反推公式应使 {test_abscissa} → 8.0")
    else:
        print(f"  ✗ abscissa={test_abscissa} 与 col 8 (x={x8}) 差距较大")
        print(f"  ✗ 需要进一步确认网格参数")


if __name__ == "__main__":
    main()
