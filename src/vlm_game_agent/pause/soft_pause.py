"""[feat] 软暂停 - 通过发送全局快捷键冻结/恢复游戏."""

from __future__ import annotations

import sys
import time

import pygetwindow as gw
from loguru import logger
from pynput.keyboard import Controller, Key

from .base import PauseStrategy

if sys.platform == "win32":
    import win32gui
    import win32con
    import win32api
    import win32process


class SoftPauseStrategy(PauseStrategy):
    """软暂停策略.

    利用游戏内置的暂停快捷键（如 Esc、Space、P 等）冻结画面。
    发送按键前会确保游戏窗口拥有焦点，避免按键被其他窗口接收。
    """

    def __init__(
        self,
        pause_key: str = "esc",
        resume_key: str | None = None,
        delay_after_pause: float = 0.1,
        delay_after_resume: float = 0.1,
    ) -> None:
        """初始化软暂停策略.

        Args:
            pause_key: 暂停快捷键名称，如 'esc', 'space', 'p'。
            resume_key: 恢复快捷键名称，默认与 pause_key 相同。
            delay_after_pause: 发送暂停键后的等待时间（秒），等游戏冻结稳定。
            delay_after_resume: 发送恢复键后的等待时间（秒），等游戏恢复稳定。
        """
        self.pause_key = pause_key.lower()
        self.resume_key = (resume_key or pause_key).lower()
        self.delay_after_pause = delay_after_pause
        self.delay_after_resume = delay_after_resume
        self._kbd = Controller()

    @property
    def name(self) -> str:
        return "soft"

    def pause(self, window: gw.Win32Window) -> None:
        """发送暂停快捷键，确保游戏窗口在前台."""
        self._ensure_foreground(window)
        logger.info("[时停-软暂停] 发送暂停键: {}", self.pause_key)
        self._press_key(self.pause_key)
        time.sleep(self.delay_after_pause)

    def resume(self, window: gw.Win32Window) -> None:
        """发送恢复快捷键，确保游戏窗口在前台."""
        self._ensure_foreground(window)
        logger.info("[时停-软暂停] 发送恢复键: {}", self.resume_key)
        self._press_key(self.resume_key)
        time.sleep(self.delay_after_resume)

    @staticmethod
    def _ensure_foreground(window: gw.Win32Window) -> None:
        """确保目标窗口是前台窗口，避免按键发到错误的应用."""
        if sys.platform != "win32":
            return
        hwnd = window._hWnd
        if win32gui.GetForegroundWindow() == hwnd:
            return
        try:
            # 恢复最小化窗口
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # AttachThreadInput 绕过前台限制
            current_thread = win32api.GetCurrentThreadId()
            target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
            if current_thread != target_thread:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                win32gui.SetForegroundWindow(hwnd)
                win32process.AttachThreadInput(current_thread, target_thread, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.05)
        except Exception as exc:
            logger.warning("[时停-软暂停] 切换前台窗口失败: {}", exc)

    def _press_key(self, key_name: str) -> None:
        """通过 pynput 按下并释放指定按键."""
        key = self._parse_key(key_name)
        self._kbd.press(key)
        self._kbd.release(key)

    @staticmethod
    def _parse_key(name: str):
        """将字符串按键名解析为 pynput Key 对象."""
        mapping = {
            "esc": Key.esc,
            "space": Key.space,
            "enter": Key.enter,
            "tab": Key.tab,
            "shift": Key.shift,
            "ctrl": Key.ctrl,
            "alt": Key.alt,
            "pause": Key.pause,
            "p": "p",
        }
        return mapping.get(name.lower(), name)
