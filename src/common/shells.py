"""跨侧共享 Shell 探测与包装 —— 系统可用 shell 的检测/格式化/命令包装

供 daemon 启动日志（daemon/lifecycle.py）、web shell provider、
client 侧 daemonctl（src/client/daemonctl）输出环境信息使用；
命令包装（wrap_command）供 exec --shell / set-default shell 使用。
本模块仅依赖标准库 shutil/shlex/subprocess，与子进程管道模式无关。
"""

import shlex
import shutil

from ..config.common import IS_WINDOWS

if IS_WINDOWS:
    # Shell 名称 → 可执行文件规格
    # - None 表示该 shell 由系统直接解析（如 cmd 始终位于 System32）
    # - 字符串表示需要通过 shutil.which 解析的可执行文件名
    _SHELL_MAP = {
        "cmd": None,
        "powershell": "powershell.exe",   # Windows PowerShell 5.1
        "pwsh": "pwsh",                   # PowerShell 7+
        "bash": "bash.exe",               # Git for Windows / WSL 启动器
        "python": "python.exe",           # Python 交互式 REPL
        "node": "node.exe",               # Node.js REPL
    }
else:
    # Shell 名称 → [可执行文件, 参数]（-c/-e 为交互执行参数）
    _SHELL_MAP = {
        "bash": ["bash", "-c"],
        "sh": ["sh", "-c"],
        "zsh": ["zsh", "-c"],
        "fish": ["fish", "-c"],
        "dash": ["dash", "-c"],
        "ash": ["ash", "-c"],
        "ksh": ["ksh", "-c"],
        "tcsh": ["tcsh", "-c"],
        "csh": ["csh", "-c"],
        "python": ["python3", "-c"],      # Python 交互式 REPL
        "node": ["node", "-e"],           # Node.js REPL
    }


def _posix_join(command: list) -> str:
    """POSIX 引号规则重组命令字符串（复杂引号/空格/特殊字符保真）"""
    return shlex.join(command)


def _win_join(command: list) -> str:
    """Windows 命令行规则重组命令字符串（cmd/powershell 解析兼容）"""
    join = getattr(shlex, "join", None)  # 兼容无 list2cmdline 的环境（非 Windows）
    try:
        import subprocess

        return subprocess.list2cmdline(command)
    except AttributeError:
        return join(command) if join else " ".join(command)


# 支持包装的 shell → (启动参数, 命令字符串构造器)
# - POSIX 系/解释器：shlex.join 重组（引号/空格/操作符保真），如 bash -c "…"
# - cmd/powershell：Windows 命令行规则（list2cmdline），如 cmd /c "…"
_SHELL_WRAP = {
    "bash": ("-c", _posix_join),
    "sh": ("-c", _posix_join),
    "zsh": ("-c", _posix_join),
    "fish": ("-c", _posix_join),
    "dash": ("-c", _posix_join),
    "ash": ("-c", _posix_join),
    "ksh": ("-c", _posix_join),
    "tcsh": ("-c", _posix_join),
    "csh": ("-c", _posix_join),
    "python": ("-c", _posix_join),
    "node": ("-e", _posix_join),
    "cmd": ("/c", _win_join),
    "powershell": ("-Command", _win_join),
    "pwsh": ("-Command", _win_join),
}


def detect_available_shells() -> dict:
    """检测系统可用的 shell

    Returns:
        dict: shell 名称 → 可执行文件路径（不可用时为 None）。
    """
    result = {}
    if IS_WINDOWS:
        cmd_path = shutil.which("cmd.exe")
        result["cmd"] = cmd_path or "cmd.exe"
    for name, spec in _SHELL_MAP.items():
        if IS_WINDOWS and name == "cmd":
            continue
        if spec is None:
            continue
        exe = spec[0] if isinstance(spec, list) else spec
        result[name] = shutil.which(exe)
    return result


def wrap_command(command, shell: str) -> list:
    """用指定 shell 包装命令（exec --shell / set-default shell）

    命令可为原始字符串或已拆分的参数列表：
    - str：原样作为单条命令字符串传给 shell（操作符/复杂引号由 shell 按语义
      解析，不做任何重组，避免拆分-重组丢失语义，如 && 被引成字面量）。
    - list：按目标 shell 的引号规则重组为单条命令字符串（POSIX 系用 shlex.join；
      cmd/pwsh 用 Windows 命令行规则；Windows 上的 python/node 解释器同样用
      Windows 规则——其 -c/-e 参数由 CreateProcess 解析，POSIX 引号会变成字面量）。

    Args:
        command: 命令（str 或 List[str]）。
        shell:   shell 名称（见 _SHELL_WRAP 键，如 bash/cmd/pwsh）。

    Returns:
        包装后的命令列表，如 ["bash", "-c", "echo a && echo b"]。

    Raises:
        ValueError: shell 不受支持，或 PATH 中找不到该 shell 的可执行文件。
    """
    if shell not in _SHELL_WRAP:
        raise ValueError(
            f"不支持的 shell: {shell!r}（可用: {', '.join(sorted(_SHELL_WRAP))}）"
        )
    shell_path = detect_available_shells().get(shell)
    if not shell_path:
        raise ValueError(f"找不到 shell: {shell!r}（PATH 中不可用）")
    flag, joiner = _SHELL_WRAP[shell]
    if isinstance(command, str):
        # 原始字符串直接传给 shell，不做重组
        if shell == "cmd":
            # cmd.exe 不识别 pywezterm 命令行序列化的 \" 转义（那是 C 运行时规则，
            # cmd 会保留反斜杠输出 \"）；用 cmd 的 caret 转义（^"）表示字面引号，
            # 使含双引号的命令在 cmd 下语义正确
            command = command.replace('"', '^"')
        return [shell_path, flag, command]
    # Windows 上的解释器（python/node）由 CreateProcess 直接解析参数，
    # POSIX 引号（shlex.join）会变成代码字面量导致静默无输出，须用 Windows 规则
    if IS_WINDOWS and shell in ("python", "node"):
        joiner = _win_join
    return [shell_path, flag, joiner(command)]


def format_shell_info() -> str:
    """格式化 shell 信息字符串（用于日志）"""
    shells = detect_available_shells()
    parts = []
    for name, path in shells.items():
        if path:
            parts.append(f"{name} ({path})")
        else:
            parts.append(f"{name} (unavailable)")
    return "Shells: " + ", ".join(parts)
