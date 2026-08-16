"""测试日志装配入口"""
import logging
import os
import time

from src.logging import registry, setup
from src.logging.config import LoggingConfig


def test_assemble_creates_log_files(isolated_log_dir):
    """_assemble 创建日志文件并绑定 handler"""
    config = LoggingConfig(
        log_dir=isolated_log_dir,
        log_format="%(message)s",
        log_date_format="%Y-%m-%d %H:%M:%S",
        groups={"test": ["pty-setup-test"]},
        levels={"test": logging.DEBUG},
        queue_size=100,
    )
    files = setup._assemble(config)

    assert "test" in files
    assert os.path.exists(files["test"])

    # 写入日志
    logger = logging.getLogger("pty-setup-test")
    logger.info("装配测试")
    time.sleep(0.3)  # 等待异步写入

    setup.shutdown()

    with open(files["test"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "装配测试" in content


def test_assemble_all_none_levels(isolated_log_dir):
    """全部级别为 None 时挂 NullHandler"""
    config = LoggingConfig(
        log_dir=isolated_log_dir,
        log_format="%(message)s",
        log_date_format="%Y-%m-%d %H:%M:%S",
        groups={"test": ["pty-null-test"]},
        levels={"test": None},
    )
    files = setup._assemble(config)

    assert files == {}
    logger = logging.getLogger("pty-null-test")
    assert any(isinstance(h, logging.NullHandler) for h in logger.handlers)
    assert logger.propagate is False


def test_assemble_registers_loggers(isolated_log_dir):
    """_assemble 将所有 logger 名注册到注册表"""
    config = LoggingConfig(
        log_dir=isolated_log_dir,
        log_format="%(message)s",
        log_date_format="%Y-%m-%d %H:%M:%S",
        groups={"g1": ["pty-reg-a", "pty-reg-b"], "g2": ["pty-reg-c"]},
        levels={"g1": logging.DEBUG, "g2": logging.INFO},
        queue_size=100,
    )
    setup._assemble(config)

    assert registry.is_registered("pty-reg-a")
    assert registry.is_registered("pty-reg-b")
    assert registry.is_registered("pty-reg-c")
    assert registry.get_registered()["pty-reg-a"] == "g1"

    setup.shutdown()


def test_assemble_propagate_false(isolated_log_dir):
    """所有 logger propagate=False"""
    config = LoggingConfig(
        log_dir=isolated_log_dir,
        log_format="%(message)s",
        log_date_format="%Y-%m-%d %H:%M:%S",
        groups={"test": ["pty-prop-test"]},
        levels={"test": logging.DEBUG},
        queue_size=100,
    )
    setup._assemble(config)

    logger = logging.getLogger("pty-prop-test")
    assert logger.propagate is False

    setup.shutdown()


def test_shutdown_flushes_queue(isolated_log_dir):
    """shutdown 刷空队列，所有日志落盘"""
    config = LoggingConfig(
        log_dir=isolated_log_dir,
        log_format="%(message)s",
        log_date_format="%Y-%m-%d %H:%M:%S",
        groups={"test": ["pty-flush-test"]},
        levels={"test": logging.DEBUG},
        queue_size=100,
    )
    files = setup._assemble(config)

    logger = logging.getLogger("pty-flush-test")
    for i in range(50):
        logger.info("消息 %d", i)

    setup.shutdown()

    with open(files["test"], "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 50
