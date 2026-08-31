"""UTF-8 解码模块单元测试

测试 decode_utf8 和 _utf8_trim_tail 纯函数。
"""

import pytest

from src.session.encoding.codec import decode_utf8, _utf8_trim_tail


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


class TestDecodeUtf8:
    """decode_utf8 测试"""

    def test_empty(self):
        """空数据返回空字符串"""
        assert decode_utf8(b"") == ""

    def test_ascii(self):
        """纯 ASCII"""
        assert decode_utf8(b"Hello, World!\n") == "Hello, World!\n"

    def test_utf8(self):
        """UTF-8 正常解码"""
        assert decode_utf8("你好".encode("utf-8")) == "你好"

    def test_truncated_tail(self):
        """末尾截断：自动裁剪不完整尾部"""
        data = "你好".encode("utf-8")[:-1]
        result = decode_utf8(data)
        assert result == "你"

    def test_invalid_tail_replacement(self):
        """无效尾部用替换符替代"""
        data = b"hello\xff\xfe"
        result = decode_utf8(data)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "\ufffd" in result

    def test_long_utf8(self):
        """长文本 UTF-8 解码"""
        text = "你好世界" * 100
        data = text.encode("utf-8")
        assert decode_utf8(data) == text

    def test_single_byte_truncated(self):
        """单个字节且不完整"""
        data = b"\xe4"
        result = decode_utf8(data)
        assert isinstance(result, str)
        # 可能是空字符串或替换符
        assert len(result) <= 1