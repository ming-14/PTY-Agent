"""SVG 渲染 — 零依赖矢量输出

同色连续字符合并为一条 <text> 元素以减小体积；可选 scour 压缩。
字体依赖查看器，CJK 宽度按 2 格处理。
"""

import logging
import re
from xml.sax.saxutils import escape as xml_escape

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS
from .common import _char_width, _resolve_color, _expand_lines

_logger = logging.getLogger("pty-client")


def _flush_svg_run(parts, run_chars, run_x, y, cell_h, run_fg, run_bold):
    """将当前文本 run 追加为一条 <text> 元素到 parts

    提取为模块级函数，避免在循环内重复定义闭包（消除 B023 延迟绑定警告与重定义开销）。
    """
    if not run_chars:
        return
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
    cell_w = 8
    cell_h = 16
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
        run_bold = False

        col = 0
        while col < cols and col < len(line):
            cell = line[col]
            d = cell.get("d", " ")
            fg_color = _resolve_color(cell.get("f", "default"), is_fg=True) or "#e5e5e5"
            bold = cell.get("bo", False)
            cw = _char_width(d) if d != " " else 1

            if fg_color == run_fg and bold == run_bold and run_chars:
                run_chars.append(d)
            else:
                _flush_svg_run(parts, run_chars, run_x, y, cell_h, run_fg, run_bold)
                run_x = x
                run_chars = [d]
                run_fg = fg_color
                run_bold = bold

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

        _flush_svg_run(parts, run_chars, run_x, y, cell_h, run_fg, run_bold)

    parts.append("</svg>")
    return "\n".join(parts)


def _compress_svg(svg: str, level: int) -> str:
    svg = re.sub(r"<text[^>]*>\s*</text>", "", svg, flags=re.DOTALL)
    if level <= 0:
        return svg
    try:
        from scour import scour
    except ImportError:
        _logger.warning("scour 未安装，SVG 压缩降级为 level 0。安装: pip install scour")
        return svg
    if level == 1:
        options = {
            "strip_xml_prolog": False,
            "remove_descriptive_elements": False,
            "enable_comment_stripping": False,
            "shorten_ids": False,
            "create_groups": False,
            "digits": 5,
            "c_digits": 5,
            "newlines": True,
            "indent_type": "space",
            "enable_viewboxing": False,
            "renderer_workaround": True,
            "strip_xml_space_attribute": False,
        }
    else:
        options = {
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
            "strip_xml_space_attribute": False,
        }
    return scour.scourString(svg, options=options)
