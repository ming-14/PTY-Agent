//! 终端模拟器（wezterm-term）Python 绑定
//!
//! 提供：
//! - Terminal：feed 喂 VT 字节、resize、snapshot 读可见字符网格、
//!   scrollback 历史、cursor 光标、text 纯文本
//! - 模式感知键盘/鼠标编码：key_down/key_up/mouse 依据终端当前状态
//!   （应用光标模式 / kitty / CSI-u / win32 编码）生成字节，写入捕获
//!   缓冲后返回，由调用方决定下发路径（如写入 pty）。
//!
//! wezterm_term::Terminal 内含 RefCell（escape parser）非 Sync，
//! 故用 Mutex<Terminal> 包裹使其 Send+Sync，支持多线程（reader 线程
//! 喂字节、其他线程查询）访问；访问由 GIL + Mutex 双重串行化。

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;
use std::sync::{Arc, Mutex};

use termwiz::input::KeyboardEncoding;
use wezterm_term::input::{
    KeyCode, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use wezterm_term::color::{ColorAttribute, ColorPalette};
use wezterm_term::{
    Alert, AlertHandler, Clipboard, ClipboardSelection, DeviceControlHandler, DownloadHandler,
    Intensity, Line, Progress, SemanticType, Terminal, TerminalConfiguration, TerminalSize,
    Underline,
};

mod selection;
pub(crate) use selection::SelectionState;

/// 写入捕获缓冲的 writer：wezterm 编码的输入/应答字节统一被捕获，
/// 供 Python 侧决定如何下发。pub(crate)：Mux 构造多个 Terminal pane 复用。
#[derive(Clone, Default)]
pub(crate) struct CaptureWriter(Arc<Mutex<Vec<u8>>>);

impl CaptureWriter {
    /// 用现有捕获缓冲构造（字段私有，供 Mux 在仓外构造）
    pub(crate) fn new(buf: Arc<Mutex<Vec<u8>>>) -> Self {
        Self(buf)
    }
}

impl std::io::Write for CaptureWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// 内嵌终端配置：颜色走默认调色板，滚动行数可配（pub(crate)：Mux 复用）
#[derive(Debug)]
pub(crate) struct EmbeddedConfig {
    pub(crate) scrollback: usize,
}

impl TerminalConfiguration for EmbeddedConfig {
    fn color_palette(&self) -> ColorPalette {
        ColorPalette::default()
    }
    fn scrollback_size(&self) -> usize {
        self.scrollback
    }
}

/// 把 ColorAttribute 规范化为字符串：default | p<index> | #rrggbb
fn color_attr_to_string(c: ColorAttribute) -> String {
    match c {
        ColorAttribute::Default => "default".to_string(),
        ColorAttribute::PaletteIndex(i) => format!("p{i}"),
        ColorAttribute::TrueColorWithDefaultFallback(s) => rgb_hex(s.to_srgb_u8()),
        ColorAttribute::TrueColorWithPaletteFallback(s, _) => rgb_hex(s.to_srgb_u8()),
    }
}

/// (r, g, b, a) -> "#rrggbb"
fn rgb_hex(rgba: (u8, u8, u8, u8)) -> String {
    format!("#{:02x}{:02x}{:02x}", rgba.0, rgba.1, rgba.2)
}

/// 单元格元组：(列索引, 字符, 前景, 背景, 粗体, 斜体, 下划线, 反显, 删除线, 宽度)
/// 列索引用 CellRef::cell_index()（wide 字符后续被跳过的空白格不出现，列号会跳位）
pub(crate) type CellTuple = (usize, String, String, String, bool, bool, bool, bool, bool, usize);

/// 单行可见格 → 单元格元组列表
pub(crate) fn cells_of_line(line: &Line) -> Vec<CellTuple> {
    line.visible_cells()
        .map(|cell| {
            let attrs = cell.attrs();
            (
                cell.cell_index(),
                cell.str().to_string(),
                color_attr_to_string(attrs.foreground()),
                color_attr_to_string(attrs.background()),
                attrs.intensity() == Intensity::Bold,
                attrs.italic(),
                attrs.underline() != Underline::None,
                attrs.reverse(),
                attrs.strikethrough(),
                cell.width(),
            )
        })
        .collect()
}

/// 单元格是否默认空白（无字符、无样式）——渲染剪枝用
fn is_default_cell(cell: &CellTuple) -> bool {
    (cell.1.is_empty() || cell.1 == " ")
        && (cell.2.is_empty() || cell.2 == "default")
        && (cell.3.is_empty() || cell.3 == "default")
        && !cell.4
        && !cell.5
        && !cell.6
        && !cell.7
        && !cell.8
}

/// 应用单个 DECSET/DECRST 模式
fn apply_mode(m: &mut TermModeState, ps: u16, enable: bool) {
    match ps {
        1000 | 1002 | 1003 => m.mouse_tracking = if enable { ps } else { 0 },
        1006 => m.sgr_mouse = enable,
        1049 | 1047 | 47 => m.alt_screen = enable,
        25 => m.cursor_visible = enable,
        2004 => m.bracketed_paste = enable,
        _ => {}
    }
}

/// 扫描字节流中的 DECSET/DECRST 序列（\x1b[?NNNNh/l），更新模式状态。
/// 与宿主侧（ptyagent）此前手写的正则嗅探语义一致：备用屏幕、鼠标追踪
/// （1000/1002/1003 互斥）、SGR 鼠标（1006）、光标可见（25）、paste（2004）。
fn update_mode_state(mode: &Mutex<TermModeState>, data: &[u8]) {
    let mut m = mode.lock().unwrap();
    let mut i = 0usize;
    while i < data.len() {
        if data[i] == 0x1b && i + 2 < data.len() && data[i + 1] == b'[' && data[i + 2] == b'?' {
            let mut j = i + 3;
            let mut params: Vec<u16> = Vec::new();
            let mut cur: u16 = 0;
            let mut has_digit = false;
            while j < data.len() {
                let c = data[j];
                if c.is_ascii_digit() {
                    cur = cur.saturating_mul(10).saturating_add((c - b'0') as u16);
                    has_digit = true;
                    j += 1;
                } else if c == b';' {
                    params.push(cur);
                    cur = 0;
                    has_digit = false;
                    j += 1;
                } else if c == b'h' || c == b'l' {
                    if has_digit || !params.is_empty() {
                        params.push(cur);
                    }
                    let enable = c == b'h';
                    for ps in params {
                        apply_mode(&mut m, ps, enable);
                    }
                    j += 1;
                    break;
                } else {
                    break;
                }
            }
            i = j;
        } else {
            i += 1;
        }
    }
}

/// 单元格样式 → SGR 序列（\x1b[...m）；全默认返回 \x1b[0m（与宿主渲染语义一致）
fn cell_sgr(cell: &CellTuple) -> String {
    let mut attrs: Vec<String> = Vec::new();
    if cell.4 {
        attrs.push("1".to_string());
    }
    if cell.5 {
        attrs.push("3".to_string());
    }
    if cell.6 {
        attrs.push("4".to_string());
    }
    if cell.7 {
        attrs.push("7".to_string());
    }
    if cell.8 {
        attrs.push("9".to_string());
    }
    if let Some(s) = sgr_color(&cell.2, true) {
        attrs.push(s);
    }
    if let Some(s) = sgr_color(&cell.3, false) {
        attrs.push(s);
    }
    if attrs.is_empty() {
        return "\x1b[0m".to_string();
    }
    format!("\x1b[{}m", attrs.join(";"))
}

/// 颜色字符串 → SGR 颜色段（None = default，不输出）
fn sgr_color(color: &str, is_fg: bool) -> Option<String> {
    let prefix = if is_fg { "38" } else { "48" };
    match color {
        "default" => None,
        _ if color.starts_with('p') => {
            // wezterm 调色板索引 "pN"（N ∈ 0-255）
            color[1..].parse::<u8>().ok().map(|n| format!("{prefix};5;{n}"))
        }
        _ if color.starts_with('#') => {
            // #rrggbb 真彩色
            let hex = &color[1..];
            if hex.len() == 6 {
                let r = u8::from_str_radix(&hex[0..2], 16).ok()?;
                let g = u8::from_str_radix(&hex[2..4], 16).ok()?;
                let b = u8::from_str_radix(&hex[4..6], 16).ok()?;
                Some(format!("{prefix};2;{r};{g};{b}"))
            } else {
                None
            }
        }
        _ => None,
    }
}

/// 解析按键描述字符串 → KeyCode
pub(crate) fn parse_keycode(s: &str) -> PyResult<KeyCode> {
    use KeyCode::*;
    Ok(match s {
        "Up" => UpArrow,
        "Down" => DownArrow,
        "Left" => LeftArrow,
        "Right" => RightArrow,
        "Home" => Home,
        "End" => End,
        "Insert" => Insert,
        "Delete" => Delete,
        "PageUp" => PageUp,
        "PageDown" => PageDown,
        "Backspace" => Backspace,
        "Tab" => Tab,
        "Enter" => Enter,
        "Esc" => Escape,
        "Space" => Char(' '),
        _ => {
            if let Some(n) = s.strip_prefix('F').and_then(|n| n.parse::<u8>().ok()) {
                Function(n)
            } else if let Some(c) = s.chars().next() {
                Char(c)
            } else {
                return Err(PyValueError::new_err(format!("无法解析按键: {s:?}")));
            }
        }
    })
}

/// 解析鼠标事件类型
pub(crate) fn parse_mouse_kind(s: &str) -> PyResult<MouseEventKind> {
    Ok(match s {
        "press" => MouseEventKind::Press,
        "release" => MouseEventKind::Release,
        "move" => MouseEventKind::Move,
        _ => return Err(PyValueError::new_err(format!("未知鼠标事件类型: {s:?}"))),
    })
}

/// 解析鼠标按钮
pub(crate) fn parse_mouse_button(s: &str) -> PyResult<MouseButton> {
    Ok(match s {
        "left" => MouseButton::Left,
        "middle" => MouseButton::Middle,
        "right" => MouseButton::Right,
        "wheel_up" => MouseButton::WheelUp(1),
        "wheel_down" => MouseButton::WheelDown(1),
        "none" => MouseButton::None,
        _ => return Err(PyValueError::new_err(format!("未知鼠标按钮: {s:?}"))),
    })
}

/// 终端模式状态（feed 时跟踪 DECSET/DECRST，供订阅恢复/查询）
#[derive(Clone, Copy, Default)]
struct TermModeState {
    /// 鼠标追踪模式（0=关闭，1000/1002/1003）
    mouse_tracking: u16,
    /// SGR 鼠标编码（DECSET 1006）
    sgr_mouse: bool,
    /// 备用屏幕激活（1049/1047/47）
    alt_screen: bool,
    /// 光标可见（DECSET 25）
    cursor_visible: bool,
    /// bracketed paste（DECSET 2004）
    bracketed_paste: bool,
}

/// 终端模拟器实例（Mutex 包裹以支持多线程访问）
#[pyclass(name = "Terminal")]
pub struct PyTerminal {
    terminal: Mutex<Terminal>,
    capture: Arc<Mutex<Vec<u8>>>,
    view_offset: Mutex<usize>,
    /// 选区状态（stable 行 + 列；跨 scrollback 与可见区）
    selection: Mutex<SelectionState>,
    /// feed 时跟踪的 DECSET 模式状态（备用屏幕/鼠标/光标/paste）
    mode: Mutex<TermModeState>,
    /// 跨 feed 边界的 DECSET 序列尾部窗口（最多 64 字节，拼接后扫描）
    mode_tail: Mutex<Vec<u8>>,
    /// 剪贴板/下载/设备控制/通知回调（OSC 52 等），替换旧引用即释放
    clipboard_cb: Mutex<Option<Py<PyAny>>>,
    download_cb: Mutex<Option<Py<PyAny>>>,
    device_control_cb: Mutex<Option<Py<PyAny>>>,
    notification_cb: Mutex<Option<Py<PyAny>>>,
}

/// 内部辅助（不暴露给 Python）
impl PyTerminal {
    /// 视图顶部相对可视区顶部的偏移（scrollback 行），用于滚动查看历史。
    /// scroll(delta)/scroll_to_bottom() 修改它，snapshot*/scrollback 读取反映。
    /// 纯计算，不碰锁——由调用方在已持有 terminal 数据时传入总数与可视行数。
    fn _view_size(&self, total: usize, rows: usize) -> usize {
        let offset = *self.view_offset.lock().unwrap();
        // 偏移不能超过可用历史行数（保证视野至少仍在可视区）
        offset.min(total.saturating_sub(rows))
    }

    /// 取走捕获缓冲中全部字节（编码输出/应答序列）
    fn flush_capture(&self) -> Vec<u8> {
        std::mem::take(&mut *self.capture.lock().unwrap())
    }
}

#[pymethods]
impl PyTerminal {
    /// 创建终端模拟器
    #[new]
    #[pyo3(signature = (cols=80, rows=24, scrollback=10000))]
    fn new(cols: usize, rows: usize, scrollback: usize) -> PyResult<Self> {
        let capture = Arc::new(Mutex::new(Vec::new()));
        let size = TerminalSize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
            dpi: 0,
        };
        let terminal = Terminal::new(
            size,
            Arc::new(EmbeddedConfig { scrollback }),
            "pywezterm",
            env!("CARGO_PKG_VERSION"),
            Box::new(CaptureWriter(capture.clone())),
        );
        // 启用 ConPTY 语义：resize 时内容锚顶、光标绑定文本行（保留 scrollback），
        // 与 Windows ConPTY 的实际 resize 行为一致，避免 resize 后快照光标
        // 与 ConPTY 实测光标不一致。同时抑制初始 title OSC。
        let mut terminal = terminal;
        terminal.enable_conpty_quirks();
        Ok(Self {
            terminal: Mutex::new(terminal),
            capture,
            view_offset: Mutex::new(0),
            selection: Mutex::new(SelectionState::default()),
            mode: Mutex::new(TermModeState {
                cursor_visible: true,
                ..Default::default()
            }),
            mode_tail: Mutex::new(Vec::with_capacity(64)),
            clipboard_cb: Mutex::new(None),
            download_cb: Mutex::new(None),
            device_control_cb: Mutex::new(None),
            notification_cb: Mutex::new(None),
        })
    }

    /// 喂入程序输出的 VT 字节流（同步跟踪 DECSET 模式状态）
    fn feed(&self, data: &[u8]) {
        // 尾部窗口拼接（跨 feed 边界的 DECSET 序列，如 \x1b[?10 + 03h）
        let mut tail = self.mode_tail.lock().unwrap();
        tail.extend_from_slice(data);
        while tail.len() > 64 {
            let drop = tail.len() - 64;
            tail.drain(0..drop);
        }
        update_mode_state(&self.mode, &tail);
        drop(tail);
        self.terminal.lock().unwrap().advance_bytes(data);
    }

    /// 调整终端尺寸（行/列）
    fn resize(&self, cols: usize, rows: usize) {
        let size = TerminalSize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
            dpi: 0,
        };
        self.terminal.lock().unwrap().resize(size);
    }

    /// 光标位置 (row, col, visible)，0-based；计入视图滚动偏移（同
    /// snapshot()/text() 语义，滚动查看历史时光标随内容平移，滚出
    /// 可见区则隐藏——与 Mux.pane_cursor() 一致）。
    fn cursor(&self) -> (usize, usize, bool) {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let c = term.cursor_pos();
        let offset = self._view_size(total, rows);
        let crow_screen = (c.y.max(0) as usize).saturating_add(offset);
        let visible = matches!(c.visibility, wezterm_surface::CursorVisibility::Visible)
            && crow_screen < rows;
        // 列号 clamp 到物理列宽：写满整行后光标在边界外
        let col = c.x.min(screen.physical_cols.saturating_sub(1));
        (crow_screen, col, visible)
    }

    /// 终端当前序列号（每次 feed 递增），供调用方记录渲染基线做脏行差分
    fn current_seqno(&self) -> usize {
        let term = self.terminal.lock().unwrap();
        term.current_seqno()
    }

    /// 自 since_seqno 以来变化过的稳定行号列表（含可见区 + scrollback）。
    /// 宿主据此只重绘涉及变动的逻辑行，替代逐行签名对比。
    fn changed_stable_rows(&self, since_seqno: usize) -> Vec<isize> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        screen.get_changed_stable_rows(0..total as isize, since_seqno)
    }

    /// 可见区逻辑行（按 wrap 正确重组的完整行），每项 =
    /// (first_stable, last_stable, cells)：
    /// - first_stable/last_stable：该逻辑行跨越的稳定行区间（脏行差分用）
    /// - cells：单元格元组一维列表（跨 wrap 物理行已拼接）
    ///
    /// 由 wezterm 的 for_each_logical_line_in_stable_range 完成重组，带超长
    /// 逻辑行防护（宿主不再手写 wrap 拼接）。
    fn logical_lines(&self) -> Vec<(isize, isize, Vec<CellTuple>)> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = self._view_size(total, rows);
        let vis_end = total.saturating_sub(offset);
        let vis_start = vis_end.saturating_sub(rows);
        let start_stable = screen.phys_to_stable_row_index(vis_start);
        let end_stable = screen.phys_to_stable_row_index(vis_end);
        let mut out: Vec<(isize, isize, Vec<CellTuple>)> = Vec::new();
        screen.for_each_logical_line_in_stable_range(
            start_stable..end_stable,
            |stable_range, line_vec| {
                let mut cells: Vec<CellTuple> = Vec::new();
                for line in line_vec {
                    cells.extend(cells_of_line(line));
                }
                out.push((stable_range.start, stable_range.end - 1, cells));
                true
            },
        );
        out
    }

    /// 可见屏幕字符网格：每行 = [(col, ch, fg, bg, bold, italic, underline, reverse, width), ...]
    ///
    /// 视图当前可见区（计入 scroll 偏移），返回纯 cells（供消费方读取）。
    /// 生产构建下 Screen 无 visible_lines()（cfg(test)），通过 phys 行号区间读取。
    fn snapshot(
        &self,
    ) -> PyResult<Vec<Vec<CellTuple>>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = self._view_size(total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        Ok(screen
            .lines_in_phys_range(start..end)
            .iter()
            .map(cells_of_line)
            .collect())
    }

    /// 可见屏幕字符网格，每行 = (wrapped, cells)：wrapped 表示该物理行以折行结尾，
    /// 需与下行拼接为逻辑行（宿主渲染用）。
    fn snapshot_lines(
        &self,
    ) -> PyResult<Vec<(bool, Vec<CellTuple>)>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = self._view_size(total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        Ok(screen
            .lines_in_phys_range(start..end)
            .iter()
            .map(|line| (line.last_cell_was_wrapped(), cells_of_line(line)))
            .collect())
    }

    /// scrollback 历史区字符网格（格式同 snapshot：纯 cells）
    fn scrollback(
        &self,
    ) -> PyResult<Vec<Vec<CellTuple>>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        // 历史区为可见区（物理末尾 physical_rows 行）之上的全部历史；
        // 不滚动视图（offset=0）用原语义，滚动由 snapshot_lines 承担。
        let start = total.saturating_sub(rows);
        Ok(screen
            .lines_in_phys_range(0..start)
            .iter()
            .map(cells_of_line)
            .collect())
    }

    /// scrollback 历史行数
    fn scrollback_count(&self) -> usize {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        screen.scrollback_rows().saturating_sub(screen.physical_rows)
    }

    /// 可见屏幕 ANSI 渲染：每行前 CSI row+1;1H 定位 + SGR 文本，截断末尾空行。
    /// 末尾可选追加光标定位序列（include_cursor=true）。
    fn render_ansi(&self, include_cursor: bool) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = self._view_size(total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        let mut line_results: Vec<(usize, String, bool)> = Vec::new();
        let mut last_non_empty: usize = 0;
        for (li, line) in screen.lines_in_phys_range(start..end).iter().enumerate() {
            let cells = cells_of_line(line);
            let mut line_parts = String::new();
            let mut last_sgr = String::new();
            let mut has_content = false;
            for cell in &cells {
                if cell.1.is_empty() {
                    continue;
                }
                if !has_content && !is_default_cell(cell) {
                    has_content = true;
                }
                let sgr = cell_sgr(cell);
                if sgr != last_sgr {
                    line_parts.push_str(&sgr);
                    last_sgr = sgr;
                }
                line_parts.push_str(&cell.1);
            }
            if !last_sgr.is_empty() {
                line_parts.push_str("\x1b[0m");
            }
            if has_content {
                last_non_empty = li;
            }
            line_results.push((li, line_parts, has_content));
        }
        let mut out = String::new();
        for (li, rendered, _) in line_results {
            if li > last_non_empty {
                break;
            }
            // 定位行首后先清行尾：短内容覆盖长内容时旧字符残留
            out.push_str(&format!("\x1b[{};1H\x1b[K", li + 1));
            out.push_str(&rendered);
        }
        if include_cursor {
            let c = term.cursor_pos();
            // 光标坐标用视口行（c.y + offset）；滚出可见区时不定位只隐藏
            let crow = c.y.max(0) as usize + offset;
            let visible = matches!(c.visibility, wezterm_surface::CursorVisibility::Visible)
                && crow < rows;
            if visible {
                // 列号 clamp 到 [0, cols)：整行写满后光标停在本列边界外
                // （如 100 列宽写满 100 字符 → c.x=100），超界定位会被宿主
                // clamp 到最末列，表现为光标跳到行尾。
                let col = c.x.min(screen.physical_cols.saturating_sub(1));
                out.push_str(&crate::cursor_seq(crow, col, true));
            } else {
                out.push_str("\x1b[?25l");
            }
        }
        out
    }

    /// scrollback 历史区渲染：keep_ansi=false 纯文本（行间 \n，去尾空白）；
    /// keep_ansi=true 每行 SGR 文本 + \r\n（供前端恢复 scrollback）。
    fn render_scrollback(&self, keep_ansi: bool) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let start = total.saturating_sub(rows);
        let mut out = String::new();
        for line in screen.lines_in_phys_range(0..start).iter() {
            let cells = cells_of_line(line);
            if !keep_ansi {
                let mut s = String::new();
                for cell in &cells {
                    s.push_str(&cell.1);
                }
                while s.ends_with(' ') {
                    s.pop();
                }
                out.push_str(&s);
                out.push('\n');
            } else {
                let mut line_parts = String::new();
                let mut last_sgr = String::new();
                for cell in &cells {
                    if cell.1.is_empty() {
                        continue;
                    }
                    let sgr = cell_sgr(cell);
                    if sgr != last_sgr {
                        line_parts.push_str(&sgr);
                        last_sgr = sgr;
                    }
                    line_parts.push_str(&cell.1);
                }
                if !last_sgr.is_empty() {
                    line_parts.push_str("\x1b[0m");
                }
                out.push_str(&line_parts);
                out.push_str("\r\n");
            }
        }
        if !keep_ansi {
            // 剔除末尾空行
            while out.ends_with("\n\n") {
                out.pop();
            }
            if out.ends_with('\n') {
                out.pop();
            }
        }
        out
    }

    /// 滚动查看历史：delta>0 上滚（查看更早），delta<0 回落。clamp 到合法范围。
    fn scroll(&self, delta: i64) {
        let max_hist = self.scrollback_count();
        let mut off = self.view_offset.lock().unwrap();
        let cur = *off as i64;
        let next = (cur + delta).clamp(0, max_hist as i64);
        *off = next as usize;
    }

    /// 回落到底部，恢复跟随最新输出
    fn scroll_to_bottom(&self) {
        *self.view_offset.lock().unwrap() = 0;
    }

    /// 清空 scrollback 历史区（对应 VT 序列 \x1b[3J）
    fn clear_scrollback(&self) {
        let mut term = self.terminal.lock().unwrap();
        term.erase_scrollback();
    }

    /// 重置终端：清空屏幕与 scrollback，恢复初始状态
    ///
    /// 喂 RIS（Reset to Initial State, \x1bc）而非 full_reset()：
    /// full_reset() 只清 keyboard_stack，不清可见内容；
    /// RIS 由 performer 完整处理（擦除屏幕 + scrollback + 重置全部状态）。
    fn reset(&self) {
        let mut term = self.terminal.lock().unwrap();
        term.advance_bytes(b"\x1bc");
    }

    /// 可见屏幕纯文本（每行去尾空白，去掉末尾空行，行间 \n）；计入视图滚动偏移
    fn text(&self) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let offset = self._view_size(total, rows);
        let end = total.saturating_sub(offset);
        let start = end.saturating_sub(rows);
        let mut lines: Vec<String> = Vec::new();
        for line in screen.lines_in_phys_range(start..end) {
            let mut s = String::new();
            for cell in line.visible_cells() {
                s.push_str(cell.str());
            }
            while s.ends_with(' ') {
                s.pop();
            }
            lines.push(s);
        }
        while lines.last().map_or(false, |l| l.is_empty()) {
            lines.pop();
        }
        lines.join("\n")
    }

    /// 键盘按下编码（模式感知），返回应下发到 pty 的字节。
    /// 注意：仅编码返回，不写任何 pty（与 Mux.key_down 不同，后者会
    /// 自动下发到焦点 pane 的 pty）。
    /// key 合法取值：Up/Down/Left/Right/Home/End/Insert/Delete/PageUp/PageDown/
    /// Backspace/Tab/Enter/Esc/Space/F1-F24/单字符；mods 为 KeyModifiers 位
    /// （SHIFT=2 ALT=4 CTRL=8）。
    fn key_down(&self, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let code = parse_keycode(key)?;
        let mods = KeyModifiers::from_bits_truncate(mods);
        self.flush_capture();
        let mut term = self.terminal.lock().unwrap();
        term.key_down(code, mods)
            .map_err(|e| PyRuntimeError::new_err(format!("key_down 编码失败: {e:#}")))?;
        term.flush_sync();
        drop(term);
        Ok(self.flush_capture())
    }

    /// 键盘抬起编码（模式感知），返回应下发到 pty 的字节
    fn key_up(&self, key: &str, mods: u16) -> PyResult<Vec<u8>> {
        let code = parse_keycode(key)?;
        let mods = KeyModifiers::from_bits_truncate(mods);
        self.flush_capture();
        let mut term = self.terminal.lock().unwrap();
        term.key_up(code, mods)
            .map_err(|e| PyRuntimeError::new_err(format!("key_up 编码失败: {e:#}")))?;
        term.flush_sync();
        drop(term);
        Ok(self.flush_capture())
    }

    /// 鼠标事件编码（模式感知），返回应下发到 pty 的字节
    #[pyo3(signature = (x, y, kind="press", button="left", mods=0))]
    fn mouse(&self, x: usize, y: i64, kind: &str, button: &str, mods: u16) -> PyResult<Vec<u8>> {
        let ev = MouseEvent {
            kind: parse_mouse_kind(kind)?,
            x,
            y,
            x_pixel_offset: 0,
            y_pixel_offset: 0,
            button: parse_mouse_button(button)?,
            modifiers: KeyModifiers::from_bits_truncate(mods),
        };
        self.flush_capture();
        let mut term = self.terminal.lock().unwrap();
        term.mouse_event(ev)
            .map_err(|e| PyRuntimeError::new_err(format!("mouse 编码失败: {e:#}")))?;
        term.flush_sync();
        drop(term);
        Ok(self.flush_capture())
    }

    /// 取走捕获缓冲中全部字节（编码输出/应答序列），同步等待后台写入完成
    fn drain_written(&self) -> Vec<u8> {
        self.terminal.lock().unwrap().flush_sync();
        self.flush_capture()
    }

    /// 应用是否接管鼠标（DECSET 1000/1002/1003 追踪模式）
    fn is_mouse_grabbed(&self) -> bool {
        self.terminal.lock().unwrap().is_mouse_grabbed()
    }

    /// 鼠标追踪模式与 SGR 编码状态（feed 时跟踪）：
    /// 返回 (mode, sgr)——mode ∈ {0, 1000, 1002, 1003}，sgr = DECSET 1006 是否启用。
    /// 供宿主订阅时恢复具体模式（is_mouse_grabbed 仅布尔，不含模式号）。
    fn get_mouse_encoding(&self) -> (u16, bool) {
        let m = self.mode.lock().unwrap();
        (m.mouse_tracking, m.sgr_mouse)
    }

    /// 生成终端模式恢复序列（新订阅者重建 xterm 状态用）：
    /// 备用屏幕/鼠标追踪+SGR/光标可见/paste 的 DECSET 恢复前缀。
    fn mode_restore_seq(&self) -> String {
        let m = self.mode.lock().unwrap();
        let mut parts: Vec<String> = Vec::new();
        if m.alt_screen {
            parts.push("\x1b[?1049h".to_string());
        }
        if m.mouse_tracking > 0 {
            parts.push(format!("\x1b[?{}h", m.mouse_tracking));
            if m.sgr_mouse {
                parts.push("\x1b[?1006h".to_string());
            }
        }
        if m.bracketed_paste {
            parts.push("\x1b[?2004h".to_string());
        }
        if !m.cursor_visible {
            parts.push("\x1b[?25l".to_string());
        }
        parts.concat()
    }

    /// 当前键盘编码协议名：xterm / csi-u / win32 / kitty
    fn get_keyboard_encoding(&self) -> String {
        match self.terminal.lock().unwrap().get_keyboard_encoding() {
            KeyboardEncoding::Xterm => "xterm".to_string(),
            KeyboardEncoding::CsiU => "csi-u".to_string(),
            KeyboardEncoding::Win32 => "win32".to_string(),
            KeyboardEncoding::Kitty(_) => "kitty".to_string(),
        }
    }

    /// 是否处于备用屏幕（alternate screen）
    fn is_alt_screen_active(&self) -> bool {
        self.terminal.lock().unwrap().is_alt_screen_active()
    }

    /// 粘贴模式（bracketed paste）是否启用
    fn bracketed_paste_enabled(&self) -> bool {
        self.terminal.lock().unwrap().bracketed_paste_enabled()
    }

    /// 上报焦点状态给应用（DECTCEM 等价；配合 DECSET 1004 焦点报告）
    fn focus_changed(&self, focused: bool) {
        self.terminal.lock().unwrap().focus_changed(focused);
    }

    /// 窗口/图标标题（OSC 0/2）
    fn get_title(&self) -> String {
        self.terminal.lock().unwrap().get_title().to_string()
    }

    /// 当前工作目录（OSC 7），未设置则为 None
    fn get_current_dir(&self) -> Option<String> {
        self.terminal
            .lock()
            .unwrap()
            .get_current_dir()
            .map(|url| url.to_string())
    }

    /// 进度状态（OSC 9），返回字符串标签：none / percentage / error / indeterminate
    fn get_progress(&self) -> (String, Option<u8>) {
        match self.terminal.lock().unwrap().get_progress() {
            Progress::None => ("none".to_string(), None),
            Progress::Percentage(p) => ("percentage".to_string(), Some(p)),
            Progress::Error(p) => ("error".to_string(), Some(p)),
            Progress::Indeterminate => ("indeterminate".to_string(), None),
        }
    }

    /// 语义区（OSC 133 prompt/input/output），返回 (start_y, start_x, end_y, end_x, type)
    fn get_semantic_zones(&self) -> PyResult<Vec<(isize, usize, isize, usize, String)>> {
        let mut term = self.terminal.lock().unwrap();
        let zones = term
            .get_semantic_zones()
            .map_err(|e| PyRuntimeError::new_err(format!("get_semantic_zones 失败: {e:#}")))?;
        let out: Vec<(isize, usize, isize, usize, String)> = zones
            .into_iter()
            .map(|z| {
                let ty = match z.semantic_type {
                    SemanticType::Prompt => "prompt",
                    SemanticType::Input => "input",
                    SemanticType::Output => "output",
                };
                (z.start_y, z.start_x, z.end_y, z.end_x, ty.to_string())
            })
            .collect();
        Ok(out)
    }

    /// 模式感知粘贴下发（bracketed paste 开启时自动包裹）
    fn send_paste(&self, text: &str) -> PyResult<()> {
        self.terminal
            .lock()
            .unwrap()
            .send_paste(text)
            .map_err(|e| PyRuntimeError::new_err(format!("send_paste 失败: {e}")))
    }

    // ---- 选区（stable 行 + 列；跨 scrollback 与可见区）--------------------

    /// 区域选择：anchor → end（stable 坐标），矩形内全部文本
    fn selection_set(
        &self,
        anchor_row: isize,
        anchor_col: usize,
        end_row: isize,
        end_col: usize,
    ) {
        self.selection
            .lock()
            .unwrap()
            .set_region((anchor_row, anchor_col), (end_row, end_col));
    }

    /// 双击选词：以 (row, col) 所在词边界（空白/标点分隔）
    fn selection_select_word(&self, row: isize, col: usize) {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        self.selection.lock().unwrap().select_word(&screen, row, col);
    }

    /// 三击选行：以 (row, col) 所在物理行整行（含换行）
    fn selection_select_line(&self, row: isize, col: usize) {
        self.selection.lock().unwrap().select_line(row, col);
    }

    /// 当前选区纯文本（无选区返回空串）
    fn selection_text(&self) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        self.selection.lock().unwrap().text(&screen)
    }

    /// 是否有活动选区
    fn selection_active(&self) -> bool {
        self.selection.lock().unwrap().is_active()
    }

    /// 清除选区
    fn selection_clear(&self) {
        self.selection.lock().unwrap().clear();
    }

    // ---- 回调绑定（OSC 52 剪贴板写 / 下载 / 设备控制 / 通知）---------------

    /// 设置剪贴板回调：应用发 OSC 52 时把内容交给 Python（取代默认丢弃）。
    /// callback: Callable[[str, Optional[str]], None]（selection 名, 内容）
    fn set_clipboard_callback(&self, py: Python, callback: Py<PyAny>) -> PyResult<()> {
        let clip: Arc<dyn Clipboard> = Arc::new(PyClipboard(callback.clone_ref(py)));
        self.terminal.lock().unwrap().set_clipboard(&clip);
        *self.clipboard_cb.lock().unwrap() = Some(callback);
        Ok(())
    }

    /// 设置下载回调（OSC 8/超链接保存等触发的下载请求）
    fn set_download_callback(&self, py: Python, callback: Py<PyAny>) -> PyResult<()> {
        let dl: Arc<dyn DownloadHandler> = Arc::new(PyDownloadHandler(callback.clone_ref(py)));
        self.terminal.lock().unwrap().set_download_handler(&dl);
        *self.download_cb.lock().unwrap() = Some(callback);
        Ok(())
    }

    /// 设置设备控制回调（DCS 序列）
    fn set_device_control_callback(&self, py: Python, callback: Py<PyAny>) -> PyResult<()> {
        let dc: Box<dyn DeviceControlHandler> = Box::new(PyDeviceControlHandler(callback.clone_ref(py)));
        self.terminal.lock().unwrap().set_device_control_handler(dc);
        *self.device_control_cb.lock().unwrap() = Some(callback);
        Ok(())
    }

    /// 设置通知回调（Alert：Bell/标题/进度等）
    fn set_notification_callback(&self, py: Python, callback: Py<PyAny>) -> PyResult<()> {
        let al: Box<dyn AlertHandler> = Box::new(PyAlertHandler(callback.clone_ref(py)));
        self.terminal.lock().unwrap().set_notification_handler(al);
        *self.notification_cb.lock().unwrap() = Some(callback);
        Ok(())
    }

    /// 强制全量失效（选区渲染 v2 用：选区变化后令全部行标记脏）
    fn make_all_lines_dirty(&self) {
        self.terminal.lock().unwrap().make_all_lines_dirty();
    }

    /// 可见屏幕 SVG 渲染（run 合并 + 压缩）
    ///
    /// compression_level: 0=原样；>=1 压缩（去空 text + 标签间空白折叠）
    /// 渲染与 snapshot() 同视角（计入视图滚动偏移），消费终端模型直接生成。
    fn render_svg(&self, compression_level: u8) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let cols = screen.physical_cols;
        let offset = self._view_size(total, rows);
        let lines = crate::render::visible_lines(screen, offset, rows);
        let svg = crate::render::svg::render_svg_string(&lines, cols, rows);
        crate::render::svg::compress_svg(&svg, compression_level)
    }

    /// 可见屏幕图片渲染（png/jpg/jpeg/bmp）
    ///
    /// scale: 缩放倍数（1.0=CELL_W/CELL_H 像素格，2.0=高清）
    /// fmt: 图片格式（png/jpg/jpeg/bmp）
    /// 渲染与 snapshot() 同视角。
    fn render_image(&self, scale: f64, fmt: &str) -> PyResult<Vec<u8>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let rows = screen.physical_rows;
        let cols = screen.physical_cols;
        let offset = self._view_size(total, rows);
        let lines = crate::render::visible_lines(screen, offset, rows);
        let scale = if scale.is_finite() && scale > 0.0 { scale } else { 1.0 };
        Ok(crate::render::pixmap::render_image_bytes(&lines, cols, rows, scale, fmt))
    }
}

// ---- 回调 trait 实现（把 Python 回调接到 wezterm-term 的 handler）---------

/// Clipboard trait 实现：OSC 52 触发时经 GIL 调 Python 回调。
/// 回调只做剪贴板写入，不反查终端状态（否则与 reader 线程持锁互等死锁）。
/// pub(crate)：Mux.set_focus_selection_callback 复用同一实现。
pub(crate) struct PyClipboard(pub(crate) Py<PyAny>);

impl Clipboard for PyClipboard {
    fn set_contents(
        &self,
        selection: ClipboardSelection,
        data: Option<String>,
    ) -> anyhow::Result<()> {
        let sel = match selection {
            ClipboardSelection::Clipboard => "clipboard",
            ClipboardSelection::PrimarySelection => "primary",
        };
        Python::attach(|py| {
            let bound = self.0.bind(py);
            if let Err(e) = bound.call1((sel, data)) {
                log::error!("clipboard callback 异常: {e}");
                e.print(py);
            }
        });
        Ok(())
    }
}

/// DownloadHandler trait 实现：下载请求 → Python 回调
struct PyDownloadHandler(Py<PyAny>);

impl DownloadHandler for PyDownloadHandler {
    fn save_to_downloads(&self, name: Option<String>, data: Vec<u8>) {
        Python::attach(|py| {
            let bound = self.0.bind(py);
            if let Err(e) = bound.call1((name, data)) {
                log::error!("download callback 异常: {e}");
            }
        });
    }
}

/// DeviceControlHandler trait 实现：DCS 序列 → Python 回调
struct PyDeviceControlHandler(Py<PyAny>);

impl DeviceControlHandler for PyDeviceControlHandler {
    fn handle_device_control(&mut self, control: wezterm_escape_parser::DeviceControlMode) {
        Python::attach(|py| {
            let bound = self.0.bind(py);
            if let Err(e) = bound.call1((format!("{control:?}"),)) {
                log::error!("device control callback 异常: {e}");
            }
        });
    }
}

/// AlertHandler trait 实现：Alert（Bell/标题/进度等）→ Python 回调
struct PyAlertHandler(Py<PyAny>);

impl AlertHandler for PyAlertHandler {
    fn alert(&mut self, alert: Alert) {
        Python::attach(|py| {
            let bound = self.0.bind(py);
            if let Err(e) = bound.call1((format!("{alert:?}"),)) {
                log::error!("notification callback 异常: {e}");
            }
        });
    }
}
