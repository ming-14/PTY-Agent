"""奖杯与成就柜的 ASCII/制表符绘制（金色 ANSI 上色，不使用 emoji）。

所有精灵用固定显示宽度的行字符串表示，调用方通过 (row, col) 定位输出。
"""

from __future__ import annotations

from typing import List, Tuple

# 金色系 256 色
GOLD_HI = "\033[38;5;228m"   # 亮金
GOLD = "\033[38;5;184m"      # 金
GOLD_DIM = "\033[38;5;178m"  # 暗金（描边/底座）


# 奖杯精灵：每个字符串显示宽度均为 TROPHY_W 列（含边缘空格，绘制时跳过空格实现透明）。
# 轮廓：宽碗口 → 杯身（两侧把手）→ 收窄杯脚 → 台阶底座（无数字刻字）
_RAW_TROPHY = [
    " ▄▄▄▄▄▄▄▄▄▄ ",   # 杯口上沿
    " ██████████ ",    # 杯口
    " ██████████ ",    # 杯身
    "▐██████████▌",    # 杯身最宽处 + 把手上端
    "▐█ ██████ █▌",    # 把手环（两侧镂空）+ 杯身收窄
    "    ████    ",    # 杯脚
    "   ██████   ",    # 底座台阶
    "  ████████  ",    # 底座
]
TROPHY_W = 12
TROPHY_H = len(_RAW_TROPHY)

# 每一行的金色：杯口亮金，杯身金，杯脚与底座暗金
TROPHY_ROW_COLOR = [GOLD_HI, GOLD, GOLD, GOLD, GOLD,
                    GOLD_DIM, GOLD_DIM, GOLD_DIM]

# 成就柜与棋盘右侧之间的间距（列）
CABINET_GAP = 2


def trophy() -> List[str]:
    """返回金色奖杯的每行**原始字形**（不含 ANSI）。

    调用方负责区分空格（跳过 → 透明）与字素（绘制 → 覆盖），
    从而让奖杯像浮层一样，只要"浮"在上层，空隙不影响下层画面。
    """
    return list(_RAW_TROPHY)


def cabinet(filled: bool, glow: bool = False, win_value: int = 2048) -> Tuple[List[str], int, int]:
    """绘制成就柜（一个带陈列槽的柜台）。

    参数:
        filled: 是否已陈列奖杯（未陈列则显示空槽）。
        glow: 是否请求"泛金"效果（仅作标记，颜色由调用方以 style 参数应用）。
        win_value: 陈列的成就数值刻字。
    返回:
        (每行字符串, 宽度, 高度)——行内不含任何 ANSI 转义，
        调用方需要上色时通过画布 style 参数完成，避免转义序列被当作
        字面字符逐格存放（以防浮层覆盖时产生裸转义/乱码）。
    """
    slot_w = TROPHY_W          # 陈列槽宽度与奖杯同宽
    body_w = slot_w + 4        # 槽位（含边框）左右各垫 1 格
    w = body_w + 2             # 外加左右边框

    # 槽位边框本身占 slot_w + 2 列，剩余空间左右各 1 格，保证各行等宽
    pad_left = 1
    pad_right = body_w - pad_left - (slot_w + 2)

    slot_text = " {} ✦ ".format(win_value).center(slot_w) if filled else " " * slot_w

    rows = [
        "┌" + "─" * body_w + "┐",
        "│" + " ACHIEVEMENTS ".center(body_w) + "│",
        "│" + " " * pad_left + "┌" + "─" * slot_w + "┐" + " " * pad_right + "│",
        "│" + " " * pad_left + "│" + slot_text + "│" + " " * pad_right + "│",
        "│" + " " * pad_left + "└" + "─" * slot_w + "┘" + " " * pad_right + "│",
        "└" + "─" * body_w + "┘",
    ]
    # 不再内嵌 ANSI 转义：glow 效果由调用方（ui._draw_cabinet）以 style 参数上色
    return rows, w, len(rows)