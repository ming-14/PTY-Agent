"""终端网格 — 参考 tmux grid.c 的 scrollback + 可见屏幕数据结构

核心设计（对齐 tmux）：
- Grid.linedata 是一段连续列表，布局为 [scrollback..., visible...]
- hsize 是 scrollback 行数，sy 是可见行数
- 每行 GridLine 持有 cells 列表 + flags（LINE_WRAPPED 标记软换行）
- reflow() 利用 LINE_WRAPPED 标记合并/拆分行，实现 resize 时不丢内容

参考:
- tmux: grid.c (struct grid, grid_reflow, grid_scroll_history)
- Windows Terminal: textBuffer.cpp (TextBuffer::Reflow, WasWrapForced)

与 pyte.Screen 的关系:
- Grid 是纯数据结构，不做 VT 解析
- GridScreen(pyte.Screen) 在 feed 时同步 pyte.buffer 到 Grid
- resize 时 GridScreen 调用 Grid.reflow() 重排，再同步回 pyte
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

_logger = logging.getLogger("pty-grid")

# 行标志位（参考 tmux GRID_LINE_WRAPPED = 0x1）
LINE_WRAPPED = 0x1
"""软换行标记：该行因列宽不足被自动换行（非用户按 Enter）。
reflow 时只有 WRAPPED 链上的连续行才会被合并重排。"""

# ANSI 颜色名 → 16 色码（与 screen.py _ANSI_FG_NAMES 一致）
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


@dataclass
class GridCell:
    """单个字符单元格（对应 tmux grid_cell / pyte Char）

    属性与 pyte.Screen.buffer 中的 Char 完全对齐，
    便于从 pyte.buffer 同步到 Grid。
    """
    data: str = " "          # 字符内容（宽字符的 stub 为空字符串）
    fg: str = "default"      # 前景色: "default" / "red" / "brightblue" / RGB hex / 256-color int
    bg: str = "default"      # 背景色
    bold: bool = False
    italics: bool = False
    underscore: bool = False
    reverse: bool = False
    strikethrough: bool = False

    def is_default(self) -> bool:
        """是否为默认空白 cell（用于测量行宽和跳过空行）"""
        return (
            (not self.data or self.data == " ")
            and (not self.fg or self.fg == "default")
            and (not self.bg or self.bg == "default")
            and not self.bold
            and not self.italics
            and not self.underscore
            and not self.reverse
            and not self.strikethrough
        )

    def copy(self) -> "GridCell":
        return GridCell(
            data=self.data, fg=self.fg, bg=self.bg,
            bold=self.bold, italics=self.italics,
            underscore=self.underscore, reverse=self.reverse,
            strikethrough=self.strikethrough,
        )


@dataclass
class GridLine:
    """一行数据（对应 tmux grid_line）

    cells 长度始终等于 Grid.sx（列宽）。
    flags 记录 LINE_WRAPPED 等标记。
    used 显式内容长度（仅部分填充的 WRAPPED 行设置，见 _content_width）：
        -1 = 未设置：WRAPPED 行内容宽 = len(cells)（换行由列满溢出产生，
             整行含 wrap 边界空格都是内容）；非 WRAPPED 行用 width() 裁剪尾部。
        >= 0 = 真实内容宽（_split_line / _split_long_line 拆出的部分段），
             避免 width() 把 wrap 边界的真实尾部空格当作 padding 裁掉。
    """
    cells: List[GridCell] = field(default_factory=list)
    flags: int = 0
    used: int = -1

    @staticmethod
    def empty(cols: int) -> "GridLine":
        """创建指定宽度的空行"""
        return GridLine(cells=[GridCell() for _ in range(cols)], flags=0)

    @property
    def wrapped(self) -> bool:
        return bool(self.flags & LINE_WRAPPED)

    @wrapped.setter
    def wrapped(self, value: bool) -> None:
        if value:
            self.flags |= LINE_WRAPPED
        else:
            self.flags &= ~LINE_WRAPPED

    def copy(self) -> "GridLine":
        return GridLine(
            cells=[c.copy() for c in self.cells],
            flags=self.flags,
            used=self.used,
        )

    def width(self) -> int:
        """实际非默认内容的宽度（从右向左裁剪默认 cell）

        用于 reflow 时判断行内容是否填满列宽。
        """
        w = len(self.cells)
        while w > 0 and self.cells[w - 1].is_default():
            w -= 1
        return w


class Grid:
    """终端网格：scrollback + 可见屏幕（对应 tmux struct grid）

    布局：
        linedata[0 .. hsize-1]           = scrollback（历史区）
        linedata[hsize .. hsize+sy-1]    = 可见屏幕

    线程安全：Grid 本身不加锁，由调用方（GridScreen）通过 _lock 保护。

    用法:
        grid = Grid(cols=80, rows=24, hlimit=10000)
        grid.scroll_history(captured_line)  # PTY 输出滚动时调用
        snapshot = grid.capture_visible()   # 获取可见屏幕快照
        grid.reflow(132, 24)                # resize 时重排
    """

    def __init__(self, cols: int, rows: int, hlimit: int = 10000):
        self.sx: int = cols
        self.sy: int = rows
        self.hlimit: int = hlimit
        self.hsize: int = 0
        self.linedata: List[GridLine] = [GridLine.empty(cols) for _ in range(rows)]

    @property
    def total_lines(self) -> int:
        """linedata 总长度 = hsize + sy"""
        return len(self.linedata)

    def get_line(self, index: int) -> Optional[GridLine]:
        """按绝对索引获取行（0-based，包含 scrollback）"""
        if 0 <= index < len(self.linedata):
            return self.linedata[index]
        return None

    def get_scrollback_line(self, offset: int) -> Optional[GridLine]:
        """按偏移获取 scrollback 行（0 = 最旧，hsize-1 = 最新）"""
        if 0 <= offset < self.hsize:
            return self.linedata[offset]
        return None

    def get_visible_line(self, row: int) -> Optional[GridLine]:
        """按行号获取可见行（0 = 顶部可见行）"""
        if 0 <= row < self.sy:
            return self.linedata[self.hsize + row]
        return None

    def scroll_history(self, line: GridLine) -> None:
        """追加一行到 scrollback 末尾（紧邻可见区上方）

        参考 tmux grid_scroll_history (grid.c:497-511)。
        超过 hlimit 时裁剪最旧的一行。
        """
        # 在 scrollback 末尾（visible 之前）插入
        self.linedata.insert(self.hsize, line)
        self.hsize += 1
        self._trim_history()

    def _trim_history(self) -> None:
        """裁剪超限 scrollback（参考 tmux grid_trim_history）"""
        while self.hsize > self.hlimit and self.linedata:
            self.linedata.pop(0)
            self.hsize -= 1

    def clear_scrollback(self) -> None:
        """清除所有 scrollback（对应 VT 序列 \\x1b[3J）"""
        del self.linedata[:self.hsize]
        self.hsize = 0

    def clear_all(self) -> None:
        """清除 scrollback + 可见屏幕（对应 term.reset()）"""
        self.linedata = [GridLine.empty(self.sx) for _ in range(self.sy)]
        self.hsize = 0

    def reflow(self, new_cols: int, new_rows: int) -> int:
        """resize 时重排 scrollback + 可见屏幕（ConPTY 语义）

        与 ConPTY（Windows conhost）resize 行为严格对齐：
        - 宽度变化：scrollback 区与 visible 区【各自独立】rewrap（软换行感知，
          不跨边界搬运行 —— ConPTY 没有 scrollback，终端的 scrollback 由终端
          自己维护，reflow 不应把行在两个区之间转移）。
        - 高度增长（grow）：visible 底部补空行，scrollback 不变，光标不动。
        - 高度收缩（shrink）：优先砍 visible 底部空行（内容/光标不动）；
          底部空行不够砍时，从 visible 顶部取 N 行推入 scrollback
          （内容上移，光标随文本行上移 N 行）。
        - 宽度变窄导致 visible 行数增多时，同样从顶部推入 scrollback。

        旧实现（tmux grid_reflow 风格）把"重排后末尾 new_rows 行"当作可见区，
        等价于内容锚底：grow 时会把 scrollback 的行提升进可见区，prompt 被
        推到屏幕底部。但 ConPTY 锚顶（光标绝对位置不变），两套坐标系错位，
        resize 后 ConPTY 的绝对光标定位（如 \\x1b[24;34H）会落在前端显示的
        输出内容中间 —— "光标在 dir 输出中间" bug 的根因。

        Args:
            new_cols: 新列数
            new_rows: 新行数

        Returns:
            pushed: visible 顶部被推入 scrollback 的行数。
                    调用方据此把 cursor.y 上移 pushed 行（光标绑定文本行）。
        """
        if new_cols == self.sx and new_rows == self.sy:
            return 0

        # 1. 分区取出 scrollback / visible，各自独立 rewrap
        sb_part = list(self.linedata[:self.hsize])
        vs_part = list(self.linedata[self.hsize:])
        if new_cols != self.sx:
            sb_part = _reflow_lines(sb_part, new_cols)
            vs_part = _reflow_lines(vs_part, new_cols)

        # 2. visible 行数调整（ConPTY 语义：锚顶 + 底部空行弹性）
        pushed = 0
        if len(vs_part) > new_rows:
            # 先砍底部空行（内容不动）
            while len(vs_part) > new_rows and _line_is_blank(vs_part[-1]):
                vs_part.pop()
            # 仍超出：顶部行推入 scrollback（内容上移，光标随文本行）
            if len(vs_part) > new_rows:
                pushed = len(vs_part) - new_rows
                sb_part.extend(vs_part[:pushed])
                vs_part = vs_part[pushed:]
        elif len(vs_part) < new_rows:
            # grow：底部补空行，scrollback 保持不变
            vs_part.extend(GridLine.empty(new_cols)
                           for _ in range(new_rows - len(vs_part)))

        self.linedata = sb_part + vs_part
        self.sx = new_cols
        self.sy = new_rows
        self.hsize = len(sb_part)
        self._trim_history()
        return pushed

    def capture_range(self, start: int, end: int,
                      with_position: bool = False) -> str:
        """序列化 [start, end) 行为 ANSI 字符串

        Args:
            start: 起始行索引（绝对，含 scrollback）。负数表示从 visible 倒数。
            end: 结束行索引（不含）。负数表示从 visible 倒数。
            with_position: True 时每行前加 CSI row+1;1H（用于 visible screen 定位）；
                           False 时行间用 \\r\\n 分隔（用于 scrollback 推入）。

        Returns:
            带 SGR 颜色序列的 ANSI 字符串。
        """
        # 处理负数索引（相对于 visible 区）
        total = len(self.linedata)
        if start < 0:
            start = total + start
        if end < 0:
            end = total + end
        start = max(0, start)
        end = min(total, end)

        if start >= end:
            return ""

        # —— 第一遍：收集所有行的内容，同时找到最后一行非空行 ——
        line_results: List[tuple] = []  # (row_idx, rendered_line, has_content)
        last_non_empty_idx = -1

        for row_idx in range(start, end):
            line = self.linedata[row_idx]
            line_parts: List[str] = []
            has_content = False
            last_sgr = ""

            # 只迭代到实际内容宽度（剥除尾部默认空格）。
            # 否则 ConPTY repaint 会把行填充到满宽（如 132 列），导致写入 snapshot
            # 后光标停在列尾（pending wrap），而非 prompt 文本末尾。
            content_w = line.width()
            for col_idx in range(content_w):
                cell = line.cells[col_idx] if col_idx < len(line.cells) else GridCell()
                if not cell.data:
                    continue
                if not has_content and not cell.is_default():
                    has_content = True
                sgr = _cell_to_sgr(cell)
                if sgr != last_sgr:
                    line_parts.append(sgr)
                    last_sgr = sgr
                line_parts.append(cell.data)

            if last_sgr:
                line_parts.append("\x1b[0m")
            rendered = "".join(line_parts)
            line_results.append((row_idx, rendered, has_content))
            if has_content:
                last_non_empty_idx = len(line_results) - 1

        if last_non_empty_idx < 0:
            return ""  # 全部是空行

        # —— 诊断：打印 last_non_empty_idx 对应行内容（resize 光标定位排查）——
        try:
            last_row_idx, last_rendered, _ = line_results[last_non_empty_idx]
            last_visible_row = last_row_idx - self.hsize
            # strip ANSI 用于日志可读
            import re as _re
            last_text = _re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', last_rendered).rstrip()
            _logger.debug(
                "capture_range: last_non_empty_idx=%d visible_row=%d (1-based=%d) text=%r",
                last_non_empty_idx, last_visible_row, last_visible_row + 1,
                last_text[:80])
        except Exception:
            pass

        # —— 第二遍：构建输出，with_position 模式下只截断末尾空行，保留中间空行 ——
        parts: List[str] = []
        for i, (row_idx, rendered, has_content) in enumerate(line_results):
            if with_position and i > last_non_empty_idx:
                break  # 末尾空行，跳过
            if with_position:
                # visible 行：CSI row+1;1H 前缀（row 是 visible 内的行号）
                visible_row = row_idx - self.hsize
                parts.append(f"\x1b[{visible_row + 1};1H")
                parts.append(rendered)
            else:
                # scrollback 行：ANSI 内容 + \r\n
                parts.append(rendered)
                parts.append("\r\n")

        return "".join(parts)

    def capture_scrollback(self) -> str:
        """序列化全部 scrollback 为 ANSI 字符串（用于 subscribe 响应）

        格式：每行 ANSI 内容 + \\r\\n，可直接写入 xterm.js 推入 scrollback 区。
        """
        if self.hsize == 0:
            return ""
        return self.capture_range(0, self.hsize, with_position=False)

    def capture_visible(self, include_cursor: bool = False,
                        cursor_pos: Optional[tuple] = None,
                        cursor_hidden: bool = False) -> str:
        """序列化可见屏幕为 ANSI 字符串（每行前缀 CSI row+1;1H）

        注意: resize 场景的 snapshot 不走此路径 —— resize snapshot 必须
        从 pyte.buffer 渲染（ConPTY 真实状态，见 GridScreen.resize 注释）。
        本方法仅供需要 Grid 视角 visible 的调用方使用。
        """
        result = self.capture_range(self.hsize, self.hsize + self.sy,
                                     with_position=True)
        if include_cursor and cursor_pos is not None:
            y, x = cursor_pos
            result += f"\x1b[{y + 1};{x + 1}H"
            result += "\x1b[?25l" if cursor_hidden else "\x1b[?25h"
        return result


# ── reflow 辅助函数 ──────────────────────────────────────────────

def _line_is_blank(line: GridLine) -> bool:
    """是否空行（无实际内容）。

    shrink 时优先从 visible 底部砍空行（对齐 ConPTY）。
    注意: 不看 WRAPPED 标记 —— 实践中空行不参与 wrap 链。
    """
    return line.width() == 0


def _content_width(line: GridLine) -> int:
    """行的真实内容宽度（reflow 专用，含 wrap 边界的真实尾部空格）

    与 width() 的区别：width() 会裁掉尾部默认 cell（含"看似空白"的真实
    空格），用于渲染裁剪与空行判断；reflow 拼接/拆分需要精确的内容边界：
    - used >= 0：显式记录的内容长度（拆分产生的部分填充 WRAPPED 行）
    - WRAPPED 行：换行由列满溢出产生，整行（含边界空格）都是内容
    - 其它行：width()（尾部默认 cell 是 padding，可裁剪）
    """
    if line.used >= 0:
        return min(line.used, len(line.cells))
    if line.wrapped:
        return len(line.cells)
    return line.width()


def _reflow_lines(old_lines: List[GridLine], new_cols: int) -> List[GridLine]:
    """按新列宽重排一组行（软换行感知合并/拆分，不区分 scrollback/visible）

    算法（参考 tmux grid_reflow / Windows Terminal textbuffer Reflow）:
    1. 遍历所有旧行
    2. 用 LINE_WRAPPED 标志判断是否为同一逻辑行
    3. 按新列宽重新合并/拆分（宽度一律用 _content_width，保留 wrap 边界
       的真实尾部空格，否则 "Microsoft Windows" 会变 "MicrosoftWindows"）:
       - 内容宽 > new_cols: 拆成多行，中间行打 WRAPPED
       - 内容宽 <= new_cols 且有 WRAPPED: 沿链合并后续行，
         合不下时从下一行切一段填满当前行，剩余部分独立成行延续原链
       - 内容宽 <= new_cols 且无 WRAPPED: 独立行
    """
    target: List[GridLine] = []

    old_y = 0
    while old_y < len(old_lines):
        old_line = old_lines[old_y]
        line_width = _content_width(old_line)

        if line_width == 0 and not old_line.wrapped:
            # 空行：直接搬，不合并
            target.append(GridLine.empty(new_cols))
            old_y += 1
            continue

        if line_width > new_cols:
            # 内容超过新列宽：拆分
            target.extend(_split_long_line(old_line, new_cols))
            old_y += 1
            continue

        # 行宽不超过新列宽：沿 WRAPPED 链合并后续行
        merged = _resize_line(old_line, new_cols)
        old_y += 1
        # merged 是否已入列（split 分支内 append 后置位，防止末尾重复 append
        # 导致内容翻倍、wrap 链被劫持）
        chain_appended = False
        while old_y < len(old_lines) and merged.wrapped:
            next_line = old_lines[old_y]
            next_width = _content_width(next_line)
            merged_width = _content_width(merged)
            if next_width == 0:
                # 后续空行：合并结束
                break
            if merged_width + next_width <= new_cols:
                # 可以完整合并
                merged = _merge_lines(merged, next_line, new_cols)
                old_y += 1
                if not next_line.wrapped:
                    # 后续行不是 WRAPPED，合并结束
                    merged.wrapped = False
                    break
            else:
                # 合并后超过新列宽：从 next_line 切一段填满 merged，
                # 剩余部分独立成行（继承 next_line flags，链未断则仍 WRAPPED）
                remaining = new_cols - merged_width
                first_part, second_part = _split_line(next_line, remaining, new_cols)
                merged = _merge_lines(merged, first_part, new_cols)
                target.append(merged)
                # second_part 必非 None：本分支条件保证 next_width > remaining，
                # 即剩余内容非空
                target.append(second_part)
                old_y += 1
                chain_appended = True
                break
        if not chain_appended:
            target.append(merged)

    return target


def _resize_line(line: GridLine, new_cols: int) -> GridLine:
    """调整行宽到 new_cols（内容不变，仅扩展或截断 cells 长度）

    仅在内容宽 <= new_cols 的合并路径调用（截断不发生）。
    关键不变量：WRAPPED 行的 used 必须显式记录内容宽 —— cells 扩展到
    new_cols 后 len(cells) 不再等于内容宽，_content_width 的
    "WRAPPED 行内容宽 = len(cells)" 规则只在 cells 未被扩展时成立。
    """
    new_cells = [GridCell() for _ in range(new_cols)]
    for i in range(min(len(line.cells), new_cols)):
        new_cells[i] = line.cells[i].copy()
    used = line.used
    if used < 0 and line.wrapped:
        # WRAPPED 且 used 未设置：内容宽 = 旧 cells 长度（换行时的行宽）
        used = min(len(line.cells), new_cols)
    return GridLine(cells=new_cells, flags=line.flags, used=used)


def _merge_lines(line1: GridLine, line2: GridLine, new_cols: int) -> GridLine:
    """合并两行：line2 的内容追加到 line1 内容末尾

    line1 必为 WRAPPED 行（只有 WRAPPED 链才会合并），拼接偏移取
    _content_width 而非 width()：width() 会把 wrap 边界的真实尾部空格
    当 padding 裁掉，导致 "Microsoft Windows" 合并成 "MicrosoftWindows"。

    合并结果的 WRAPPED 标志取 line2 的（line2 仍 WRAPPED 说明链未结束）；
    链未结束时用 used 记录真实内容宽，供下一次合并/拆分定位内容边界。
    """
    new_cells = [GridCell() for _ in range(new_cols)]
    w1 = _content_width(line1)
    w2 = _content_width(line2)
    # 复制 line1 内容
    for i in range(min(w1, new_cols, len(line1.cells))):
        new_cells[i] = line1.cells[i].copy()
    # 追加 line2 内容
    for i in range(min(w2, new_cols - w1, len(line2.cells))):
        new_cells[w1 + i] = line2.cells[i].copy()
    # 合并后的 WRAPPED 标志取 line2 的（如果 line2 也是 WRAPPED，说明还有后续）
    result = GridLine(cells=new_cells,
                      flags=line2.flags if line2.wrapped else 0)
    if result.wrapped:
        result.used = min(w1 + w2, new_cols)
    return result


def _split_line(line: GridLine, at: int, new_cols: int) -> tuple:
    """在内容偏移 at 处拆分行，返回 (first_part, second_part)

    first_part：内容 [0, at)，标记 WRAPPED，used=at（部分填充，
        后续合并按 used 定位拼接点，保留 wrap 边界真实尾部空格）。
    second_part：内容 [at, 内容宽)，flags 继承 line（line 仍 WRAPPED
        则 second 也 WRAPPED 延续原链，并用 used 记录剩余内容宽）。
    剩余内容为空时 second_part 为 None。
    """
    content_width = _content_width(line)
    first = GridLine(cells=[GridCell() for _ in range(new_cols)],
                     flags=LINE_WRAPPED, used=at)
    for i in range(min(at, len(line.cells), new_cols)):
        first.cells[i] = line.cells[i].copy()

    remaining_width = content_width - at
    if remaining_width <= 0:
        return first, None

    # line 非 WRAPPED 时 second 是链尾：used=-1（width() 裁剪尾部即可）；
    # line 仍 WRAPPED 时 second 延续原链：显式 used 记录部分填充的内容宽
    second = GridLine(cells=[GridCell() for _ in range(new_cols)],
                      flags=line.flags,
                      used=remaining_width if line.wrapped else -1)
    for i in range(min(remaining_width, new_cols, len(line.cells) - at)):
        second.cells[i] = line.cells[at + i].copy()
    return first, second


def _split_long_line(line: GridLine, new_cols: int) -> List[GridLine]:
    """将超长内容拆分为多个 new_cols 宽的行

    除最后一段外都标记 WRAPPED。最后一段继承原行 flags：
    - 原行非 WRAPPED（独立长行）：末段是链尾，used=-1（width() 裁剪尾部）
    - 原行仍 WRAPPED（链在下一物理行继续）：末段是部分填充的 WRAPPED 行，
      used 记录真实内容宽，供后续合并/拆分定位内容边界
    """
    result: List[GridLine] = []
    content_width = _content_width(line)
    offset = 0
    while offset < content_width:
        chunk = GridLine(cells=[GridCell() for _ in range(new_cols)],
                         flags=LINE_WRAPPED)
        for i in range(min(new_cols, content_width - offset)):
            chunk.cells[i] = line.cells[offset + i].copy()
        result.append(chunk)
        offset += new_cols

    # 末段继承原行 flags（链尾清 WRAPPED；链中保留继续标记）
    if result:
        result[-1].flags = line.flags
        if line.wrapped:
            result[-1].used = content_width - (len(result) - 1) * new_cols

    return result


# ── SGR 颜色序列化（与 screen.py _char_to_sgr / _color_to_sgr 一致） ──

def _cell_to_sgr(cell: GridCell) -> str:
    """将 cell 属性转为 SGR 序列"""
    attrs: List[str] = []
    if cell.bold:
        attrs.append("1")
    if cell.italics:
        attrs.append("3")
    if cell.underscore:
        attrs.append("4")
    if cell.strikethrough:
        attrs.append("9")
    if cell.reverse:
        attrs.append("7")
    fg_sgr = _color_to_sgr(cell.fg, is_fg=True)
    if fg_sgr:
        attrs.append(fg_sgr)
    bg_sgr = _color_to_sgr(cell.bg, is_fg=False)
    if bg_sgr:
        attrs.append(bg_sgr)
    if not attrs:
        return "\x1b[0m"
    return f"\x1b[{';'.join(attrs)}m"


def _color_to_sgr(color, is_fg: bool) -> str:
    """颜色值转 SGR 序列（与 screen.py _color_to_sgr 逻辑一致）"""
    if color == "default":
        return ""
    prefix = "38" if is_fg else "48"
    if color in _ANSI_FG_NAMES:
        code = 30 + _ANSI_FG_NAMES[color] if is_fg else 40 + _ANSI_FG_NAMES[color]
        return str(code)
    if color in _ANSI_FG_BRIGHT:
        code = 90 + _ANSI_FG_BRIGHT[color] - 8 if is_fg else 100 + _ANSI_FG_BRIGHT[color] - 8
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
