"""pty/windows/job.py 单元测试 — JobNotification 类"""

import pytest

from src.pty.windows.job import JobNotification
from src.pty.windows.convars import (
    _JOB_OBJECT_MSG_NEW_PROCESS,
    _JOB_OBJECT_MSG_EXIT_PROCESS,
    _JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS,
)


class TestJobNotification:
    def test_is_spawn(self):
        n = JobNotification(_JOB_OBJECT_MSG_NEW_PROCESS, pid=123)
        assert n.is_spawn() is True
        assert n.is_exit() is False
        assert n.is_crash() is False

    def test_is_exit(self):
        n = JobNotification(_JOB_OBJECT_MSG_EXIT_PROCESS, pid=123, exit_code=0)
        assert n.is_exit() is True
        assert n.is_spawn() is False
        assert n.is_crash() is False

    def test_is_crash(self):
        n = JobNotification(_JOB_OBJECT_MSG_ABNORMAL_EXIT_PROCESS, pid=123, exit_code=1)
        assert n.is_crash() is True
        assert n.is_exit() is False
        assert n.is_spawn() is False

    def test_attributes(self):
        n = JobNotification(3, pid=456, exit_code=42)
        assert n.msg_type == 3
        assert n.pid == 456
        assert n.exit_code == 42
