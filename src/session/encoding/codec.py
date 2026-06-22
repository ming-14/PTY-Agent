"""编码自动探测与解码

提供终端输出编码的自动探测和尾部截断解码功能。
Session 类内部不再直接实现这些逻辑，通过本模块的纯函数完成。
"""

import logging
import locale
from typing import Optional

_logger = logging.getLogger("pty-session")

_MAX_STRIP_TRIES = 20  # 从 100 降到 20，配合智能截断


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


def _gbk_trim_tail(data: bytes) -> bytes:
    """智能裁剪 GBK 末尾不完整字节序列"""
    if not data:
        return data
    i = len(data) - 1
    while i >= 0 and data[i] < 0x80:
        i -= 1
    if i < 0:
        return data
    b = data[i]
    if 0x81 <= b <= 0xFE:
        # 孤立的 GBK 首字节
        return data[:i]
    if 0x40 <= b <= 0xFE and i > 0 and 0x81 <= data[i - 1] <= 0xFE:
        return data  # 完整双字节
    if 0x40 <= b <= 0xFE:
        return data[:i]  # 孤立的尾字节
    return data


def _smart_trim(data: bytes, encoding: str) -> bytes:
    """根据编码智能裁剪末尾不完整序列"""
    enc = encoding.lower().replace("-", "").replace("_", "")
    if enc in ("utf8", "utf"):
        return _utf8_trim_tail(data)
    if enc in ("gbk", "gb2312", "gb18030", "cp936"):
        return _gbk_trim_tail(data)
    return data


def decode_strip_tail(data: bytes, encoding: str) -> str:
    """解码字节数据，自动移除末尾不完整的多字节序列

    多字节编码（GBK、UTF-8 等）的字符可能被管道读取跨周期拆分，
    导致末尾出现孤立的首字节。

    性能优化：
    - 快速路径：严格解码成功直接返回
    - 编码规则智能裁剪：根据 UTF-8/GBK 编码规则直接定位不完整尾部
    - 回退路径：仅在前两者失败时才用线性截断

    Args:
        data:     待解码的字节数据。
        encoding: 编码名称（如 'utf-8', 'gbk'）。

    Returns:
        解码后的字符串。
    """
    if not data:
        return ""
    # 快速路径：严格解码成功直接返回
    try:
        decoded = data.decode(encoding)
        _logger.debug("decode_strip_tail: fast path OK len=%d enc=%s", len(data), encoding)
        return decoded
    except UnicodeDecodeError:
        pass

    # 智能裁剪：根据编码规则直接定位不完整序列
    trimmed = _smart_trim(data, encoding)
    if trimmed != data:
        _logger.debug("decode_strip_tail: smart trim removed %d bytes (enc=%s)",
                      len(data) - len(trimmed), encoding)
        try:
            return trimmed.decode(encoding)
        except UnicodeDecodeError:
            pass

    # 回退路径：替换模式扫描尾部替换符
    tries = 0
    while len(data) > 0 and tries < _MAX_STRIP_TRIES:
        result = data.decode(encoding, errors="replace")
        i = len(result) - 1
        while i >= 0 and result[i] in ('\ufffd', '\r', '\n', '\t', ' '):
            if result[i] == '\ufffd':
                break
            i -= 1
        else:
            _logger.debug("decode_strip_tail: fallback path tries=%d len=%d", tries, len(data))
            return result
        data = data[:-1]
        tries += 1
    result = data.decode(encoding, errors="replace") if data else ""
    _logger.debug("decode_strip_tail: fallback end tries=%d len=%d", tries, len(data))
    return result


def detect_decode_ext(
    data: bytes,
    encoding: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """探测编码并解码，返回 (文本, 检测到的编码)

    Args:
        data:     待解码的字节数据。
        encoding: 指定编码。None 表示自动探测。

    Returns:
        (decoded_text, detected_encoding) 元组。
        detected_encoding 为 None 表示无输入数据。
    """
    if not data:
        return "", None
    if encoding:
        return decode_strip_tail(data, encoding), encoding
    return auto_detect(data)


def auto_detect(data: bytes) -> tuple[str, str]:
    """自动探测编码并解码

    优先级：UTF-8 → 系统 locale 编码（如 GBK）。
    仅在 UTF-8 解码产生 >5% 替换符时回退到系统编码。

    Returns:
        (decoded_text, detected_encoding) 元组。
    """
    # 尝试 UTF-8（严格）
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    # UTF-8 宽松解码，检查替换符比例
    decoded = data.decode("utf-8", errors="replace")
    if len(decoded) > 0:
        ratio = decoded.count("\ufffd") / len(decoded)
        if ratio > 0.05:
            sys_enc = locale.getpreferredencoding()
            try:
                return decode_strip_tail(data, sys_enc), sys_enc
            except Exception:
                pass
    return decoded, "utf-8"


def check_encoding_ok(text: str) -> bool:
    """检查解码后的文本替换符占比是否可接受

    Returns:
        True 表示编码正确（替换符占比 < 5%）。
    """
    if not text:
        return True
    return text.count("\ufffd") / max(len(text), 1) < 0.05


def detect_decode(data: bytes, encoding: Optional[str] = None) -> str:
    """探测编码并解码字节数据

    自动探测策略：优先尝试 UTF-8，若替换字符占比 > 5%
    则回退到系统编码（如 Windows GBK）。

    自动处理末尾不完整的多字节序列（PTY 管道跨读周期拆分字符导致）。

    Args:
        data:     待解码的字节数据。
        encoding: 指定编码。None 表示自动探测。

    Returns:
        解码后的字符串。空字节返回空字符串。
    """
    text, _ = detect_decode_ext(data, encoding)
    return text
