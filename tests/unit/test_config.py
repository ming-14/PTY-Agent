"""config.py 单元测试"""

import pytest

from src.config.common import (
    DAEMON_HOST,
    MAX_COMMAND_LEN,
    MAX_INPUT_LEN,
    MAX_PATTERN_LEN,
    MAX_SESSION_ID_LEN,
    IS_WINDOWS,
)
from src.config.daemon import (
    DEFAULT_DAEMON_PORT,
    MAX_OUTPUT_BUFFER,
    MAX_TRIGGER_SCAN,
    DEFAULT_TRIGGER_TIMEOUT,
    DAEMON_START_TIMEOUT,
    PING_TIMEOUT,
    STOP_TIMEOUT,
    SOCKET_LISTEN_BACKLOG,
    SOCKET_RECV_BUFSIZE,
    PTY_READ_SIZE,
    MMAP_NAME,
    MMAP_SIZE,
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
    AUTH_TOKEN_ROTATE_INTERVAL,
    AUTH_TOKEN_GRACE_PERIOD,
)
from src.config.client import CONNECT_TIMEOUT


class TestNetworkConfig:
    def test_daemon_host(self):
        assert DAEMON_HOST == "127.0.0.1"

    def test_default_port(self):
        assert isinstance(DEFAULT_DAEMON_PORT, int)
        assert DEFAULT_DAEMON_PORT > 0


class TestBufferConfig:
    def test_max_output_buffer(self):
        assert MAX_OUTPUT_BUFFER == 100 * 1024 * 1024

    def test_max_trigger_scan(self):
        assert MAX_TRIGGER_SCAN == 1 * 1024 * 1024


class TestTimeoutConfig:
    def test_default_trigger_timeout(self):
        assert DEFAULT_TRIGGER_TIMEOUT == 120.0

    def test_daemon_start_timeout(self):
        assert DAEMON_START_TIMEOUT == 3.0

    def test_ping_timeout(self):
        assert PING_TIMEOUT == 1.0

    def test_connect_timeout(self):
        assert CONNECT_TIMEOUT == 30.0

    def test_stop_timeout(self):
        assert STOP_TIMEOUT == 3.0


class TestLengthLimits:
    def test_max_session_id_len(self):
        assert MAX_SESSION_ID_LEN == 128

    def test_max_command_len(self):
        assert MAX_COMMAND_LEN == 65536

    def test_max_pattern_len(self):
        assert MAX_PATTERN_LEN == 4096

    def test_max_input_len(self):
        assert MAX_INPUT_LEN == 65536


class TestShmConfig:
    def test_mmap_name(self):
        assert MMAP_NAME == "Local\\PTYAgentDaemon"

    def test_mmap_size(self):
        assert MMAP_SIZE == 32

    def test_auth_token_name(self):
        assert AUTH_TOKEN_NAME == "Local\\PTYAgentAuth"

    def test_auth_token_size(self):
        assert AUTH_TOKEN_SIZE == 64

    def test_auth_token_rotate_interval(self):
        assert AUTH_TOKEN_ROTATE_INTERVAL == 1800

    def test_auth_token_grace_period(self):
        assert AUTH_TOKEN_GRACE_PERIOD == 120


class TestPlatform:
    def test_is_windows_type(self):
        assert isinstance(IS_WINDOWS, bool)
