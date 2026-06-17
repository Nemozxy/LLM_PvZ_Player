"""PvZ 代码注入器 — 向 PvZ 进程注入 x86 机器码，直接调用游戏内部函数.

核心机制:
1. 写入 hack 字节 (WriteProcessMemory) — 修改游戏代码段实现功能开关
2. 注入 shellcode (VirtualAllocEx + WriteProcessMemory + CreateRemoteThread) — 调用游戏函数
3. 注入前暂停游戏主循环 (block_main_loop hack)，防止竞态条件

参考项目:
- pvztoolkit (code.cpp): Code 类 + asm_code_inject
- AsmVsZombies (avz_asm.cpp): AAsm 内联汇编调用
- 所有地址和数据来自 pvztoolkit data.cpp, 仅维护 V1_0_0_1051_EN
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
import time
from dataclasses import dataclass

from loguru import logger

from .memory import PvZMemory

# ================================================================== #
#  Windows API
# ================================================================== #

_kernel32 = ctypes.windll.kernel32

# 注入用
_VirtualAllocEx = _kernel32.VirtualAllocEx
_VirtualFreeEx = _kernel32.VirtualFreeEx
_WriteProcessMemory = _kernel32.WriteProcessMemory
_CreateRemoteThread = _kernel32.CreateRemoteThread
_WaitForSingleObject = _kernel32.WaitForSingleObject
_CloseHandle = _kernel32.CloseHandle
_GetExitCodeThread = _kernel32.GetExitCodeThread
_OpenProcess = _kernel32.OpenProcess

# 常量
MEM_COMMIT = 0x00001000
MEM_RELEASE = 0x00008000
PAGE_EXECUTE_READWRITE = 0x40
INFINITE = 0xFFFFFFFF
WAIT_TIMEOUT = 0x00000102

# 进程权限 — 注入需要更多权限
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_CREATE_THREAD = 0x0002

# ================================================================== #
#  V1_0_0_1051_EN 游戏内部地址 (来自 pvztoolkit data.cpp)
# ================================================================== #

# 全局基址
PVZ_BASE = 0x6A9EC0

# PvzBase 内部偏移
BOARD_OFFSET = 0x768   # PvzBase → Board*
MOUSE_OFFSET = 0x320   # PvzBase → MouseWindow*

# ---- 游戏内部函数地址 ----

# PutPlant(Board*, col, row, type, imitator_type)
# 调用约定: push imitator_type, push type, mov eax=row, push col,
#           mov ebp=[PVZ_BASE], mov ebp=[ebp+BOARD_OFFSET], push ebp
FUNC_PUT_PLANT = 0x40D120

# ShovelPlant — 来自 AsmVsZombies AAsm::ShovelPlant
# 签名: push 6, push 1, mov ecx=y, mov edx=x, Board* in eax
FUNC_SHOVEL_PLANT = 0x411060

# MouseDown / MouseUp — 来自 AsmVsZombies AAsm::MouseDown / MouseUp
# MouseDown: push x, eax=y, ebx=key, ecx=MouseWindow*
FUNC_MOUSE_DOWN = 0x539390
# MouseUp: push key, push x, eax=MouseWindow*, ebx=y
FUNC_MOUSE_UP = 0x5392E0

# GridToAbscissa(row, col) → x 像素坐标
# ECX = Board*, EAX = col, ESI = row
FUNC_GRID_TO_ABSCISSA = 0x41C680

# GridToOrdinate(row, col) → y 像素坐标
# EBX = Board*, ECX = col, EAX = row
FUNC_GRID_TO_ORDINATE = 0x41C740


# ================================================================== #
#  Hack 定义 — 来自 pvztoolkit data.cpp V1_0_0_1051_EN
# ================================================================== #
# HACK 格式: {addr, hack_value, reset_value}
# enable: 将 hack_value 写入 addr
# disable: 将 reset_value 写回 addr

@dataclass(frozen=True)
class HackInfo:
    """一个 hack 条目: 地址 + 修改值 + 原始值."""
    addr: int
    hack_value: bytes
    reset_value: bytes


# 注入前暂停游戏主循环 — 防止注入代码与游戏主循环并发执行
# pvztoolkit: "block_main_loop", 0x552014, hack=0xFE(jmp), reset=0xDB
HACK_BLOCK_MAIN_LOOP = HackInfo(0x00552014, b'\xFE', b'\xDB')

# 自动收集阳光 — 阳光出现后自动飞向计数器
# pvztoolkit: "auto_collected", 0x43158F, hack=0xEB(jmp short), reset=0x75(jnz)
HACK_AUTO_COLLECTED = HackInfo(0x0043158F, b'\xEB', b'\x75')

# 无阳光上限
# pvztoolkit: "unlock_sun_limit", 0x430A23, hack=0xEB, reset=0x7E
HACK_UNLOCK_SUN_LIMIT = HackInfo(0x00430A23, b'\xEB', b'\x7E')

# 植物可种在任何位置
# pvztoolkit: "placed_anywhere", 0x40FE30, hack=0x81, reset=0x84
HACK_PLACED_ANYWHERE = HackInfo(0x0040FE30, b'\x81', b'\x84')


# ================================================================== #
#  Shellcode 构建辅助
# ================================================================== #

def _build_put_plant_code(row: int, col: int, plant_type: int, imitater: bool) -> bytearray:
    """构建 PutPlant(row, col, type, imitater) 的 x86 机器码.

    调用约定来自 pvztoolkit asm_put_plant (非 GOTY 版本):
        push imitator_type   ; -1 或 实际类型
        push type            ; 植物类型
        mov eax, row         ; 行号
        push col             ; 列号
        mov ebp, [PVZ_BASE]
        mov ebp, [ebp + BOARD_OFFSET]
        push ebp             ; Board*
        call PutPlant
        ret

    对应 AvZ AAsm::PutPlant (avz_asm.cpp L388-414).
    """
    code = bytearray()

    if imitater:
        # push plant_type (实际要模仿的植物类型)
        code += b'\x68' + struct.pack('<i', plant_type)
        # push 48 (模仿者卡片类型)
        code += b'\x68' + struct.pack('<I', 48)
    else:
        # push -1 (非模仿者)
        code += b'\x6A\xFF'
        # push plant_type
        code += b'\x68' + struct.pack('<i', plant_type)

    # mov eax, row
    code += b'\xB8' + struct.pack('<i', row)
    # push col
    code += b'\x68' + struct.pack('<i', col)
    # mov ebp, [PVZ_BASE]
    code += b'\x8B\x2D' + struct.pack('<I', PVZ_BASE)
    # mov ebp, [ebp + BOARD_OFFSET]
    code += b'\x8B\xAD' + struct.pack('<I', BOARD_OFFSET)
    # push ebp
    code += b'\x55'
    # mov edx, FUNC_PUT_PLANT
    code += b'\xBA' + struct.pack('<I', FUNC_PUT_PLANT)
    # call edx
    code += b'\xFF\xD2'
    # ret
    code += b'\xC3'

    return code


def _build_shovel_code(x: int, y: int) -> bytearray:
    """构建 ShovelPlant(x, y) 的 x86 机器码.

    调用约定来自 AsmVsZombies AAsm::ShovelPlant (avz_asm.cpp L243-256):
        push 6
        push 1
        mov ecx, y
        mov edx, x
        mov eax, [PVZ_BASE]
        mov eax, [eax + BOARD_OFFSET]  ; eax = Board*
        mov ebx, FUNC_SHOVEL_PLANT
        call ebx
        ret
    """
    code = bytearray()

    # push 6
    code += b'\x6A\x06'
    # push 1
    code += b'\x6A\x01'
    # mov ecx, y
    code += b'\xB9' + struct.pack('<i', y)
    # mov edx, x
    code += b'\xBA' + struct.pack('<i', x)
    # mov eax, [PVZ_BASE]
    code += b'\xA1' + struct.pack('<I', PVZ_BASE)
    # mov eax, [eax + BOARD_OFFSET]
    code += b'\x8B\x80' + struct.pack('<I', BOARD_OFFSET)
    # mov ebx, FUNC_SHOVEL_PLANT
    code += b'\xBB' + struct.pack('<I', FUNC_SHOVEL_PLANT)
    # call ebx
    code += b'\xFF\xD3'
    # ret
    code += b'\xC3'

    return code


def _build_mouse_click_code(x: int, y: int, button: int = 1, with_ret: bool = True) -> bytearray:
    """构建完整的 MouseClick(x, y, button) 的 x86 机器码.

    实现 AvZ AAsm::MouseClick 的逻辑 (avz_asm.cpp L153-159):
        MouseDown(x, y, key)
        MouseUp(x, y, key)

    MouseDown: push x, eax=y, ebx=key, ecx=MouseWindow*
    MouseUp:   push key, push x, eax=MouseWindow*, ebx=y

    Args:
        x: 游戏 x 坐标。
        y: 游戏 y 坐标。
        button: 1=左键, 2=右键。
        with_ret: 是否末尾添加 ret（批量拼接时设为 False）。
    """
    code = bytearray()

    # ---- MouseDown(x, y, button) ----
    # mov ecx, [PVZ_BASE]
    code += b'\x8B\x0D' + struct.pack('<I', PVZ_BASE)
    # mov ecx, [ecx + MOUSE_OFFSET]  ; ecx = MouseWindow*
    code += b'\x8B\x89' + struct.pack('<I', MOUSE_OFFSET)
    # push x
    code += b'\x68' + struct.pack('<i', x)
    # mov eax, y
    code += b'\xB8' + struct.pack('<i', y)
    # mov ebx, button
    code += b'\xBB' + struct.pack('<i', button)
    # mov edx, FUNC_MOUSE_DOWN
    code += b'\xBA' + struct.pack('<I', FUNC_MOUSE_DOWN)
    # call edx
    code += b'\xFF\xD2'

    # ---- MouseUp(x, y, button) ----
    # mov eax, [PVZ_BASE]
    code += b'\xA1' + struct.pack('<I', PVZ_BASE)
    # mov eax, [eax + MOUSE_OFFSET]  ; eax = MouseWindow*
    code += b'\x8B\x80' + struct.pack('<I', MOUSE_OFFSET)
    # push button
    code += b'\x68' + struct.pack('<i', button)
    # push x
    code += b'\x68' + struct.pack('<i', x)
    # mov ebx, y
    code += b'\xBB' + struct.pack('<i', y)
    # mov edx, FUNC_MOUSE_UP
    code += b'\xBA' + struct.pack('<I', FUNC_MOUSE_UP)
    # call edx
    code += b'\xFF\xD2'

    if with_ret:
        code += b'\xC3'

    return code


def _build_grid_to_x_code(row: int, col: int) -> bytearray:
    """构建 GridToAbscissa(row, col) 的 x86 机器码，结果写入固定地址.

    调用约定来自 AvZ (avz_asm.cpp L339-352):
        mov ecx, [PVZ_BASE]
        mov ecx, [ecx + BOARD_OFFSET]  ; ecx = Board*
        mov eax, col
        mov esi, row
        call GridToAbscissa
        ; 返回值在 eax

    结果写入 PVZ_BASE + 0x900（PvzBase 对象尾部安全区域）。
    """
    RESULT_ADDR = PVZ_BASE + 0x900

    code = bytearray()

    # mov ecx, [PVZ_BASE]
    code += b'\x8B\x0D' + struct.pack('<I', PVZ_BASE)
    # mov ecx, [ecx + BOARD_OFFSET]
    code += b'\x8B\x89' + struct.pack('<I', BOARD_OFFSET)
    # mov eax, col
    code += b'\xB8' + struct.pack('<i', col)
    # mov esi, row
    code += b'\xBE' + struct.pack('<i', row)
    # mov edx, FUNC_GRID_TO_ABSCISSA
    code += b'\xBA' + struct.pack('<I', FUNC_GRID_TO_ABSCISSA)
    # call edx
    code += b'\xFF\xD2'
    # mov [RESULT_ADDR], eax
    code += b'\xA3' + struct.pack('<I', RESULT_ADDR)
    # ret
    code += b'\xC3'

    return code


def _build_grid_to_y_code(row: int, col: int) -> bytearray:
    """构建 GridToOrdinate(row, col) 的 x86 机器码，结果写入固定地址.

    调用约定来自 AvZ (avz_asm.cpp L355-369):
        mov ebx, [PVZ_BASE]
        mov ebx, [ebx + BOARD_OFFSET]  ; ebx = Board*
        mov ecx, col
        mov eax, row
        call GridToOrdinate
        ; 返回值在 eax
    """
    RESULT_ADDR = PVZ_BASE + 0x904

    code = bytearray()

    # mov ebx, [PVZ_BASE]
    code += b'\x8B\x1D' + struct.pack('<I', PVZ_BASE)
    # mov ebx, [ebx + BOARD_OFFSET]
    code += b'\x8B\x9B' + struct.pack('<I', BOARD_OFFSET)
    # mov ecx, col
    code += b'\xB9' + struct.pack('<i', col)
    # mov eax, row
    code += b'\xB8' + struct.pack('<i', row)
    # mov edx, FUNC_GRID_TO_ORDINATE
    code += b'\xBA' + struct.pack('<I', FUNC_GRID_TO_ORDINATE)
    # call edx
    code += b'\xFF\xD2'
    # mov [RESULT_ADDR], eax
    code += b'\xA3' + struct.pack('<I', RESULT_ADDR)
    # ret
    code += b'\xC3'

    return code


# ================================================================== #
#  代码注入器
# ================================================================== #

class PvZCodeInjector:
    """PvZ 代码注入执行器.

    向 PvZ 进程注入小型 x86 shellcode 并远程执行。

    核心流程 (参考 pvztoolkit PvZ::asm_code_inject):
        1. 暂停游戏主循环 (enable_hack BLOCK_MAIN_LOOP)
        2. VirtualAllocEx 分配可执行内存
        3. WriteProcessMemory 写入 shellcode
        4. CreateRemoteThread 远程执行
        5. WaitForSingleObject 等待完成
        6. VirtualFreeEx 释放内存
        7. 恢复游戏主循环 (disable_hack BLOCK_MAIN_LOOP)
    """

    def __init__(self, memory: PvZMemory) -> None:
        self._mem = memory
        if not self._mem.is_connected():
            raise RuntimeError("PvZ 进程未连接，无法注入代码")

        # 重新打开进程，注入需要更多权限
        handle = self._open_process_for_injection()
        if not handle:
            raise RuntimeError("无法以注入权限打开 PvZ 进程")
        self._inject_handle = handle

        # hack 状态跟踪
        self._active_hacks: dict[str, bool] = {}

        logger.info("[注入器] 初始化成功，注入句柄=0x{:X}", handle)

    def close(self) -> None:
        """释放注入句柄和恢复所有 hack."""
        for name in list(self._active_hacks):
            try:
                self._disable_hack_by_name(name)
            except Exception:
                pass
        if self._inject_handle:
            _CloseHandle(self._inject_handle)
            self._inject_handle = 0

    # ------------------------------------------------------------------ #
    #  Hack 机制 — 写入字节修改游戏行为
    # ------------------------------------------------------------------ #

    def enable_hack(self, name: str, hack: HackInfo) -> None:
        """启用一个 hack: 将 hack_value 写入目标地址."""
        self._write_bytes(hack.addr, hack.hack_value)
        self._active_hacks[name] = True
        logger.debug("[Hack] 启用 {} (0x{:X})", name, hack.addr)

    def disable_hack(self, name: str, hack: HackInfo) -> None:
        """禁用一个 hack: 将 reset_value 写回目标地址."""
        self._write_bytes(hack.addr, hack.reset_value)
        self._active_hacks.pop(name, None)
        logger.debug("[Hack] 禁用 {} (0x{:X})", name, hack.addr)

    def _disable_hack_by_name(self, name: str) -> None:
        """按名称禁用 hack (内部清理用)."""
        hack_map = {
            "block_main_loop": HACK_BLOCK_MAIN_LOOP,
            "auto_collected": HACK_AUTO_COLLECTED,
            "unlock_sun_limit": HACK_UNLOCK_SUN_LIMIT,
            "placed_anywhere": HACK_PLACED_ANYWHERE,
        }
        hack = hack_map.get(name)
        if hack:
            self.disable_hack(name, hack)

    def _write_bytes(self, addr: int, data: bytes) -> None:
        """向 PvZ 进程指定地址写入字节.

        用于 hack 机制: 修改游戏代码段中的指令字节。
        """
        handle = self._inject_handle
        written = wintypes.SIZE_T(0)
        success = _WriteProcessMemory(
            handle,
            ctypes.c_void_p(addr),
            data,
            len(data),
            ctypes.byref(written),
        )
        if not success or written.value != len(data):
            raise RuntimeError(
                f"WriteProcessMemory 写入 0x{addr:X} 失败: "
                f"written={written.value}/{len(data)}"
            )

    # ------------------------------------------------------------------ #
    #  注入核心 — shellcode 执行
    # ------------------------------------------------------------------ #

    def _inject_and_execute(self, code: bytes, timeout_ms: int = 3000) -> int:
        """注入 shellcode 到 PvZ 进程并执行.

        参考 pvztoolkit PvZ::asm_code_inject:
        在注入前后暂停/恢复游戏主循环，防止竞态条件。
        """
        # 1. 暂停游戏主循环
        self.enable_hack("block_main_loop", HACK_BLOCK_MAIN_LOOP)
        time.sleep(0.01)  # 等待主循环停下来

        try:
            result = self._raw_inject(code, timeout_ms)
        finally:
            # 2. 恢复游戏主循环
            self.disable_hack("block_main_loop", HACK_BLOCK_MAIN_LOOP)

        return result

    def _raw_inject(self, code: bytes, timeout_ms: int = 3000) -> int:
        """底层注入执行（不暂停主循环）."""
        handle = self._inject_handle
        if not handle:
            raise RuntimeError("注入句柄无效")

        code_size = len(code)

        # 分配可执行内存
        code_addr = _VirtualAllocEx(
            handle, None, code_size,
            MEM_COMMIT, PAGE_EXECUTE_READWRITE,
        )
        if not code_addr:
            raise RuntimeError(f"VirtualAllocEx 失败")

        try:
            # 写入 shellcode
            written = wintypes.SIZE_T(0)
            if not _WriteProcessMemory(
                handle, ctypes.c_void_p(code_addr),
                code, code_size,
                ctypes.byref(written),
            ):
                raise RuntimeError("WriteProcessMemory 失败")
            if written.value != code_size:
                raise RuntimeError(f"写入不完整: {written.value}/{code_size}")

            # 创建远程线程执行
            thread = _CreateRemoteThread(
                handle, None, 0,
                ctypes.c_void_p(code_addr),
                None, 0, None,
            )
            if not thread:
                raise RuntimeError("CreateRemoteThread 失败")

            # 等待完成
            wait_result = _WaitForSingleObject(thread, timeout_ms)
            if wait_result == WAIT_TIMEOUT:
                _CloseHandle(thread)
                raise RuntimeError("远程线程执行超时")

            # 读取退出码
            exit_code = wintypes.DWORD(0)
            _GetExitCodeThread(thread, ctypes.byref(exit_code))
            _CloseHandle(thread)

            return exit_code.value

        finally:
            _VirtualFreeEx(handle, ctypes.c_void_p(code_addr), 0, MEM_RELEASE)

    # ------------------------------------------------------------------ #
    #  高层动作接口
    # ------------------------------------------------------------------ #

    def put_plant(self, row: int, col: int, plant_type: int, imitater: bool = False) -> None:
        """直接放置植物（不经过鼠标操作）.

        Args:
            row: 行号 (0-based)。
            col: 列号 (0-based)。
            plant_type: 植物类型 ID（0=豌豆射手, 1=向日葵, ...）。
            imitater: 是否为模仿者。
        """
        logger.info("[注入] PutPlant row={}, col={}, type={}, imitater={}",
                     row, col, plant_type, imitater)
        code = _build_put_plant_code(row, col, plant_type, imitater)
        self._inject_and_execute(bytes(code))
        time.sleep(0.05)

    def shovel(self, x: int, y: int) -> None:
        """在游戏坐标位置执行铲除操作.

        Args:
            x: 游戏内 x 坐标（800x600 基准）。
            y: 游戏内 y 坐标（800x600 基准）。
        """
        logger.info("[注入] ShovelPlant ({}, {})", x, y)
        code = _build_shovel_code(x, y)
        self._inject_and_execute(bytes(code))
        time.sleep(0.05)

    def mouse_click(self, x: int, y: int, button: int = 1) -> None:
        """在游戏坐标空间内模拟鼠标点击.

        实现 MouseDown + MouseUp 的完整点击序列。

        Args:
            x: 游戏内 x 坐标（800x600 基准）。
            y: 游戏内 y 坐标（800x600 基准）。
            button: 1=左键, 2=右键。
        """
        logger.info("[注入] MouseClick ({}, {}), button={}", x, y, button)
        code = _build_mouse_click_code(x, y, button)
        self._inject_and_execute(bytes(code))
        time.sleep(0.05)

    def grid_to_pixel(self, row: int, col: int) -> tuple[int, int]:
        """调用游戏内部 GridToAbscissa/Ordinate 获取格子中心像素坐标.

        这比用公式近似计算更准确，特别是屋顶场景的斜坡偏移。

        注意: GridToOrdinate 返回格子顶部 y, 需 +40 得到格子中心 (与 AvZ
        AGridToCoordinate 一致: y = GridToOrdinate(row, col) + 40).

        Args:
            row: 行号 (0-based)。
            col: 列号 (0-based)。

        Returns:
            (x, y) 格子中心的游戏像素坐标 (800x600 基准)。
        """
        # 注入 GridToAbscissa
        x_code = _build_grid_to_x_code(row, col)
        self._inject_and_execute(bytes(x_code))

        # 注入 GridToOrdinate
        y_code = _build_grid_to_y_code(row, col)
        self._inject_and_execute(bytes(y_code))

        # 读取结果
        RESULT_X_ADDR = PVZ_BASE + 0x900
        RESULT_Y_ADDR = PVZ_BASE + 0x904

        try:
            x = self._mem.read_int(RESULT_X_ADDR)
            y = self._mem.read_int(RESULT_Y_ADDR) + 40  # 格子顶部 → 格子中心
        except Exception:
            # 读取失败，用近似公式兜底
            x = (col + 1) * 80
            y = self._approximate_grid_y(row)
            logger.warning("[注入] GridToOrdinate 读取失败，用近似值 ({}, {})", x, y)

        return x, y

    def collect_sun_at(self, x: int, y: int) -> None:
        """在游戏坐标位置收集阳光/物品（等于模拟左键点击）."""
        self.mouse_click(int(x), int(y))

    def collect_all_sun(self, item_coords: list[tuple[int, int]]) -> int:
        """批量收集阳光.

        一次注入批量点击，减少注入次数。

        Args:
            item_coords: 阳光的游戏坐标列表。

        Returns:
            收集的数量。
        """
        code = bytearray()
        for x, y in item_coords[:8]:
            code += _build_mouse_click_code(x, y, 1, with_ret=False)

        if code:
            code += b'\xC3'  # ret
            self._inject_and_execute(bytes(code))

        return min(len(item_coords), 8)

    # ------------------------------------------------------------------ #
    #  Hack 开关接口
    # ------------------------------------------------------------------ #

    def set_auto_collect(self, on: bool) -> None:
        """开启/关闭自动收集阳光.

        开启后阳光出现会自动飞向计数器，无需手动点击。
        """
        if on:
            self.enable_hack("auto_collected", HACK_AUTO_COLLECTED)
            logger.info("[注入] 自动收集阳光 已开启")
        else:
            self.disable_hack("auto_collected", HACK_AUTO_COLLECTED)
            logger.info("[注入] 自动收集阳光 已关闭")

    def set_unlock_sun_limit(self, on: bool) -> None:
        """开启/关闭阳光上限解锁."""
        if on:
            self.enable_hack("unlock_sun_limit", HACK_UNLOCK_SUN_LIMIT)
        else:
            self.disable_hack("unlock_sun_limit", HACK_UNLOCK_SUN_LIMIT)

    def set_placed_anywhere(self, on: bool) -> None:
        """开启/关闭植物任意放置."""
        if on:
            self.enable_hack("placed_anywhere", HACK_PLACED_ANYWHERE)
        else:
            self.disable_hack("placed_anywhere", HACK_PLACED_ANYWHERE)

    # ------------------------------------------------------------------ #
    #  内部工具
    # ------------------------------------------------------------------ #

    def _open_process_for_injection(self) -> int:
        """以注入权限重新打开 PvZ 进程.

        PvZMemory 只开了 PROCESS_VM_READ + PROCESS_QUERY_INFORMATION，
        注入需要 PROCESS_VM_WRITE + PROCESS_VM_OPERATION + PROCESS_CREATE_THREAD.
        """
        access = (
            PROCESS_VM_READ |
            PROCESS_VM_WRITE |
            PROCESS_VM_OPERATION |
            PROCESS_CREATE_THREAD |
            PROCESS_QUERY_INFORMATION
        )
        handle = _OpenProcess(access, False, self._mem._pid)
        if not handle:
            logger.warning("[注入器] 无法以注入权限打开进程，尝试使用原句柄")
            return self._mem._handle
        return handle

    @staticmethod
    def _approximate_grid_y(row: int) -> int:
        """近似计算格子 y 坐标 (仅作为 grid_to_pixel 读取失败时的兜底)."""
        return 50 + row * 100 + 40
