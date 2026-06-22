"""shm_utils 共享内存工具单元测试

测试认证令牌生成、端口读写、令牌读写。
"""

import pytest

from src.session.shm_utils import generate_auth_token, read_port_from_shm, write_port_to_shm


class TestGenerateAuthToken:
    """generate_auth_token 测试"""

    def test_returns_non_empty_string(self):
        """生成非空令牌"""
        token = generate_auth_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_is_hex(self):
        """令牌为 hex 编码"""
        token = generate_auth_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_length(self):
        """32 字节随机 → 64 字符 hex"""
        token = generate_auth_token()
        assert len(token) == 64

    def test_unique_tokens(self):
        """每次生成不同令牌"""
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2


class TestPortShm:
    """端口共享内存读写测试"""

    def test_read_default_when_empty(self):
        """无共享内存时返回默认端口"""
        port = read_port_from_shm()
        assert isinstance(port, int)
        assert port > 0

    def test_write_and_read_port(self):
        """写入后读取端口"""
        import mmap
        from src.config import MMAP_NAME, MMAP_SIZE, IS_WINDOWS

        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        shm = write_port_to_shm(19876)
        try:
            port = read_port_from_shm()
            assert port == 19876
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass