"""文件传输子包 —— file upload / download（daemon 侧）

结构：
- map.py：TransferMap（mtime 映射表，SQLite 持久化）
- judge.py：相同文件判定 + 传输计划（纯函数）
- transfer.py：daemon 侧 upload 接收 / download 发送（经 PluginIO 帧收发）

CLI 侧驱动（client_upload / client_download）与公共定义
（帧常量、清单条目结构、目录树扫描）在 src/transfer。
"""

from .judge import build_plan, classify
from .map import TransferMap, TransferRecord, get_default_map
from .transfer import daemon_download, daemon_upload

__all__ = [
    "build_plan",
    "classify",
    "TransferMap",
    "TransferRecord",
    "get_default_map",
    "daemon_upload",
    "daemon_download",
]
