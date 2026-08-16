"""渲染共享基础 — 颜色映射、字符宽度判定、行格式展开

各渲染后端 (SVG / Pillow / GDI) 共用的纯函数与常量集中于此，
形成单向依赖底座，消除重复定义。
"""

import functools
import os
import unicodedata
from typing import Optional

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS

try:
    from wcwidth import wcwidth as _wcwidth

    _HAS_WCWIDTH = True
except ImportError:
    _HAS_WCWIDTH = False
    _wcwidth = None

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


@functools.lru_cache(maxsize=4096)
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


@functools.lru_cache(maxsize=4096)
def _char_width(c: str) -> int:
    """字符显示宽度（常见字符缓存，避免逐 cell 查 wcwidth/unicodedata）"""
    if _HAS_WCWIDTH:
        w = _wcwidth(c)
        return w if w > 0 else 1
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


class _SparseLine:
    """稀疏行视图：按列索引读取 cell（缺失返回默认空格），不展开全量网格

    消费方仅按 `line[col]` 读取，视图惰性提供单元格，
    避免稀疏行全量展开 rows×cols 的 dict 创建开销。
    """

    __slots__ = ("_by_col", "_default", "_length")

    def __init__(self, cells: list, cols: int):
        self._by_col = {c["c"]: c for c in cells}
        self._default = {"d": " ", "f": "default", "b": "default", "bo": False}
        self._length = cols

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, col: int) -> dict:
        return self._by_col.get(col, self._default)


# 空行共享：消费方仅按 line[col] 只读，空行按 cols 缓存单个行对象复用，
# 避免每行分配 cols 个默认 cell dict（rows 行 × cols 列）
_default_cell = {"d": " ", "f": "default", "b": "default", "bo": False}
_EMPTY_ROWS: dict = {}


def _empty_row(cols: int) -> list:
    row = _EMPTY_ROWS.get(cols)
    if row is None:
        row = [dict(_default_cell) for _ in range(cols)]
        _EMPTY_ROWS[cols] = row
    return row


def _expand_lines(buf: dict) -> list:
    """将稀疏/全量 lines 统一展开为二维行序列 [[cell_dict, ...], ...]

    稀疏格式: lines[row] = [{"c":col, "d":..., "f":..., "b":..., "bo":...}, ...]
    全量格式: lines[row] = [{"d":..., "f":..., "b":..., "bo":...}, ...]

    稀疏行以 _SparseLine 视图返回（按列索引读取，不展开全量网格），
    消费方按 line[col] 访问即可，语义与原全量展开一致。
    """
    cols = buf.get("cols", DEFAULT_COLS)
    rows = buf.get("rows", DEFAULT_ROWS)
    lines = buf.get("lines", [])
    expanded = []
    for row_idx in range(min(rows, len(lines))):
        raw_line = lines[row_idx]
        if not raw_line:
            expanded.append(_empty_row(cols))
            continue
        first = raw_line[0] if raw_line else None
        if first and "c" in first:
            expanded.append(_SparseLine(raw_line, cols))
        else:
            expanded.append(raw_line)
    while len(expanded) < rows:
        expanded.append(_empty_row(cols))
    return expanded


@functools.lru_cache(maxsize=4096)
def _hex_to_colorref(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
