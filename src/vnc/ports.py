"""VNC 远程桌面服务抽象接口。

封装 winvnc.exe 进程的启停与状态查询。
WebSocket→VNC TCP 代理由守护进程的 /vnc/websockify 端点实现，
无需 websockify 子进程，所有方法应是同步的，调用方通过 ThreadExecutor 调度。
"""

from abc import ABC, abstractmethod
from typing import Optional


class VncServicePort(ABC):
    """VNC 远程桌面服务抽象。"""

    @abstractmethod
    def is_available(self) -> bool:
        """VNC 功能是否可用（winvnc.exe 存在 + 配置启用）。"""

    @abstractmethod
    def start(self) -> dict:
        """启动 winvnc.exe 进程。

        Returns:
            dict: {vnc_port, password, vnc_pid}

        Raises:
            RuntimeError: 启动失败（端口占用、winvnc 缺失等）。
        """

    @abstractmethod
    def stop(self) -> None:
        """停止 winvnc.exe 进程。"""

    @abstractmethod
    def get_status(self) -> dict:
        """返回运行状态。

        Returns:
            dict: {running, vnc_port, vnc_pid, password}
            running=False 时其他字段为 None。
        """

    @abstractmethod
    def get_connection_info(self) -> Optional[dict]:
        """返回前端连接所需信息。

        Returns:
            dict: {vnc_port, password} 或 None（未运行）。
            vnc_port 供守护进程的 /vnc/websockify 代理端点使用。
        """
