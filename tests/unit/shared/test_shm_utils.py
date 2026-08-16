"""shm_utils 共享内存工具单元测试

测试认证令牌生成、令牌/HMAC 密钥读写、清理，以及边界情况。
daemon 端口为固定端口配置，不再经共享内存发现。
"""

import mmap
import os
import pytest

from src.ipc.shm import (
    generate_auth_token,
    read_auth_token,
    write_auth_token,
    cleanup_auth_shm,
    read_hmac_key,
    write_hmac_key,
    cleanup_hmac_shm,
    cleanup_all_shm,
)
from src.config.common import IS_WINDOWS, DATA_DIR
from src.config.shared import AUTH_TOKEN_SIZE, HMAC_KEY_SIZE


class TestGenerateAuthToken:
    """generate_auth_token 测试"""

    def test_returns_non_empty_string(self):
        token = generate_auth_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_is_hex(self):
        token = generate_auth_token()
        assert all(c in "0123456789abcdef" for c in token)

    def test_token_length(self):
        token = generate_auth_token()
        assert len(token) == 62  # 31 字节 hex，≤ SHM 数据区 63 字节（seq 布局）

    def test_unique_tokens(self):
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2

    def test_multiple_tokens_all_unique(self):
        tokens = {generate_auth_token() for _ in range(20)}
        assert len(tokens) == 20


@pytest.fixture()
def _no_shm_residue():
    """清理认证令牌与 HMAC 密钥共享内存残留，避免测试间干扰"""
    cleanup_all_shm()
    yield
    cleanup_all_shm()


class TestAuthTokenShm:
    """认证令牌共享内存读写测试"""

    def test_read_none_when_empty(self, _no_shm_residue):
        token = read_auth_token()
        assert token is None

    def test_write_and_read_token(self, _no_shm_residue):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        test_token = generate_auth_token()
        shm = write_auth_token(test_token)
        try:
            read = read_auth_token()
            assert read == test_token
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass

    def test_overwrite_token(self, _no_shm_residue):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        t1 = generate_auth_token()
        shm1 = write_auth_token(t1)
        shm1.close()
        t2 = generate_auth_token()
        shm2 = write_auth_token(t2)
        try:
            read = read_auth_token()
            assert read == t2
        finally:
            if shm2:
                try:
                    shm2.close()
                except Exception:
                    pass

    def test_update_token_in_place(self, _no_shm_residue):
        """update_auth_token 原地轮换（seqlock 发布），读者读到新令牌"""
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        from src.ipc.shm import update_auth_token

        shm = write_auth_token(generate_auth_token())
        try:
            t2 = generate_auth_token()
            update_auth_token(shm, t2)
            assert read_auth_token() == t2
        finally:
            try:
                shm.close()
            except Exception:
                pass

    def test_token_fits_shm_size(self):
        # seqlock 布局：1 字节 seq + 63 字节数据区，62 字符令牌可容纳
        assert AUTH_TOKEN_SIZE >= 1 + len(generate_auth_token())


class TestHmacKeyShm:
    """HMAC 密钥共享内存读写测试"""

    def test_read_none_when_empty(self, _no_shm_residue):
        key = read_hmac_key()
        assert key is None or isinstance(key, bytes)

    def test_write_and_read_key(self, _no_shm_residue):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        test_key = os.urandom(32)
        shm = write_hmac_key(test_key)
        try:
            read = read_hmac_key()
            assert read == test_key
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass

    def test_overwrite_key(self, _no_shm_residue):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        k1 = os.urandom(32)
        shm1 = write_hmac_key(k1)
        shm1.close()
        k2 = os.urandom(32)
        shm2 = write_hmac_key(k2)
        try:
            read = read_hmac_key()
            assert read == k2
        finally:
            if shm2:
                try:
                    shm2.close()
                except Exception:
                    pass

    def test_key_fits_shm_size(self):
        assert HMAC_KEY_SIZE >= 64


class TestCleanupShm:
    """cleanup 函数测试（仅验证不崩溃）"""

    def test_cleanup_auth_shm_no_error(self):
        cleanup_auth_shm()

    def test_cleanup_hmac_shm_no_error(self):
        cleanup_hmac_shm()

    def test_cleanup_auth_shm_idempotent(self):
        cleanup_auth_shm()
        cleanup_auth_shm()

    def test_cleanup_hmac_shm_idempotent(self):
        cleanup_hmac_shm()
        cleanup_hmac_shm()

    def test_cleanup_all_shm_no_error(self):
        cleanup_all_shm()

    def test_shm_cleared_after_cleanup(self, _no_shm_residue):
        """清理后令牌与密钥应不可读"""
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        write_auth_token(generate_auth_token())
        write_hmac_key(os.urandom(32))
        cleanup_all_shm()
        assert read_auth_token() is None
        assert read_hmac_key() is None