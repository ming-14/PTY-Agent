"""wezterm-py PTY 后端 — 跨平台统一 PseudoTerminal 适配（wezterm-pty）

所有平台统一由 wezterm-py 提供伪终端：Windows 用 OpenConsole 宿主
（侧载 conpty.dll + OpenConsole.exe），Unix 用 portable-pty 的 openpty；
创建、spawn、读写、resize 全部由 pywezterm 完成。

关键行为：
- 输入编码由 wezterm-term 模式感知完成（Terminal.key_down/mouse），直接写 pty。
- spawn 后立即 register_root(pid, process_handle)，与 tracker 同一约定
  （Windows: AssignProcessToJobObject；Unix: getpgid 捕获）。
- Windows 侧 ConPTY 输出恒 UTF-8：默认不设置 PYTHONIOENCODING
  （Python 3.6+ _WindowsConsoleIO 输出 UTF-8 正确）；仅显式非 UTF-8
  编码（如 gbk）时启用传统字节模式；Unix 侧无此语义，不设这些变量。
"""

import os
import shutil
import sys
from typing import List, Optional

from ..config.common import DEFAULT_COLS, DEFAULT_ROWS, IS_WINDOWS
from .base import PseudoTerminal
from ..logging import get_logger

# 加载 vendored pywezterm（bin/pywezterm，BUILD.ps1 编译产出），先注入 sys.path
_here = os.path.dirname(os.path.abspath(__file__))
_BIN_DIR = os.path.normpath(os.path.join(_here, *([os.path.pardir] * 2), "bin"))
_WEZTERM_PY_DIR = os.path.join(_BIN_DIR, "pywezterm")
if os.path.isdir(_WEZTERM_PY_DIR) and _WEZTERM_PY_DIR not in sys.path:
    sys.path.insert(0, _WEZTERM_PY_DIR)

try:
    import pywezterm

    _HAS_WEZTERM = True
except ImportError:
    _HAS_WEZTERM = False
    pywezterm = None  # type: ignore[assignment]

_logger = get_logger("pty-wezterm")


def _check_command_exists(command: list) -> None:
    """spawn 前预检命令可执行性（可执行文件缺失时给出准确错误）

    只检查命令首元素是否可解析为可执行文件（绝对路径直接校验存在性，
    否则在 PATH 中查找）；找不到抛 FileNotFoundError（消息含命令名），
    让上层区分"命令不可执行"与"PTY 后端创建失败"。
    """
    if not command:
        return
    exe = command[0]
    if not exe:
        return
    # 绝对/相对路径：直接校验文件存在
    if os.sep in exe or (IS_WINDOWS and "/" in exe):
        if not os.path.isfile(exe):
            raise FileNotFoundError(f"可执行文件不存在: {exe!r}")
        return
    # PATH 查找（Windows 下含 PATHEXT 扩展名尝试）
    found = shutil.which(exe)
    if found is None:
        raise FileNotFoundError(
            f"命令不存在或不在 PATH 中: {exe!r}（请检查命令名或 PATH）"
        )


class WeztermPseudoTerminal(PseudoTerminal):
    """wezterm-py 伪终端后端（跨平台）

    Args:
        command:  已拆分的命令参数列表。
        cols:     终端列数（伪控制台初始宽度）。
        rows:     终端行数（伪控制台初始高度）。
        cwd:      子进程工作目录。
        env:      额外环境变量（合并到 os.environ）。
        encoding: Windows 终端输出编码（非 UTF-8 时设置 PYTHONIOENCODING）。
        tracker:  进程树追踪器（spawn 后登记根进程）。
    """

    def __init__(
        self,
        command: List[str],
        cols: int = DEFAULT_COLS,
        rows: int = DEFAULT_ROWS,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        encoding: Optional[str] = None,
        tracker=None,
    ):
        if not _HAS_WEZTERM:
            raise RuntimeError("wezterm-py 不可用，无法创建 wezterm PTY 后端")
        self._cols = cols
        self._rows = rows
        self._tracker = tracker
        self._child_pid: Optional[int] = None
        # 缓解：构造前快照 ConPTY 宿主（OpenConsole），构造后 diff 定位本次新增，
        # 并入命令树 Job（KILL_ON_JOB_CLOSE），宿主异常死亡时连带清理，规避 conhost
        # 对死 server 管道不自旋退出时的残留。
        # 注意：OpenConsole 在 Pty 构造（CreatePseudoConsole）时即创建，须在构造
        # 之前快照，否则 diff 为空。
        before_cons: set = set()
        if IS_WINDOWS and tracker is not None:
            try:
                from ..process.windows.api import enum_console_host_children

                before_cons = set(enum_console_host_children(os.getpid()))
            except Exception:
                before_cons = set()

        self._pty = pywezterm.Pty(cols=cols, rows=rows)

        # 环境：合并 + Windows 编码语义（对齐原生 ConPTY 后端）
        env_dict = os.environ.copy()
        if isinstance(env, dict):
            env_dict.update(env)
        if encoding and IS_WINDOWS:
            enc_norm = encoding.lower().replace("-", "").replace("_", "")
            if enc_norm not in ("utf8", "utf"):
                env_dict.setdefault("PYTHONIOENCODING", encoding)
                env_dict.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")
        # 终端颜色语义：TERM/COLORTERM 缺省时补支持颜色的值（程序据此检测
        # 颜色支持）；NO_COLOR 在 daemon 入口层剥离（Rust 侧环境块 = 进程
        # 环境 + 覆盖，Python 侧无法删除基础环境变量）。
        env_dict.setdefault("TERM", "xterm-256color")
        env_dict.setdefault("COLORTERM", "truecolor")

        try:
            # cmd /c <单字符串命令> 形态（--shell cmd 包装产物）：命令字符串
            # 原样作为 raw_cmdline 传给 CreateProcess——cmd.exe 自行解析引号，
            # argv 序列化的 \" 转义（C 运行时规则）会变成 cmd 的字面反斜杠
            raw_cmdline = None
            if (
                IS_WINDOWS
                and len(command) == 3
                and command[1] == "/c"
                and isinstance(command[2], str)
            ):
                # 完整命令行原样传给 CreateProcess，绕过 argv 引号序列化
                # raw_cmdline 首 token 被底层作为 lpApplicationName 直接传给
                # CreateProcessW（不做 PATH 搜索），裸命令名（如 "cmd"）会失败；
                # 用 which 解析为绝对路径
                exe = shutil.which(command[0]) or command[0]
                if " " in exe:
                    exe = f'"{exe}"'
                raw_cmdline = " ".join([exe] + command[1:])
            # 命令可执行性预检：可执行文件不存在时（如 PATH 中找不到），
            # 在 Python 侧抛出 FileNotFoundError 给出准确错误，避免底层
            # CreateProcess 失败被误报为"PTY 后端创建失败"
            _check_command_exists(command)
            # cwd 缺省统一为守护进程当前目录：显式传入避免后端默认行为
            # 差异（wezterm 底层 cwd 缺失时回退 USERPROFILE，与子进程模式
            # 继承 daemon cwd 不一致）
            if cwd is None:
                cwd = os.getcwd()
            pid, handle = self._pty.spawn(
                command, cwd=cwd, env=env_dict, raw_cmdline=raw_cmdline
            )
        except FileNotFoundError:
            # 命令不存在：保留 OSError 语义透传，让上层给出"命令不可执行"
            self._pty.close()
            raise
        except Exception as e:
            self._pty.close()
            raise RuntimeError(f"wezterm spawn 失败: {e}") from e

        self._child_pid = pid or None
        _logger.info("WeztermPseudoTerminal: spawned pid=%s cmd=%r", pid, command)

        # 同一代码路径内登记 root 到 tracker（进程树归属 tracker）
        if self._tracker and pid:
            try:
                self._tracker.register_root(pid, handle)
            except Exception as e:
                _logger.warning("register_root 失败: pid=%d err=%s", pid, e)

        # 把本次新增的 OpenConsole 并入 tracker Job（缓解，见上）
        if IS_WINDOWS and self._tracker is not None:
            try:
                from ..process.windows.api import enum_console_host_children

                after_cons = set(enum_console_host_children(os.getpid()))
                for cpid in after_cons - before_cons:
                    try:
                        if self._tracker.assign_extra_process(cpid):
                            # 登记为宿主进程：自然结束检测排除（见 tracker）
                            self._tracker.register_host_pid(cpid)
                            _logger.debug("OpenConsole %d 并入 Job", cpid)
                    except Exception as e:
                        _logger.warning(
                            "OpenConsole %d 并入 Job 失败: %s", cpid, e
                        )
            except Exception as e:
                _logger.warning("枚举 OpenConsole 并入 Job 失败: %s", e)

    # ── PseudoTerminal 接口 ──────────────────────────────────────

    def get_type(self) -> str:
        """返回 PTY 后端类型标识"""
        return "wezterm"

    def read(self, n: int = 65536) -> bytes:
        """阻塞读取输出（最多 n 字节）；EOF 返回 b""" ""
        return self._pty.read(n)

    def drain(self, max_bytes: int = 65536) -> bytes:
        """排空当前已就绪的输出（非阻塞，timeout=0）"""
        return self._pty.read(max_bytes, timeout=0.0)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        if b"\x1b[" in data:
            _logger.debug("write: %d bytes VT=%r", len(data), data[:200])
        else:
            _logger.debug("write: %d bytes", len(data))
        self._pty.write(data)

    def resize(self, cols: int, rows: int):
        self._pty.resize(cols, rows)

    def close(self):
        """关闭伪终端（终止子进程 + 取消 reader 阻塞读 + 释放底层资源，幂等）"""
        _logger.info("close: pid=%s", self._child_pid)
        self._pty.close()

    def fileno(self):
        """返回 None：wezterm-pty 内部 reader 线程 + 缓冲队列，无需 select"""
        return

    def get_child_pid(self):
        return self._child_pid

    def get_exit_code(self) -> Optional[int]:
        """获取子进程退出码；仍在运行则返回 None"""
        try:
            return self._pty.try_wait()
        except Exception:
            return None

    # ── wezterm 特有能力 ─────────────────────────────────────────

    def hpcon(self):
        """底层 ConPTY HPCON 句柄（Windows 沙箱外部 spawn 用；无则 None）"""
        try:
            return self._pty.hpcon()
        except Exception:
            return None

    @property
    def pty(self):
        """底层 pywezterm.Pty（供 session 输入编码等访问）"""
        return self._pty
