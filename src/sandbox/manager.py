"""沙箱会话管理器 —— pybind11 in-process 直调（C++ 核心）

形态：win_sandbox_native.pyd 加载进 Python 进程，SandboxInstance /
Process 为 C++ 对象的 pybind11 绑定，方法直调，无子进程、无 IPC、
无管道协议。

职责：
  - 懒加载原生扩展：经 vendored 包 win_sandbox（bin/ 下）导入 SandboxInstance
  - start_process 启动沙箱内进程（ConPTY 时 hpcon 直传——同进程句柄有效）
  - 回调（on_job_process_started/exited）由 C++ IOCP 线程触发 → 通知队列
  - 命令直调：terminate / signal / query_process_list / query_process_exit_code
  - 根进程退出检测：Process.poll_exit() 非阻塞探测

文件语义：workspace-write（工作区 = working_dir 可写，WRITE_RESTRICTED
令牌 + 能力 SID 白名单）；网络不做限制（能力范围裁剪）。
"""

import os
import queue
import sys
import threading
from typing import List, Optional

from ..process.base import NOTIF_CRASH, NOTIF_EXIT, NOTIF_SPAWN, ProcessNotification
from ..logging import get_logger

_logger = get_logger("sandbox-manager")

# pyd 所在：bin/（构建产物，由 BUILD.py 复制）；经 vendored 包 win_sandbox
# 加载（其 __init__ 把 _native/ 加入 sys.path 再 import win_sandbox_native）
_BIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin",
)
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

try:
    from win_sandbox import SandboxInstance  # noqa: F401
    _HAS_NATIVE = True
except ImportError:
    SandboxInstance = None  # type: ignore[assignment]
    _HAS_NATIVE = False


class SandboxError(Exception):
    """沙箱会话错误"""


class SandboxSessionManager:
    """win_sandbox_native（pybind11 in-process）沙箱会话管理器

    Args:
        quota:       资源配额 dict（键名：memory_mb/job_memory_mb/cpu_ms/
                     cpu_rate_percent/max_processes/wall_clock_timeout_ms/
                     no_ui/crash_silent/breakaway_ok）。
        isolation:   兼容接受（网络/剪贴板策略在新沙箱不生效）。
        log_level:   保留兼容参数。
    """

    def __init__(
        self,
        quota: Optional[dict] = None,
        isolation: Optional[dict] = None,
        log_level: str = "info",
    ):
        self._quota = dict(quota or {})
        self._isolation = dict(isolation or {})
        self._log_level = log_level

        self._instance = None
        self._process = None
        self._process_id: Optional[int] = None
        self._root_pid: Optional[int] = None
        self._exit_code: Optional[int] = None
        self._exit_reason: Optional[str] = None
        self._exit_event = threading.Event()

        self._notif_queue: queue.Queue[ProcessNotification] = queue.Queue()
        self._closed = False

    # ── 生命周期 ──

    def start(self) -> None:
        """创建原生沙箱实例（幂等；close 后可重新 start）"""
        if self._instance is not None:
            return
        self._closed = False
        if not _HAS_NATIVE:
            raise SandboxError(
                "win_sandbox_native 不可用（bin/win_sandbox/_native/ 下 pyd 缺失），无法创建沙箱实例"
            )
        self._instance = SandboxInstance()
        _logger.info("sandbox 原生实例已创建")

    def start_process(
        self,
        command_line: str,
        working_dir: Optional[str] = None,
        env_vars: Optional[dict] = None,
        hpcon: Optional[int] = None,
        timeout: float = 10.0,
    ) -> tuple:
        """启动沙箱内进程（hpcon 传入外部 ConPTY 句柄时进入 ConPTY 模式）

        文件语义：workspace-write（工作区 = working_dir 可写）。
        回调（on_job_process_started/exited）由 C++ IOCP 线程触发。

        Returns:
            (process_id, os_pid) 元组。
        """
        if self._instance is None:
            raise SandboxError("sandbox 未启动")
        if self._process is not None and self._process.poll_exit() is None:
            raise SandboxError("sandbox 会话已在运行")

        workspace = working_dir or os.getcwd()
        proc = self._instance.start_process(
            command_line,
            workspace,
            True,  # workspace-write
            self._quota,
            hpcon if hpcon else None,
            env_vars or {},
        )
        self._process = proc
        self._root_pid = proc.pid
        self._process_id = 1  # 单会话模型：进程 id 固定
        self._exit_code = None
        self._exit_reason = None

        def on_started(pid: int) -> None:
            self._notif_queue.put(ProcessNotification(NOTIF_SPAWN, pid=pid))

        def on_exited(pid: int, exit_code: int, abnormal: bool) -> None:
            kind = NOTIF_CRASH if abnormal else NOTIF_EXIT
            self._notif_queue.put(ProcessNotification(
                kind, pid=pid, exit_code=exit_code))
            _logger.debug(
                "sandbox job exited: pid=%s exit_code=%s abnormal=%s",
                pid, exit_code, abnormal,
            )

        proc.on_job_process_started = on_started
        proc.on_job_process_exited = on_exited
        _logger.info(
            "sandbox 进程已启动: command=%r workspace=%s hpcon=%s",
            command_line[:120], workspace, hpcon if hpcon else None,
        )
        return self._process_id, self._root_pid

    def close(self) -> None:
        """关闭沙箱：终止全部进程并释放实例（C++ 侧清理 grants/temp）"""
        if self._closed:
            return
        self._closed = True
        if self._instance is not None:
            try:
                self._instance.shutdown()
            except Exception as e:
                _logger.warning("沙箱关闭异常: %s", e)
            self._instance = None
        self._process = None
        _logger.info("sandbox 会话已关闭: process_id=%s", self._process_id)

    # ── 命令（直调）──

    def signal(self, signal: str) -> None:
        """发送信号（ctrl_break|kill）"""
        self._require_process()
        if signal == "kill":
            self.terminate(1)
            return
        if signal != "ctrl_break":
            raise ValueError(f"unknown signal: {signal}")
        self._process.signal_ctrl_break()

    def terminate(self, exit_code: int = 1) -> None:
        """终止沙箱 Job 内全部进程（TerminateJobObject）"""
        self._require_process()
        self._process.terminate(exit_code)

    def get_process_list(self) -> List[int]:
        """查询沙箱 Job 内全部 PID 列表"""
        self._require_process()
        return list(self._process.query_process_list())

    def get_process_exit_code(self, pid: int) -> Optional[int]:
        """查询 Job 内任意 PID 退出码（None = 仍在运行）"""
        self._require_process()
        code, active = self._process.query_process_exit_code(pid)
        return None if active else code

    # ── 状态 ──

    def drain_notifications(self) -> List[ProcessNotification]:
        """取出所有待处理的通知（取出即清空）"""
        out = []
        while True:
            try:
                out.append(self._notif_queue.get_nowait())
            except queue.Empty:
                break
        return out

    def get_exit_code(self) -> Optional[int]:
        """沙箱主进程退出码（None = 仍在运行）

        poll_exit() 非阻塞探测：返回 (exit_code, reason) 元组或 None。
        """
        if self._exit_code is not None:
            return self._exit_code
        proc = self._process
        if proc is None:
            return None
        result = proc.poll_exit()
        if result is None:
            return None
        self._exit_code, self._exit_reason = result
        self._exit_event.set()
        _logger.info(
            "sandbox 根进程退出: pid=%s exit_code=%s reason=%s",
            self._root_pid, self._exit_code, self._exit_reason,
        )
        return self._exit_code

    def is_root_alive(self) -> bool:
        return self.get_exit_code() is None

    def _require_process(self):
        if self._process is None:
            raise SandboxError("sandbox 进程未启动")