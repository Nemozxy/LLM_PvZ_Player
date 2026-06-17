"""[feat] 窗口捕获模块 - mss 屏幕截图 + 自动切前台."""

from __future__ import annotations

import base64
import io
import sys
import time
from dataclasses import dataclass
from typing import Generator

import pygetwindow as gw
from loguru import logger
from mss import mss
from mss.models import Monitor
from PIL import Image

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32ui
    from ctypes import windll


@dataclass
class CaptureConfig:
    """截图配置."""

    # 图像缩放比例 (1.0 = 原始尺寸)
    scale: float = 1.0
    # 裁剪区域 (left, top, right, bottom)，None 表示不裁剪
    crop_box: tuple[int, int, int, int] | None = None
    # 输出格式: "PNG", "JPEG", "BMP"
    output_format: str = "PNG"
    # JPEG 质量 (1-95)
    jpeg_quality: int = 85
    # 目标捕获帧率 (FPS)，用于控制连续截图间隔
    target_fps: float = 2.0
    # 截图模式: "screen" 屏幕截图(mss) + 自动切前台; "background" 后台截图(PrintWindow)
    capture_mode: str = "screen"
    # 激活窗口后的等待时间（秒），给游戏重绘留出时间
    activate_delay: float = 0.15
    # 截图范围: "window" 完整窗口外框(含标题栏边框，可能有毛边); "client" 仅客户区(纯内容)
    capture_area: str = "client"
    # 截图前是否自动将窗口切到前台。
    # 软暂停策略下应设为 False，避免切前台触发游戏自动暂停导致截图包含暂停菜单。
    ensure_foreground: bool = True


class WindowCapture:
    """窗口截图器.

    默认使用 mss 屏幕截图，截图前自动将目标窗口切到前台（确保截到正确内容）。
    也可切换到 background 模式使用 Windows PrintWindow API 进行后台截图。
    """

    def __init__(self, config: CaptureConfig | None = None) -> None:
        self.config = config or CaptureConfig()
        self._sct = mss()
        self._window: gw.Win32Window | None = None
        self._window_title: str = ""

    # ------------------------------------------------------------------ #
    #  窗口发现
    # ------------------------------------------------------------------ #
    def find_window(self, title: str) -> gw.Win32Window:
        """根据标题（或部分标题）查找窗口.

        Args:
            title: 窗口完整标题或部分标题。若匹配到多个，返回第一个。

        Returns:
            pygetwindow.Win32Window 对象。

        Raises:
            RuntimeError: 未找到匹配窗口时抛出。
        """
        try:
            win = gw.getWindowsWithTitle(title)
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"查找窗口失败: {exc}") from exc

        if not win:
            raise RuntimeError(f"未找到标题包含 '{title}' 的窗口")

        self._window = win[0]
        self._window_title = self._window.title
        logger.info(
            "[窗口捕获] 已定位窗口: '{}' ({}x{})",
            self._window_title,
            self._window.width,
            self._window.height,
        )
        return self._window

    def list_windows(self) -> list[str]:
        """列出当前所有可见窗口标题（方便用户选择）.

        Returns:
            可见窗口标题列表，已去除空标题。
        """
        return [w.title for w in gw.getAllWindows() if w.title.strip()]

    # ------------------------------------------------------------------ #
    #  截图核心
    # ------------------------------------------------------------------ #
    def capture(self) -> Image.Image:
        """对当前已定位窗口执行一次截图.

        Returns:
            PIL Image 对象 (RGB 模式)。

        Raises:
            RuntimeError: 尚未定位窗口时抛出。
        """
        if self._window is None:
            raise RuntimeError("请先调用 find_window() 定位目标窗口")

        # 刷新窗口位置
        try:
            _ = self._window_box
        except Exception as exc:
            raise RuntimeError(f"获取窗口坐标失败，窗口可能已关闭: {exc}") from exc

        img: Image.Image | None = None

        if self.config.capture_mode == "background" and sys.platform == "win32":
            img = self._capture_background()
            if img is not None:
                logger.debug("[窗口捕获] 后台截图成功")

        if img is None:
            if self.config.capture_mode == "screen":
                if self.config.ensure_foreground:
                    self._ensure_foreground()
                    time.sleep(self.config.activate_delay)
            img = self._capture_screen()
            logger.debug("[窗口捕获] 屏幕截图完成")

        # 后处理（裁剪 & 缩放）
        if self.config.crop_box:
            img = img.crop(self.config.crop_box)

        if self.config.scale != 1.0:
            new_size = (
                int(img.width * self.config.scale),
                int(img.height * self.config.scale),
            )
            img = img.resize(new_size, Image.Resampling.LANCZOS)

        logger.debug(
            "[窗口捕获] 最终截图尺寸: {}x{}",
            img.width,
            img.height,
        )
        return img

    # ------------------------------------------------------------------ #
    #  前台激活 (Windows)
    # ------------------------------------------------------------------ #
    def _ensure_foreground(self) -> None:
        """将目标窗口强制切到前台.

        使用 AttachThreadInput + SetForegroundWindow，绕过 Windows 前台限制。
        如果窗口最小化，先恢复。
        """
        if self._window is None or sys.platform != "win32":
            return

        hwnd = self._window._hWnd

        # 最小化则恢复
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        # 绕过 Windows 前台限制：AttachThreadInput
        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

        try:
            if current_thread != target_thread:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                win32gui.SetForegroundWindow(hwnd)
                win32process.AttachThreadInput(current_thread, target_thread, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
        except Exception as exc:
            logger.warning("[窗口捕获] SetForegroundWindow 失败: {}", exc)
            # 备选：直接置顶
            try:
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOP,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE
                    | win32con.SWP_NOSIZE
                    | win32con.SWP_SHOWWINDOW,
                )
            except Exception as exc2:
                logger.warning("[窗口捕获] SetWindowPos 也失败: {}", exc2)

    # ------------------------------------------------------------------ #
    #  屏幕截图 (mss)
    # ------------------------------------------------------------------ #
    def _capture_screen(self) -> Image.Image:
        """使用 mss 抓取屏幕指定区域（要求窗口已在前台）."""
        box = self._window_box
        monitor: Monitor = {
            "left": box[0],
            "top": box[1],
            "width": box[2] - box[0],
            "height": box[3] - box[1],
        }
        raw = self._sct.grab(monitor)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    # ------------------------------------------------------------------ #
    #  后台截图 (Windows PrintWindow)
    # ------------------------------------------------------------------ #
    def _capture_background(self) -> Image.Image | None:
        """使用 Windows PrintWindow API 进行后台截图.

        直接从窗口 DC 读取像素，窗口被遮挡也能截到正确内容。
        部分硬件加速游戏可能黑屏，此时返回 None 由调用方回退。
        """
        if self._window is None:
            return None

        hwnd = self._window._hWnd
        width, height = self.window_size

        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)

        PW_RENDERFULLCONTENT = 3
        result = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)

        if result == 0:
            result = windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)

        if result == 0:
            self._cleanup_dc(hwnd, hwndDC, mfcDC, saveDC, saveBitMap)
            return None

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr, "raw", "BGRX", 0, 1,
        )

        self._cleanup_dc(hwnd, hwndDC, mfcDC, saveDC, saveBitMap)
        return img

    @staticmethod
    def _cleanup_dc(
        hwnd: int,
        hwndDC: int,
        mfcDC: win32ui.DC,
        saveDC: win32ui.DC,
        saveBitMap: win32ui.Bitmap,
    ) -> None:
        """释放 Win32 GDI 资源."""
        win32gui.DeleteObject(saveBitMap.GetHandle())
        saveDC.DeleteDC()
        mfcDC.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwndDC)

    # ------------------------------------------------------------------ #
    #  编码输出
    # ------------------------------------------------------------------ #
    def capture_to_bytes(self) -> bytes:
        """截图并编码为字节流."""
        img = self.capture()
        fmt = self.config.output_format.upper()
        buf = io.BytesIO()

        if fmt == "JPEG":
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format=fmt, quality=self.config.jpeg_quality)
        else:
            img.save(buf, format=fmt)

        return buf.getvalue()

    def capture_to_base64(self) -> str:
        """截图并编码为 Base64 字符串."""
        data = self.capture_to_bytes()
        return base64.b64encode(data).decode("ascii")

    # ------------------------------------------------------------------ #
    #  连续截图 (生成器)
    # ------------------------------------------------------------------ #
    def capture_continuous(self) -> Generator[Image.Image, None, None]:
        """按目标帧率持续截图."""
        interval = 1.0 / self.config.target_fps
        logger.info(
            "[窗口捕获] 开始连续截图，目标帧率: {:.1f} FPS",
            self.config.target_fps,
        )

        while True:
            t0 = time.perf_counter()
            try:
                yield self.capture()
            except RuntimeError as exc:
                logger.error("[窗口捕获] 截图失败: {}", exc)
                break

            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    # ------------------------------------------------------------------ #
    #  辅助属性
    # ------------------------------------------------------------------ #
    @property
    def _window_box(self) -> tuple[int, int, int, int]:
        """返回窗口当前 (left, top, right, bottom) 绝对坐标.

        根据 capture_area 配置决定截取范围：
        - "window": 完整外框（含标题栏、边框），可能有桌面毛边
        - "client": 仅客户区（纯内容区域），Windows 下通过 ClientToScreen 计算
        """
        if self._window is None:
            raise RuntimeError("窗口未初始化")

        if self.config.capture_area == "client" and sys.platform == "win32":
            hwnd = self._window._hWnd
            # 客户区左上角映射到屏幕坐标
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            # 客户区大小
            _, _, cw, ch = win32gui.GetClientRect(hwnd)
            return (cx, cy, cx + cw, cy + ch)

        # 默认/非 Windows: 使用外框
        return (
            self._window.left,
            self._window.top,
            self._window.right,
            self._window.bottom,
        )

    @property
    def window_size(self) -> tuple[int, int]:
        """返回与截图范围一致的尺寸 (width, height).

        根据 capture_area 配置返回对应区域的尺寸，
        确保与实际截图像素一一对应，避免坐标映射偏移。
        """
        if self._window is None:
            raise RuntimeError("窗口未初始化")
        if self.config.capture_area == "client" and sys.platform == "win32":
            box = self._window_box  # 已是客户区坐标
            return (box[2] - box[0], box[3] - box[1])
        return (self._window.width, self._window.height)

    def close(self) -> None:
        """释放 mss 资源."""
        self._sct.close()
        logger.info("[窗口捕获] 资源已释放")

    def __enter__(self) -> WindowCapture:
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        self.close()
