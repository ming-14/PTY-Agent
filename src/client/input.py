"""输入文本处理与安全输出

提供输入文本的转义处理（自动追加换行）和安全打印（适配控制台编码）功能。
"""

import logging
import sys
import json

_logger = logging.getLogger("pty-client")


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


def process_input(text: str, json_escaping: bool = False) -> str:
    """处理输入文本：可选 JSON 转义解码 + 自动追加换行

    默认 raw 模式（json_escaping=False）：原样发送，不做任何转义处理。
    Windows 路径中的反斜杠（如 C:\\Users\\username\\new_folder）不会被误转换。

    启用 json_escaping 时：使用完整 JSON 反转移处理所有标准转义序列
   （\\n、\\t、\\r、\\uXXXX、\\"、\\\\ 等），适用于需要发送多行代码等场景。

    Args:
        text: 原始输入文本。
        json_escaping: 是否启用 JSON 转义解码（默认 False，raw 模式）。

    Returns:
        处理后的文本，末尾始终有换行符。
    """
    if json_escaping:
        text = unescape_json_string(text)
    if not text.endswith("\n") and not text.endswith("\r"):
        text += "\n"
    _logger.debug("process_input: len=%d json_escaping=%s ends_with_newline=%s",
                  len(text), json_escaping, text.endswith("\n"))
    return text


def safe_print(text: str, **kwargs):
    """安全打印，统一 UTF-8 输出

    优先将文本以 UTF-8 字节直接写入输出流，避免控制台/管道编码不匹配
    导致的乱码；无 buffer 或写入失败时回退到原生 print。

    Args:
        text:   要打印的文本。
        **kwargs: 传递给 print 的其他参数（如 file=, end=）。
    """
    target = kwargs.get("file", sys.stdout)
    is_tty = hasattr(target, "isatty") and target.isatty()
    buf = getattr(target, "buffer", None)

    # 非 TTY 且可访问 buffer 时，直接以 UTF-8 字节写入
    if buf is not None and not is_tty:
        try:
            buf.write(text.encode("utf-8") + b"\n")
            buf.flush()
            return
        except Exception:
            pass

    try:
        print(text, **kwargs)
    except UnicodeEncodeError:
        console_enc = sys.stdout.encoding or "utf-8"
        try:
            encoded = text.encode(console_enc, errors="xmlcharrefreplace")
            print(encoded.decode(console_enc, errors="replace"), **kwargs)
        except Exception:
            encoded = text.encode(console_enc, errors="replace")
            print(encoded.decode(console_enc, errors="replace"), **kwargs)
