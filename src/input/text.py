"""输入文本处理 — JSON 转义解码、控制字符展开与行尾追加

供 CLI send（client）与 workflow send 步骤（daemon）共用同一输入语义：
默认追加 \\r 模拟终端 Enter，-j/{enter} 等转义在 json 展开模式下可用。
"""

from ..logging import get_logger
import json
from typing import Optional

_logger = get_logger("pty-client")


def unescape_json_string(text: str) -> str:
    """JSON 转义解码（\\n、\\t、\\uXXXX、\\"、\\\\ 等）

    Args:
        text: 可能包含 JSON 转义序列的文本。

    Returns:
        解码后的文本。无转义序列时原样返回。
    """
    if "\\" not in text:
        return text
    original = text
    try:
        text = json.loads(f'"{text}"')
        if text != original:
            _logger.debug("unescape_json_string: %r -> %r", original[:100], text[:100])
    except json.JSONDecodeError:
        pass
    return text


_CONTROL_KEYS = {
    "enter": "\r",
    "return": "\r",
    "tab": "\t",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[1~",
    "end": "\x1b[4~",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "insert": "\x1b[2~",
    "delete": "\x1b[3~",
    "del": "\x1b[3~",
    "esc": "\x1b",
    "escape": "\x1b",
    "backspace": "\x7f",
    "bs": "\x7f",
    "backtab": "\x1b[Z",
    "space": "\x20",
}

_FUNCTION_KEYS = {
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}


def _expand_control_token(body: str) -> Optional[str]:
    r"""把 {body} 中的内容解析为控制字符或 VT 序列。

    支持：
    - 修饰键+字母：{ctrl+a}、{ctrl+alt+s}、{ctrl+alt+shift+s}
    - 特殊键：enter、tab、方向键、home/end、pageup/pagedown、insert、delete、f1~f12
    - alt 修饰会为结果前缀 ESC（如 {alt+a} -> \e+a）

    无法识别时返回 None（由调用方决定如何处理）。
    """
    parts = [p.strip().lower() for p in body.split("+") if p.strip()]
    if not parts:
        return None
    main = parts[-1]
    modifiers = set(parts[:-1])

    def _letter_to_ctrl(ch: str) -> str:
        if "A" <= ch <= "Z":
            return chr(ord(ch) - ord("A") + 1)
        return chr(ord(ch) - ord("a") + 1)

    result: str
    if len(main) == 1 and main.isalpha():
        if not modifiers:
            return None
        ch = main
        if "shift" in modifiers and "ctrl" not in modifiers:
            ch = ch.upper()
        if "ctrl" in modifiers:
            result = _letter_to_ctrl(ch)
        else:
            result = ch
    elif main in _CONTROL_KEYS:
        result = _CONTROL_KEYS[main]
    elif main in _FUNCTION_KEYS:
        result = _FUNCTION_KEYS[main]
    else:
        return None

    if "alt" in modifiers:
        result = "\x1b" + result
    return result


def expand_control_characters(text: str) -> str:
    """控制字符转义展开。

    语法：
    - {ctrl+a}、{ctrl+alt+s}、{ctrl+alt+shift+s} 生成对应 ASCII 控制字符。
    - {enter}、{tab}、{up}、{down}、{left}、{right}、{home}、{end}、
      {pageup}、{pagedown}、{insert}、{delete}、{f1}~{f12} 生成终端 VT 序列。
    - 字面量 `{` 或 `}` 使用反引号转义：`` `{ ``、`` `} ``。

    大小写不敏感。

    Raises:
        ValueError: 遇到无法识别的 {...} 转义序列时抛出，附带修复提示。
    """
    out: list = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "`" and i + 1 < n and text[i + 1] in "{}":
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "{":
            j = i + 1
            while j < n and not (text[j] == "}" and text[j - 1] != "`"):
                j += 1
            if j == n:
                out.append("{")
                i += 1
                continue
            body = text[i + 1 : j]
            expanded = _expand_control_token(body)
            if expanded is None:
                raise ValueError(
                    f"无法识别的转义序列 '{{{body}}}'。"
                    f"-j 模式下 '{' 和 '}' 需使用反引号转义："
                    f"`{'{'}  `{'}'}  "
                    f"（例：a`{'{'}b`{'}'}c → a{{b}}c）。"
                    f"支持的转义：{{ctrl+a}}, {{alt+x}}, {{enter}}, {{tab}}, {{up}}, {{f1}} 等。"
                )
            out.append(expanded)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def process_input(text: str, json_escaping: bool = False, send_eol: str = "\r") -> str:
    """处理输入文本：可选 JSON/控制字符转义解码 + 自动追加行尾符

     默认 raw 模式（json_escaping=False）：原样发送，不做任何转义处理。
     Windows 路径中的反斜杠不会被误转换。

     启用 json_escaping 时：先使用完整 JSON 反转移处理所有标准转义序列
    （\\n、\\t、\\r、\\uXXXX、\\"、\\\\ 等），再展开控制字符转义
     （{ctrl+a}、{enter}、{up}、{f1} 等），适用于需要发送多行代码或按键场景。

     Args:
         text: 原始输入文本。
         json_escaping: 是否启用 JSON/控制字符转义解码（默认 False，raw 模式）。
         send_eol: 末尾追加的行尾符。默认 "\\r"（模拟终端 Enter）。可选 "\\n"、"\\r\\n"、"\\r"、""（不追加）。
                   当输入已以 \\n 或 \\r 结尾时不重复追加。

     Returns:
          处理后的文本。
    """
    if json_escaping:
        text = unescape_json_string(text)
        text = expand_control_characters(text)
    if send_eol:
        if not text.endswith("\n") and not text.endswith("\r"):
            text += send_eol
    _logger.debug(
        "process_input: len=%d json_escaping=%s send_eol=%r ends_with_newline=%s",
        len(text),
        json_escaping,
        send_eol,
        text.endswith("\n"),
    )
    return text