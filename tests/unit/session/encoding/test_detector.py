"""session/encoding/detector.py 单元测试"""

import pytest

from src.session.detector import EncodingDetector


class TestEncodingDetectorInit:
    def test_default_encoding(self):
        det = EncodingDetector()
        assert det.encoding is None
        assert det._encoding_locked is False

    def test_explicit_encoding(self):
        det = EncodingDetector("utf-8")
        assert det.encoding == "utf-8"
        assert det._encoding_locked is True

    def test_gbk_encoding(self):
        det = EncodingDetector("gbk")
        assert det.encoding == "gbk"
        assert det._encoding_locked is True


class TestEncodingDetectorDetectDecode:
    def test_empty_data(self):
        det = EncodingDetector()
        assert det.detect_decode(b"") == ""

    def test_utf8_auto_detect(self):
        det = EncodingDetector()
        result = det.detect_decode("Hello World".encode("utf-8"))
        assert result == "Hello World"
        assert det.encoding == "utf-8"
        assert det._encoding_locked is True

    def test_explicit_encoding(self):
        det = EncodingDetector()
        result = det.detect_decode("Hello".encode("utf-8"), encoding="utf-8")
        assert result == "Hello"

    def test_locked_encoding(self):
        det = EncodingDetector("utf-8")
        result = det.detect_decode("Hello".encode("utf-8"))
        assert result == "Hello"

    def test_auto_switch_to_utf8(self):
        det = EncodingDetector("gbk")
        result = det.detect_decode("Hello World".encode("utf-8"))
        assert "Hello" in result


class TestEncodingDetectorDecodeOnly:
    def test_empty_data(self):
        det = EncodingDetector()
        assert det.decode_only(b"") == ""

    def test_utf8(self):
        det = EncodingDetector("utf-8")
        result = det.decode_only("Hello".encode("utf-8"))
        assert result == "Hello"

    def test_no_side_effects(self):
        det = EncodingDetector()
        original_encoding = det.encoding
        det.decode_only("Hello".encode("utf-8"))
        assert det.encoding == original_encoding

    def test_locked_encoding_decode(self):
        det = EncodingDetector("gbk")
        data = "你好".encode("gbk")
        result = det.decode_only(data)
        assert "你好" in result
