"""PvZ 代码注入器 测试脚本 — 无需 LLM，逐步验证注入功能.

用法:
  1. 以管理员权限运行 PowerShell / CMD
  2. 打开 PvZ (V1_0_0_1051_EN)，进入战斗界面
  3. 运行: python test_injector.py

逐步测试:
  Step 1 - 连接进程 + 初始化注入器
  Step 2 - 测试 GridToAbscissa/Ordinate 精确坐标
  Step 3 - 测试 hack 开关 (auto_collect / unlock_sun)
  Step 4 - 交互式动作测试 (种植物 / 铲除 / 点击)
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.pvz import (
    PvZMemory,
    PvZStateReader,
    PvZCodeInjector,
    HACK_BLOCK_MAIN_LOOP,
    HACK_AUTO_COLLECTED,
    HACK_UNLOCK_SUN_LIMIT,
    HACK_PLACED_ANYWHERE,
)
from vlm_game_agent.pvz.executor import grid_to_game_pixel
from vlm_game_agent.pvz.injector import PVZ_BASE


def print_sep(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def step1_connect() -> tuple[PvZMemory, PvZCodeInjector, PvZStateReader] | None:
    """Step 1: 连接进程 + 初始化注入器."""
    print_sep("Step 1: 连接进程 + 初始化注入器")

    mem = PvZMemory()
    print("正在查找 PvZ 进程...")
    if not mem.connect():
        print("❌ 连接失败！请确认游戏已启动并以管理员权限运行。")
        return None

    print(f"✅ 内存连接成功")
    print(f"   版本: {mem.version_name}")
    print(f"   PID:  {mem._pid}")

    # 初始化注入器
    try:
        injector = PvZCodeInjector(mem)
        print(f"✅ 注入器初始化成功 (handle=0x{injector._inject_handle:X})")
    except Exception as exc:
        print(f"❌ 注入器初始化失败: {exc}")
        print("   可能原因: 权限不足，请以管理员身份运行")
        return None

    # 读取游戏状态
    reader = PvZStateReader(mem)
    state = reader.read_state()

    ui_names = {0: "??", 1: "主界面", 2: "选卡界面", 3: "战斗界面"}
    print(f"   游戏UI: {ui_names.get(state.game_ui, '未知')}")
    print(f"   阳光: {state.sun}")

    if state.game_ui != 3:
        print("\n⚠️  当前不在战斗界面，部分测试可能无法正常工作")
        print("   建议进入关卡战斗画面后再继续")

    return mem, injector, reader


def step2_grid_coordinates(injector: PvZCodeInjector, reader: PvZStateReader) -> None:
    """Step 2: 测试 GridToAbscissa/Ordinate 精确坐标."""
    print_sep("Step 2: GridToAbscissa/Ordinate 精确坐标")

    print("调用游戏内部函数获取精确坐标 (注入模式) vs 近似公式 (鼠标模式):\n")

    # 对比表头
    print(f"  {'(row,col)':>10} | {'注入 x':>8} {'注入 y':>8} | {'近似 x':>8} {'近似 y':>8} | {'Δx':>5} {'Δy':>5}")
    print(f"  {'-'*10} | {'-'*8} {'-'*8} | {'-'*8} {'-'*8} | {'-'*5} {'-'*5}")

    n_rows = 5  # 标准关卡 5 行
    for row in range(n_rows):
        for col in range(9):
            try:
                ix, iy = injector.grid_to_pixel(row, col)
            except Exception as exc:
                print(f"  ({row},{col}): 注入失败 - {exc}")
                continue

            ax, ay = grid_to_game_pixel(row, col, 0)
            dx = ix - ax
            dy = iy - ay
            marker = " ⚠️" if abs(dx) > 5 or abs(dy) > 5 else ""
            print(f"  ({row},{col}):  {ix:>5}    {iy:>5}  |  {ax:>5}    {ay:>5}  | {dx:>+5} {dy:>+5}{marker}")

    # 读取 PVZ_BASE+0x900/0x904 验证写入区
    try:
        val_x = injector._mem.read_int(PVZ_BASE + 0x900)
        val_y = injector._mem.read_int(PVZ_BASE + 0x904)
        print(f"\n  最后写入的结果地址 PVZ_BASE+0x900/0x904:")
        print(f"    x = {val_x} (0x{val_x & 0xFFFFFFFF:08X})")
        print(f"    y = {val_y} (0x{val_y & 0xFFFFFFFF:08X})")
    except Exception as exc:
        print(f"  读取结果地址失败: {exc}")


def step3_hacks(injector: PvZCodeInjector, reader: PvZStateReader) -> None:
    """Step 3: 测试 hack 开关."""
    print_sep("Step 3: Hack 开关测试")

    print("可用 hack:")
    print(f"  1. auto_collect   阳光自动收集  (addr=0x{HACK_AUTO_COLLECTED.addr:X})")
    print(f"  2. unlock_sun     解除阳光上限  (addr=0x{HACK_UNLOCK_SUN_LIMIT.addr:X})")
    print(f"  3. placed_anywhere 任意位置种植 (addr=0x{HACK_PLACED_ANYWHERE.addr:X})")
    print()

    while True:
        state = reader.read_state()
        print(f"当前阳光: {state.sun}")
        print("  a - 开启 auto_collect (阳光自动飞向计数器)")
        print("  A - 关闭 auto_collect")
        print("  u - 开启 unlock_sun_limit (阳光无上限)")
        print("  U - 关闭 unlock_sun_limit")
        print("  p - 开启 placed_anywhere (任意位置种植)")
        print("  P - 关闭 placed_anywhere")
        print("  q - 下一步")
        choice = input("> ").strip().lower()

        if choice == 'q':
            break
        elif choice == 'a':
            injector.set_auto_collect(True)
            print("✅ auto_collect 已开启 — 观察阳光是否自动飞向计数器")
        elif choice == 'A':
            injector.set_auto_collect(False)
            print("✅ auto_collect 已关闭")
        elif choice == 'u':
            injector.set_unlock_sun_limit(True)
            print("✅ unlock_sun_limit 已开启")
        elif choice == 'U':
            injector.set_unlock_sun_limit(False)
            print("✅ unlock_sun_limit 已关闭")
        elif choice == 'p':
            injector.set_placed_anywhere(True)
            print("✅ placed_anywhere 已开启")
        elif choice == 'P':
            injector.set_placed_anywhere(False)
            print("✅ placed_anywhere 已关闭")


def step4_actions(
    injector: PvZCodeInjector,
    reader: PvZStateReader,
    mem: PvZMemory,
) -> None:
    """Step 4: 交互式动作测试."""
    print_sep("Step 4: 交互式动作测试")

    print("使用代码注入执行游戏动作:")
    print()

    while True:
        state = reader.read_state()
        print(f"阳光: {state.sun}  波次: {state.wave+1}/{state.total_wave}")

        # 显示卡片
        if state.seeds:
            seed_line = "卡片: "
            for s in state.seeds:
                mark = "✅" if s.is_ready else "⏳"
                seed_line += f"[{s.index}]{s.name}{mark} "
            print(seed_line)

        print("\n  p <卡片序号> <行> <列> - 种植植物 (如 p 0 2 3)")
        print("  h <行> <列>           - 铲除植物 (如 h 1 4)")
        print("  c <卡片序号>          - 点击卡片 (如 c 0)")
        print("  s                     - 收集一颗阳光")
        print("  S                     - 收集所有阳光")
        print("  m <x> <y>             - MouseClick 坐标点击 (如 m 400 300)")
        print("  r                     - 重新读取状态")
        print("  q                     - 退出")

        choice = input("> ").strip()

        if choice == 'q':
            break
        elif choice == 'r':
            continue
        elif choice == 's':
            state = reader.read_state()
            sun_items = [it for it in state.items if it.is_sun and not it.is_collected]
            if not sun_items:
                print("当前没有可收集的阳光")
                continue
            it = sun_items[0]
            print(f"  收集阳光 at ({int(it.x)}, {int(it.y)})")
            try:
                injector.collect_sun_at(int(it.x), int(it.y))
                print("  ✅ 已执行")
            except Exception as exc:
                print(f"  ❌ 失败: {exc}")
        elif choice == 'S':
            state = reader.read_state()
            sun_items = [it for it in state.items if it.is_sun and not it.is_collected]
            if not sun_items:
                print("当前没有可收集的阳光")
                continue
            coords = [(int(it.x), int(it.y)) for it in sun_items[:8]]
            print(f"  批量收集 {len(coords)} 个阳光")
            try:
                count = injector.collect_all_sun(coords)
                print(f"  ✅ 已收集 {count} 个")
            except Exception as exc:
                print(f"  ❌ 失败: {exc}")
        elif choice.startswith('p '):
            try:
                parts = choice.split()
                idx, row, col = int(parts[1]), int(parts[2]), int(parts[3])
                state = reader.read_state()
                if idx < 0 or idx >= len(state.seeds):
                    print(f"无效卡片序号: {idx}")
                    continue
                seed = state.seeds[idx]
                plant_type = seed.plant_type
                imitater = seed.imitator_type >= 0
                if imitater:
                    plant_type = seed.imitator_type
                print(f"  💉 种植: 点击卡片[{idx}] + 点击格子({row},{col})")
                try:
                    # 点卡片中心
                    if seed.x > 0 and seed.y > 0:
                        card_cx = seed.x + seed.width // 2
                        card_cy = seed.y + seed.height // 2
                    else:
                        card_cx = 80 + seed.index * 51 + 25
                        card_cy = 10
                    injector.mouse_click(card_cx, card_cy)
                    time.sleep(0.1)
                    # 点格子中心
                    gx, gy = injector.grid_to_pixel(row, col)
                    injector.mouse_click(gx, gy)
                    print("  ✅ 已执行 — 观察游戏画面")
                except Exception as exc:
                    print(f"  ❌ 失败: {exc}")
            except (ValueError, IndexError):
                print("用法: p <卡片序号> <行> <列>，如 p 0 2 3")
        elif choice.startswith('h '):
            try:
                parts = choice.split()
                row, col = int(parts[1]), int(parts[2])
                state = reader.read_state()
                print(f"  💉 铲除 row={row} col={col}")
                try:
                    # 1. 释放鼠标选中状态
                    injector.release_mouse()
                    # 2. 点击铲子按钮
                    if state.seeds:
                        last = state.seeds[-1]
                        if last.x > 0 and last.width > 0:
                            shovel_x = last.x + last.width + 10 + 24
                            shovel_y = last.y + last.height // 2
                        else:
                            shovel_x, shovel_y = 400, 43
                    else:
                        shovel_x, shovel_y = 400, 43
                    print(f"    铲子按钮: ({shovel_x}, {shovel_y})")
                    injector.mouse_click(shovel_x, shovel_y)
                    time.sleep(0.1)
                    # 3. 点击目标格子
                    gx, gy = injector.grid_to_pixel(row, col)
                    print(f"    格子坐标: ({gx}, {gy})")
                    injector.mouse_click(gx, gy)
                    print("  ✅ 已执行 — 观察游戏画面")
                except Exception as exc:
                    print(f"  ❌ 失败: {exc}")
            except (ValueError, IndexError):
                print("用法: h <行> <列>，如 h 1 4")
        elif choice.startswith('c '):
            try:
                idx = int(choice.split()[1])
                state = reader.read_state()
                if idx < 0 or idx >= len(state.seeds):
                    print(f"无效卡片序号: {idx}")
                    continue
                seed = state.seeds[idx]
                if seed.x > 0 and seed.y > 0:
                    cx = seed.x + seed.width // 2
                    cy = seed.y + seed.height // 2
                else:
                    cx = 80 + seed.index * 51 + 25
                    cy = 10
                print(f"  💉 MouseClick card [{idx}] at ({cx}, {cy})")
                try:
                    injector.mouse_click(cx, cy)
                    print("  ✅ 已执行")
                except Exception as exc:
                    print(f"  ❌ 失败: {exc}")
            except (ValueError, IndexError):
                print("用法: c <卡片序号>，如 c 0")
        elif choice.startswith('m '):
            try:
                parts = choice.split()
                x, y = int(parts[1]), int(parts[2])
                print(f"  💉 MouseClick at ({x}, {y})")
                try:
                    injector.mouse_click(x, y)
                    print("  ✅ 已执行")
                except Exception as exc:
                    print(f"  ❌ 失败: {exc}")
            except (ValueError, IndexError):
                print("用法: m <x> <y>，如 m 400 300")


def main():
    print("PvZ 代码注入器 测试")
    print("=" * 60)
    print()
    print("⚠️  前提条件:")
    print("  1. PvZ 游戏已启动 (V1_0_0_1051_EN)")
    print("  2. 已进入战斗界面")
    print("  3. 以管理员权限运行此脚本")
    print()
    input("按 Enter 开始...")

    # Step 1: 连接 + 初始化
    result = step1_connect()
    if not result:
        return
    mem, injector, reader = result

    # Step 2: 精确坐标
    try:
        step2_grid_coordinates(injector, reader)
    except Exception as exc:
        print(f"❌ Step 2 失败: {exc}")

    # Step 3: Hack 开关
    try:
        step3_hacks(injector, reader)
    except Exception as exc:
        print(f"❌ Step 3 失败: {exc}")

    # Step 4: 交互式动作
    try:
        step4_actions(injector, reader, mem)
    except KeyboardInterrupt:
        print("\n\n已退出")

    # 清理
    print("\n正在清理 (恢复所有 hack, 关闭注入句柄)...")
    injector.close()
    print("✅ 清理完成，测试结束。")


if __name__ == "__main__":
    main()
