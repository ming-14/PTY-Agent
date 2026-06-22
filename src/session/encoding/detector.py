"""编码探测与解码状态管理

管理终端输出的自动编码探测状态（encoding / _encoding_locked），
提供两种解码方法：
- detect_decode  修改 self.encoding（用于 get_output）
- decode_only    无副作用（用于持锁路径，如 TriggerMatcher.check）
"""

import logging
from typing import Optional

from .codec import (
    detect_decode,
    decode_strip_tail,
    detect_decode_ext,
    check_encoding_ok,
)

_logger = logging.getLogger("pty-session")


class EncodingDetector:
    """编码探测与解码状态管理

    维护当前编码及锁定状态，提供两种解码入口：
    - detect_decode:  可修改 self.encoding，在 get_output 中调用（无锁）
    - decode_only:    无副作用，在持锁路径（TriggerMatcher.check）中使用

    Attributes:
        encoding:          当前探测到的编码（None 表示尚未探测）。
        _encoding_locked:  编码是否已锁定（手动指定或探测成功后锁定）。
    """

    def __init__(self, encoding: Optional[str] = None):
        self.encoding = encoding
        self._encoding_locked = encoding is not None
        _logger.debug(
            "EncodingDetector init: encoding=%s locked=%s",
            encoding, self._encoding_locked,
        )

    # ── 主解码入口（可修改 self.encoding）──────────────────────

    def detect_decode(self, data: bytes,
                      encoding: Optional[str] = None) -> str:
        """探测编码并解码（可修改 self.encoding）

        在 get_output 中调用，无持锁要求。

        Args:
            data:     待解码的原始字节数据。
            encoding: 显式指定解码编码；None 表示使用已锁定编码或自动探测。

        Returns:
            解码后的文本字符串。
        """
        if not data:
            return ""

        # ── 显式指定编码 ──
        if encoding:
            enc_norm = encoding.lower().replace("-", "")
            if enc_norm != "utf8":
                tail = data[-1024:] if len(data) > 1024 else data
                try:
                    tail.decode("utf-8")
                    _logger.info(
                        "编码切换(显式): %s → utf-8 (尾部纯 UTF-8)", encoding)
                    self.encoding = "utf-8"
                    self._encoding_locked = True
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            text = decode_strip_tail(data, encoding)
            if check_encoding_ok(text):
                return text
            _logger.info(
                "编码回退: 显式编码 %s 不可用，回退自动探测", encoding)

        # ── 已锁定编码 ──
        if self._encoding_locked:
            enc_norm = self.encoding.lower().replace("-", "")
            if enc_norm != "utf8":
                tail = data[-1024:] if len(data) > 1024 else data
                try:
                    tail.decode("utf-8")
                    _logger.info(
                        "编码切换: %s → utf-8 (尾部纯 UTF-8)", self.encoding)
                    self.encoding = "utf-8"
                    self._encoding_locked = True
                except UnicodeDecodeError:
                    pass
            text = decode_strip_tail(data, self.encoding)
            if check_encoding_ok(text):
                return text
            _logger.info(
                "编码重探测: 锁定编码 %s 产生替换符，重新探测", self.encoding)

        # ── 自动探测 ──
        result, detected_enc = detect_decode_ext(data)
        self.encoding = detected_enc or "utf-8"
        self._encoding_locked = True
        return result

    # ── 无副作用解码（持锁路径使用）─────────────────────────────

    def decode_only(self, data: bytes) -> str:
        """仅解码，不修改 self.encoding（无副作用）

        在持锁路径（TriggerMatcher.check）中使用，避免并发写入编码状态。

        Args:
            data: 待解码的原始字节数据。

        Returns:
            解码后的文本字符串。
        """
        if not data:
            return ""
        if self._encoding_locked and self.encoding:
            enc_norm = self.encoding.lower().replace("-", "")
            if enc_norm != "utf8":
                tail = data[-256:] if len(data) > 256 else data
                try:
                    tail.decode("utf-8")
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            text = decode_strip_tail(data, self.encoding)
            if check_encoding_ok(text):
                return text
        return detect_decode(data)
