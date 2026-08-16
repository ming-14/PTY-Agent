"""测试异步队列核心"""
import logging
import os
import time

from src.logging._queue import (
    AsyncLogDispatcher,
    AsyncQueueHandler,
    RoutingQueueListener,
)
from src.logging.formatters import ContextFormatter
from src.logging.handlers import create_file_handler


def _make_record(msg="测试", name="pty-test", level=logging.INFO):
    return logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=42,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_queue_handler_emit():
    """AsyncQueueHandler 将记录放入队列"""
    import queue

    q = queue.Queue(maxsize=10)
    handler = AsyncQueueHandler(q)
    record = _make_record()
    handler.emit(record)
    assert q.qsize() == 1


def test_queue_handler_drop_oldest():
    """队列满时丢弃最旧记录"""
    import queue

    q = queue.Queue(maxsize=2)
    handler = AsyncQueueHandler(q)

    handler.emit(_make_record(msg="msg1"))
    handler.emit(_make_record(msg="msg2"))
    handler.emit(_make_record(msg="msg3"))  # 队列满，丢弃 msg1

    assert handler.dropped_count == 1
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert len(items) == 2
    assert items[0].msg == "msg2"
    assert items[1].msg == "msg3"


def test_routing_listener_dispatch(isolated_log_dir):
    """RoutingQueueListener 按 logger 名路由到对应 handler"""
    import queue

    q = queue.Queue(maxsize=10)
    fmt = ContextFormatter("%(message)s")

    log_file = os.path.join(isolated_log_dir, "test.log")
    fh = create_file_handler(log_file, fmt, logging.DEBUG)

    name_to_handler = {"pty-test": fh}
    listener = RoutingQueueListener(q, name_to_handler)
    listener.start()

    try:
        q.put(_make_record(msg="路由测试", name="pty-test"))
        time.sleep(0.2)  # 等待 listener 处理
    finally:
        listener.stop()

    fh.close()
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "路由测试" in content


def test_routing_listener_skip_unmapped():
    """未注册的 logger 名记录被丢弃"""
    import queue

    q = queue.Queue(maxsize=10)
    name_to_handler = {}
    listener = RoutingQueueListener(q, name_to_handler)
    listener.start()

    try:
        q.put(_make_record(msg="未注册", name="pty-unknown"))
        time.sleep(0.2)
    finally:
        listener.stop()

    # 不报错即通过


def test_dispatcher_full_flow(isolated_log_dir):
    """AsyncLogDispatcher 完整流程：创建 handler → 添加路由 → 启动 → 写入 → 停止"""
    fmt = ContextFormatter("%(message)s")
    log_file = os.path.join(isolated_log_dir, "dispatch.log")
    fh = create_file_handler(log_file, fmt, logging.DEBUG)

    dispatcher = AsyncLogDispatcher(queue_size=100)
    queue_handler = dispatcher.create_queue_handler()
    dispatcher.add_route(["pty-dispatch-test"], fh)
    dispatcher.start()

    try:
        logger = logging.getLogger("pty-dispatch-test")
        logger.handlers.clear()
        logger.addHandler(queue_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        logger.info("异步分发测试")
        time.sleep(0.3)  # 等待后台线程处理
    finally:
        dispatcher.stop()

    fh.close()
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "异步分发测试" in content


def test_dispatcher_dropped_count():
    """dispatcher.dropped_count 返回丢弃计数"""
    dispatcher = AsyncLogDispatcher(queue_size=2)
    qh = dispatcher.create_queue_handler()

    qh.emit(_make_record(msg="1"))
    qh.emit(_make_record(msg="2"))
    qh.emit(_make_record(msg="3"))

    assert dispatcher.dropped_count == 1
