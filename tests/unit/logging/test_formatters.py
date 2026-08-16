"""测试文本格式器"""
import logging

from src.logging import bind, unbind
from src.logging.formatters import ContextFormatter


def _make_record(msg="测试消息", level=logging.INFO, name="pty-test"):
    """构造测试 LogRecord"""
    return logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=42,
        msg=msg,
        args=(),
        exc_info=None,
    )


def test_format_without_context():
    """无上下文时格式与标准 Formatter 一致"""
    fmt = ContextFormatter(
        "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    record = _make_record()
    text = fmt.format(record)
    assert "测试消息" in text
    assert "[sid=" not in text


def test_format_with_context():
    """有上下文时在 message 前注入 [key=val ...]"""
    fmt = ContextFormatter(
        "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    token = bind(session_id="abc123", connection_id="conn1")
    try:
        record = _make_record()
        text = fmt.format(record)
        assert "[session_id=abc123 connection_id=conn1]" in text
        assert "测试消息" in text
    finally:
        unbind(token)


def test_format_with_exception():
    """有异常信息时格式化 traceback"""
    fmt = ContextFormatter("%(message)s")
    try:
        raise ValueError("测试异常")
    except ValueError:
        import sys

        record = logging.LogRecord(
            name="pty-test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=42,
            msg="错误",
            args=(),
            exc_info=sys.exc_info(),
        )
    text = fmt.format(record)
    assert "错误" in text
    assert "ValueError" in text
    assert "测试异常" in text


def test_format_with_context_and_exception():
    """有上下文和异常时同时格式化"""
    fmt = ContextFormatter("%(message)s")
    token = bind(session_id="err-session")
    try:
        try:
            raise RuntimeError("崩溃")
        except RuntimeError:
            import sys

            record = logging.LogRecord(
                name="pty-test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=42,
                msg="处理失败",
                args=(),
                exc_info=sys.exc_info(),
            )
        text = fmt.format(record)
        assert "[session_id=err-session]" in text
        assert "RuntimeError" in text
    finally:
        unbind(token)
