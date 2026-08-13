"""读写状态机 —— 记录文件 readTime/writeTime，检测外部修改冲突

- view（file read）成功后 record_read 刷新 readTime
- write/edit 前检查：modTime > readTime → 文件已被外部修改，拒绝
- 写成功后 record_write + record_read 双刷新（工具自身知道最新内容）

writeTime 本期只记录不消费（预留"写后未读再写"检测）。

进程内存储：daemon 重启即失效（不落盘避免过度工程）。
线程安全：daemon 每连接一线程，读写互斥。
"""

import threading
import time
from typing import Dict, Optional

from .paths import normalize_key


class FileRecordStore:
    """文件记录表：path → (readTime, writeTime)，统一时间戳单位（秒）"""

    def __init__(self) -> None:
        self._records: Dict[str, Dict[str, float]] = {}
        self._lock = threading.Lock()

    def record_read(self, path: str, at: Optional[float] = None) -> None:
        """记录一次读取（file read 成功后调用）

        Args:
            at: 记录时刻（秒）；默认当前时间。
                工具写成功后应传文件自身的 mtime 作为基准，
                避免 python 时钟与文件系统时钟的微秒级偏差造成
                紧邻的自写操作被误判为外部冲突。
        """
        key = normalize_key(path)
        with self._lock:
            rec = self._records.setdefault(key, {"read": 0.0, "write": 0.0})
            rec["read"] = at if at is not None else time.time()

    def record_write(self, path: str) -> None:
        """记录一次写入（本期无消费点，预留给写后检测）"""
        key = normalize_key(path)
        with self._lock:
            rec = self._records.setdefault(key, {"read": 0.0, "write": 0.0})
            rec["write"] = time.time()

    def last_read(self, path: str) -> Optional[float]:
        """最近一次读取时间戳；从未读过返回 None"""
        key = normalize_key(path)
        with self._lock:
            rec = self._records.get(key)
            if rec is None or rec["read"] == 0.0:
                return None
            return rec["read"]

    def reset(self) -> None:
        """清空全部记录（测试与 daemon 重置用）"""
        with self._lock:
            self._records.clear()


_default_store = FileRecordStore()


def get_default_store() -> FileRecordStore:
    """daemon 级单例状态机

    RequestHandler 每连接重建，状态记录必须跨连接共享，
    故用模块级单例。
    """
    return _default_store