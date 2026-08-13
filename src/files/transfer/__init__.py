"""src/files/transfer 包 —— 文件传输业务（file upload/download）

结构：
- common.py：帧常量再导出、传输错误、清单条目结构
- map.py：TransferMap（mtime 映射表，SQLite 持久化）
- judge.py：相同文件判定 + 传输计划（纯函数）
- scan.py：目录树扫描（scp -r 语义全量传输）
- client_upload.py / client_download.py：CLI 侧驱动
- daemon_upload.py / daemon_download.py：daemon 侧驱动
"""

from .common import TransferError, TransferTimeoutError, TransferAbortedError
from .map import TransferMap, TransferRecord, get_default_map
from .judge import build_plan, classify
from .scan import scan_tree
from .client_upload import upload, ProgressReporter
from .client_download import download

__all__ = [
    "TransferError",
    "TransferTimeoutError",
    "TransferAbortedError",
    "TransferMap",
    "TransferRecord",
    "get_default_map",
    "build_plan",
    "classify",
    "scan_tree",
    "upload",
    "download",
    "ProgressReporter",
]