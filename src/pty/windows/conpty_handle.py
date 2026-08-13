"""ConPtyHandle — 可独立持有的 ConPTY 句柄组件（WindowsPseudoTerminal / SandboxPty 共用）

抽取自 WindowsPseudoTerminal.__init__ 的 ConPTY 句柄创建与 I/O 逻辑：
创建双匿名管道 + CreatePseudoConsole，持有 hpc 与 inW/outR 读写端。

关键决策点：
  - CreatePseudoConsole 第一参数必须是 COORD 结构体按值传参（win32_api 已
    声明 argtypes=[_COORD, ...]，x64 ABI 下 COORD 打包进寄存器）。若传
    byref(size)，conhost 会把指针地址解析为尺寸，子进程启动静默失败
    （win-sandbox 侧曾以 ab_compare 实证：byref 3/3 bytes=0 vs 值传递 3/3 正常）。
  - 父进程持有的可继承副本 inR/outW 由 CreateProcess 消费后必须关闭
    （discard_inherited_ends），否则句柄泄漏；两个使用者都在 spawn 成功后调用。
  - close() 顺序：CancelIoEx(outR) → ClosePseudoConsole → 关闭管道句柄，
    与 WindowsPseudoTerminal._cleanup 保持一致（取消挂起读优先，防死锁）。
"""

import ctypes
import logging
from ctypes import wintypes as W

from .win32_api import (
    K,
    _CreatePseudoConsole,
    _ResizePseudoConsole,
    _ClosePseudoConsole,
    _ReadFile,
    _WriteFile,
    _CloseHandle,
    _PeekNamedPipe,
    _CancelIoEx,
    _HPCON,
    _COORD,
)

_logger = logging.getLogger("pty-windows")

HANDLE_FLAG_INHERIT = 1


class ConPtyHandle:
    """ConPTY 句柄三件套（hpc + inW/outR）+ I/O 能力

    Args:
        cols: 初始宽度（列数）。
        rows: 初始高度（行数）。
    """

    def __init__(self, cols: int, rows: int):
        self._inR = W.HANDLE()
        self._inW = W.HANDLE()
        self._outR = W.HANDLE()
        self._outW = W.HANDLE()
        self._hpc = None
        self._cols = cols
        self._rows = rows

        K.CreatePipe(ctypes.byref(self._inR), ctypes.byref(self._inW), None, 0)
        K.CreatePipe(ctypes.byref(self._outR), ctypes.byref(self._outW), None, 0)

        # inR/outW 为子进程可继承端（CreateProcess 经 STARTF_USESTDHANDLES 传入）
        K.SetHandleInformation(self._inR, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
        K.SetHandleInformation(self._outW, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)

        self._hpc = _HPCON()
        # COORD 按值传参（argtypes 已声明），x64 下 byref 会导致尺寸解析为
        # 指针地址，ConPTY 子进程启动静默失败
        hr = _CreatePseudoConsole(
            _COORD(cols, rows), self._inR, self._outW, 0,
            ctypes.byref(self._hpc),
        )
        if hr < 0:
            raise OSError(f"CreatePseudoConsole 失败 hr={hr:#x}")
        _logger.info("ConPtyHandle: CreatePseudoConsole OK hr=%d %dx%d", hr, cols, rows)

    # ── 属性 ──

    @property
    def hpc(self) -> _HPCON:
        """HPCON c_void_p 实例（UpdateProcThreadAttribute 用）"""
        return self._hpc

    @property
    def hpcon_value(self) -> int:
        """HPCON 指针整数值（win-sandbox start_process(hpcon=...) 用）"""
        if self._hpc is None:
            return 0
        return int(self._hpc.value)

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    # ── I/O ──

    def write(self, data):
        """写入 ConPTY 输入管道（str 按 utf-8 编码，与 PseudoTerminal.write 语义一致）"""
        if isinstance(data, str):
            data = data.encode()
        wr = W.DWORD(0)
        _WriteFile(self._inW, data, len(data), ctypes.byref(wr), None)

    def read(self, n: int = 65536) -> bytes:
        """阻塞读取 ConPTY 输出（最多 n 字节）；管道断开返回 b""（EOF）"""
        if not self._outR:
            return b""
        buf = ctypes.create_string_buffer(n)
        br = W.DWORD(0)
        if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
            err = ctypes.get_last_error()
            if err == 109:  # ERROR_BROKEN_PIPE
                _logger.debug("ConPtyHandle: read broken pipe (EOF)")
                return b""
            _logger.warning("ConPtyHandle: ReadFile failed err=%d", err)
            return b""
        return buf.raw[:br.value]

    def drain(self, max_bytes: int = 65536) -> bytes:
        """非阻塞排空当前就绪输出（PeekNamedPipe 循环）"""
        chunks = []
        total = 0
        while self._outR:
            avail = W.DWORD(0)
            ok = _PeekNamedPipe(self._outR, None, 0, None, ctypes.byref(avail), None)
            if not ok or avail.value == 0:
                break
            n = min(avail.value, max_bytes)
            buf = ctypes.create_string_buffer(n)
            br = W.DWORD(0)
            if not _ReadFile(self._outR, buf, n, ctypes.byref(br), None):
                break
            if br.value == 0:
                break
            chunks.append(buf.raw[:br.value])
            total += br.value
        if total:
            _logger.debug("ConPtyHandle: drain %d bytes", total)
        return b"".join(chunks)

    def resize(self, cols: int, rows: int):
        """调整 ConPTY 尺寸（conhost 内部 reflow，宽变时发 repaint）"""
        if self._hpc is None:
            return
        try:
            hr = _ResizePseudoConsole(self._hpc, _COORD(X=cols, Y=rows))
            if hr != 0:
                _logger.warning("ConPtyHandle: ResizePseudoConsole failed hr=0x%08X", hr & 0xFFFFFFFF)
            else:
                self._cols = cols
                self._rows = rows
                _logger.debug("ConPtyHandle: resize %dx%d", cols, rows)
        except Exception as e:
            _logger.warning("ConPtyHandle: resize failed: %s", e)

    # ── 句柄管理 ──

    def discard_inherited_ends(self):
        """关闭父进程持有的可继承副本 inR/outW（spawn 完成后立即调用）"""
        for h in (self._inR, self._outW):
            if h:
                try:
                    _CloseHandle(h)
                except Exception:
                    pass
        self._inR = None
        self._outW = None

    def close(self):
        """统一清理：CancelIoEx → ClosePseudoConsole → 关闭管道句柄（幂等）

        顺序与 WindowsPseudoTerminal._cleanup 保持一致：先取消挂起读，
        再关伪控制台（conhost 退出），最后关管道，避免 reader 死锁。
        """
        if self._outR:
            try:
                _CancelIoEx(self._outR, None)
            except Exception:
                pass
        if self._hpc is not None:
            try:
                _ClosePseudoConsole(self._hpc)
            except Exception as e:
                _logger.warning("ConPtyHandle: ClosePseudoConsole failed: %s", e)
            self._hpc = None
        for h in (self._outR, self._inW):
            if h:
                try:
                    _CloseHandle(h)
                except Exception:
                    pass
        self._outR = None
        self._inW = None
        self.discard_inherited_ends()
