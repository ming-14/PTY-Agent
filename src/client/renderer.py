"""终端屏幕快照渲染器 — 将 screenBuffer 渲染为图片或写入文本文件

双轨策略:
- SVG: 零依赖，矢量无损，字体依赖查看器
- Pillow: 可选依赖 (pip install pillow)，像素精确，支持 PNG/JPG/BMP
"""

import os
import unicodedata
import logging
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS

_logger = logging.getLogger("pty-client")

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".svg"}

_ANSI_COLOR_MAP = {
    "black": "#000000",
    "red": "#cd0000",
    "green": "#00cd00",
    "brown": "#cdcd00",
    "blue": "#0000ee",
    "magenta": "#cd00cd",
    "cyan": "#00cdcd",
    "white": "#e5e5e5",
    "brightblack": "#7f7f7f",
    "brightred": "#ff0000",
    "brightgreen": "#00ff00",
    "brightbrown": "#ffff00",
    "brightblue": "#5c5cff",
    "brightmagenta": "#ff00ff",
    "brightcyan": "#00ffff",
    "brightwhite": "#ffffff",
    "default": None,
}


def _resolve_color(color_str: str, is_fg: bool = True) -> Optional[str]:
    if not color_str or color_str == "default":
        return None
    if color_str in _ANSI_COLOR_MAP:
        return _ANSI_COLOR_MAP[color_str]
    if isinstance(color_str, str) and len(color_str) == 6:
        try:
            int(color_str, 16)
            return f"#{color_str}"
        except ValueError:
            pass
    if color_str.startswith("rgb:"):
        parts = color_str[4:].split("/")
        if len(parts) == 3:
            try:
                r, g, b = (int(p, 16) // 256 for p in parts)
                return f"#{r:02x}{g:02x}{b:02x}"
            except (ValueError, ZeroDivisionError):
                pass
    return None


def _char_width(c: str) -> int:
    try:
        from wcwidth import wcwidth
        w = wcwidth(c)
        return w if w > 0 else 1
    except ImportError:
        eaw = unicodedata.east_asian_width(c)
        return 2 if eaw in ("W", "F") else 1


def _is_cjk_char(c: str) -> bool:
    if len(c) != 1:
        return False
    cp = ord(c)
    if 0x4E00 <= cp <= 0x9FFF:
        return True
    if 0x3400 <= cp <= 0x4DBF:
        return True
    if 0x20000 <= cp <= 0x2A6DF:
        return True
    if 0x2A700 <= cp <= 0x2B73F:
        return True
    if 0xF900 <= cp <= 0xFAFF:
        return True
    if 0x2F800 <= cp <= 0x2FA1F:
        return True
    if 0x3000 <= cp <= 0x303F:
        return True
    if 0x3040 <= cp <= 0x309F:
        return True
    if 0x30A0 <= cp <= 0x30FF:
        return True
    if 0xAC00 <= cp <= 0xD7AF:
        return True
    if 0xFF01 <= cp <= 0xFF60:
        return True
    eaw = unicodedata.east_asian_width(c)
    return eaw in ("W", "F")


def is_image_ext(path: str) -> bool:
    _, ext = os.path.splitext(path.lower())
    return ext in _IMAGE_EXTS


def _expand_lines(buf: dict) -> list:
    """将稀疏/全量 lines 统一展开为全量二维数组 [[cell_dict, ...], ...]

    稀疏格式: lines[row] = [{"c":col, "d":..., "f":..., "b":..., "bo":...}, ...]
    全量格式: lines[row] = [{"d":..., "f":..., "b":..., "bo":...}, ...]
    """
    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = buf.get("lines", [])
    default_cell = {"d": " ", "f": "default", "b": "default", "bo": False}
    expanded = []
    for row_idx in range(min(rows, len(lines))):
        raw_line = lines[row_idx]
        if not raw_line:
            expanded.append([dict(default_cell) for _ in range(cols)])
            continue
        first = raw_line[0] if raw_line else None
        if first and "c" in first:
            full = [dict(default_cell) for _ in range(cols)]
            for cell in raw_line:
                c = cell.get("c", 0)
                if 0 <= c < cols:
                    full[c] = {"d": cell.get("d", " "), "f": cell.get("f", "default"),
                               "b": cell.get("b", "default"), "bo": cell.get("bo", False)}
            expanded.append(full)
        else:
            expanded.append(raw_line)
    while len(expanded) < rows:
        expanded.append([dict(default_cell) for _ in range(cols)])
    return expanded


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
        f'<rect width="100%" height="100%" fill="#0c0c0c"/>',
        f'<style>text{{font-family:Consolas,Monaco,"Courier New","Microsoft YaHei","SimHei","Noto Sans CJK SC",monospace;font-size:{cell_h - 2}px;'
        f'dominant-baseline:text-before-edge;white-space:pre}}</style>',
    ]

    for y, line in enumerate(lines):
        if y >= rows:
            break
        x = 0
        run_chars = []
        run_fg = None
        run_bold = False

        def flush_run():
            if not run_chars:
                return
            text = xml_escape("".join(run_chars))
            attrs = f'x="{run_x}" y="{y * cell_h}"'
            if run_fg:
                attrs += f' fill="{run_fg}"'
            if run_bold:
                attrs += ' font-weight="bold"'
            parts.append(f'<text {attrs}>{text}</text>')

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
                flush_run()
                run_x = x
                run_chars = [d]
                run_fg = fg_color
                run_bold = bold

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

        flush_run()

    parts.append("</svg>")
    return "\n".join(parts)


def _compress_svg(svg: str, level: int) -> str:
    import re
    svg = re.sub(r'<text[^>]*>\s*</text>', '', svg, flags=re.DOTALL)
    if level <= 0:
        return svg
    try:
        from scour import scour
    except ImportError:
        _logger.warning("scour 未安装，SVG 压缩降级为 level 0。安装: pip install scour")
        return svg
    if level == 1:
        options = {
            'strip_xml_prolog': False,
            'remove_descriptive_elements': False,
            'enable_comment_stripping': False,
            'shorten_ids': False,
            'create_groups': False,
            'digits': 5,
            'c_digits': 5,
            'newlines': True,
            'indent_type': 'space',
            'enable_viewboxing': False,
            'renderer_workaround': True,
            'strip_xml_space_attribute': False,
        }
    else:
        options = {
            'strip_xml_prolog': True,
            'remove_descriptive_elements': True,
            'enable_comment_stripping': True,
            'shorten_ids': True,
            'create_groups': True,
            'digits': 3,
            'c_digits': 3,
            'newlines': False,
            'indent_type': 'none',
            'enable_viewboxing': False,
            'renderer_workaround': True,
            'strip_xml_space_attribute': False,
        }
    return scour.scourString(svg, options=options)


def render_to_file(path: str, response: dict, svg_compression_level: int = 1) -> Optional[str]:
    _, ext = os.path.splitext(path.lower())
    screen_buffer = response.get("screenBuffer")
    is_img = ext in _IMAGE_EXTS

    if is_img and ext == ".svg":
        if not screen_buffer:
            return "SVG output requires screen buffer (use --snapshot or --snapshot-mode)"
        svg = render_svg_string(screen_buffer)
        svg = _compress_svg(svg, svg_compression_level)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(svg)
            _logger.info("SVG written to %s (%d bytes)", path, len(svg))
        except OSError as e:
            return f"Failed to write {path}: {e}"
        return None

    if is_img and ext in (".png", ".jpg", ".jpeg", ".bmp"):
        if not screen_buffer:
            return "Image output requires screen buffer (use --snapshot or --snapshot-mode)"
        return _render_pillow(path, screen_buffer, ext)

    text = response.get("outputStream") or response.get("stdout") or ""
    if not text and response.get("type") != "error":
        _logger.debug("render_to_file: no text output to write")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        _logger.info("Output written to %s", path)
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None


def _render_svg(path: str, buf: dict) -> Optional[str]:
    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = _expand_lines(buf)
    cell_w = 8
    cell_h = 16
    w = cols * cell_w
    h = rows * cell_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'<rect width="100%" height="100%" fill="#0c0c0c"/>',
        f'<style>text{{font-family:Consolas,Monaco,"Courier New","Microsoft YaHei","SimHei","Noto Sans CJK SC",monospace;font-size:{cell_h - 2}px;'
        f'dominant-baseline:text-before-edge;white-space:pre}}</style>',
    ]

    for y, line in enumerate(lines):
        if y >= rows:
            break
        x = 0
        run_chars = []
        run_fg = None
        run_bold = False

        def flush_run():
            if not run_chars:
                return
            text = xml_escape("".join(run_chars))
            attrs = f'x="{run_x}" y="{y * cell_h}"'
            if run_fg:
                attrs += f' fill="{run_fg}"'
            if run_bold:
                attrs += ' font-weight="bold"'
            parts.append(f'<text {attrs}>{text}</text>')

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
                flush_run()
                run_x = x
                run_chars = [d]
                run_fg = fg_color
                run_bold = bold

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

        flush_run()

    parts.append("</svg>")
    svg_content = "\n".join(parts)

    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        _logger.info("SVG written to %s (%d bytes)", path, len(svg_content))
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None


def _is_block_element(c: str) -> bool:
    if len(c) != 1:
        return False
    cp = ord(c)
    return 0x2500 <= cp < 0x25A0


_SHAPE_LIGHT = 0
_SHAPE_HEAVY = 1
_SHAPE_FILL = 2
_SHAPE_EMPTY_RECT = 3
_SHAPE_ROUND_RECT = 4
_SHAPE_SHADE = 5

_BOX_DRAWING_TABLE = None


def _get_box_drawing_table():
    global _BOX_DRAWING_TABLE
    if _BOX_DRAWING_TABLE is not None:
        return _BOX_DRAWING_TABLE

    L = _SHAPE_LIGHT
    H = _SHAPE_HEAVY
    F = _SHAPE_FILL
    E = _SHAPE_EMPTY_RECT
    R = _SHAPE_ROUND_RECT
    S = _SHAPE_SHADE

    _BOX_DRAWING_TABLE = {
        0x2500: [(L, 0,0, 0.5,0, 1,0, 0.5,0)],
        0x2501: [(H, 0,0, 0.5,0, 1,0, 0.5,0)],
        0x2502: [(L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2503: [(H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2504: [(L, 0,0, 0.5,0, 2/9,0, 0.5,0), (L, 3/9,0, 0.5,0, 5/9,0, 0.5,0), (L, 6/9,0, 0.5,0, 8/9,0, 0.5,0)],
        0x2505: [(H, 0,0, 0.5,0, 2/9,0, 0.5,0), (H, 3/9,0, 0.5,0, 5/9,0, 0.5,0), (H, 6/9,0, 0.5,0, 8/9,0, 0.5,0)],
        0x2506: [(L, 0.5,0, 0,0, 0.5,0, 2/9,0), (L, 0.5,0, 3/9,0, 0.5,0, 5/9,0), (L, 0.5,0, 6/9,0, 0.5,0, 8/9,0)],
        0x2507: [(H, 0.5,0, 0,0, 0.5,0, 2/9,0), (H, 0.5,0, 3/9,0, 0.5,0, 5/9,0), (H, 0.5,0, 6/9,0, 0.5,0, 8/9,0)],
        0x2508: [(L, 0,0, 0.5,0, 2/12,0, 0.5,0), (L, 3/12,0, 0.5,0, 5/12,0, 0.5,0), (L, 6/12,0, 0.5,0, 8/12,0, 0.5,0), (L, 9/12,0, 0.5,0, 11/12,0, 0.5,0)],
        0x2509: [(H, 0,0, 0.5,0, 2/12,0, 0.5,0), (H, 3/12,0, 0.5,0, 5/12,0, 0.5,0), (H, 6/12,0, 0.5,0, 8/12,0, 0.5,0), (H, 9/12,0, 0.5,0, 11/12,0, 0.5,0)],
        0x250A: [(L, 0.5,0, 0,0, 0.5,0, 2/12,0), (L, 0.5,0, 3/12,0, 0.5,0, 5/12,0), (L, 0.5,0, 6/12,0, 0.5,0, 8/12,0), (L, 0.5,0, 9/12,0, 0.5,0, 11/12,0)],
        0x250B: [(H, 0.5,0, 0,0, 0.5,0, 2/12,0), (H, 0.5,0, 3/12,0, 0.5,0, 5/12,0), (H, 0.5,0, 6/12,0, 0.5,0, 8/12,0), (H, 0.5,0, 9/12,0, 0.5,0, 11/12,0)],
        0x250C: [(L, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x250D: [(H, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x250E: [(L, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x250F: [(H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2510: [(L, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2511: [(H, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2512: [(L, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2513: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2514: [(L, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2515: [(H, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2516: [(L, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2517: [(H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2518: [(L, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2519: [(H, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x251A: [(L, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x251B: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x251C: [(L, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x251D: [(H, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x251E: [(L, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x251F: [(L, 0.5,-1, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2520: [(L, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2521: [(H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2522: [(H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2523: [(H, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2524: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2525: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2526: [(L, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2527: [(L, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2528: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2529: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x252A: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x252B: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x252C: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x252D: [(H, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x252E: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x252F: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2530: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2531: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2532: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2533: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2534: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2535: [(H, 0,0, 0.5,0, 0.5,0.5, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2536: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-0.5, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2537: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2538: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2539: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x253A: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x253B: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x253C: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x253D: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x253E: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x253F: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2540: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2541: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2542: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2543: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2544: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2545: [(H, 0,0, 0.5,0, 0.5,1, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2546: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,-1, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2547: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2548: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2549: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x254A: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x254B: [(H, 0,0, 0.5,0, 1,0, 0.5,0), (H, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x254C: [(L, 0,0, 0.5,0, 2/6,0, 0.5,0), (L, 3/6,0, 0.5,0, 5/6,0, 0.5,0)],
        0x254D: [(H, 0,0, 0.5,0, 2/6,0, 0.5,0), (H, 3/6,0, 0.5,0, 5/6,0, 0.5,0)],
        0x254E: [(L, 0.5,0, 0,0, 0.5,0, 2/6,0), (L, 0.5,0, 3/6,0, 0.5,0, 5/6,0)],
        0x254F: [(H, 0.5,0, 0,0, 0.5,0, 2/6,0), (H, 0.5,0, 3/6,0, 0.5,0, 5/6,0)],
        0x2550: [(L, 0,0, 0.5,-1, 1,0, 0.5,-1), (L, 0,0, 0.5,1, 1,0, 0.5,1)],
        0x2551: [(L, 0.5,-1, 0,0, 0.5,-1, 1,0), (L, 0.5,1, 0,0, 0.5,1, 1,0)],
        0x2552: [(L, 0.5,-0.5, 0.5,-1, 1,0, 0.5,-1), (L, 0.5,-0.5, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0.5,-1, 0.5,0, 1,0)],
        0x2553: [(L, 0.5,-1, 0.5,-0.5, 0.5,-1, 1,0), (L, 0.5,1, 0.5,-0.5, 0.5,1, 1,0), (L, 0.5,-1, 0.5,0, 1,0, 0.5,0)],
        0x2554: [(E, 0.5,-1, 0.5,-1, 1.5,0, 1.5,0)],
        0x2555: [(L, 0,0, 0.5,-1, 0.5,0.5, 0.5,-1), (L, 0,0, 0.5,1, 0.5,0.5, 0.5,1), (L, 0.5,0, 0.5,-1, 0.5,0, 1,0)],
        0x2556: [(L, 0.5,-1, 0.5,-0.5, 0.5,-1, 1,0), (L, 0.5,1, 0.5,-0.5, 0.5,1, 1,0), (L, 0,0, 0.5,0, 0.5,1, 0.5,0)],
        0x2557: [(E, -0.5,0, 0.5,-1, 0.5,1, 1.5,0)],
        0x2558: [(L, 0.5,-0.5, 0.5,-1, 1,0, 0.5,-1), (L, 0.5,-0.5, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 0.5,1)],
        0x2559: [(L, 0.5,-1, 0,0, 0.5,-1, 0.5,0.5), (L, 0.5,1, 0,0, 0.5,1, 0.5,0.5), (L, 0.5,-1, 0.5,0, 1,0, 0.5,0)],
        0x255A: [(E, 0.5,-1, -0.5,0, 1.5,0, 0.5,1)],
        0x255B: [(L, 0,0, 0.5,-1, 0.5,0.5, 0.5,-1), (L, 0,0, 0.5,1, 0.5,0.5, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 0.5,1)],
        0x255C: [(L, 0.5,-1, 0,0, 0.5,-1, 0.5,0.5), (L, 0.5,1, 0,0, 0.5,1, 0.5,0.5), (L, 0,0, 0.5,0, 0.5,1, 0.5,0)],
        0x255D: [(E, -0.5,0, -0.5,0, 0.5,1, 0.5,1)],
        0x255E: [(L, 0.5,0, 0.5,-1, 1,0, 0.5,-1), (L, 0.5,0, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x255F: [(L, 0.5,-1, 0,0, 0.5,-1, 1,0), (L, 0.5,1, 0,0, 0.5,1, 1,0), (L, 0.5,1, 0.5,0, 1,0, 0.5,0)],
        0x2560: [(L, 0.5,-1, 0,0, 0.5,-1, 1,0), (E, 0.5,1, -0.5,0, 1.5,0, 0.5,-1)],
        0x2561: [(L, 0,0, 0.5,-1, 0.5,0, 0.5,-1), (L, 0,0, 0.5,1, 0.5,0, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x2562: [(L, 0.5,-1, 0,0, 0.5,-1, 1,0), (L, 0.5,1, 0,0, 0.5,1, 1,0), (L, 0,0, 0.5,0, 0.5,-1, 0.5,0)],
        0x2563: [(L, 0.5,1, 0,0, 0.5,1, 1,0), (E, -0.5,0, -0.5,0, 0.5,-1, 0.5,-1)],
        0x2564: [(L, 0,0, 0.5,-1, 1,0, 0.5,-1), (L, 0,0, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0.5,1, 0.5,0, 1,0)],
        0x2565: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,-1, 0.5,0, 0.5,-1, 1,0), (L, 0.5,1, 0.5,0, 0.5,1, 1,0)],
        0x2566: [(L, 0,0, 0.5,-1, 1,0, 0.5,-1), (E, -0.5,0, 0.5,1, 0.5,-1, 1.5,0)],
        0x2567: [(L, 0,0, 0.5,-1, 1,0, 0.5,-1), (L, 0,0, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 0.5,-1)],
        0x2568: [(L, 0,0, 0.5,0, 1,0, 0.5,0), (L, 0.5,-1, 0,0, 0.5,-1, 0.5,0), (L, 0.5,1, 0,0, 0.5,1, 0.5,0)],
        0x2569: [(L, 0,0, 0.5,1, 1,0, 0.5,1), (E, -0.5,0, -0.5,0, 0.5,-1, 0.5,-1)],
        0x256A: [(L, 0,0, 0.5,-1, 1,0, 0.5,-1), (L, 0,0, 0.5,1, 1,0, 0.5,1), (L, 0.5,0, 0,0, 0.5,0, 1,0)],
        0x256B: [(L, 0.5,-1, 0,0, 0.5,-1, 1,0), (L, 0.5,1, 0,0, 0.5,1, 1,0), (L, 0,0, 0.5,0, 1,0, 0.5,0)],
        0x256C: [(E, -0.5,0, -0.5,0, 0.5,-1, 0.5,-1)],
        0x256D: [(R, 0.5,0, 0.5,0, 1,0, 1,0)],
        0x256E: [(R, 0,0, 0.5,0, 0.5,0, 1,0)],
        0x256F: [(R, 0,0, 0,0, 0.5,0, 0.5,0)],
        0x2570: [(R, 0.5,0, 0,0, 1,0, 0.5,0)],
        0x2571: [(L, 0,0, 1,0, 1,0, 0,0)],
        0x2572: [(L, 0,0, 0,0, 1,0, 1,0)],
        0x2573: [(L, 0,0, 1,0, 1,0, 0,0), (L, 0,0, 0,0, 1,0, 1,0)],
        0x2574: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0)],
        0x2575: [(L, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x2576: [(L, 0.5,0, 0.5,0, 1,0, 0.5,0)],
        0x2577: [(L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2578: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0)],
        0x2579: [(H, 0.5,0, 0,0, 0.5,0, 0.5,0)],
        0x257A: [(H, 0.5,0, 0.5,0, 1,0, 0.5,0)],
        0x257B: [(H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x257C: [(L, 0,0, 0.5,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 1,0, 0.5,0)],
        0x257D: [(L, 0.5,0, 0,0, 0.5,0, 0.5,0), (H, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x257E: [(H, 0,0, 0.5,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 1,0, 0.5,0)],
        0x257F: [(H, 0.5,0, 0,0, 0.5,0, 0.5,0), (L, 0.5,0, 0.5,0, 0.5,0, 1,0)],
        0x2580: [(F, 0,0, 0,0, 1,0, 0.5,0)],
        0x2581: [(F, 0,0, 7/8,0, 1,0, 1,0)],
        0x2582: [(F, 0,0, 3/4,0, 1,0, 1,0)],
        0x2583: [(F, 0,0, 5/8,0, 1,0, 1,0)],
        0x2584: [(F, 0,0, 0.5,0, 1,0, 1,0)],
        0x2585: [(F, 0,0, 3/8,0, 1,0, 1,0)],
        0x2586: [(F, 0,0, 1/4,0, 1,0, 1,0)],
        0x2587: [(F, 0,0, 1/8,0, 1,0, 1,0)],
        0x2588: [(F, 0,0, 0,0, 1,0, 1,0)],
        0x2589: [(F, 0,0, 0,0, 7/8,0, 1,0)],
        0x258A: [(F, 0,0, 0,0, 3/4,0, 1,0)],
        0x258B: [(F, 0,0, 0,0, 5/8,0, 1,0)],
        0x258C: [(F, 0,0, 0,0, 0.5,0, 1,0)],
        0x258D: [(F, 0,0, 0,0, 3/8,0, 1,0)],
        0x258E: [(F, 0,0, 0,0, 1/4,0, 1,0)],
        0x258F: [(F, 0,0, 0,0, 1/8,0, 1,0)],
        0x2590: [(F, 0.5,0, 0,0, 1,0, 1,0)],
        0x2591: [(S, 0,0, 0,0, 1,0, 1,0)],
        0x2592: [(S, 0,0, 0,0, 1,0, 1,0)],
        0x2593: [(S, 0,0, 0,0, 1,0, 1,0)],
        0x2594: [(F, 0,0, 0,0, 1,0, 1/8,0)],
        0x2595: [(F, 7/8,0, 0,0, 1,0, 1,0)],
        0x2596: [(F, 0,0, 0.5,0, 0.5,0, 1,0)],
        0x2597: [(F, 0.5,0, 0.5,0, 1,0, 1,0)],
        0x2598: [(F, 0,0, 0,0, 0.5,0, 0.5,0)],
        0x2599: [(F, 0,0, 0,0, 0.5,0, 1,0), (F, 0.5,0, 0.5,0, 1,0, 1,0)],
        0x259A: [(F, 0,0, 0,0, 0.5,0, 0.5,0), (F, 0.5,0, 0.5,0, 1,0, 1,0)],
        0x259B: [(F, 0,0, 0,0, 0.5,0, 1,0), (F, 0.5,0, 0,0, 1,0, 0.5,0)],
        0x259C: [(F, 0,0, 0,0, 0.5,0, 0.5,0), (F, 0.5,0, 0,0, 1,0, 1,0)],
        0x259D: [(F, 0.5,0, 0,0, 1,0, 0.5,0)],
        0x259E: [(F, 0,0, 0.5,0, 0.5,0, 1,0), (F, 0.5,0, 0,0, 1,0, 0.5,0)],
        0x259F: [(F, 0,0, 0.5,0, 0.5,0, 1,0), (F, 0.5,0, 0,0, 1,0, 1,0)],
    }
    return _BOX_DRAWING_TABLE


def _draw_block_element(gdi32, user32, hdc, x: int, y: int, w: int, h: int, cp: int, fg: int, bg: int):
    import ctypes
    import ctypes.wintypes as W
    import struct

    LIGHT = 0
    HEAVY = 1
    FILL = 2
    EMPTY_RECT = 3
    ROUND_RECT = 4
    SHADE = 5

    FillRect = user32.FillRect
    FillRect.restype = ctypes.c_int
    FillRect.argtypes = [W.HDC, ctypes.c_void_p, W.HBRUSH]

    CreateSolidBrush = gdi32.CreateSolidBrush
    CreateSolidBrush.restype = W.HBRUSH
    CreateSolidBrush.argtypes = [W.COLORREF]

    DeleteObject = gdi32.DeleteObject
    DeleteObject.restype = W.BOOL
    DeleteObject.argtypes = [W.HGDIOBJ]

    CreatePen = gdi32.CreatePen
    CreatePen.restype = W.HPEN
    CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, W.COLORREF]

    SelectObject_fn = gdi32.SelectObject
    SelectObject_fn.restype = W.HGDIOBJ
    SelectObject_fn.argtypes = [W.HDC, W.HGDIOBJ]

    MoveToEx = gdi32.MoveToEx
    MoveToEx.restype = W.BOOL
    MoveToEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]

    LineTo = gdi32.LineTo
    LineTo.restype = W.BOOL
    LineTo.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int]

    Arc = gdi32.Arc
    Arc.restype = W.BOOL
    Arc.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]

    PS_SOLID = 0

    table = _get_box_drawing_table()
    instructions = table.get(cp)
    if instructions is None:
        _ext_text_out_fallback(gdi32, hdc, x, y, w, h, cp, fg, bg)
        return

    if bg != 0x0C0C0C:
        hbr_bg = CreateSolidBrush(bg)
        rect_bg = (ctypes.c_long * 4)(x, y, x + w, y + h)
        FillRect(hdc, rect_bg, hbr_bg)
        DeleteObject(hbr_bg)

    light_lw = max(1, round(w / 6))
    heavy_lw = max(1, round(w / 4))
    corner_radius = min(light_lw * 5, min(w, h) // 2)

    for instr in instructions:
        shape = instr[0]
        bx_frac, bx_off, by_frac, by_off = instr[1], instr[2], instr[3], instr[4]
        ex_frac, ex_off, ey_frac, ey_off = instr[5], instr[6], instr[7], instr[8]

        lw = light_lw if shape == _SHAPE_LIGHT else heavy_lw if shape == _SHAPE_HEAVY else light_lw

        px1 = x + int(bx_frac * w + bx_off * lw)
        py1 = y + int(by_frac * h + by_off * lw)
        px2 = x + int(ex_frac * w + ex_off * lw)
        py2 = y + int(ey_frac * h + ey_off * lw)

        if shape == _SHAPE_FILL:
            hbr = CreateSolidBrush(fg)
            rect = (ctypes.c_long * 4)(px1, py1, px2, py2)
            FillRect(hdc, rect, hbr)
            DeleteObject(hbr)
        elif shape in (_SHAPE_LIGHT, _SHAPE_HEAVY):
            line_lw = light_lw if shape == _SHAPE_LIGHT else heavy_lw
            is_horizontal = (py1 == py2)
            is_vertical = (px1 == px2)
            if is_horizontal:
                ry = py1 - line_lw // 2
                hbr = CreateSolidBrush(fg)
                rect = (ctypes.c_long * 4)(px1, ry, px2, ry + line_lw)
                FillRect(hdc, rect, hbr)
                DeleteObject(hbr)
            elif is_vertical:
                rx = px1 - line_lw // 2
                hbr = CreateSolidBrush(fg)
                rect = (ctypes.c_long * 4)(rx, py1, rx + line_lw, py2)
                FillRect(hdc, rect, hbr)
                DeleteObject(hbr)
            else:
                hpen = CreatePen(PS_SOLID, line_lw, fg)
                old_pen = SelectObject_fn(hdc, hpen)
                GetStockObject_fn = gdi32.GetStockObject
                GetStockObject_fn.restype = W.HGDIOBJ
                GetStockObject_fn.argtypes = [ctypes.c_int]
                hbr_null = GetStockObject_fn(5)
                old_brush = SelectObject_fn(hdc, hbr_null)
                MoveToEx(hdc, px1, py1, None)
                LineTo(hdc, px2, py2)
                SelectObject_fn(hdc, old_pen)
                SelectObject_fn(hdc, old_brush)
                DeleteObject(hpen)
        elif shape == _SHAPE_EMPTY_RECT:
            line_lw = light_lw
            hbr = CreateSolidBrush(fg)
            top = (ctypes.c_long * 4)(px1, py1, px2, py1 + line_lw)
            FillRect(hdc, top, hbr)
            bottom = (ctypes.c_long * 4)(px1, py2 - line_lw, px2, py2)
            FillRect(hdc, bottom, hbr)
            left = (ctypes.c_long * 4)(px1, py1, px1 + line_lw, py2)
            FillRect(hdc, left, hbr)
            right = (ctypes.c_long * 4)(px2 - line_lw, py1, px2, py2)
            FillRect(hdc, right, hbr)
            DeleteObject(hbr)
        elif shape == _SHAPE_ROUND_RECT:
            line_lw = light_lw
            cr = min(light_lw * 5, min(w, h) // 2)
            hpen = CreatePen(PS_SOLID, line_lw, fg)
            old_pen = SelectObject_fn(hdc, hpen)
            GetStockObject_fn = gdi32.GetStockObject
            GetStockObject_fn.restype = W.HGDIOBJ
            GetStockObject_fn.argtypes = [ctypes.c_int]
            hbr_null = GetStockObject_fn(5)
            old_brush = SelectObject_fn(hdc, hbr_null)
            cx = (px1 + px2) // 2
            cy = (py1 + py2) // 2
            if cp == 0x256D:
                Arc(hdc, px1 - cr, py1 - cr, px1 + cr, py1 + cr,
                    px1, cy, cx, py1)
                MoveToEx(hdc, cx, py1, None)
                LineTo(hdc, px2, py1)
                MoveToEx(hdc, px1, cy, None)
                LineTo(hdc, px1, py2)
            elif cp == 0x256E:
                Arc(hdc, px2 - cr, py1 - cr, px2 + cr, py1 + cr,
                    cx, py1, px2, cy)
                MoveToEx(hdc, px1, py1, None)
                LineTo(hdc, cx, py1)
                MoveToEx(hdc, px2, cy, None)
                LineTo(hdc, px2, py2)
            elif cp == 0x256F:
                Arc(hdc, px2 - cr, py2 - cr, px2 + cr, py2 + cr,
                    px2, cy, cx, py2)
                MoveToEx(hdc, px1, py2, None)
                LineTo(hdc, cx, py2)
                MoveToEx(hdc, px2, py1, None)
                LineTo(hdc, px2, cy)
            elif cp == 0x2570:
                Arc(hdc, px1 - cr, py2 - cr, px1 + cr, py2 + cr,
                    cx, py2, px1, cy)
                MoveToEx(hdc, cx, py2, None)
                LineTo(hdc, px2, py2)
                MoveToEx(hdc, px1, py1, None)
                LineTo(hdc, px1, cy)
            SelectObject_fn(hdc, old_pen)
            SelectObject_fn(hdc, old_brush)
            DeleteObject(hpen)
        elif shape == _SHAPE_SHADE:
            density = {0x2591: 0.25, 0x2592: 0.50, 0x2593: 0.75}.get(cp, 1.0)
            _draw_shade_pattern(gdi32, user32, hdc, px1, py1, px2, py2, fg, bg, density)



def _draw_shade_pattern(gdi32, user32, hdc, x1, y1, x2, y2, fg, bg, density):
    import ctypes
    import ctypes.wintypes as W

    FillRect = user32.FillRect
    FillRect.restype = ctypes.c_int
    FillRect.argtypes = [W.HDC, ctypes.c_void_p, W.HBRUSH]

    CreateSolidBrush = gdi32.CreateSolidBrush
    CreateSolidBrush.restype = W.HBRUSH
    CreateSolidBrush.argtypes = [W.COLORREF]

    DeleteObject = gdi32.DeleteObject
    DeleteObject.restype = W.BOOL
    DeleteObject.argtypes = [W.HGDIOBJ]

    hbr = CreateSolidBrush(fg)
    cw = x2 - x1
    ch = y2 - y1
    if cw <= 0 or ch <= 0:
        DeleteObject(hbr)
        return

    if density >= 1.0:
        rect = (ctypes.c_long * 4)(x1, y1, x2, y2)
        FillRect(hdc, rect, hbr)
        DeleteObject(hbr)
        return

    dot_size = max(2, min(cw, ch) // 4)
    cols = max(1, cw // dot_size)
    rows = max(1, ch // dot_size)

    if density <= 0.25:
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 4 == 0:
                    dx = x1 + col * dot_size
                    dy = y1 + row * dot_size
                    rect = (ctypes.c_long * 4)(dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2))
                    FillRect(hdc, rect, hbr)
    elif density <= 0.50:
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 2 == 0:
                    dx = x1 + col * dot_size
                    dy = y1 + row * dot_size
                    rect = (ctypes.c_long * 4)(dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2))
                    FillRect(hdc, rect, hbr)
    else:
        rect = (ctypes.c_long * 4)(x1, y1, x2, y2)
        FillRect(hdc, rect, hbr)
        bg_brush = CreateSolidBrush(bg if bg != 0x0C0C0C else 0x0C0C0C)
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 4 == 0:
                    dx = x1 + col * dot_size
                    dy = y1 + row * dot_size
                    rect = (ctypes.c_long * 4)(dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2))
                    FillRect(hdc, rect, bg_brush)
        DeleteObject(bg_brush)

    DeleteObject(hbr)


def _ext_text_out_fallback(gdi32, hdc, x, y, w, h, cp, fg, bg):
    import ctypes
    import ctypes.wintypes as W
    import struct
    SetTextColor = gdi32.SetTextColor
    SetTextColor.restype = W.COLORREF
    SetTextColor.argtypes = [W.HDC, W.COLORREF]
    SetBkColor = gdi32.SetBkColor
    SetBkColor.restype = W.COLORREF
    SetBkColor.argtypes = [W.HDC, W.COLORREF]
    ExtTextOutW = gdi32.ExtTextOutW
    ExtTextOutW.restype = W.BOOL
    ExtTextOutW.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, W.UINT,
                            ctypes.c_void_p, ctypes.c_wchar_p, W.UINT, ctypes.c_void_p]
    OPAQUE = 2
    SetTextColor(hdc, fg)
    SetBkColor(hdc, bg)
    rect = struct.pack("llll", x, y, x + w, y + h)
    rect_buf = ctypes.create_string_buffer(rect)
    ExtTextOutW(hdc, x, y, OPAQUE, rect_buf, chr(cp), 1, None)


_BUNDLED_FONT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "fonts", "MapleMono-NF-CN-Regular.ttf",
)


def _load_font_pair(ImageFont, size: int):
    """加载字体对 (ascii_font, cjk_font)

    优先使用项目内嵌的 MapleMono NF CN（等宽 + CJK + Nerd Font 图标覆盖），
    找不到则回退系统字体。
    """
    font = None
    if os.path.isfile(_BUNDLED_FONT_PATH):
        try:
            font = ImageFont.truetype(_BUNDLED_FONT_PATH, size)
        except (OSError, IOError):
            _logger.warning("内嵌字体加载失败: %s", _BUNDLED_FONT_PATH)

    if font is not None:
        return font, font

    ascii_font = None
    for name in ("Consolas", "DejaVu Sans Mono", "Courier New", "Liberation Mono", "Menlo"):
        try:
            ascii_font = ImageFont.truetype(name, size)
            break
        except (OSError, IOError):
            continue
    if ascii_font is None:
        try:
            ascii_font = ImageFont.truetype("consola.ttf", size)
        except (OSError, IOError):
            ascii_font = ImageFont.load_default()

    cjk_font = None
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc",
                 "msgothic.ttc", "NotoSansCJK-Regular.otf", "NotoSansSC-Regular.otf"):
        try:
            cjk_font = ImageFont.truetype(name, size)
            break
        except (OSError, IOError):
            continue
    if cjk_font is None:
        cjk_font = ascii_font

    return ascii_font, cjk_font


def _render_pillow(path: str, buf: dict, ext: str) -> Optional[str]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return "PNG/JPG/BMP output requires Pillow: pip install pillow (or use .svg instead)"

    from ..config.common import IS_WINDOWS
    if IS_WINDOWS:
        err = _render_gdi(path, buf, ext, Image)
        if err is None:
            return None
        _logger.warning("GDI 渲染失败，回退 Pillow: %s", err)

    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = _expand_lines(buf)
    cell_w = 8
    cell_h = 16
    font_size = cell_h - 2

    ascii_font, cjk_font = _load_font_pair(ImageFont, font_size)

    img = Image.new("RGB", (cols * cell_w, rows * cell_h), (12, 12, 12))
    draw = ImageDraw.Draw(img)

    for y, line in enumerate(lines):
        if y >= rows:
            break
        x = 0
        col = 0
        while col < cols and col < len(line):
            cell = line[col]
            d = cell.get("d", " ")
            cw = _char_width(d) if d != " " else 1

            bg_hex = _resolve_color(cell.get("b", "default"), is_fg=False)
            if bg_hex:
                bg_rgb = _hex_to_rgb(bg_hex)
                draw.rectangle(
                    [x, y * cell_h, x + cw * cell_w, (y + 1) * cell_h],
                    fill=bg_rgb,
                )

            if d.strip():
                fg_hex = _resolve_color(cell.get("f", "default"), is_fg=True) or "#e5e5e5"
                fg_rgb = _hex_to_rgb(fg_hex)
                font = cjk_font if _is_cjk_char(d) else ascii_font
                draw.text((x, y * cell_h), d, fill=fg_rgb, font=font)

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "png": "PNG"}.get(ext.lstrip("."), "PNG")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path, fmt)
        _logger.info("Image written to %s (%s)", path, fmt)
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None


def _render_gdi(path: str, buf: dict, ext: str, PIL_Image) -> Optional[str]:
    """使用 Windows GDI 渲染终端屏幕快照

    GDI 的 ExtTextOutW 自带系统字体回退，CJK 字符自动映射到微软雅黑等字体，
    与 Windows Terminal 的 GDI 渲染器原理相同。
    """
    import ctypes
    import ctypes.wintypes as W
    import struct

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    CreateCompatibleDC = gdi32.CreateCompatibleDC
    CreateCompatibleDC.restype = W.HDC
    CreateCompatibleDC.argtypes = [W.HDC]

    CreateDIBSection = gdi32.CreateDIBSection
    CreateDIBSection.restype = W.HBITMAP
    CreateDIBSection.argtypes = [W.HDC, ctypes.c_void_p, W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]

    SelectObject = gdi32.SelectObject
    SelectObject.restype = W.HGDIOBJ
    SelectObject.argtypes = [W.HDC, W.HGDIOBJ]

    DeleteObject = gdi32.DeleteObject
    DeleteObject.restype = W.BOOL
    DeleteObject.argtypes = [W.HGDIOBJ]

    DeleteDC = gdi32.DeleteDC
    DeleteDC.restype = W.BOOL
    DeleteDC.argtypes = [W.HDC]

    CreateFontW = gdi32.CreateFontW
    CreateFontW.restype = W.HFONT
    CreateFontW.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, W.DWORD, W.DWORD, W.DWORD,
                            W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                            W.DWORD, ctypes.c_wchar_p]

    GetTextMetricsW = gdi32.GetTextMetricsW
    GetTextMetricsW.restype = W.BOOL
    GetTextMetricsW.argtypes = [W.HDC, ctypes.c_void_p]

    SetBkMode = gdi32.SetBkMode
    SetBkMode.restype = ctypes.c_int
    SetBkMode.argtypes = [W.HDC, ctypes.c_int]

    SetTextColor = gdi32.SetTextColor
    SetTextColor.restype = W.COLORREF
    SetTextColor.argtypes = [W.HDC, W.COLORREF]

    SetBkColor = gdi32.SetBkColor
    SetBkColor.restype = W.COLORREF
    SetBkColor.argtypes = [W.HDC, W.COLORREF]

    ExtTextOutW = gdi32.ExtTextOutW
    ExtTextOutW.restype = W.BOOL
    ExtTextOutW.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, W.UINT,
                            ctypes.c_void_p, ctypes.c_wchar_p, W.UINT, ctypes.c_void_p]

    OPAQUE = 2
    FW_NORMAL = 400
    FW_BOLD = 700
    DEFAULT_CHARSET = 1
    OUT_DEFAULT_PRECIS = 0
    CLIP_DEFAULT_PRECIS = 0
    CLEARTYPE_QUALITY = 5
    FIXED_PITCH = 1
    FF_MODERN = 48
    BI_RGB = 0
    DIB_RGB_COLORS = 0

    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = _expand_lines(buf)

    font_size = 14

    hdc = CreateCompatibleDC(None)
    if not hdc:
        return "CreateCompatibleDC failed"

    _PREFERRED_FONT = "Maple Mono NF CN"
    _FALLBACK_FONT = "Consolas"

    font_name = _PREFERRED_FONT
    hfont = CreateFontW(
        -font_size, 0, 0, 0, FW_NORMAL, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, font_name
    )
    hfont_bold = CreateFontW(
        -font_size, 0, 0, 0, FW_BOLD, 0, 0, 0,
        DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, font_name
    )
    SelectObject(hdc, hfont)

    class TEXTMETRIC(ctypes.Structure):
        _fields_ = [
            ("tmHeight", W.LONG), ("tmAscent", W.LONG), ("tmDescent", W.LONG),
            ("tmInternalLeading", W.LONG), ("tmExternalLeading", W.LONG),
            ("tmAveCharWidth", W.LONG), ("tmMaxCharWidth", W.LONG),
            ("tmWeight", W.LONG), ("tmOverhang", W.LONG),
            ("tmDigitizedAspectX", W.LONG), ("tmDigitizedAspectY", W.LONG),
            ("tmFirstChar", W.WCHAR), ("tmLastChar", W.WCHAR),
            ("tmDefaultChar", W.WCHAR), ("tmBreakChar", W.WCHAR),
            ("tmItalic", W.BYTE), ("tmUnderlined", W.BYTE),
            ("tmStruckOut", W.BYTE), ("tmPitchAndFamily", W.BYTE),
            ("tmCharSet", W.BYTE),
        ]

    tm = TEXTMETRIC()
    GetTextMetricsW(hdc, ctypes.byref(tm))

    # 若首选字体未生效（GDI 会静默回退系统字体），检查 tmAveCharWidth 是否合理
    # MapleMono 是等宽字体，如果回退到比例字体则 tmMaxCharWidth 会远大于 tmAveCharWidth
    if tm.tmMaxCharWidth > tm.tmAveCharWidth * 2:
        _logger.info("GDI 字体 '%s' 未生效，回退 '%s'", font_name, _FALLBACK_FONT)
        DeleteObject(hfont)
        DeleteObject(hfont_bold)
        font_name = _FALLBACK_FONT
        hfont = CreateFontW(
            -font_size, 0, 0, 0, FW_NORMAL, 0, 0, 0,
            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, font_name
        )
        hfont_bold = CreateFontW(
            -font_size, 0, 0, 0, FW_BOLD, 0, 0, 0,
            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS,
            CLEARTYPE_QUALITY, FIXED_PITCH | FF_MODERN, font_name
        )
        SelectObject(hdc, hfont)
        GetTextMetricsW(hdc, ctypes.byref(tm))
    cell_w = tm.tmAveCharWidth
    cell_h = tm.tmHeight
    if cell_w <= 0:
        cell_w = 8
    if cell_h <= 0:
        cell_h = 16

    _logger.debug("GDI font metrics: cell_w=%d cell_h=%d ascent=%d descent=%d",
                  cell_w, cell_h, tm.tmAscent, tm.tmDescent)

    img_w = cols * cell_w
    img_h = rows * cell_h

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
            ("biPlanes", W.WORD), ("biBitCount", W.WORD),
            ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
            ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG),
            ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = img_w
    bmi.biHeight = -img_h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB

    ppv_bits = ctypes.c_void_p()
    hbitmap = CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                ctypes.byref(ppv_bits), None, 0)
    if not hbitmap:
        DeleteObject(hfont)
        DeleteObject(hfont_bold)
        DeleteDC(hdc)
        return "CreateDIBSection failed"

    SelectObject(hdc, hbitmap)
    SetBkMode(hdc, OPAQUE)
    SetBkColor(hdc, 0x0C0C0C)

    for y, line in enumerate(lines):
        if y >= rows:
            break
        col = 0
        while col < cols and col < len(line):
            cell = line[col]
            d = cell.get("d", " ")
            cw = _char_width(d) if d != " " else 1
            x = col * cell_w
            yp = y * cell_h

            bg_hex = _resolve_color(cell.get("b", "default"), is_fg=False)
            bg_colorref = _hex_to_colorref(bg_hex) if bg_hex else 0x0C0C0C

            fg_hex = _resolve_color(cell.get("f", "default"), is_fg=True) or "#e5e5e5"
            fg_colorref = _hex_to_colorref(fg_hex)

            bold = cell.get("bo", False)

            if _is_block_element(d):
                _draw_block_element(gdi32, user32, hdc, x, yp, cw * cell_w, cell_h,
                                     ord(d), fg_colorref, bg_colorref)
            else:
                SelectObject(hdc, hfont_bold if bold else hfont)
                SetTextColor(hdc, fg_colorref)
                SetBkColor(hdc, bg_colorref)
                rect = struct.pack("llll", x, yp, x + cw * cell_w, yp + cell_h)
                rect_buf = ctypes.create_string_buffer(rect)
                ExtTextOutW(hdc, x, yp, OPAQUE, rect_buf, d, len(d), None)

            col += cw if cw > 1 and d != " " else 1

    buf_size = img_w * img_h * 4
    pixel_data = (ctypes.c_ubyte * buf_size).from_address(ppv_bits.value)
    raw = bytes(pixel_data)
    img = PIL_Image.frombytes("RGB", (img_w, img_h), raw, "raw", "BGRX", 0, 1)

    DeleteObject(hfont)
    DeleteObject(hfont_bold)
    DeleteObject(hbitmap)
    DeleteDC(hdc)

    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "png": "PNG"}.get(ext.lstrip("."), "PNG")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path, fmt)
        _logger.info("GDI image written to %s (%s)", path, fmt)
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None


def _hex_to_colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
