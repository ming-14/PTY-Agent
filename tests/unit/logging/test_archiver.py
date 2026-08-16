"""测试日志归档"""
import gzip
import os
import time

from src.logging.archiver import LogArchiver


def _create_old_log(log_dir, name="old.log", days_old=1):
    """创建一个 N 天前的日志文件"""
    path = os.path.join(log_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write("old log content\n")
    # 设置 mtime 为 N 天前
    old_time = time.time() - days_old * 86400
    os.utime(path, (old_time, old_time))
    return path


def test_archive_old_log(isolated_log_dir):
    """前一日日志被 gzip 归档"""
    old_log = _create_old_log(isolated_log_dir, days_old=1)

    archiver = LogArchiver(isolated_log_dir, interval=600)
    count = archiver.archive_once()

    assert count == 1
    assert not os.path.exists(old_log)
    assert os.path.exists(old_log + ".gz")

    # 验证压缩内容
    with gzip.open(old_log + ".gz", "rt", encoding="utf-8") as f:
        assert f.read() == "old log content\n"


def test_archive_skips_today_log(isolated_log_dir):
    """今日日志不归档"""
    today_log = os.path.join(isolated_log_dir, "today.log")
    with open(today_log, "w", encoding="utf-8") as f:
        f.write("today content\n")

    archiver = LogArchiver(isolated_log_dir, interval=600)
    count = archiver.archive_once()

    assert count == 0
    assert os.path.exists(today_log)
    assert not os.path.exists(today_log + ".gz")


def test_archive_skips_non_log_files(isolated_log_dir):
    """非 .log 文件不归档"""
    _create_old_log(isolated_log_dir, "data.txt", days_old=1)
    archiver = LogArchiver(isolated_log_dir, interval=600)
    count = archiver.archive_once()
    assert count == 0


def test_archive_multiple_files(isolated_log_dir):
    """归档多个旧日志文件"""
    _create_old_log(isolated_log_dir, "a.log", days_old=1)
    _create_old_log(isolated_log_dir, "b.log", days_old=2)
    _create_old_log(isolated_log_dir, "c.log", days_old=3)

    archiver = LogArchiver(isolated_log_dir, interval=600)
    count = archiver.archive_once()

    assert count == 3
    assert os.path.exists(os.path.join(isolated_log_dir, "a.log.gz"))
    assert os.path.exists(os.path.join(isolated_log_dir, "b.log.gz"))
    assert os.path.exists(os.path.join(isolated_log_dir, "c.log.gz"))


def test_archive_empty_dir(isolated_log_dir):
    """空目录归档返回 0"""
    archiver = LogArchiver(isolated_log_dir, interval=600)
    assert archiver.archive_once() == 0


def test_archive_nonexistent_dir():
    """不存在的目录归档返回 0"""
    archiver = LogArchiver("/nonexistent/path", interval=600)
    assert archiver.archive_once() == 0


def test_archiver_start_stop(isolated_log_dir):
    """归档线程可正常启动和停止"""
    archiver = LogArchiver(isolated_log_dir, interval=0.1)
    archiver.start()
    assert archiver._thread is not None
    assert archiver._thread.is_alive()

    archiver.stop()
    assert archiver._thread is None
