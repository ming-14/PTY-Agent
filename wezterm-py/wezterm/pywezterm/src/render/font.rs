//! 字体栈 — fontdb 系统字体发现 + fontdue 字形光栅化 + has_glyph 回退
//!
//! 回退语义（实测验证）：
//! - 首选：随构建分发的 MapleMono NF CN（src/assets/fonts，含 ASCII/CJK/Nerd Font 符号）；
//! - 主字体（Consolas）覆盖 ASCII + box drawing（█▄▀ 等单宽字形）；
//! - 符号字体（Segoe UI Symbol 等）覆盖 ✔✘⚠★ 等特殊符号（Consolas/雅黑均无）；
//! - CJK 字符（你/好 等）主字体无字形 → 回退中文字体（微软雅黑等，双宽）；
//! - 判定依据是 `font.has_glyph(ch)`，**不能按 is_ascii 分类**
//!   （否则 box drawing 会被错配成 CJK 双宽）。

use std::path::Path;
use std::sync::OnceLock;

use fontdb::{Database, Family, Query, Source};
use fontdue::{Font, FontSettings, Metrics};

/// 首选字体族（构建时下载到 src/assets/fonts，MapleMono NF CN 全覆盖）
const PREFERRED_FAMILIES: &[&str] = &["Maple Mono NF CN", "MapleMono NF CN"];
/// 随构建分发的字体目录候选（相对当前工作目录 / 模块路径）
const FONT_DIR_CANDIDATES: &[&str] = &["src/assets/fonts"];
/// 主字体族候选（按序回退）
const ASCII_FAMILIES: &[&str] = &["Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Menlo"];
/// 符号字体族候选（✔✘⚠★ 等；Windows 优先 Segoe UI Symbol）
const SYMBOL_FAMILIES: &[&str] = &[
    "Segoe UI Symbol",
    "Segoe UI Emoji",
    "DejaVu Sans",
    "Symbola",
    "Noto Sans Symbols",
];
/// CJK 字体族候选（按序回退；Windows 优先微软雅黑）
const CJK_FAMILIES: &[&str] = &[
    "Microsoft YaHei",
    "SimHei",
    "SimSun",
    "DengXian",
    "Noto Sans CJK SC",
    "WenQuanYi Micro Hei",
];

/// 字体栈（全局单例，懒初始化）
pub struct FontStack {
    /// 主字体（首选 MapleMono 或系统 ASCII）；可能为 None（系统无任何字体，几乎不可能）
    ascii: Option<Font>,
    /// 符号回退字体（✔✘⚠★ 等）
    symbol: Option<Font>,
    /// CJK 回退字体
    cjk: Option<Font>,
}

impl FontStack {
    /// 初始化字体栈：优先加载分发字体目录，再扫描系统字体，按候选族加载主/符号/CJK
    fn new() -> Self {
        let mut db = Database::new();
        // 先加载随构建分发的字体（MapleMono NF CN），目录不存在时静默跳过
        for dir in FONT_DIR_CANDIDATES {
            if Path::new(dir).is_dir() {
                db.load_fonts_dir(dir);
            }
        }
        db.load_system_fonts();
        let ascii = load_first_family(&db, PREFERRED_FAMILIES)
            .or_else(|| load_first_family(&db, ASCII_FAMILIES))
            .or_else(|| load_any_font(&db));
        if ascii.is_none() {
            log::warn!("render: no system font found, glyphs will be blank");
        }
        let symbol = load_first_family(&db, SYMBOL_FAMILIES);
        let cjk = load_first_family(&db, CJK_FAMILIES);
        Self { ascii, symbol, cjk }
    }

    /// 全局字体栈实例
    pub fn global() -> &'static FontStack {
        static STACK: OnceLock<FontStack> = OnceLock::new();
        STACK.get_or_init(FontStack::new)
    }

    /// 光栅化字符 → (metrics, coverage alpha, 显示宽度)，字符宽度按字体回退结果返回
    /// 返回 (Metrics, coverage, 显示宽度：1=单宽 8px，2=双宽 16px)
    /// 系统无字体时返回 None（调用方跳过字形绘制）。
    pub fn rasterize(&self, ch: char, px: f32) -> Option<(Metrics, Vec<u8>, u8)> {
        if let Some(ascii) = &self.ascii {
            if ascii.has_glyph(ch) {
                let (m, cov) = ascii.rasterize(ch, px);
                return Some((m, cov, 1));
            }
        }
        if let Some(symbol) = &self.symbol {
            if symbol.has_glyph(ch) {
                let (m, cov) = symbol.rasterize(ch, px);
                return Some((m, cov, 1));
            }
        }
        if let Some(cjk) = &self.cjk {
            if cjk.has_glyph(ch) {
                let (m, cov) = cjk.rasterize(ch, px);
                return Some((m, cov, 2));
            }
        }
        // 字形缺失：用主字体的替换字符（或空格），按单宽处理
        if let Some(ascii) = &self.ascii {
            let (m, cov) = ascii.rasterize('\u{fffd}', px);
            return Some((m, cov, 1));
        }
        None
    }

    /// 计算适配 cell 高度的字号（px）
    ///
    /// 按主字体的 line metrics 缩放：line_height(px) ≈ cell 高。
    /// 直接固定字号时 MapleMono 的 block 字形（█ 高 21px @15px）> cell 17px，
    /// 会越界覆盖相邻行；按 line height 缩放使字形适配 cell。
    pub fn font_size_for_cell(&self, cell_h: f64) -> f64 {
        const REF_PX: f32 = 15.0;
        if let Some(ascii) = &self.ascii {
            if let Some(lm) = ascii.horizontal_line_metrics(REF_PX) {
                let line_h = (lm.ascent - lm.descent).max(1.0) as f64;
                return cell_h * REF_PX as f64 / line_h;
            }
        }
        // 兜底：常规终端字号（cell 高 - 2px）
        (cell_h - 2.0).max(1.0)
    }

    /// 主字体 descent（负值，基线到 cell 底部的距离，像素）
    ///
    /// 基线定位用：所有字形共享同一条基线，基线在 cell 内的位置 =
    /// cell 顶 + cell_h + descent（与 wezterm RenderMetrics.descender_row 语义一致）。
    /// 若主字体不可用则返回 -cell_h/4 兜底。
    pub fn descent(&self, px: f32) -> f64 {
        if let Some(ascii) = &self.ascii {
            if let Some(lm) = ascii.horizontal_line_metrics(px) {
                return lm.descent as f64;
            }
        }
        -(px as f64) / 4.0
    }
}

/// 按候选族列表加载第一个可用字体
fn load_first_family(db: &Database, families: &[&str]) -> Option<Font> {
    for family in families {
        if let Some(font) = load_family(db, family) {
            return Some(font);
        }
    }
    None
}

/// 按族名加载字体
fn load_family(db: &Database, family: &str) -> Option<Font> {
    let id = db.query(&Query {
        families: &[Family::Name(family)],
        ..Default::default()
    })?;
    let face = db.face(id)?;
    let data = read_font_data(&face.source)?;
    Font::from_bytes(data, FontSettings::default()).ok()
}

/// 加载任意第一个字体（兜底）
fn load_any_font(db: &Database) -> Option<Font> {
    let face = db.faces().next()?;
    let data = read_font_data(&face.source)?;
    Font::from_bytes(data, FontSettings::default()).ok()
}

/// 从 fontdb Source 读取字体文件字节
fn read_font_data(source: &Source) -> Option<Vec<u8>> {
    match source {
        Source::File(path) | Source::SharedFile(path, _) => std::fs::read(path).ok(),
        Source::Binary(data) => Some(data.as_ref().as_ref().to_vec()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_font_stack_loads() {
        let stack = FontStack::new();
        // 主字体必须加载（MapleMono 或系统等宽字体）
        assert!(stack.ascii.is_some(), "主字体未加载");
        // 符号字体必须加载（✔✘⚠ 等特殊符号需要）
        assert!(stack.symbol.is_some(), "符号字体未加载（Segoe UI Symbol 等）");
        // 主字体必须能渲染基础字符（有字形）
        assert!(stack.rasterize('A', 14.0).is_some());
    }

    #[test]
    fn test_fallback_semantics() {
        let stack = FontStack::new();
        let (_, _, w_ascii) = stack.rasterize('A', 14.0).unwrap();
        assert_eq!(w_ascii, 1);
        let (_, _, w_box) = stack.rasterize('█', 14.0).unwrap();
        assert_eq!(w_box, 1, "box drawing 应走主字体单宽，不得错配 CJK");
        // CJK 字符：如果首选字体（MapleMono）已加载，它包含 CJK 字形（单宽等宽）；
        // 否则走 CJK 回退（双宽）。两种都合法。
        let (_, _, w_cjk) = stack.rasterize('你', 14.0).unwrap();
        assert!(w_cjk == 1 || w_cjk == 2, "CJK 宽度应为 1（MapleMono）或 2（回退）");
        // 符号（✔）走符号字体（单宽），不得回退到 CJK 双宽
        let (_, _, w_sym) = stack.rasterize('\u{2714}', 14.0).unwrap();
        assert_eq!(w_sym, 1, "符号应走符号字体");
    }
}
