"""JSON 换行分隔消息协议

Message 类提供消息的编码、解码、发送和接收功能。
所有方法为 @staticmethod，无状态设计。
"""

import json
import logging
import socket
from typing import Optional

_logger = logging.getLogger("pty-protocol")


class Message:
    """JSON 换行分隔消息

    每条消息为单行 JSON，以 ``\\n`` 结尾，UTF-8 编码。
    接收端使用逐行缓冲读取，支持连接级别的接收缓冲区。
    """

    # 接收缓冲区池（按 socket fileno 索引），避免在 socket 对象上设置自定义属性
    _recv_buffers: dict = {}

    @staticmethod
    def encode(obj: dict) -> bytes:
        """将 dict 编码为 JSON 行 + \\n + UTF-8 字节"""
        encoded = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        _logger.debug("Message.encode: type=%s len=%d", obj.get("type", "?"), len(encoded))
        return encoded

    @staticmethod
    def decode(data: bytes) -> dict:
        """从 bytes 解码为 dict"""
        try:
            decoded = json.loads(data.decode("utf-8"))
            _logger.debug("Message.decode: type=%s len=%d", decoded.get("type", "?"), len(data))
            return decoded
        except Exception as e:
            _logger.warning("Message.decode 失败: %s, data=%r", e, data[:200])
            raise

    @staticmethod
    def recv(sock: socket.socket, max_retries: int = 3) -> Optional[dict]:
        """从 socket 接收一条消息（基于缓冲的行读取，效率更高）

        Args:
            sock: 已连接的 TCP socket。
            max_retries: socket.timeout 最大重试次数。

        Returns:
            解码后的 dict，连接关闭时返回 None。
        """
        fd = sock.fileno()
        buf = Message._recv_buffers.get(fd, b"")
        _logger.debug("recv: fd=%d buffered=%d", fd, len(buf))
        retries = 0
        while True:
            idx = buf.find(b"\n")
            if idx >= 0:
                line = buf[:idx]
                Message._recv_buffers[fd] = buf[idx + 1:]
                _logger.debug("recv: fd=%d complete line len=%d", fd, len(line))
                return Message.decode(line) if line else None
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                retries += 1
                if retries >= max_retries:
                    _logger.warning("recv: fd=%d timeout after %d retries", fd, max_retries)
                    Message._recv_buffers.pop(fd, None)
                    return None
                continue
            except ConnectionError as e:
                _logger.warning("recv: fd=%d connection error: %s", fd, e)
                Message._recv_buffers.pop(fd, None)
                return None
            if not chunk:
                _logger.info("recv: fd=%d connection closed", fd)
                Message._recv_buffers.pop(fd, None)
                return None
            buf += chunk
            # 防止恶意客户端发送超大行
            if len(buf) > 1024 * 1024:
                _logger.warning("recv: fd=%d line too large (%d), dropping", fd, len(buf))
                Message._recv_buffers.pop(fd, None)
                return None

    @staticmethod
    def send(sock: socket.socket, obj: dict):
        """发送一条消息到 socket"""
        data = Message.encode(obj)
        _logger.debug("send: fd=%d type=%s len=%d", sock.fileno(), obj.get("type", "?"), len(data))
        sock.sendall(data)
