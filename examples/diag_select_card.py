"""诊断脚本 — dump 选卡界面（"选择你的植物"）的真实内存值.

用途: 确认选卡界面 bug 的根因。
运行条件: 游戏停在"选择你的植物"选卡界面（能看到"一起摇摆吧！"按钮）。
运行: python examples/diag_select_card.py
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

    print("=" * 55)
    print(f"game_ui   = {ui}  ({ui_names.get(ui, '未知/其他')})")
    print(f"  → GameUI.IN_GAME (=3)? {ui == GameUI.IN_GAME}")
    print(f"game_mode = {mode}")
    print(f"scene     = {scene}")
    print(f"MainObject ptr = 0x{mem.main_object:X}" if mem.main_object else "MainObject ptr = NULL")
    print("=" * 55)

    reader = PvZStateReader(mem)
    state = reader.read_state()

    print(f"\nread_state() 读到:")
    print(f"  plants 数量: {len(state.plants)}")
    print(f"  zombies 数量: {len(state.zombies)}")
    print(f"  lawn_mowers 数量: {len(state.lawn_mowers)}")
    print(f"  grid_items 数量: {len(state.grid_items)}")
    print(f"  in_battle (当前判定): {state.in_battle}")
    print(f"  sun: {state.sun}")
    print(f"  wave: {state.wave}")

    print(f"\nformat_state() 输出（模型实际收到的 <game_state>）:")
    print("-" * 55)
    print(reader.format_state(state))
    print("-" * 55)

    print("\n→ 如果选卡界面 in_battle=True 且输出了阳光/波次/僵尸，")
    print("  说明 in_battle 判定回归了：选卡界面预加载的僵尸被误判为'战斗中'。")


if __name__ == "__main__":
    main()
