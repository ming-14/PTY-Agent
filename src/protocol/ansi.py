"""ANSI 转义序列过滤

仅去除终端颜色/样式 (SGR) 码，保留光标定位、清屏、清行等语义控制序列。
与任何业务逻辑无关，独立可测。

注意：
- `\x08` (Backspace)、`\x7f` (DEL) 等 ASCII 控制字符不属于 ANSI 转义序列，
  不会被过滤。过滤仅针对 \x1b 开头的转义序列。
- 实现为线性扫描而非正则：OSC 匹配（\x1b]...BEL/ST）若用惰性 `.*?` 加
  二选一终结符的正则，输入含多个无终结符的 \x1b] 时每个起点都会把
  匹配扩张到串尾再失败，退化为 O(n²)（输出内容可控，属可注入面）。
  改用逐字符状态机保持等价语义且 O(n)。
"""

from ..logging import get_logger
import unicodedata

_logger = get_logger("pty-protocol")

# 过滤规则（仅过滤格式化信息，保留语义控制）:
#   1. CSI SGR: ESC [ ... m   → 颜色/样式码（如 \x1b[31m, \x1b[1m, \x1b[0m）
#   2. OSC:     ESC ] ... BEL/ST → 窗口标题/超链接等非显示内容
#   3. 无终结符的 OSC：原正则匹配失败、整段保留；段内可独立匹配的 SGR 仍剥离
#
# 不匹配（保留，视为有效控制语义）:
#   - 光标定位: H, f, A, B, C, D, s, u
#   - 清屏/清行: J, K
#   - 删除/插入: P, X, L, M
#   - 模式设置: h, l


def _is_re_digit(ch: str) -> bool:
    """等价于 re 的 \\d：ASCII 数字 + Unicode 十进制数字（Nd）"""
    return "0" <= ch <= "9" or unicodedata.category(ch) == "Nd"


def _scan_sgr(text: str, j: int, n: int) -> int:
    """扫描 CSI SGR 参数（数字/分号）后的位置；返回非参数字符下标"""
    while j < n:
        c = text[j]
        if c == ";" or "0" <= c <= "9" or unicodedata.category(c) == "Nd":
            j += 1
            continue
        break
    return j


def strip_ansi(text: str) -> str:
    """去除字符串中的 ANSI 颜色/样式码，保留光标控制、清屏等语义操作

    Args:
        text: 可能包含 ANSI 转义序列的输入字符串。

    Returns:
        过滤掉 SGR 颜色/样式码和 OSC 非显示内容后的字符串。
        清屏序列（\\x1b[2J）、归位（\\x1b[H）、清行（\\x1b[K）等保留。
    """
    if "\x1b" not in text:
        return text
    out = []
    n = len(text)
    pos = 0
    # 已确认自该位置起不存在 OSC 终结符：其后的 \x1b] 全部不匹配，直接
    # 保留。否则每个 \x1b] 起点都会扫描到串尾才失败（等价语义但 O(n²)）。
    osc_dead = -1
    while pos < n:
        esc = text.find("\x1b", pos)
        if esc < 0:
            out.append(text[pos:])
            break
        if esc > pos:
            out.append(text[pos:esc])
        nxt_i = esc + 1
        if nxt_i >= n:
            out.append("\x1b")
            break
        nxt = text[nxt_i]
        if nxt == "[":
            # CSI SGR：参数为数字/分号，终止符 m
            j = _scan_sgr(text, nxt_i + 1, n)
            if j < n and text[j] == "m":
                pos = j + 1  # 剥离 \x1b[...m
                continue
            out.append("\x1b")  # 非 SGR 的 CSI 序列：保留
            pos = nxt_i
            continue
        if nxt == "]":
            if osc_dead >= 0:
                out.append("\x1b]")  # 死区内 OSC 不匹配，整体保留
                pos = nxt_i + 1
                continue
            # OSC：到第一个 BEL 或 ST（\x1b\\）为止
            j = nxt_i + 1
            term = -1
            while j < n:
                ch = text[j]
                if ch == "\x07":
                    term = j + 1
                    break
                if ch == "\x1b" and j + 1 < n and text[j + 1] == "\\":
                    term = j + 2
                    break
                j += 1
            if term >= 0:
                pos = term  # 剥离 \x1b]...BEL/ST
                continue
            osc_dead = esc
            out.append("\x1b")
            pos = nxt_i
            continue
        out.append("\x1b")  # 其他转义序列：保留
        pos = nxt_i
        continue
    stripped = "".join(out)
    if text != stripped:
        _logger.debug(
            "strip_ansi: removed %d chars from %d to %d",
            len(text) - len(stripped),
            len(text),
            len(stripped),
        )
    return stripped
