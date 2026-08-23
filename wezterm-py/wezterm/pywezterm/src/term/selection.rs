//! 选区状态机（绑定层自建，纯 Rust 无 pyo3）
//!
//! vendored wezterm-term 无选区状态，此模块在绑定层实现 word/line/区域选择
//! 与纯文本提取。坐标模型：**stable 行 + 列**（跨 scrollback 与可见区，不受
//! 视图滚动影响，与 get_semantic_zones 坐标基准一致）。
//!
//! 数据源复用 term.rs 的 cells_of_line + Screen::lines_in_phys_range，
//! 不触碰任何 vendored crate；选区算法纯函数化，可单测。
//!
//! PyTerminal 与 Mux 的每个 Pane 各持一份 SelectionState；Mux 入口把整屏
//! 坐标换算为 pane 内坐标再转 stable 行后调用本模块。

use wezterm_term::screen::Screen;

use super::cells_of_line;

/// 选区类型
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum SelectionKind {
    /// 区域选择（鼠标按下拖拽）：锚点 → 当前端点，矩形内全部文本
    Region,
    /// 双击选词：以锚点所在词边界（空白/标点分隔）为起止
    Word,
    /// 三击选行：锚点所在物理行整行（含换行）
    Line,
}

/// 选区状态（Pane 内持有；与 view_offset 解耦——滚动不影响选区边界）
#[derive(Clone, Debug)]
pub struct SelectionState {
    /// 锚点 (stable_row, col)；None = 无选区
    pub anchor: Option<(isize, usize)>,
    pub kind: SelectionKind,
    /// 当前端点 (stable_row, col)
    pub end: (isize, usize),
}

impl Default for SelectionState {
    fn default() -> Self {
        Self {
            anchor: None,
            kind: SelectionKind::Region,
            end: (0, 0),
        }
    }
}

impl SelectionState {
    /// 区域选择：anchor → end（stable 坐标）
    pub fn set_region(&mut self, anchor: (isize, usize), end: (isize, usize)) {
        self.anchor = Some(anchor);
        self.kind = SelectionKind::Region;
        self.end = end;
    }

    /// 双击选词：以 (row, col) 所在词边界为选区（未命中词则不选区）
    pub fn select_word(&mut self, screen: &Screen, row: isize, col: usize) {
        if let Some((start_col, end_col)) = word_bounds(screen, row, col) {
            self.anchor = Some((row, start_col));
            self.kind = SelectionKind::Word;
            self.end = (row, end_col);
        }
    }

    /// 三击选行：锚点所在物理行整行（含换行）
    pub fn select_line(&mut self, row: isize, _col: usize) {
        self.anchor = Some((row, 0));
        self.kind = SelectionKind::Line;
        self.end = (row, usize::MAX);
    }

    pub fn clear(&mut self) {
        self.anchor = None;
    }

    /// 是否有活动选区
    pub fn is_active(&self) -> bool {
        self.anchor.is_some()
    }

    /// 当前选区纯文本（无选区返回空串）。
    ///
    /// 算法（纯函数）：
    /// 1. 锚点/终点按 stable 行号排序 → (lo, hi)；首/末列随方向取对应端列
    /// 2. 对每个 stable 行取物理行 → cells_of_line → 拼接 cell 字符（跳过
    ///    续列，宽字符按其宽度占位）
    /// 3. 首行截 [start_col, ∞)、末行截 [0, end_col]（列按 cell 列号）
    /// 4. 中间行整行；行间补 \n；Line 选区末尾补换行
    /// 5. 末尾行尾空白裁剪（与 pane_text 语义一致）
    pub fn text(&self, screen: &Screen) -> String {
        let (anchor, end) = match self.anchor {
            Some(a) => (a, self.end),
            None => return String::new(),
        };
        let (lo, hi) = if anchor.0 <= end.0 {
            (anchor.0, end.0)
        } else {
            (end.0, anchor.0)
        };
        let (start_col, end_col) = if anchor.0 <= end.0 {
            (anchor.1, end.1)
        } else {
            (end.1, anchor.1)
        };
        let mut lines: Vec<String> = Vec::new();
        for stable in lo..=hi {
            let phys = match screen.stable_row_to_phys(stable) {
                Some(p) => p,
                None => continue, // 该 stable 行已不在屏幕/历史中
            };
            let line = &screen.lines_in_phys_range(phys..phys + 1)[0];
            let cells = cells_of_line(line);
            let mut s = String::new();
            for cell in &cells {
                let (col, ch, ..) = cell;
                if ch.is_empty() {
                    continue; // 跳过续列（宽字符后续列）
                }
                if stable == lo && *col < start_col {
                    continue; // 首行：截 [start_col, ∞)
                }
                if stable == hi && *col > end_col {
                    continue; // 末行：截 [0, end_col]
                }
                s.push_str(ch);
            }
            lines.push(s);
        }
        let mut text = lines.join("\n");
        // 三击选行语义含换行（复制粘贴后光标换行）
        if self.kind == SelectionKind::Line && !text.is_empty() {
            text.push('\n');
        }
        while text.ends_with(' ') {
            text.pop();
        }
        text
    }
}

/// 词内字符：非空白且（字母数字或下划线）
fn is_word_char(ch: char) -> bool {
    !ch.is_whitespace() && (ch.is_alphanumeric() || ch == '_')
}

/// 找 (row, col) 所在词的列区间 [start_col, end_col]（闭区间，末列含）；
/// 未命中词（col 落在空白/标点等分隔符上）返回 None。
fn word_bounds(screen: &Screen, row: isize, col: usize) -> Option<(usize, usize)> {
    let phys = screen.stable_row_to_phys(row)?;
    let line = &screen.lines_in_phys_range(phys..phys + 1)[0];
    let cells = cells_of_line(line);
    // 命中包含 col 的 cell（宽字符按 [cell.0, cell.0+width) 覆盖）
    let i = cells.iter().position(|c| {
        let w = c.9.max(1);
        col >= c.0 && col < c.0 + w
    })?;
    // 命中的 cell 本身是分隔符（空格/标点）：不选区
    let ch = cells[i].1.chars().next()?;
    if !is_word_char(ch) {
        return None;
    }
    // 向左扩展到词边界
    let mut start = i;
    while start > 0 {
        let ch = cells[start - 1].1.chars().next()?;
        if is_word_char(ch) {
            start -= 1;
        } else {
            break;
        }
    }
    // 向右扩展到词边界
    let mut end = i;
    while end + 1 < cells.len() {
        let ch = cells[end + 1].1.chars().next()?;
        if is_word_char(ch) {
            end += 1;
        } else {
            break;
        }
    }
    let start_col = cells[start].0;
    // end_col 为末词字符列号（闭区间语义，与 text() 的 [0, end_col] 一致）
    let end_col = cells[end].0 + cells[end].9.max(1) - 1;
    Some((start_col, end_col))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    use wezterm_term::{Terminal, TerminalSize};

    use crate::term::{CaptureWriter, EmbeddedConfig};

    /// 构造一个已喂入文本的终端（small cols/rows，无滚动干扰）
    fn make_terminal(text: &[u8], cols: usize, rows: usize) -> Terminal {
        let capture = Arc::new(Mutex::new(Vec::new()));
        let mut term = Terminal::new(
            TerminalSize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
                dpi: 0,
            },
            Arc::new(EmbeddedConfig { scrollback: 10000 }),
            "pywezterm-test",
            "0.1.0",
            Box::new(CaptureWriter::new(capture)),
        );
        term.advance_bytes(text);
        term
    }

    /// 终端屏幕（feed 后物理行 0..rows 即 stable 行 0..rows）
    fn screen(term: &Terminal) -> &Screen {
        term.screen()
    }

    /// 构造带 quirks 的终端（与 PyTerminal/Mux 一致）
    fn make_terminal_quirks(text: &[u8], cols: usize, rows: usize) -> Terminal {
        let mut term = make_terminal(text, cols, rows);
        term.enable_conpty_quirks();
        term
    }

    /// 取全部物理行文本（scrollback + 可见区，按物理顺序）
    fn all_lines_text(term: &Terminal) -> Vec<String> {
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let mut out = Vec::new();
        for line in screen.lines_in_phys_range(0..total) {
            let mut s = String::new();
            for c in line.visible_cells() {
                s.push_str(c.str());
            }
            out.push(s.trim_end().to_string());
        }
        out
    }

    /// rewrap 回归：窄化→复原后 CJK 宽字符文本不得错乱（孤立/乱码/错位）。
    /// 回归：dir 输出的中文摘要行（如 "31 个文件 458,874 字节"）窄化后
    /// rewrap 会把宽字符按列切割，复原后必须恢复完整文本。
    fn assert_rewrap_cjk_integrity(quirks: bool) {
        let mut feed: Vec<u8> = Vec::new();
        for i in 0..8 {
            feed.extend_from_slice(
                format!(
                    "2026/08/09  16:35             4,5{:02} speedtest_nodes2_extra_long_file_name_{:02}.py\r\n",
                    i, i
                )
                .as_bytes(),
            );
            if i == 4 {
                feed.extend_from_slice("              31 个文件        458,874 字节\r\n".as_bytes());
                feed.extend_from_slice("              85 个目录 156,158,554,112 可用字节\r\n".as_bytes());
            }
        }
        let mut t = if quirks {
            make_terminal_quirks(&feed, 80, 10)
        } else {
            make_terminal(&feed, 80, 10)
        };
        // 窄化（长行 wrap 多段）→ 复原（应完整合并）
        t.resize(TerminalSize {
            rows: 10,
            cols: 40,
            pixel_width: 0,
            pixel_height: 0,
            dpi: 0,
        });
        t.resize(TerminalSize {
            rows: 10,
            cols: 80,
            pixel_width: 0,
            pixel_height: 0,
            dpi: 0,
        });
        let text = all_lines_text(&t).join("\n");
        // 中文摘要行必须完整保留（"31 个文件"、"字节"、"85 个目录" 不拆散）
        assert!(
            text.contains("31 个文件") && text.contains("458,874 字节"),
            "CJK 行 rewrap 错乱: {:?}",
            text
        );
        assert!(
            text.contains("85 个目录") && text.contains("可用字节"),
            "CJK 行 rewrap 错乱: {:?}",
            text
        );
        // 每行 ASCII 文件名须完整（无孤立片段混入其他行）
        for i in 0..8 {
            assert!(
                text.contains(&format!("file_name_{i:02}.py")),
                "文件行 {} 缺失/错乱: {:?}",
                i,
                text
            );
        }
    }

    #[test]
    fn test_rewrap_cjk_integrity_no_quirks() {
        assert_rewrap_cjk_integrity(false);
    }

    #[test]
    fn test_rewrap_cjk_integrity_quirks() {
        assert_rewrap_cjk_integrity(true);
    }

    #[test]
    fn test_no_selection_empty() {
        let term = make_terminal(b"abc\r\ndef\r\n", 5, 3);
        let sel = SelectionState::default();
        assert_eq!(sel.text(screen(&term)), "");
        assert!(!sel.is_active());
    }

    #[test]
    fn test_region_single_row() {
        let term = make_terminal(b"abcdef\r\n", 10, 3);
        let mut sel = SelectionState::default();
        // 末行截 [0, end_col] 为闭区间：含 end_col 列（col 1..=3 → "bcd"）
        sel.set_region((0, 1), (0, 3));
        assert_eq!(sel.text(screen(&term)), "bcd");
    }

    #[test]
    fn test_region_across_rows() {
        let term = make_terminal(b"abc\r\ndef\r\n", 5, 3);
        let mut sel = SelectionState::default();
        sel.set_region((0, 1), (1, 2));
        // 首行 [1,∞) → "bc"；末行 [0,2] → "def"（列 0,1,2 均 ≤2）
        assert_eq!(sel.text(screen(&term)), "bc\ndef");
    }

    #[test]
    fn test_region_reversed_rows() {
        let term = make_terminal(b"abc\r\ndef\r\n", 5, 3);
        let mut sel = SelectionState::default();
        // 反向拖选：end 在 anchor 上方
        sel.set_region((1, 0), (0, 2));
        // lo=0 hi=1；start_col=2（end 列），end_col=0（anchor 列）
        // 首行 [2,∞) → "c"；末行 [0,0] → "d"
        assert_eq!(sel.text(screen(&term)), "c\nd");
    }

    #[test]
    fn test_region_middle_rows_full() {
        let term = make_terminal(b"aaa\r\nbbb\r\nccc\r\n", 5, 4);
        let mut sel = SelectionState::default();
        sel.set_region((0, 1), (2, 1));
        assert_eq!(sel.text(screen(&term)), "aa\nbbb\ncc");
    }

    #[test]
    fn test_word_selection() {
        let term = make_terminal(b"hello world\r\n", 20, 3);
        let mut sel = SelectionState::default();
        sel.select_word(screen(&term), 0, 6); // 'w' 起点 → "world"
        assert_eq!(sel.text(screen(&term)), "world");
        assert!(sel.is_active());
    }

    #[test]
    fn test_word_selection_boundary_punct() {
        let term = make_terminal(b"foo,bar(baz)\r\n", 20, 3);
        let mut sel = SelectionState::default();
        sel.select_word(screen(&term), 0, 2); // 在 "foo" 内 → "foo"
        assert_eq!(sel.text(screen(&term)), "foo");
        sel.clear();
        sel.select_word(screen(&term), 0, 5); // "bar" 内 → "bar"
        assert_eq!(sel.text(screen(&term)), "bar");
    }

    #[test]
    fn test_word_selection_gap_returns_none() {
        let term = make_terminal(b"hello world\r\n", 20, 3);
        let mut sel = SelectionState::default();
        sel.select_word(screen(&term), 0, 5); // 空格处 → 无选区
        assert!(!sel.is_active());
        assert_eq!(sel.text(screen(&term)), "");
    }

    #[test]
    fn test_line_selection_includes_newline() {
        let term = make_terminal(b"hello world\r\n", 20, 3);
        let mut sel = SelectionState::default();
        sel.select_line(0, 3);
        assert_eq!(sel.text(screen(&term)), "hello world\n");
    }

    #[test]
    fn test_clear() {
        let term = make_terminal(b"abc\r\n", 5, 3);
        let mut sel = SelectionState::default();
        sel.set_region((0, 0), (0, 2));
        assert!(sel.is_active());
        sel.clear();
        assert!(!sel.is_active());
        assert_eq!(sel.text(screen(&term)), "");
    }

    #[test]
    fn test_wide_char_selection() {
        // CJK 双宽字符：宽字符按 cell 列号截取，"你好"占 4 列
        let term = make_terminal("你好world\r\n".as_bytes(), 20, 3);
        let mut sel = SelectionState::default();
        // "你"(0-1) "好"(2-3) "world"(4-8)
        sel.set_region((0, 2), (0, 6));
        let t = sel.text(screen(&term));
        assert_eq!(t, "好wor");
    }

    #[test]
    fn test_trailing_space_trimmed() {
        let term = make_terminal(b"ab   \r\n", 10, 3);
        let mut sel = SelectionState::default();
        sel.set_region((0, 0), (0, 4));
        assert_eq!(sel.text(screen(&term)), "ab");
    }
}
