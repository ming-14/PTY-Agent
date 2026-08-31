"""UTF-8 解码与尾部裁剪

终端输出统一按 UTF-8 解码，不再做编码自动探测。
PTY 管道读取可能跨读周期拆分 UTF-8 多字节字符，导致末尾出现不完整的
字节序列，通过智能裁剪处理。
"""

import logging

_logger = logging.getLogger("pty-session")


def _utf8_trim_tail(data: bytes) -> bytes:
    """智能裁剪 UTF-8 末尾不完整字节序列

    直接根据 UTF-8 编码规则判定末尾缺少的续字节数量，
    避免逐字节解码重试。

    Args:
        data: UTF-8 字节数据。

    Returns:
        裁剪不完整尾部的字节数据。
    """
    if not data:
        return data
    i = len(data) - 1
    # 跳过尾部 ASCII 字节（0x00-0x7F）
    while i >= 0 and data[i] < 0x80:
        i -= 1
    if i < 0:
        return data  # 全 ASCII，无不完整字符
    b = data[i]
    if 0x80 <= b <= 0xBF:
        # 续字节结尾：找前面的起始字节
        start = i
        while start >= 0 and 0x80 <= data[start] <= 0xBF:
            start -= 1
        if start < 0 or data[start] < 0xC0:
            return data[:start] if start >= 0 else b""
        b = data[start]
        expected = 1 if b < 0xE0 else (2 if b < 0xF0 else 3)
        have = i - start
        if have < expected:
            return data[:start]
        return data[:i + 1]
    elif b >= 0xC0:
        # 孤立起始字节
        expected = 1 if b < 0xE0 else (2 if b < 0xF0 else 3)
        have = len(data) - i - 1
        if have < expected:
            return data[:i]
    return data


def decode_utf8(data: bytes) -> str:
    """按 UTF-8 解码字节数据，自动移除末尾不完整的多字节序列

    多字节编码（UTF-8）的字符可能被管道读取跨周期拆分，
    导致末尾出现孤立的首字节或缺失续字节。

    Args:
        data: 待解码的原始字节数据。

    Returns:
        解码后的字符串。空数据返回空字符串。
    """
    if not data:
        return ""
    # 快速路径：严格解码成功直接返回
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # 智能裁剪：根据 UTF-8 规则直接定位不完整尾部
    trimmed = _utf8_trim_tail(data)
    if trimmed != data:
        try:
            return trimmed.decode("utf-8")
        except UnicodeDecodeError:
            pass
    # 兜底：替换无效字节
    return data.decode("utf-8", errors="replace")
