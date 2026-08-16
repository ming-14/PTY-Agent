"""异步队列核心 — 业务线程零阻塞的日志分发

设计：
- 业务线程挂 AsyncQueueHandler，emit() 仅 queue.put_nowait()（O(1)）
- 后台单线程 RoutingQueueListener 从 queue 取记录，按 logger 名路由到对应文件 handler
- 队列满时 drop_oldest：丢弃最旧记录，首次丢弃时 stderr 告警一次
- traceback 在业务线程格式化为文本（避免跨线程引用帧对象），exc_info 清空

线程模型：整个进程一个 pty-log-writer 后台线程处理所有分组的日志写入。
"""

import logging
import queue
import sys
import threading
import traceback
from logging.handlers import QueueHandler
from typing import Dict, List, Optional


class AsyncQueueHandler(QueueHandler):
    """业务线程挂的 handler：仅入队，不格式化不 IO

    队列满时丢弃最旧记录（drop_oldest），防止背压阻塞业务线程。
    """

    def __init__(self, q: queue.Queue):
        super().__init__(q)
        self.dropped_count = 0
        self._warned = False

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """在业务线程预处理：格式化 traceback 为文本，清空 exc_info 引用

        不调用 self.format()，避免业务线程做完整格式化工作。
        traceback 格式化相对轻量，且必须在此处做（exc_info 引用帧对象不能跨线程）。
        """
        if record.exc_info:
            record.exc_text = "".join(
                traceback.format_exception(*record.exc_info)
            )
            record.exc_info = None
        return record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.prepare(record)
            self.queue.put_nowait(record)
        except queue.Full:
            self._drop_oldest(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def _drop_oldest(self, record: logging.LogRecord) -> None:
        """队列满时丢弃最旧记录，腾位放入新记录"""
        try:
            self.queue.get_nowait()
            self.queue.put_nowait(record)
            self.dropped_count += 1
            if not self._warned:
                self._warned = True
                sys.stderr.write(
                    "[pty-logging] queue full, dropping oldest log records\n"
                )
        except (queue.Empty, queue.Full):
            pass


class RoutingQueueListener:
    """按 logger 名路由到对应 handler 的后台监听线程

    单线程处理所有分组的日志写入，O(1) 路由（dict 查找）。
    """

    def __init__(self, q: queue.Queue, name_to_handler: Dict[str, logging.Handler]):
        self._queue = q
        self._name_to_handler = name_to_handler
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="pty-log-writer"
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """停止监听线程，刷空队列后退出"""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                record = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            self._dispatch(record)
        # 刷空队列剩余记录
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                break
            self._dispatch(record)

    def _dispatch(self, record: logging.LogRecord) -> None:
        handler = self._name_to_handler.get(record.name)
        if handler is not None:
            handler.handle(record)


class AsyncLogDispatcher:
    """异步日志分发器：管理 queue + handler 路由 + 监听线程"""

    def __init__(self, queue_size: int = 8192):
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._listener: Optional[RoutingQueueListener] = None
        self._name_to_handler: Dict[str, logging.Handler] = {}
        self._queue_handler: Optional[AsyncQueueHandler] = None

    def create_queue_handler(self) -> AsyncQueueHandler:
        """创建业务 logger 挂的 QueueHandler（共享同一 queue）"""
        if self._queue_handler is None:
            self._queue_handler = AsyncQueueHandler(self._queue)
        return self._queue_handler

    def add_route(self, names: List[str], handler: logging.Handler) -> None:
        """注册 logger 名 → 文件 handler 的路由"""
        for name in names:
            self._name_to_handler[name] = handler

    def start(self) -> None:
        self._listener = RoutingQueueListener(self._queue, self._name_to_handler)
        self._listener.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._listener is not None:
            self._listener.stop(timeout=timeout)
            self._listener = None

    @property
    def dropped_count(self) -> int:
        if self._queue_handler is not None:
            return self._queue_handler.dropped_count
        return 0
