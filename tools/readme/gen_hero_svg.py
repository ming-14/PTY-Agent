"""生成 README 头图 SVG（深色终端卡片）。

画框文本改 ART 后跑一次即可重生成：

    python tools/readme/gen_hero_svg.py

产物：docs/assets/hero-terminal.svg

网格度量与 pywezterm 的 SVG 导出器（src/render/svg.rs）保持一致，
使 README 截图观感与 `-o xxx.svg` 的真实导出结果同款：
CELL_W=8 / CELL_H=17 / font-size=CELL_H-2 / Consolas-等宽栈 / 底色 #0c0c0c。
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

# 项目根目录：本脚本位于 <root>/tools/readme/，向上两级
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets" / "hero-terminal.svg"

CELL_W = 8
CELL_H = 17
FONT_SIZE = CELL_H - 2
FONT_FAMILY = 'Consolas,"Microsoft YaHei",monospace'
BG = "#0c0c0c"
FG = "#e5e5e5"
# 画框四周留白，对齐 GitHub 代码块的 16px padding
PAD = 16

ART = (
    '┌─ PTY-Agent ─────────────────────────────────────────────────────────────────┐',
    '│ $ app.py exec dbg -c "cdb.exe myapp.exe" -t "0:000" --timeout 5             │',
    '│                                                                             │',
    '│ ─────────────────────────────── matched ───────────────────────────────     │',
    '│ Microsoft (R) Windows Debugger Version 10.0.11451.4                         │',
    '│ 0:000>                                                                      │',
    '│ ───────────────────────────────────────────────────────────────────────     │',
    '│ [exec · matched · 0.42s]  dbg  running  pty                                 │',
    '└─────────────────────────────────────────────────────────────────────────────┘',
)


def build_svg() -> str:
    lines = [ln.rstrip("\n") for ln in ART]
    cols = max(len(ln) for ln in lines)
    rows = len(lines)
    text_w = cols * CELL_W
    w = text_w + PAD * 2
    h = rows * CELL_H + PAD * 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'<rect width="100%" height="100%" fill="{BG}"/>',
        f"<style>text{{font-family:{FONT_FAMILY};font-size:{FONT_SIZE}px;"
        f"dominant-baseline:text-before-edge;white-space:pre}}</style>",
    ]
    for y, line in enumerate(lines):
        if not line.strip():
            continue
        # textLength + spacingAndGlyphs：把每行锁到网格宽度，避免宿主字体字宽
        # 与 CELL_W 不一致时右边界被画布裁掉，同时保证各行左右边框严格对齐。
        parts.append(
            f'<text x="{PAD}" y="{PAD + y * CELL_H}" fill="{FG}" '
            f'textLength="{text_w}" lengthAdjust="spacingAndGlyphs">'
            f"{escape(line)}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def main() -> None:
    svg = build_svg()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    widths = {len(ln) for ln in ART}
    print(f"wrote {OUT.relative_to(ROOT)} ({len(svg)} bytes)")
    print(f"art lines: {len(ART)}, columns: {sorted(widths)}")


if __name__ == "__main__":
    main()
