"""UTF-8 统一解码单元测试 — encoding"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.session.encoding import decode_utf8


def test_decode_empty():
    """空数据返回空字符串"""
    assert decode_utf8(b"") == ""


def test_decode_utf8():
    """UTF-8 正常解码"""
    result = decode_utf8("你好".encode("utf-8"))
    assert result == "你好"


def test_decode_utf8_strip_tail():
    """UTF-8 末尾截断：丢失最后一个多字节字符"""
    data = "你好".encode("utf-8")[:-1]  # 6B -> 5B, 末尾残缺
    result = decode_utf8(data)
    # "你好"=6字节UTF-8, 去掉1字节后只剩"你"(3字节)可完整解码
    assert result == "你", f"got {result!r}"


def test_decode_ascii():
    """纯 ASCII"""
    result = decode_utf8(b"Hello, World!\n")
    assert result == "Hello, World!\n"


def test_decode_invalid_tail():
    """无效尾部使用替换符安全处理"""
    data = b"hello\xff\xfe"
    result = decode_utf8(data)
    assert isinstance(result, str)
    assert len(result) > 0


def run_all():
    """运行所有测试"""
    tests = [
        ("空数据",                  test_decode_empty),
        ("UTF-8 解码",              test_decode_utf8),
        ("UTF-8 截断",              test_decode_utf8_strip_tail),
        ("纯 ASCII",                test_decode_ascii),
        ("无效尾部",                test_decode_invalid_tail),
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
