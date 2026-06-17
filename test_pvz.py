"""[调试] PvZ 内存读取 + 动作执行 测试脚本.

用法:
  1. 以管理员权限运行 PowerShell / CMD
  2. 打开 PvZ 游戏，进入战斗界面（选好卡，开始关卡）
  3. 运行: python test_pvz.py

逐步测试:
  Step 1 - 连接进程，检测版本
  Step 2 - 读取完整游戏状态并打印
  Step 3 - 验证格子→屏幕坐标映射
  Step 4 - 测试安全动作（收集阳光 / 点击卡片）
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from vlm_game_agent.pvz import PvZMemory, PvZStateReader, PvZExecutor, GameState
from vlm_game_agent.pvz.executor import (
    grid_to_game_pixel,
    game_pixel_to_screen,
    _grid_y_by_scene,
    PVZ_STANDARD_WIDTH,
    PVZ_STANDARD_HEIGHT,
    SHOVEL_X,
    SHOVEL_Y,
)
from vlm_game_agent.vision import WindowCapture, CaptureConfig


def get_pvz_client_rect() -> tuple[int, int, int, int]:
    """获取 PvZ 窗口客户区屏幕坐标"""
    import win32gui
    import win32con

    # 查找 PvZ 窗口
    def find_pvz():
        result = []
        def callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                cls = win32gui.GetClassName(hwnd)
                if cls == "MainWindow" and ("Plants vs" in title or "植物" in title):
                    result.append(hwnd)
            return True
        win32gui.EnumWindows(callback, None)
        return result[0] if result else None

    hwnd = find_pvz()
    if not hwnd:
        raise RuntimeError("找不到 PvZ 窗口，请确保游戏已启动")

    rect = win32gui.GetClientRect(hwnd)
    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    left, top = pt
    right, bottom = left + rect[2], top + rect[3]
    return left, top, right, bottom


def print_separator(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def step1_connect() -> PvZMemory | None:
    """Step 1: 连接 PvZ 进程"""
    print_separator("Step 1: 连接 PvZ 进程")

    mem = PvZMemory()
    print("正在查找 PvZ 窗口...")
    print("（如果长时间无输出，请确认游戏已启动）")

    if mem.connect():
        print(f"✅ 连接成功！")
        print(f"   版本: {mem.version_name}")
        print(f"   进程 ID: {mem._pid}")
        print(f"   窗口句柄: {mem._hwnd:#x}")
        return mem
    else:
        print("❌ 连接失败！可能的原因：")
        print("   1. PvZ 游戏没有启动")
        print("   2. 没有以管理员权限运行此脚本")
        print("   3. 版本不支持（支持: 英文原版/中文年度版/英文年度版等）")
        return None


def step2_read_state(mem: PvZMemory) -> GameState | None:
    """Step 2: 读取游戏状态"""
    print_separator("Step 2: 读取完整游戏状态")

    reader = PvZStateReader(mem)
    state = reader.read_state()

    # 基础信息
    ui_names = {0: "??", 1: "主界面", 2: "选卡界面", 3: "战斗界面"}
    print(f"游戏 UI: {ui_names.get(state.game_ui, '未知')}")
    print(f"场景: {state.scene_name}")
    print(f"阳光: {state.sun}")
    print(f"波次: {state.wave + 1}/{state.total_wave}")
    print(f"暂停: {'是' if state.is_paused else '否'}")

    if state.game_ui != 3:
        print("\n⚠️  当前不在战斗界面！")
        print("   请进入关卡战斗画面后重新运行。")
        print("   （在主界面选一个关卡进入后即可）")
        return None

    print(f"\n--- 格式化输出（与发给 LLM 的一致）---")
    formatted = reader.format_state(state)
    print(formatted)

    return state


def step3_coordinates(mem: PvZMemory, state: GameState) -> None:
    """Step 3: 验证坐标映射"""
    print_separator("Step 3: 坐标映射验证")

    try:
        left, top, right, bottom = get_pvz_client_rect()
        cw, ch = right - left, bottom - top
        print(f"窗口客户区: ({left}, {top}) - ({right}, {bottom})")
        print(f"客户区尺寸: {cw}x{ch}")
        print(f"缩放比例: x={cw/PVZ_STANDARD_WIDTH:.3f}, y={ch/PVZ_STANDARD_HEIGHT:.3f}")
    except RuntimeError:
        print("⚠️  无法获取窗口坐标，使用 win32gui 兜底")
        left, top, right, bottom = 0, 0, PVZ_STANDARD_WIDTH, PVZ_STANDARD_HEIGHT
        cw, ch = PVZ_STANDARD_WIDTH, PVZ_STANDARD_HEIGHT

    # 测试格子坐标
    n_rows = 6 if state.scene in (2, 3) else 5
    print(f"\n格子坐标 ({n_rows}行 x 9列, 场景={state.scene_name}):")
    print(f"  {'col>':>5}", end="")
    for col in range(9):
        print(f"  {col:>6}", end="")
    print()
    for row in range(n_rows):
        print(f"  row{row:>2}", end=" ")
        for col in range(9):
            gx, gy = grid_to_game_pixel(row, col, state.scene)
            sx = int(left + gx * cw / PVZ_STANDARD_WIDTH)
            sy = int(top + gy * ch / PVZ_STANDARD_HEIGHT)
            print(f"({sx:>4},{sy:>4})", end="")
        print()

    # 铲子按钮屏幕坐标
    sx, sy = game_pixel_to_screen(
        SHOVEL_X, SHOVEL_Y,
        lambda: (left, top, right, bottom),
    )
    print(f"\n铲子按钮屏幕坐标: ({sx}, {sy})")

    # 卡片坐标（如果有）
    if state.seeds:
        print(f"\n卡片屏幕坐标:")
        for s in state.seeds:
            if s.x > 0 and s.y > 0:
                cx = s.x + s.width // 2
                cy = s.y + s.height // 2
                ssx = int(left + cx * cw / PVZ_STANDARD_WIDTH)
                ssy = int(top + cy * ch / PVZ_STANDARD_HEIGHT)
            else:
                # 兜底估算
                cx = 80 + s.index * 51 + 25
                cy = 10
                ssx = int(left + cx * cw / PVZ_STANDARD_WIDTH)
                ssy = int(top + cy * ch / PVZ_STANDARD_HEIGHT)
            print(f"  [{s.index}] {s.name}: ({ssx}, {ssy}){' ✅' if s.is_ready else ' ⏳'}")

    # 阳光位置（如果有）
    sun_items = [it for it in state.items if it.is_sun and not it.is_collected]
    if sun_items:
        print(f"\n阳光屏幕坐标 ({len(sun_items)}个):")
        for it in sun_items[:5]:
            sx = int(left + it.x * cw / PVZ_STANDARD_WIDTH)
            sy = int(top + it.y * ch / PVZ_STANDARD_HEIGHT)
            print(f"  [{it.index}] {it.name}: ({sx}, {sy})")


def step4_safe_action(mem: PvZMemory, state: GameState) -> None:
    """Step 4: 执行安全动作"""
    print_separator("Step 4: 安全动作测试")

    try:
        rect = get_pvz_client_rect()
    except RuntimeError:
        print("❌ 无法获取窗口坐标，跳过")
        return

    reader = PvZStateReader(mem)
    executor = PvZExecutor(memory=mem, get_client_rect=lambda: rect)

    while True:
        print("\n可选操作:")
        print("  s - 收集一颗阳光")
        print("  a - 收集所有阳光")
        print("  c <序号> - 点击第N张卡片（如 c 0）")
        print("  p <序号> <行> <列> - 种植植物（如 p 0 2 3）")
        print("  h <行> <列> - 铲除植物（如 h 1 4）")
        print("  r - 重新读取状态")
        print("  q - 退出")
        choice = input("\n> ").strip().lower()

        if choice == 'q':
            break
        elif choice == 'r':
            state = reader.read_state()
            formatted = reader.format_state(state)
            print(formatted)
            continue
        elif choice == 's':
            state = reader.read_state()
            result = executor.execute("collect_sun", {"index": "0"}, state)
            print(f"结果: {result}")
        elif choice == 'a':
            state = reader.read_state()
            result = executor.execute("collect_sun", {"index": "all"}, state)
            print(f"结果: {result}")
        elif choice.startswith('c '):
            try:
                idx = int(choice.split()[1])
                state = reader.read_state()
                result = executor.execute("click_card", {"card_index": idx}, state)
                print(f"结果: {result}")
            except (ValueError, IndexError):
                print("用法: c <序号>，如 c 0")
        elif choice.startswith('p '):
            try:
                parts = choice.split()
                idx, row, col = int(parts[1]), int(parts[2]), int(parts[3])
                state = reader.read_state()
                result = executor.execute("place_plant",
                    {"card_index": idx, "row": row, "col": col}, state)
                print(f"结果: {result}")
            except (ValueError, IndexError):
                print("用法: p <序号> <行> <列>，如 p 0 2 3")
        elif choice.startswith('h '):
            try:
                parts = choice.split()
                row, col = int(parts[1]), int(parts[2])
                state = reader.read_state()
                result = executor.execute("shovel",
                    {"row": row, "col": col}, state)
                print(f"结果: {result}")
            except (ValueError, IndexError):
                print("用法: h <行> <列>，如 h 1 4")


def main():
    print("PvZ 内存读取 + 动作执行 测试")
    print("=" * 60)
    print()
    print("⚠️  请先确认：")
    print("  1. PvZ 游戏已启动")
    print("  2. 已进入战斗界面（选好卡，关卡已开始）")
    print("  3. 如果有杀毒软件拦截，请允许本脚本访问进程内存")
    print("  4. 以管理员权限运行（右键 → 以管理员身份运行）")
    print()
    input("按 Enter 开始测试...")

    # Step 1
    mem = step1_connect()
    if not mem:
        return

    # Step 2
    state = step2_read_state(mem)
    if not state:
        print("\n💡 提示: 请先进入任意关卡的战斗画面，然后重新运行此脚本。")
        return

    # Step 3
    step3_coordinates(mem, state)

    # Step 4 - 交互式
    try:
        step4_safe_action(mem, state)
    except KeyboardInterrupt:
        print("\n\n已退出")

    print("\n测试结束！")


if __name__ == "__main__":
    main()
