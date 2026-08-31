//! 终端渲染模块 — 自研渲染 utils，不引入重依赖
//!
//! 使用 fontdb（系统字体发现）+ fontdue（字形光栅化）+ tiny-skia（合成/编码），
//! 全部纯 Rust 无 C 库依赖。
//!
//! 渲染直接消费 Terminal 的 visible cells（CellTuple），不经过 JSON 序列化。
//!
//! - svg::render_svg_string：SVG 矢量输出
//! - pixmap::render_image_bytes：像素输出（png/jpg/bmp）

pub mod common;
pub mod font;
pub mod pixmap;
pub mod svg;

use crate::term::CellTuple;

/// 从 Terminal 的 visible lines 提取可见区网格（CellTuple 二维数组）
/// 语义与 snapshot() 一致（计入视图滚动偏移）。
pub fn visible_lines(
    screen: &wezterm_term::screen::Screen,
    view_offset: usize,
    rows: usize,
) -> Vec<Vec<CellTuple>> {
    let total = screen.scrollback_rows();
    let end = total.saturating_sub(view_offset);
    let start = end.saturating_sub(rows);
    screen
        .lines_in_phys_range(start..end)
        .iter()
        .map(|line| {
            let mut cells: Vec<CellTuple> = Vec::new();
            for cell in line.visible_cells() {
                let attrs = cell.attrs();
                cells.push((
                    cell.cell_index(),
                    cell.str().to_string(),
                    color_attr_to_string(attrs.foreground()),
                    color_attr_to_string(attrs.background()),
                    attrs.intensity() == wezterm_term::Intensity::Bold,
                    attrs.italic(),
                    attrs.underline() != wezterm_term::Underline::None,
                    attrs.reverse(),
                    attrs.strikethrough(),
                    cell.width(),
                ));
            }
            cells
        })
        .collect()
}

/// ColorAttribute → 字符串（与 term.rs cell_of_line 一致）
fn color_attr_to_string(c: wezterm_term::color::ColorAttribute) -> String {
    use wezterm_term::color::ColorAttribute;
    match c {
        ColorAttribute::Default => "default".to_string(),
        ColorAttribute::PaletteIndex(i) => format!("p{i}"),
        ColorAttribute::TrueColorWithDefaultFallback(s) => rgb_hex(s.to_srgb_u8()),
        ColorAttribute::TrueColorWithPaletteFallback(s, _) => rgb_hex(s.to_srgb_u8()),
    }
}

fn rgb_hex(rgba: (u8, u8, u8, u8)) -> String {
    format!("#{:02x}{:02x}{:02x}", rgba.0, rgba.1, rgba.2)
}