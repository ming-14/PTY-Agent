"""统一的消息格式 helper — 面向用户的消息文案的唯一产出

所有面向用户的「消息」（非内容类：信息/状态/成功/失败/错误/提示）统一为
``(PTY-Agent message: <text>)`` 并写到 stderr，供 presenter（CLI 命令结果呈现）
与 client/daemonctl（守护进程控制提示）共用，保证单一格式来源、不各自实现前缀。

「内容」（程序输出/快照/表格/正文/config）不经过本模块，仍走 stdout 原样。
"""

import sys
from typing import Optional, TextIO


_MSG_PREFIX = "PTY-Agent message"


def fmt_message(text: str) -> str:
    """把文本包装为统一消息格式 ``(PTY-Agent message: <text>)``"""
    return f"({_MSG_PREFIX}: {text})"


def emit_message(text: str, file: Optional[TextIO] = None, end: str = "\n") -> None:
    """输出统一消息到 stderr（默认），带前缀；写失败静默（不打断命令结果）"""
    try:
        stream = file or sys.stderr
        stream.write(fmt_message(text) + end)
        stream.flush()
    except Exception:
        pass