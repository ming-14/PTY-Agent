"""日志归档 — gzip 压缩前一日日志

改进点：
- 归档异常不再静默，写告警日志 + 计数
- 提供 stop() 接口，支持优雅关闭前最后一次归档
- 归档线程用 Event 控制循环，可及时停止
"""

import gzip
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from typing import Optional

_logger = logging.getLogger("pty-logging-archiver")


class LogArchiver:
    """日志归档器：后台线程定期 gzip 压缩本地 0 点前的 *.log

    按文件 mtime 判定归属日期，正被占用（OSError）的文件跳过留待下次。
    """

    def __init__(self, log_dir: str, interval: float):
        self._log_dir = log_dir
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """启动归档后台线程"""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="pty-log-archiver"
        )
        self._thread.start()

    def stop(self) -> None:
        """停止归档线程并执行最后一次归档"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
            self._thread = None
        self.archive_once()

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self.archive_once()
            self._stop_event.wait(self._interval)

    def archive_once(self) -> int:
        """把本地 0 点前的 *.log 归档为 .log.gz 并删除原文件

        Returns:
            成功归档的文件数
        """
        if not os.path.isdir(self._log_dir):
            return 0
        today = datetime.now(tz=timezone.utc).astimezone().date()
        count = 0
        for entry in os.scandir(self._log_dir):
            if not entry.is_file() or not entry.name.endswith(".log"):
                continue
            if (
                datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
                .astimezone()
                .date()
                >= today
            ):
                continue
            src = entry.path
            dst = src + ".gz"
            try:
                with open(src, "rb") as fin, gzip.open(
                    dst, "wb", compresslevel=6
                ) as fout:
                    shutil.copyfileobj(fin, fout)
                os.remove(src)
                count += 1
            except OSError as e:
                _logger.warning("归档日志文件失败 %s: %s", src, e)
                try:
                    os.remove(dst)
                except OSError:
                    pass
        return count
