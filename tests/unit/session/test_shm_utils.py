"""session/shm_utils.py 单元测试"""

import pytest

from src.session.shm_utils import generate_auth_token


class TestGenerateAuthToken:
    def test_returns_string(self):
        token = generate_auth_token()
        assert isinstance(token, str)

    def test_length_is_64(self):
        token = generate_auth_token()
        assert len(token) == 64

    def test_is_hex(self):
        token = generate_auth_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_unique(self):
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2
