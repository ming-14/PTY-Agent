"""文件传输公共定义 —— 帧常量再导出、传输错误、条目结构

依赖方向：src/transfer/ 是两端（CLI/daemon 插件）共用的核心层，
依赖 src/protocol/transfer.py（帧 IO）与 config/，不反向依赖 daemon/client。
"""

from ...protocol.transfer import (  # noqa: F401  （帧类型向上再导出，业务层统一从此取）
    FT_ABORT,
    FT_ACK,
    FT_DATA,
    FT_FILE_END,
    FT_MANIFEST,
    FT_PLAN,
    TransferProtocolError,
)

# 清单条目：kind = "file" | "dir"；dir 条目 size/mtime 为 0（不参与判定/传输）
ENTRY_FILE = "file"
ENTRY_DIR = "dir"


class TransferError(Exception):
    """传输业务错误（无异常链时 message 即用户提示）"""


class TransferTimeoutError(TransferError):
    """--timeout 总时限超时，传输中止并清理"""


class TransferAbortedError(TransferError):
    """对端发来 ABORT 或连接中断，传输中止"""


def entry(relpath: str, kind: str, size: int = 0, mtime: float = 0.0) -> dict:
    """构造清单条目"""
    return {"relpath": relpath, "kind": kind, "size": size, "mtime": mtime}
