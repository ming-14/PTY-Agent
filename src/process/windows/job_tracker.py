"""JobProcessTreeTracker — Windows Job Object 进程树追踪实现

实现 `ProcessTreeTracker` 抽象端口（见 design/process-manager-refactor.md §3.2）：

- register_root：CreateProcess 返回后同一代码路径内登记 root（AssignProcessToJobObject，
  子进程自动继承入 Job，杜绝孙进程逃逸）
- kill_tree：枚举 Job 内 PID → 逐个 TerminateProcess（与 winsandbox TerminateAll
  语义一致），超时后依赖 KILL_ON_JOB_CLOSE 兜底
- IOCP 实时通知：进程创建/退出/崩溃通过完成端口推送，统一映射为 ProcessNotification
- GUI 三件套：聚合 GuiWindowMonitor（依赖 get_process_list()）

生命周期归 Session（kill_tree → pty.close → tracker.close），PTY 不持有。
"""

import ctypes
import threading
import time
from ctypes import wintypes as W
from typing import List, Optional

from ...config.daemon import JOB_OBJECT_NAME_PREFIX
from ..base import (
    NOTIF_CRASH,
    NOTIF_EXIT,
    NOTIF_SPAWN,
    ProcessNotification,
    ProcessTreeTracker,
)
from ..win32_error import STILL_ACTIVE
from .api import (
    _INVALID_HANDLE_VALUE,
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS,
    _JOB_OBJECT_MSG_EXIT_PROCESS,
    _JOB_OBJECT_MSG_NEW_PROCESS,
    _MAX_JOB_PIDS,
    _WAIT_TIMEOUT,
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT,
    JOBOBJECT_BASIC_PROCESS_ID_LIST,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SET_QUOTA,
    PROCESS_TERMINATE,
    _AssignProcessToJobObject,
    _CloseHandle,
    _CreateIoCompletionPort,
    _CreateJobObjectW,
    _GetExitCodeProcess,
    _GetQueuedCompletionStatus,
    _JobObjectAssociateCompletionPortInformation,
    _JobObjectBasicProcessIdList,
    _JobObjectExtendedLimitInformation,
    _OpenProcess,
    _PostQueuedCompletionStatus,
    _QueryInformationJobObject,
    _SetInformationJobObject,
    _TerminateProcess,
)
from .gui_monitor import GuiWindowMonitor
from ...logging import get_logger

_logger = get_logger("process-job-tracker")

# ── IOCP 超时（毫秒）──
_IOCP_TIMEOUT = 1000  # 每秒检查停止标志
# ── kill_tree 终止后的残留进程轮询间隔（秒）──
_KILL_POLL_INTERVAL = 0.05


class JobProcessTreeTracker(ProcessTreeTracker):
    """Windows Job Object 进程树追踪器

    维护一个命名 Job Object（KILL_ON_JOB_CLOSE + DIE_ON_UNHANDLED_EXCEPTION），
    通过 IOCP 实时通知进程事件，聚合 GUI 窗口检测。

    Attributes:
        hjob: Job Object 句柄（None 表示已关闭）。
        name: Job Object 名称（用于调试标识）。
    """

    def __init__(self, name: str = ""):
        """创建 Job Object 并启动 IOCP 通知线程

        Args:
            name: 可选的 Job Object 名称，用于调试标识。
        """
        self.name = name
        self._hjob: Optional[int] = None
        self._iocp: Optional[int] = None
        self._notif_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # 线程安全的事件队列（由通知线程写入，外部 drain 读取）
        self._notif_lock = threading.Lock()
        self._notifications: List[ProcessNotification] = []
        self._root_pid: Optional[int] = None
        self._host_pids: set = set()
        self._gui_monitor = GuiWindowMonitor(self)

        job_name = None
        if name:
            job_name = f"{JOB_OBJECT_NAME_PREFIX}{name}"
        self._hjob = _CreateJobObjectW(None, job_name)
        if not self._hjob:
            err = ctypes.get_last_error()
            _logger.warning("CreateJobObjectW('%s') 失败: err=%d", name, err)
            return

        self._set_job_limits()
        self._setup_notifications()

    # ── 登记 ──

    def register_root(self, pid: int, hprocess: Optional[int] = None) -> bool:
        """登记 root 进程并分配到 Job

        PTY spawn 成功后立即调用（同一代码路径内），hprocess 为
        CreateProcess 返回的进程句柄；为 None 时按 PID 打开。
        子进程自动继承 Job 归属，从根上杜绝进程树逃逸。

        Returns:
            True 登记成功，False 句柄无效或分配失败。
        """
        self._root_pid = pid
        hproc = hprocess
        if not hproc:
            hproc = _OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid
            )
            if not hproc:
                _logger.warning(
                    "register_root: OpenProcess(%d) 失败 err=%d",
                    pid,
                    ctypes.get_last_error(),
                )
                return False
            try:
                return self._assign(hproc)
            finally:
                _CloseHandle(hproc)
        return self._assign(hproc)

    def assign_extra_process(self, pid: int) -> bool:
        """把额外进程纳入 Job 作用域（缓解：ConPTY 宿主 OpenConsole 连带清理）

        daemon spawn 的 OpenConsole/conhost 默认不在命令树 Job 内，宿主异常死亡时
        会残留（其 VtInputThread 对死 server 管道不自旋退出时）。此处把这类进程
        Assign 进当前 Job（KILL_ON_JOB_CLOSE），保证宿主进程句柄关闭时被连带清理。

        Args:
            pid: 目标进程 PID。

        Returns:
            True 入组成功；进程已退出或无法打开时返回 False。
        """
        if not self._hjob or not pid:
            return False
        hproc = _OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not hproc:
            _logger.warning(
                "assign_extra_process: OpenProcess(%d) 失败 err=%d",
                pid,
                ctypes.get_last_error(),
            )
            return False
        try:
            return self._assign(hproc)
        finally:
            _CloseHandle(hproc)

    def _assign(self, hprocess: int) -> bool:
        """将进程分配到 Job Object（子进程自动继承）"""
        if not self._hjob or not hprocess:
            return False
        ok = _AssignProcessToJobObject(self._hjob, hprocess)
        if not ok:
            _logger.warning(
                "AssignProcessToJobObject 失败: handle=%s err=%d",
                hprocess,
                ctypes.get_last_error(),
            )
        return bool(ok)

    # ── 宿主进程登记（ConPTY 宿主等非工作进程）──

    def register_host_pid(self, pid: int):
        """登记宿主进程 PID（如 OpenConsole），自然结束检测时排除

        宿主进程常驻于 PTY 生命周期（直至 pty.close），若被计入工作进程，
        Job 进程列表恒非空，会话将永远检测不到自然结束。
        """
        if pid:
            self._host_pids.add(pid)

    def get_work_process_list(self) -> List[int]:
        """获取工作进程 PID 列表（排除已登记的宿主进程）"""
        if not self._host_pids:
            return self.get_process_list()
        return [p for p in self.get_process_list() if p not in self._host_pids]

    # ── 进程树查询 ──

    def get_process_list(self) -> List[int]:
        """获取 Job 内所有进程的 PID 列表"""
        if not self._hjob:
            return []
        try:
            # JOBOBJECT_BASIC_PROCESS_ID_LIST 内存布局：
            #   [NumberOfAssignedProcesses] [NumberOfProcessIdsInList] [PID列表...]
            # 不能用扁平数组，否则会把前两个 DWORD 也当作 PID
            buf_size = ctypes.sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST)
            buf = ctypes.create_string_buffer(buf_size)
            info = JOBOBJECT_BASIC_PROCESS_ID_LIST.from_buffer(buf)
            ret_len = W.DWORD(0)
            ok = _QueryInformationJobObject(
                self._hjob,
                _JobObjectBasicProcessIdList,
                ctypes.byref(info),
                buf_size,
                ctypes.byref(ret_len),
            )
            if ok:
                count = info.NumberOfProcessIdsInList
                return [info.ProcessIdList[i] for i in range(min(count, _MAX_JOB_PIDS))]
            return []
        except Exception as e:
            _logger.warning("查询 Job 进程列表异常: %s", e)
            return []

    def get_process_count(self) -> int:
        """获取 Job 内当前进程数"""
        return len(self.get_process_list())

    def is_root_alive(self) -> bool:
        """root 进程是否存活（退出码为 STILL_ACTIVE）

        未登记或查询失败视为已死。
        """
        rc = self.get_root_exit_code()
        if rc is None:
            return False
        return rc == STILL_ACTIVE

    # ── 终止 ──

    def kill_tree(self, timeout: float = 3.0):
        """终止 Job 内全部进程（枚举 PID + TerminateProcess）

        与 winsandbox TerminateAll 语义一致：逐个 TerminateProcess，
        然后轮询等待进程全部退出，超时兜底（close 时 KILL_ON_JOB_CLOSE
        仍会补刀）。杀树后 tracker 仍可查询（行为增强）。
        """
        if not self._hjob:
            return
        deadline = time.monotonic() + timeout
        while True:
            pids = self.get_process_list()
            for pid in pids:
                self._terminate_pid(pid)
            if not pids:
                return
            if time.monotonic() >= deadline:
                _logger.warning("kill_tree 超时，残留进程: %s", pids)
                return
            time.sleep(_KILL_POLL_INTERVAL)

    def _terminate_pid(self, pid: int) -> bool:
        """强制终止指定 PID（不保证立即退出）"""
        hproc = _OpenProcess(PROCESS_TERMINATE, False, pid)
        if not hproc:
            return False
        try:
            ok = _TerminateProcess(hproc, 1)
            if not ok:
                _logger.warning(
                    "TerminateProcess(%d) 失败 err=%d", pid, ctypes.get_last_error()
                )
            return bool(ok)
        finally:
            _CloseHandle(hproc)

    # ── 退出码 ──

    def get_root_exit_code(self) -> Optional[int]:
        """查询 root 进程退出码（None 表示未登记或查询失败）"""
        if not self._root_pid:
            return None
        return self._get_exit_code(self._root_pid)

    def get_process_exit_code(self, pid: int) -> Optional[int]:
        """查询指定 PID 的进程退出码"""
        return self._get_exit_code(pid)

    def _get_exit_code(self, pid: int) -> Optional[int]:
        """查询指定 PID 的进程退出码"""
        try:
            hproc = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not hproc:
                return None
            try:
                code = W.DWORD(0)
                if _GetExitCodeProcess(hproc, ctypes.byref(code)):
                    return code.value
                return None
            finally:
                _CloseHandle(hproc)
        except Exception:
            return None

    # ── 通知 ──

    def drain_notifications(self) -> List[ProcessNotification]:
        """取出所有待处理的通知（线程安全，引用交换避免复制）"""
        with self._notif_lock:
            items, self._notifications = self._notifications, []
        return items

    def _push_notif(self, notif: ProcessNotification):
        """线程安全地添加通知"""
        with self._notif_lock:
            self._notifications.append(notif)

    def _setup_notifications(self):
        """设置 Job Object 完成端口通知

        创建 IOCP 并与 Job 关联，启动后台线程监听实时进程事件。
        """
        if not self._hjob:
            return
        try:
            self._iocp = _CreateIoCompletionPort(
                _INVALID_HANDLE_VALUE,
                None,
                None,
                0,
            )
            if not self._iocp:
                _logger.warning("CreateIoCompletionPort 失败")
                return

            # 关联 Job 与 IOCP
            COMPLETION_KEY = ctypes.c_void_p(0x505459)  # "PTY" 标识
            assoc = JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
            assoc.CompletionKey = COMPLETION_KEY
            assoc.CompletionPort = self._iocp
            ok = _SetInformationJobObject(
                self._hjob,
                _JobObjectAssociateCompletionPortInformation,
                ctypes.byref(assoc),
                ctypes.sizeof(assoc),
            )
            if not ok:
                err = ctypes.get_last_error()
                _logger.warning("关联 Job→IOCP 失败: err=%d", err)
                _CloseHandle(self._iocp)
                self._iocp = None
                return

            _logger.info("Job IOCP 通知已启动")
            self._notif_thread = threading.Thread(
                target=self._notification_loop,
                daemon=True,
                name=f"job-iocp-{self.name}",
            )
            self._notif_thread.start()
        except Exception as e:
            _logger.warning("Job 通知初始化失败: %s", e)
            if self._iocp:
                _CloseHandle(self._iocp)
                self._iocp = None

    def _notification_loop(self):
        """后台线程：监听 Job Object IOCP 通知

        消息类型：
          - NEW_PROCESS(6): lpOverlapped = PID → spawn 通知（尽力填充 name/path）
          - EXIT_PROCESS(7): lpOverlapped = PID → exit 通知
          - ABNORMAL_EXIT_PROCESS(8): lpOverlapped = PID → crash 通知
        """
        _logger.info("Job 通知线程启动")
        while not self._stop_event.is_set():
            try:
                nbytes = W.DWORD(0)
                key = ctypes.c_void_p()
                ovl = ctypes.c_void_p()

                ok = _GetQueuedCompletionStatus(
                    self._iocp,
                    ctypes.byref(nbytes),
                    ctypes.byref(key),
                    ctypes.byref(ovl),
                    _IOCP_TIMEOUT,
                )
                if not ok:
                    err = ctypes.get_last_error()
                    if err == _WAIT_TIMEOUT:
                        continue
                    if not self._stop_event.is_set():
                        _logger.debug("GQCS err=%d", err)
                    continue

                msg_type = nbytes.value
                # Job Object 通知的 lpOverlapped 直接存数值（不是指针）
                raw_value = ovl.value if ovl else 0

                if msg_type == _JOB_OBJECT_MSG_NEW_PROCESS:
                    pid = raw_value
                    proc_path = self._get_process_path_fast(pid)
                    proc_name = (
                        proc_path.rsplit("\\", 1)[-1]
                        if proc_path and "\\" in proc_path
                        else proc_path
                    )
                    _logger.info("Job NEW_PROCESS: pid=%d name=%s", pid, proc_name)
                    self._push_notif(
                        ProcessNotification(
                            NOTIF_SPAWN,
                            pid=pid,
                            process_name=proc_name or "",
                            process_path=proc_path or "",
                        )
                    )
                elif msg_type in (
                    _JOB_OBJECT_MSG_EXIT_PROCESS,
                    _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS,
                ):
                    pid = raw_value
                    exit_code = self._get_exit_code(pid)
                    is_crash = msg_type == _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS
                    _logger.info(
                        "Job %s: pid=%d exit=%s",
                        "ABNORMAL_EXIT" if is_crash else "EXIT",
                        pid,
                        exit_code,
                    )
                    self._push_notif(
                        ProcessNotification(
                            NOTIF_CRASH if is_crash else NOTIF_EXIT,
                            pid=pid,
                            exit_code=exit_code,
                        )
                    )
                else:
                    _logger.debug("Job 通知: type=%d data=%d", msg_type, raw_value)
            except Exception as e:
                if not self._stop_event.is_set():
                    _logger.warning("Job 通知循环异常: %s", e)
        _logger.info("Job 通知线程退出")

    def _get_process_path_fast(self, pid: int) -> Optional[str]:
        """在通知线程中快速获取进程路径（进程刚创建，一定还活着）

        收敛到统一的 psutil 查询，失败返回 None（与阻塞式 _get_process_path
        的 'PID {pid}' 哨兵区分）。
        """
        from ..info import _get_process_path

        path = _get_process_path(pid)
        return None if path.startswith("PID ") else path

    # ── GUI 窗口（聚合 GuiWindowMonitor）──

    def get_gui_windows(self) -> List[dict]:
        """获取已检测到的全部 GUI 窗口（dict 列表）"""
        return [w.to_dict() for w in self._gui_monitor.windows]

    def poll_gui_windows(self, pids: Optional[List[int]] = None) -> List[dict]:
        """轮询检测新增 GUI 窗口（仅返回本轮新增）

        Args:
            pids: 调用方已获取的进程树 PID 列表（同一 tick 复用）；None 时自行查询。
        """
        return [w.to_dict() for w in self._gui_monitor.poll(pids)]

    def close_gui_window(self, hwnd: int) -> bool:
        """通过 WM_CLOSE 关闭指定 GUI 窗口"""
        return self._gui_monitor.close_window(hwnd)

    # ── 生命周期 ──

    def _set_job_limits(self):
        """设置 JOB 限制标志

        - KILL_ON_JOB_CLOSE：关闭句柄时终止所有进程（kill_tree 超时兜底）
        - DIE_ON_UNHANDLED_EXCEPTION：子进程崩溃时不弹对话框，直接退出
        """
        if not self._hjob:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        )
        _SetInformationJobObject(
            self._hjob,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

    def close(self):
        """关闭 Job Object 句柄，停止通知线程

        先关闭 Job 句柄（KILL_ON_JOB_CLOSE）立即终止所有残留进程，
        再停止通知线程，最后关闭 GUI 监控。
        """
        self._stop_event.set()
        if self._hjob:
            _CloseHandle(self._hjob)
            self._hjob = None
        # 发送退出信号到 IOCP 以唤醒 GetQueuedCompletionStatus
        if self._iocp:
            try:
                _PostQueuedCompletionStatus(self._iocp, 0, None, None)
            except Exception:
                pass
        if self._notif_thread and self._notif_thread.is_alive():
            self._notif_thread.join(2.0)
            if self._notif_thread.is_alive():
                _logger.warning("Job 通知线程未退出")
        if self._iocp:
            _CloseHandle(self._iocp)
            self._iocp = None
        self._gui_monitor.close()
