//! wezterm-surface 增量渲染绑定（Mux 复用器渲染层）
//!
//! 提供 PySurface：把"写格子→增量 Change→ANSI 字节"整套 wezterm 成品暴露给 Python。
//! - set_cell：在 (x,y) 写一个带样式的字符（CursorPosition + 属性 + Text）
//! - get_changes_bytes(seq)：取"自 seq 以来变化"的增量，用 terminfo renderer
//!   转成 ANSI 字节（CUP/属性/文本），返回 (新 seqno, bytes)
//!
//! 复用：`wezterm-surface::Surface.get_changes` 做单元格级 diff（替代手写 build_diff），
//! `termwiz::render::terminfo::TerminfoRenderer` 做 Change→字节（替代手写 cell_line）。

use pyo3::exceptions::{PyRuntimeError};
use pyo3::prelude::*;

use wezterm_surface::{Change, Position};
use wezterm_term::color::{ColorAttribute, RgbColor};
use wezterm_term::{CellAttributes, Intensity, Underline};
use termwiz::caps::{Capabilities, ColorLevel, ProbeHints};
use termwiz::render::terminfo::TerminfoRenderer;

/// 解析颜色字符串 → ColorAttribute（与 term.rs color_attr_to_string 反向）
/// 支持：default / p<index> / #rrggbb
pub(crate) fn parse_color(attr: &str) -> ColorAttribute {
    let s = attr.trim();
    if s == "default" {
        return ColorAttribute::Default;
    }
    if let Some(idx) = s.strip_prefix('p') {
        if let Ok(i) = idx.parse::<u8>() {
            return ColorAttribute::PaletteIndex(i);
        }
    }
    if let Some(hex) = s.strip_prefix('#') {
        if hex.len() == 6 {
            if let Ok(v) = u32::from_str_radix(hex, 16) {
                let r = ((v >> 16) & 0xff) as u8;
                let g = ((v >> 8) & 0xff) as u8;
                let b = (v & 0xff) as u8;
                return ColorAttribute::TrueColorWithDefaultFallback(
                    RgbColor::new_8bpc(r, g, b).to_tuple_rgba(),
                );
            }
        }
    }
    ColorAttribute::Default
}

/// 增量渲染 surface：内部持有 wezterm-surface Surface
#[pyclass(name = "Surface")]
pub struct PySurface {
    surface: wezterm_surface::Surface,
}

impl PySurface {
    /// 记录一次带样式的单元格写入到 (x, y)
    fn put_cell(
        &mut self,
        x: usize,
        y: usize,
        text: &str,
        fg: &str,
        bg: &str,
        bold: bool,
        italic: bool,
        underline: bool,
        reverse: bool,
        strike: bool,
    ) {
        let mut attrs = CellAttributes::default();
        attrs.set_foreground(parse_color(fg));
        attrs.set_background(parse_color(bg));
        if bold {
            attrs.set_intensity(Intensity::Bold);
        }
        if italic {
            attrs.set_italic(true);
        }
        if underline {
            attrs.set_underline(Underline::Single);
        }
        if reverse {
            attrs.set_reverse(true);
        }
        if strike {
            attrs.set_strikethrough(true);
        }
        self.surface.add_change(Change::CursorPosition {
            x: Position::Absolute(x),
            y: Position::Absolute(y),
        });
        self.surface.add_change(Change::AllAttributes(attrs));
        self.surface.add_change(Change::Text(text.to_string()));
    }
}

#[pymethods]
impl PySurface {
    /// 创建增量渲染表面（对应宿主真实终端/画面尺寸）
    #[new]
    #[pyo3(signature = (cols=80, rows=24))]
    fn new(cols: usize, rows: usize) -> Self {
        Self {
            surface: wezterm_surface::Surface::new(cols, rows),
        }
    }

    /// 当前尺寸 (cols, rows)
    fn dimensions(&self) -> (usize, usize) {
        self.surface.dimensions()
    }

    /// 调整表面大小；尺寸变化会丢弃已缓冲的 change 流，下次全量重绘
    fn resize(&mut self, cols: usize, rows: usize) {
        self.surface.resize(cols, rows);
    }

    /// 清空整屏（重置为默认）
    fn clear(&mut self) {
        // 全量重绘：清掉已缓冲 changes，等价 resize 触发的 invalidation
        let (w, h) = self.surface.dimensions();
        self.surface.resize(w, h);
        self.surface
            .add_change(Change::ClearScreen(ColorAttribute::Default));
    }

    /// 当前序列号（增量基线）
    fn current_seqno(&self) -> usize {
        self.surface.current_seqno()
    }

    /// 在 (x, y) 写一个带样式的字符（列/行 0-based）
    #[pyo3(signature = (x, y, text, fg="default", bg="default", bold=false, italic=false, underline=false, reverse=false, strike=false))]
    #[allow(clippy::too_many_arguments)]
    fn set_cell(
        &mut self,
        x: usize,
        y: usize,
        text: &str,
        fg: &str,
        bg: &str,
        bold: bool,
        italic: bool,
        underline: bool,
        reverse: bool,
        strike: bool,
    ) {
        if text.is_empty() {
            return;
        }
        self.put_cell(x, y, text, fg, bg, bold, italic, underline, reverse, strike);
    }

    /// 取"自 since_seqno 以来变化"的增量 ANSI 字节。
    /// 返回 (新 seqno, bytes)：bytes 为空表示无变化。
    /// 若 since_seqno 过旧/超预算，get_changes 会返回全量重绘（仍在后者内）。
    fn get_changes_bytes(&mut self, since_seqno: usize) -> PyResult<(usize, Vec<u8>)> {
        let (seq, changes) = self.surface.get_changes(since_seqno);
        if changes.is_empty() {
            self.surface.flush_changes_older_than(seq);
            return Ok((seq, vec![]));
        }
        let (cols, rows) = self.surface.dimensions();
        let buf = render_changes_bytes(&changes, cols, rows)?;
        self.surface.flush_changes_older_than(seq);
        Ok((seq, buf))
    }

    /// 全量重绘字节（清屏后画表面当前内容）
    ///
    /// 强制以 seqno=0 调用 get_changes：wezterm-surface 的 get_changes(0)
    /// 恒走"seq==0 → repaint_all"分支输出全量。
    fn repaint_bytes(&mut self) -> PyResult<(usize, Vec<u8>)> {
        self.get_changes_bytes(0)
    }
}

/// 把 Change 流渲染为 ANSI 字节（terminfo renderer），供 Surface.py 与 Mux 合成复用。
/// 用 ANSI SGR 渲染（不依赖真实 TERM 环境）：TrueColor 能力 + 强制 ANSI SGR。
///
/// cols/rows 为表面尺寸：Text 写满整行后 Surface 内部光标会推进到行尾边界外
/// （如 100 列写满 → 光标 x=100），全量重绘会把该越界位置输出为 CUP 定位，
/// 宿主终端收到后 clamp 到行尾（表现为光标跳到最末列）。渲染前把越界坐标
/// clamp 回合法范围。
pub(crate) fn render_changes_bytes(changes: &[Change], cols: usize, rows: usize) -> PyResult<Vec<u8>> {
    if changes.is_empty() {
        return Ok(vec![]);
    }
    let hints = ProbeHints::default()
        .color_level(Some(ColorLevel::TrueColor))
        .force_terminfo_render_to_use_ansi_sgr(Some(true));
    let caps = Capabilities::new_with_hints(hints)
        .map_err(|e| PyRuntimeError::new_err(format!("capabilities: {e:#}")))?;
    let mut renderer = TerminfoRenderer::new(caps);
    let mut buf: Vec<u8> = Vec::new();
    let mut rw = BufferWriter { buf: &mut buf };
    // wezterm-surface 的 Change::CursorPosition 语义是 (x=列, y=行)，但 termwiz
    // TerminfoRenderer 渲染绝对定位时把 x 当行、y 当列（line=x/col=y），两者
    // 相反。在渲染前把 Absolute 坐标的 x/y 互换，使输出回到终端标准语义；
    // Relative/EndRelative 在渲染器里走独立的行/列相对移动分支（语义正确），
    // 不参与互换。
    let max_col = cols.saturating_sub(1);
    let max_row = rows.saturating_sub(1);
    let fixed: Vec<Change> = changes
        .iter()
        .map(|c| match c {
            Change::CursorPosition {
                x: Position::Absolute(x),
                y: Position::Absolute(y),
            } => Change::CursorPosition {
                // 先 clamp（列 < cols、行 < rows），再互换
                x: Position::Absolute((*y).min(max_row)),
                y: Position::Absolute((*x).min(max_col)),
            },
            other => other.clone(),
        })
        .collect();
    renderer
        .render_to(&fixed, &mut rw)
        .map_err(|e| PyRuntimeError::new_err(format!("render: {e:#}")))?;
    let mut out = normalize_sgr_colon(&buf);
    // 帧开头强制 SGR 全重置：渲染器内部状态每次从默认开始，但宿主终端的
    // 实际 SGR 是上一帧的最终状态（若上一帧以彩色 run 结束、无行尾重置，
    // 宿主仍停留在该颜色）。不重置会让本帧的默认色 run 沿用旧颜色（多
    // pane 颜色互相干扰）。\x1b[0m 在帧首无害（随后 CUP 定位覆盖位置）。
    out.insert(0, 0x1b);
    out.insert(1, b'[');
    out.insert(2, b'0');
    out.insert(3, b'm');
    Ok(out)
}

/// 把渲染字节里的 ITU T.416 冒号 SGR 颜色序列归一化为 Windows 终端识别的分号格式。
///
/// termwiz escape-parser 对 16 色以上的 PaletteIndex / TrueColor 输出
/// `\x1b[38:5:Nm` / `\x1b[38:2::R:G:Bm`（冒号分隔，T.416 扩展语法），
/// 但 Windows 终端（conhost / OpenConsole / Windows Terminal）只认
/// `\x1b[38;5;Nm` / `\x1b[38;2;R;G;Bm`，冒号形式会被整体丢弃导致颜色丢失。
/// 这里把 `38/48/58` 颜色参数的冒号分隔改为分号分隔，并去掉 T.416 的
/// 空位（`38:2::R:G:B` 双冒号后是 colorspace 空槽）。
fn normalize_sgr_colon(buf: &[u8]) -> Vec<u8> {
    const CSI: u8 = 0x1b;
    let mut out: Vec<u8> = Vec::with_capacity(buf.len());
    let mut i = 0usize;
    while i < buf.len() {
        if buf[i] == CSI && i + 1 < buf.len() && buf[i + 1] == b'[' {
            // 找到本 CSI 参数结束（SGR 以 m 结尾；其他 CSI 原样透传）
            if let Some(rel) = buf[i + 2..].iter().position(|&b| b == b'm') {
                let end = i + 2 + rel;
                let params = &buf[i + 2..end];
                let is_color = params.len() >= 3
                    && matches!(&params[..3], b"38:" | b"48:" | b"58:");
                if is_color {
                    out.extend_from_slice(&[CSI, b'[']);
                    // 整体按冒号分段、跳过 T.416 空槽（如 38:2::R:G:B 的双冒号
                    // 是 colorspace 空位），用分号重连成 Windows 识别的形式
                    let mut first = true;
                    for seg in params.split(|&b| b == b':') {
                        if seg.is_empty() {
                            continue;
                        }
                        if !first {
                            out.push(b';');
                        }
                        out.extend_from_slice(seg);
                        first = false;
                    }
                    out.push(b'm');
                    i = end + 1;
                    continue;
                }
            }
        }
        out.push(buf[i]);
        i += 1;
    }
    out
}

/// 把 Change 渲染结果写入内存缓冲（RenderTty 只需 Write + 尺寸）
struct BufferWriter<'a> {
    buf: &'a mut Vec<u8>,
}

impl std::io::Write for BufferWriter<'_> {
    fn write(&mut self, data: &[u8]) -> std::io::Result<usize> {
        self.buf.extend_from_slice(data);
        Ok(data.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl termwiz::render::RenderTty for BufferWriter<'_> {
    fn get_size_in_cells(&mut self) -> termwiz::Result<(usize, usize)> {
        // 渲染到纯字节缓冲，无真实终端尺寸；返回 0 用不到（仅 Write 生效）
        Ok((0, 0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_sgr_colon_256_fg() {
        // T.416 冒号 256 色 → 分号
        assert_eq!(normalize_sgr_colon(b"\x1b[38:5:208mX"), b"\x1b[38;5;208mX");
        assert_eq!(normalize_sgr_colon(b"\x1b[48:5:17mX"), b"\x1b[48;5;17mX");
    }

    #[test]
    fn test_normalize_sgr_colon_truecolor() {
        // T.416 真彩带空槽（:: 后是 colorspace 空位）→ 分号
        assert_eq!(
            normalize_sgr_colon(b"\x1b[38:2::255:0:255mX"),
            b"\x1b[38;2;255;0;255mX"
        );
        assert_eq!(
            normalize_sgr_colon(b"\x1b[48:2::1:2:3mX"),
            b"\x1b[48;2;1;2;3mX"
        );
    }

    #[test]
    fn test_normalize_sgr_colon_16color_untouched() {
        // 16 色走标准分号/单码，不应改动
        assert_eq!(normalize_sgr_colon(b"\x1b[31mX"), b"\x1b[31mX");
        assert_eq!(normalize_sgr_colon(b"\x1b[1;32mX"), b"\x1b[1;32mX");
        assert_eq!(normalize_sgr_colon(b"\x1b[0mX"), b"\x1b[0mX");
        // 非 SGR CSI 原样透传
        assert_eq!(normalize_sgr_colon(b"\x1b[2J"), b"\x1b[2J");
        assert_eq!(normalize_sgr_colon(b"\x1b[1;1H"), b"\x1b[1;1H");
    }

    #[test]
    fn test_normalize_sgr_colon_mixed() {
        let input = b"\x1b[38:5:208mO\x1b[39mT\x1b[38:2::10:20:30mX";
        let want = b"\x1b[38;5;208mO\x1b[39mT\x1b[38;2;10;20;30mX";
        assert_eq!(normalize_sgr_colon(input), want);
    }
}