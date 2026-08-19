"""安全输出（适配控制台编码）

输入文本处理已下沉到 src/input/text.py（CLI 与 daemon 共用），
本模块仅保留 client 侧控制台打印适配。
"""

import sys

from ..logging import get_logger

_logger = get_logger("pty-client")


def safe_print(text: str, **kwargs):
    """安全打印，自动适配控制台编码

    优先使用原生 print，遇到编码错误时回退到 XML 字符引用或系统编码。
    当 stdout 被重定向且编码不是 UTF-8 时，强制使用 UTF-8 编码字节流写入，
    避免 GBK 终端与实际 UTF-8 管道不匹配。

    Args:
        text:   要打印的文本。
        **kwargs: 传递给 print 的其他参数（如 file=, end=）。
    """
    target = kwargs.get("file", sys.stdout)
    is_tty = hasattr(target, "isatty") and target.isatty()

    # 非 TTY 且编码为 GBK 时，强制 UTF-8 输出
    if not is_tty and hasattr(target, "encoding"):
        enc = getattr(target, "encoding", None)
        if enc and enc.lower() in ("gbk", "cp936", "gb2312"):
            try:
                raw = text.encode("utf-8")
                kwargs.get("file", sys.stdout).buffer.write(raw + b"\n")
                kwargs.get("file", sys.stdout).buffer.flush()
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
