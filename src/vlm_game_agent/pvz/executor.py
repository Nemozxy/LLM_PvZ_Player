"""PvZ 专用动作执行器 — 高层游戏动作到精确鼠标点击的映射.

将 place_plant/shovel/collect_sun 等语义化动作，
利用内存读取的卡片坐标和格子→像素映射，转为精确的屏幕点击。

坐标体系:
- 卡片位置: 从内存读取 (SeedInfo.x/y/width/height)，即游戏内像素坐标
- 格子位置: 通过 row/col + 场景类型计算得到游戏内像素坐标
- 屏幕坐标: 游戏内坐标 → 窗口偏移 → 屏幕绝对坐标
"""

from __future__ import annotations

import time
from typing import Any, Callable

import pyautogui
from loguru import logger

from .memory import PvZMemory
from .offsets import SceneType
from .reader import GameState, SeedInfo


# ================================================================== #
#  格子 → 游戏内像素坐标映射
# ================================================================== #

# 游戏内网格原点和尺寸（来自 AsmVsZombies / pvztoolkit 逆向）
# 这些是 800x600 窗口下的值，其他分辨率按比例缩放

# 白天/黑夜场景 (草地)
GRID_LAWN_LEFT = 40       # 第0列左边界的 x 坐标
GRID_LAWN_TOP = 90        # 第0行上边界的 y 坐标
GRID_LAWN_CELL_W = 80     # 每格宽度
GRID_LAWN_CELL_H = 100    # 每格高度

# 泳池/雾夜场景
GRID_POOL_LEFT = 40
GRID_POOL_TOP = 90
GRID_POOL_CELL_W = 80
GRID_POOL_CELL_H = 100

# 天台/月夜场景
GRID_ROOF_LEFT = 40
GRID_ROOF_TOP = 90
GRID_ROOF_CELL_W = 80
GRID_ROOF_CELL_H = 100

# PvZ 窗口标准客户区尺寸
PVZ_STANDARD_WIDTH = 800
PVZ_STANDARD_HEIGHT = 600

# 铲子按钮在游戏内的大致位置 (800x600 基准)
SHOVEL_X = 640
SHOVEL_Y = 10


def grid_to_game_pixel(
    row: int,
    col: int,
    scene: int = 0,
) -> tuple[int, int]:
    """将 (行, 列) 转换为游戏内像素坐标 (格子中心).

    Args:
        row: 行号 0~5 (从上到下)
        col: 列号 0~8 (从左到右)
        scene: 场景类型 (0=白天 1=黑夜 2=泳池 3=雾夜 4=天台 5=月夜)

    Returns:
        (x, y) 游戏内像素坐标 (800x600 基准)
    """
    if scene in (SceneType.POOL, SceneType.FOG):
        left, top = GRID_POOL_LEFT, GRID_POOL_TOP
        cell_w, cell_h = GRID_POOL_CELL_W, GRID_POOL_CELL_H
    elif scene in (SceneType.ROOF, SceneType.MOON):
        left, top = GRID_ROOF_LEFT, GRID_ROOF_TOP
        cell_w, cell_h = GRID_ROOF_CELL_W, GRID_ROOF_CELL_H
    else:
        left, top = GRID_LAWN_LEFT, GRID_LAWN_TOP
        cell_w, cell_h = GRID_LAWN_CELL_W, GRID_LAWN_CELL_H

    x = left + col * cell_w + cell_w // 2
    y = top + row * cell_h + cell_h // 2
    return x, y


def game_pixel_to_screen(
    gx: int,
    gy: int,
    get_client_rect: Callable[[], tuple[int, int, int, int]],
) -> tuple[int, int]:
    """将游戏内像素坐标 (800x600 基准) 转换为屏幕绝对坐标.

    Args:
        gx: 游戏内 x 坐标
        gy: 游戏内 y 坐标
        get_client_rect: 返回窗口客户区屏幕坐标的回调

    Returns:
        (screen_x, screen_y) 屏幕绝对坐标
    """
    left, top, right, bottom = get_client_rect()
    cw = right - left
    ch = bottom - top

    # 按窗口实际大小缩放
    scale_x = cw / PVZ_STANDARD_WIDTH
    scale_y = ch / PVZ_STANDARD_HEIGHT

    screen_x = int(left + gx * scale_x)
    screen_y = int(top + gy * scale_y)
    return screen_x, screen_y


# ================================================================== #
#  PvZ 动作执行器
# ================================================================== #

class PvZExecutor:
    """PvZ 专用动作执行器.

    处理 place_plant / shovel / collect_sun 等高层动作，
    利用内存数据精确计算点击位置。

    通用动作 (left_click / wait / key 等) 仍由 ActionExecutor 处理。
    """

    def __init__(
        self,
        memory: PvZMemory,
        get_client_rect: Callable[[], tuple[int, int, int, int]],
    ) -> None:
        self._mem = memory
        self._get_rect = get_client_rect

    def can_execute(self, action: str) -> bool:
        """判断是否为 PvZ 专属动作."""
        return action in (
            "place_plant", "shovel", "collect_sun",
            "use_cob_cannon", "click_card",
        )

    def execute(self, action: str, args: dict[str, Any], state: GameState) -> dict[str, Any]:
        """执行 PvZ 专属动作.

        Args:
            action: 动作名称。
            args: 动作参数。
            state: 当前游戏状态（用于获取卡片坐标等）。

        Returns:
            执行结果字典。
        """
        logger.info("[PvZ执行] action={}, args={}", action, args)
        result: dict[str, Any] = {"action": action, "status": "ok"}

        try:
            if action == "place_plant":
                self._place_plant(args, state, result)
            elif action == "click_card":
                self._click_card(args, state, result)
            elif action == "shovel":
                self._shovel(args, state, result)
            elif action == "collect_sun":
                self._collect_sun(args, state, result)
            elif action == "use_cob_cannon":
                self._use_cob_cannon(args, state, result)
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
        """种植植物: 先点击卡片，再点击目标格子.

        参数:
            card_index: 卡片槽位号 (0-based, 与游戏状态中卡片编号一致)
            row: 目标行 (0-based)
            col: 目标列 (0-based)
        """
        card_index = args.get("card_index")
        row = args.get("row")
        col = args.get("col")

        if card_index is None or row is None or col is None:
            raise ValueError("place_plant 需要 card_index, row, col 参数")

        # 验证卡片
        if card_index < 0 or card_index >= len(state.seeds):
            raise ValueError(f"无效卡片序号: {card_index}，共 {len(state.seeds)} 张卡")

        seed = state.seeds[card_index]
        if not seed.is_ready:
            raise ValueError(f"卡片 [{card_index}] {seed.name} 未就绪 (冷却中或不可用)")

        # 1. 点击卡片
        self._click_seed_card(seed)
        time.sleep(0.1)

        # 2. 点击目标格子
        gx, gy = grid_to_game_pixel(row, col, state.scene)
        sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
        pyautogui.click(sx, sy)

        result["detail"] = f"种植 {seed.name} 到 行{row}列{col}"
        result["card_index"] = card_index
        result["grid"] = (row, col)

    def _click_card(self, args: dict, state: GameState, result: dict) -> None:
        """点击卡片 (选中但暂不放置).

        参数:
            card_index: 卡片槽位号 (0-based)
        """
        card_index = args.get("card_index")
        if card_index is None:
            raise ValueError("click_card 需要 card_index 参数")

        if card_index < 0 or card_index >= len(state.seeds):
            raise ValueError(f"无效卡片序号: {card_index}")

        seed = state.seeds[card_index]
        self._click_seed_card(seed)

        result["detail"] = f"选中卡片 [{card_index}] {seed.name}"

    def _shovel(self, args: dict, state: GameState, result: dict) -> None:
        """铲除植物: 先点击铲子按钮，再点击目标格子.

        参数:
            row: 目标行 (0-based)
            col: 目标列 (0-based)
        """
        row = args.get("row")
        col = args.get("col")
        if row is None or col is None:
            raise ValueError("shovel 需要 row, col 参数")

        # 1. 点击铲子按钮
        sx, sy = game_pixel_to_screen(SHOVEL_X, SHOVEL_Y, self._get_rect)
        pyautogui.click(sx, sy)
        time.sleep(0.1)

        # 2. 点击目标格子
        gx, gy = grid_to_game_pixel(row, col, state.scene)
        sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
        pyautogui.click(sx, sy)

        result["detail"] = f"铲除 行{row}列{col} 的植物"
        result["grid"] = (row, col)

    def _collect_sun(self, args: dict, state: GameState, result: dict) -> None:
        """收集阳光: 点击场上的阳光.

        参数:
            index: 阳光在收集物列表中的序号 (0-based)
              或 "all" 收集所有阳光
        """
        index = args.get("index", 0)

        if index == "all":
            # 收集所有阳光
            sun_items = [it for it in state.items if it.is_sun and not it.is_collected]
            count = 0
            for it in sun_items[:5]:  # 最多一次收 5 个，避免耗时
                gx, gy = int(it.x), int(it.y)
                sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
                pyautogui.click(sx, sy)
                count += 1
                time.sleep(0.05)
            result["detail"] = f"收集了 {count} 个阳光"
        else:
            sun_items = [it for it in state.items if it.is_sun and not it.is_collected]
            if not sun_items:
                raise ValueError("当前没有可收集的阳光")
            idx = min(int(index), len(sun_items) - 1)
            it = sun_items[idx]
            gx, gy = int(it.x), int(it.y)
            sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
            pyautogui.click(sx, sy)
            result["detail"] = f"收集阳光 (位置: {gx},{gy})"

    def _use_cob_cannon(self, args: dict, state: GameState, result: dict) -> None:
        """使用玉米加农炮: 先点击炮台，再点击落点.

        参数:
            row: 炮台所在行 (0-based)
            col: 炮台所在列 (0-based)
            target_row: 落点行 (0-based)
            target_col: 落点列 (0-based)
        """
        row = args.get("row")
        col = args.get("col")
        target_row = args.get("target_row")
        target_col = args.get("target_col")

        if any(v is None for v in (row, col, target_row, target_col)):
            raise ValueError("use_cob_cannon 需要 row, col, target_row, target_col 参数")

        # 1. 点击炮台
        gx, gy = grid_to_game_pixel(row, col, state.scene)
        sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
        pyautogui.click(sx, sy)
        time.sleep(0.15)

        # 2. 点击落点
        gx, gy = grid_to_game_pixel(target_row, target_col, state.scene)
        sx, sy = game_pixel_to_screen(gx, gy, self._get_rect)
        pyautogui.click(sx, sy)

        result["detail"] = f"玉米炮 ({row},{col}) → ({target_row},{target_col})"

    # ------------------------------------------------------------------ #
    #  内部工具
    # ------------------------------------------------------------------ #

    def _click_seed_card(self, seed: SeedInfo) -> None:
        """点击一张种子卡片.

        利用内存中读取的卡片 x/y/width/height 计算精确点击位置。
        """
        if seed.x > 0 and seed.y > 0 and seed.width > 0 and seed.height > 0:
            # 使用内存中的精确坐标
            cx = seed.x + seed.width // 2
            cy = seed.y + seed.height // 2
        else:
            # 兜底：按卡片序号估算位置
            # 卡片区起始 x ≈ 80，每张卡宽 ≈ 50，间距 ≈ 1
            cx = 80 + seed.index * 51 + 25
            cy = 10

        sx, sy = game_pixel_to_screen(cx, cy, self._get_rect)
        pyautogui.click(sx, sy)
