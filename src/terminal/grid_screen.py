"""GridScreen — 继承 pyte.Screen，集成 tmux 风格的 scrollback 管理

核心职责:
1. 继承 pyte.Screen 的全部 VT 解析能力（draw/index/linefeed/...）
2. 在 draw() auto-wrap 路径标记 _line_wrapped（区分软换行 vs 硬换行）
3. 在 index() 行滚动前捕获顶部行到 Grid.scroll_history()（不再丢失）
4. 在 resize() 时调用 Grid.reflow() 重排 scrollback + visible，再同步回 pyte

设计要点:
- 平时 draw/index 只维护 pyte.buffer + _line_wrapped（性能开销极小）
- Grid 仅持有 scrollback（visible 部分在 reflow 前才同步，避免每次 draw 都同步）
- reflow 流程：sync_pyte_to_grid_visible → grid.reflow → sync_grid_to_pyte_visible

参考:
- tmux grid.c + screen.c（grid_write/_screen_write_linefeed 等）
- pyte screens.py（Screen.draw / Screen.index / Screen.resize）
"""

import logging
import unicodedata
from typing import List, Optional

try:
    import pyte
    from pyte.screens import Char, Margins, Cursor, Screen
    from pyte import modes as mo
    from pyte.streams import Stream
    from wcwidth import wcwidth
    _HAS_PYTE = True
except ImportError:
    _HAS_PYTE = False
    pyte = None  # type: ignore
    Screen = object  # type: ignore[misc,assignment]

from .grid import Grid, GridCell, GridLine, LINE_WRAPPED

_logger = logging.getLogger("pty-grid-screen")


class GridScreen(Screen):  # type: ignore[misc]
    """带 scrollback 的 pyte.Screen（tmux 风格）

    用法:
        screen = GridScreen(80, 24, hlimit=10000)
        screen.draw("hello")           # 正常 VT 解析
        scrollback = screen.capture_scrollback()  # 获取 scrollback（带 SGR）
        screen.resize(24, 132)         # reflow 不丢内容
    """

    def __init__(self, columns: int, lines: int, hlimit: int = 10000):
        # 先初始化 _grid 和 _line_wrapped，因为父类 __init__ 会调用 reset()
        self._grid = Grid(columns, lines, hlimit)
        # _line_wrapped[y] = True 表示第 y 行因列满被自动换行（软换行）
        # 用于 reflow 时判断是否合并下一行
        self._line_wrapped: List[bool] = [False] * lines
        super().__init__(columns, lines)

    # ── pyte.Screen override ──────────────────────────────────────

    def draw(self, data: str) -> None:
        """复写 pyte.Screen.draw，在 auto-wrap 路径标记 _line_wrapped

        与原版差异：
        1. 在 `cursor.x == columns` 且 `DECAWM in mode` 时，执行 carriage_return() +
           linefeed() 之前设置 _line_wrapped[cursor.y] = True，标识该行为软换行。
        2. 在 cursor.x == 0 且准备写入 char_width > 0 的字符时，清除
           _line_wrapped[cursor.y] = False。这确保当行被显式重写（如 ConPTY
           repaint 用 CSI 定位 + draw，或正常 \\r\\n 后 draw）时，旧的
           _line_wrapped 标记被清除，避免下一次 reflow 基于陈旧标记错误合并行。

           关键不变量：auto-wrap 路径在上面已对【旧行】设 True，之后
           carriage_return + linefeed 把 cursor 移到【新行】x=0，此处 clear
           作用在新行上，不会撤销旧行的 wrapped 标记。

           修复的 bug：反复 resize 变窄后再变宽时内容错乱堆叠。
           根因：ConPTY repaint 用 CSI row;col H 显式定位每行后 draw，
           pyte.draw 不更新 _line_wrapped → 标记陈旧 → 下次 reflow 错误合并。
        """
        data = data.translate(
            self.g1_charset if self.charset else self.g0_charset)

        for char in data:
            char_width = wcwidth(char)

            # 列满 + auto-wrap 模式：标记软换行后换行
            if self.cursor.x == self.columns:
                if mo.DECAWM in self.mode:
                    self.dirty.add(self.cursor.y)
                    # ↓↓↓ GridScreen 新增：标记软换行（参考 tmux GRID_LINE_WRAPPED）
                    self._line_wrapped[self.cursor.y] = True
                    self.carriage_return()
                    self.linefeed()
                elif char_width > 0:
                    self.cursor.x -= char_width

            # ↓↓↓ 新增：在行首（x==0）写入字符时清除该行的 wrapped 标记。
            # 该行正被显式写入（ConPTY repaint / 正常 \r\n 输出 / 光标定位后写入），
            # 不是上一行 auto-wrap 的延续。清除陈旧标记避免 reflow 错误合并。
            # 注意：auto-wrap 路径已在上面对【旧行】设 True，此处 cursor.y 已是
            # 【新行】（carriage_return + linefeed 后），清除的是新行标记，不影响旧行。
            if self.cursor.x == 0 and char_width > 0:
                self._line_wrapped[self.cursor.y] = False

            # 插入模式：新字符把旧字符推到右侧
            if mo.IRM in self.mode and char_width > 0:
                self.insert_characters(char_width)

            line = self.buffer[self.cursor.y]
            if char_width == 1:
                line[self.cursor.x] = self.cursor.attrs._replace(data=char)
            elif char_width == 2:
                # 宽字符占两格，第二格是 stub（空 data）
                line[self.cursor.x] = self.cursor.attrs._replace(data=char)
                if self.cursor.x + 1 < self.columns:
                    line[self.cursor.x + 1] = self.cursor.attrs \
                        ._replace(data="")
            elif char_width == 0 and unicodedata.combining(char):
                # 组合字符：与前一字符合并
                if self.cursor.x:
                    last = line[self.cursor.x - 1]
                    normalized = unicodedata.normalize("NFC", last.data + char)
                    line[self.cursor.x - 1] = last._replace(data=normalized)
                elif self.cursor.y:
                    last = self.buffer[self.cursor.y - 1][self.columns - 1]
                    normalized = unicodedata.normalize("NFC", last.data + char)
                    self.buffer[self.cursor.y - 1][self.columns - 1] = \
                        last._replace(data=normalized)
            else:
                break  # 不可打印字符，不推进光标

            if char_width > 0:
                self.cursor.x = min(self.cursor.x + char_width, self.columns)

        self.dirty.add(self.cursor.y)

    def index(self) -> None:
        """复写 pyte.Screen.index，行滚动前捕获顶部行到 scrollback

        原版在 cursor.y == bottom 时 `buffer[y] = buffer[y+1]; buffer.pop(bottom)`，
        顶部行直接丢失。GridScreen 在丢失前将其转为 GridLine 推入 Grid.scroll_history()。
        """
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom:
            # ↓↓↓ GridScreen 新增：捕获顶部行到 scrollback
            self._capture_line_to_scrollback(top)
            self.dirty.update(range(self.lines))
            for y in range(top, bottom):
                self.buffer[y] = self.buffer[y + 1]
                # 同步 _line_wrapped 标记
                self._line_wrapped[y] = self._line_wrapped[y + 1]
            self.buffer.pop(bottom, None)
            self._line_wrapped[bottom] = False  # 新行（底部）默认非软换行
        else:
            self.cursor_down()

    def reverse_index(self) -> None:
        """复写 pyte.Screen.reverse_index，向上滚动时捕获底部行到 scrollback

        原版在 cursor.y == top 时 `buffer[y] = buffer[y-1]; buffer.pop(top)`，
        底部行直接丢失。GridScreen 在丢失前将其推入 scrollback。

        注意：tmux 的 reverse_index（RI）实际上不保存底部行到 scrollback，
        因为 RI 是"在顶部插入空行"，底部行被推出。但为了保留用户输出，
        我们选择保存（与 index 对称）。如果测试发现行为不符预期可移除。
        """
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == top:
            # ↓↓↓ GridScreen 新增：捕获底部行到 scrollback
            # 注意：reverse_index 是向上滚动，底部行被推出
            self._capture_line_to_scrollback(bottom)
            self.dirty.update(range(self.lines))
            for y in range(bottom, top, -1):
                self.buffer[y] = self.buffer[y - 1]
                self._line_wrapped[y] = self._line_wrapped[y - 1]
            self.buffer.pop(top, None)
            self._line_wrapped[top] = False
        else:
            self.cursor_up()

    def erase_in_display(self, how: int = 0, *args, **kwargs) -> None:
        """复写 pyte.Screen.erase_in_display，how==2/3 时同时清空 Grid.scrollback

        Windows cls 命令发送 \\x1b[2J（how==2），原版 pyte 只清空可见屏幕，不清空
        scrollback。但 GridScreen 的 Grid.scrollback 保留了 cls 之前的内容，resize 时
        Grid.reflow 会把 scrollback 旧内容重新提到 visible → "cls 后 resize 前面内容
        又回来" bug。

        修复：how==2 或 how==3 时同时清空 Grid.scrollback，确保 cls 后 resize 不带回
        旧内容。how==3 是标准 \\x1b[3J（Erase Scrollback），本来就应清空 scrollback。
        """
        super().erase_in_display(how, *args, **kwargs)
        if how in (2, 3):
            self._grid.clear_scrollback()
            _logger.debug("erase_in_display how=%d: cleared Grid.scrollback", how)

    def resize(self, lines: Optional[int] = None,
               columns: Optional[int] = None) -> None:
        """复写 pyte.Screen.resize：按 ConPTY 语义重排并写回 pyte.buffer

        核心原则：resize 后 pyte.buffer 必须与 ConPTY 的可见区状态完全一致，
        否则 ConPTY 后续的绝对光标定位（\\x1b[row;colH）会落到错误位置
        （"光标在 dir 输出中间" bug 的根因）。

        ConPTY resize 语义（实证，见 tests/e2e/test_resize_cursor_sync.py）：
        - 高度增长：内容锚顶，底部补空行，光标绝对位置不变
        - 高度收缩：优先砍底部空行；不够则从顶部删行（内容上移），
          光标随其文本行上移
        - 宽度变化：软换行感知 rewrap，光标随文本

        pyte 原生 resize 不满足此语义（shrink 无脑砍顶部 + cursor restore
        原位置；无宽度 rewrap），因此本方法完全接管：

        流程:
        1. 同步 pyte.buffer visible → Grid.visible（reflow 输入）
        2. Grid.reflow(new_cols, new_rows) 按 ConPTY 语义重排，
           返回 visible 顶部被推入 scrollback 的行数 pushed
        3. Grid.visible 写回 pyte.buffer（两模型统一，后续 ConPTY
           repaint/输出直接作用于正确坐标）
        4. 更新 lines/columns/margins + dirty 标记
        5. cursor 绑定文本行：y = old_y - pushed；宽度变化时用行文本
           在 visible 中重新定位（wrap 链合并/拆分导致的行位移）
        6. _line_wrapped 从 Grid flags 同步（rewrap 后的新标记）

        宽度变化时 ConPTY 必发送 repaint（frame 全变），pyte feed 后
        cursor 会被精确修正；此处近似值只在 repaint 到达前短暂使用。
        """
        lines = lines or self.lines
        columns = columns or self.columns

        if lines == self.lines and columns == self.columns:
            return  # 无变化

        _logger.debug("GridScreen.resize: %dx%d -> %dx%d",
                      self.columns, self.lines, columns, lines)
        self._log_grid_state("before_reflow")

        width_changed = (columns != self.columns)
        old_cursor_y, old_cursor_x = self.cursor.y, self.cursor.x
        # 记录 cursor 所在行的文本（宽度变化时用于重新定位行号）
        old_cursor_line_text = ""
        if width_changed:
            try:
                old_cursor_line_text = "".join(
                    self.buffer[old_cursor_y][c].data or ""
                    for c in range(self.columns)
                ).rstrip()
            except Exception:
                pass

        # 1. 同步 pyte visible → Grid visible（始终执行：pyte.buffer 是
        #    ConPTY 输出喂出来的真实状态，sync 永远安全）
        self._sync_pyte_to_grid_visible()

        # 2. Grid.reflow 按 ConPTY 语义重排 scrollback + visible
        pushed = 0
        try:
            pushed = self._grid.reflow(columns, lines)
        except Exception as e:
            _logger.warning("Grid.reflow 失败（scrollback 可能不正确）: %s", e)
        self._log_grid_state("after_reflow")

        # 3. Grid.visible 写回 pyte.buffer（关键：统一两个模型的可见区）
        try:
            self._sync_grid_to_pyte_visible()
        except Exception as e:
            _logger.warning("resize 写回 pyte.buffer 失败: %s", e)

        # 4. 更新 lines/columns/margins（行调整已在 reflow 完成，
        #    不调用 pyte 原生 resize —— 其 shrink 砍顶部 + cursor restore
        #    与 ConPTY 语义不符）
        self.lines, self.columns = lines, columns
        self.set_margins()
        self.dirty.update(range(lines))

        # 5. cursor 绑定文本行：
        #    - 高度语义：grow/砍底空行时 pushed=0（cursor 不动）；
        #      砍顶部 pushed>0 时 cursor 随内容上移 pushed 行
        #    - 宽度变化：wrap 链合并/拆分可能移动 cursor 所在行，
        #      用行文本在 visible 中重新定位（找不到则退回 old_y - pushed）
        new_y = old_cursor_y - pushed
        if width_changed and old_cursor_line_text:
            found = self._find_visible_row_by_text(
                old_cursor_line_text, hint=new_y)
            if found is not None:
                new_y = found
        self.cursor.y = max(0, min(new_y, lines - 1))
        self.cursor.x = max(0, min(old_cursor_x, columns - 1))
        _logger.debug(
            "GridScreen.resize: pushed=%d cursor=(%d,%d) -> (%d,%d)",
            pushed, old_cursor_y, old_cursor_x, self.cursor.y, self.cursor.x)

        # 6. _line_wrapped 从 Grid flags 同步（rewrap 后的新标记）
        try:
            self._line_wrapped = self._grid_wrapped_flags()
        except Exception:
            self._line_wrapped = [False] * self.lines

    # ── GridScreen 专属方法 ──────────────────────────────────────

    @property
    def grid(self) -> Grid:
        """暴露底层 Grid 供 TerminalScreen 访问 scrollback"""
        return self._grid

    @property
    def scrollback_lines_count(self) -> int:
        return self._grid.hsize

    def _log_grid_state(self, label: str) -> None:
        """诊断：dump Grid 状态摘要到日志（hsize/sy/首行/末行）"""
        try:
            g = self._grid
            sb_first = ""
            vs_first = ""
            vs_last = ""
            if g.hsize > 0:
                sb_line = g.get_scrollback_line(0)
                if sb_line:
                    sb_first = "".join(c.data for c in sb_line.cells[:80]).rstrip()
            vs0 = g.get_visible_line(0)
            if vs0:
                vs_first = "".join(c.data for c in vs0.cells[:80]).rstrip()
            vsN = g.get_visible_line(g.sy - 1)
            if vsN:
                vs_last = "".join(c.data for c in vsN.cells[:80]).rstrip()
            _logger.debug("GridState[%s]: hsize=%d sy=%d total=%d | SB[0]=%r | VS[0]=%r | VS[-1]=%r",
                          label, g.hsize, g.sy, g.total_lines, sb_first[:60], vs_first[:60], vs_last[:60])
        except Exception:
            pass

    def _capture_line_to_scrollback(self, row: int) -> None:
        """将 pyte.buffer[row] 捕获为 GridLine 推入 scrollback

        在 index()/reverse_index() 行滚动前调用，避免顶部/底部行丢失。
        """
        line = self._pyte_line_to_grid_line(row)
        self._grid.scroll_history(line)

    def _pyte_line_to_grid_line(self, row: int) -> GridLine:
        """将 pyte.buffer[row] 转为 GridLine

        属性映射: pyte.Char → GridCell（字段名一致）
        flags: 从 _line_wrapped[row] 推导 LINE_WRAPPED 标记
        """
        cells: List[GridCell] = []
        for col in range(self.columns):
            try:
                char = self.buffer[row][col]
            except (KeyError, IndexError):
                cells.append(GridCell())
                continue
            cells.append(GridCell(
                # 宽字符（中文）的 stub cell 在 pyte 中 data=""（空字符串），
                # 必须保留空字符串而非转为 " "，否则 capture_range 无法跳过
                # （空格是 truthy，not " " 为 False），导致中文后多输出一个空格，
                # 进而使行宽超过 cols 触发 xterm.js 折行，破坏 scrollback 推入逻辑。
                # 普通字符 data 非空，空 cell data=" "，均不受影响。
                data=char.data or "",
                fg=str(char.fg) if char.fg else "default",
                bg=str(char.bg) if char.bg else "default",
                bold=bool(char.bold),
                italics=bool(char.italics),
                underscore=bool(char.underscore),
                reverse=bool(char.reverse),
                strikethrough=bool(char.strikethrough),
            ))
        flags = LINE_WRAPPED if (
            0 <= row < len(self._line_wrapped) and self._line_wrapped[row]
        ) else 0
        return GridLine(cells=cells, flags=flags)

    def _sync_pyte_to_grid_visible(self) -> None:
        """将当前 pyte.buffer 的 visible 同步到 Grid.visible

        在 reflow 前调用：Grid.scrollback 保持不变，Grid.visible 被替换为
        pyte 当前可见区。这样 Grid.reflow 能基于完整数据重排。
        """
        # 诊断日志：sync 前 pyte.buffer visible 首行 + Grid.visible 首行
        try:
            pyte_first = ""
            for row in range(self.lines):
                line_text = "".join(
                    self.buffer[row][col].data
                    for col in range(self.columns)
                    if self.buffer[row][col].data
                ).rstrip()
                if line_text:
                    pyte_first = line_text[:80]
                    break
            grid_first = ""
            vl = self._grid.get_visible_line(0)
            if vl:
                grid_first = "".join(c.data for c in vl.cells[:80]).rstrip()
            _logger.debug("_sync_pyte_to_grid_visible: pyte_first=%r grid_first=%r",
                          pyte_first, grid_first)
        except Exception:
            pass

        visible_lines: List[GridLine] = []
        for row in range(self.lines):
            visible_lines.append(self._pyte_line_to_grid_line(row))
        # 替换 Grid 的 visible 部分（保留 scrollback）
        self._grid.linedata = (
            self._grid.linedata[:self._grid.hsize] + visible_lines
        )
        self._grid.sx = self.columns
        self._grid.sy = self.lines

        # 诊断日志：sync 后 Grid.visible 首行
        try:
            vl2 = self._grid.get_visible_line(0)
            after_first = "".join(c.data for c in vl2.cells[:80]).rstrip() if vl2 else ""
            _logger.debug("_sync_pyte_to_grid_visible: after sync grid_first=%r", after_first)
        except Exception:
            pass

    def _sync_grid_to_pyte_visible(self) -> None:
        """将 Grid.visible 同步回 pyte.buffer

        reflow 后调用：清空 pyte.buffer，用 Grid.visible 重新填充。
        """
        self.buffer.clear()
        for row in range(self._grid.sy):
            grid_line = self._grid.get_visible_line(row)
            if grid_line is None:
                continue
            for col in range(self._grid.sx):
                cell = (grid_line.cells[col]
                        if col < len(grid_line.cells) else GridCell())
                self.buffer[row][col] = Char(
                    # 宽字符 stub cell 的 data 为空字符串，必须保留 "" 而非转为 " "，
                    # 与 _pyte_line_to_grid_line 的 data=char.data or "" 保持一致。
                    # 否则 snapshot 生成（_render_with_colors / capture_range）的
                    # `if not char.data: continue` 无法跳过 stub cell，
                    # 导致 CJK 字符间多输出空格（如"我喜欢你"→"我 喜 欢 你"）。
                    data=cell.data or "",
                    fg=cell.fg,
                    bg=cell.bg,
                    bold=cell.bold,
                    italics=cell.italics,
                    underscore=cell.underscore,
                    strikethrough=cell.strikethrough,
                    reverse=cell.reverse,
                )

    def _grid_wrapped_flags(self) -> List[bool]:
        """从 Grid.visible 提取每行的 WRAPPED 标记，用于 _line_wrapped 同步"""
        flags: List[bool] = []
        for row in range(self._grid.sy):
            line = self._grid.get_visible_line(row)
            flags.append(line.wrapped if line else False)
        return flags

    def _find_visible_row_by_text(self, text: str, hint: int = 0) -> Optional[int]:
        """在 Grid.visible 中按行文本查找行号（宽度变化后重绑定 cursor 行）

        取与 hint 距离最近的匹配行；无匹配返回 None。
        """
        best: Optional[int] = None
        best_dist = 1 << 30
        for row in range(self._grid.sy):
            line = self._grid.get_visible_line(row)
            if line is None:
                continue
            line_text = "".join(c.data for c in line.cells).rstrip()
            if line_text == text:
                dist = abs(row - hint)
                if dist < best_dist:
                    best, best_dist = row, dist
        return best

    # ── scrollback 访问接口（供 TerminalScreen 调用） ────────────

    def capture_scrollback(self) -> str:
        """序列化 scrollback 为 ANSI 字符串（带 SGR 颜色）

        用于 subscribe 响应：前端写入 xterm.js 后即推入 scrollback 区。
        格式：每行 ANSI 内容 + \\r\\n。
        """
        return self._grid.capture_scrollback()


    def clear_scrollback(self) -> None:
        """清除所有 scrollback（对应 VT 序列 \\x1b[3J）"""
        self._grid.clear_scrollback()

    # ── 辅助：重置时同步 Grid ─────────────────────────────────────

    def reset(self) -> None:
        """重置终端：同时清空 Grid 的 scrollback 和 visible"""
        super().reset()
        self._grid.clear_all()
        self._line_wrapped = [False] * self.lines
