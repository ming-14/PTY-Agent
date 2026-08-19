"""JSON 换行分隔消息协议

Message 类提供消息的编码、解码、发送和接收功能。
所有方法为 @staticmethod，无状态设计。

安全增强：通过 MessageSigner 实现消息签名验证，防止同机进程伪造消息。
签名按方向分离为两个独立角色，均存储在线程局部变量（threading.local）中：
- 出站签名器（outbound_signer）：send 时给本端发出的消息签名
- 入站验证器（inbound_verifier）：recv 时验证本端收到的消息签名

线程局部化使双端口架构成为可能：basic/token Listener 和 TLS Listener 在不同线程中
运行，各自设置独立的签名器/验证器，互不干扰。

这使非对称签名（Ed25519）成为可能：客户端用私钥签请求（出站），
daemon 用公钥验请求（入站）；daemon 响应不签（无私钥），客户端不验响应。
对称签名（HMAC）则两端都能签能验，双向保护。
"""

import json
import socket
import threading
import weakref
from typing import Optional

from ..config.shared import MAX_MESSAGE_LENGTH, SOCKET_RECV_BUFSIZE
from .signing import MessageSigner
from ..logging import get_logger

_logger = get_logger("pty-protocol")


class Message:
    """JSON 换行分隔消息

    每条消息为单行 JSON，以 ``\\n`` 结尾，UTF-8 编码。
    接收端使用逐行缓冲读取，支持连接级别的接收缓冲区。

    签名按方向分离：send 用出站签名器签名，recv 用入站验证器验签。
    """

    _recv_buffers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
    # 线程局部存储：每个线程独立持有出站签名器与入站验证器
    # 双端口架构下 basic/token Listener 和 TLS Listener 在不同线程中运行，
    # 各自需要独立的签名器/验证器，互不干扰
    _tls = threading.local()

    @classmethod
    def set_outbound_signer(cls, signer: Optional[MessageSigner]):
        """设置当前线程的出站签名器（send 时调用 sign）

        线程局部：仅影响调用线程，其他线程不受影响。
        daemon 侧在每个连接处理线程启动时调用，客户端侧在主线程调用一次。

        Args:
            signer: MessageSigner 实例，None 表示发送时不签名。
        """
        cls._tls.outbound_signer = signer
        if signer:
            _logger.debug("出站签名器已设置: %s", signer.name)
        else:
            _logger.debug("出站签名器已清除")

    @classmethod
    def get_outbound_signer(cls) -> Optional[MessageSigner]:
        """获取当前线程的出站签名器"""
        return getattr(cls._tls, "outbound_signer", None)

    @classmethod
    def set_inbound_verifier(cls, verifier: Optional[MessageSigner]):
        """设置当前线程的入站验证器（recv 时调用 verify_and_strip）

        线程局部：仅影响调用线程，其他线程不受影响。

        Args:
            verifier: MessageSigner 实例，None 表示接收时不验签。
        """
        cls._tls.inbound_verifier = verifier
        if verifier:
            _logger.debug("入站验证器已设置: %s", verifier.name)
        else:
            _logger.debug("入站验证器已清除")

    @classmethod
    def get_inbound_verifier(cls) -> Optional[MessageSigner]:
        """获取当前线程的入站验证器"""
        return getattr(cls._tls, "inbound_verifier", None)

    @classmethod
    def set_outbound_response_wrapper(cls, fn):
        """设置当前线程的出站响应包装（daemon 连接线程：套响应信封并分组）

        线程局部：仅影响调用线程。客户端不设置（出站是请求信封），
        ping/pong 等 skip_sign 发送豁免。
        """
        cls._tls.response_wrapper = fn

    @staticmethod
    def encode(obj: dict) -> bytes:
        """将 dict 编码为 JSON 行 + \\n + UTF-8 字节"""
        encoded = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        _logger.debug(
            "Message.encode: type=%s len=%d", obj.get("type", "?"), len(encoded)
        )
        return encoded

    @staticmethod
    def decode(data: bytes) -> dict:
        """从 bytes 解码为 dict"""
        try:
            decoded = json.loads(data.decode("utf-8"))
            _logger.debug(
                "Message.decode: type=%s len=%d", decoded.get("type", "?"), len(data)
            )
            return decoded
        except Exception as e:
            _logger.warning("Message.decode 失败: %s, data=%r", e, data[:200])
            raise

    @staticmethod
    def recv(
        sock: socket.socket, max_retries: int = 3, skip_sign: bool = False
    ) -> Optional[dict]:
        """从 socket 接收一条消息（基于缓冲的行读取，效率更高）

        Args:
            sock: 已连接的 TCP socket。
            max_retries: socket.timeout 最大重试次数。
            skip_sign: 为 True 时跳过签名验证（用于内部 ping/stop 健康检查）。

        Returns:
            解码后的 dict，连接关闭时返回 None。
        """
        sock_key = sock
        buf = Message._recv_buffers.get(sock_key)
        if buf is None:
            # 逐行缓冲用 bytearray 原地追加/截断：大消息（如 screenBufferZ，
            # 可达 MB 级）被拆成多块到达时避免逐块复制整个已累积缓冲（O(n²)）
            buf = bytearray()
            Message._recv_buffers[sock_key] = buf
        _logger.debug("recv: fd=%d buffered=%d", sock.fileno(), len(buf))
        retries = 0
        while True:
            idx = buf.find(b"\n")
            if idx >= 0:
                line = bytes(buf[:idx])
                del buf[: idx + 1]
                _logger.debug(
                    "recv: fd=%d complete line len=%d", sock.fileno(), len(line)
                )
                if not line:
                    return None
                try:
                    msg = Message.decode(line)
                except Exception:
                    _logger.warning("recv: 解码失败，丢弃")
                    return None
                signer = Message.get_inbound_verifier()
                if signer and not skip_sign:
                    # 检查消息是否携带任何已知签名字段
                    has_sig = any(f in msg for f in signer.signature_fields)
                    if has_sig:
                        verified = signer.verify_and_strip(msg)
                        if verified is None:
                            _logger.warning(
                                "recv: 签名验证失败，丢弃 (type=%s)", msg.get("type")
                            )
                            return None
                        msg = verified
                    elif msg.get("type") not in ("ping", "pong", "stop"):
                        _logger.warning(
                            "recv: 签名已启用但消息无签名，丢弃 (type=%s)",
                            msg.get("type"),
                        )
                        return None
                return msg
            try:
                chunk = sock.recv(SOCKET_RECV_BUFSIZE)
            except socket.timeout:
                retries += 1
                if retries >= max_retries:
                    _logger.warning(
                        "recv: fd=%d timeout after %d retries",
                        sock.fileno(),
                        max_retries,
                    )
                    Message._recv_buffers.pop(sock_key, None)
                    return None
                continue
            except ConnectionError as e:
                _logger.warning("recv: fd=%d connection error: %s", sock.fileno(), e)
                Message._recv_buffers.pop(sock_key, None)
                return None
            if not chunk:
                _logger.info("recv: fd=%d connection closed", sock.fileno())
                Message._recv_buffers.pop(sock_key, None)
                return None
            buf.extend(chunk)
            if len(buf) > MAX_MESSAGE_LENGTH:
                _logger.warning(
                    "recv: fd=%d line too large (%d), dropping", sock.fileno(), len(buf)
                )
                Message._recv_buffers.pop(sock_key, None)
                return None

    @staticmethod
    def send(sock: socket.socket, obj: dict, skip_sign: bool = False):
        """发送一条消息到 socket

        Args:
            sock: TCP socket。
            obj: 消息字典。
            skip_sign: 为 True 时跳过签名（用于内部 ping/stop 健康检查）。
        """
        signer = Message.get_outbound_signer()
        # daemon 出站响应包装（线程局部）：把通知构建好的扁平响应体
        # 套上响应信封并分组（data/state/meta）；ping/pong 等 skip_sign 豁免
        wrapper = getattr(Message._tls, "response_wrapper", None)
        if wrapper is not None and not skip_sign:
            obj = wrapper(obj)
        if signer and not skip_sign:
            # sign_bytes 由签名器单次序列化产出完整 wire（签名器内部已有规范 JSON，
            # 在规范字节上拼接签名字段，避免二次 json.dumps）
            data = signer.sign_bytes(obj)
        else:
            data = Message.encode(obj)
        _logger.debug(
            "send: fd=%d type=%s len=%d", sock.fileno(), obj.get("type", "?"), len(data)
        )
        sock.sendall(data)

    @staticmethod
    def ping(host: str, port: int, timeout: float) -> bool:
        """探测指定端口的守护进程是否响应 ping（单实例检查 / 健康探测共用）

        ping 消息走 dispatcher 的 ping 豁免（不校验认证），send 时 skip_sign=True。
        客户端控制方与 daemon 自身启动检查共用此方法，避免重复实现。

        Args:
            host: 目标地址。
            port: 目标端口。
            timeout: 连接与等待超时（秒）。

        Returns:
            True 表示对端响应了 pong。
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            Message.send(sock, {"type": "ping"}, skip_sign=True)
            resp = Message.recv(sock, skip_sign=True)
            sock.close()
            return resp is not None and resp.get("type") == "pong"
        except (ConnectionRefusedError, OSError):
            return False
