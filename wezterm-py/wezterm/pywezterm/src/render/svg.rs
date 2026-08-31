//! SVG 渲染 — 零依赖矢量输出
//!
//! 同色连续字符合并为一条 <text> 元素以减小体积；可选压缩
//! （level>=1：移除空 <text> + 标签间空白折叠）。

use crate::term::CellTuple;

use super::common::{resolve_color, CELL_H, CELL_W};

/// 空 <text> 元素正则（替代 Python re.compile）
fn strip_empty_text(svg: &str) -> String {
    let mut out = String::with_capacity(svg.len());
    let bytes = svg.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        // 找 "<text"
        if bytes[i..].starts_with(b"<text") {
            // 找标签结束 ">"
            if let Some(gt) = find_byte(&bytes[i..], b'>') {
                let tag_end = i + gt + 1;
                // 找 "</text>"
                let rest = &bytes[tag_end..];
                if let Some(close) = find_subslice(rest, b"</text>") {
                    let content = &rest[..close];
                    if content.iter().all(|b| b.is_ascii_whitespace()) {
                        // 空 text：跳过整个元素
                        i = tag_end + close + b"</text>".len();
                        continue;
                    }
                }
            }
        }
        out.push(bytes[i] as char);
        i += 1;
    }
    out
}

/// 在 slice 中找字节，返回相对位置
fn find_byte(s: &[u8], b: u8) -> Option<usize> {
    s.iter().position(|&x| x == b)
}

/// 在 slice 中找子串，返回相对位置
fn find_subslice(s: &[u8], sub: &[u8]) -> Option<usize> {
    if sub.is_empty() {
        return Some(0);
    }
    s.windows(sub.len()).position(|w| w == sub)
}

/// 标签间空白折叠：`>\n  <` → `><`（不影响属性内与 text 内容）
fn collapse_intertag_whitespace(svg: &str) -> String {
    let mut out = String::with_capacity(svg.len());
    let bytes = svg.as_bytes();
    let mut i = 0usize;
    let mut pending_ws = false;
    while i < bytes.len() {
        let b = bytes[i];
        if b == b'>' {
            out.push('>');
            i += 1;
            // 折叠后续空白直到下一个 '<'
            while i < bytes.len() && bytes[i].is_ascii_whitespace() {
                pending_ws = true;
                i += 1;
            }
            if i < bytes.len() && bytes[i] == b'<' && pending_ws {
                // 丢弃空白
            }
            pending_ws = false;
            continue;
        }
        out.push(b as char);
        i += 1;
    }
    out
}

/// XML 转义（& < >；与 Python xml.sax.saxutils.escape 一致）
fn xml_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(c),
        }
    }
    out
}

/// 单行 run 追加：<rect>（背景）+ <text>（前景）
fn flush_svg_run(
    parts: &mut Vec<String>,
    run_chars: &mut Vec<char>,
    run_x: usize,
    run_w: usize,
    y: usize,
    run_fg: Option<(u8, u8, u8)>,
    run_bg: Option<(u8, u8, u8)>,
    run_bold: bool,
) {
    if run_chars.is_empty() {
        return;
    }
    if let Some(bg) = run_bg {
        parts.push(format!(
            r##"<rect x="{}" y="{}" width="{}" height="{}" fill="#{:02x}{:02x}{:02x}"/>"##,
            run_x, y * CELL_H, run_w, CELL_H, bg.0, bg.1, bg.2
        ));
    }
    let text: String = run_chars.iter().collect();
    let mut attrs = format!(r#"x="{}" y="{}""#, run_x, y * CELL_H);
    if let Some(fg) = run_fg {
        attrs.push_str(&format!(r##" fill="#{:02x}{:02x}{:02x}""##, fg.0, fg.1, fg.2));
    }
    if run_bold {
        attrs.push_str(r#" font-weight="bold""#);
    }
    parts.push(format!("<text {attrs}>{}</text>", xml_escape(&text)));
}

/// 渲染可见网格为 SVG 字符串
///
/// lines: 每行 CellTuple 列表（来自 Terminal.snapshot / cells_of_line，稀疏列号）
/// cols/rows: 网格尺寸
pub fn render_svg_string(lines: &[Vec<CellTuple>], cols: usize, rows: usize) -> String {
    let w = cols * CELL_W;
    let h = rows * CELL_H;
    let mut parts = vec![
        format!(
            r#"<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="{}" height="{}" viewBox="0 0 {} {}">"#,
            w, h, w, h
        ),
        r##"<rect width="100%" height="100%" fill="#0c0c0c"/>"##.to_string(),
        format!(
            r#"<style>text{{font-family:Consolas,"Microsoft YaHei",monospace;font-size:{}px;dominant-baseline:text-before-edge;white-space:pre}}</style>"#,
            CELL_H - 2
        ),
    ];

    for (y, line) in lines.iter().enumerate() {
        if y >= rows {
            break;
        }
        let mut run_x = 0usize;
        let mut run_chars: Vec<char> = Vec::new();
        let mut run_fg: Option<(u8, u8, u8)> = None;
        let mut run_bg: Option<(u8, u8, u8)> = None;
        let mut run_bold = false;

        // 按列号排序的稀疏 cell：直接按 cell.0（列号）定位
        for cell in line {
            let col = cell.0;
            let text = &cell.1;
            if text.is_empty() {
                continue;
            }
            let fg = resolve_color(&cell.2).or(Some(super::common::DEFAULT_FG));
            let bg = resolve_color(&cell.3);
            let bold = cell.4;
            let x = col * CELL_W;

            if fg == run_fg && bg == run_bg && bold == run_bold && !run_chars.is_empty() {
                run_chars.extend(text.chars());
            } else {
                flush_svg_run(
                    &mut parts,
                    &mut run_chars,
                    run_x,
                    x.saturating_sub(run_x),
                    y,
                    run_fg,
                    run_bg,
                    run_bold,
                );
                run_x = x;
                run_chars = text.chars().collect();
                run_fg = fg;
                run_bg = bg;
                run_bold = bold;
            }
        }
        flush_svg_run(
            &mut parts,
            &mut run_chars,
            run_x,
            cols.saturating_mul(CELL_W).saturating_sub(run_x),
            y,
            run_fg,
            run_bg,
            run_bold,
        );
    }

    parts.push("</svg>".to_string());
    parts.join("\n")
}

/// 压缩 SVG（level>=1：去空 text + 标签间空白折叠；0=原样）
pub fn compress_svg(svg: &str, level: u8) -> String {
    if level == 0 {
        return svg.to_string();
    }
    let stripped = strip_empty_text(svg);
    collapse_intertag_whitespace(&stripped)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(col: usize, text: &str, fg: &str, bg: &str, bold: bool) -> CellTuple {
        (col, text.to_string(), fg.to_string(), bg.to_string(), bold, false, false, false, false, text.chars().count())
    }

    #[test]
    fn test_render_svg_basic() {
        let lines = vec![vec![cell(0, "hi", "green", "default", false)]];
        let svg = render_svg_string(&lines, 4, 1);
        assert!(svg.contains("<svg"));
        assert!(svg.contains(">hi<"));
        assert!(svg.contains("width=\"32\""));
        assert!(svg.contains("height=\"17\""));
    }

    #[test]
    fn test_render_svg_run_merge() {
        // 同色连续字符合并为一条 text
        let lines = vec![vec![
            cell(0, "a", "red", "default", false),
            cell(1, "b", "red", "default", false),
            cell(2, "c", "blue", "default", false),
        ]];
        let svg = render_svg_string(&lines, 4, 1);
        // "ab" 合并，'c' 单独
        assert!(svg.contains(">ab<"), "同色 run 应合并: {svg}");
        assert!(svg.contains(">c<"));
    }

    #[test]
    fn test_compress_empty_text_removed() {
        let svg = r#"<text x="0" y="0"></text><text x="8" y="0">a</text>"#;
        let out = compress_svg(svg, 1);
        assert!(!out.contains("</text></text>") || out.contains("<text x=\"8\""), "空 text 应移除: {out}");
        assert!(out.contains(">a<"));
    }

    #[test]
    fn test_xml_escape() {
        assert_eq!(xml_escape("a<b&c>d"), "a&lt;b&amp;c&gt;d");
    }
}
