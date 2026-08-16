"""session/shm_utils.py 单元测试"""

import pytest

from src.ipc.shm import generate_auth_token


class TestGenerateAuthToken:
    def test_returns_string(self):
        token = generate_auth_token()
        assert isinstance(token, str)

    def test_length_is_62(self):
        # 31 字节 hex（62 字符）≤ SHM 数据区 63 字节（seqlock 布局：1 字节 seq）
        token = generate_auth_token()
        assert len(token) == 62

    def test_is_hex(self):
        token = generate_auth_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_unique(self):
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2
