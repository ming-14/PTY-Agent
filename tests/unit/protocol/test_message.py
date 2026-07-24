"""协议层单元测试 — message 模块"""

import sys
import os
import socket
import threading
import json
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from src.protocol.message import Message
from src.auth.token import HmacMessageSigner


class TestEncode:
    def test_basic_encode(self):
        obj = {"type": "ping", "id": "test"}
        data = Message.encode(obj)
        assert isinstance(data, bytes)
        assert data.endswith(b"\n")

    def test_unicode_encode(self):
        obj = {"output": "你好, 世界! 🔥"}
        data = Message.encode(obj)
        decoded = Message.decode(data)
        assert decoded["output"] == "你好, 世界! 🔥"

    def test_encode_produces_valid_json(self):
        obj = {"type": "result", "data": [1, 2, 3]}
        data = Message.encode(obj)
        text = data.decode("utf-8").strip()
        parsed = json.loads(text)
        assert parsed == obj

    def test_encode_empty_dict(self):
        data = Message.encode({})
        decoded = Message.decode(data)
        assert decoded == {}


class TestDecode:
    def test_basic_decode(self):
        obj = {"type": "ping", "id": "test"}
        data = Message.encode(obj)
        decoded = Message.decode(data)
        assert decoded == obj

    def test_decode_invalid_json(self):
        with pytest.raises(Exception):
            Message.decode(b"not json at all")

    def test_decode_utf8_bytes(self):
        raw = '{"key": "值"}\n'.encode("utf-8")
        decoded = Message.decode(raw)
        assert decoded["key"] == "值"


class TestSendRecv:
    def _make_pair(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        return srv, port

    def test_send_recv_roundtrip(self):
        srv, port = self._make_pair()
        ready = threading.Event()

        def server():
            conn, _ = srv.accept()
            ready.set()
            msg = Message.recv(conn)
            assert msg == {"type": "ping"}
            Message.send(conn, {"type": "pong", "echo": msg})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "ping"})
        resp = Message.recv(cli)
        assert resp == {"type": "pong", "echo": {"type": "ping"}}
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_recv_large_message(self):
        big_obj = {"type": "result", "output": "x" * 10000}
        data = Message.encode(big_obj)

        srv, port = self._make_pair()

        def server():
            conn, _ = srv.accept()
            conn.sendall(data)
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is not None
        assert len(resp["output"]) == 10000
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_recv_multiple_messages(self):
        srv, port = self._make_pair()

        def server():
            conn, _ = srv.accept()
            for i in range(3):
                Message.send(conn, {"type": "msg", "seq": i})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        for i in range(3):
            resp = Message.recv(cli)
            assert resp is not None
            assert resp["seq"] == i
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_recv_empty_connection_returns_none(self):
        srv, port = self._make_pair()

        def server():
            conn, _ = srv.accept()
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is None
        cli.close()
        srv.close()
        t.join(timeout=5)


class TestRecvBuffer:
    def test_buffer_cleanup_on_close(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        fd = cli.fileno()
        Message.recv(cli)
        assert fd not in Message._recv_buffers
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_buffer_entry_after_successful_recv(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            Message.send(conn, {"type": "test"})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        fd = cli.fileno()
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["type"] == "test"
        cli.close()
        srv.close()
        t.join(timeout=5)


class TestRecvTimeout:
    def test_recv_timeout_returns_none(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            time.sleep(5)
            conn.close()

        import time
        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        cli.settimeout(0.1)
        resp = Message.recv(cli, max_retries=1)
        assert resp is None
        cli.close()
        srv.close()
        t.join(timeout=5)


class TestHMAC:
    """HMAC 签名收发测试

    HMAC 对称签名器：出站签 + 入站验，复用同一实例。
    测试中两端共享 Message 类级状态，故同时设出/入站为同一 signer。
    """

    @staticmethod
    def _set_hmac(key: bytes):
        """设置 HMAC 对称签名器（同时设出站签 + 入站验）"""
        signer = HmacMessageSigner(key)
        Message.set_outbound_signer(signer)
        Message.set_inbound_verifier(signer)

    @staticmethod
    def _clear_hmac():
        """清除出/入站签名器"""
        Message.set_outbound_signer(None)
        Message.set_inbound_verifier(None)

    @pytest.fixture(autouse=True)
    def _reset_hmac(self):
        self._clear_hmac()
        yield
        self._clear_hmac()

    def test_hmac_send_recv_roundtrip(self):
        key = b"\x01\x02\x03\x04\x05\x06\x07\x08" * 4
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            self._set_hmac(key)  # 线程局部：server 线程需独立设置签名器
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            assert msg is not None
            assert msg["type"] == "exec"
            assert msg["command"] == "ls"
            Message.send(conn, {"type": "result", "output": "file.txt"})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "exec", "command": "ls"})
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["type"] == "result"
        assert resp["output"] == "file.txt"
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_skip_sign(self):
        key = b"\xaa\xbb\xcc\xdd" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            Message.send(conn, {"type": "pong"}, skip_sign=True)
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "ping"}, skip_sign=True)
        resp = Message.recv(cli, skip_sign=True)
        assert resp is not None
        assert resp["type"] == "pong"
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_ping_without_sig_accepted(self):
        """ping/stop 消息无签名时被接受（内部健康检查）"""
        key = b"\xaa\xbb\xcc\xdd" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            raw = json.dumps({"type": "ping"}) + "\n"
            conn.sendall(raw.encode("utf-8"))
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["type"] == "ping"
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_exec_without_sig_rejected(self):
        """非 ping/stop 消息无签名时被拒绝"""
        key = b"\x55\x66\x77\x88" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            raw = json.dumps({"type": "exec", "command": "ls"}) + "\n"
            conn.sendall(raw.encode("utf-8"))
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is None
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_tampered_message_rejected(self):
        key = b"\x11\x22\x33\x44" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            self._set_hmac(key)  # 线程局部：server 线程需独立设置签名器
            conn, _ = srv.accept()
            original = {"type": "exec", "command": "ls"}
            sig = Message.get_outbound_signer()._compute_signature(original)
            tampered = dict(original)
            tampered["command"] = "rm -rf /"
            tampered["_sig"] = sig
            data = Message.encode(tampered)
            conn.sendall(data)
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is None
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_no_signature_rejected(self):
        key = b"\x55\x66\x77\x88" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            raw = json.dumps({"type": "exec", "command": "ls"}) + "\n"
            conn.sendall(raw.encode("utf-8"))
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        resp = Message.recv(cli)
        assert resp is None
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_unicode_payload(self):
        key = b"\xde\xad\xbe\xef" * 8
        self._set_hmac(key)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            self._set_hmac(key)  # 线程局部：server 线程需独立设置签名器
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            assert msg is not None
            assert msg["output"] == "你好世界 🔥"
            Message.send(conn, {"type": "ack", "msg": "收到 ✓"})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "result", "output": "你好世界 🔥"})
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["msg"] == "收到 ✓"
        cli.close()
        srv.close()
        t.join(timeout=5)

    def test_hmac_canonical_json_deterministic(self):
        obj1 = {"b": 2, "a": 1, "type": "test"}
        obj2 = {"a": 1, "b": 2, "type": "test"}
        assert HmacMessageSigner._canonical_json(obj1) == HmacMessageSigner._canonical_json(obj2)

    def test_hmac_disabled_no_signature(self):
        self._clear_hmac()

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def server():
            conn, _ = srv.accept()
            msg = Message.recv(conn)
            assert msg is not None
            assert "_sig" not in msg
            Message.send(conn, {"type": "pong"})
            conn.close()

        t = threading.Thread(target=server, daemon=True)
        t.start()

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        Message.send(cli, {"type": "ping"})
        resp = Message.recv(cli)
        assert resp is not None
        assert resp["type"] == "pong"
        cli.close()
        srv.close()
        t.join(timeout=5)
