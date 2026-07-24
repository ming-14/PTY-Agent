"""
@file rendering.py
@brief 纯数学圆环渲染函数（alpha 掩码 + BGRA 合成）
"""

import math


def build_alpha_mask(size, outer_r, inner_r):
    """构建抗锯齿圆环形状的 alpha 掩码（1 byte/pixel），255=完全不透明

    @param size 掩码边长（像素）
    @param outer_r 外圈半径
    @param inner_r 内圈半径（中间透明处的半径）
    @return bytearray 长度为 size*size
    """
    mask = bytearray(size * size)
    half = size // 2
    for py in range(size):
        dy = py - half
        dy2 = dy * dy
        row = py * size
        for px in range(size):
            dx = px - half
            dist = math.sqrt(dx * dx + dy2)
            if dist <= inner_r - 0.5:
                a = 0
            elif dist <= inner_r + 0.5:
                t = (dist - (inner_r - 0.5)) / 1.0
                a = int(255 * t)
            elif dist <= outer_r - 0.5:
                a = 255
            elif dist <= outer_r + 0.5:
                t = ((outer_r + 0.5) - dist) / 1.0
                a = int(255 * t)
            else:
                a = 0
            mask[row + px] = a
    return mask


def render_ring_from_mask(mask, size, color_r, color_g, color_b, user_alpha):
    """从缓存的 alpha 掩码应用颜色，返回 premultiplied-alpha BGRA 字节数组

    @param mask build_alpha_mask 输出的掩码
    @param size 掩码边长
    @param color_r, color_g, color_b RGB 颜色分量 (0-255)
    @param user_alpha 用户设置的整体透明度 (0-255)
    @return bytearray 长度为 size*size*4 (BGRA)
    """
    buf = bytearray(size * size * 4)
    for py in range(size):
        row_mask = py * size
        row_buf = py * size * 4
        for px in range(size):
            a_shape = mask[row_mask + px]
            if a_shape == 0:
                continue
            a = a_shape * user_alpha // 255
            pr = color_r * a // 255
            pg = color_g * a // 255
            pb = color_b * a // 255
            off = row_buf + px * 4
            buf[off]     = pb
            buf[off + 1] = pg
            buf[off + 2] = pr
            buf[off + 3] = a
    return buf
