//! 渲染共享基础 — 颜色解析、网格常量
//!
//! 颜色支持 default / p<N> 调色板索引 / #rrggbb / 6 位 hex；
//! 网格度量与 SVG/ANSI 输出像素级一致（CELL_W=8, CELL_H=17）。

/// 单元格网格度量：行高取 Windows GDI 实测（Consolas 14px 的 tmHeight=17），
/// 与 SVG/ANSI 输出保持像素级一致
pub const CELL_W: usize = 8;
pub const CELL_H: usize = 17;

/// ANSI 16 色调色板（CellTuple fg/bg 的 "pN" 调色板索引格式），
/// 顺序与 wezterm-term 默认调色板一致
const PALETTE: [[u8; 3]; 16] = [
    [0x00, 0x00, 0x00],
    [0xcd, 0x00, 0x00],
    [0x00, 0xcd, 0x00],
    [0xcd, 0xcd, 0x00],
    [0x00, 0x00, 0xee],
    [0xcd, 0x00, 0xcd],
    [0x00, 0xcd, 0xcd],
    [0xe5, 0xe5, 0xe5],
    [0x7f, 0x7f, 0x7f],
    [0xff, 0x00, 0x00],
    [0x00, 0xff, 0x00],
    [0xff, 0xff, 0x00],
    [0x5c, 0x5c, 0xff],
    [0xff, 0x00, 0xff],
    [0x00, 0xff, 0xff],
    [0xff, 0xff, 0xff],
];

/// ANSI 颜色名映射（CellTuple 颜色字符串可能出现的命名色，兼容旧数据）
fn ansi_name_to_rgb(name: &str) -> Option<[u8; 3]> {
    Some(match name {
        "black" => PALETTE[0],
        "red" => PALETTE[1],
        "green" => PALETTE[2],
        "brown" => PALETTE[3],
        "blue" => PALETTE[4],
        "magenta" => PALETTE[5],
        "cyan" => PALETTE[6],
        "white" => PALETTE[7],
        "brightblack" => PALETTE[8],
        "brightred" => PALETTE[9],
        "brightgreen" => PALETTE[10],
        "brightbrown" => PALETTE[11],
        "brightblue" => PALETTE[12],
        "brightmagenta" => PALETTE[13],
        "brightcyan" => PALETTE[14],
        "brightwhite" => PALETTE[15],
        _ => return None,
    })
}

/// 解析颜色字符串 → (r, g, b)；None = default/解析失败（渲染层用回退色）
///
/// 支持（与 Python _resolve_color 语义一致）：
/// - "default" / 空 → None
/// - "p<N>" 调色板索引（wezterm-term 默认 16 色）
/// - "#rrggbb" / 6 位 hex
/// - ANSI 命名色（black/red/.../brightwhite）
pub fn resolve_color(color: &str) -> Option<(u8, u8, u8)> {
    let s = color.trim();
    if s.is_empty() || s == "default" {
        return None;
    }
    // 调色板索引 "pN"
    if let Some(idx) = s.strip_prefix('p') {
        if let Ok(i) = idx.parse::<usize>() {
            if let Some(c) = PALETTE.get(i) {
                return Some((c[0], c[1], c[2]));
            }
        }
        return None;
    }
    // "#rrggbb"（7 字符）
    if let Some(hex) = s.strip_prefix('#') {
        return parse_hex_rgb(hex);
    }
    // 6 位 hex
    if s.len() == 6 {
        if let Some(c) = parse_hex_rgb(s) {
            return Some(c);
        }
    }
    // ANSI 命名色
    ansi_name_to_rgb(s).map(|c| (c[0], c[1], c[2]))
}

/// 解析 6 位 hex 颜色 → (r, g, b)；失败返回 None
fn parse_hex_rgb(hex: &str) -> Option<(u8, u8, u8)> {
    if hex.len() != 6 {
        return None;
    }
    let v = u32::from_str_radix(hex, 16).ok()?;
    Some((((v >> 16) & 0xff) as u8, ((v >> 8) & 0xff) as u8, (v & 0xff) as u8))
}

/// 默认前景色（无 fg 时用）
pub const DEFAULT_FG: (u8, u8, u8) = (0xe5, 0xe5, 0xe5);
/// 默认背景色（无 bg 时用，与 SVG 背景一致）
pub const DEFAULT_BG: (u8, u8, u8) = (0x0c, 0x0c, 0x0c);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_default() {
        assert_eq!(resolve_color("default"), None);
        assert_eq!(resolve_color(""), None);
    }

    #[test]
    fn test_resolve_palette_index() {
        assert_eq!(resolve_color("p0"), Some((0, 0, 0)));
        assert_eq!(resolve_color("p7"), Some((0xe5, 0xe5, 0xe5)));
        assert_eq!(resolve_color("p15"), Some((0xff, 0xff, 0xff)));
        assert_eq!(resolve_color("p16"), None); // 越界
        assert_eq!(resolve_color("px"), None);
    }

    #[test]
    fn test_resolve_hex() {
        assert_eq!(resolve_color("#ff0000"), Some((0xff, 0, 0)));
        assert_eq!(resolve_color("#00ff00"), Some((0, 0xff, 0)));
        assert_eq!(resolve_color("ffffff"), Some((0xff, 0xff, 0xff)));
        assert_eq!(resolve_color("#zzzzzz"), None);
    }

    #[test]
    fn test_resolve_ansi_names() {
        assert_eq!(resolve_color("red"), Some((0xcd, 0, 0)));
        assert_eq!(resolve_color("brightwhite"), Some((0xff, 0xff, 0xff)));
        assert_eq!(resolve_color("bogus"), None);
    }
}
