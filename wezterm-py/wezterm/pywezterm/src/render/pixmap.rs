//! 像素渲染 — tiny-skia 合成 + fontdue 字形光栅化 + PNG/JPG/BMP 编码
//!
//! 全部纯 Rust 无 C 库依赖。
//! - 背景矩形：tiny-skia fill_rect
//! - 字形：fontdue rasterize → 预乘 alpha blit 到 Pixmap
//! - 编码：tiny-skia encode_png / image crate 编码 JPG/BMP

use crate::term::CellTuple;

use super::common::{resolve_color, DEFAULT_BG, DEFAULT_FG, CELL_H, CELL_W};
use super::font::FontStack;

/// 渲染终端可见网格为图片字节（png/jpg/jpeg/bmp）
///
/// 两遍渲染（对齐 wezterm 的 layer 模型）：
/// 1. 背景层：所有 cell 的背景矩形（含 reverse 交换）；
/// 2. 字形层：字形/block 几何，绘制在背景之上。字形按**基线定位**
///    （descender 允许溢出到下一行背景之上，与 wezterm 一致），
///    block elements 几何填满 cell（相邻无缝连续）。
pub fn render_image_bytes(
    lines: &[Vec<CellTuple>],
    cols: usize,
    rows: usize,
    scale: f64,
    fmt: &str,
) -> Vec<u8> {
    let img_w = (cols as f64 * CELL_W as f64 * scale) as u32;
    let img_h = (rows as f64 * CELL_H as f64 * scale) as u32;
    let cell_w = (CELL_W as f64 * scale) as u32;
    let cell_h = (CELL_H as f64 * scale) as u32;

    let mut pix = tiny_skia::Pixmap::new(img_w, img_h).expect("Pixmap creation failed");
    let font_stack = FontStack::global();
    // 字号按主字体 line metrics 适配 cell 高度：
    // 直接固定字号（如 15px）时 MapleMono 的 block 字形高达 21px > cell 17px，
    // 越界覆盖相邻行；按 line height 缩放使字形适配 cell
    let font_size = font_stack.font_size_for_cell(cell_h as f64);
    // 基线 = cell 顶 + cell_h + descent（descent 为负；与 wezterm descender_row 一致）
    let descent = font_stack.descent(font_size as f32);

    // 背景清屏
    let mut paint = tiny_skia::Paint::default();
    paint.set_color_rgba8(DEFAULT_BG.0, DEFAULT_BG.1, DEFAULT_BG.2, 255);
    pix.fill_rect(
        tiny_skia::Rect::from_xywh(0.0, 0.0, img_w as f32, img_h as f32).unwrap(),
        &paint,
        tiny_skia::Transform::identity(),
        None,
    );

    // ---- 第一遍：背景层（所有 cell 背景矩形） ----
    for (y, line) in lines.iter().enumerate() {
        if y >= rows {
            break;
        }
        let yp = (y as f64 * cell_h as f64) as i32;
        for cell in line {
            let text = &cell.1;
            if text.is_empty() {
                continue;
            }
            let cw = cell.9.max(1);
            let xp = (cell.0 as f64 * cell_w as f64) as i32;
            let w = (cw as f64 * cell_w as f64) as u32;

            let bg = if cell.7 {
                // reverse：fg 当背景
                Some(resolve_color(&cell.2).unwrap_or(DEFAULT_FG))
            } else {
                resolve_color(&cell.3)
            };
            if let Some(bg) = bg {
                paint.set_color_rgba8(bg.0, bg.1, bg.2, 255);
                pix.fill_rect(
                    tiny_skia::Rect::from_xywh(xp as f32, yp as f32, w as f32, cell_h as f32)
                        .unwrap(),
                    &paint,
                    tiny_skia::Transform::identity(),
                    None,
                );
            }
        }
    }

    // ---- 第二遍：字形层（字形/block 几何 + 下划线/删除线） ----
    for (y, line) in lines.iter().enumerate() {
        if y >= rows {
            break;
        }
        let yp = (y as f64 * cell_h as f64) as i32;
        for cell in line {
            let text = &cell.1;
            if text.is_empty() {
                continue;
            }
            let cw = cell.9.max(1);
            let xp = (cell.0 as f64 * cell_w as f64) as i32;
            let w = (cw as f64 * cell_w as f64) as u32;

            let fg = if cell.7 {
                resolve_color(&cell.3).unwrap_or(DEFAULT_BG)
            } else {
                resolve_color(&cell.2).unwrap_or(DEFAULT_FG)
            };
            let (fg_r, fg_g, fg_b) = fg;
            let bold = cell.4;
            let ch = text.chars().next().unwrap_or(' ');

            // block elements（U+2580–U+259F）几何绘制：按 cell 尺寸直接填充，
            // 不依赖字体字形（MapleMono 的 block 字形高 20-21px > cell 17px，
            // 用字形会越界；几何填充保证相邻 block 无缝连续且不重叠）
            if is_block_element(ch) {
                draw_block_element(
                    &mut pix,
                    ch,
                    xp,
                    yp,
                    w as i32,
                    cell_h as i32,
                    fg_r,
                    fg_g,
                    fg_b,
                );
            } else {
                blit_glyph(
                    &mut pix,
                    font_stack,
                    ch,
                    font_size,
                    xp,
                    yp,
                    w as i32,
                    cell_h as i32,
                    descent,
                    fg_r,
                    fg_g,
                    fg_b,
                    bold,
                );
            }

            // 下划线
            if cell.6 {
                paint.set_color_rgba8(fg_r, fg_g, fg_b, 255);
                let ul_y = (yp + cell_h as i32 - 2).max(yp);
                pix.fill_rect(
                    tiny_skia::Rect::from_xywh(
                        xp as f32,
                        ul_y as f32,
                        w as f32,
                        (1.0_f64.max(scale)) as f32,
                    )
                    .unwrap(),
                    &paint,
                    tiny_skia::Transform::identity(),
                    None,
                );
            }

            // 删除线
            if cell.8 {
                paint.set_color_rgba8(fg_r, fg_g, fg_b, 255);
                let st_y = yp + cell_h as i32 / 2;
                pix.fill_rect(
                    tiny_skia::Rect::from_xywh(
                        xp as f32,
                        st_y as f32,
                        w as f32,
                        (1.0_f64.max(scale)) as f32,
                    )
                    .unwrap(),
                    &paint,
                    tiny_skia::Transform::identity(),
                    None,
                );
            }
        }
    }

    // 编码：png 走 tiny-skia（零依赖）；jpg/jpeg/bmp 用 image crate（workspace 已有）
    encode_image(&pix, fmt)
}

/// 按格式编码 pixmap：png 用 tiny-skia；jpg/jpeg/bmp 用 image crate
fn encode_image(pix: &tiny_skia::Pixmap, fmt: &str) -> Vec<u8> {
    match fmt {
        "jpg" | "jpeg" => {
            // JPEG 不支持 alpha：RGBA → RGB（黑底合成）
            let w = pix.width();
            let h = pix.height();
            let mut rgb = Vec::with_capacity((w * h * 3) as usize);
            for p in pix.pixels() {
                let c = p.demultiply();
                rgb.extend_from_slice(&[c.red(), c.green(), c.blue()]);
            }
            let img = image::RgbImage::from_raw(w, h, rgb).expect("RGB buffer size mismatch");
            let mut out = Vec::new();
            img.write_to(
                &mut std::io::Cursor::new(&mut out),
                image::ImageFormat::Jpeg,
            )
            .expect("JPEG encoding failed");
            out
        }
        "bmp" => {
            let w = pix.width();
            let h = pix.height();
            let mut rgb = Vec::with_capacity((w * h * 3) as usize);
            for p in pix.pixels() {
                let c = p.demultiply();
                rgb.extend_from_slice(&[c.red(), c.green(), c.blue()]);
            }
            let img = image::RgbImage::from_raw(w, h, rgb).expect("RGB buffer size mismatch");
            let mut out = Vec::new();
            img.write_to(
                &mut std::io::Cursor::new(&mut out),
                image::ImageFormat::Bmp,
            )
            .expect("BMP encoding failed");
            out
        }
        _ => pix.encode_png().expect("PNG encoding failed"),
    }
}

/// block elements 判定（U+2580–U+259F）
fn is_block_element(ch: char) -> bool {
    let cp = ch as u32;
    (0x2580..=0x259F).contains(&cp)
}

/// 按 f32 坐标填充一个矩形（几何 block 绘制原语，alpha 可选）
///
/// 坐标用 f32：cell 高 17px 不是 8 的倍数（17/8=2.125），整数截断会让
/// 1/8 块偏小产生缝隙；f32 精确对齐 wezterm 的 BlockCoord 语义。
fn fill_rect_f(
    pix: &mut tiny_skia::Pixmap,
    x: f32,
    y: f32,
    w: f32,
    h: f32,
    r: u8,
    g: u8,
    b: u8,
    alpha: u8,
) {
    if w <= 0.0 || h <= 0.0 {
        return;
    }
    let mut paint = tiny_skia::Paint::default();
    paint.set_color_rgba8(r, g, b, alpha);
    // 与 wezterm 一致：关闭抗锯齿，保证相邻 cell 像素精确衔接、无缝连续
    paint.anti_alias = false;
    pix.fill_rect(
        tiny_skia::Rect::from_xywh(x, y, w, h).unwrap(),
        &paint,
        tiny_skia::Transform::identity(),
        None,
    );
}

/// block elements 几何绘制（U+2580–U+259F）
///
/// 完全对齐 wezterm customglyph 语义：
/// - 所有 block 按 **cell 尺寸**几何填充（1/8 网格坐标），不依赖字体字形
///   （MapleMono 的 block 字形高 20-21px > cell 17px，用字形必然越界重叠）；
/// - █ 等实心块 = 不透明填充；░▒▓ 阴影 = **整格填充 fg 色 + alpha 透明度**
///   （25%/50%/75%），不是点阵——保证相邻 cell 无缝连续（进度条语义）；
/// - 填充关闭抗锯齿，相邻 cell 像素精确衔接，不会重叠也不会留缝。
fn draw_block_element(
    pix: &mut tiny_skia::Pixmap,
    ch: char,
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    r: u8,
    g: u8,
    b: u8,
) {
    let cp = ch as u32;
    let (fw, fh) = (w as f32, h as f32);
    let x8 = fw / 8.0;
    let y8 = fh / 8.0;
    let (fx, fy) = (x as f32, y as f32);

    // 实心填充辅助（不透明）
    let solid = |pix: &mut tiny_skia::Pixmap, x0: f32, x1: f32, y0: f32, y1: f32| {
        fill_rect_f(pix, fx + x0, fy + y0, x1 - x0, y1 - y0, r, g, b, 255);
    };
    // 阴影填充辅助（整格 + alpha）
    let shade = |pix: &mut tiny_skia::Pixmap, alpha: u8| {
        fill_rect_f(pix, fx, fy, fw, fh, r, g, b, alpha);
    };

    // 1/8 网格分块（与 wezterm Block::UpperBlock/LowerBlock/LeftBlock/RightBlock 一致）
    match cp {
        // 上半块族：1..8 个八分之一
        0x2580 => solid(pix, 0.0, fw, 0.0, 4.0 * y8), // UPPER 4/8
        0x2581 => solid(pix, 0.0, fw, fh - 1.0 * y8, fh),
        0x2582 => solid(pix, 0.0, fw, fh - 2.0 * y8, fh),
        0x2583 => solid(pix, 0.0, fw, fh - 3.0 * y8, fh),
        0x2584 => solid(pix, 0.0, fw, fh - 4.0 * y8, fh),
        0x2585 => solid(pix, 0.0, fw, fh - 5.0 * y8, fh),
        0x2586 => solid(pix, 0.0, fw, fh - 6.0 * y8, fh),
        0x2587 => solid(pix, 0.0, fw, fh - 7.0 * y8, fh),
        // 全块
        0x2588 => solid(pix, 0.0, fw, 0.0, fh),
        // 左块族
        0x2589 => solid(pix, 0.0, 7.0 * x8, 0.0, fh),
        0x258a => solid(pix, 0.0, 6.0 * x8, 0.0, fh),
        0x258b => solid(pix, 0.0, 5.0 * x8, 0.0, fh),
        0x258c => solid(pix, 0.0, 4.0 * x8, 0.0, fh),
        0x258d => solid(pix, 0.0, 3.0 * x8, 0.0, fh),
        0x258e => solid(pix, 0.0, 2.0 * x8, 0.0, fh),
        0x258f => solid(pix, 0.0, 1.0 * x8, 0.0, fh),
        // 右半块
        0x2590 => solid(pix, 4.0 * x8, fw, 0.0, fh),
        // 上 1/8
        0x2594 => solid(pix, 0.0, fw, 0.0, 1.0 * y8),
        // 右 1/8
        0x2595 => solid(pix, fw - 1.0 * x8, fw, 0.0, fh),
        // 阴影：整格填充 + alpha（wezterm BlockAlpha 语义）
        0x2591 => shade(pix, 64),  // Light 25%
        0x2592 => shade(pix, 128), // Medium 50%
        0x2593 => shade(pix, 192), // Dark 75%
        // 象限块（2x2 网格）
        0x2596 => solid(pix, 0.0, 4.0 * x8, 4.0 * y8, fh), // LL
        0x2597 => solid(pix, 4.0 * x8, fw, 4.0 * y8, fh),  // LR
        0x2598 => solid(pix, 0.0, 4.0 * x8, 0.0, 4.0 * y8), // UL
        0x2599 => {
            solid(pix, 0.0, 4.0 * x8, 0.0, 4.0 * y8);
            solid(pix, 0.0, 4.0 * x8, 4.0 * y8, fh);
            solid(pix, 4.0 * x8, fw, 4.0 * y8, fh);
        }
        0x259a => {
            solid(pix, 0.0, 4.0 * x8, 0.0, 4.0 * y8);
            solid(pix, 4.0 * x8, fw, 4.0 * y8, fh);
        }
        0x259b => {
            solid(pix, 0.0, 4.0 * x8, 0.0, 4.0 * y8);
            solid(pix, 4.0 * x8, fw, 0.0, 4.0 * y8);
            solid(pix, 0.0, 4.0 * x8, 4.0 * y8, fh);
        }
        0x259c => {
            solid(pix, 0.0, 4.0 * x8, 0.0, 4.0 * y8);
            solid(pix, 4.0 * x8, fw, 0.0, 4.0 * y8);
            solid(pix, 4.0 * x8, fw, 4.0 * y8, fh);
        }
        0x259d => solid(pix, 4.0 * x8, fw, 0.0, 4.0 * y8), // UR
        0x259e => {
            solid(pix, 4.0 * x8, fw, 0.0, 4.0 * y8);
            solid(pix, 0.0, 4.0 * x8, 4.0 * y8, fh);
        }
        0x259f => {
            solid(pix, 4.0 * x8, fw, 0.0, 4.0 * y8);
            solid(pix, 0.0, 4.0 * x8, 4.0 * y8, fh);
            solid(pix, 4.0 * x8, fw, 4.0 * y8, fh);
        }
        _ => {
            // 未知 block：走全块（保守）
            solid(pix, 0.0, fw, 0.0, fh);
        }
    }
}

/// 将字形 blit 到 pixmap 指定位置（SourceOver 预乘 alpha 混合）
///
/// fontdue 坐标语义（实测确认）：
/// - metrics.xmin/ymin 是位图**边缘**相对字原点的偏移（像素单位，已是最终尺寸）；
/// - ymin 是位图**底部**边缘偏移（正=在基线上方，负=下方）。
///
/// 定位（对齐 wezterm RenderMetrics 公式）：
/// - 基线在 cell 内的位置 = cell_h + descender（descender 为负，即 cell 底向上）；
/// - 位图顶行 = 基线 - (ymin + height)。
/// 字形 descender（如 g/j/q/y）允许溢出到下一行背景之上——wezterm 的 layer
/// 模型先画背景再画字形，溢出部分不会被后续行覆盖。
/// 仅裁剪到整图边界（不裁剪到 cell 边界，保留 descender 视觉）。
/// 混合：SourceOver（out = src + dst*(1-src_a)），避免半透明边缘与背景脱节产生白边。
#[allow(clippy::too_many_arguments)]
fn blit_glyph(
    pix: &mut tiny_skia::Pixmap,
    font_stack: &FontStack,
    ch: char,
    font_size: f64,
    xp: i32,
    yp: i32,
    _cell_w: i32,
    cell_h: i32,
    descender: f64,
    fg_r: u8,
    fg_g: u8,
    fg_b: u8,
    bold: bool,
) {
    let Some((metrics, coverage, _)) = font_stack.rasterize(ch, font_size as f32) else {
        return;
    };
    let gw = metrics.width as i32;
    let gh = metrics.height as i32;
    if gw <= 0 || gh <= 0 {
        return;
    }

    // 基线定位（wezterm 公式）：
    // 基线 = cell 顶 + cell_h + descender；位图顶 = 基线 - (ymin + height)
    let baseline_y = yp + cell_h + descender.round() as i32;
    let base_x = xp + metrics.xmin;
    let base_y = baseline_y - metrics.ymin - gh;

    // 仅裁剪整图边界；允许字形溢出 cell（descender 沉到下一行背景上）
    let img_w = pix.width() as i32;
    let img_h = pix.height() as i32;
    for py in 0..gh {
        let dy = base_y + py;
        if dy < 0 || dy >= img_h {
            continue;
        }
        for px in 0..gw {
            let a = coverage[(py * gw + px) as usize];
            if a == 0 {
                continue;
            }
            let dx = base_x + px;
            if dx < 0 || dx >= img_w {
                continue;
            }
            blend_pixel(pix, dx, dy, fg_r, fg_g, fg_b, a);
            if bold {
                let dx2 = dx + 1;
                if dx2 >= 0 && dx2 < img_w {
                    blend_pixel(pix, dx2, dy, fg_r, fg_g, fg_b, a);
                }
            }
        }
    }
}

/// 单像素 SourceOver 混合：out = src + dst*(1 - src_a)（预乘 alpha）
fn blend_pixel(
    pix: &mut tiny_skia::Pixmap,
    dx: i32,
    dy: i32,
    fg_r: u8,
    fg_g: u8,
    fg_b: u8,
    a: u8,
) {
    use tiny_skia::PremultipliedColorU8;

    let idx = (dy as u32 * pix.width() + dx as u32) as usize;
    let dst = pix.pixels_mut()[idx];
    if a == 255 {
        // 不透明：直接覆盖（预乘后 RGB==原色）
        pix.pixels_mut()[idx] =
            PremultipliedColorU8::from_rgba(fg_r, fg_g, fg_b, 255).unwrap();
        return;
    }
    // 预乘源色
    let src_r = (fg_r as u32 * a as u32) / 255;
    let src_g = (fg_g as u32 * a as u32) / 255;
    let src_b = (fg_b as u32 * a as u32) / 255;
    let inv_a = 255 - a as u32;
    // SourceOver：out = src + dst*(1-a)（预乘域）
    let out_r = src_r + dst.red() as u32 * inv_a / 255;
    let out_g = src_g + dst.green() as u32 * inv_a / 255;
    let out_b = src_b + dst.blue() as u32 * inv_a / 255;
    let out_a = a as u32 + dst.alpha() as u32 * inv_a / 255;
    pix.pixels_mut()[idx] = PremultipliedColorU8::from_rgba(
        out_r.min(255) as u8,
        out_g.min(255) as u8,
        out_b.min(255) as u8,
        out_a.min(255) as u8,
    )
    .unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(col: usize, text: &str, fg: &str, bg: &str, bold: bool, reverse: bool) -> CellTuple {
        (
            col,
            text.to_string(),
            fg.to_string(),
            bg.to_string(),
            bold,
            false,
            false,
            reverse,
            false,
            text.chars().count(),
        )
    }

    #[test]
    fn test_render_png_basic() {
        let lines = vec![vec![cell(0, "hi", "green", "default", false, false)]];
        let png = render_image_bytes(&lines, 4, 1, 1.0, "png");
        assert!(png.len() > 100, "PNG should be non-trivial, got {} bytes", png.len());
        // PNG header magic
        assert_eq!(&png[..4], [0x89, b'P', b'N', b'G']);
    }

    #[test]
    fn test_render_png_reverse() {
        // reverse 应交换 fg/bg（验证不 panic 且有输出）
        let lines = vec![vec![cell(0, "A", "white", "red", false, true)]];
        let png = render_image_bytes(&lines, 4, 1, 1.0, "png");
        assert_eq!(&png[..4], [0x89, b'P', b'N', b'G']);
    }

    #[test]
    fn test_render_png_scale() {
        let lines = vec![vec![cell(0, "X", "white", "default", false, false)]];
        let png = render_image_bytes(&lines, 4, 1, 2.0, "png");
        assert_eq!(&png[..4], [0x89, b'P', b'N', b'G']);
        // 2x scale 时图像尺寸加倍
        let png1 = render_image_bytes(&lines, 4, 1, 1.0, "png");
        assert!(png.len() > png1.len(), "2x PNG should be larger");
    }

    #[test]
    fn test_render_jpg_bmp() {
        // jpg/bmp 编码应分别产生对应格式头
        let lines = vec![vec![cell(0, "X", "white", "default", false, false)]];
        let jpg = render_image_bytes(&lines, 4, 1, 1.0, "jpg");
        assert_eq!(&jpg[..2], [0xFF, 0xD8], "JPEG SOI magic");
        let bmp = render_image_bytes(&lines, 4, 1, 1.0, "bmp");
        assert_eq!(&bmp[..2], b"BM", "BMP magic");
    }
}