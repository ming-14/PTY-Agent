"""文件 handler 封装 — 带毫秒时间戳的日志文件名"""

import logging
from datetime import datetime, timezone


def generate_log_timestamp() -> str:
    """生成精确到毫秒的本地时间戳字符串，用于日志文件名

    格式: YYYYMMDD-HHMMSS.mmm
    """
    now = datetime.now(tz=timezone.utc).astimezone()
    return now.strftime("%Y%m%d-%H%M%S") + f".{now.microsecond // 1000:03d}"


def create_file_handler(
    log_file: str, formatter: logging.Formatter, level: int
) -> logging.FileHandler:
    """创建 UTF-8 追加模式的文件 handler"""
    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(formatter)
    fh.setLevel(level)
    return fh
