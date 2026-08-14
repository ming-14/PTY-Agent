"""沙箱会话管理器 —— 原生 win_sandbox_native（pybind11 in-process）封装

职责：
  - 懒加载 win_sandbox（vendored python 包 + pybind11 pyd，进程内直调）
  - start_process 启动沙箱内进程（可外部传入 hpcon 走 ConPTY 模式）
  - 进程通知由 Process.on_job_process_started/on_job_process_exited 回调
    入队（回调内只入队，禁止调 C++ 方法，契约见 ProcessBinding.cpp）
  - 命令封装：terminate / signal / query_process_list / query_process_exit_code

线程模型：
  - pybind 回调在 win-sandbox 内部线程触发，通知入队加锁保护
  - 根进程退出检测：win-sandbox 的 Job 退出回调显式排除根进程
    （NativeSandboxedProcess.cpp: notif.pid != process_.pid），根进程退出
    只能经 Process.wait(timeout_ms=0) 非阻塞探测（仍在运行抛
    SandboxTimeoutError，已退出返回 (exit_code, ...)），结果缓存

接口形态（进程内直调，无管道）：
  - 无 exe_path/pipe_name/connect_timeout/sbx_config：进程内直调
  - 无 read_output/drain_output/read/write_stdin：ConPTY 模式下 stdio 由
    SandboxPty 持有的 wezterm Pty 直驱（hpcon 外部传入）
  - signal/write_stdin 命令走 Process 对象直调
"""

import logging
import os
import queue
import sys
import threading
from typing import List, Optional

from ..process.base import NOTIF_CRASH, NOTIF_EXIT, NOTIF_SPAWN, ProcessNotification

_logger = logging.getLogger("sandbox-manager")

# 把 bin/ 加入 sys.path（win_sandbox 为 vendored 包）
_BIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "bin",
)
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

# win_sandbox 仅 Windows 可用（pybind11 扩展加载）；非 Windows 平台不应导入
import win_sandbox
from win_sandbox import SandboxTimeoutError


class SandboxError(Exception):
    """沙箱会话错误"""


class SandboxSessionManager:
    """原生 win_sandbox 沙箱实例的会话管理器

    Args:
        quota:       资源配额 dict（对齐 win-sandbox quota payload）。
        isolation:   隔离策略 dict（对齐 win-sandbox isolation_policy payload）。
        log_level:   win_sandbox_native 日志级别（trace|debug|info|warn|error）。
    """

    # 同步等待默认超时（秒）
    DEFAULT_START_TIMEOUT = 10.0

    def __init__(
        self,
        quota: Optional[dict] = None,
        isolation: Optional[dict] = None,
        log_level: str = "info",
    ):
        # 浅拷贝隔离策略：本管理器不修改 dict（无 path_rules 等可变字段），
        # 拷贝仅防止调用方后续改动影响本会话
        self._quota = dict(quota or {})
        self._isolation = dict(isolation or {})
        self._log_level = log_level

        self._instance = None
        self._process = None
        self._process_id: Optional[int] = None
        self._root_pid: Optional[int] = None
        self._exit_code: Optional[int] = None
        self._exit_event = threading.Event()

        # 通知队列（回调线程入队，tracker 消费）
        self._notif_queue: queue.Queue[ProcessNotification] = queue.Queue()

        self._closed = False

    # ── 生命周期 ──

    def start(self) -> None:
        """创建原生沙箱实例（幂等）"""
        if self._instance is not None:
            return
        self._instance = win_sandbox.SandboxInstance(
            config=None, log_level=self._log_level
        )
        _logger.info(
            "sandbox 原生实例已创建: process_count=%s", self._instance.process_count
        )

    def start_process(
        self,
        command_line: str,
        working_dir: Optional[str] = None,
        env_vars: Optional[dict] = None,
        hpcon: Optional[int] = None,
        timeout: float = DEFAULT_START_TIMEOUT,
    ) -> tuple:
        """启动沙箱内进程（hpcon 传入外部 ConPTY 句柄时进入 ConPTY 模式）

        Args:
            hpcon: 外部创建的 HPCON 指针整数值（wezterm Pty.hpcon()）。
                   ConPTY 模式下 stdio 由伪控制台驱动，Process 的
                   stdin/stdout/stderr 句柄为 None，I/O 走外部 ConPTY。

        Returns:
            (process_id, os_pid) 元组。
        """
        if self._instance is None:
            raise SandboxError("sandbox 未启动")
        proc = self._instance.start_process(
            command_line,
            working_dir=working_dir,
            env_vars=env_vars,
            quota=self._build_quota(),
            isolation_policy=self._isolation or None,
            hpcon=hpcon,
        )
        self._process = proc
        self._process_id = int(proc.process_id)
        self._root_pid = int(proc.pid)
        # 回调契约：回调内只入队/设标志，禁止调 C++ 方法（可能死锁）
        proc.on_job_process_started = self._on_job_process_started
        proc.on_job_process_exited = self._on_job_process_exited
        _logger.info(
            "sandbox 进程已启动: command=%r process_id=%s os_pid=%s hpcon=%s",
            command_line[:120],
            self._process_id,
            self._root_pid,
            hpcon if hpcon else None,
        )
        return self._process_id, self._root_pid

    def close(self) -> None:
        """关闭沙箱：shutdown（原生三阶段 GIL 管理，终止所有进程并清理）"""
        if self._closed:
            return
        self._closed = True
        instance = self._instance
        self._instance = None
        if instance is not None:
            try:
                instance.shutdown()
            except Exception as e:
                _logger.warning("沙箱关闭异常: %s", e)
        _logger.info("sandbox 会话已关闭: process_id=%s", self._process_id)

    # ── 命令 ──

    def signal(self, signal: str) -> None:
        """发送信号（ctrl_break|kill）"""
        self._require_process()
        self._process.signal(signal)
        _logger.debug("sandbox signal: %s", signal)

    def terminate(self, exit_code: int = 1) -> None:
        """终止沙箱 Job 内全部进程（KILL_ON_JOB 语义）"""
        self._require_process()
        self._process.terminate(exit_code=exit_code)
        _logger.info("sandbox terminate: exit_code=%s", exit_code)

    def get_process_list(self) -> List[int]:
        """查询沙箱 Job 内全部 PID 列表"""
        self._require_process()
        return [int(pid) for pid in self._process.query_process_list()]

    def get_process_exit_code(self, pid: int) -> Optional[int]:
        """查询 Job 内任意 PID 退出码（None = 仍在运行）

        win-sandbox 的 query_process_exit_code 返回 (exit_code, is_active)
        元组（is_active 对应 GetExitCodeProcess 的 STILL_ACTIVE 语义），
        此处映射为进程树追踪器端口的标量约定：is_active → None。
        """
        self._require_process()
        code, is_active = self._process.query_process_exit_code(pid)
        return None if is_active else int(code)

    def _require_process(self):
        if self._instance is None or self._process is None:
            raise SandboxError("sandbox 进程未启动")

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
        """沙箱内主进程退出码（wait(0) 非阻塞探测；None = 仍在运行）

        win-sandbox 的 Job 退出回调排除根进程，根进程退出只能经
        Process.wait 感知；结果缓存，避免重复 wait 开销。
        """
        if self._exit_code is not None:
            return self._exit_code
        proc = self._process
        if proc is None:
            return None
        try:
            result = proc.wait(timeout_ms=0)
        except SandboxTimeoutError:
            return None
        self._exit_code = int(result[0])
        self._exit_event.set()
        _logger.info(
            "sandbox 根进程退出: pid=%s exit_code=%s reason=%s",
            self._root_pid,
            self._exit_code,
            result[1],
        )
        return self._exit_code

    def is_root_alive(self) -> bool:
        return self.get_exit_code() is None

    # ── 原生回调（win-sandbox 内部线程触发，只入队不调 C++）──

    def _on_job_process_started(self, info: dict) -> None:
        """Job 内子进程创建回调 → 通知队列"""
        try:
            self._notif_queue.put(
                ProcessNotification(
                    NOTIF_SPAWN,
                    pid=info.get("pid"),
                    process_name=info.get("process_name", ""),
                    process_path=info.get("process_path", ""),
                )
            )
            _logger.debug(
                "sandbox job started: pid=%s name=%s",
                info.get("pid"),
                info.get("process_name"),
            )
        except Exception as e:
            _logger.error("job_process_started 回调处理异常: %s", e)

    def _on_job_process_exited(self, info: dict) -> None:
        """Job 内子进程退出回调 → 通知队列

        注意：win-sandbox 回调显式排除根进程（native 端 notif.pid !=
        process_.pid 过滤），根进程退出码由 get_exit_code 经 wait 探测。
        """
        try:
            pid = info.get("pid")
            exit_code = info.get("exit_code")
            kind = info.get("exit_kind", "unknown")
            notif_type = NOTIF_CRASH if kind == "abnormal" else NOTIF_EXIT
            self._notif_queue.put(
                ProcessNotification(
                    notif_type,
                    pid=pid,
                    exit_code=exit_code,
                )
            )
            _logger.debug(
                "sandbox job exited: pid=%s exit_code=%s kind=%s", pid, exit_code, kind
            )
        except Exception as e:
            _logger.error("job_process_exited 回调处理异常: %s", e)

    # ── 内部 ──

    def _build_quota(self) -> Optional[dict]:
        """构建 quota payload：过滤 0 值字段

        win-sandbox 校验：0 为非法值（cpu_ms must be > 0）——"不限制"应
        省略字段而非传 0。bool 字段（crash_silent 等）原样保留。
        """
        q = {}
        for k, v in self._quota.items():
            if isinstance(v, (int, float)) and v == 0:
                continue
            q[k] = v
        return q or None
