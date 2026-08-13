"""文件传输二进制帧协议 —— 零业务编解码（file upload/download 专用）

帧格式（大端）：
    [4B payload_len][1B frame_type][payload]

- payload_len 不含 type 字节，控制帧（MANIFEST/PLAN/FILE_END/ACK/ABORT）
  payload 为 UTF-8 JSON，数据帧（DATA）payload 为原始字节
- 数据帧 payload 上限 TRANSFER_CHUNK_SIZE，控制帧上限 TRANSFER_MAX_CONTROL
- 帧读取与 Message._recv_buffers 共享连接级缓冲：握手（JSON）与二进制帧
  在同一 TCP 连接上顺序传输，JSON 行读取可能已把二进制帧前缀预读进缓冲，
  续读必须从残留缓冲开始（协议正确性要求，非防御）
"""

import json
import struct
from typing import Optional, Tuple

from ..config.files import TRANSFER_CHUNK_SIZE, TRANSFER_MAX_CONTROL
from .message import Message as _Msg

# 帧类型
FT_DATA = 0x01        # 文件数据块（原始字节）
FT_FILE_END = 0x02    # 单文件结束：payload=JSON {"relpath","sha256","size","mtime"}
FT_ACK = 0x03         # 单文件结果：payload=JSON {"relpath","ok","error"?}
FT_MANIFEST = 0x04    # 文件清单：payload=JSON {"entries":[...]}
FT_PLAN = 0x05        # 传输计划：payload=JSON {"transfers","skips","mkdirs"}
FT_ABORT = 0x06       # 中止：payload=JSON {"reason"}

_HEADER = struct.Struct(">IB")

MAX_DATA_PAYLOAD = TRANSFER_CHUNK_SIZE
MAX_CONTROL_PAYLOAD = TRANSFER_MAX_CONTROL


class TransferProtocolError(Exception):
    """帧协议错误：超限/截断/非法帧，接收方应中止传输并清理"""


def encode_frame(frame_type: int, payload: bytes) -> bytes:
    """帧编码（含 4B 长度头）"""
    max_payload = MAX_DATA_PAYLOAD if frame_type == FT_DATA else MAX_CONTROL_PAYLOAD
    if len(payload) > max_payload:
        raise TransferProtocolError(
            "frame payload too large: type=%d len=%d max=%d"
            % (frame_type, len(payload), max_payload))
    return _HEADER.pack(len(payload), frame_type) + payload


def decode_frame(header: bytes) -> Tuple[int, int]:
    """解析 5B 帧头 → (payload_len, frame_type)"""
    if len(header) != _HEADER.size:
        raise TransferProtocolError("invalid frame header length: %d" % len(header))
    payload_len, frame_type = _HEADER.unpack(header)
    if frame_type not in (FT_DATA, FT_FILE_END, FT_ACK, FT_MANIFEST, FT_PLAN, FT_ABORT):
        raise TransferProtocolError("unknown frame type: %d" % frame_type)
    max_payload = MAX_DATA_PAYLOAD if frame_type == FT_DATA else MAX_CONTROL_PAYLOAD
    if payload_len > max_payload:
        raise TransferProtocolError(
            "frame payload too large: type=%d len=%d max=%d"
            % (frame_type, payload_len, max_payload))
    return payload_len, frame_type


def _buffered_bytes(sock) -> bytes:
    """连接级接收缓冲中的残留字节（Message.recv 预读的二进制帧前缀）"""
    return _Msg._recv_buffers.pop(sock, b"")


def recv_frame(sock, timeout: Optional[float] = None) -> Optional[Tuple[int, bytes]]:
    """读取一帧：返回 (frame_type, payload)；连接关闭返回 None

    Args:
        sock: TCP socket
        timeout: 本次帧读的总时限（秒）；None = 沿用 socket 超时。
                 超时抛 socket.timeout，调用方按传输中止处理。
    """
    deadline = None if timeout is None else _now() + timeout
    # 共享累积缓冲：先取残留，再按需 recv 补齐，天然处理粘包/拆包
    buf = bytearray(_buffered_bytes(sock))

    def fill(n: int) -> bool:
        """把缓冲补齐到至少 n 字节；连接关闭返回 False"""
        nonlocal buf
        while len(buf) < n:
            if deadline is not None:
                sock.settimeout(max(0.01, deadline - _now()))
            chunk = sock.recv(65536)
            if not chunk:
                return False
            buf += chunk
        return True

    if not fill(_HEADER.size):
        return None
    payload_len, frame_type = decode_frame(bytes(buf[:_HEADER.size]))
    del buf[:_HEADER.size]
    if not fill(payload_len):
        return None
    payload = bytes(buf[:payload_len])
    # 帧 payload 之后的字节留给下一帧（同一连接连读多帧场景）
    del buf[:payload_len]
    if buf:
        _Msg._recv_buffers[sock] = bytes(buf)
    return frame_type, payload


def recv_control_frame(sock, timeout: Optional[float] = None) -> Optional[dict]:
    """读取一帧并解析 JSON（控制帧专用）"""
    frame = recv_frame(sock, timeout)
    if frame is None:
        return None
    ftype, payload = frame
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TransferProtocolError("control frame JSON parse failed: %s" % e) from e


def send_frame(sock, frame_type: int, payload: bytes) -> None:
    """发送一帧（发送前编码校验）"""
    sock.sendall(encode_frame(frame_type, payload))


def send_control_frame(sock, frame_type: int, obj: dict) -> None:
    """发送 JSON 控制帧"""
    send_frame(sock, frame_type, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _now() -> float:
    import time
    return time.monotonic()