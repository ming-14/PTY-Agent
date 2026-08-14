"""渲染共享基础 — 颜色映射、字符宽度判定、行格式展开

各渲染后端 (SVG / Pillow / GDI) 共用的纯函数与常量集中于此，
形成单向依赖底座，消除重复定义。
"""

import os
import unicodedata
from typing import Optional

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS

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


def _is_block_element(c: str) -> bool:
    if len(c) != 1:
        return False
    cp = ord(c)
    return 0x2500 <= cp < 0x25A0


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
                    full[c] = {
                        "d": cell.get("d", " "),
                        "f": cell.get("f", "default"),
                        "b": cell.get("b", "default"),
                        "bo": cell.get("bo", False),
                    }
            expanded.append(full)
        else:
            expanded.append(raw_line)
    while len(expanded) < rows:
        expanded.append([dict(default_cell) for _ in range(cols)])
    return expanded


def _hex_to_colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
