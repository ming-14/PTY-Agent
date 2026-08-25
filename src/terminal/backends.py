"""终端模拟后端 —— wezterm-py 终端仿真引擎

终端模型统一抽象：可见区/scrollback 都表示为「行 × 单元格」的稀疏网格，
单元格是引擎原生元组 (col, data, fg, bg, bold, italic, underline, reverse,
strikethrough, width)，渲染（纯文本 / 带 SGR 颜色 / 光标序列）下沉
pywezterm 绑定层完成，宿主仅透传/查询，不手写终端渲染与 VT 嗅探。

实现：
- WeztermBackend：包装 pywezterm.Terminal（wezterm-term 终端模型），
  唯一后端，提供与 wezterm 完全一致的 VT 解析/光标/scrollback 语义。
  TerminalScreen（screen.py）作为门面，通过 create_backend() 创建后端，
  对外保持稳定 API，业务层不感知具体引擎。
"""

import os
import sys

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS
from ..logging import get_logger

_logger = get_logger("pty-session")

# 加载 vendored pywezterm（bin/pywezterm，BUILD.ps1 编译产出），先注入 sys.path
_here = os.path.dirname(os.path.abspath(__file__))
_BIN_DIR = os.path.normpath(os.path.join(_here, *([os.path.pardir] * 2), "bin"))
_WEZTERM_PY_DIR = os.path.join(_BIN_DIR, "pywezterm")
if os.path.isdir(_WEZTERM_PY_DIR) and _WEZTERM_PY_DIR not in sys.path:
    sys.path.insert(0, _WEZTERM_PY_DIR)

try:
    import pywezterm

    _HAS_WEZTERM = True
except ImportError:
    _HAS_WEZTERM = False
    pywezterm = None  # type: ignore[assignment]

# scrollback 历史行上限（与 screen.py 保持一致：30000 行，过长输出按
# "万行尾缓冲"截断的问题缓解，前部历史仍由 100MB OutputBuffer 兜底）
_DEFAULT_HLIMIT = 30000

# 单元格元组字段下标（与 wezterm-py snapshot() 返回的 CellTuple 一致）：
# (col, data, fg, bg, bold, italic, underline, reverse, strikethrough, width)
_CELL_COL = 0
_CELL_DATA = 1
_CELL_FG = 2
_CELL_BG = 3
_CELL_BOLD = 4
_CELL_ITALIC = 5
_CELL_UNDERLINE = 6
_CELL_REVERSE = 7
_CELL_STRIKE = 8


def is_default_cell(cell) -> bool:
    """是否默认空白单元格（无字符、无样式）"""
    return (
        (not cell[_CELL_DATA] or cell[_CELL_DATA] == " ")
        and (not cell[_CELL_FG] or cell[_CELL_FG] == "default")
        and (not cell[_CELL_BG] or cell[_CELL_BG] == "default")
        and not cell[_CELL_BOLD]
        and not cell[_CELL_ITALIC]
        and not cell[_CELL_UNDERLINE]
        and not cell[_CELL_REVERSE]
        and not cell[_CELL_STRIKE]
    )


def build_cursor_seq(x, y, visible) -> str:
    """构建光标定位 VT 序列（CSI Pl;Pc H 与显示/隐藏）

    坐标 0-based 输入，输出 1-based（与 xterm.js CPR 一致）。
    光标不可用时返回 ""。
    委托 pywezterm.cursor_seq（下沉绑定层，与 leaf/ptyagent 共用）。
    """
    if x is None or y is None:
        return ""
    return pywezterm.cursor_seq(y, x, visible)


class ScreenBackend:
    """终端模拟后端接口（wezterm 实现遵循的公共契约）

    后端只负责维护终端模型；可见区/scrollback 统一以
    List[List[cell-tuple]] 稀疏网格暴露，渲染由模块级函数完成。
    """

    name = "base"

    # —— 生命周期 ——
    def feed(self, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    def feed_text(self, text: str) -> None:  # pragma: no cover
        """feed ANSI 字符串（resize 重建用）"""
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def reset(self) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def available(self) -> bool:  # pragma: no cover
        raise NotImplementedError

    @property
    def cols(self) -> int:  # pragma: no cover
        raise NotImplementedError

    @property
    def rows(self) -> int:  # pragma: no cover
        raise NotImplementedError

    # —— 底层引擎访问 ——
    @property
    def emulator(self):  # pragma: no cover
        """底层终端模拟引擎对象（供输入编码共享模式状态；不适用时返回 None）"""
        raise NotImplementedError

    def drain_terminal_response(self) -> bytes:  # pragma: no cover
        """取走终端引擎生成的应答字节（终端查询如 DA1/CPR 的回复）

        feed() 处理后，引擎可能生成需回写子进程的应答序列（如 DA1/CPR/
        XTGETTCAP/OSC 颜色查询）。上层 reader 循环调用本方法取走并写回 PTY。
        不适用时返回 b""。
        """
        return b""

    # —— 数据查询 ——
    def cells(self):  # pragma: no cover
        """可见区稀疏网格：List[List[cell-tuple]]（引擎原生元组，勿构造中间对象）"""
        raise NotImplementedError

    def cursor(self):  # pragma: no cover
        """光标位置 (x, y, visible)，0-based"""
        raise NotImplementedError

    def capture_scrollback(self) -> str:  # pragma: no cover
        raise NotImplementedError

    def clear_scrollback(self) -> None:  # pragma: no cover
        raise NotImplementedError

    @property
    def scrollback_lines_count(self) -> int:  # pragma: no cover
        raise NotImplementedError

    # —— 选区（阶段4：wezterm 后端透传 pywezterm.Terminal.selection_*）——
    def selection_set(self, anchor_row, anchor_col, end_row, end_col):  # pragma: no cover
        raise NotImplementedError

    def selection_select_word(self, row, col):  # pragma: no cover
        raise NotImplementedError

    def selection_select_line(self, row, col):  # pragma: no cover
        raise NotImplementedError

    def selection_text(self):  # pragma: no cover
        raise NotImplementedError

    def selection_active(self):  # pragma: no cover
        raise NotImplementedError

    def selection_clear(self):  # pragma: no cover
        raise NotImplementedError

    def set_clipboard_callback(self, callback):  # pragma: no cover
        """OSC 52 剪贴板写回调；底层 Terminal 收到 OSC 52 时调用 callback(selection, data)"""
        raise NotImplementedError


class WeztermBackend(ScreenBackend):
    """wezterm-py 后端（wezterm-term 终端模型）"""

    name = "wezterm"

    def __init__(self, cols: int, rows: int, hlimit: int = _DEFAULT_HLIMIT):
        self._cols = cols
        self._rows = rows
        self._hlimit = hlimit
        self._term = None
        if _HAS_WEZTERM:
            try:
                self._term = pywezterm.Terminal(cols=cols, rows=rows, scrollback=hlimit)
            except Exception as e:
                _logger.warning("WeztermBackend 初始化失败: %s", e)
                self._term = None

    @property
    def available(self) -> bool:
        return self._term is not None

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def emulator(self):
        """wezterm-py Terminal（输入编码与终端模型共享同一实例，模式状态一致）"""
        return self._term

    def drain_terminal_response(self) -> bytes:
        """取走 wezterm-term 生成的应答字节（DA1/CPR/XTGETTCAP/OSC 等查询回复）"""
        if self._term is None:
            return b""
        return self._term.drain_written()

    def feed(self, data: bytes) -> None:
        if self._term is None:
            return
        self._term.feed(data)

    def feed_text(self, text: str) -> None:
        if self._term is None or not text:
            return
        self._term.feed(text.encode("utf-8", "replace"))

    def resize(self, cols: int, rows: int) -> None:
        if self._term is None:
            return
        self._term.resize(cols, rows)
        self._cols = cols
        self._rows = rows

    def reset(self) -> None:
        if self._term is None:
            return
        self._term.reset()

    def cells(self):
        if self._term is None:
            return []
        # 直接返回引擎原生网格元组，渲染层按下标消费（避免逐 cell 转换）
        return self._term.snapshot()

    def cursor(self):
        if self._term is None:
            return (None, None, None)
        # wezterm 返回 (row, col, visible)
        y, x, visible = self._term.cursor()
        return (x, y, visible)

    def capture_scrollback(self, keep_ansi: bool = False) -> str:
        if self._term is None:
            return ""
        # 下沉到 pywezterm 绑定层渲染（render_scrollback）
        return self._term.render_scrollback(keep_ansi=keep_ansi)

    def render_ansi(self, include_cursor: bool = False) -> str:
        """可见屏幕 ANSI 渲染（每行前 CUP 定位 + SGR），下沉 pywezterm 绑定层"""
        if self._term is None:
            return ""
        return self._term.render_ansi(include_cursor=include_cursor)

    def render_plain(self) -> str:
        """可见屏幕纯文本（去尾空白/末尾空行）——pywezterm text()"""
        if self._term is None:
            return ""
        return self._term.text()

    def get_mouse_encoding(self):
        """鼠标追踪模式与 SGR 编码状态：(mode, sgr)，下沉 pywezterm 绑定层"""
        if self._term is None:
            return (0, False)
        return self._term.get_mouse_encoding()

    def mode_restore_seq(self) -> str:
        """终端模式恢复序列（备用屏幕/鼠标/光标/paste），下沉 pywezterm 绑定层"""
        if self._term is None:
            return ""
        return self._term.mode_restore_seq()

    @property
    def scrollback_lines_count(self) -> int:
        if self._term is None:
            return 0
        return self._term.scrollback_count()

    # —— 选区（阶段4：透传 pywezterm.Terminal.selection_*）——
    def selection_set(self, anchor_row, anchor_col, end_row, end_col):
        if self._term is None:
            return
        self._term.selection_set(anchor_row, anchor_col, end_row, end_col)

    def selection_select_word(self, row, col):
        if self._term is None:
            return
        self._term.selection_select_word(row, col)

    def selection_select_line(self, row, col):
        if self._term is None:
            return
        self._term.selection_select_line(row, col)

    def selection_text(self) -> str:
        if self._term is None:
            return ""
        return self._term.selection_text()

    def selection_active(self) -> bool:
        if self._term is None:
            return False
        return bool(self._term.selection_active())

    def selection_clear(self) -> None:
        if self._term is None:
            return
        self._term.selection_clear()

    def set_clipboard_callback(self, callback):
        """OSC 52 剪贴板写回调：底层 Terminal 收到 OSC 52 时调 callback(selection, data)"""
        if self._term is None:
            return
        self._term.set_clipboard_callback(callback)


def create_backend(
    cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS, hlimit: int = _DEFAULT_HLIMIT
):
    """创建 wezterm-py 终端模拟后端

    Returns:
        WeztermBackend 实例；wezterm-py 不可用时返回 None。
    """
    backend = WeztermBackend(cols, rows, hlimit)
    if backend.available:
        return backend
    return None
