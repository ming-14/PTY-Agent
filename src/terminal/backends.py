"""终端模拟后端 —— wezterm-py 终端仿真引擎

终端模型统一抽象：可见区/scrollback 都表示为「行 × 单元格」的稀疏网格，
单元格用 ScreenCell 描述（列索引 + 字符 + 前景/背景色 + 样式 + 宽度）。
渲染（纯文本 / 带 SGR 颜色 / 光标序列）在模块级共享，与具体引擎解耦。

实现：
- WeztermBackend：包装 pywezterm.Terminal（wezterm-term 终端模型），
  唯一后端，提供与 wezterm 完全一致的 VT 解析/光标/scrollback 语义。

TerminalScreen（screen.py）作为门面，通过 create_backend() 创建后端，
对外保持稳定 API，业务层不感知具体引擎。
"""

import logging
import os
import sys
from collections import namedtuple

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS

_logger = logging.getLogger("pty-session")

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

# scrollback 历史行上限（参考 tmux default history-limit 2000，这里给更大值）
_DEFAULT_HLIMIT = 10000

# 单元格：列索引, 字符, 前景, 背景, 粗体, 斜体, 下划线, 反显, 删除线, 宽度
# 与 wezterm-py snapshot() 返回的元组字段顺序一致
ScreenCell = namedtuple(
    "ScreenCell",
    "col data fg bg bold italic underline reverse strikethrough width",
)


# ── SGR 颜色序列化（与 grid.py / 旧 screen.py 逻辑一致，扩展 wezterm "pN" 调色板） ──

_ANSI_FG_NAMES = {
    "black": 0,
    "red": 1,
    "green": 2,
    "brown": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "default": 9,
}
_ANSI_FG_BRIGHT = {
    "brightblack": 8,
    "brightred": 9,
    "brightgreen": 10,
    "brightbrown": 11,
    "brightblue": 12,
    "brightmagenta": 13,
    "brightcyan": 14,
    "brightwhite": 15,
}


def color_to_sgr(color, is_fg: bool) -> str:
    """颜色值转 SGR 序列

    支持 wezterm 调色板索引、RGB hex（#rrggbb）、256 色索引（int）。
    """
    if color == "default":
        return ""
    prefix = "38" if is_fg else "48"
    # wezterm 调色板索引格式 "pN"（N ∈ 0-255，直接映射 256 色）
    if isinstance(color, str) and color.startswith("p") and color[1:].isdigit():
        return f"{prefix};5;{int(color[1:])}"
    if color in _ANSI_FG_NAMES:
        code = 30 + _ANSI_FG_NAMES[color] if is_fg else 40 + _ANSI_FG_NAMES[color]
        return str(code)
    if color in _ANSI_FG_BRIGHT:
        code = (
            90 + _ANSI_FG_BRIGHT[color] - 8
            if is_fg
            else 100 + _ANSI_FG_BRIGHT[color] - 8
        )
        return str(code)
    if isinstance(color, str):
        # wezterm 对 RGB 真彩色返回 "#rrggbb"（带 #，7 字符），同时兼容 6 位 hex
        hex_rgb = color[1:] if color.startswith("#") else color
        if len(hex_rgb) == 6:
            try:
                r = int(hex_rgb[0:2], 16)
                g = int(hex_rgb[2:4], 16)
                b = int(hex_rgb[4:6], 16)
                return f"{prefix};2;{r};{g};{b}"
            except ValueError:
                return ""
    if isinstance(color, int):
        return f"{prefix};5;{color}"
    return ""


def cell_to_sgr(cell: ScreenCell) -> str:
    """将单元格属性转为 SGR 序列"""
    attrs = []
    if cell.bold:
        attrs.append("1")
    if cell.italic:
        attrs.append("3")
    if cell.underline:
        attrs.append("4")
    if cell.reverse:
        attrs.append("7")
    if cell.strikethrough:
        attrs.append("9")
    fg_sgr = color_to_sgr(cell.fg, is_fg=True)
    if fg_sgr:
        attrs.append(fg_sgr)
    bg_sgr = color_to_sgr(cell.bg, is_fg=False)
    if bg_sgr:
        attrs.append(bg_sgr)
    if not attrs:
        return "\x1b[0m"
    return f"\x1b[{';'.join(attrs)}m"


def is_default_cell(cell: ScreenCell) -> bool:
    """是否默认空白单元格（无字符、无样式）"""
    return (
        (not cell.data or cell.data == " ")
        and (not cell.fg or cell.fg == "default")
        and (not cell.bg or cell.bg == "default")
        and not cell.bold
        and not cell.italic
        and not cell.underline
        and not cell.reverse
        and not cell.strikethrough
    )


def build_cursor_seq(x, y, visible) -> str:
    """构建光标定位 VT 序列（CSI Pl;Pc H 与显示/隐藏）

    坐标 0-based 输入，输出 1-based（与 xterm.js CPR 一致）。
    光标不可用时返回 ""。
    """
    if x is None or y is None:
        return ""
    seq = f"\x1b[{y + 1};{x + 1}H"
    seq += "\x1b[?25h" if visible else "\x1b[?25l"
    return seq


def render_plain(cells_rows) -> str:
    """可见屏幕纯文本：每行去尾空白，去掉末尾空行，行间 \\n"""
    lines = []
    for cells in cells_rows:
        lines.append("".join(c.data for c in cells).rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def render_ansi(cells_rows, include_cursor: bool = False, cursor=None) -> str:
    """可见屏幕 ANSI 渲染：每行前 CSI row+1;1H 定位，截断末尾空行

    每行前显式 CUP 定位到第 1 列，不依赖行末分隔符（比 ConPTY repaint
    更健壮，即使中间有空行也不会错位）。末尾可选追加光标定位序列。
    """
    line_results = []  # (row, rendered, has_content)
    last_non_empty = -1
    for row, cells in enumerate(cells_rows):
        line_parts = []
        last_sgr = ""
        has_content = False
        for cell in cells:
            if not cell.data:
                continue
            if not has_content and not is_default_cell(cell):
                has_content = True
            sgr = cell_to_sgr(cell)
            if sgr != last_sgr:
                line_parts.append(sgr)
                last_sgr = sgr
            line_parts.append(cell.data)
        if last_sgr:
            line_parts.append("\x1b[0m")
        rendered = "".join(line_parts)
        line_results.append((row, rendered, has_content))
        if has_content:
            last_non_empty = len(line_results) - 1

    parts = []
    for i, (row, rendered, _) in enumerate(line_results):
        if i > last_non_empty:
            break  # 末尾空行，跳过
        parts.append(f"\x1b[{row + 1};1H")
        parts.append(rendered)
    if include_cursor and cursor is not None:
        parts.append(build_cursor_seq(*cursor))
    return "".join(parts)


def render_scrollback(cells_rows) -> str:
    """scrollback 历史区 ANSI 渲染：每行 ANSI 内容 + \\r\\n（供前端推入 scrollback）"""
    if not cells_rows:
        return ""
    parts = []
    for cells in cells_rows:
        line_parts = []
        last_sgr = ""
        for cell in cells:
            if not cell.data:
                continue
            sgr = cell_to_sgr(cell)
            if sgr != last_sgr:
                line_parts.append(sgr)
                last_sgr = sgr
            line_parts.append(cell.data)
        if last_sgr:
            line_parts.append("\x1b[0m")
        parts.append("".join(line_parts))
        parts.append("\r\n")
    return "".join(parts)


class ScreenBackend:
    """终端模拟后端接口（wezterm 实现遵循的公共契约）

    后端只负责维护终端模型；可见区/scrollback 统一以
    List[List[ScreenCell]] 稀疏网格暴露，渲染由模块级函数完成。
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
        """可见区稀疏网格：List[List[ScreenCell]]"""
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

    @staticmethod
    def _cells_of(grid):
        return [[ScreenCell(*t) for t in row] for row in grid]

    def cells(self):
        if self._term is None:
            return []
        return self._cells_of(self._term.snapshot())

    def cursor(self):
        if self._term is None:
            return (None, None, None)
        # wezterm 返回 (row, col, visible)
        y, x, visible = self._term.cursor()
        return (x, y, visible)

    def capture_scrollback(self) -> str:
        if self._term is None:
            return ""
        return render_scrollback(self._cells_of(self._term.scrollback()))

    def clear_scrollback(self) -> None:
        if self._term is None:
            return
        self._term.clear_scrollback()

    @property
    def scrollback_lines_count(self) -> int:
        if self._term is None:
            return 0
        return self._term.scrollback_count()


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
