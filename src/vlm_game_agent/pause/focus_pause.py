"""[feat] 失焦暂停 - 通过切出焦点让游戏自动暂停."""

from __future__ import annotations

import time

import pygetwindow as gw
from loguru import logger

from .base import PauseStrategy

if __import__("sys").platform == "win32":
    import win32gui
    import win32con


class FocusPauseStrategy(PauseStrategy):
    """失焦暂停策略.

    利用多数游戏在失去焦点时自动暂停的机制：
    - 暂停时：将系统焦点切出游戏窗口（如切到桌面或某个占位窗口）
    - 恢复时：将焦点切回游戏窗口
    """

    def __init__(self, delay_after_focus_out: float = 0.2, delay_after_focus_in: float = 0.2) -> None:
        """初始化失焦暂停策略.

        Args:
            delay_after_focus_out: 切出焦点后的等待时间（秒），等游戏完成暂停。
            delay_after_focus_in: 切回焦点后的等待时间（秒），等游戏恢复运行。
        """
        self.delay_after_focus_out = delay_after_focus_out
        self.delay_after_focus_in = delay_after_focus_in
        self._previous_hwnd: int | None = None

    @property
    def name(self) -> str:
        return "focus"

    def pause(self, window: gw.Win32Window) -> None:
        """将焦点从游戏窗口移出，触发自动暂停."""
        if __import__("sys").platform != "win32":
            logger.warning("[时停-失焦暂停] 非 Windows 平台，失焦暂停无效")
            return

        hwnd = window._hWnd
        self._previous_hwnd = win32gui.GetForegroundWindow()

        logger.info("[时停-失焦暂停] 切出焦点，当前前台窗口: 0x{:X}", self._previous_hwnd)

        # 将焦点切到桌面窗口（Progman），确保游戏失去焦点
        desktop = win32gui.GetDesktopWindow()
        self._set_foreground_safe(desktop)

        time.sleep(self.delay_after_focus_out)

    def resume(self, window: gw.Win32Window) -> None:
        """将焦点切回游戏窗口."""
        if __import__("sys").platform != "win32":
            return

        hwnd = window._hWnd
        logger.info("[时停-失焦暂停] 切回游戏窗口: '{}'", window.title)

        # 恢复窗口（如果被最小化）
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        self._set_foreground_safe(hwnd)
        time.sleep(self.delay_after_focus_in)

    def _set_foreground_safe(self, hwnd: int) -> None:
        """安全地设置前台窗口，绕过 Windows 限制."""
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # 备选：使用 SetWindowPos 将窗口提到最前
            try:
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOP,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                )
            except Exception as exc:
                logger.warning("[时停-失焦暂停] 切换焦点失败: {}", exc)
