"""[feat] 硬暂停 - 通过挂起/恢复进程强制冻结游戏."""

from __future__ import annotations

import atexit
import sys
from ctypes import windll, wintypes

import pygetwindow as gw
from loguru import logger

from .base import PauseStrategy

if sys.platform == "win32":
    import win32gui
    import win32process

    # 加载 ntdll 中的 NtSuspendProcess / NtResumeProcess
    _ntdll = windll.ntdll
    _NtSuspendProcess = _ntdll.NtSuspendProcess
    _NtSuspendProcess.argtypes = (wintypes.HANDLE,)
    _NtSuspendProcess.restype = wintypes.LONG

    _NtResumeProcess = _ntdll.NtResumeProcess
    _NtResumeProcess.argtypes = (wintypes.HANDLE,)
    _NtResumeProcess.restype = wintypes.LONG

    import ctypes

    _OpenProcess = windll.kernel32.OpenProcess
    _OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _OpenProcess.restype = wintypes.HANDLE

    _CloseHandle = windll.kernel32.CloseHandle
    _CloseHandle.argtypes = (wintypes.HANDLE,)
    _CloseHandle.restype = wintypes.BOOL

    PROCESS_SUSPEND_RESUME = 0x0800


def _do_resume_process(pid: int) -> bool:
    """恢复指定 PID 的进程，成功返回 True."""
    h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h_process:
        logger.warning("[时停-硬暂停] 紧急恢复: OpenProcess 失败，PID: {}", pid)
        return False
    status = _NtResumeProcess(h_process)
    _CloseHandle(h_process)
    if status >= 0:
        logger.info("[时停-硬暂停] 紧急恢复: 进程已恢复，PID: {}", pid)
        return True
    logger.error("[时停-硬暂停] 紧急恢复: NtResumeProcess 失败，状态码: 0x{:08X}", status & 0xFFFFFFFF)
    return False


class HardPauseStrategy(PauseStrategy):
    """硬暂停策略（进程级）.

    通过 Windows NtSuspendProcess / NtResumeProcess 直接挂起/恢复目标进程的所有线程，
    无视游戏是否有暂停功能，强制冻结画面。

    内置崩溃保护：通过 atexit 注册紧急恢复函数，确保 Python 进程异常退出时
    也能尝试恢复被挂起的游戏进程，避免游戏永久冻结。

    注意：部分反作弊系统可能会检测到此行为。
    """

    def __init__(self) -> None:
        self._pid: int | None = None
        self._suspended = False
        atexit.register(self._emergency_resume)

    @property
    def name(self) -> str:
        return "hard"

    def pause(self, window: gw.Win32Window) -> None:
        """挂起目标窗口所属的进程."""
        if sys.platform != "win32":
            logger.warning("[时停-硬暂停] 仅支持 Windows 平台")
            return

        pid = self._get_pid(window)
        if pid is None:
            logger.error("[时停-硬暂停] 无法获取进程 PID")
            return

        h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h_process:
            logger.error("[时停-硬暂停] OpenProcess 失败，PID: {}", pid)
            return

        status = _NtSuspendProcess(h_process)
        _CloseHandle(h_process)

        if status >= 0:
            self._suspended = True
            logger.info("[时停-硬暂停] 进程已挂起，PID: {}", pid)
        else:
            logger.error("[时停-硬暂停] NtSuspendProcess 失败，状态码: 0x{:08X}", status & 0xFFFFFFFF)

    def resume(self, window: gw.Win32Window) -> None:
        """恢复目标窗口所属的进程."""
        if sys.platform != "win32":
            return

        pid = self._get_pid(window)
        if pid is None:
            return

        h_process = _OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
        if not h_process:
            logger.error("[时停-硬暂停] OpenProcess 失败，PID: {}", pid)
            return

        status = _NtResumeProcess(h_process)
        _CloseHandle(h_process)

        if status >= 0:
            self._suspended = False
            logger.info("[时停-硬暂停] 进程已恢复，PID: {}", pid)
        else:
            logger.error("[时停-硬暂停] NtResumeProcess 失败，状态码: 0x{:08X}", status & 0xFFFFFFFF)

    def _emergency_resume(self) -> None:
        """atexit 紧急恢复：Python 进程退出时确保游戏不被永久冻结."""
        if not self._suspended or self._pid is None:
            return
        logger.warning("[时停-硬暂停] 检测到进程退出但游戏仍处于挂起状态，执行紧急恢复，PID: {}", self._pid)
        _do_resume_process(self._pid)

    def _get_pid(self, window: gw.Win32Window) -> int | None:
        """通过窗口句柄获取进程 PID.

        每次都重新查询，避免游戏重启后 PID 变化导致恢复错误进程。
        """
        hwnd = window._hWnd
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            self._pid = pid
            return pid
        except Exception as exc:
            logger.error("[时停-硬暂停] 获取进程 PID 失败: {}", exc)
            return None
