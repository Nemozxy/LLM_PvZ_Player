"""PvZ 进程内存读写 — 基于 ctypes 的 ReadProcessMemory 封装.

参考: pvztoolkit process.h 的多级指针链读取模式.

核心能力:
- 通过窗口类名/标题找到 PvZ 进程
- 版本自动检测（PE 时间戳）
- 多级指针链读取: [[base] + offset1] + offset2] → value
- 单值/数组/字符串读取
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
from typing import Any, TypeVar

from loguru import logger

from .offsets import (
    PVZ_BASE_ADDRESS,
    PVZ_WINDOW_CLASS,
    PVZ_WINDOW_TITLES,
    PvZVersion,
    detect_version_from_timestamp,
    get_offsets,
    PvZOffsets,
)

# ================================================================== #
#  Windows API 声明
# ================================================================== #

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32

# FindWindowW(hwnd, lpWindowName)
_FindWindowW = user32.FindWindowW
_FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_FindWindowW.restype = wintypes.HWND

# GetWindowThreadProcessId(hwnd, lpdwProcessId)
_GetWindowThreadProcessId = user32.GetWindowThreadProcessId
_GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_GetWindowThreadProcessId.restype = wintypes.DWORD

# OpenProcess(dwDesiredAccess, bInheritHandle, dwProcessId)
_OpenProcess = kernel32.OpenProcess
_OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_OpenProcess.restype = wintypes.HANDLE

# CloseHandle(hObject)
_CloseHandle = kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL

# ReadProcessMemory(hProcess, lpBaseAddress, lpBuffer, nSize, lpNumberOfBytesRead)
_ReadProcessMemory = kernel32.ReadProcessMemory
_ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_ReadProcessMemory.restype = wintypes.BOOL

# 常量
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
STILL_ACTIVE = 259

T = TypeVar("T")


class PvZMemoryError(Exception):
    """PvZ 内存访问错误."""


class PvZMemory:
    """PvZ 进程内存读取器.

    使用方法::

        mem = PvZMemory()
        if mem.connect():
            print(f"版本: {mem.version_name}")
            print(f"阳光: {mem.read_int(mem.main_object, 0x5578)}")
    """

    def __init__(self) -> None:
        self._hwnd: int = 0
        self._pid: int = 0
        self._handle: int = 0
        self._version: PvZVersion = PvZVersion.NOT_FOUND
        self._offsets: PvZOffsets | None = None
        self._pvz_base: int = 0       # PvzBase 对象地址（缓存）
        self._main_object: int = 0    # MainObject 对象地址（缓存）

    # ------------------------------------------------------------------ #
    #  连接 / 断开
    # ------------------------------------------------------------------ #

    def connect(self) -> bool:
        """查找并连接 PvZ 进程，自动检测版本.

        Returns:
            True 表示成功连接且版本受支持。
        """
        # 1. 查找窗口
        hwnd = self._find_pvz_window()
        if not hwnd:
            logger.debug("[PvZ] 未找到 PvZ 窗口")
            return False
        self._hwnd = hwnd

        # 2. 获取进程 ID
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            logger.warning("[PvZ] 无法获取进程 ID")
            return False
        self._pid = pid.value

        # 3. 打开进程句柄
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        handle = _OpenProcess(access, False, self._pid)
        if not handle:
            logger.warning("[PvZ] 无法打开进程（需要管理员权限?）")
            self._version = PvZVersion.OPEN_ERROR
            return False
        self._handle = handle

        # 4. 版本检测
        self._version = self._detect_version()
        if self._version in (PvZVersion.NOT_FOUND, PvZVersion.UNSUPPORTED):
            logger.warning("[PvZ] 不支持的 PvZ 版本: {}", self._version)
            self.disconnect()
            return False
        if self._version == PvZVersion.OPEN_ERROR:
            logger.warning("[PvZ] 版本检测失败")
            self.disconnect()
            return False

        # 5. 加载偏移表
        self._offsets = get_offsets(self._version)
        if not self._offsets:
            logger.error("[PvZ] 版本 {} 无偏移表", self._version)
            self.disconnect()
            return False

        # 6. 缓存关键指针
        self._pvz_base = self.read_pointer(PVZ_BASE_ADDRESS)
        if not self._pvz_base:
            logger.error("[PvZ] 无法读取 PvzBase")
            self.disconnect()
            return False

        self._main_object = self.read_pointer(self._pvz_base + self._offsets.main_object)
        if not self._main_object:
            # 可能不在战斗界面，MainObject 可能为空
            logger.debug("[PvZ] MainObject 为空（可能不在战斗中）")

        logger.info(
            "[PvZ] 连接成功: {} (PID={}), PvzBase=0x{:X}",
            self._offsets.version_name, self._pid, self._pvz_base,
        )
        return True

    def disconnect(self) -> None:
        """断开与 PvZ 进程的连接."""
        if self._handle:
            _CloseHandle(self._handle)
            self._handle = 0
        self._hwnd = 0
        self._pid = 0
        self._pvz_base = 0
        self._main_object = 0

    def is_connected(self) -> bool:
        """检查连接是否仍然有效."""
        if not self._handle:
            return False
        # 检查进程是否仍在运行
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(self._handle, ctypes.byref(exit_code))
        return exit_code.value == STILL_ACTIVE

    # ------------------------------------------------------------------ #
    #  属性
    # ------------------------------------------------------------------ #

    @property
    def version(self) -> PvZVersion:
        return self._version

    @property
    def version_name(self) -> str:
        return self._offsets.version_name if self._offsets else "Unknown"

    @property
    def offsets(self) -> PvZOffsets:
        """获取当前版本的偏移量表（已连接时可用）."""
        if not self._offsets:
            raise PvZMemoryError("尚未连接到 PvZ 进程")
        return self._offsets

    @property
    def pvz_base(self) -> int:
        """PvzBase 对象地址."""
        return self._pvz_base

    @property
    def main_object(self) -> int:
        """MainObject 对象地址（可能为 0，如不在战斗中）."""
        return self._main_object

    def refresh_main_object(self) -> int:
        """重新读取 MainObject 指针（场景切换后调用）."""
        if self._pvz_base and self._offsets:
            self._main_object = self.read_pointer(
                self._pvz_base + self._offsets.main_object
            )
        return self._main_object

    # ------------------------------------------------------------------ #
    #  底层内存读取
    # ------------------------------------------------------------------ #

    def read_bytes(self, address: int, size: int) -> bytes:
        """从指定地址读取原始字节.

        Args:
            address: 目标进程中的内存地址。
            size: 要读取的字节数。

        Returns:
            读取到的字节数据。

        Raises:
            PvZMemoryError: 读取失败时抛出。
        """
        buf = ctypes.create_string_buffer(size)
        bytes_read = ctypes.c_size_t()
        success = _ReadProcessMemory(
            self._handle,
            ctypes.c_void_p(address),
            buf,
            size,
            ctypes.byref(bytes_read),
        )
        if not success or bytes_read.value != size:
            raise PvZMemoryError(
                f"读取内存失败: address=0x{address:X}, size={size}, "
                f"read={bytes_read.value}"
            )
        return buf.raw

    def read_int(self, address: int) -> int:
        """读取 32 位有符号整数."""
        data = self.read_bytes(address, 4)
        return struct.unpack("<i", data)[0]

    def read_uint(self, address: int) -> int:
        """读取 32 位无符号整数."""
        data = self.read_bytes(address, 4)
        return struct.unpack("<I", data)[0]

    def read_int8(self, address: int) -> int:
        """读取 8 位有符号整数."""
        data = self.read_bytes(address, 1)
        return struct.unpack("<b", data)[0]

    def read_uint8(self, address: int) -> int:
        """读取 8 位无符号整数."""
        data = self.read_bytes(address, 1)
        return struct.unpack("<B", data)[0]

    def read_bool(self, address: int) -> bool:
        """读取布尔值（1 字节）."""
        return self.read_uint8(address) != 0

    def read_float(self, address: int) -> float:
        """读取 32 位浮点数."""
        data = self.read_bytes(address, 4)
        return struct.unpack("<f", data)[0]

    def read_pointer(self, address: int) -> int:
        """读取指针（32 位地址）."""
        data = self.read_bytes(address, 4)
        return struct.unpack("<I", data)[0]

    def read_int16(self, address: int) -> int:
        """读取 16 位有符号整数."""
        data = self.read_bytes(address, 2)
        return struct.unpack("<h", data)[0]

    def read_uint16(self, address: int) -> int:
        """读取 16 位无符号整数."""
        data = self.read_bytes(address, 2)
        return struct.unpack("<H", data)[0]

    # ------------------------------------------------------------------ #
    #  多级指针链读取 — 对应 pvztoolkit ReadMemory<initializer_list>
    # ------------------------------------------------------------------ #

    def read_chain(self, offsets: list[int], *, read_type: str = "int") -> Any:
        """沿指针链读取值.

        模拟 pvztoolkit 的 ReadMemory({addr1, addr2, ..., field}):
        - 前面每级都是读指针: [addr1] → ptr, [ptr + addr2] → ptr, ...
        - 最后一级读取目标值

        Args:
            offsets: 偏移列表。
                单元素 [0x6a9ec0]: 直接读该地址的值
                双元素 [0x6a9ec0, 0x768]: 读 [0x6a9ec0] + 0x768 的值
                三元素 [0x6a9ec0, 0x768, 0x5560]: 读 [[0x6a9ec0]+0x768]+0x5560 的值
            read_type: 最后一级读取类型: "int", "uint", "float", "bool", "pointer",
                       "int8", "uint8", "int16", "uint16"

        Returns:
            读取到的值。

        Example::

            # 读取阳光: [[[0x6a9ec0] +0x868] +0x5578]
            sun = mem.read_chain([0x6A9EC0, 0x868, 0x5578])

            # 读取游戏界面: [[0x6a9ec0] +0x920]
            ui = mem.read_chain([0x6A9EC0, 0x920])
        """
        if not offsets:
            raise ValueError("offsets 不能为空")

        if len(offsets) == 1:
            # 直接读取绝对地址
            return self._read_typed(offsets[0], read_type)

        # 逐级解引用指针
        ptr = 0
        for i, off in enumerate(offsets):
            if i < len(offsets) - 1:
                # 中间级: 读取指针
                ptr = self.read_pointer(ptr + off)
                if ptr == 0:
                    return self._default_value(read_type)
            else:
                # 最后级: 读取目标值
                return self._read_typed(ptr + off, read_type)

        # 不可达
        return self._default_value(read_type)

    def read_chain_safe(self, offsets: list[int], *, read_type: str = "int") -> Any:
        """安全的指针链读取，任何环节失败返回默认值而不抛异常."""
        try:
            return self.read_chain(offsets, read_type=read_type)
        except (PvZMemoryError, OSError):
            return self._default_value(read_type)

    def _read_typed(self, address: int, read_type: str) -> Any:
        """根据类型标识读取值."""
        readers = {
            "int": self.read_int,
            "uint": self.read_uint,
            "float": self.read_float,
            "bool": self.read_bool,
            "pointer": self.read_pointer,
            "int8": self.read_int8,
            "uint8": self.read_uint8,
            "int16": self.read_int16,
            "uint16": self.read_uint16,
        }
        reader = readers.get(read_type)
        if not reader:
            raise ValueError(f"不支持的读取类型: {read_type}")
        return reader(address)

    @staticmethod
    def _default_value(read_type: str) -> Any:
        """返回类型的默认值（读取失败时使用）."""
        defaults = {
            "int": 0, "uint": 0, "float": 0.0, "bool": False,
            "pointer": 0, "int8": 0, "uint8": 0, "int16": 0, "uint16": 0,
        }
        return defaults.get(read_type, 0)

    # ------------------------------------------------------------------ #
    #  PvZ 快捷读取方法
    # ------------------------------------------------------------------ #

    def get_game_ui(self) -> int:
        """读取当前游戏界面状态 (1=主界面 2=选卡 3=战斗)."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.game_ui], read_type="int"
        )

    def get_game_mode(self) -> int:
        """读取游戏模式/关卡 ID."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.game_mode], read_type="int"
        )

    def get_card_slot_count(self, default: int = 10) -> int:
        """读取当前卡槽数量（选卡/战斗界面通用）.

        从 MainObject→SeedArray→[seed_count] 读取已分配的卡片槽位数。
        读不到或异常时返回 default（生存模式固定 10 槽）。

        选卡界面 MainObject 已存在但 SeedArray 可能尚未填充，此时
        返回默认值，由调用方降级提示。
        """
        try:
            count = self.read_chain_safe(
                [
                    PVZ_BASE_ADDRESS,
                    self.offsets.main_object,
                    self.offsets.seed_array,
                    self.offsets.seed_count,
                ],
                read_type="int",
            )
            if 1 <= count <= 10:
                return count
            return default
        except Exception:
            return default

    def get_sun(self) -> int:
        """读取当前阳光数量."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.sun],
            read_type="int",
        )

    def get_scene(self) -> int:
        """读取当前场景 (0=白天 1=黑夜 2=泳池 3=雾夜 4=天台 5=月夜)."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.scene],
            read_type="int",
        )

    def get_wave(self) -> int:
        """读取当前波数 (0-indexed)."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.wave],
            read_type="int",
        )

    def get_total_wave(self) -> int:
        """读取总波数."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.total_wave],
            read_type="int",
        )

    def get_game_clock(self) -> int:
        """读取游戏时钟（厘秒，1/100 秒）."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.game_clock],
            read_type="int",
        )

    def is_game_paused(self) -> bool:
        """游戏是否暂停."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.game_paused],
            read_type="bool",
        )

    def get_refresh_countdown(self) -> int:
        """读取僵尸刷新倒计时（厘秒）."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.refresh_countdown],
            read_type="int",
        )

    def get_huge_wave_countdown(self) -> int:
        """读取大波僵尸刷新倒计时（厘秒）."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.huge_wave_countdown],
            read_type="int",
        )

    def get_level_end_countdown(self) -> int:
        """读取关卡结束倒计时."""
        return self.read_chain_safe(
            [PVZ_BASE_ADDRESS, self.offsets.main_object, self.offsets.level_end_countdown],
            read_type="int",
        )

    # ------------------------------------------------------------------ #
    #  内部工具
    # ------------------------------------------------------------------ #

    def _find_pvz_window(self) -> int:
        """查找 PvZ 窗口句柄."""
        # 先尝试已知标题
        for title in PVZ_WINDOW_TITLES:
            hwnd = _FindWindowW(PVZ_WINDOW_CLASS, title)
            if hwnd:
                return hwnd
        # 兜底: 只按类名查找
        hwnd = _FindWindowW(PVZ_WINDOW_CLASS, None)
        return hwnd if hwnd else 0

    def _detect_version(self) -> PvZVersion:
        """通过 PE 时间戳检测 PvZ 版本.

        参考 pvztoolkit pvz.cpp FindPvZ:
        1. 读取 DOS header → e_lfanew → NT header 偏移
        2. 读取 NT header + 0x08 处的 TimeDateStamp
        3. 与已知时间戳比对
        """
        try:
            # PvZ 基址固定为 0x400000
            base = 0x400000
            # 读取 e_lfanew (NT header 偏移)
            nt_header_offset = self.read_uint(base + 0x3C)
            # 读取 TimeDateStamp
            timestamp = self.read_uint(base + nt_header_offset + 0x08)
            version = detect_version_from_timestamp(timestamp)
            logger.info(
                "[PvZ] PE 时间戳: 0x{:08X} → 版本: {}",
                timestamp, version,
            )
            return version
        except (PvZMemoryError, OSError) as e:
            logger.warning("[PvZ] 版本检测失败: {}", e)
            return PvZVersion.UNSUPPORTED
