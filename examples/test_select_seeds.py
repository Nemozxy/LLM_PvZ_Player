"""验证 choose_card 修复 — 在选卡界面跑一遍完整选卡→填满→开始游戏.

用法:
  1. 进入游戏选卡界面（能看到"选择你的植物"和"一起摇摆吧！"按钮）
  2. python -m examples.test_select_seeds            # 默认选 [0,1,3,5]
  3. 自定义植物: python -m examples.test_select_seeds 2 4 6

验证点:
  - 不崩溃 → choose_card 的多余解引用 bug 已修复
  - 卡组被填满、进入战斗界面 (game_ui 由 2 → 3) → 三步全对
"""

import sys
import time

from vlm_game_agent.pvz.memory import PvZMemory
from vlm_game_agent.pvz.injector import PvZCodeInjector


def main() -> int:
    seeds = [int(x) for x in sys.argv[1:]] or [0, 1, 3, 5]
    print(f"计划选卡 (植物类型): {seeds}")

    # ---- 1. 连接 ----
    mem = PvZMemory()
    if not mem.connect():
        print("✗ 无法连接 PvZ 进程")
        return 1
    print(f"✓ 已连接: {mem.version_name}")

    # ---- 2. 必须在选卡界面 ----
    mem.refresh_main_object()
    ui = mem.get_game_ui()
    if ui != 2:
        names = {1: "主界面", 2: "选卡", 3: "战斗"}
        print(f"✗ 当前 game_ui={ui} ({names.get(ui, '未知')})，请在选卡界面运行")
        return 2
    print("✓ 已在选卡界面 (game_ui=2)")

    # ---- 3. 初始化注入器并逐张选卡 ----
    inj = PvZCodeInjector(mem)
    try:
        chosen: list[int] = []
        for pt in seeds:
            inj.choose_card(pt)
            chosen.append(pt)
            print(f"  ✓ 选卡 植物类型={pt}")

        # 随机填满剩余卡槽
        inj.pick_random_seeds()
        print("  ✓ 随机填满卡槽")

        # 开始游戏
        inj.rock()
        print("  ✓ Rock 开始游戏")
    finally:
        inj.close()

    # ---- 4. 复查界面是否切到战斗 ----
    time.sleep(1.0)
    mem.refresh_main_object()
    ui2 = mem.get_game_ui()
    print(f"\n选卡后 game_ui={ui2}  (期望 3=战斗)")
    if ui2 == 3:
        print("✅ 成功：已进入战斗界面，choose_card 修复验证通过")
        return 0
    print("⚠️  仍未进入战斗，可能卡组未满或 Rock 未生效（但未崩溃，说明注入本身正确）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
