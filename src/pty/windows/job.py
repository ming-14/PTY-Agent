"""ProcessJob — Windows Job Object 封装

管理一个 Windows Job Object，支持：
- 创建命名 Job Object
- 将进程分配到 Job（跟踪进程树）
- 设置 KILL_ON_JOB_CLOSE（关闭句柄时自动终止所有进程）
- 设置 DIE_ON_UNHANDLED_EXCEPTION（崩溃直接退出不弹对话框）
- **IOCP 实时通知**：进程创建/退出/崩溃通过完成端口推送，无需轮询
- 上下文管理器支持（自动清理）

典型用法:
    with ProcessJob("my-session") as job:
        job.assign(hProcess)
        # ... 运行子进程 ...
        pids = job.query_process_list()
"""

import ctypes
import logging
import threading
from typing import List, Optional, Tuple
from ctypes import wintypes as W

from .convars import (
    _CreateJobObjectW,
    _AssignProcessToJobObject,
    _SetInformationJobObject,
    _QueryInformationJobObject,
    _CloseHandle,
    _GetExitCodeProcess,
    _JobObjectExtendedLimitInformation,
    _JobObjectBasicProcessIdList,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOBOBJECT_BASIC_PROCESS_ID_LIST,
    _MAX_JOB_PIDS,
    # ── IOCP 通知 ──
    _JobObjectAssociateCompletionPortInformation,
    JOBOBJECT_ASSOCIATE_COMPLETION_PORT,
    _CreateIoCompletionPort,
    _GetQueuedCompletionStatus,
    _PostQueuedCompletionStatus,
    _JOB_OBJECT_MSG_NEW_PROCESS,
    _JOB_OBJECT_MSG_EXIT_PROCESS,
    _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS,
    K,
)
from .error_msg import STILL_ACTIVE

_logger = logging.getLogger("pty-job")

# ── IOCP 超时（毫秒）──
_IOCP_TIMEOUT = 1000  # 每秒检查停止标志


class JobNotification:
    """Job Object 实时通知

    封装 IOCP 推送的进程事件，包含发生时间。
    """

    def __init__(self, msg_type: int, pid: int = 0, exit_code: Optional[int] = None):
        self.msg_type = msg_type
        self.pid = pid
        self.exit_code = exit_code

    def is_crash(self) -> bool:
        return self.msg_type == _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS

    def is_exit(self) -> bool:
        return self.msg_type == _JOB_OBJECT_MSG_EXIT_PROCESS

    def is_spawn(self) -> bool:
        return self.msg_type == _JOB_OBJECT_MSG_NEW_PROCESS


class ProcessJob:
    """Windows Job Object 封装

    Attributes:
        hjob:   Job Object 句柄（None 表示已关闭）。
        name:   Job Object 名称（用于调试标识）。
    """

    def __init__(self, name: str = ""):
        """创建 Job Object

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
        self._notifications: List[JobNotification] = []

        job_name = None
        if name:
            job_name = f"Local\\PTYJob_{name}"
        self._hjob = _CreateJobObjectW(None, job_name)
        if not self._hjob:
            err = ctypes.get_last_error()
            _logger.warning("CreateJobObjectW('%s') 失败: err=%d", name, err)

        if self._hjob:
            self._set_job_limits()
            self._setup_notifications()

    def _set_job_limits(self):
        """设置 JOB 限制标志

        - KILL_ON_JOB_CLOSE：关闭句柄时终止所有进程
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
            self._hjob, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )

    def _setup_notifications(self):
        """设置 Job Object 完成端口通知

        创建 IOCP 并与 Job 关联，启动后台线程监听实时进程事件。
        """
        if not self._hjob:
            return
        try:
            # 创建 I/O 完成端口
            self._iocp = _CreateIoCompletionPort(
                _INVALID_HANDLE_VALUE, None, None, 0,
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
                self._hjob, _JobObjectAssociateCompletionPortInformation,
                ctypes.byref(assoc), ctypes.sizeof(assoc),
            )
            if not ok:
                err = ctypes.get_last_error()
                _logger.warning("关联 Job→IOCP 失败: err=%d", err)
                _CloseHandle(self._iocp)
                self._iocp = None
                return

            _logger.info("Job IOCP 通知已启动")
            # 启动通知监听线程
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

        Job Object 通知通过 I/O 完成端口实时推送，消息类型：
          - NEW_PROCESS(3): lpOverlapped = PID
          - EXIT_PROCESS(4): lpOverlapped = exit code
          - ABNORMAL_EXIT_PROCESS(5): lpOverlapped = exit code
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

                if msg_type in (_JOB_OBJECT_MSG_NEW_PROCESS,):
                    pid = raw_value
                    _logger.info("Job NEW_PROCESS: pid=%d", pid)
                    self._push_notif(JobNotification(msg_type, pid=pid))
                elif msg_type in (_JOB_OBJECT_MSG_EXIT_PROCESS, _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS):
                    pid = raw_value
                    exit_code = self._get_exit_code(pid)
                    is_crash = msg_type == _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS
                    _logger.info("Job %s: pid=%d exit=%s",
                                 "ABNORMAL_EXIT" if is_crash else "EXIT", pid, exit_code)
                    self._push_notif(JobNotification(
                        msg_type, pid=pid, exit_code=exit_code,
                    ))
                else:
                    _logger.debug("Job 通知: type=%d data=%d", msg_type, raw_value)
            except Exception as e:
                if not self._stop_event.is_set():
                    _logger.warning("Job 通知循环异常: %s", e)
        _logger.info("Job 通知线程退出")

    def _push_notif(self, notif: JobNotification):
        """线程安全地添加通知"""
        with self._notif_lock:
            self._notifications.append(notif)

    def drain_notifications(self) -> List[JobNotification]:
        """取出所有待处理的通知（线程安全）

        Returns:
            未处理的通知列表。
        """
        with self._notif_lock:
            items = list(self._notifications)
            self._notifications.clear()
        return items

    def _get_exit_code(self, pid: int) -> Optional[int]:
        """查询指定 PID 的进程退出码"""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        try:
            hproc = K.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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

    def assign(self, hprocess: int) -> bool:
        """将进程分配到 Job Object（子进程自动继承）

        Args:
            hprocess: 进程句柄（必须有效，None 或 0 返回 False）。

        Returns:
            True 分配成功，False 句柄无效或分配失败。
        """
        if not self._hjob:
            return False
        if not hprocess:
            return False
        ok = _AssignProcessToJobObject(self._hjob, hprocess)
        if not ok:
            _logger.warning("AssignProcessToJobObject 失败: handle=%s err=%d",
                            hprocess, ctypes.get_last_error())
        return bool(ok)

    def query_process_list(self) -> List[int]:
        """获取 Job 内所有进程的 PID 列表"""
        if not self._hjob:
            return []
        try:
            # 使用正确的 JOBOBJECT_BASIC_PROCESS_ID_LIST 结构体，
            # 其内存布局为：
            #   [NumberOfAssignedProcesses] [NumberOfProcessIdsInList] [PID列表...]
            # 不能用扁平数组，否则会把前两个 DWORD 也当作 PID
            buf_size = ctypes.sizeof(JOBOBJECT_BASIC_PROCESS_ID_LIST)
            buf = ctypes.create_string_buffer(buf_size)
            info = JOBOBJECT_BASIC_PROCESS_ID_LIST.from_buffer(buf)
            ret_len = W.DWORD(0)
            ok = _QueryInformationJobObject(
                self._hjob, _JobObjectBasicProcessIdList,
                ctypes.byref(info), buf_size, ctypes.byref(ret_len),
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
        return len(self.query_process_list())

    def query_process_exit_code(self, pid: int) -> Optional[int]:
        """查询指定 PID 的进程退出码"""
        return self._get_exit_code(pid)

    def close(self):
        """关闭 Job Object 句柄，停止通知线程"""
        self._stop_event.set()
        # 退出通知线程
        if self._notif_thread and self._notif_thread.is_alive():
            # 发送退出信号到 IOCP 以唤醒 GetQueuedCompletionStatus
            if self._iocp:
                try:
                    _PostQueuedCompletionStatus(self._iocp, 0, None, None)
                except Exception:
                    pass
            self._notif_thread.join(2.0)
            if self._notif_thread.is_alive():
                _logger.warning("Job 通知线程未退出")
        # 关闭 IOCP
        if self._iocp:
            _CloseHandle(self._iocp)
            self._iocp = None
        # KILL_ON_JOB_CLOSE：关闭句柄时终止所有关联进程
        if self._hjob:
            _CloseHandle(self._hjob)
            self._hjob = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()


# ── Windows 常量 ──
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WAIT_TIMEOUT = 0x00000102
