"""沙箱 PTY 后端 —— SandboxPty（PseudoTerminal 端口实现）

pybind11 in-process 沙箱会话的完整终端语义：SandboxPty 用 wezterm-py Pty
创建 ConPTY（侧载 OpenConsole 宿主），把 HPCON 直传 win_sandbox_native
start_process(hpcon=...) —— 伪控制台句柄在同一进程内有效（in-process），
沙箱进程的 stdio 由该伪控制台驱动，回显/方向键/resize/Ctrl+C 与原生
ConPTY 完全一致。

数据通路：
  - 输出：wezterm Pty 内部 reader 线程（master 读端）→ 缓冲队列，read/drain 直读
  - 输入：wezterm Pty master 写端，write 直写（VT 字节流，conhost 解析）
  - resize：wezterm Pty resize（HPCON 信号管道）

沙箱场景下 wezterm Pty 不调用 spawn_command（由沙箱启动进程挂接 HPCON），
仅作为 ConPTY 的创建/IO/尺寸控制方。spawn 成功后立即 register_root
（与原生后端同一约定）。进程树终止 / 沙箱 shutdown 由 Session 经 tracker
控制。
"""

from typing import List, Optional

from ..process.base import ProcessTreeTracker
from ..pty.base import PseudoTerminal
from ..pty.wezterm_pty import _HAS_WEZTERM, pywezterm
from .manager import SandboxSessionManager
from ..logging import get_logger

_logger = get_logger("sandbox-pty")


class SandboxPty(PseudoTerminal):
    """win_sandbox_native（pybind11 in-process）沙箱 ConPTY 伪终端后端

    Args:
        command:  已拆分的命令参数列表。
        cols:     终端列数（ConPTY 初始宽度）。
        rows:     终端行数（ConPTY 初始高度）。
        cwd:      工作目录（透传 start_process working_dir；workspace-write
                  下沙箱内可写）。
        env:      额外环境变量。
        tracker:  SandboxProcessTreeTracker（spawn 后登记根进程）。
        manager:  沙箱会话管理器（工厂注入，与 tracker 共享实例）。
    """

    def __init__(
        self,
        command: List[str],
        cols: int = 80,
        rows: int = 24,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        encoding: Optional[str] = None,
        tracker: Optional[ProcessTreeTracker] = None,
        manager: Optional[SandboxSessionManager] = None,
    ):
        if manager is None:
            raise ValueError("SandboxPty 需要 manager（沙箱会话管理器）")
        if not _HAS_WEZTERM:
            raise RuntimeError("wezterm-py 不可用，无法创建沙箱 ConPTY")
        self._manager = manager
        self._tracker = tracker
        self._cols = cols
        self._rows = rows
        self._cwd = cwd
        self._child_pid: Optional[int] = None
        self._process_id: Optional[int] = None
        self._closed = False

        self._manager.start()
        # 用 wezterm-py Pty 创建 ConPTY（不 spawn，由沙箱挂接 HPCON）
        self._pty = pywezterm.Pty(cols=cols, rows=rows)
        hpcon = self._pty.hpcon()
        if not hpcon:
            self._pty.close()
            raise RuntimeError("wezterm Pty 未提供 HPCON")
        command_line = self._build_command_line(command, cwd, env)
        try:
            self._process_id, self._child_pid = self._manager.start_process(
                command_line,
                working_dir=cwd,
                env_vars=env,
                hpcon=hpcon,
            )
        except Exception:
            # 启动失败时释放已创建但未挂接的 ConPTY，避免泄漏
            self._pty.close()
            raise
        # spawn → 登记（紧耦合约定，与原生后端一致）
        if tracker is not None:
            tracker.register_root(self._child_pid)
        _logger.info(
            "SandboxPty 就绪: process_id=%s os_pid=%s %dx%d",
            self._process_id,
            self._child_pid,
            cols,
            rows,
        )

    @staticmethod
    def _build_command_line(
        command: List[str], cwd: Optional[str], env: Optional[dict]
    ) -> str:
        """把拆分的命令列表拼回命令行（沙箱需要完整命令行字符串）

        必须用 Windows 语义（双引号转义）：shlex.quote 是 POSIX 单引号，
        CreateProcessW 不认单引号，会破坏含 cmd 特殊字符（&& | 空格等）的命令。
        """
        if not command:
            raise ValueError("command 不能为空")
        import subprocess
        return subprocess.list2cmdline(command)

    # ── PseudoTerminal 端口 ──

    def get_type(self) -> str:
        return "win-sandbox"

    def read(self, n: int = 65536) -> bytes:
        """阻塞读取 ConPTY 输出（最多 n 字节）；EOF 返回 b""" ""
        if self._closed:
            return b""
        return self._pty.read(n)

    def drain(self, max_bytes: int = 65536) -> bytes:
        """非阻塞排空当前已就绪输出"""
        return self._pty.read(max_bytes, timeout=0.0)

    def write(self, data):
        """写入 ConPTY 输入管道（VT 字节流，conhost 解析）

        str 按 utf-8 编码（与原生 ConPTY write 语义一致）。
        """
        if isinstance(data, str):
            data = data.encode()
        self._pty.write(data)

    def resize(self, cols: int, rows: int):
        """调整 ConPTY 尺寸（conhost 内部 reflow + repaint，与原生一致）"""
        self._pty.resize(cols, rows)

    def close(self):
        """关闭 ConPTY（进程树终止与沙箱 shutdown 由 Session 经 tracker 控制）"""
        if self._closed:
            return
        self._closed = True
        self._pty.close()
        _logger.debug("SandboxPty close: process_id=%s", self._process_id)

    def fileno(self):
        """无文件描述符"""
        return

    def get_child_pid(self):
        """主进程 OS PID（start_process 返回）"""
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """主进程退出码（poll_exit 探测；None = 仍在运行）"""
        return self._manager.get_exit_code()

    def inject_mouse_event(
        self, x: int, y: int, button: int, is_release: bool, control_key_state: int = 0
    ) -> bool:
        """沙箱场景不支持鼠标注入（ConPTY 输入仅 VT 字节流）"""
        return False
