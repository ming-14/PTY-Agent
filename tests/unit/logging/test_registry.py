"""测试 logger 名注册表"""
import logging

from src.logging import registry


def test_register_group():
    """注册分组后，所有 logger 名可通过 is_registered 查到"""
    registry.register_group("test-group", ["pty-test-a", "pty-test-b"])
    assert registry.is_registered("pty-test-a")
    assert registry.is_registered("pty-test-b")
    assert not registry.is_registered("pty-test-c")


def test_get_registered():
    """get_registered 返回 name -> group 映射"""
    registry.register_group("group1", ["pty-a"])
    registry.register_group("group2", ["pty-b", "pty-c"])
    registered = registry.get_registered()
    assert registered["pty-a"] == "group1"
    assert registered["pty-b"] == "group2"
    assert registered["pty-c"] == "group2"


def test_get_logger_returns_standard_logger():
    """get_logger 返回标准 logging.Logger 实例"""
    logger = registry.get_logger("pty-test")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "pty-test"


def test_get_logger_warns_on_unregistered_after_config_loaded():
    """配置加载后，未注册的 logger 名触发告警"""
    registry.mark_config_loaded()
    # 捕获告警日志
    warn_logger = logging.getLogger("pty-logging-registry")
    handler = _CaptureHandler()
    warn_logger.addHandler(handler)
    warn_logger.setLevel(logging.WARNING)

    registry.get_logger("pty-unregistered-test")

    assert len(handler.records) == 1
    assert "pty-unregistered-test" in handler.records[0].getMessage()


def test_get_logger_no_warn_before_config_loaded():
    """配置加载前，未注册的 logger 名不触发告警"""
    warn_logger = logging.getLogger("pty-logging-registry")
    handler = _CaptureHandler()
    warn_logger.addHandler(handler)
    warn_logger.setLevel(logging.WARNING)

    registry.get_logger("pty-no-warn-test")

    assert len(handler.records) == 0


def test_get_logger_warns_only_once():
    """同一未注册 logger 名只告警一次"""
    registry.mark_config_loaded()
    warn_logger = logging.getLogger("pty-logging-registry")
    handler = _CaptureHandler()
    warn_logger.addHandler(handler)
    warn_logger.setLevel(logging.WARNING)

    registry.get_logger("pty-duplicate-test")
    registry.get_logger("pty-duplicate-test")
    registry.get_logger("pty-duplicate-test")

    assert len(handler.records) == 1


class _CaptureHandler(logging.Handler):
    """捕获日志记录的测试 handler"""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)
