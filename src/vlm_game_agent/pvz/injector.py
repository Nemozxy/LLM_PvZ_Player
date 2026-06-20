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

# ReleaseMouse — 释放鼠标选中状态 (如取消卡片选中)
# 调用约定: eax = Board*, 无参数
FUNC_RELEASE_MOUSE = 0x40CD80

# FadeOutLevel — 来自 pvztoolkit PvZ::DirectWin (data.cpp:179, V1_0_0_1051_EN)
# 直接过关: 触发关卡结束的淡出动画，游戏判定本关通关。
# 调用约定: ECX = Board*, 无参数 (thiscall，与 ReleaseMouse 仅寄存器不同)
FUNC_FADE_OUT_LEVEL = 0x41B8D0

# ---- 选卡界面函数 (来自 AsmVsZombies avz_asm.cpp, V1_0_0_1051_EN) ----
# SelectCardUi_p 偏移: PvzBase + 0x774 (选卡界面 UI 对象)
SELECT_CARD_UI_OFFSET = 0x774
# ChooseCard(card_entry_ptr): 把植物加入卡组。参数是卡片条目地址
# (SelectCardUi_p + 0xa4 + cardType*0x3c)，而非植物类型本身。
FUNC_CHOOSE_CARD = 0x486030
# Rock(): 开始游戏 (等价点"一起摇摆吧！"按钮)。esi=PvzBase, edi=1, ebp=1
FUNC_ROCK = 0x486D20
# PickRandomSeeds(): 随机填满剩余空卡槽 (等价点调试试玩按钮)
FUNC_PICK_RANDOM_SEEDS = 0x4859B0

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

        # 默认开启自动收集阳光 — Agent 模式下无需手动点阳光
        self.set_auto_collect(True)

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
        logger.trace("[Hack] 启用 {} (0x{:X})", name, hack.addr)

    def disable_hack(self, name: str, hack: HackInfo) -> None:
        """禁用一个 hack: 将 reset_value 写回目标地址."""
        self._write_bytes(hack.addr, hack.reset_value)
        self._active_hacks.pop(name, None)
        logger.trace("[Hack] 禁用 {} (0x{:X})", name, hack.addr)

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

        用于 hack 机制和内存修改。
        """
        handle = self._inject_handle
        written = ctypes.c_size_t(0)
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

    def write_int(self, addr: int, value: int) -> None:
        """向 PvZ 进程指定地址写入 32 位整数.

        用于修改游戏数据（如阳光数量）。
        """
        self._write_bytes(addr, struct.pack('<i', value))

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
            written = ctypes.c_size_t(0)
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

    def put_plant(self, row: int, col: int, plant_type: int, imitater: bool = False,
                  sun_cost: int = 0) -> None:
        """直接放置植物（不经过鼠标操作）.

        注意: PutPlant 内部函数只创建植物对象，不扣除阳光。
        如果需要扣阳光，传入 sun_cost 参数，会手动修改内存中的阳光值。

        Args:
            row: 行号 (0-based)。
            col: 列号 (0-based)。
            plant_type: 植物类型 ID（0=豌豆射手, 1=向日葵, ...）。
            imitater: 是否为模仿者。
            sun_cost: 阳光消耗，0 表示不扣。
        """
        logger.info("[注入] PutPlant row={}, col={}, type={}, imitater={}, cost={}",
                     row, col, plant_type, imitater, sun_cost)
        code = _build_put_plant_code(row, col, plant_type, imitater)
        self._inject_and_execute(bytes(code))

        # 手动扣除阳光（PutPlant 内部函数不走 UI 逻辑，不扣阳光）
        if sun_cost > 0:
            self._deduct_sun(sun_cost)

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

    def release_mouse(self) -> None:
        """释放游戏鼠标选中状态.

        当卡片处于选中状态时（如种植物后卡片仍高亮），调用此方法取消选中。
        AvZ 在铲除前也会先调用 ReleaseMouse。
        调用约定: eax = Board*, 无参数。
        """
        code = bytearray()
        # mov eax, [PVZ_BASE]
        code += b'\xA1' + struct.pack('<I', PVZ_BASE)
        # mov eax, [eax + BOARD_OFFSET]  ; eax = Board*
        code += b'\x8B\x80' + struct.pack('<I', BOARD_OFFSET)
        # mov edx, FUNC_RELEASE_MOUSE
        code += b'\xBA' + struct.pack('<I', FUNC_RELEASE_MOUSE)
        # call edx
        code += b'\xFF\xD2'
        # ret
        code += b'\xC3'
        self._inject_and_execute(bytes(code))

    def win_level(self) -> None:
        """直接通关 — 调用游戏内部 FadeOutLevel 触发本关结束.

        对应 pvztoolkit 的"直接过关"功能。用于跳过 AI 难以胜任的实时小游戏
        （如坚果保龄球、传送带关卡），这些关卡对实时性要求极高，AI 的
        截图→推理→执行周期跟不上，强行玩只会失败。

        调用约定: ECX = Board*, 无参数 (thiscall)。
        """
        logger.info("[注入] FadeOutLevel 直接通关")
        code = bytearray()
        # mov ecx, [PVZ_BASE]
        code += b'\x8B\x0D' + struct.pack('<I', PVZ_BASE)
        # mov ecx, [ecx + BOARD_OFFSET]  ; ecx = Board*
        code += b'\x8B\x89' + struct.pack('<I', BOARD_OFFSET)
        # mov edx, FUNC_FADE_OUT_LEVEL
        code += b'\xBA' + struct.pack('<I', FUNC_FADE_OUT_LEVEL)
        # call edx
        code += b'\xFF\xD2'
        # ret
        code += b'\xC3'
        self._inject_and_execute(bytes(code))
        time.sleep(0.05)

    def choose_card(self, plant_type: int) -> None:
        """选卡界面: 把指定植物加入卡组.

        对应 AsmVsZombies AAsm::ChooseCard (avz_asm.cpp:258-275)。
        传入植物类型 (0~47)，内部计算卡片条目地址后调用游戏函数。
        必须在选卡界面 (game_ui=2) 调用。

        Args:
            plant_type: 植物类型 ID (0=豌豆射手, 1=向日葵, ...)。
        """
        logger.info("[注入] ChooseCard 植物类型={}", plant_type)
        code = bytearray()
        # mov eax, [PVZ_BASE]  (eax = PvzBase 对象指针，0x6a9ec0 处即对象本身)
        # 注意: [PVZ_BASE] 已经是 PvzBase 对象，不可再 mov eax,[eax] 二次解引用，
        # 否则会读到对象头部的 vtable 指针，后续 [eax+0x774] 越界访问 .text 段导致崩溃。
        # 与 PickRandomSeeds 的解引用层数一致 (共 2 层)。
        code += b'\xA1' + struct.pack('<I', PVZ_BASE)
        # mov eax, [eax + SELECT_CARD_UI_OFFSET]  ; eax = SelectCardUi_p
        code += b'\x8B\x80' + struct.pack('<I', SELECT_CARD_UI_OFFSET)
        # mov edx, plant_type
        code += b'\xBA' + struct.pack('<i', plant_type)
        # shl edx, 4
        code += b'\xC1\xE2\x04'
        # sub edx, plant_type  (edx = plant_type * 15)
        code += b'\x81\xEA' + struct.pack('<i', plant_type)
        # shl edx, 2  (edx = plant_type * 60 = plant_type * 0x3c)
        code += b'\xC1\xE2\x02'
        # add edx, 0xa4
        code += b'\x81\xC2\xA4\x00\x00\x00'
        # add edx, eax  (edx = SelectCardUi_p + 0xa4 + plant_type*0x3c)
        code += b'\x01\xC2'
        # push edx
        code += b'\x52'
        # mov ecx, FUNC_CHOOSE_CARD
        code += b'\xB9' + struct.pack('<I', FUNC_CHOOSE_CARD)
        # call ecx
        code += b'\xFF\xD1'
        # ret
        code += b'\xC3'
        self._inject_and_execute(bytes(code))
        time.sleep(0.2)  # 等待选卡动画

    def pick_random_seeds(self) -> None:
        """选卡界面: 随机填满剩余空卡槽.

        对应 AsmVsZombies AAsm::PickRandomSeeds (avz_asm.cpp:887-897)。
        等价于点击选卡界面的"调试试玩"按钮，会随机填充未选的卡槽。
        用于选卡数量不足卡槽上限时补满。
        """
        logger.info("[注入] PickRandomSeeds 随机填满卡槽")
        code = bytearray()
        # mov eax, [PVZ_BASE]
        code += b'\xA1' + struct.pack('<I', PVZ_BASE)
        # mov eax, [eax + SELECT_CARD_UI_OFFSET]  ; eax = SelectCardUi_p
        code += b'\x8B\x80' + struct.pack('<I', SELECT_CARD_UI_OFFSET)
        # push eax
        code += b'\x50'
        # mov ecx, FUNC_PICK_RANDOM_SEEDS
        code += b'\xB9' + struct.pack('<I', FUNC_PICK_RANDOM_SEEDS)
        # call ecx
        code += b'\xFF\xD1'
        # ret
        code += b'\xC3'
        self._inject_and_execute(bytes(code))
        time.sleep(0.5)  # 等待随机选卡动画完成

    def rock(self) -> None:
        """选卡界面: 开始游戏 (等价点"一起摇摆吧！"按钮).

        对应 AsmVsZombies AAsm::Rock (avz_asm.cpp:111-123)。
        调用后游戏从选卡界面进入战斗。卡组未满时调用可能无效，
        建议先 pick_random_seeds 补满。
        """
        logger.info("[注入] Rock 开始游戏")
        code = bytearray()
        # mov ebx, [PVZ_BASE]
        code += b'\x8B\x1D' + struct.pack('<I', PVZ_BASE)
        # mov ebx, [ebx + SELECT_CARD_UI_OFFSET]  ; ebx = SelectCardUi_p
        code += b'\x8B\x9B' + struct.pack('<I', SELECT_CARD_UI_OFFSET)
        # mov esi, [PVZ_BASE]  (Rock 内部用 esi=PvzBase)
        code += b'\x8B\x35' + struct.pack('<I', PVZ_BASE)
        # mov edi, 1
        code += b'\xBF\x01\x00\x00\x00'
        # mov ebp, 1
        code += b'\xBD\x01\x00\x00\x00'
        # mov eax, FUNC_ROCK
        code += b'\xB8' + struct.pack('<I', FUNC_ROCK)
        # call eax
        code += b'\xFF\xD0'
        # ret
        code += b'\xC3'
        self._inject_and_execute(bytes(code))
        time.sleep(0.05)

    def grid_to_pixel(self, row: int, col: int) -> tuple[int, int]:
        """调用游戏内部 GridToAbscissa/Ordinate 获取格子中心像素坐标.

        这比用公式近似计算更准确，特别是屋顶场景的斜坡偏移。

        注意: GridToAbscissa/Ordinate 返回格子左上角坐标，需各 +40 得到
        格子中心 (与 AvZ AGridToCoordinate 一致):
            x = GridToAbscissa(row, col) + 40
            y = GridToOrdinate(row, col) + 40

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
            x = self._mem.read_int(RESULT_X_ADDR) + 40  # 格子左边缘 → 格子中心
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

    def set_main_loop_blocked(self, on: bool) -> None:
        """开启/关闭游戏主循环冻结，用于 Agent 思考期间暂停游戏。"""
        if on:
            self.enable_hack("block_main_loop", HACK_BLOCK_MAIN_LOOP)
            logger.info("[注入] 游戏主循环 已冻结")
        else:
            self.disable_hack("block_main_loop", HACK_BLOCK_MAIN_LOOP)
            logger.info("[注入] 游戏主循环 已恢复")

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

    def _deduct_sun(self, cost: int) -> None:
        """手动扣除阳光.

        PutPlant 内部函数只创建植物对象，不走 UI 逻辑，不扣阳光。
        阳光地址: [[PVZ_BASE] + BOARD_OFFSET] + sun_offset
        """
        try:
            board_ptr = self._mem.read_pointer(PVZ_BASE + BOARD_OFFSET)
            if board_ptr == 0:
                logger.warning("[注入] Board* 为空，无法扣阳光")
                return
            sun_addr = board_ptr + self._mem.offsets.sun
            current_sun = self._mem.read_int(sun_addr)
            new_sun = max(0, current_sun - cost)
            self.write_int(sun_addr, new_sun)
            logger.debug("[注入] 阳光 {} → {} (-{})", current_sun, new_sun, cost)
        except Exception as exc:
            logger.warning("[注入] 扣除阳光失败: {}", exc)

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
