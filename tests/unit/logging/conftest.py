"""日志系统测试 — 公共 fixture

isolated_log_dir: 临时日志目录，测试后自动清理
reset_registry: 每个测试前重置 logger 注册表
clean_loggers: 每个测试后清理所有 pty-* logger 的 handler
"""
import logging

import pytest

from src.logging import registry


@pytest.fixture
def isolated_log_dir(tmp_path):
    """临时日志目录"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    yield str(log_dir)


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前重置 logger 注册表"""
    registry.reset()
    yield
    registry.reset()


@pytest.fixture(autouse=True)
def clean_loggers():
    """每个测试后清理所有 pty-* / process-* / sandbox-* / screenshare* logger 的 handler"""
    yield
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.PlaceHolder):
            continue
        if (
            name.startswith("pty-")
            or name.startswith("process-")
            or name.startswith("sandbox-")
            or name.startswith("screenshare")
            or name.startswith("pty-logging")
        ):
            logger.handlers.clear()
            logger.propagate = True
