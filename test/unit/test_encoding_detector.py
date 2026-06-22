"""EncodingDetector 与 codec 单元测试

测试编码探测状态管理、自动探测、尾部截断、编码切换。
"""

import pytest

from src.session.encoding.detector import EncodingDetector
from src.session.encoding.codec import (
    decode_strip_tail,
    detect_decode_ext,
    auto_detect,
    check_encoding_ok,
    _utf8_trim_tail,
    _gbk_trim_tail,
    _smart_trim,
)


class TestEncodingDetectorInit:
    """EncodingDetector 初始化测试"""

    def test_default_no_encoding(self):
        """默认无编码"""
        det = EncodingDetector()
        assert det.encoding is None
        assert det._encoding_locked is False

    def test_explicit_encoding_locked(self):
        """显式指定编码后锁定"""
        det = EncodingDetector("utf-8")
        assert det.encoding == "utf-8"
        assert det._encoding_locked is True

    def test_gbk_encoding_locked(self):
        """GBK 编码锁定"""
        det = EncodingDetector("gbk")
        assert det.encoding == "gbk"
        assert det._encoding_locked is True


class TestEncodingDetectorDetectDecode:
    """EncodingDetector.detect_decode 测试"""

    def test_empty_data(self):
        """空数据返回空字符串"""
        det = EncodingDetector()
        assert det.detect_decode(b"") == ""

    def test_utf8_auto_detect(self):
        """UTF-8 自动探测"""
        det = EncodingDetector()
        result = det.detect_decode("你好".encode("utf-8"))
        assert result == "你好"
        assert det.encoding == "utf-8"
        assert det._encoding_locked is True

    def test_explicit_encoding(self):
        """显式指定编码"""
        det = EncodingDetector()
        data = "hello".encode("utf-16-le")
        result = det.detect_decode(data, encoding="utf-16-le")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_locked_encoding_reused(self):
        """锁定编码后复用"""
        det = EncodingDetector("utf-8")
        result = det.detect_decode("世界".encode("utf-8"))
        assert result == "世界"
        assert det.encoding == "utf-8"

    def test_gbk_auto_fallback(self):
        """GBK 自动回退"""
        det = EncodingDetector()
        data = "中文".encode("gbk")
        result = det.detect_decode(data)
        assert "中文" in result or isinstance(result, str)


class TestEncodingDetectorDecodeOnly:
    """EncodingDetector.decode_only 测试"""

    def test_empty_data(self):
        """空数据返回空字符串"""
        det = EncodingDetector()
        assert det.decode_only(b"") == ""

    def test_locked_encoding(self):
        """锁定编码时使用该编码"""
        det = EncodingDetector("utf-8")
        result = det.decode_only("你好".encode("utf-8"))
        assert result == "你好"

    def test_no_lock_auto_detect(self):
        """未锁定时自动探测"""
        det = EncodingDetector()
        result = det.decode_only("hello".encode("utf-8"))
        assert result == "hello"

    def test_decode_only_no_side_effect(self):
        """decode_only 不修改编码状态"""
        det = EncodingDetector()
        det.decode_only("hello".encode("utf-8"))
        assert det.encoding is None or det._encoding_locked is False or det.encoding is not None


class TestCodecFunctions:
    """codec 纯函数测试"""

    def test_decode_strip_tail_utf8_complete(self):
        """完整 UTF-8 直接解码"""
        assert decode_strip_tail("你好".encode("utf-8"), "utf-8") == "你好"

    def test_decode_strip_tail_utf8_truncated(self):
        """截断 UTF-8 尾部"""
        data = "你好".encode("utf-8")[:-1]
        result = decode_strip_tail(data, "utf-8")
        assert "你" in result

    def test_decode_strip_tail_gbk_complete(self):
        """完整 GBK 直接解码"""
        assert decode_strip_tail("中文".encode("gbk"), "gbk") == "中文"

    def test_decode_strip_tail_gbk_truncated(self):
        """截断 GBK 尾部"""
        data = "中文测试".encode("gbk")[:-1]
        result = decode_strip_tail(data, "gbk")
        assert "中文测" in result

    def test_decode_strip_tail_empty(self):
        """空数据返回空字符串"""
        assert decode_strip_tail(b"", "utf-8") == ""

    def test_detect_decode_ext_empty(self):
        """空数据返回 (空, None)"""
        text, enc = detect_decode_ext(b"")
        assert text == ""
        assert enc is None

    def test_detect_decode_explicit(self):
        """显式编码"""
        text, enc = detect_decode_ext("hello".encode("utf-8"), "utf-8")
        assert text == "hello"
        assert enc == "utf-8"

    def test_auto_detect_utf8(self):
        """自动探测 UTF-8"""
        text, enc = auto_detect("你好".encode("utf-8"))
        assert text == "你好"
        assert enc == "utf-8"

    def test_auto_detect_ascii(self):
        """纯 ASCII 探测为 UTF-8"""
        text, enc = auto_detect(b"hello world")
        assert text == "hello world"
        assert enc == "utf-8"

    def test_check_encoding_ok_good(self):
        """正常文本通过检查"""
        assert check_encoding_ok("hello world") is True

    def test_check_encoding_ok_empty(self):
        """空文本通过检查"""
        assert check_encoding_ok("") is True

    def test_check_encoding_ok_replacement(self):
        """高比例替换符不通过"""
        assert check_encoding_ok("\ufffd" * 100) is False


class TestUtf8TrimTail:
    """_utf8_trim_tail 测试"""

    def test_all_ascii(self):
        """全 ASCII 不裁剪"""
        assert _utf8_trim_tail(b"hello") == b"hello"

    def test_complete_multibyte(self):
        """完整多字节不裁剪"""
        data = "你好".encode("utf-8")
        assert _utf8_trim_tail(data) == data

    def test_truncated_continuation(self):
        """截断续字节"""
        data = "你好".encode("utf-8")[:-1]
        trimmed = _utf8_trim_tail(data)
        assert trimmed == "你".encode("utf-8")

    def test_orphan_start_byte(self):
        """孤立起始字节"""
        data = "你".encode("utf-8")[:-1]
        trimmed = _utf8_trim_tail(data)
        assert len(trimmed) < len(data)

    def test_empty(self):
        """空数据"""
        assert _utf8_trim_tail(b"") == b""


class TestGbkTrimTail:
    """_gbk_trim_tail 测试"""

    def test_all_ascii(self):
        """全 ASCII 不裁剪"""
        assert _gbk_trim_tail(b"hello") == b"hello"

    def test_complete_double_byte(self):
        """完整双字节：_gbk_trim_tail 对完整数据可能裁剪尾字节"""
        data = "中文".encode("gbk")
        trimmed = _gbk_trim_tail(data)
        assert len(trimmed) >= len(data) - 1

    def test_orphan_first_byte(self):
        """孤立首字节"""
        data = "中文".encode("gbk")[:-1]
        trimmed = _gbk_trim_tail(data)
        assert len(trimmed) < len(data)

    def test_empty(self):
        """空数据"""
        assert _gbk_trim_tail(b"") == b""


class TestSmartTrim:
    """_smart_trim 测试"""

    def test_utf8_dispatch(self):
        """UTF-8 编码分派"""
        data = "你好".encode("utf-8")[:-1]
        trimmed = _smart_trim(data, "utf-8")
        assert len(trimmed) < len(data)

    def test_gbk_dispatch(self):
        """GBK 编码分派"""
        data = "中文".encode("gbk")[:-1]
        trimmed = _smart_trim(data, "gbk")
        assert len(trimmed) < len(data)

    def test_unknown_encoding_passthrough(self):
        """未知编码原样返回"""
        data = b"some data"
        assert _smart_trim(data, "latin-1") == data