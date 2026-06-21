"""PvZ 专用动作执行器 — 高层游戏动作到注入/鼠标的映射.

执行策略:
- 代码注入 (PvZCodeInjector): 直接调用游戏内部函数，100% 可靠
- 鼠标操作 (fallback): 仅在注入器不可用时使用，不可靠

坐标体系:
- 注入模式: 传入 row/col 给游戏函数，游戏内部处理坐标
- 鼠标模式: 通过 row/col + 场景类型 → 游戏像素坐标 → 屏幕坐标
"""

from __future__ import annotations

import ctypes
import time
from typing import Any, Callable

import pyautogui
from loguru import logger

from .injector import PvZCodeInjector
from .memory import PvZMemory
from .offsets import PLANT_NAMES, PLANT_UPGRADE_MAP
from .reader import GameState, SeedInfo

# Windows API (鼠标 fallback 用)
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004


def _win_click(x: int, y: int, move_duration: float = 0.3) -> None:
    """用 Windows API 移动鼠标并点击 (鼠标 fallback 用)."""
    from_x, from_y = pyautogui.position()
    steps = max(1, int(move_duration / 0.02))
    for i in range(1, steps + 1):
        t = i / steps
        cur_x = int(from_x + (x - from_x) * t)
        cur_y = int(from_y + (y - from_y) * t)
        ctypes.windll.user32.SetCursorPos(cur_x, cur_y)
        time.sleep(0.02)
    time.sleep(0.03)
    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


# ================================================================== #
#  格子 → 游戏内像素坐标 (鼠标 fallback 用，注入模式不需要)
# ================================================================== #

COL_WIDTH = 80
PVZ_STANDARD_WIDTH = 800
PVZ_STANDARD_HEIGHT = 600
SHOVEL_X = 755   # 铲子按钮中心 x (兜底值，教学关无卡片时使用)
SHOVEL_Y = 50    # 工具栏垂直居中 y


def _grid_y_by_scene(row: int, scene: int) -> int:
    """根据场景类型近似计算格子中心的 y 坐标 (800x600 基准)."""
    if scene in (2, 3):
        return 50 + row * 85 + 42
    elif scene in (4, 5):
        return 40 + row * 85 + 42
    else:
        return 50 + row * 100 + 40


def grid_to_game_pixel(row: int, col: int, scene: int = 0) -> tuple[int, int]:
    """将 (行, 列) 转换为游戏内像素坐标 (鼠标 fallback 用).

    注入模式下应使用 injector.grid_to_pixel() 调用游戏内部函数获取精确坐标。
    """
    x = (col + 1) * COL_WIDTH
    y = _grid_y_by_scene(row, scene)
    x = max(0, min(799, x))
    y = max(0, min(599, y))
    return x, y


def game_pixel_to_screen(
    gx: int,
    gy: int,
    get_client_rect: Callable[[], tuple[int, int, int, int]],
) -> tuple[int, int]:
    """将游戏内像素坐标 (800x600 基准) 转换为屏幕绝对坐标 (鼠标 fallback 用)."""
    left, top, right, bottom = get_client_rect()
    cw = right - left
    ch = bottom - top
    scale_x = cw / PVZ_STANDARD_WIDTH
    scale_y = ch / PVZ_STANDARD_HEIGHT
    return int(left + gx * scale_x), int(top + gy * scale_y)


# ================================================================== #
#  PvZ 动作执行器
# ================================================================== #

class PvZExecutor:
    """PvZ 专用动作执行器.

    优先使用代码注入，鼠标操作仅作为不可靠的后备。
    """

    def __init__(
        self,
        memory: PvZMemory,
        get_client_rect: Callable[[], tuple[int, int, int, int]],
    ) -> None:
        self._mem = memory
        self._get_rect = get_client_rect

        # 初始化代码注入器
        self._injector: PvZCodeInjector | None = None
        if memory.is_connected():
            try:
                self._injector = PvZCodeInjector(memory)
                logger.info("[PvZ执行] 代码注入器已启用")
            except Exception as exc:
                logger.warning("[PvZ执行] 代码注入器初始化失败: {}，退回鼠标模式", exc)

    def close(self) -> None:
        """释放资源."""
        if self._injector:
            self._injector.close()
            self._injector = None

    @property
    def injector(self) -> PvZCodeInjector | None:
        """获取注入器实例（用于外部调用 hack 开关等）."""
        return self._injector

    def pause_for_thinking(self) -> None:
        """冻结 PvZ 主循环，用于 VLM 推理期间暂停游戏。"""
        if not self._injector:
            raise RuntimeError("PvZ 注入器未启用，无法冻结主循环")
        self._injector.set_main_loop_blocked(True)

    def resume_after_thinking(self) -> None:
        """恢复 PvZ 主循环，对应 pause_for_thinking。"""
        if not self._injector:
            raise RuntimeError("PvZ 注入器未启用，无法恢复主循环")
        self._injector.set_main_loop_blocked(False)

    def can_execute(self, action: str) -> bool:
        """判断是否为 PvZ 专属动作."""
        return action in (
            "place_plant", "shovel",
            "use_cob_cannon", "click_card", "win_level", "select_seeds",
        )

    def execute(self, action: str, args: dict[str, Any], state: GameState) -> dict[str, Any]:
        """执行 PvZ 专属动作."""
        logger.info("[PvZ执行] action={}, args={}", action, args)
        result: dict[str, Any] = {"action": action, "status": "ok"}

        try:
            if action == "place_plant":
                self._place_plant(args, state, result)
            elif action == "click_card":
                self._click_card(args, state, result)
            elif action == "shovel":
                self._shovel(args, state, result)
            elif action == "use_cob_cannon":
                self._use_cob_cannon(args, state, result)
            elif action == "win_level":
                self._win_level(args, state, result)
            elif action == "select_seeds":
                self._select_seeds(args, state, result)
            else:
                raise ValueError(f"未知 PvZ 动作: {action}")
        except Exception as exc:
            logger.error("[PvZ执行] 动作失败: {}", exc)
            result["status"] = "error"
            result["error"] = str(exc)

        return result

    # ------------------------------------------------------------------ #
    #  具体动作实现
    # ------------------------------------------------------------------ #

    def _place_plant(self, args: dict, state: GameState, result: dict) -> None:
        """种植植物 — 点卡片选中 + 点格子放置.

        注入模式: MouseClick 点击卡片中心 + MouseClick 点击格子中心
        鼠标模式: Windows API 点击屏幕坐标

        走 MouseClick 路线而非直接调 PutPlant，因为 PutPlant 绕过 UI 逻辑
        （不扣阳光、不重置冷却、不检测占用），会产生大量副作用需要手动修补。
        MouseClick 让游戏自己处理全部 UI 逻辑，零副作用。
        """
        card_index = args.get("card_index")
        row = args.get("row")
        col = args.get("col")

        if card_index is None or row is None or col is None:
            raise ValueError("place_plant 需要 card_index, row, col 参数")

        if card_index < 0 or card_index >= len(state.seeds):
            raise ValueError(f"无效卡片序号: {card_index}，共 {len(state.seeds)} 张卡")

        seed = state.seeds[card_index]
        if not seed.is_ready:
            raise ValueError(f"卡片 [{card_index}] {seed.name} 未就绪 (冷却中或不可用)")
        if state.sun < seed.sun_cost:
            raise ValueError(f"卡片 [{card_index}] {seed.name} 需要 {seed.sun_cost} 阳光，当前只有 {state.sun}")

        # 升级植物检查：必须点在已有基础植物上
        base_type = PLANT_UPGRADE_MAP.get(seed.plant_type)
        if base_type is not None:
            base_name = PLANT_NAMES.get(base_type, f"类型{base_type}")
            target_plant = None
            for p in state.plants:
                if p.row == row and p.col == col:
                    target_plant = p
                    break
            if target_plant is None:
                raise ValueError(
                    f"升级植物 {seed.name} 必须种在已有 {base_name} 上，"
                    f"行{row}列{col} 为空地"
                )
            if target_plant.plant_type != base_type:
                raise ValueError(
                    f"升级植物 {seed.name} 必须种在 {base_name} 上，"
                    f"行{row}列{col} 是 {target_plant.name}，不是 {base_name}"
                )
        else:
            # 普通植物：目标格子必须空闲，不能叠种
            for p in state.plants:
                if p.row == row and p.col == col:
                    raise ValueError(
                        f"行{row}列{col} 已有 {p.name}，不能叠种 {seed.name}"
                    )

        if self._injector:
            # 注入模式: MouseClick 点卡片 + 点格子
            # 1. 点卡片中心
            if seed.x > 0 and seed.y > 0:
                card_cx = seed.x + seed.width // 2
                card_cy = seed.y + seed.height // 2
            else:
                card_cx = 80 + seed.index * 51 + 25
                card_cy = 10
            logger.info("[PvZ执行] 💉 点击卡片 [{}] ({},{})", seed.index, card_cx, card_cy)
            self._injector.mouse_click(card_cx, card_cy)
            time.sleep(0.1)

            # 2. 点格子中心 (精确坐标)
            gx, gy = self._injector.grid_to_pixel(row, col)
            logger.info("[PvZ执行] 💉 点击格子 ({},{}) → ({},{})", row, col, gx, gy)
            self._injector.mouse_click(gx, gy)
        else:
            self._place_plant_mouse(seed, row, col, state)

        result["detail"] = f"种植 {seed.name} 到 行{row}列{col}"
        result["card_index"] = card_index
        result["grid"] = (row, col)

    def _place_plant_mouse(self, seed: SeedInfo, row: int, col: int, state: GameState) -> None:
        """鼠标模式种植（不可靠的后备）."""
        cx, cy = self._seed_center(seed)
        logger.info("[PvZ执行] 🖱 点击卡片 [{}] 屏幕({},{})", seed.index, cx, cy)
        _win_click(cx, cy)
        time.sleep(0.3)

        gx, gy = grid_to_game_pixel(row, col, state.scene)
        sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
        logger.info("[PvZ执行] 🖱 点击格子 ({},{}) 屏幕({},{})", row, col, sx, sy)
        _win_click(sx, sy, move_duration=0.4)
        time.sleep(0.2)

    def _click_card(self, args: dict, state: GameState, result: dict) -> None:
        """点击卡片 (选中但暂不放置)."""
        card_index = args.get("card_index")
        if card_index is None:
            raise ValueError("click_card 需要 card_index 参数")

        if card_index < 0 or card_index >= len(state.seeds):
            raise ValueError(f"无效卡片序号: {card_index}")

        seed = state.seeds[card_index]

        if self._injector:
            # 注入模式: 用 MouseClick 点击卡片中心坐标
            if seed.x > 0 and seed.y > 0:
                cx = seed.x + seed.width // 2
                cy = seed.y + seed.height // 2
                self._injector.mouse_click(cx, cy)
            else:
                # 兜底估算
                cx = 80 + seed.index * 51 + 25
                cy = 10
                self._injector.mouse_click(cx, cy)
        else:
            sx, sy = self._seed_center(seed)
            _win_click(sx, sy)

        result["detail"] = f"选中卡片 [{card_index}] {seed.name}"

    def _shovel(self, args: dict, state: GameState, result: dict) -> None:
        """铲除植物 — MouseClick 点铲子按钮 + 点目标格子.

        走 MouseClick 路线让游戏自己处理铲子的 UI 逻辑（选中/取消/教程等），
        跟种植物一样避免绕过 UI 产生副作用。
        """
        row = args.get("row")
        col = args.get("col")
        if row is None or col is None:
            raise ValueError("shovel 需要 row, col 参数")

        if self._injector:
            # 1. 先释放当前鼠标选中状态
            self._injector.release_mouse()
            time.sleep(0.05)

            # 2. 点击铲子按钮
            shovel_x, shovel_y = self._get_shovel_button_pos(state)
            using_fallback = not state.seeds or not state.seeds[-1].x > 0
            logger.info("[PvZ执行] 💉 点击铲子 ({},{}), fallback={}", shovel_x, shovel_y, using_fallback)
            self._injector.mouse_click(shovel_x, shovel_y)
            time.sleep(0.3)  # 等待游戏进入铲子模式

            # 3. 点击目标格子
            gx, gy = self._injector.grid_to_pixel(row, col)
            logger.info("[PvZ执行] 💉 点击格子 ({},{}) → ({},{})", row, col, gx, gy)
            self._injector.mouse_click(gx, gy)
            time.sleep(0.1)
        else:
            sx, sy = game_pixel_to_screen(SHOVEL_X, SHOVEL_Y, self._get_rect)
            _win_click(sx, sy)
            time.sleep(0.25)
            gx, gy = grid_to_game_pixel(row, col, state.scene)
            sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
            _win_click(sx, sy, move_duration=0.4)

        result["detail"] = f"铲除 行{row}列{col} 的植物"
        result["grid"] = (row, col)

    def _use_cob_cannon(self, args: dict, state: GameState, result: dict) -> None:
        """使用玉米加农炮: 先点击炮台，再点击落点."""
        row = args.get("row")
        col = args.get("col")
        target_row = args.get("target_row")
        target_col = args.get("target_col")

        if any(v is None for v in (row, col, target_row, target_col)):
            raise ValueError("use_cob_cannon 需要 row, col, target_row, target_col 参数")

        if self._injector:
            # 注入模式: 精确坐标点击
            gx1, gy1 = self._injector.grid_to_pixel(row, col)
            self._injector.mouse_click(gx1, gy1)
            time.sleep(0.1)
            gx2, gy2 = self._injector.grid_to_pixel(target_row, target_col)
            self._injector.mouse_click(gx2, gy2)
        else:
            gx, gy = grid_to_game_pixel(row, col, state.scene)
            sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
            _win_click(sx, sy)
            time.sleep(0.15)
            gx, gy = grid_to_game_pixel(target_row, target_col, state.scene)
            sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
            _win_click(sx, sy)

        result["detail"] = f"玉米炮 ({row},{col}) → ({target_row},{target_col})"

    def _win_level(self, args: dict, state: GameState, result: dict) -> None:
        """直接通关 — 跳过当前关卡.

        用于跳过 AI 难以胜任的实时小游戏（坚果保龄球、传送带关卡等）。
        调用游戏内部 FadeOutLevel 触发通关。纯注入功能，无鼠标 fallback。
        """
        if self._injector:
            self._injector.win_level()
        else:
            raise RuntimeError("直接通关需要代码注入器，当前未启用")

        result["detail"] = "已触发直接通关"

    def _select_seeds(self, args: dict, state: GameState, result: dict) -> None:
        """选卡界面: 选择植物并开始游戏.

        传入植物类型列表，程序用游戏内部函数逐张选卡，再随机填满剩余
        卡槽，最后开始游戏。完全绕过视觉坐标点击，避免选卡界面点不准。

        Args (从 args 读):
            seeds: 植物类型列表 (0~47)，如 [0, 1, 3, 5]。
        """
        seeds = args.get("seeds")
        if not seeds or not isinstance(seeds, list):
            raise ValueError("select_seeds 需要 seeds 参数（植物类型列表，如 [0,1,3]）")

        if not self._injector:
            raise RuntimeError("选卡需要代码注入器，当前未启用")

        # 等待选卡 UI 完全渲染。
        # 关卡结束→选卡界面过渡期间，game_ui 已变为 SELECT_CARD 但 SelectCardUi_p
        # 仍为 0，此时执行选卡会导致下一波 UI 异常（卡片栏/阳光/铲子不显示）。
        # 必须等 SelectCardUi_p 非零，表示 UI 对象已创建，选卡操作才安全。
        mem = self._injector._mem
        for _ in range(50):  # 最多等 5 秒
            ptr = mem.get_select_card_ui_ptr()
            if ptr:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("等待选卡 UI 就绪超时 (5s)，SelectCardUi_p 始终为 0")

        # 逐张选卡。任何一张失败都应中止，避免带着残缺/错误的卡组继续
        # pick_random_seeds → rock，那样会用错误的植物开局却误以为成功了。
        chosen: list[int] = []
        for plant_type in seeds:
            try:
                self._injector.choose_card(int(plant_type))
                chosen.append(int(plant_type))
            except Exception as exc:
                logger.error("[PvZ执行] 选卡 {} 失败，中止选卡: {}", plant_type, exc)
                raise RuntimeError(f"选卡失败 (植物 {plant_type}): {exc}") from exc

        # 随机填满剩余卡槽
        self._injector.pick_random_seeds()

        # 开始游戏
        self._injector.rock()

        result["detail"] = f"已选卡 {len(chosen)} 张 {chosen} 并开始游戏"
        result["seeds"] = chosen

    # ------------------------------------------------------------------ #
    #  内部工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_shovel_button_pos(state: GameState) -> tuple[int, int]:
        """根据卡片位置推算铲子按钮中心坐标 (800x600 基准).

        PvZ 工具栏布局: 阳光计数器 → 种子卡片 → 铲子按钮。
        铲子按钮紧跟最后一张卡片右侧，y 与卡片对齐。

        无卡片时返回兜底值 (按10张卡片估算)。对于教学关等特殊布局，
        VLM 应直接用 computer_use 点击截图中的铲子按钮。
        """
        if state.seeds:
            last = state.seeds[-1]
            if last.x > 0 and last.width > 0:
                # 铲子按钮紧跟最后一张卡片右侧
                shovel_x = last.x + last.width + 10 + 24  # 右边缘 + 间距 + 半宽
                shovel_y = last.y + last.height // 2      # 垂直居中
                return shovel_x, shovel_y

        # 兜底: 按10张卡片推算的位置
        return SHOVEL_X, SHOVEL_Y

    def _seed_center(self, seed: SeedInfo) -> tuple[int, int]:
        """计算卡片中心屏幕坐标 (鼠标 fallback 用)."""
        if seed.x > 0 and seed.y > 0 and seed.width > 0 and seed.height > 0:
            cx = seed.x + seed.width // 2
            cy = seed.y + seed.height // 2
        else:
            cx = 80 + seed.index * 51 + 25
            cy = 10
        return game_pixel_to_screen(cx, cy, self._get_rect)
