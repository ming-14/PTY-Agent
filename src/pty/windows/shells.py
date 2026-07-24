"""Windows 可用 Shell 检测

提供系统可用 shell 的探测与格式化能力，供 web shell provider 与 daemon 启动日志使用。
本模块仅依赖标准库 shutil，与 subprocess 管道模式无关。
"""

import shutil
import logging

_logger = logging.getLogger("pty-windows")

# Shell 名称 → 可执行文件规格
# - None 表示该 shell 由系统直接解析（如 cmd 始终位于 System32）
# - 字符串表示需要通过 shutil.which 解析的可执行文件名
_SHELL_MAP = {
    "cmd": None,
    "powershell": "powershell.exe",
    "pwsh": "pwsh",
}


def detect_available_shells() -> dict:
    """检测系统可用的 shell

    Returns:
        dict: shell 名称 → 可执行文件路径（不可用时为 None）。
    """
    result = {}
    cmd_path = shutil.which("cmd.exe")
    result["cmd"] = cmd_path or "cmd.exe"
    for name, spec in _SHELL_MAP.items():
        if name == "cmd":
            continue
        if spec is None:
            continue
        exe = spec[0] if isinstance(spec, list) else spec
        result[name] = shutil.which(exe)
    return result


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


def resolve_default_shell() -> str:
    """解析默认 shell 名称

    优先返回 powershell（若可用），否则回退 cmd。
    """
    if shutil.which("powershell.exe"):
        return "powershell"
    return "cmd"
