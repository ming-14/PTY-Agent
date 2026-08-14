"""插件 I/O 端口 — 进程级插件的连接收发通道

包装连接（conn）为窄接口，只暴露消息与传输帧收发：
- send_message: 发送 JSON 消息（Message.send，含响应签名）
- send_frame / recv_frame / send_control_frame / recv_control_frame:
  委托 protocol.transfer（二进制帧协议，file upload/download 等用）

插件不接触裸 socket，保持协议层封装与可测性（测试可注入假实现）。
仅声明 needs_io=True 的进程级插件由调度器注入。
"""

from typing import Optional, Tuple

from ..protocol.message import Message
from ..protocol.transfer import (
    recv_control_frame,
    recv_frame,
    send_control_frame,
    send_frame,
)


class PluginIO:
    """连接 I/O 端口（进程级插件消息处理用）"""

    def __init__(self, conn):
        self._conn = conn

    def send_message(self, obj: dict) -> None:
        """发送 JSON 消息（消息签名由 Message.send 完成）"""
        Message.send(self._conn, obj)

    def send_frame(self, frame_type: int, payload: bytes) -> None:
        """发送二进制帧"""
        send_frame(self._conn, frame_type, payload)

    def recv_frame(
        self, timeout: Optional[float] = None
    ) -> Optional[Tuple[int, bytes]]:
        """读取一帧；连接关闭返回 None，超时抛 socket.timeout"""
        return recv_frame(self._conn, timeout)

    def send_control_frame(self, frame_type: int, obj: dict) -> None:
        """发送 JSON 控制帧"""
        send_control_frame(self._conn, frame_type, obj)

    def recv_control_frame(self, timeout: Optional[float] = None) -> Optional[dict]:
        """读取一帧并解析 JSON（控制帧专用）"""
        return recv_control_frame(self._conn, timeout)
