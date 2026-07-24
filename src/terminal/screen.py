"""终端屏幕快照 — 使用 pyte 将 VT 序列流解析为用户可见的终端界面文本

核心类 TerminalScreen 维护一个 pyte.Screen 实例，通过 feed() 喂入
PTY 输出的原始 VT 序列字节，pyte 内部解析并维护字符网格。
snapshot() 方法返回当前终端屏幕的可见文本（去除行尾空白和底部空行）。

参考:
- Alacritty: vte crate 解析 VT → Grid<Cell> → display_iter() 渲染
- pyte: 纯 Python VT102 终端模拟器，Screen 维护字符网格，Stream 解析 VT 序列
"""

import threading
import logging

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS

_logger = logging.getLogger("pty-session")

try:
    import pyte
    from .grid_screen import GridScreen
    _HAS_PYTE = True
except ImportError:
    _HAS_PYTE = False
    GridScreen = None  # type: ignore[assignment]
    _logger.warning("pyte 未安装，终端屏幕快照功能不可用。安装: pip install pyte")

# scrollback 历史行上限（参考 tmux default history-limit 2000，这里给更大值）
_DEFAULT_HLIMIT = 10000


class TerminalScreen:
    """终端屏幕快照管理器

    线程安全地维护一个 pyte.Screen 实例，将 PTY 输出的 VT 序列
    解析为字符网格，并提供 snapshot() 方法获取用户真正看到的终端界面文本。

    用法:
        screen = TerminalScreen(cols=120, rows=30)
        screen.feed(raw_vt_bytes)    # 每次 reader 线程读到数据时调用
        text = screen.snapshot()     # 获取当前屏幕快照
    """

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS,
                 hlimit: int = _DEFAULT_HLIMIT):
        self._cols = cols
        self._rows = rows
        self._hlimit = hlimit
        self._lock = threading.Lock()
        self._screen = None
        self._stream = None
        self._feed_count = 0
        self._feed_bytes = 0
        self._feed_errors = 0
        self._change_event = threading.Event()
        if _HAS_PYTE:
            self._init_screen()

    def _init_screen(self):
        try:
            # 使用 GridScreen（继承 pyte.Screen，带 tmux 风格 scrollback 管理）
            # pyte.Stream 兼容 GridScreen（仍是 pyte.Screen 子类）
            self._screen = GridScreen(self._cols, self._rows, hlimit=self._hlimit)
            self._stream = pyte.Stream(self._screen)
        except Exception as e:
            _logger.warning("TerminalScreen 初始化失败: %s", e)
            self._screen = None
            self._stream = None

    @property
    def available(self) -> bool:
        return self._screen is not None and self._stream is not None

    @property
    def cols(self) -> int:
        return self._cols

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def feed_count(self) -> int:
        return self._feed_count

    def wait_for_change(self, timeout: float = 0.5, prior_count: int = -1) -> bool:
        """等待 screen 内容变化（feed 被调用）

        Args:
            timeout: 等待超时（秒）。
            prior_count: 进入前已知的 feed_count，若当前已不同则立即返回。

        Returns:
            True 表示检测到新 feed（feed_count 变化），False 表示超时。
        """
        if self._feed_count != prior_count:
            return True
        self._change_event.clear()
        if self._feed_count != prior_count:
            self._change_event.set()
            return True
        result = self._change_event.wait(timeout)
        self._change_event.set()
        return result

    def feed(self, data: bytes) -> None:
        if not self._screen or not self._stream:
            return
        self._feed_count += 1
        self._feed_bytes += len(data)
        # 诊断日志：记录每次 feed 的摘要（识别 ConPTY repaint vs 用户输出）
        # ConPTY repaint 通常含 \x1b[?25h（显示光标）、\x1b[row;colH（光标定位）、
        # 大量 SGR 序列 + 部分行内容（非完整屏幕）
        try:
            preview = data[:120].decode('utf-8', errors='replace')
            preview = preview.replace('\r', '\\r').replace('\n', '\\n').replace('\x1b', '\\e')
            _logger.debug("feed: count=%d bytes=%d preview=%r", self._feed_count, len(data), preview)
        except Exception:
            pass
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return
        with self._lock:
            try:
                self._stream.feed(text)
            except Exception as e:
                self._feed_errors += 1
                _logger.debug("pyte feed 异常（可忽略）: %s", e)
        self._change_event.set()

    def feed_snapshot(self, snapshot: str) -> None:
        """将之前的屏幕快照（含 VT 颜色序列）写入 pyte，用于 resize 后重建屏幕

        与 feed() 不同，此方法直接接收字符串（非 bytes），用于在 resize 后
        将保留的快照内容写入新尺寸的 pyte screen。pyte 会解析其中的 VT 序列
        （SGR 颜色、光标移动等）并正确重建字符网格。
        """
        if not self._screen or not self._stream or not snapshot:
            return
        with self._lock:
            try:
                self._stream.feed(snapshot)
            except Exception as e:
                _logger.debug("feed_snapshot 异常: %s", e)

    def snapshot(self, keep_ansi: bool = False, include_cursor: bool = False) -> str:
        if not self._screen:
            return ""
        with self._lock:
            try:
                if keep_ansi:
                    return self._render_with_colors(include_cursor=include_cursor)
                else:
                    text = self._render()
                    if include_cursor:
                        text += self._build_cursor_seq()
                    return text
            except Exception as e:
                _logger.warning("snapshot 渲染失败: %s", e)
                return ""

    def _build_cursor_seq(self) -> str:
        """构建光标定位 VT 序列（CSI Pl;Pc H 与显示/隐藏）

        pyte 的 cursor.x / cursor.y 是 0-based，xterm.js 的 CPR 也是 1-based，
        因此输出时 +1。若光标隐藏则追加 ?25l。

        Returns:
            形如 "\\x1b[10;5H\\x1b[?25h" 的序列；无光标信息时返回 ""。
        """
        try:
            cursor = self._screen.cursor
            if not cursor:
                return ""
            seq = f"\x1b[{cursor.y + 1};{cursor.x + 1}H"
            # 光标显示/隐藏（?25h 显示，?25l 隐藏）
            if getattr(cursor, "hidden", False):
                seq += "\x1b[?25l"
            else:
                seq += "\x1b[?25h"
            return seq
        except Exception:
            return ""

    def get_cursor_location(self) -> dict:
        """获取光标位置及所在行内容

        Returns:
            {"col": int, "row": int, "line": str} — 坐标 1-based；
            无 pyte screen 时返回 {"col": 0, "row": 0, "line": ""}。
        """
        if not self._screen:
            return {"col": 0, "row": 0, "line": ""}
        with self._lock:
            try:
                cursor = self._screen.cursor
                if not cursor:
                    return {"col": 0, "row": 0, "line": ""}
                col = cursor.x + 1
                row = cursor.y + 1
                line = ""
                display = self._screen.display
                if 0 <= cursor.y < len(display):
                    line = display[cursor.y].rstrip()
                return {"col": col, "row": row, "line": line}
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
        if not self._screen:
            return ""
        with self._lock:
            return self._build_cursor_seq()

    def capture_scrollback(self) -> str:
        """捕获 scrollback 历史区为 ANSI 字符串（带 SGR 颜色）

        Phase 2 新增：委托给 GridScreen.capture_scrollback()。
        用于 subscribe 响应时前端恢复 scrollback 历史（Phase 3 启用）。

        Returns:
            每行 ANSI 内容 + \\r\\n 的字符串；无 scrollback 时返回 ""。
        """
        if not self._screen or not hasattr(self._screen, "capture_scrollback"):
            return ""
        with self._lock:
            try:
                return self._screen.capture_scrollback()
            except Exception as e:
                _logger.debug("capture_scrollback 异常: %s", e)
                return ""

    @property
    def scrollback_lines_count(self) -> int:
        """当前 scrollback 行数（供诊断/调试）"""
        if not self._screen or not hasattr(self._screen, "scrollback_lines_count"):
            return 0
        with self._lock:
            try:
                return self._screen.scrollback_lines_count
            except Exception:
                return 0

    def _render(self) -> str:
        lines = []
        try:
            display = self._screen.display
        except Exception:
            return ""
        for line in display:
            try:
                lines.append(line.rstrip())
            except Exception:
                lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def line_text(self, row: int) -> str:
        """获取指定行的可见文本（0-based 行索引）

        Args:
            row: 行索引，从 0 开始。

        Returns:
            该行去除右侧空白后的文本；越界或 pyte 不可用时返回空字符串。
        """
        if not self._screen:
            return ""
        with self._lock:
            try:
                display = self._screen.display
                if 0 <= row < len(display):
                    return display[row].rstrip()
            except Exception:
                pass
        return ""

    _ANSI_FG_NAMES = {
        "black": 0, "red": 1, "green": 2, "brown": 3,
        "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
        "default": 9,
    }
    _ANSI_FG_BRIGHT = {
        "brightblack": 8, "brightred": 9, "brightgreen": 10,
        "brightbrown": 11, "brightblue": 12, "brightmagenta": 13,
        "brightcyan": 14, "brightwhite": 15,
    }

    def _render_with_colors(self, include_cursor: bool = False) -> str:
        """生成带 VT 颜色序列的屏幕快照

        v6 fix（A2）: 每行前显式定位光标到 (row+1, 1)。
        旧实现用 "\\n".join(lines) 只有 LF，xterm.js 解析 LF 时光标只下移
        一行而列保持不变，导致第二行内容从上一行末尾列开始写入 → 错位。
        改为每行前显式 CUP 序列定位到第 1 列，不依赖行末分隔符，
        格式比 ConPTY repaint 更健壮（即使中间有空行也不会错位）。

        v6 fix（snapshot 体积）: 用 _is_default_cell 跟踪行内是否有非默认字符。
        旧实现用 line_text.strip() 判断空行，但 _char_to_sgr 对默认 cell 返回
        \\x1b[0m（\\x1b 不是空白），导致 strip() 非空、空行被误判为有内容，
        snapshot 包含所有 35 行（~4800 字符）而非仅 4 行内容（~600 字符）。

        v7 fix（中间空行丢失）: 旧逻辑 `row > last_non_empty_row and not has_content`
        会跳过中间的空行（因为 last_non_empty_row 尚未到达最后）。
        改为先收集所有行，找到最后一行非空行后，只截断末尾空行，保留中间空行。
        """
        buf = self._screen.buffer
        rows = self._rows
        cols = self._cols
        # —— 第一遍：收集所有行的内容，同时找到最后一行非空行 ——
        line_results = []  # List[Tuple[int, str]]: (row_index, rendered_line)
        last_non_empty_row = -1
        for row in range(rows):
            line_parts = []
            last_sgr = ""
            has_non_default_char = False
            col = 0
            while col < cols:
                try:
                    char = buf[row][col]
                except (KeyError, IndexError):
                    col += 1
                    continue
                if not char.data:
                    col += 1
                    continue
                # 跟踪是否有非默认字符（用于判断空行）
                if not has_non_default_char and not self._is_default_cell(char):
                    has_non_default_char = True
                sgr = self._char_to_sgr(char)
                if sgr != last_sgr:
                    line_parts.append(sgr)
                    last_sgr = sgr
                line_parts.append(char.data)
                col += 1
            if last_sgr:
                line_parts.append("\x1b[0m")
            rendered = "".join(line_parts)
            line_results.append((row, rendered))
            if has_non_default_char:
                last_non_empty_row = row
        # —— 第二遍：构建 snapshot，只输出到最后一行非空行（含），保留中间空行 ——
        parts = []
        for row, rendered in line_results:
            if row > last_non_empty_row:
                break  # 末尾空行，跳过
            parts.append(f"\x1b[{row + 1};1H")
            parts.append(rendered)
        # 末尾追加光标定位序列（include_cursor=True 时）
        if include_cursor:
            parts.append(self._build_cursor_seq())
        return "".join(parts)

    @classmethod
    def _char_to_sgr(cls, char) -> str:
        attrs = []
        if char.bold:
            attrs.append("1")
        if char.italics:
            attrs.append("3")
        if char.underscore:
            attrs.append("4")
        if char.strikethrough:
            attrs.append("9")
        if char.reverse:
            attrs.append("7")
        fg_sgr = cls._color_to_sgr(char.fg, is_fg=True)
        if fg_sgr:
            attrs.append(fg_sgr)
        bg_sgr = cls._color_to_sgr(char.bg, is_fg=False)
        if bg_sgr:
            attrs.append(bg_sgr)
        if not attrs:
            return "\x1b[0m"
        return f"\x1b[{';'.join(attrs)}m"

    @classmethod
    def _color_to_sgr(cls, color, is_fg: bool) -> str:
        if color == "default":
            return ""
        prefix = "38" if is_fg else "48"
        if color in cls._ANSI_FG_NAMES:
            code = 30 + cls._ANSI_FG_NAMES[color] if is_fg else 40 + cls._ANSI_FG_NAMES[color]
            return str(code)
        if color in cls._ANSI_FG_BRIGHT:
            code = 90 + cls._ANSI_FG_BRIGHT[color] - 8 if is_fg else 100 + cls._ANSI_FG_BRIGHT[color] - 8
            return str(code)
        if isinstance(color, str) and len(color) == 6:
            try:
                r = int(color[0:2], 16)
                g = int(color[2:4], 16)
                b = int(color[4:6], 16)
                return f"{prefix};2;{r};{g};{b}"
            except ValueError:
                return ""
        if isinstance(color, int):
            return f"{prefix};5;{color}"
        return ""

    def export_buffer(self) -> dict:
        """导出字符网格为可序列化字典（供客户端渲染图片用）

        Returns:
            {"cols": int, "rows": int, "lines": [[{"d":str,"f":str,"b":str,"bo":bool}, ...], ...]}
            无 pyte 时返回空 dict。
        """
        if not self._screen:
            return {}
        with self._lock:
            try:
                return self._export_buffer_locked()
            except Exception as e:
                _logger.warning("export_buffer 失败: %s", e)
                return {}

    _DEFAULT_CELL = {"d": " ", "f": "default", "b": "default", "bo": False}

    def _is_default_cell(self, char) -> bool:
        return (
            (not char.data or char.data == " ")
            and (not char.fg or str(char.fg) == "default")
            and (not char.bg or str(char.bg) == "default")
            and not char.bold
        )

    def _export_buffer_locked(self) -> dict:
        buf = self._screen.buffer
        cols = self._cols
        rows = self._rows
        sparse_lines = []
        for row in range(rows):
            cells = []
            for col in range(cols):
                try:
                    char = buf[row][col]
                except (KeyError, IndexError):
                    continue
                if self._is_default_cell(char):
                    continue
                cells.append({
                    "c": col,
                    "d": char.data if char.data else " ",
                    "f": str(char.fg) if char.fg else "default",
                    "b": str(char.bg) if char.bg else "default",
                    "bo": bool(char.bold),
                })
            sparse_lines.append(cells)
        return {"cols": cols, "rows": rows, "lines": sparse_lines}

    def diagnostics(self) -> dict:
        """返回屏幕快照管理器的诊断信息（用于调试空快照等问题）"""
        info = {
            "pyte_available": _HAS_PYTE,
            "screen_initialized": self._screen is not None,
            "stream_initialized": self._stream is not None,
            "cols": self._cols,
            "rows": self._rows,
            "feed_count": self._feed_count,
            "feed_bytes": self._feed_bytes,
            "feed_errors": self._feed_errors,
        }
        if self._screen:
            with self._lock:
                try:
                    display = self._screen.display
                    non_empty = sum(1 for line in display if line and line.strip())
                    info["display_lines"] = len(display)
                    info["non_empty_lines"] = non_empty
                except Exception as e:
                    info["display_error"] = str(e)
                try:
                    info["cursor_position"] = (
                        self._screen.cursor.y, self._screen.cursor.x
                    )
                except Exception as e:
                    info["cursor_error"] = str(e)
                try:
                    if display:
                        info["first_line_preview"] = display[0][:80] if display[0] else "(empty)"
                        if non_empty > 0:
                            for line in display:
                                if line and line.strip():
                                    info["first_content_line"] = line.rstrip()[:120]
                                    break
                except Exception as e:
                    info["content_error"] = str(e)
        return info

    def resize(self, cols: int, rows: int) -> None:
        """调整屏幕尺寸（GridScreen.resize 按 ConPTY 语义重排 + 写回 pyte.buffer）

        resize 后 pyte.buffer 即为 ConPTY 真实可见区状态（内容锚顶、光标绑定
        文本行），snapshot() 直接可用，无需任何"防 sync 覆盖"特殊逻辑。
        """
        if not self._screen:
            return
        with self._lock:
            try:
                self._screen.resize(rows, cols)
                self._cols = cols
                self._rows = rows
            except Exception as e:
                _logger.debug("pyte resize 异常: %s", e)

    def resize_and_reset(self, cols: int, rows: int) -> None:
        """原子地 resize + reset：在一次锁保护内更新尺寸并重建 pyte screen

        避免单独调用 resize() 与 reset() 之间读者线程 feed() 写入旧数据。
        用于 session.resize() 场景：ConPTY resize 后立即清空 pyte buffer，
        让后续 ConPTY repaint 字节以新尺寸干净地重建屏幕。
        """
        with self._lock:
            self._cols = cols
            self._rows = rows
            if _HAS_PYTE:
                self._init_screen()

    def reset(self) -> None:
        with self._lock:
            if _HAS_PYTE:
                self._init_screen()
