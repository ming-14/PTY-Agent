"""Box Drawing 字符 (U+2500–U+259F) 的 GDI 几何绘制

_BOX_DRAWING_TABLE 将每个码点映射为几何指令序列 (shape, 起止点分数+线宽偏移)；
_draw_block_element 用 GDI 原语 (FillRect / LineTo / Arc) 按指令绘制，
对齐 Windows Terminal 的矢量绘制效果，避免依赖字体中的 box drawing 字形。
"""

import logging

_logger = logging.getLogger("pty-client")

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
        0x2500: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x2501: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x2502: [(L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2503: [(H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2504: [
            (L, 0, 0, 0.5, 0, 2 / 9, 0, 0.5, 0),
            (L, 3 / 9, 0, 0.5, 0, 5 / 9, 0, 0.5, 0),
            (L, 6 / 9, 0, 0.5, 0, 8 / 9, 0, 0.5, 0),
        ],
        0x2505: [
            (H, 0, 0, 0.5, 0, 2 / 9, 0, 0.5, 0),
            (H, 3 / 9, 0, 0.5, 0, 5 / 9, 0, 0.5, 0),
            (H, 6 / 9, 0, 0.5, 0, 8 / 9, 0, 0.5, 0),
        ],
        0x2506: [
            (L, 0.5, 0, 0, 0, 0.5, 0, 2 / 9, 0),
            (L, 0.5, 0, 3 / 9, 0, 0.5, 0, 5 / 9, 0),
            (L, 0.5, 0, 6 / 9, 0, 0.5, 0, 8 / 9, 0),
        ],
        0x2507: [
            (H, 0.5, 0, 0, 0, 0.5, 0, 2 / 9, 0),
            (H, 0.5, 0, 3 / 9, 0, 0.5, 0, 5 / 9, 0),
            (H, 0.5, 0, 6 / 9, 0, 0.5, 0, 8 / 9, 0),
        ],
        0x2508: [
            (L, 0, 0, 0.5, 0, 2 / 12, 0, 0.5, 0),
            (L, 3 / 12, 0, 0.5, 0, 5 / 12, 0, 0.5, 0),
            (L, 6 / 12, 0, 0.5, 0, 8 / 12, 0, 0.5, 0),
            (L, 9 / 12, 0, 0.5, 0, 11 / 12, 0, 0.5, 0),
        ],
        0x2509: [
            (H, 0, 0, 0.5, 0, 2 / 12, 0, 0.5, 0),
            (H, 3 / 12, 0, 0.5, 0, 5 / 12, 0, 0.5, 0),
            (H, 6 / 12, 0, 0.5, 0, 8 / 12, 0, 0.5, 0),
            (H, 9 / 12, 0, 0.5, 0, 11 / 12, 0, 0.5, 0),
        ],
        0x250A: [
            (L, 0.5, 0, 0, 0, 0.5, 0, 2 / 12, 0),
            (L, 0.5, 0, 3 / 12, 0, 0.5, 0, 5 / 12, 0),
            (L, 0.5, 0, 6 / 12, 0, 0.5, 0, 8 / 12, 0),
            (L, 0.5, 0, 9 / 12, 0, 0.5, 0, 11 / 12, 0),
        ],
        0x250B: [
            (H, 0.5, 0, 0, 0, 0.5, 0, 2 / 12, 0),
            (H, 0.5, 0, 3 / 12, 0, 0.5, 0, 5 / 12, 0),
            (H, 0.5, 0, 6 / 12, 0, 0.5, 0, 8 / 12, 0),
            (H, 0.5, 0, 9 / 12, 0, 0.5, 0, 11 / 12, 0),
        ],
        0x250C: [
            (L, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x250D: [
            (H, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x250E: [(L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x250F: [(H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2510: [
            (L, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2511: [
            (H, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2512: [(L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2513: [(H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2514: [
            (L, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x2515: [
            (H, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x2516: [(L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2517: [(H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2518: [
            (L, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x2519: [
            (H, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x251A: [(L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x251B: [(H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x251C: [(L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x251D: [(H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x251E: [
            (L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x251F: [
            (L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2520: [(L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2521: [
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2522: [
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2523: [(H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2524: [(L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2525: [(H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2526: [
            (L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2527: [
            (L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2528: [(L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2529: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x252A: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x252B: [(H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x252C: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x252D: [
            (H, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x252E: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x252F: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2530: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2531: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2532: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2533: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2534: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2535: [
            (H, 0, 0, 0.5, 0, 0.5, 0.5, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x2536: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -0.5, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x2537: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2538: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2539: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x253A: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
        ],
        0x253B: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x253C: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x253D: [
            (H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x253E: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x253F: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2540: [
            (L, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2541: [
            (L, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2542: [(L, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x2543: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2544: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2545: [
            (H, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2546: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2547: [
            (H, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2548: [
            (H, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0),
        ],
        0x2549: [
            (H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x254A: [
            (L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0),
            (H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0),
            (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x254B: [(H, 0, 0, 0.5, 0, 1, 0, 0.5, 0), (H, 0.5, 0, 0, 0, 0.5, 0, 1, 0)],
        0x254C: [
            (L, 0, 0, 0.5, 0, 2 / 6, 0, 0.5, 0),
            (L, 3 / 6, 0, 0.5, 0, 5 / 6, 0, 0.5, 0),
        ],
        0x254D: [
            (H, 0, 0, 0.5, 0, 2 / 6, 0, 0.5, 0),
            (H, 3 / 6, 0, 0.5, 0, 5 / 6, 0, 0.5, 0),
        ],
        0x254E: [
            (L, 0.5, 0, 0, 0, 0.5, 0, 2 / 6, 0),
            (L, 0.5, 0, 3 / 6, 0, 0.5, 0, 5 / 6, 0),
        ],
        0x254F: [
            (H, 0.5, 0, 0, 0, 0.5, 0, 2 / 6, 0),
            (H, 0.5, 0, 3 / 6, 0, 0.5, 0, 5 / 6, 0),
        ],
        0x2550: [(L, 0, 0, 0.5, -1, 1, 0, 0.5, -1), (L, 0, 0, 0.5, 1, 1, 0, 0.5, 1)],
        0x2551: [(L, 0.5, -1, 0, 0, 0.5, -1, 1, 0), (L, 0.5, 1, 0, 0, 0.5, 1, 1, 0)],
        0x2552: [
            (L, 0.5, -0.5, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0.5, -0.5, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0.5, -1, 0.5, 0, 1, 0),
        ],
        0x2553: [
            (L, 0.5, -1, 0.5, -0.5, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0.5, -0.5, 0.5, 1, 1, 0),
            (L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
        ],
        0x2554: [(E, 0.5, -1, 0.5, -1, 1.5, 0, 1.5, 0)],
        0x2555: [
            (L, 0, 0, 0.5, -1, 0.5, 0.5, 0.5, -1),
            (L, 0, 0, 0.5, 1, 0.5, 0.5, 0.5, 1),
            (L, 0.5, 0, 0.5, -1, 0.5, 0, 1, 0),
        ],
        0x2556: [
            (L, 0.5, -1, 0.5, -0.5, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0.5, -0.5, 0.5, 1, 1, 0),
            (L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
        ],
        0x2557: [(E, -0.5, 0, 0.5, -1, 0.5, 1, 1.5, 0)],
        0x2558: [
            (L, 0.5, -0.5, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0.5, -0.5, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 1),
        ],
        0x2559: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 0.5, 0.5),
            (L, 0.5, 1, 0, 0, 0.5, 1, 0.5, 0.5),
            (L, 0.5, -1, 0.5, 0, 1, 0, 0.5, 0),
        ],
        0x255A: [(E, 0.5, -1, -0.5, 0, 1.5, 0, 0.5, 1)],
        0x255B: [
            (L, 0, 0, 0.5, -1, 0.5, 0.5, 0.5, -1),
            (L, 0, 0, 0.5, 1, 0.5, 0.5, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 1),
        ],
        0x255C: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 0.5, 0.5),
            (L, 0.5, 1, 0, 0, 0.5, 1, 0.5, 0.5),
            (L, 0, 0, 0.5, 0, 0.5, 1, 0.5, 0),
        ],
        0x255D: [(E, -0.5, 0, -0.5, 0, 0.5, 1, 0.5, 1)],
        0x255E: [
            (L, 0.5, 0, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0.5, 0, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x255F: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0, 0, 0.5, 1, 1, 0),
            (L, 0.5, 1, 0.5, 0, 1, 0, 0.5, 0),
        ],
        0x2560: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 1, 0),
            (E, 0.5, 1, -0.5, 0, 1.5, 0, 0.5, -1),
        ],
        0x2561: [
            (L, 0, 0, 0.5, -1, 0.5, 0, 0.5, -1),
            (L, 0, 0, 0.5, 1, 0.5, 0, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x2562: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0, 0, 0.5, 1, 1, 0),
            (L, 0, 0, 0.5, 0, 0.5, -1, 0.5, 0),
        ],
        0x2563: [
            (L, 0.5, 1, 0, 0, 0.5, 1, 1, 0),
            (E, -0.5, 0, -0.5, 0, 0.5, -1, 0.5, -1),
        ],
        0x2564: [
            (L, 0, 0, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0, 0, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0.5, 1, 0.5, 0, 1, 0),
        ],
        0x2565: [
            (L, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, -1, 0.5, 0, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0.5, 0, 0.5, 1, 1, 0),
        ],
        0x2566: [
            (L, 0, 0, 0.5, -1, 1, 0, 0.5, -1),
            (E, -0.5, 0, 0.5, 1, 0.5, -1, 1.5, 0),
        ],
        0x2567: [
            (L, 0, 0, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0, 0, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 0.5, -1),
        ],
        0x2568: [
            (L, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
            (L, 0.5, -1, 0, 0, 0.5, -1, 0.5, 0),
            (L, 0.5, 1, 0, 0, 0.5, 1, 0.5, 0),
        ],
        0x2569: [
            (L, 0, 0, 0.5, 1, 1, 0, 0.5, 1),
            (E, -0.5, 0, -0.5, 0, 0.5, -1, 0.5, -1),
        ],
        0x256A: [
            (L, 0, 0, 0.5, -1, 1, 0, 0.5, -1),
            (L, 0, 0, 0.5, 1, 1, 0, 0.5, 1),
            (L, 0.5, 0, 0, 0, 0.5, 0, 1, 0),
        ],
        0x256B: [
            (L, 0.5, -1, 0, 0, 0.5, -1, 1, 0),
            (L, 0.5, 1, 0, 0, 0.5, 1, 1, 0),
            (L, 0, 0, 0.5, 0, 1, 0, 0.5, 0),
        ],
        0x256C: [(E, -0.5, 0, -0.5, 0, 0.5, -1, 0.5, -1)],
        0x256D: [(R, 0.5, 0, 0.5, 0, 1, 0, 1, 0)],
        0x256E: [(R, 0, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x256F: [(R, 0, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2570: [(R, 0.5, 0, 0, 0, 1, 0, 0.5, 0)],
        0x2571: [(L, 0, 0, 1, 0, 1, 0, 0, 0)],
        0x2572: [(L, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2573: [(L, 0, 0, 1, 0, 1, 0, 0, 0), (L, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2574: [(L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0)],
        0x2575: [(L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2576: [(L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x2577: [(L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2578: [(H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0)],
        0x2579: [(H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x257A: [(H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x257B: [(H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x257C: [(L, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x257D: [(L, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0), (H, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x257E: [(H, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0), (L, 0.5, 0, 0.5, 0, 1, 0, 0.5, 0)],
        0x257F: [(H, 0.5, 0, 0, 0, 0.5, 0, 0.5, 0), (L, 0.5, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2580: [(F, 0, 0, 0, 0, 1, 0, 0.5, 0)],
        0x2581: [(F, 0, 0, 7 / 8, 0, 1, 0, 1, 0)],
        0x2582: [(F, 0, 0, 3 / 4, 0, 1, 0, 1, 0)],
        0x2583: [(F, 0, 0, 5 / 8, 0, 1, 0, 1, 0)],
        0x2584: [(F, 0, 0, 0.5, 0, 1, 0, 1, 0)],
        0x2585: [(F, 0, 0, 3 / 8, 0, 1, 0, 1, 0)],
        0x2586: [(F, 0, 0, 1 / 4, 0, 1, 0, 1, 0)],
        0x2587: [(F, 0, 0, 1 / 8, 0, 1, 0, 1, 0)],
        0x2588: [(F, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2589: [(F, 0, 0, 0, 0, 7 / 8, 0, 1, 0)],
        0x258A: [(F, 0, 0, 0, 0, 3 / 4, 0, 1, 0)],
        0x258B: [(F, 0, 0, 0, 0, 5 / 8, 0, 1, 0)],
        0x258C: [(F, 0, 0, 0, 0, 0.5, 0, 1, 0)],
        0x258D: [(F, 0, 0, 0, 0, 3 / 8, 0, 1, 0)],
        0x258E: [(F, 0, 0, 0, 0, 1 / 4, 0, 1, 0)],
        0x258F: [(F, 0, 0, 0, 0, 1 / 8, 0, 1, 0)],
        0x2590: [(F, 0.5, 0, 0, 0, 1, 0, 1, 0)],
        0x2591: [(S, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2592: [(S, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2593: [(S, 0, 0, 0, 0, 1, 0, 1, 0)],
        0x2594: [(F, 0, 0, 0, 0, 1, 0, 1 / 8, 0)],
        0x2595: [(F, 7 / 8, 0, 0, 0, 1, 0, 1, 0)],
        0x2596: [(F, 0, 0, 0.5, 0, 0.5, 0, 1, 0)],
        0x2597: [(F, 0.5, 0, 0.5, 0, 1, 0, 1, 0)],
        0x2598: [(F, 0, 0, 0, 0, 0.5, 0, 0.5, 0)],
        0x2599: [(F, 0, 0, 0, 0, 0.5, 0, 1, 0), (F, 0.5, 0, 0.5, 0, 1, 0, 1, 0)],
        0x259A: [(F, 0, 0, 0, 0, 0.5, 0, 0.5, 0), (F, 0.5, 0, 0.5, 0, 1, 0, 1, 0)],
        0x259B: [(F, 0, 0, 0, 0, 0.5, 0, 1, 0), (F, 0.5, 0, 0, 0, 1, 0, 0.5, 0)],
        0x259C: [(F, 0, 0, 0, 0, 0.5, 0, 0.5, 0), (F, 0.5, 0, 0, 0, 1, 0, 1, 0)],
        0x259D: [(F, 0.5, 0, 0, 0, 1, 0, 0.5, 0)],
        0x259E: [(F, 0, 0, 0.5, 0, 0.5, 0, 1, 0), (F, 0.5, 0, 0, 0, 1, 0, 0.5, 0)],
        0x259F: [(F, 0, 0, 0.5, 0, 0.5, 0, 1, 0), (F, 0.5, 0, 0, 0, 1, 0, 1, 0)],
    }
    return _BOX_DRAWING_TABLE


def _draw_block_element(
    gdi32, user32, hdc, x: int, y: int, w: int, h: int, cp: int, fg: int, bg: int
):
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
    Arc.argtypes = [
        W.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
    ]

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

    for instr in instructions:
        shape = instr[0]
        bx_frac, bx_off, by_frac, by_off = instr[1], instr[2], instr[3], instr[4]
        ex_frac, ex_off, ey_frac, ey_off = instr[5], instr[6], instr[7], instr[8]

        lw = (
            light_lw
            if shape == _SHAPE_LIGHT
            else heavy_lw
            if shape == _SHAPE_HEAVY
            else light_lw
        )

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
            is_horizontal = py1 == py2
            is_vertical = px1 == px2
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
                Arc(hdc, px1 - cr, py1 - cr, px1 + cr, py1 + cr, px1, cy, cx, py1)
                MoveToEx(hdc, cx, py1, None)
                LineTo(hdc, px2, py1)
                MoveToEx(hdc, px1, cy, None)
                LineTo(hdc, px1, py2)
            elif cp == 0x256E:
                Arc(hdc, px2 - cr, py1 - cr, px2 + cr, py1 + cr, cx, py1, px2, cy)
                MoveToEx(hdc, px1, py1, None)
                LineTo(hdc, cx, py1)
                MoveToEx(hdc, px2, cy, None)
                LineTo(hdc, px2, py2)
            elif cp == 0x256F:
                Arc(hdc, px2 - cr, py2 - cr, px2 + cr, py2 + cr, px2, cy, cx, py2)
                MoveToEx(hdc, px1, py2, None)
                LineTo(hdc, cx, py2)
                MoveToEx(hdc, px2, py1, None)
                LineTo(hdc, px2, cy)
            elif cp == 0x2570:
                Arc(hdc, px1 - cr, py2 - cr, px1 + cr, py2 + cr, cx, py2, px1, cy)
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
                    rect = (ctypes.c_long * 4)(
                        dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2)
                    )
                    FillRect(hdc, rect, hbr)
    elif density <= 0.50:
        for row in range(rows):
            for col in range(cols):
                if (row + col) % 2 == 0:
                    dx = x1 + col * dot_size
                    dy = y1 + row * dot_size
                    rect = (ctypes.c_long * 4)(
                        dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2)
                    )
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
                    rect = (ctypes.c_long * 4)(
                        dx, dy, min(dx + dot_size, x2), min(dy + dot_size, y2)
                    )
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
    SetTextColor(hdc, fg)
    SetBkColor(hdc, bg)
    rect = struct.pack("llll", x, y, x + w, y + h)
    rect_buf = ctypes.create_string_buffer(rect)
    ExtTextOutW(hdc, x, y, OPAQUE, rect_buf, chr(cp), 1, None)
