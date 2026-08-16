import locale
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.encoding import detect_decode, decode_strip_tail

# 自动探测仅回退到系统默认编码（Windows cp936 / 其他 GBK 系 locale）：
# 系统编码为 UTF-8 的环境（现代 Linux）无 GBK 回退语义，无法自动识别
_is_gbk_locale = "gbk" in locale.getpreferredencoding().lower() or "cp936" in \
    locale.getpreferredencoding().lower()


def test_decode_empty():
    """空数据返回空字符串"""
    assert detect_decode(b"") == ""


def test_decode_utf8():
    """UTF-8 正常解码"""
    result = detect_decode("你好".encode("utf-8"))
    assert result == "你好"


def test_decode_utf8_strip_tail():
    """UTF-8 末尾截断：丢失最后一个多字节字符"""
    data = "你好".encode("utf-8")[:-1]  # 6B -> 5B, 末尾残缺
    result = decode_strip_tail(data, "utf-8")
    # "你好"=6字节UTF-8, 去掉1字节后只剩"你"(3字节)可完整解码
    assert result == "你", f"got {result!r}"


@pytest.mark.skipif(not _is_gbk_locale,
                    reason="系统编码非 GBK 类（自动探测无 GBK 回退语义）")
def test_decode_gbk():
    """GBK 解码（系统编码回退语义，仅 GBK 系 locale 生效）"""
    data = "中文".encode("gbk")
    result = detect_decode(data)
    assert result == "中文"


def test_decode_gbk_strip_tail():
    """GBK 末尾截断：丢失最后一个多字节字符"""
    data = "中文测试".encode("gbk")[:-1]  # 8B -> 7B, 末尾残缺
    result = decode_strip_tail(data, "gbk")
    # "中文测试"=8字节GBK, 去掉1字节后"中文测"(6B)可完整解码
    assert result == "中文测", f"got {result!r}"


def test_decode_invalid_tail():
    """无效尾部的安全处理"""
    data = b"hello\xff\xfe"
    result = detect_decode(data)
    assert isinstance(result, str)
    assert len(result) > 0


def test_decode_utf8_ascii():
    """纯 ASCII"""
    result = detect_decode(b"Hello, World!\n")
    assert result == "Hello, World!\n"


def test_decode_utf8_strip_cycle():
    """连续截断不超过最大次数"""
    data = b"\xe4\xb8\xad\xe6\x96\x87\xff"
    result = decode_strip_tail(data, "utf-8")
    assert isinstance(result, str)


def test_decode_specified_encoding():
    """指定编码"""
    result = detect_decode("hello".encode("utf-16-le"), encoding="utf-16-le")
    assert result == "hello"


def run_all():
    """运行所有测试"""
    tests = [
        ("空数据",                  test_decode_empty),
        ("UTF-8 解码",              test_decode_utf8),
        ("UTF-8 截断",              test_decode_utf8_strip_tail),
        ("GBK 解码",                test_decode_gbk),
        ("GBK 截断",                test_decode_gbk_strip_tail),
        ("无效尾部",                test_decode_invalid_tail),
        ("纯 ASCII",                test_decode_utf8_ascii),
        ("连续截断上限",             test_decode_utf8_strip_cycle),
        ("指定编码",                test_decode_specified_encoding),
    ]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  [PASS] {name}")
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:
            print(f"  [FAIL] {name}: 异常 {e}")
    total = len(tests)
    print(f"\n结果: {passed}/{total} 通过")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
