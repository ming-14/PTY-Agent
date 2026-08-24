"""编码探测与解码状态管理

管理终端输出的自动编码探测状态（encoding / _encoding_locked），
提供两种解码方法：
- detect_decode  修改 self.encoding（用于 get_output）
- decode_only    无副作用（用于持锁路径，如 TriggerMatcher.check）
"""

from typing import Optional

from .codec import (
    check_encoding_ok,
    decode_strip_tail,
    decode_strip_tail_len,
    detect_decode,
    detect_decode_ext,
)
from ..logging import get_logger

_logger = get_logger("pty-session")


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
            encoding,
            self._encoding_locked,
        )

    # ── 主解码入口（可修改 self.encoding）──────────────────────

    def detect_decode(self, data: bytes, encoding: Optional[str] = None) -> str:
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
            text = decode_strip_tail(data, encoding)
            if check_encoding_ok(text):
                self.encoding = encoding
                self._encoding_locked = True
                return text
            _logger.info("编码回退: 显式编码 %s 不可用，回退自动探测", encoding)

        # ── 已锁定编码 ──
        if self._encoding_locked and self.encoding:
            text = decode_strip_tail(data, self.encoding)
            if check_encoding_ok(text):
                return text
            _logger.info("编码重探测: 锁定编码 %s 产生替换符，重新探测", self.encoding)

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
        text, _ = self.decode_only_len(data)
        return text

    def decode_only_len(self, data: bytes) -> tuple:
        """仅解码并返回 (文本, 被消费的字节长度)（无副作用，持锁路径使用）

        与 decode_only 语义一致，额外返回文本对应的完整字节前缀长度，
        供 TriggerMatcher 滚动解码缓存跟踪跨块拆分的残缺尾部字节。

        Args:
            data: 待解码的原始字节数据。

        Returns:
            (解码后的文本, 被消费的字节长度)。
        """
        if not data:
            return "", 0
        if self._encoding_locked and self.encoding:
            text, consumed = decode_strip_tail_len(data, self.encoding)
            if check_encoding_ok(text):
                return text, consumed
        # 自动探测路径按全部字节消费（首块无历史，无跨块边界问题）
        return detect_decode(data), len(data)
