"""[feat] 时停控制器 - 统一封装暂停/恢复逻辑."""

from __future__ import annotations

import pygetwindow as gw
from loguru import logger

from .base import PauseStrategy
from .focus_pause import FocusPauseStrategy
from .hard_pause import HardPauseStrategy
from .soft_pause import SoftPauseStrategy


class PauseController:
    """时停控制器.

    负责协调"冻结 → 思考 → 执行 → 恢复"的原子性时停流程。
    支持策略热切换，可根据游戏特性选择最合适的暂停方式。
    """

    def __init__(self, strategy: PauseStrategy | None = None) -> None:
        """初始化时停控制器.

        Args:
            strategy: 初始暂停策略，默认使用 SoftPauseStrategy。
        """
        self._strategy = strategy or SoftPauseStrategy()
        self._window: gw.Win32Window | None = None
        self._paused = False

    # ------------------------------------------------------------------ #
    #  策略管理
    # ------------------------------------------------------------------ #
    def set_strategy(self, strategy: PauseStrategy) -> None:
        """切换暂停策略.

        Args:
            strategy: 新的暂停策略实例。
        """
        logger.info("[时停控制] 切换策略: {} -> {}", self._strategy.name, strategy.name)
        self._strategy = strategy

    def set_soft(self, pause_key: str = "esc", resume_key: str | None = None) -> None:
        """快捷切换到软暂停策略."""
        self.set_strategy(SoftPauseStrategy(pause_key=pause_key, resume_key=resume_key))

    def set_focus(self) -> None:
        """快捷切换到失焦暂停策略."""
        self.set_strategy(FocusPauseStrategy())

    def set_hard(self) -> None:
        """快捷切换到硬暂停策略."""
        self.set_strategy(HardPauseStrategy())

    # ------------------------------------------------------------------ #
    #  窗口绑定
    # ------------------------------------------------------------------ #
    def bind_window(self, window: gw.Win32Window) -> None:
        """绑定目标窗口.

        Args:
            window: 要控制的窗口对象。
        """
        self._window = window
        logger.info("[时停控制] 已绑定窗口: '{}'", window.title)

    # ------------------------------------------------------------------ #
    #  核心控制
    # ------------------------------------------------------------------ #
    def pause(self) -> None:
        """暂停游戏.

        Raises:
            RuntimeError: 未绑定窗口时抛出。
        """
        if self._window is None:
            raise RuntimeError("未绑定目标窗口，请先调用 bind_window()")
        if self._paused:
            logger.warning("[时停控制] 当前已处于暂停状态，忽略重复 pause()")
            return

        logger.info("[时停控制] 执行暂停 (策略: {})", self._strategy.name)
        self._strategy.pause(self._window)
        self._paused = True

    def resume(self) -> None:
        """恢复游戏.

        Raises:
            RuntimeError: 未绑定窗口时抛出。
        """
        if self._window is None:
            raise RuntimeError("未绑定目标窗口，请先调用 bind_window()")
        if not self._paused:
            logger.warning("[时停控制] 当前未暂停，忽略重复 resume()")
            return

        logger.info("[时停控制] 执行恢复 (策略: {})", self._strategy.name)
        self._strategy.resume(self._window)
        self._paused = False

    def is_paused(self) -> bool:
        """返回当前是否处于暂停状态."""
        return self._paused

    # ------------------------------------------------------------------ #
    #  上下文管理器 (with 语句)
    # ------------------------------------------------------------------ #
    def __enter__(self) -> PauseController:
        self.pause()
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        self.resume()
