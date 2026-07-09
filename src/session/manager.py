"""会话管理器 — SessionManager

管理所有 PTY 会话的创建、获取、列出、移除和批量停止。
"""

import logging
import threading
from typing import Optional

from .session import Session

_logger = logging.getLogger("pty-session")


class SessionManager:
    """会话管理器

    负责会话 CRUD 操作和生命周期管理，线程安全。
    """

    def __init__(self):
        self._sessions: dict = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        session_id: str,
        command,
        encoding: Optional[str] = None,
        shell: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> Session:
        """创建并启动新会话

        Args:
            session_id: 会话唯一标识符。
            command:    要执行的命令。
            encoding:   终端编码（默认自动探测）。
            shell:      指定解释器（cmd/powershell/pwsh/bash），默认 cmd。
            cwd:        子进程工作目录（默认守护进程当前目录）。

        Returns:
            新创建的 Session 实例。

        Raises:
            ValueError: session_id 无效。
            KeyError:   session_id 已存在。
        """
        if not session_id or not isinstance(session_id, str):
            raise ValueError("会话 ID 必须为非空字符串")
        with self._lock:
            if session_id in self._sessions:
                raise KeyError(f"会话 '{session_id}' 已存在")
            s = Session(session_id, command, encoding=encoding, shell=shell, cwd=cwd)
            self._sessions[session_id] = s
        s.start()
        return s

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取指定会话

        Args:
            session_id: 会话标识符。

        Returns:
            Session 实例，不存在时返回 None。
        """
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self) -> list:
        """列出所有活跃会话

        自动清理已结束的会话。

        Returns:
            dict 列表，每项包含 id/command/running 字段。
        """
        with self._lock:
            ended_ids = [
                sid for sid, s in self._sessions.items() if not s.running
            ]
            for sid in ended_ids:
                s = self._sessions.pop(sid, None)
                if s:
                    try:
                        s.stop()
                    except Exception as e:
                        _logger.warning("清理已结束会话 '%s' 时异常: %s", sid, e)
            return [
                {
                    "id": s.id,
                    "command": (
                        s.command
                        if isinstance(s.command, str)
                        else " ".join(s.command)
                    ),
                    "running": s.running,
                }
                for s in self._sessions.values()
            ]

    def remove_session(self, session_id: str):
        """移除并停止指定会话

        Args:
            session_id: 会话标识符。
        """
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if s:
            try:
                s.stop()
            except Exception as e:
                _logger.warning("移除会话 '%s' 时异常: %s", session_id, e)

    def stop_all(self):
        """停止所有会话"""
        with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            self.remove_session(sid)
