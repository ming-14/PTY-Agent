"""session/encoding/codec.py 单元测试"""

import locale

import pytest

from src.encoding.codec import (
    _utf8_trim_tail,
    _gbk_trim_tail,
    decode_strip_tail,
    detect_decode_ext,
    auto_detect,
    check_encoding_ok,
    detect_decode,
)

# 自动探测仅回退到系统默认编码：GBK 系 locale（Windows cp936）才有 GBK 回退
_is_gbk_locale = "gbk" in locale.getpreferredencoding().lower() or \
    "cp936" in locale.getpreferredencoding().lower()


class TestUtf8TrimTail:
    def test_empty(self):
        assert _utf8_trim_tail(b"") == b""

    def test_ascii_only(self):
        assert _utf8_trim_tail(b"hello") == b"hello"

    def test_complete_utf8(self):
        data = "你好".encode("utf-8")
        assert _utf8_trim_tail(data) == data

    def test_incomplete_two_byte(self):
        data = "你".encode("utf-8")
        incomplete = data[:1]
        result = _utf8_trim_tail(incomplete)
        assert len(result) == 0

    def test_incomplete_three_byte(self):
        data = "你".encode("utf-8")
        incomplete = data[:2]
        result = _utf8_trim_tail(incomplete)
        assert len(result) == 0

    def test_complete_then_incomplete(self):
        complete = "你".encode("utf-8")
        incomplete = "好".encode("utf-8")[:1]
        data = complete + incomplete
        result = _utf8_trim_tail(data)
        assert result == complete


class TestGbkTrimTail:
    def test_empty(self):
        assert _gbk_trim_tail(b"") == b""

    def test_ascii_only(self):
        assert _gbk_trim_tail(b"hello") == b"hello"

    def test_complete_gbk(self):
        data = "你好".encode("gbk")
        result = _gbk_trim_tail(data)
        assert len(result) > 0
        assert result == data or len(result) >= len(data) - 1

    def test_incomplete_gbk(self):
        data = "你".encode("gbk")
        incomplete = data[:1]
        result = _gbk_trim_tail(incomplete)
        assert len(result) == 0


class TestDecodeStripTail:
    def test_empty_data(self):
        assert decode_strip_tail(b"", "utf-8") == ""

    def test_valid_utf8(self):
        data = "Hello World".encode("utf-8")
        assert decode_strip_tail(data, "utf-8") == "Hello World"

    def test_valid_gbk(self):
        data = "你好".encode("gbk")
        assert decode_strip_tail(data, "gbk") == "你好"

    def test_incomplete_utf8_tail(self):
        data = "Hello".encode("utf-8") + b"\xe4"
        result = decode_strip_tail(data, "utf-8")
        assert "Hello" in result


class TestDetectDecodeExt:
    def test_empty(self):
        text, enc = detect_decode_ext(b"")
        assert text == ""
        assert enc is None

    def test_utf8(self):
        data = "Hello".encode("utf-8")
        text, enc = detect_decode_ext(data)
        assert text == "Hello"
        assert enc == "utf-8"

    def test_explicit_encoding(self):
        data = "Hello".encode("utf-8")
        text, enc = detect_decode_ext(data, encoding="utf-8")
        assert text == "Hello"
        assert enc == "utf-8"


class TestAutoDetect:
    def test_utf8(self):
        data = "Hello World".encode("utf-8")
        text, enc = auto_detect(data)
        assert text == "Hello World"
        assert enc == "utf-8"

    @pytest.mark.skipif(not _is_gbk_locale,
                    reason="系统编码非 GBK 类（自动探测无 GBK 回退语义）")
    def test_gbk(self):
        data = "你好世界".encode("gbk")
        text, enc = auto_detect(data)
        assert "你好" in text or enc == "gbk"


class TestCheckEncodingOk:
    def test_empty(self):
        assert check_encoding_ok("") is True

    def test_good_text(self):
        assert check_encoding_ok("Hello World") is True

    def test_bad_text(self):
        assert check_encoding_ok("\ufffd" * 100) is False


class TestDetectDecode:
    def test_empty(self):
        assert detect_decode(b"") == ""

    def test_utf8(self):
        data = "Hello".encode("utf-8")
        assert detect_decode(data) == "Hello"
