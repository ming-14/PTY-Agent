"""传输帧协议单元测试 —— 编解码边界（零长度/超限/粘包/残留缓冲）"""

import socket
import threading
import time

import pytest

from src.config.transfer import TRANSFER_CHUNK_SIZE, TRANSFER_MAX_CONTROL
from src.protocol import message as _message_mod
from src.protocol.message import Message
from src.protocol import transfer as tf


class TestEncodeDecode:
    def test_roundtrip_data(self):
        data = b"hello world"
        frame = tf.encode_frame(tf.FT_DATA, data)
        assert frame == (len(data)).to_bytes(4, "big") + bytes([tf.FT_DATA]) + data

    def test_roundtrip_control(self):
        payload = b'{"a": 1}'
        frame = tf.encode_frame(tf.FT_PLAN, payload)
        assert frame == (len(payload)).to_bytes(4, "big") + bytes([tf.FT_PLAN]) + payload

    def test_zero_payload(self):
        frame = tf.encode_frame(tf.FT_ACK, b"")
        assert frame == b"\x00\x00\x00\x00" + bytes([tf.FT_ACK])

    def test_data_payload_at_limit(self):
        payload = b"x" * TRANSFER_CHUNK_SIZE
        assert len(tf.encode_frame(tf.FT_DATA, payload)) == 4 + 1 + TRANSFER_CHUNK_SIZE

    def test_data_payload_over_limit(self):
        with pytest.raises(tf.TransferProtocolError):
            tf.encode_frame(tf.FT_DATA, b"x" * (TRANSFER_CHUNK_SIZE + 1))

    def test_control_payload_over_limit(self):
        with pytest.raises(tf.TransferProtocolError):
            tf.encode_frame(tf.FT_PLAN, b"x" * (TRANSFER_MAX_CONTROL + 1))

    def test_decode_unknown_type(self):
        header = (0).to_bytes(4, "big") + bytes([0x77])
        with pytest.raises(tf.TransferProtocolError):
            tf.decode_frame(header)

    def test_decode_bad_header_len(self):
        with pytest.raises(tf.TransferProtocolError):
            tf.decode_frame(b"\x00\x00")

    def test_decode_oversize_len(self):
        header = (TRANSFER_MAX_CONTROL + 1).to_bytes(4, "big") + bytes([tf.FT_PLAN])
        with pytest.raises(tf.TransferProtocolError):
            tf.decode_frame(header)


class _Channel:
    """单向字节通道：一端的 sendall 写入，另一端的 recv 读取（模拟 TCP 半连接）"""

    def __init__(self):
        self._buf = b""
        self._lock = threading.Lock()
        self._closed = False

    def sendall(self, data):
        with self._lock:
            self._buf += data

    def recv(self, n):
        while True:
            with self._lock:
                if self._buf:
                    out = self._buf[:n]
                    self._buf = self._buf[n:]
                    return out
                if self._closed:
                    return b""
            import time
            import time
            time.sleep(0.002)

    def close(self):
        with self._lock:
            self._closed = True


class _Sock:
    """socket 替身：inbound 通道供 recv，outbound 通道供 sendall（成对构造）"""

    def __init__(self, inbound: _Channel, outbound: _Channel):
        self._in = inbound
        self._out = outbound
        self._timeout = None

    def sendall(self, data):
        self._out.sendall(data)

    def recv(self, n):
        if self._timeout is not None:
            deadline = time.monotonic() + self._timeout
            while True:
                with self._in._lock:
                    if self._in._buf:
                        out = self._in._buf[:n]
                        self._in._buf = self._in._buf[n:]
                        return out
                    if self._in._closed:
                        return b""
                if time.monotonic() >= deadline:
                    raise socket.timeout()
                time.sleep(0.002)
        while True:
            with self._in._lock:
                if self._in._buf:
                    out = self._in._buf[:n]
                    self._in._buf = self._in._buf[n:]
                    return out
                if self._in._closed:
                    return b""
            time.sleep(0.002)

    def settimeout(self, t):
        self._timeout = t

    def close(self):
        self._out.close()

    def fileno(self):
        return -1


def _pair():
    """构造双向 socket 对：a.send → b.recv，b.send → a.recv"""
    ab, ba = _Channel(), _Channel()
    return _Sock(ba, ab), _Sock(ab, ba)


class TestFrameIO:
    def test_recv_single_frame(self):
        a, b = _pair()
        data = b"payload-data"
        tf.send_frame(a, tf.FT_DATA, data)
        ftype, payload = tf.recv_frame(b)
        assert (ftype, payload) == (tf.FT_DATA, data)

    def test_recv_back_to_back_frames_no_leftover_loss(self):
        a, b = _pair()
        # 两帧粘包发送，接收端必须无损切分
        a.sendall(tf.encode_frame(tf.FT_DATA, b"aaa"))
        a.sendall(tf.encode_frame(tf.FT_FILE_END, b"bbb"))
        f1 = tf.recv_frame(b)
        f2 = tf.recv_frame(b)
        assert f1 == (tf.FT_DATA, b"aaa")
        assert f2 == (tf.FT_FILE_END, b"bbb")

    def test_recv_chunked_frame(self):
        a, b = _pair()
        payload = b"z" * 1000
        frame = tf.encode_frame(tf.FT_DATA, payload)
        # 逐字节投喂模拟拆包
        for byte in frame:
            a.sendall(bytes([byte]))
        ftype, got = tf.recv_frame(b)
        assert (ftype, got) == (tf.FT_DATA, payload)

    def test_recv_after_message_json_leftover(self):
        """握手 JSON 读取后残留缓冲含二进制帧前缀：recv_frame 必须从残留续读"""
        a, b = _pair()
        json_line = b'{"type":"file_upload_start"}\n'
        frame = tf.encode_frame(tf.FT_DATA, b"abc")
        a.sendall(json_line + frame)
        # 模拟 daemon：Message.recv 读 JSON 时把二进制帧前缀一并预读进缓冲
        assert Message.recv(b) == {"type": "file_upload_start"}
        # Message.recv 内经模块全局解析 Message，须按模块属性校验（e2e 重载后
        # 类对象可能更新，模块属性才是 recv 实际写入缓冲的类）
        assert b in _message_mod.Message._recv_buffers
        ftype, payload = tf.recv_frame(b)
        assert (ftype, payload) == (tf.FT_DATA, b"abc")
        # 残留缓冲已消费
        assert _message_mod.Message._recv_buffers.get(b, b"") == b""

    def test_recv_control_json(self):
        a, b = _pair()
        tf.send_control_frame(a, tf.FT_PLAN, {"transfers": ["a.txt"], "skips": []})
        assert tf.recv_control_frame(b) == {"transfers": ["a.txt"], "skips": []}

    def test_recv_bad_control_json(self):
        a, b = _pair()
        tf.send_frame(a, tf.FT_PLAN, b"{not-json")
        with pytest.raises(tf.TransferProtocolError):
            tf.recv_control_frame(b)

    def test_conn_closed_returns_none(self):
        a, b = _pair()
        a.close()
        assert tf.recv_frame(b) is None

    def test_timeout_raises_socket_timeout(self):
        a, b = _pair()
        with pytest.raises(socket.timeout):
            tf.recv_frame(b, timeout=0.05)