"""日志系统共享工具：按模块分组写独立日志文件 + 前一日日志自动 gzip 归档

供 daemon/lifecycle.py 与 client/lifecycle.py 复用：
- configure_log_files(): 按分组创建带毫秒时间戳的日志文件（无轮转），绑定各 logger
- archive_previous_day_logs(): 把本地 0 点前的 *.log 归档为 .log.gz 并删除原文件
- start_log_archiver(): 启动后台线程定期执行归档
"""

import gzip
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional


def generate_log_timestamp() -> str:
    """生成精确到毫秒的时间戳字符串，用于日志文件名"""
    now = datetime.now()
    return now.strftime("%Y%m%d-%H%M%S") + f".{now.microsecond // 1000:03d}"


def configure_log_files(
    log_dir: str,
    groups: Dict[str, List[str]],
    levels: Dict[str, Optional[int]],
    formatter: logging.Formatter,
) -> Dict[str, str]:
    """按日志分组创建独立的带时间戳日志文件，并绑定各 logger。

    Args:
        log_dir: 日志目录（不存在则自动创建）。
        groups: {分组名: [logger 名, ...]}，每个分组对应一个 {分组名}-{时间戳}.log 文件。
        levels: {分组名: logging 级别或 None}，级别为 None 的分组不写文件。
            全部为 None 时，所有 logger 挂 NullHandler（静默，不落盘）。
    Returns:
        {分组名: 日志文件绝对路径}（未启用的分组不在结果中）。
    """
    os.makedirs(log_dir, exist_ok=True)
    timestamp = generate_log_timestamp()
    files: Dict[str, str] = {}

    if not any(lv is not None for lv in levels.values()):
        for names in groups.values():
            for name in names:
                logger = logging.getLogger(name)
                logger.handlers.clear()
                logger.addHandler(logging.NullHandler())
                logger.setLevel(logging.WARNING)
                logger.propagate = False
        return files

    for group, names in groups.items():
        level = levels.get(group)
        if level is None or not names:
            continue
        log_file = os.path.join(log_dir, f"{group}-{timestamp}.log")
        fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        fh.setFormatter(formatter)
        fh.setLevel(level)
        for name in names:
            logger = logging.getLogger(name)
            logger.handlers.clear()
            logger.addHandler(fh)
            logger.setLevel(level)
            logger.propagate = False
        files[group] = log_file
    return files


def archive_previous_day_logs(log_dir: str) -> int:
    """把本地 0 点前的 *.log 归档为 .log.gz 并删除原文件。

    仅处理 .log（排除 .log.gz），按文件 mtime 判定归属日期。
    正在被写入/占用而无法删除的文件跳过，留待下次归档。

    Args:
        log_dir: 日志目录。
    Returns:
        本次归档成功的文件数。
    """
    if not os.path.isdir(log_dir):
        return 0
    today = datetime.now().date()
    count = 0
    for entry in os.scandir(log_dir):
        if not entry.is_file() or not entry.name.endswith(".log"):
            continue
        if datetime.fromtimestamp(entry.stat().st_mtime).date() >= today:
            continue
        src = entry.path
        dst = src + ".gz"
        try:
            with open(src, "rb") as fin, gzip.open(dst, "wb", compresslevel=9) as fout:
                shutil.copyfileobj(fin, fout)
            os.remove(src)
            count += 1
        except OSError:
            # 文件正被写入/占用，跳过留待下次；清理可能残留的半个归档文件
            try:
                os.remove(dst)
            except OSError:
                pass
    return count


def start_log_archiver(log_dir: str, interval: float) -> threading.Thread:
    """启动后台日志归档线程：定期把前一日日志 gzip 归档。

    Args:
        log_dir: 日志目录。
        interval: 检查间隔秒数。
    Returns:
        后台线程（daemon=True，随进程退出）。
    """

    def _loop():
        while True:
            try:
                archive_previous_day_logs(log_dir)
            except Exception:
                pass
            time.sleep(interval)

    thread = threading.Thread(target=_loop, daemon=True, name="pty-log-archiver")
    thread.start()
    return thread
