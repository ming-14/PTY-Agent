"""SVG 渲染 — 零依赖矢量输出

同色连续字符合并为一条 <text> 元素以减小体积；可选 scour 压缩。
字体依赖查看器，CJK 宽度按 2 格处理。
背景色按 run 输出 <rect>（无各单元格背景时省略）。
"""

import re
import types
from xml.sax.saxutils import escape as xml_escape

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS
from .common import CELL_H, CELL_W, _char_width, _resolve_color, _expand_lines
from ...logging import get_logger

_logger = get_logger("pty-client")

# 空 <text> 元素正则（模块级编译；level 0 = 仅移除空标签）
_EMPTY_TEXT_RE = re.compile(r"<text[^>]*>\s*</text>", flags=re.DOTALL)

# scour 压缩选项（按等级固定，模块级常量避免每次调用重建）
# level 1 轻度：紧凑输出但保留全部信息（描述元素/注释/id/groups）；
# level 2 深度：额外剥离描述、缩短 id、合并分组、降低坐标精度。
# 两者均不追加 XML prolog（输入本身无 prolog，追加只会膨胀体积）
_SCOUR_OPTIONS_LEVEL1 = {
    "strip_xml_prolog": True,
    "remove_descriptive_elements": False,
    "enable_comment_stripping": False,
    "shorten_ids": False,
    "create_groups": False,
    "digits": 5,
    "c_digits": 5,
    "newlines": False,
    "indent_type": "none",
    "enable_viewboxing": False,
    "renderer_workaround": True,
    "strip_xml_space_attribute": False,
}
_SCOUR_OPTIONS_LEVEL2 = {
    "strip_xml_prolog": True,
    "remove_descriptive_elements": True,
    "enable_comment_stripping": True,
    "shorten_ids": True,
    "create_groups": True,
    "digits": 3,
    "c_digits": 3,
    "newlines": False,
    "indent_type": "none",
    "enable_viewboxing": False,
    "renderer_workaround": True,
    "strip_xml_space_attribute": True,
}


def _flush_svg_run(parts, run_chars, run_x, run_w, y, cell_h, run_fg, run_bg, run_bold):
    """将当前文本 run 追加为 <rect>（背景）+ <text>（前景）到 parts

    提取为模块级函数，避免在循环内重复定义闭包（消除 B023 延迟绑定警告与重定义开销）。
    """
    if not run_chars:
        return
    if run_bg:
        parts.append(
            f'<rect x="{run_x}" y="{y * cell_h}" width="{run_w}" height="{cell_h}" fill="{run_bg}"/>'
        )
    text = xml_escape("".join(run_chars))
    attrs = f'x="{run_x}" y="{y * cell_h}"'
    if run_fg:
        attrs += f' fill="{run_fg}"'
    if run_bold:
        attrs += ' font-weight="bold"'
    parts.append(f"<text {attrs}>{text}</text>")


def render_svg_string(buf: dict) -> str:
    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = _expand_lines(buf)
    cell_w = CELL_W
    cell_h = CELL_H
    w = cols * cell_w
    h = rows * cell_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        '<rect width="100%" height="100%" fill="#0c0c0c"/>',
        f'<style>text{{font-family:Consolas,"Microsoft YaHei",monospace;font-size:{cell_h - 2}px;'
        f"dominant-baseline:text-before-edge;white-space:pre}}</style>",
    ]

    for y, line in enumerate(lines):
        if y >= rows:
            break
        x = 0
        run_x = 0
        run_chars = []
        run_fg = None
        run_bg = None
        run_bold = False

        col = 0
        while col < cols and col < len(line):
            cell = line[col]
            d = cell.get("d", " ")
            fg_color = _resolve_color(cell.get("f", "default"), is_fg=True) or "#e5e5e5"
            bg_color = _resolve_color(cell.get("b", "default"), is_fg=False)
            bold = cell.get("bo", False)
            cw = _char_width(d) if d != " " else 1

            if (
                fg_color == run_fg
                and bg_color == run_bg
                and bold == run_bold
                and run_chars
            ):
                run_chars.append(d)
            else:
                _flush_svg_run(
                    parts, run_chars, run_x, x - run_x, y, cell_h, run_fg, run_bg, run_bold
                )
                run_x = x
                run_chars = [d]
                run_fg = fg_color
                run_bg = bg_color
                run_bold = bold

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

        _flush_svg_run(
            parts, run_chars, run_x, x - run_x, y, cell_h, run_fg, run_bg, run_bold
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _compress_svg(svg: str, level: int) -> str:
    svg = _EMPTY_TEXT_RE.sub("", svg)
    if level <= 0:
        return svg
    try:
        from scour import scour
    except ImportError:
        _logger.warning("scour 未安装，SVG 压缩降级为 level 0。安装: pip install scour")
        return svg
    options = _SCOUR_OPTIONS_LEVEL1 if level == 1 else _SCOUR_OPTIONS_LEVEL2
    # scour 的 sanitizeOptions 用 dir() 取属性：传 dict 会把键当属性名读取而全部丢失，
    # 必须以属性对象（SimpleNamespace）传递选项
    return scour.scourString(svg, options=types.SimpleNamespace(**options))
