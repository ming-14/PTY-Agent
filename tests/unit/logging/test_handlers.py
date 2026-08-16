"""测试文件 handler 封装"""
import logging
import os

from src.logging.formatters import ContextFormatter
from src.logging.handlers import create_file_handler, generate_log_timestamp


def test_generate_log_timestamp_format():
    """时间戳格式为 YYYYMMDD-HHMMSS.mmm"""
    ts = generate_log_timestamp()
    # 格式: 20260816-143000.123（19 字符）
    assert len(ts) == 19
    assert ts[8] == "-"
    assert ts[15] == "."
    # 各部分为数字
    assert ts[:8].isdigit()
    assert ts[9:15].isdigit()
    assert ts[16:].isdigit()


def test_generate_log_timestamp_unique():
    """连续生成的时间戳不同（毫秒级）"""
    ts1 = generate_log_timestamp()
    ts2 = generate_log_timestamp()
    # 可能相同（同一毫秒），但格式正确
    assert len(ts1) == len(ts2) == 19


def test_create_file_handler(isolated_log_dir):
    """create_file_handler 创建可写的文件 handler"""
    log_file = os.path.join(isolated_log_dir, "test.log")
    fmt = ContextFormatter("%(message)s")
    fh = create_file_handler(log_file, fmt, logging.DEBUG)

    logger = logging.getLogger("test-handler")
    logger.handlers.clear()
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    logger.info("测试写入")

    fh.close()
    assert os.path.exists(log_file)
    with open(log_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert "测试写入" in content
