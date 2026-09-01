"""输入文本处理与安全输出

提供输入文本的处理（自动追加换行）和安全打印（适配控制台编码）功能。
"""

import logging
import sys

_logger = logging.getLogger("pty-client")


def process_input(text: str) -> str:
    """处理输入文本：原样发送 + 自动追加换行

    Windows 路径中的反斜杠（如 C:\\Users\\username\\new_folder）不会被误转换。

    Args:
        text: 原始输入文本。

    Returns:
        处理后的文本，末尾始终有换行符。
    """
    if not text.endswith("\n") and not text.endswith("\r"):
        text += "\n"
    _logger.debug("process_input: len=%d ends_with_newline=%s",
                  len(text), text.endswith("\n"))
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
