"""进程子包 — 进程信息查询、监控、GUI 窗口检测与进程树追踪端口

分层：
- base：实体层（ProcessNotification 统一通知 + ProcessTreeTracker 抽象端口）
- info / monitor / gui：上层编排（进程信息、进程监控、GUI 检测）
- windows/、unix/：平台实现（Job Object / process group 进程树追踪）
- win32_error：Windows 错误码格式化

进程树追踪器工厂（create_process_tree_tracker）是本包对外统一入口：
Session 等消费方只依赖此工厂与 ProcessTreeTracker 抽象，不接触平台实现。
"""

import uuid

from ..config.common import IS_WINDOWS
from .base import ProcessNotification, ProcessTreeTracker
from .gui import GuiDetector
from .info import (
    _format_exit_code_message,
    _format_pty_error,
    _get_process_name,
    _get_process_path,
    _signal_name,
)
from .monitor import ProcessMonitor


def create_process_tree_tracker() -> ProcessTreeTracker:
    """创建平台对应的进程树追踪器（Session 生命周期 owner）

    Windows：sandbox.enabled=true → SandboxProcessTreeTracker（winsandbox 委派，
             函数内延迟导入，避免 process ↔ sandbox 静态环：sandbox 依赖 process.base）；
             否则 JobProcessTreeTracker（Job Object）
    Unix：process group（PgidProcessTreeTracker）

    沙箱与原生后端共用同一端口，Session/PTY 对实现无感知。
    """
    if IS_WINDOWS:
        from ..config import sandbox as _sbx_cfg

        if _sbx_cfg.ENABLED:
            from ..sandbox import SandboxProcessTreeTracker, SandboxSessionManager

            manager = SandboxSessionManager(
                quota=_sbx_cfg.QUOTA,
                isolation=_sbx_cfg.ISOLATION,
                log_level=_sbx_cfg.LOG_LEVEL,
            )
            return SandboxProcessTreeTracker(manager)
        from .windows import JobProcessTreeTracker

        return JobProcessTreeTracker(name=f"session-{uuid.uuid4().hex[:8]}")
    from .unix import PgidProcessTreeTracker

    return PgidProcessTreeTracker()
