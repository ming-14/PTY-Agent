"""终端屏幕快照 — 统一的门面层，将 PTY 输出解析为用户可见的终端界面文本

核心类 TerminalScreen 是对终端模拟后端（见 backends.py）的门面封装：
- 使用 wezterm-py（wezterm-term 终端模型，与 wezterm 行为一致）。
- feed() 喂入 PTY 输出的原始 VT 序列字节，后端解析并维护字符网格。
- snapshot() 返回当前终端屏幕的可见文本（去除行尾空白和底部空行）。

业务层（session / web / input）只依赖本类稳定 API，不感知具体引擎。

参考:
- Alacritty: vte crate 解析 VT → Grid<Cell> → display_iter() 渲染
- wezterm-term: 完整 VT 解析 + 光标/scrollback 语义
"""

import re
import threading

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS
from .backends import (
    _CELL_DATA,
    _CELL_BG,
    _CELL_BOLD,
    _CELL_COL,
    _CELL_FG,
    _HAS_WEZTERM,
    build_cursor_seq,
    create_backend,
    is_default_cell,
    render_ansi,
    render_plain,
)
from ..logging import get_logger

_logger = get_logger("pty-session")

# 备用屏幕切换序列（vim/htop/less 等 TUI 应用）
_ALT_ON_RE = re.compile(rb"\x1b\[\?(1049|1047|47|1048)h")
_ALT_OFF_RE = re.compile(rb"\x1b\[\?(1049|1047|47|1048)l")

# DECSET/DECRST 通用模式序列（用于订阅时向新订阅者恢复终端模式状态）
# 跟踪：鼠标追踪（1000/1002/1003，互斥取最后激活）、SGR 鼠标编码（1006）、
# 光标可见（25）、bracketed paste（2004）、备用屏幕（1049/1047/47，见上）
_DECSET_RE = re.compile(rb"\x1b\[\?(\d+(?:;\d+)*)([hl])")

_ALT_TAIL_WINDOW = 64

# scrollback 历史行上限（参考 tmux default history-limit 2000；10000 行 ≈ 60KB
# 文本，黑盒实测 `seq 1 100000` 仅尾部约万行可取回，前部输出丢失。调至 30000
# 行在"可恢复历史"与"单会话内存（Rust 侧稀疏网格）"间取平衡；原始输出另由
# OutputBuffer（100MB）兜底，仅终端屏幕快照受本上限约束）
_DEFAULT_HLIMIT = 30000


class TerminalScreen:
    """终端屏幕快照管理器（后端可插拔）

    线程安全地维护一个终端模拟后端，将 PTY 输出的 VT 序列解析为字符网格，
    并提供 snapshot() 方法获取用户真正看到的终端界面文本。

    用法:
        screen = TerminalScreen(cols=120, rows=30)
        screen.feed(raw_vt_bytes)    # 每次 reader 线程读到数据时调用
        text = screen.snapshot()     # 获取当前屏幕快照
    """

    def __init__(
        self,
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        hlimit: int = _DEFAULT_HLIMIT,
    ):
        self._cols = cols
        self._rows = rows
        self._hlimit = hlimit
        self._lock = threading.Lock()
        self._backend = None
        self._feed_count = 0
        self._feed_bytes = 0
        self._feed_errors = 0
        self._change_event = threading.Event()
        self._backend = create_backend(cols, rows, hlimit)
        # 备用屏幕状态：feed 时对原始 VT 流跟踪 \x1b[?1049/1047/47/1048 开关
        self._alt_screen = False
        self._alt_tail = b""
        # DECSET 模式状态：订阅时据此生成模式恢复序列（鼠标/光标/paste 等）
        self._mouse_tracking_ps = 0  # 0=关闭，1000/1002/1003
        self._cursor_visible = True
        self._bracketed_paste = False
        self._sgr_mouse = False
        # 渲染缓存：屏幕内容仅随 feed（feed_count）或 resize（cols/rows）变化，
        # 版本键一致时直接复用渲染结果（snapshot 为不可变 str；export_buffer
        # 返回只读 dict，调用方仅序列化/压缩，不原地修改）
        self._snapshot_cache: Optional[tuple] = None
        self._export_cache: Optional[tuple] = None

    @property
    def available(self) -> bool:
        return self._backend is not None and self._backend.available

    @property
    def backend_name(self) -> str:
        """当前使用的后端名（wezterm），供诊断用"""
        return self._backend.name if self._backend else None

    @property
    def emulator(self):
        """底层终端模拟引擎（wezterm-py Terminal 或 None）

        输入编码与终端模型共享同一实例，保证模式状态（应用光标/鼠标上报/
        kitty 键盘编码）一致。仅 wezterm 后端可用。
        """
        if self._backend:
            try:
                return self._backend.emulator
            except Exception:
                return None
        return None

    def drain_terminal_response(self) -> bytes:
        """取走终端模型生成的应答字节（DA1/CPR 等查询回复），供 reader 回写 PTY

        feed() 后引擎可能生成需回写子进程的应答序列；调用本方法取走，
        由调用方写入 PTY 输入管道。无应答或后端不可用时返回 b""。
        """
        if not self.available:
            return b""
        with self._lock:
            try:
                return self._backend.drain_terminal_response()
            except Exception as e:
                _logger.debug("drain_terminal_response 异常: %s", e)
                return b""

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def feed_count(self) -> int:
        return self._feed_count

    def feed(self, data: bytes) -> None:
        if not self.available:
            return
        self._feed_count += 1
        self._feed_bytes += len(data)
        # alt screen 切换检测（尾部窗口拼接处理跨 feed 边界；同窗口多序列取最后者）
        self._alt_tail = (self._alt_tail + data)[-_ALT_TAIL_WINDOW:]
        m_on = _ALT_ON_RE.search(self._alt_tail)
        m_off = _ALT_OFF_RE.search(self._alt_tail)
        if m_on and (not m_off or m_on.end() > m_off.end()):
            self._alt_screen = True
        elif m_off and (not m_on or m_off.end() > m_on.end()):
            self._alt_screen = False
        # DECSET 模式跟踪：鼠标追踪（1000/1002/1003 互斥）、SGR 编码（1006）、
        # 光标可见（25）、bracketed paste（2004）——订阅时恢复新 viewer 的模式状态
        for m in _DECSET_RE.finditer(self._alt_tail):
            params = [int(p) for p in m.group(1).split(b";")]
            enable = m.group(2) == b"h"
            for ps in params:
                if ps in (1000, 1002, 1003):
                    self._mouse_tracking_ps = ps if enable else 0
                elif ps == 1006:
                    self._sgr_mouse = enable
                elif ps == 25:
                    self._cursor_visible = enable
                elif ps == 2004:
                    self._bracketed_paste = enable
        # 诊断日志：记录每次 feed 的摘要（识别 ConPTY repaint vs 用户输出）
        try:
            preview = data[:120].decode("utf-8", errors="replace")
            preview = (
                preview.replace("\r", "\\r").replace("\n", "\\n").replace("\x1b", "\\e")
            )
            _logger.debug(
                "feed: count=%d bytes=%d preview=%r",
                self._feed_count,
                len(data),
                preview,
            )
        except Exception:
            pass
        with self._lock:
            try:
                self._backend.feed(data)
            except Exception as e:
                self._feed_errors += 1
                _logger.debug("feed 异常（可忽略）: %s", e)
        self._change_event.set()

    def snapshot(self, keep_ansi: bool = False, include_cursor: bool = False) -> str:
        if not self.available:
            return ""
        with self._lock:
            try:
                # 版本键：内容仅随 feed/resize 变化（与快照渲染脏检查同源），
                # 键一致直接返回缓存，避免整屏重渲染（render_ansi 含 CUP 定位）
                key = (self._feed_count, self._cols, self._rows, keep_ansi, include_cursor)
                cached = self._snapshot_cache
                if cached is not None and cached[0] == key:
                    return cached[1]
                cells = self._backend.cells()
                if keep_ansi:
                    cursor = self._backend.cursor() if include_cursor else None
                    text = render_ansi(
                        cells, include_cursor=include_cursor, cursor=cursor
                    )
                else:
                    text = render_plain(cells)
                    if include_cursor:
                        text += build_cursor_seq(*self._backend.cursor())
                self._snapshot_cache = (key, text)
                return text
            except Exception as e:
                _logger.warning("snapshot 渲染失败: %s", e)
                return ""

    def get_cursor_location(self) -> dict:
        """获取光标位置及所在行内容

        Returns:
            {"col": int, "row": int, "line": str} — 坐标 1-based；
            后端不可用时返回 {"col": 0, "row": 0, "line": ""}。
        """
        if not self.available:
            return {"col": 0, "row": 0, "line": ""}
        with self._lock:
            try:
                x, y, _ = self._backend.cursor()
                line = ""
                cells = self._backend.cells()
                if 0 <= y < len(cells):
                    line = "".join(c[_CELL_DATA] for c in cells[y]).rstrip()
                return {"col": x + 1, "row": y + 1, "line": line}
            except Exception:
                return {"col": 0, "row": 0, "line": ""}

    def get_cursor_seq(self) -> str:
        """获取光标定位 VT 序列（公开接口，供会话层订阅时附加到 replay 末尾）

        v6 fix: 切换会话再切回时前端 replayPending 会 term.clear()+write(replay)，
        replay 是原始输出缓冲区，可能不以光标定位序列结尾，
        导致前端写入后光标停在 replay 末尾而非 PTY 真实位置。
        在 replay 末尾追加此序列可强制光标定位到正确位置。

        Returns:
            形如 "\\x1b[10;5H\\x1b[?25h" 的序列；无光标信息时返回 ""。
        """
        if not self.available:
            return ""
        with self._lock:
            try:
                return build_cursor_seq(*self._backend.cursor())
            except Exception:
                return ""

    def cursor_position(self) -> tuple:
        """光标位置 (x, y, visible)，0-based；后端不可用时返回 (None, None, None)

        供会话层 resize 诊断日志使用（替代直接访问后端内部 cursor 对象）。
        """
        if not self.available:
            return (None, None, None)
        with self._lock:
            try:
                return self._backend.cursor()
            except Exception:
                return (None, None, None)

    def is_alt_screen(self) -> bool:
        """备用屏幕是否激活（\x1b[?1049/1047/47/1048 开关序列跟踪）

        供外部（插件等）判断 TUI 应用（vim/htop/less）是否处于备用屏幕。
        任意线程可调用。
        """
        return self._alt_screen

    def is_mouse_tracking(self) -> bool:
        """TUI 应用是否激活鼠标追踪（\x1b[?1000/1002/1003 跟踪）

        供 web 订阅响应携带当前鼠标模式，前端据此恢复鼠标输入状态。
        """
        return self._mouse_tracking_ps > 0

    def mode_restore_seq(self) -> str:
        """生成终端模式恢复序列（新订阅者重建 xterm 状态用）

        网页刷新/重连后 xterm 实例重建，replay 只含屏幕内容，不含模式状态：
        鼠标追踪、光标可见性、bracketed paste、备用屏幕等全部回到默认。
        本方法根据 feed 时跟踪的 DECSET 状态生成恢复前缀，
        订阅响应将其拼在 replay 前，前端 xterm 与应用模式状态一致。

        Returns:
            模式恢复 VT 序列（如 "\\x1b[?1049h\\x1b[?1002h\\x1b[?25h"），无模式时为 ""。
        """
        parts = []
        if self._alt_screen:
            parts.append("\x1b[?1049h")
        if self._mouse_tracking_ps:
            parts.append("\x1b[?%dh" % self._mouse_tracking_ps)
            if self._sgr_mouse:
                parts.append("\x1b[?1006h")
        if self._bracketed_paste:
            parts.append("\x1b[?2004h")
        if not self._cursor_visible:
            parts.append("\x1b[?25l")
        return "".join(parts)

    def capture_scrollback(self, keep_ansi: bool = False) -> str:
        """捕获 scrollback 历史区

        keep_ansi=True: 每行 ANSI 内容 + \\r\\n（供前端恢复 scrollback）。
        keep_ansi=False: 纯文本（行间 \\n）。

        Returns:
            字符串；无 scrollback 时返回 ""。
        """
        if not self.available:
            return ""
        with self._lock:
            try:
                return self._backend.capture_scrollback(keep_ansi=keep_ansi)
            except Exception as e:
                _logger.debug("capture_scrollback 异常: %s", e)
                return ""

    def clear_scrollback(self) -> None:
        """清除后端 scrollback 历史区

        resize 后 ConPTY repaint 可能触发 index() 将可见区顶部行推入
        scrollback，导致 scrollback 与 snapshot 内容重叠。
        resize 场景下 snapshot 已包含完整可见区，scrollback 是冗余的，
        清除后由后续正常输出滚动重新产生。
        """
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.clear_scrollback()
            except Exception as e:
                _logger.debug("clear_scrollback 异常: %s", e)

    @property
    def scrollback_lines_count(self) -> int:
        """当前 scrollback 行数（供诊断/调试）"""
        if not self.available:
            return 0
        with self._lock:
            try:
                return self._backend.scrollback_lines_count
            except Exception:
                return 0

    def line_text(self, row: int) -> str:
        """获取指定行的可见文本（0-based 行索引）

        Args:
            row: 行索引，从 0 开始。

        Returns:
            该行去除右侧空白后的文本；越界或后端不可用时返回空字符串。
        """
        if not self.available:
            return ""
        with self._lock:
            try:
                cells = self._backend.cells()
                if 0 <= row < len(cells):
                    return "".join(c[_CELL_DATA] for c in cells[row]).rstrip()
            except Exception:
                pass
        return ""

    # ── 选区 / 剪贴板（阶段4：透传 wezterm 后端 selection_*）────────────────

    def selection_set(self, anchor_row, anchor_col, end_row, end_col) -> None:
        """区域选择：anchor → end（stable 行坐标，跨 scrollback 与可见区）"""
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.selection_set(
                    anchor_row, anchor_col, end_row, end_col
                )
            except Exception as e:
                _logger.debug("selection_set 异常: %s", e)

    def selection_select_word(self, row, col) -> None:
        """双击选词（stable 行坐标）"""
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.selection_select_word(row, col)
            except Exception as e:
                _logger.debug("selection_select_word 异常: %s", e)

    def selection_select_line(self, row, col) -> None:
        """三击选行（stable 行坐标）"""
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.selection_select_line(row, col)
            except Exception as e:
                _logger.debug("selection_select_line 异常: %s", e)

    def selection_text(self) -> str:
        """当前选区纯文本（无选区返回空串）"""
        if not self.available:
            return ""
        with self._lock:
            try:
                return self._backend.selection_text()
            except Exception as e:
                _logger.debug("selection_text 异常: %s", e)
                return ""

    def selection_active(self) -> bool:
        """是否有活动选区"""
        if not self.available:
            return False
        with self._lock:
            try:
                return bool(self._backend.selection_active())
            except Exception:
                return False

    def selection_clear(self) -> None:
        """清除选区"""
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.selection_clear()
            except Exception as e:
                _logger.debug("selection_clear 异常: %s", e)

    def set_clipboard_callback(self, callback) -> None:
        """OSC 52 剪贴板写回调：底层 Terminal 收到 OSC 52 时调 callback(selection, data)

        回调在终端 feed 的调用线程（reader）中执行，仅做轻量转发（如入队/推送），
        禁止在回调内查询本 screen 或长时间阻塞。
        """
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.set_clipboard_callback(callback)
            except Exception as e:
                _logger.debug("set_clipboard_callback 异常: %s", e)

    def export_buffer(self) -> dict:
        """导出字符网格为可序列化字典（供客户端渲染图片用）

        Returns:
            {"cols": int, "rows": int, "lines": [[{"c":col,"d":str,"f":str,"b":str,"bo":bool}, ...], ...]}
            后端不可用时返回空 dict。
        """
        if not self.available:
            return {}
        with self._lock:
            try:
                key = (self._feed_count, self._cols, self._rows)
                cached = self._export_cache
                if cached is not None and cached[0] == key:
                    return cached[1]
                cells_rows = self._backend.cells()
                sparse_lines = []
                for cells in cells_rows:
                    line_cells = []
                    for cell in cells:
                        if is_default_cell(cell):
                            continue
                        line_cells.append(
                            {
                                "c": cell[_CELL_COL],
                                "d": cell[_CELL_DATA] if cell[_CELL_DATA] else " ",
                                "f": cell[_CELL_FG] if cell[_CELL_FG] else "default",
                                "b": cell[_CELL_BG] if cell[_CELL_BG] else "default",
                                "bo": bool(cell[_CELL_BOLD]),
                            }
                        )
                    sparse_lines.append(line_cells)
                result = {"cols": self._cols, "rows": self._rows, "lines": sparse_lines}
                self._export_cache = (key, result)
                return result
            except Exception as e:
                _logger.warning("export_buffer 失败: %s", e)
                return {}

    def diagnostics(self) -> dict:
        """返回屏幕快照管理器的诊断信息（用于调试空快照等问题）"""
        info = {
            "wezterm_available": _HAS_WEZTERM,
            "backend": self.backend_name,
            "available": self.available,
            "cols": self._cols,
            "rows": self._rows,
            "feed_count": self._feed_count,
            "feed_bytes": self._feed_bytes,
            "feed_errors": self._feed_errors,
        }
        if not self.available:
            return info
        with self._lock:
            try:
                cells = self._backend.cells()
                non_empty = sum(
                    1 for row in cells if any(not is_default_cell(c) for c in row)
                )
                info["display_lines"] = len(cells)
                info["non_empty_lines"] = non_empty
            except Exception as e:
                info["display_error"] = str(e)
            try:
                x, y, _ = self._backend.cursor()
                info["cursor_position"] = (y, x)
            except Exception as e:
                info["cursor_error"] = str(e)
            try:
                if cells:
                    first_text = "".join(c[_CELL_DATA] for c in cells[0]).rstrip()
                    info["first_line_preview"] = (
                        first_text[:80] if first_text else "(empty)"
                    )
                    if non_empty > 0:
                        for row in cells:
                            text = "".join(c[_CELL_DATA] for c in row).rstrip()
                            if text.strip():
                                info["first_content_line"] = text[:120]
                                break
            except Exception as e:
                info["content_error"] = str(e)
        return info

    def resize(self, cols: int, rows: int) -> None:
        """调整屏幕尺寸（wezterm-py：Terminal.resize 原生 reflow）"""
        if not self.available:
            return
        with self._lock:
            try:
                self._backend.resize(cols, rows)
                self._cols = cols
                self._rows = rows
            except Exception as e:
                _logger.debug("resize 异常: %s", e)

    def reset(self) -> None:
        with self._lock:
            if not self.available:
                return
            try:
                self._backend.reset()
            except Exception as e:
                _logger.debug("reset 异常: %s", e)
