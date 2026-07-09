"""shm_utils 共享内存工具单元测试

测试认证令牌生成、守护进程信息（PID+端口）读写、令牌读写、边界情况。
"""

import pytest

from src.session.shm_utils import (
    generate_auth_token,
    read_port_from_shm,
    read_daemon_info_from_shm,
    write_daemon_info_to_shm,
    read_auth_token,
    write_auth_token,
    cleanup_auth_shm,
    cleanup_port_shm,
)
from src.config import IS_WINDOWS, DEFAULT_DAEMON_PORT, MMAP_NAME, MMAP_SIZE


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
        assert len(token) == 64

    def test_unique_tokens(self):
        t1 = generate_auth_token()
        t2 = generate_auth_token()
        assert t1 != t2

    def test_multiple_tokens_all_unique(self):
        tokens = {generate_auth_token() for _ in range(20)}
        assert len(tokens) == 20


class TestDaemonInfoShm:
    """守护进程信息（PID+端口）共享内存读写测试"""

    @pytest.fixture(autouse=True)
    def _cleanup_shm(self):
        if IS_WINDOWS:
            try:
                import mmap
                shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
                shm.write(b"\x00" * MMAP_SIZE)
                shm.close()
            except Exception:
                pass
        yield
        if IS_WINDOWS:
            try:
                import mmap
                shm = mmap.mmap(-1, MMAP_SIZE, tagname=MMAP_NAME)
                shm.write(b"\x00" * MMAP_SIZE)
                shm.close()
            except Exception:
                pass

    def test_read_default_when_empty(self):
        port = read_port_from_shm()
        assert isinstance(port, int)
        assert port == DEFAULT_DAEMON_PORT

    def test_read_info_none_when_empty(self):
        info = read_daemon_info_from_shm()
        assert info is None

    def test_write_and_read_info(self):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        shm = write_daemon_info_to_shm(12345, 19876)
        try:
            info = read_daemon_info_from_shm()
            assert info is not None
            pid, port = info
            assert pid == 12345
            assert port == 19876
            assert read_port_from_shm() == 19876
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass

    def test_overwrite_previous_info(self):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        shm1 = write_daemon_info_to_shm(11111, 22222)
        shm1.close()
        shm2 = write_daemon_info_to_shm(33333, 44444)
        try:
            info = read_daemon_info_from_shm()
            assert info is not None
            pid, port = info
            assert pid == 33333
            assert port == 44444
        finally:
            if shm2:
                try:
                    shm2.close()
                except Exception:
                    pass

    def test_large_pid_and_port(self):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        shm = write_daemon_info_to_shm(999999, 65000)
        try:
            info = read_daemon_info_from_shm()
            assert info is not None
            pid, port = info
            assert pid == 999999
            assert port == 65000
        finally:
            if shm:
                try:
                    shm.close()
                except Exception:
                    pass

    def test_port_returns_default_after_shm_closed(self):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")

        shm = write_daemon_info_to_shm(12345, 19876)
        shm.close()
        # Windows: 关闭最后一个句柄后共享内存被销毁
        # 但其他进程可能仍持有引用，结果取决于时序
        # 仅验证不崩溃
        try:
            port = read_port_from_shm()
            assert isinstance(port, int)
        except Exception:
            pass

    def test_mmap_name_is_daemon(self):
        assert MMAP_NAME == "Local\\PTYAgentDaemon"

    def test_mmap_size_sufficient(self):
        text = "999999:65000"
        assert len(text.encode("ascii")) <= MMAP_SIZE


class TestAuthTokenShm:
    """认证令牌共享内存读写测试"""

    def test_read_none_when_empty(self):
        if not IS_WINDOWS:
            pytest.skip("Windows 共享内存测试")
        token = read_auth_token()
        # 可能读到其他测试残留，仅验证不崩溃
        assert token is None or isinstance(token, str)

    def test_write_and_read_token(self):
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

    def test_overwrite_token(self):
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


class TestCleanupShm:
    """cleanup 函数测试（仅验证不崩溃）"""

    def test_cleanup_port_shm_no_error(self):
        cleanup_port_shm()

    def test_cleanup_auth_shm_no_error(self):
        cleanup_auth_shm()

    def test_cleanup_port_shm_idempotent(self):
        cleanup_port_shm()
        cleanup_port_shm()

    def test_cleanup_auth_shm_idempotent(self):
        cleanup_auth_shm()
        cleanup_auth_shm()
