"""subprocess 后端 — 纯子进程双管道捕获（非 PTY）

子进程模式（exec --subprocess / mode="subprocess"）的后端实现：
用 subprocess.Popen 直接捕获子进程的 stdout/stderr（无伪终端语义），
因此：
- 无终端回显（输入不回显，行编辑由子进程自行处理）
- 无 resize 能力（调用即报错）
- 输入通过写 stdin 管道（无终端编码/行尾处理）

进程树追踪复用 process/ 包（Windows Job Object / Unix process group），
spawn 后通过 register_root 登记根进程，与 pty 后端同一约定。

双流读取模型：
- 后台一个 reader 线程用 select 同时监听 stdout/stderr 两个管道，
  读到数据分别写入 stdout_q / stderr_q（线程安全队列），EOF 写入 None 哨兵
- 每写入一批数据触发 _data_event（threading.Event），通知 Session reader 消费
- Session 子进程 reader loop：等待 _data_event → read()/read_stderr() 非阻塞取走
  各自队列已就绪数据 → 分别 append 到 stdout/stderr 两个 OutputBuffer
- 两个管道均 EOF 且进程结束 → is_eof() 为 True，reader 退出

编码统一为字节流，由 Session 的 EncodingDetector 解码。
"""

import queue
import subprocess
import threading
from typing import List, Optional

from ..config.common import IS_WINDOWS
from .base import PseudoTerminal
from ..logging import get_logger

_logger = get_logger("pty-subprocess")


class SubprocessPseudoTerminal(PseudoTerminal):
    """纯子进程后端（Popen 双管道捕获 stdout/stderr）

    Args:
        command:  已拆分的命令参数列表。
        cwd:      子进程工作目录。
        env:      额外环境变量（合并到 os.environ）。
        tracker:  进程树追踪器（spawn 后登记根进程）。
    """

    def __init__(
        self,
        command: List[str],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        tracker=None,
    ):
        self._tracker = tracker
        self._child_pid: Optional[int] = None
        self._exit_code: Optional[int] = None
        self._returncode_lock = threading.Lock()

        # 环境：合并
        env_dict = _os_environ_copy()
        if isinstance(env, dict):
            env_dict.update(env)

        try:
            popen_kwargs: dict = {
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "cwd": cwd,
                "env": env_dict,
                "bufsize": 0,
            }
            if IS_WINDOWS:
                # Windows 下避免创建子进程时弹出控制台窗口
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                # Unix 下创建独立进程组：tracker 按 pgid 追踪/终止整棵进程树
                # （无新会话则子进程落入父进程组，killpg 会误伤守护进程自身）
                popen_kwargs["start_new_session"] = True
            self._proc = subprocess.Popen(command, **popen_kwargs)
        except Exception as e:
            raise RuntimeError(f"子进程启动失败: {e}") from e

        self._child_pid = self._proc.pid or None
        _logger.info(
            "SubprocessPseudoTerminal: spawned pid=%s cmd=%r", self._child_pid, command
        )

        # 进程树登记（同一约定：spawn 后立即 register_root）
        if self._tracker and self._child_pid:
            try:
                handle = getattr(self._proc, "_handle", None)
                self._tracker.register_root(self._child_pid, handle)
            except Exception as e:
                _logger.warning("register_root 失败: pid=%d err=%s", self._child_pid, e)

        # 双流队列 + 数据事件
        self._stdout_q: "queue.Queue" = queue.Queue()
        self._stderr_q: "queue.Queue" = queue.Queue()
        self._data_event = threading.Event()
        self._closed = threading.Event()
        self._eof_out = False
        self._eof_err = False
        self._eof_lock = threading.Lock()
        self._threads = []
        # Windows 的 select 不支持管道，用两个线程分别阻塞读 stdout/stderr
        self._start_reader(self._proc.stdout, self._stdout_q, "stdout", "_eof_out")
        self._start_reader(self._proc.stderr, self._stderr_q, "stderr", "_eof_err")

    def _start_reader(self, stream, q: "queue.Queue", name: str, eof_attr: str) -> None:
        """启动单个管道阻塞读取线程：读到数据入队，EOF 写 None 哨兵并标记"""

        def _loop():
            try:
                while not self._closed.is_set():
                    data = stream.read(65536)
                    if not data:
                        break
                    q.put(data)
                    self._data_event.set()
            except Exception as e:
                _logger.debug("读取 %s 管道异常: %s", name, e)
            finally:
                with self._eof_lock:
                    setattr(self, eof_attr, True)
                q.put(None)  # EOF 哨兵
                self._data_event.set()

        t = threading.Thread(
            target=_loop, daemon=True, name=f"pty-subproc-{name}-{self._child_pid}"
        )
        t.start()
        self._threads.append(t)

    def _all_eof(self) -> bool:
        with self._eof_lock:
            return self._eof_out and self._eof_err

    # ── 对外接口 ────────────────────────────────────────────────

    @property
    def data_event(self) -> threading.Event:
        """有新数据（或 EOF）时触发的事件，供 Session reader 等待"""
        return self._data_event

    def is_eof(self) -> bool:
        """两个管道是否均已 EOF（reader 据此退出）"""
        return self._all_eof()

    def get_type(self) -> str:
        return "subprocess"

    def read(self, n: int = 65536) -> bytes:
        """非阻塞读取 stdout 已就绪数据（EOF 哨兵消费后返回 b""）"""
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self._stdout_q.get_nowait()
            except queue.Empty:
                break
            if chunk is None:  # EOF 哨兵
                break
            data.extend(chunk)
        return bytes(data)

    def read_stderr(self, n: int = 65536) -> bytes:
        """非阻塞读取 stderr 已就绪数据（EOF 哨兵消费后返回 b""）"""
        data = bytearray()
        while len(data) < n:
            try:
                chunk = self._stderr_q.get_nowait()
            except queue.Empty:
                break
            if chunk is None:  # EOF 哨兵
                break
            data.extend(chunk)
        return bytes(data)

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空 stdout 队列所有已就绪（read 已覆盖，返回空）"""
        return b""

    def write(self, data):
        """写入 stdin 管道"""
        if isinstance(data, str):
            data = data.encode()
        if self._closed.is_set():
            return
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except Exception as e:
            _logger.debug("写入 stdin 失败: %s", e)
            raise RuntimeError(f"写入 stdin 失败: {e}") from e

    def send_signal(self, sig):
        """向子进程发送信号（如 SIGINT）"""
        if self._closed.is_set():
            return
        try:
            self._proc.send_signal(sig)
        except Exception as e:
            _logger.debug("发送信号失败: %s", e)
            raise RuntimeError(f"发送信号失败: {e}") from e

    def resize(self, cols: int, rows: int):
        """子进程模式不支持 resize"""
        raise RuntimeError("子进程模式不支持 resize（无终端）")

    def close(self):
        """终止子进程并清理资源（幂等）"""
        if self._closed.is_set():
            return
        self._closed.set()
        self._data_event.set()  # 唤醒可能的等待者
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=3)
        except Exception:
            pass
        try:
            for q in (self._stdout_q, self._stderr_q):
                try:
                    q.put_nowait(None)
                except queue.Full:
                    pass
        except Exception:
            pass
        try:
            for t in self._threads:
                try:
                    t.join(timeout=1)
                except Exception:
                    pass
        except Exception:
            pass
        _logger.info("close: pid=%s", self._child_pid)

    def fileno(self):
        """返回 None：内部 select 线程，无需外部 select"""
        return

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码；仍在运行则返回 None"""
        with self._returncode_lock:
            if self._exit_code is not None:
                return self._exit_code
            code = self._proc.poll()
            if code is not None:
                self._exit_code = code
            return code


def _os_environ_copy() -> dict:
    import os

    return os.environ.copy()