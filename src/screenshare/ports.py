"""Screenshare 仅查看屏幕服务抽象接口。

纯库调用（无子进程），按需连接（前端连即捕获，断即停止），
仅查看（无键盘鼠标交互），天然多客户端共享同一目标捕获会话。

所有方法应是同步的，调用方通过 ThreadExecutor 调度。
"""

from abc import ABC, abstractmethod


class ScreenshareServicePort(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        """Screenshare 功能是否可用（配置启用 + fastscreen.dll 加载成功）。"""

    @abstractmethod
    def list_targets(self) -> dict:
        """列出所有可查看目标（显示器 + 窗口）。

        Returns:
            dict: {
                "monitors": [{"id", "name", "left", "top", "width", "height", "primary"}, ...],
                "windows": [{"hwnd", "title", "class_name", "left", "top", "width", "height", "visible"}, ...],
            }
            Screenshare 未启用时返回 {"disabled": True, "monitors": [], "windows": []}。
        """

    @abstractmethod
    def get_status(self) -> dict:
        """返回服务状态（包含可用性 + 当前活跃会话数）。

        Returns:
            dict: {disabled, available, active_sessions}
            active_sessions 为当前 StreamManager 中正在捕获的会话数。
        """

    @abstractmethod
    def cleanup(self) -> None:
        """daemon 退出时清理所有捕获会话。"""
