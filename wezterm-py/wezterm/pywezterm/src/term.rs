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
use std::sync::{Arc, Mutex};

use wezterm_term::input::{KeyCode, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use wezterm_term::color::{ColorAttribute, ColorPalette};
use wezterm_term::{
    Intensity, Line, Terminal, TerminalConfiguration, TerminalSize, Underline,
};

/// 写入捕获缓冲的 writer：wezterm 编码的输入/应答字节统一被捕获，
/// 供 Python 侧决定如何下发。
#[derive(Clone, Default)]
struct CaptureWriter(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for CaptureWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// 内嵌终端配置：颜色走默认调色板，滚动行数可配
#[derive(Debug)]
struct EmbeddedConfig {
    scrollback: usize,
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
type CellTuple = (usize, String, String, String, bool, bool, bool, bool, bool, usize);

/// 单行可见格 → 单元格元组列表
fn cells_of_line(line: &Line) -> Vec<CellTuple> {
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

/// 解析按键描述字符串 → KeyCode
fn parse_keycode(s: &str) -> PyResult<KeyCode> {
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
fn parse_mouse_kind(s: &str) -> PyResult<MouseEventKind> {
    Ok(match s {
        "press" => MouseEventKind::Press,
        "release" => MouseEventKind::Release,
        "move" => MouseEventKind::Move,
        _ => return Err(PyValueError::new_err(format!("未知鼠标事件类型: {s:?}"))),
    })
}

/// 解析鼠标按钮
fn parse_mouse_button(s: &str) -> PyResult<MouseButton> {
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

/// 终端模拟器实例（Mutex 包裹以支持多线程访问）
#[pyclass(name = "Terminal")]
pub struct PyTerminal {
    terminal: Mutex<Terminal>,
    capture: Arc<Mutex<Vec<u8>>>,
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
        // 与 ConPTY 实测光标不一致（"光标在输出中间" bug 的根因，见
        // tests/e2e/test_resize_cursor_sync.py）。同时抑制初始 title OSC。
        let mut terminal = terminal;
        terminal.enable_conpty_quirks();
        Ok(Self {
            terminal: Mutex::new(terminal),
            capture,
        })
    }

    /// 喂入程序输出的 VT 字节流
    fn feed(&self, data: &[u8]) {
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

    /// 光标位置 (row, col, visible)，0-based
    fn cursor(&self) -> (usize, usize, bool) {
        let c = self.terminal.lock().unwrap().cursor_pos();
        let visible = matches!(c.visibility, wezterm_surface::CursorVisibility::Visible);
        (c.y as usize, c.x, visible)
    }

    /// 可见屏幕字符网格：每行 = [(col, ch, fg, bg, bold, italic, underline, reverse, width), ...]
    ///
    /// 生产构建下 Screen 无 visible_lines()（cfg(test)），
    /// 通过 phys 行号区间（末尾 physical_rows 行）读取可见区。
    fn snapshot(
        &self,
    ) -> PyResult<Vec<Vec<CellTuple>>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let start = total.saturating_sub(screen.physical_rows);
        Ok(screen
            .lines_in_phys_range(start..total)
            .iter()
            .map(cells_of_line)
            .collect())
    }

    /// scrollback 历史区字符网格（格式同 snapshot）
    fn scrollback(
        &self,
    ) -> PyResult<Vec<Vec<CellTuple>>> {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let start = total.saturating_sub(screen.physical_rows);
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

    /// 可见屏幕纯文本（每行去尾空白，去掉末尾空行，行间 \n）
    fn text(&self) -> String {
        let term = self.terminal.lock().unwrap();
        let screen = term.screen();
        let total = screen.scrollback_rows();
        let start = total.saturating_sub(screen.physical_rows);
        let mut lines: Vec<String> = Vec::new();
        for line in screen.lines_in_phys_range(start..total) {
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

    /// 键盘按下编码（模式感知），返回应下发到 pty 的字节
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

    fn flush_capture(&self) -> Vec<u8> {
        std::mem::take(&mut *self.capture.lock().unwrap())
    }
}
