"""ANSI 转义序列过滤

仅去除终端颜色/样式 (SGR) 码，保留光标定位、清屏、清行等语义控制序列。
与任何业务逻辑无关，独立可测。

注意：
- `\x08` (Backspace)、`\x7f` (DEL) 等 ASCII 控制字符不属于 ANSI 转义序列，
  不会被过滤。过滤仅针对 \x1b 开头的转义序列。
"""

import logging
import re

_logger = logging.getLogger("pty-protocol")

# 匹配规则（仅过滤格式化信息，保留语义控制）:
#   1. CSI SGR: ESC [ ... m   → 颜色/样式码（如 \x1b[31m, \x1b[1m, \x1b[0m）
#   2. OSC:     ESC ] ... BEL/ST → 窗口标题/超链接等非显示内容
#
# 不匹配（保留，视为有效控制语义）:
#   - 光标定位: H, f, A, B, C, D, s, u
#   - 清屏/清行: J, K
#   - 删除/插入: P, X, L, M
#   - 模式设置: h, l
_ANSI_RE = re.compile(
    r'\x1b\[[\d;]*m'                      # CSI SGR: 仅颜色/样式
    r'|\x1b\].*?(?:\x07|\x1b\\)'          # OSC: 窗口标题/超链接
)


def strip_ansi(text: str) -> str:
    """去除字符串中的 ANSI 颜色/样式码，保留光标控制、清屏等语义操作

    Args:
        text: 可能包含 ANSI 转义序列的输入字符串。

    Returns:
        过滤掉 SGR 颜色/样式码和 OSC 非显示内容后的字符串。
        清屏序列（\\x1b[2J）、归位（\\x1b[H）、清行（\\x1b[K）等保留。
    """
    stripped = _ANSI_RE.sub("", text)
    if text != stripped:
        _logger.debug("strip_ansi: removed %d chars from %d to %d",
                      len(text) - len(stripped), len(text), len(stripped))
    return stripped
