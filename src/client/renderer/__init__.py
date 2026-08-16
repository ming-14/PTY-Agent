"""终端屏幕快照渲染器 — 将 screenBuffer 渲染为图片或写入文本文件

包结构 (单向依赖):
- common:       颜色映射、字符宽度、行格式展开等共享基础
- svg:          SVG 矢量渲染 + scour 压缩
- image:        像素渲染后端 (Pillow 跨平台 / Windows GDI 原生)
- box_drawing:  Box Drawing 字符的 GDI 几何绘制原语

对外接口 (transport.py 使用): is_image_ext, render_to_file, render_svg_string, _compress_svg
"""

import os
from typing import Optional

from ...config.common import IS_WINDOWS
from .common import _IMAGE_EXTS, is_image_ext, _expand_lines
from .svg import render_svg_string, _compress_svg
from ...logging import get_logger

__all__ = ["render_to_file", "render_svg_string", "_compress_svg", "is_image_ext", "_expand_lines"]

_logger = get_logger("pty-client")


def render_to_file(
    path: str, response: dict, svg_compression_level: int = 1
) -> Optional[str]:
    _, ext = os.path.splitext(path.lower())
    screen_buffer = response.get("screenBuffer")
    is_img = ext in _IMAGE_EXTS

    if is_img and ext == ".svg":
        if not screen_buffer:
            return (
                "SVG output requires a screen buffer"
            )
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
            return "Image output requires a screen buffer"
        return _render_image(path, screen_buffer, ext)

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


def _render_image(path: str, buf: dict, ext: str) -> Optional[str]:
    """像素渲染后端编排: Windows 优先 GDI (失败回退 Pillow), 非 Windows 直接 Pillow

    GDI 路径仍需 PIL.Image 做最终保存，故 Pillow 为图片输出的硬依赖。
    """
    try:
        from PIL import Image
    except ImportError:
        return "PNG/JPG/BMP output requires Pillow: pip install pillow (or use .svg instead)"

    if IS_WINDOWS:
        from .image import render_gdi

        err = render_gdi(path, buf, ext, Image)
        if err is None:
            return None
        _logger.warning("GDI 渲染失败，回退 Pillow: %s", err)

    from .image import render_pillow

    return render_pillow(path, buf, ext)
