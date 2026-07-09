"""协议层单元测试 — message 模块"""

import sys
import os
import socket
import threading
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


def test_send_recv():
    """send + recv 往返"""

    def server(sock):
        conn, _ = sock.accept()
        msg = Message.recv(conn)
        assert msg == {"type": "ping"}
        Message.send(conn, {"type": "pong", "echo": msg})
        conn.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    t = threading.Thread(target=server, args=(srv,), daemon=True)
    t.start()

    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect(("127.0.0.1", port))
    Message.send(cli, {"type": "ping"})
    resp = Message.recv(cli)
    assert resp == {"type": "pong", "echo": {"type": "ping"}}
    cli.close()
    srv.close()


def test_recv_large_message():
    """接收大消息"""

    big_obj = {"type": "result", "output": "x" * 10000}
    data = Message.encode(big_obj)

    def server(sock):
        conn, _ = sock.accept()
        conn.sendall(data)
        conn.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    t = threading.Thread(target=server, args=(srv,), daemon=True)
    t.start()

    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect(("127.0.0.1", port))
    resp = Message.recv(cli)
    assert resp is not None
    assert len(resp["output"]) == 10000
    cli.close()
    srv.close()


def test_recv_multiple_messages():
    """接收多条消息"""

    def server(sock):
        conn, _ = sock.accept()
        for i in range(3):
            Message.send(conn, {"type": "msg", "seq": i})
        conn.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    t = threading.Thread(target=server, args=(srv,), daemon=True)
    t.start()

    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect(("127.0.0.1", port))
    for i in range(3):
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["seq"] == i
    cli.close()
    srv.close()


def test_recv_empty():
    """连接关闭时返回 None"""

    def server(sock):
        conn, _ = sock.accept()
        conn.close()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    t = threading.Thread(target=server, args=(srv,), daemon=True)
    t.start()

    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect(("127.0.0.1", port))
    resp = Message.recv(cli)
    assert resp is None
    cli.close()
    srv.close()


def run_all():
    """运行所有测试"""
    tests = [
        ("编码/解码",            test_encode_decode),
        ("Unicode 编码",         test_encode_unicode),
        ("send/recv 往返",       test_send_recv),
        ("大消息接收",            test_recv_large_message),
        ("多条消息接收",          test_recv_multiple_messages),
        ("连接关闭返回 None",     test_recv_empty),
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
