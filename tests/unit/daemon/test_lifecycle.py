"""daemon 控制 + 进程探测单元测试"""

import os
import pytest

from src.common.process import pid_exists


class TestPidExists:
    def test_current_pid_exists(self):
        assert pid_exists(os.getpid()) is True

    def test_nonexistent_pid(self):
        assert pid_exists(9999999) is False