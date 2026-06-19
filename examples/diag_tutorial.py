"""诊断脚本 — dump 教学关（铲子教程）进行中的真实内存值.

用途: 确认 shovel 教学关 bug 的根因。
运行条件: 游戏停在教学关"铲干净场上植物"那一幕（已显示铲子+3棵豌豆射手）。
运行: python examples/diag_tutorial.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.pvz import PvZMemory, PvZStateReader
from vlm_game_agent.pvz.offsets import GameUI


def main() -> None:
    mem = PvZMemory()
    if not mem.connect():
        print("✗ 无法连接 PvZ 进程")
        return

    print(f"✓ 已连接: {mem.version_name}\n")
    mem.refresh_main_object()

    ui = mem.get_game_ui()
    mode = mem.get_game_mode()
    scene = mem.get_scene()
    ui_names = {1: "主界面", 2: "选卡界面", 3: "战斗界面"}

    print("=" * 50)
    print(f"game_ui   = {ui}  ({ui_names.get(ui, '未知/其他')})")
    print(f"  → GameUI.IN_GAME (=3)? {ui == GameUI.IN_GAME}")
    print(f"game_mode = {mode}")
    print(f"scene     = {scene}")
    print(f"MainObject ptr = 0x{mem.main_object:X}" if mem.main_object else "MainObject ptr = NULL")
    print("=" * 50)

    # 绕过 read_state 的 game_ui 守卫，直接读 plants
    reader = PvZStateReader(mem)
    state = reader.read_state()
    print(f"\nread_state() 走守卫后读到的 plants 数量: {len(state.plants)}")
    print(f"format_state() 输出:\n{'-' * 50}")
    print(reader.format_state(state))
    print("-" * 50)

    # 直接强制读取 plants（绕过 game_ui 守卫）
    print("\n--- 直接强制读取 plants（绕过守卫）---")
    try:
        plants = reader._read_plants()
        print(f"plants 数量: {len(plants)}")
        for p in plants:
            print(f"  [{p.index}] {p.name}  row={p.row} col={p.col}  hp={p.hp}/{p.hp_max}")
        print("\n→ 模型需要的精确坐标 (0-based) 就在上面。")
        print("→ 若 read_state 守卫把它挡掉了，模型只能靠视觉数格子，列号会偏 1。")
    except Exception as exc:
        print(f"读取 plants 失败: {exc}")


if __name__ == "__main__":
    main()
