"""像素渲染后端 — Pillow 跨平台绘制 + Windows GDI 原生绘制

- render_pillow: 纯 Pillow 绘制 (ASCII 用 Consolas, CJK 用微软雅黑, 字体回退链)
- render_gdi: Windows GDI 原生绘制 (ExtTextOutW + font linking, CJK 自动回退系统字体)

后端选择编排 (Windows 优先 GDI、失败回退 Pillow) 由包 __init__ 负责，
本模块两个函数职责纯粹、互不依赖。
"""

import logging
import os
from typing import Optional

from ...config.common import DEFAULT_COLS, DEFAULT_ROWS
from .common import (
    _char_width,
    _is_cjk_char,
    _is_block_element,
    _resolve_color,
    _hex_to_rgb,
    _hex_to_colorref,
    _expand_lines,
)
from .box_drawing import _draw_block_element

_logger = logging.getLogger("pty-client")


def _load_font_pair(ImageFont, size: int):
    """加载字体对 (ascii_font, cjk_font)

    ASCII 用 Consolas，CJK 用 Microsoft YaHei (msyh.ttc)。
    """
    ascii_font = None
    for name in ("Consolas", "consola.ttf"):
        try:
            ascii_font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    if ascii_font is None:
        ascii_font = ImageFont.load_default()

    cjk_font = None
    try:
        cjk_font = ImageFont.truetype("msyh.ttc", size)
    except OSError:
        cjk_font = ascii_font

    return ascii_font, cjk_font


def render_pillow(path: str, buf: dict, ext: str) -> Optional[str]:
    """纯 Pillow 绘制: 逐格画背景矩形 + 文本"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return "PNG/JPG/BMP output requires Pillow: pip install pillow (or use .svg instead)"

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
                fg_hex = (
                    _resolve_color(cell.get("f", "default"), is_fg=True) or "#e5e5e5"
                )
                fg_rgb = _hex_to_rgb(fg_hex)
                font = cjk_font if _is_cjk_char(d) else ascii_font
                draw.text((x, y * cell_h), d, fill=fg_rgb, font=font)

            x += cw * cell_w
            col += cw if cw > 1 and d != " " else 1

    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "png": "PNG"}.get(
        ext.lstrip("."), "PNG"
    )
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path, fmt)
        _logger.info("Image written to %s (%s)", path, fmt)
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None


def render_gdi(path: str, buf: dict, ext: str, PIL_Image) -> Optional[str]:
    """Windows GDI 渲染: ExtTextOutW + 系统字体回退, DIB 像素转 PIL Image 保存

    GDI 的 font linking 自动将 CJK 字符映射到微软雅黑等系统字体，
    与 Windows Terminal 的 GDI 渲染器原理相同；Box Drawing 字符走矢量绘制。
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
    CreateDIBSection.argtypes = [
        W.HDC,
        ctypes.c_void_p,
        W.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        W.HANDLE,
        W.DWORD,
    ]

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
    CreateFontW.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        W.DWORD,
        ctypes.c_wchar_p,
    ]

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
    ExtTextOutW.argtypes = [
        W.HDC,
        ctypes.c_int,
        ctypes.c_int,
        W.UINT,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        W.UINT,
        ctypes.c_void_p,
    ]

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

    # Consolas 等宽字体；CJK 字符由 GDI font linking 自动用系统字体渲染
    font_name = "Consolas"
    hfont = CreateFontW(
        -font_size,
        0,
        0,
        0,
        FW_NORMAL,
        0,
        0,
        0,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY,
        FIXED_PITCH | FF_MODERN,
        font_name,
    )
    hfont_bold = CreateFontW(
        -font_size,
        0,
        0,
        0,
        FW_BOLD,
        0,
        0,
        0,
        DEFAULT_CHARSET,
        OUT_DEFAULT_PRECIS,
        CLIP_DEFAULT_PRECIS,
        CLEARTYPE_QUALITY,
        FIXED_PITCH | FF_MODERN,
        font_name,
    )
    SelectObject(hdc, hfont)

    class TEXTMETRIC(ctypes.Structure):
        _fields_ = [
            ("tmHeight", W.LONG),
            ("tmAscent", W.LONG),
            ("tmDescent", W.LONG),
            ("tmInternalLeading", W.LONG),
            ("tmExternalLeading", W.LONG),
            ("tmAveCharWidth", W.LONG),
            ("tmMaxCharWidth", W.LONG),
            ("tmWeight", W.LONG),
            ("tmOverhang", W.LONG),
            ("tmDigitizedAspectX", W.LONG),
            ("tmDigitizedAspectY", W.LONG),
            ("tmFirstChar", W.WCHAR),
            ("tmLastChar", W.WCHAR),
            ("tmDefaultChar", W.WCHAR),
            ("tmBreakChar", W.WCHAR),
            ("tmItalic", W.BYTE),
            ("tmUnderlined", W.BYTE),
            ("tmStruckOut", W.BYTE),
            ("tmPitchAndFamily", W.BYTE),
            ("tmCharSet", W.BYTE),
        ]

    tm = TEXTMETRIC()
    GetTextMetricsW(hdc, ctypes.byref(tm))
    cell_w = tm.tmAveCharWidth
    cell_h = tm.tmHeight
    if cell_w <= 0:
        cell_w = 8
    if cell_h <= 0:
        cell_h = 16

    _logger.debug(
        "GDI font metrics: cell_w=%d cell_h=%d ascent=%d descent=%d",
        cell_w,
        cell_h,
        tm.tmAscent,
        tm.tmDescent,
    )

    img_w = cols * cell_w
    img_h = rows * cell_h

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", W.DWORD),
            ("biWidth", W.LONG),
            ("biHeight", W.LONG),
            ("biPlanes", W.WORD),
            ("biBitCount", W.WORD),
            ("biCompression", W.DWORD),
            ("biSizeImage", W.DWORD),
            ("biXPelsPerMeter", W.LONG),
            ("biYPelsPerMeter", W.LONG),
            ("biClrUsed", W.DWORD),
            ("biClrImportant", W.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = img_w
    bmi.biHeight = -img_h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB

    ppv_bits = ctypes.c_void_p()
    hbitmap = CreateDIBSection(
        hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(ppv_bits), None, 0
    )
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
                _draw_block_element(
                    gdi32,
                    user32,
                    hdc,
                    x,
                    yp,
                    cw * cell_w,
                    cell_h,
                    ord(d),
                    fg_colorref,
                    bg_colorref,
                )
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

    fmt = {"jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "png": "PNG"}.get(
        ext.lstrip("."), "PNG"
    )
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        img.save(path, fmt)
        _logger.info("GDI image written to %s (%s)", path, fmt)
    except OSError as e:
        return f"Failed to write {path}: {e}"
    return None
