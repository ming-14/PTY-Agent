"""协议层单元测试 — message 模块（编解码部分）

通信已改为共享内存，message 仅保留 encode/decode。
"""

import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.protocol.message import Message


def test_encode_decode():
    """编码与解码"""
    obj = {"type": "ping", "id": "test"}
    data = Message.encode(obj)
    assert isinstance(data, bytes)
    assert data.endswith(b"\n")
    decoded = Message.decode(data)
    assert decoded == obj


def test_encode_unicode():
    """Unicode 文本编码"""
    obj = {"output": "你好, 世界! 🔥"}
    data = Message.encode(obj)
    decoded = Message.decode(data)
    assert decoded["output"] == "你好, 世界! 🔥"


def test_decode_invalid_json():
    """无效 JSON 返回 None"""
    result = Message.decode(b"not json\n")
    assert result is None


def test_decode_empty():
    """空数据返回 None"""
    result = Message.decode(b"")
    # 空字符串 '' 不是有效 JSON，decode 返回 None
    assert result is None


def test_decode_large():
    """大消息解码"""
    big_obj = {"type": "result", "output": "x" * 100000}
    data = Message.encode(big_obj)
    decoded = Message.decode(data)
    assert decoded["type"] == "result"
    assert len(decoded["output"]) == 100000


def run_all():
    """运行所有测试"""
    tests = [
        ("编码/解码",           test_encode_decode),
        ("Unicode 编码",        test_encode_unicode),
        ("无效 JSON 返回 None", test_decode_invalid_json),
        ("空数据返回 None",      test_decode_empty),
        ("大消息解码",           test_decode_large),
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
            import traceback
            traceback.print_exc()
    total = len(tests)
    print(f"\n结果: {passed}/{total} 通过")
    return passed == total


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)