"""VNC 进程管理器（winvnc.exe）。

独立实现，不复用 noVNC/src/process_manager.py，
避免 noVNC 模块对 `from config import ...` 的绝对导入污染主项目命名空间。

职责：
- 启停 UltraVNC winvnc.exe（VNC 服务端，监听 vnc_port）
- 自动生成随机 VNC 密码并写入 ultravnc.ini
- 通过 Windows Job Object 绑定子进程，确保随父进程退出

WebSocket→VNC TCP 代理由守护进程的 /vnc/websockify 端点直接实现
（见 server.py），统一到单一端口。
"""

import ctypes
import logging
import secrets
import socket
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .password_loader import load_vnc_password_module

_logger = logging.getLogger("pty-vnc")

# ── Windows Job Object 绑定（确保子进程随父进程退出）──
# 64 位系统上 HANDLE 是 64 位指针，ctypes.windll 默认 restype=c_int（32 位）
# 会截断句柄值导致后续调用收到错误句柄，必须显式设置函数签名。
if sys.platform == "win32":
    import ctypes.wintypes

    _kernel32 = ctypes.windll.kernel32

    # 设置函数签名，确保 HANDLE 等 64 位指针类型正确传递
    _kernel32.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _kernel32.SetInformationJobObject.restype = ctypes.wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.wintypes.DWORD,
    ]
    _kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [
        ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = ctypes.wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [
        ctypes.wintypes.HANDLE, ctypes.wintypes.HANDLE,
    ]
    _kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", ctypes.wintypes.LARGE_INTEGER),
            ("LimitFlags", ctypes.wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.wintypes.DWORD),
            ("SchedulingClass", ctypes.wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _global_job_handle = None

    def _ensure_job_object():
        """创建或返回全局 Job Object，子进程绑定后随父进程退出。"""
        global _global_job_handle
        if _global_job_handle is not None:
            return _global_job_handle
        job = _kernel32.CreateJobObjectW(None, None)
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        size = ctypes.sizeof(info)
        if not _kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), size
        ):
            raise ctypes.WinError()
        _global_job_handle = job
        return job

    def _assign_to_job(pid: int) -> None:
        """把子进程绑定到全局 Job Object。

        AssignProcessToJobObject 要求进程句柄具有
        PROCESS_SET_QUOTA (0x0100) | PROCESS_TERMINATE (0x0001) 访问权限。
        绑定失败时记录错误日志（不影响 VNC 启动，但守护进程意外退出可能残留）。
        """
        try:
            job = _ensure_job_object()
            # PROCESS_SET_QUOTA | PROCESS_TERMINATE（MSDN 要求的访问权限）
            handle = _kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
            if not handle:
                _logger.error("assign job: OpenProcess failed pid=%s err=%s",
                              pid, ctypes.WinError())
                return
            try:
                ok = _kernel32.AssignProcessToJobObject(job, handle)
                if ok:
                    _logger.info("assign job: OK pid=%s", pid)
                else:
                    _logger.error("assign job: AssignProcessToJobObject failed pid=%s err=%s",
                                  pid, ctypes.WinError())
            finally:
                _kernel32.CloseHandle(handle)
        except Exception as e:
            _logger.warning("assign job object failed pid=%s: %s", pid, e)


@dataclass
class VncProcessConfig:
    """VNC 进程配置。

    Attributes:
        winvnc_exe: winvnc.exe 绝对路径。
        ultravnc_dir: UltraVNC 目录（ultravnc.ini 写入位置）。
        vnc_src_dir: VNC 模块 src 目录（用于定位 vnc_password.py 模块）。
        logs_dir: 日志目录。
        vnc_port: VNC 服务端口（默认自动分配）。
        password: VNC 密码，None 时自动生成 12 位随机串。
        remove_wallpaper: 是否移除壁纸（节省带宽）。
    """
    winvnc_exe: Path
    ultravnc_dir: Path
    vnc_src_dir: Path
    logs_dir: Path
    vnc_port: Optional[int] = None
    password: Optional[str] = None
    remove_wallpaper: bool = False


@dataclass
class _ProcessState:
    """进程运行时状态。"""
    vnc_process: Optional[subprocess.Popen] = None
    vnc_log_file = None
    vnc_port: Optional[int] = None
    password: Optional[str] = None


class VncProcessManager:
    """winvnc.exe 进程管理器。

    单例语义：进程全局唯一，多个 web 用户共享同一 VNC 会话。
    """

    def __init__(self, config: VncProcessConfig):
        self.config = config
        self._state = _ProcessState()
        self._vnc_password_module = None  # 懒加载

    # ── 公开 API ──

    def is_winvnc_available(self) -> bool:
        """winvnc.exe 是否存在。"""
        return self.config.winvnc_exe.exists()

    def start_all(self) -> dict:
        """启动 winvnc.exe，返回连接信息。

        Returns:
            {vnc_port, password, vnc_pid}

        Raises:
            RuntimeError: 启动失败。
        """
        # 已运行则直接返回
        if self._is_vnc_running():
            _logger.info("VNC already running, reuse existing")
            return self._build_connection_info()

        # 解析端口与密码
        self._resolve_ports()
        self._resolve_password()

        # 写 ultravnc.ini
        self._write_ultravnc_ini()

        # 启动 winvnc
        vnc_pid = self._start_vnc()

        return {
            "vnc_port": self._state.vnc_port,
            "password": self._state.password,
            "vnc_pid": vnc_pid,
        }

    def stop_all(self) -> None:
        """停止 winvnc。"""
        self._stop_vnc()

    def get_status(self) -> dict:
        """返回当前运行状态。"""
        vnc_running = self._is_vnc_running()
        return {
            "running": vnc_running,
            "vnc_running": vnc_running,
            "vnc_port": self._state.vnc_port if vnc_running else None,
            "vnc_pid": self._state.vnc_process.pid if vnc_running else None,
            "password": self._state.password if vnc_running else None,
        }

    def get_connection_info(self) -> Optional[dict]:
        """返回前端连接所需信息（未运行返回 None）。

        返回 vnc_port 供守护进程的 /vnc/websockify 代理端点使用。
        """
        if not self._is_vnc_running():
            return None
        return {
            "vnc_port": self._state.vnc_port,
            "password": self._state.password,
        }

    # ── 内部实现 ──

    def _is_vnc_running(self) -> bool:
        p = self._state.vnc_process
        if p is None:
            return False
        if p.poll() is not None:
            # 进程已退出，清理引用
            self._state.vnc_process = None
            if self._state.vnc_log_file:
                try:
                    self._state.vnc_log_file.close()
                except Exception:
                    pass
                self._state.vnc_log_file = None
            return False
        return True

    @staticmethod
    def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, port)) == 0
        except (OSError, socket.error):
            return False

    @staticmethod
    def _find_free_port(start: int, end: int) -> int:
        """在 [start, end) 范围内寻找空闲端口。"""
        import random
        ports = list(range(start, end))
        random.shuffle(ports)
        for port in ports:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", port))
                    return port
            except OSError:
                continue
        raise RuntimeError(f"no available port in [{start}, {end})")

    def _resolve_ports(self) -> None:
        if self.config.vnc_port and self.config.vnc_port > 0:
            self._state.vnc_port = self.config.vnc_port
        else:
            self._state.vnc_port = self._find_free_port(5900, 6000)

    def _resolve_password(self) -> None:
        if self.config.password:
            self._state.password = self.config.password
            return
        # 生成 12 位随机密码（字母+数字）
        chars = string.ascii_letters + string.digits
        self._state.password = "".join(secrets.choice(chars) for _ in range(12))
        _logger.info("generated VNC password (length=12)")

    def _ensure_vnc_password_module(self):
        """懒加载 noVNC 的 vnc_password 模块。"""
        if self._vnc_password_module is None:
            # vnc_password.py 在 src/vnc/src/ 目录
            self._vnc_password_module = load_vnc_password_module(self.config.vnc_src_dir)
        return self._vnc_password_module

    def _write_ultravnc_ini(self) -> None:
        """通过 noVNC 的 vnc_password 模块写 ultravnc.ini。"""
        mod = self._ensure_vnc_password_module()
        mod.write_ultravnc_ini(
            self.config.ultravnc_dir,
            password=self._state.password or "123456",
            port=self._state.vnc_port,
            remove_wallpaper=self.config.remove_wallpaper,
        )

    def _kill_stale_winvnc(self) -> None:
        """清理可能残留的 winvnc 进程（调用 winvnc -kill）。"""
        try:
            subprocess.run(
                [str(self.config.winvnc_exe), "-kill"],
                cwd=str(self.config.ultravnc_dir),
                timeout=10,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            _logger.debug("kill stale winvnc: %s", e)

    def _taskkill(self, pid: int) -> bool:
        if sys.platform != "win32":
            return False
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _start_vnc(self) -> int:
        """启动 winvnc.exe，返回 pid。"""
        if not self.config.winvnc_exe.exists():
            raise RuntimeError(f"winvnc.exe not found: {self.config.winvnc_exe}")

        if self._is_port_open("127.0.0.1", self._state.vnc_port):
            raise RuntimeError(f"VNC port {self._state.vnc_port} already in use")

        self._kill_stale_winvnc()

        cmd = [str(self.config.winvnc_exe), "-run"]
        _logger.info("starting VNC: %s", " ".join(cmd))

        self.config.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.config.logs_dir / "winvnc.log"
        self._state.vnc_log_file = open(log_path, "a", encoding="utf-8")

        self._state.vnc_process = subprocess.Popen(
            cmd,
            stdout=self._state.vnc_log_file,
            stderr=subprocess.STDOUT,
            cwd=str(self.config.ultravnc_dir),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else 0,
        )

        if sys.platform == "win32":
            _assign_to_job(self._state.vnc_process.pid)

        # 等待端口就绪（最多 30 秒）
        for _ in range(30):
            time.sleep(1)
            if self._state.vnc_process.poll() is not None:
                raise RuntimeError(
                    f"winvnc exited unexpectedly, return code: {self._state.vnc_process.returncode}"
                )
            if self._is_port_open("127.0.0.1", self._state.vnc_port):
                _logger.info(
                    "VNC started, port=%d pid=%d",
                    self._state.vnc_port, self._state.vnc_process.pid,
                )
                return self._state.vnc_process.pid

        # 超时，回滚
        self._stop_vnc()
        raise RuntimeError("VNC startup timed out (30s)")

    def _stop_vnc(self) -> None:
        p = self._state.vnc_process
        if p is None or p.poll() is not None:
            self._state.vnc_process = None
            if self._state.vnc_log_file:
                try:
                    self._state.vnc_log_file.close()
                except Exception:
                    pass
                self._state.vnc_log_file = None
            return

        try:
            # 优先用 winvnc -kill 优雅停止
            subprocess.run(
                [str(self.config.winvnc_exe), "-kill"],
                cwd=str(self.config.ultravnc_dir),
                timeout=10,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            for _ in range(10):
                if p.poll() is not None:
                    break
                time.sleep(0.5)
            if p.poll() is None:
                self._taskkill(p.pid)
                for _ in range(5):
                    if p.poll() is not None:
                        break
                    time.sleep(0.5)
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception as e:
            _logger.warning("stop VNC failed: %s", e)
            if p.poll() is None:
                self._taskkill(p.pid)

        self._state.vnc_process = None
        if self._state.vnc_log_file:
            try:
                self._state.vnc_log_file.close()
            except Exception:
                pass
            self._state.vnc_log_file = None
        _logger.info("VNC stopped")

    def _build_connection_info(self) -> dict:
        return {
            "vnc_port": self._state.vnc_port,
            "password": self._state.password,
            "vnc_pid": self._state.vnc_process.pid if self._state.vnc_process else None,
        }
