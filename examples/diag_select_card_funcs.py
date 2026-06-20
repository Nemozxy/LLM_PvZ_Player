"""选卡函数地址诊断 — 纯只读，不注入，不崩溃.

验证 choose_card / rock / pick_random_seeds 三个函数所需的前提条件:
  1. PVZ_BASE (0x6A9EC0) 处的指针是否有效
  2. SelectCardUi_p = [PVZ_BASE] + 0x774 是否有效 (选卡界面才存在)
  3. 卡片条目地址 SelectCardUi_p + 0xa4 + plantType*0x3c 是否像合法对象
  4. ChooseCard(0x486030) / Rock(0x486D20) / PickRandomSeeds(0x4859B0)
     入口字节是否像函数序言 (push ebp / mov ebp,esp 等)

用法: 在选卡界面运行。
"""

import sys

from vlm_game_agent.pvz.memory import PvZMemory, PvZMemoryError
from vlm_game_agent.pvz.injector import (
    PVZ_BASE,
    SELECT_CARD_UI_OFFSET,
    FUNC_CHOOSE_CARD,
    FUNC_ROCK,
    FUNC_PICK_RANDOM_SEEDS,
)

CARD_ENTRY_BASE = 0xA4
CARD_ENTRY_STRIDE = 0x3C


def hexdump(data: bytes, base: int, length: int = 16) -> str:
    """简单 hexdump 一行."""
    b = data[:length]
    hexs = " ".join(f"{x:02X}" for x in b)
    asci = "".join(chr(x) if 32 <= x < 127 else "." for x in b)
    return f"0x{base:08X}  {hexs:<{length*3}}  {asci}"


def main() -> int:
    mem = PvZMemory()
    if not mem.connect():
        print(f"[错误] 无法连接到 PvZ 进程（版本: {mem.version_name}）")
        return 1

    print(f"PVZ_BASE = 0x{PVZ_BASE:X}")
    print(f"SELECT_CARD_UI_OFFSET = 0x{SELECT_CARD_UI_OFFSET:X}")
    print()

    # ---- 1. 验证 PVZ_BASE 指针 ----
    try:
        pvz_obj = mem.read_pointer(PVZ_BASE)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 读取 [PVZ_BASE] 失败: {exc}")
        return 2
    print(f"[PVZ_BASE] → 0x{pvz_obj:08X}  (PvzBase 对象指针)")
    if pvz_obj < 0x10000 or pvz_obj > 0x7FFFFFFF:
        print("  [异常] PvzBase 指针看起来无效")
        return 3

    # ---- 2. 读取 game_ui 判断是否在选卡界面 ----
    # game_ui 在 PvzBase + 0x7FC (V1_0_0_1051_EN)
    try:
        ui_val = mem.read_int(pvz_obj + 0x7FC)
        print(f"game_ui = {ui_val}  (2=选卡, 3=战斗)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 读 game_ui 失败: {exc}")

    # ---- 3. 验证 SelectCardUi_p ----
    try:
        scui = mem.read_pointer(pvz_obj + SELECT_CARD_UI_OFFSET)
    except Exception as exc:  # noqa: BLE001
        print(f"[错误] 读取 SelectCardUi_p 失败: {exc}")
        return 4
    print(f"[PvzBase + 0x774] → 0x{scui:08X}  (SelectCardUi_p)")
    if scui < 0x10000 or scui > 0x7FFFFFFF:
        print("  [异常] SelectCardUi_p 看起来无效 — 可能不在选卡界面")
    else:
        # 读 SelectCardUi 对象开头几个字节
        try:
            head = mem.read_bytes(scui, 16)
            print("  SelectCardUi 对象头部:")
            print("    " + hexdump(head, scui))
        except Exception as exc:  # noqa: BLE001
            print(f"  [警告] 读 SelectCardUi 头部失败: {exc}")

    print()
    print("=" * 60)
    print("卡片条目地址验证 (SelectCardUi_p + 0xa4 + type*0x3c)")
    print("=" * 60)
    if 0x10000 < scui < 0x7FFFFFFF:
        for pt in [0, 1, 2, 3, 5, 7, 20]:
            entry_addr = scui + CARD_ENTRY_BASE + pt * CARD_ENTRY_STRIDE
            try:
                entry_bytes = mem.read_bytes(entry_addr, 16)
                # 卡片条目第一个字段通常是植物类型 ID (int)
                type_field = mem.read_int(entry_addr)
                print(f"  type={pt:2d}  entry=0x{entry_addr:08X}  "
                      f"第一字段(疑似plantType)={type_field}  "
                      f"| {hexdump(entry_bytes, entry_addr)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  type={pt:2d}  entry=0x{entry_addr:08X}  [读取失败: {exc}]")

    print()
    print("=" * 60)
    print("函数入口字节验证 (应是合法函数序言)")
    print("=" * 60)
    for name, addr in [
        ("ChooseCard", FUNC_CHOOSE_CARD),
        ("Rock", FUNC_ROCK),
        ("PickRandomSeeds", FUNC_PICK_RANDOM_SEEDS),
    ]:
        try:
            entry = mem.read_bytes(addr, 16)
            print(f"  {name:16s} 0x{addr:08X}  | {hexdump(entry, addr)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:16s} 0x{addr:08X}  [读取失败: {exc}]")

    print()
    print("常见 x86 函数序言参考:")
    print("  55                push ebp")
    print("  8B EC             mov ebp, esp")
    print("  53/56/57          push ebx/esi/edi")
    print("  83 EC xx          sub esp, xx")
    print("  6A FF             push -1  (MSVC 异常帧)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
