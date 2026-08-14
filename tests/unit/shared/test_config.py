"""config.py 单元测试"""

from src.config.common import (
    MAX_COMMAND_LEN,
    MAX_INPUT_LEN,
    MAX_PATTERN_LEN,
    MAX_SESSION_ID_LEN,
    IS_WINDOWS,
)
from src.config.daemon import (
    PLAIN_ENABLED,
    PLAIN_HOST,
    PLAIN_PORT,
    TOKEN_ENABLED,
    TOKEN_HOST,
    TOKEN_PORT,
    TLS_ENABLED,
    TLS_HOST,
    TLS_PORT,
    MAX_OUTPUT_BUFFER,
    MAX_TRIGGER_SCAN,
    DEFAULT_TRIGGER_TIMEOUT,
    DAEMON_START_TIMEOUT,
    PING_TIMEOUT,
    STOP_TIMEOUT,
    AUTH_TOKEN_NAME,
    AUTH_TOKEN_SIZE,
    AUTH_TOKEN_ROTATE_INTERVAL,
    AUTH_TOKEN_GRACE_PERIOD,
)
from src.config.client import (
    CONNECT_MODE,
    CONNECT_TIMEOUT,
    PLAIN_HOST as CLIENT_PLAIN_HOST,
    PLAIN_PORT as CLIENT_PLAIN_PORT,
    TOKEN_HOST as CLIENT_TOKEN_HOST,
    TOKEN_PORT as CLIENT_TOKEN_PORT,
    TLS_HOST as CLIENT_TLS_HOST,
    TLS_PORT as CLIENT_TLS_PORT,
)


class TestListenerConfig:
    """三监听器独立配置（daemon 侧）"""

    def test_plain_listener(self):
        assert isinstance(PLAIN_ENABLED, bool)
        assert isinstance(PLAIN_HOST, str)
        assert isinstance(PLAIN_PORT, int)
        assert PLAIN_PORT > 0

    def test_token_listener(self):
        assert isinstance(TOKEN_ENABLED, bool)
        assert TOKEN_HOST == "127.0.0.1"
        assert isinstance(TOKEN_PORT, int)
        assert TOKEN_PORT > 0

    def test_tls_listener(self):
        assert isinstance(TLS_ENABLED, bool)
        assert isinstance(TLS_HOST, str)
        assert isinstance(TLS_PORT, int)
        assert TLS_PORT > 0


class TestConnectionConfig:
    """客户端连接方式配置"""

    def test_connect_mode(self):
        assert CONNECT_MODE in ("plain", "token", "tls")

    def test_mode_targets(self):
        assert CLIENT_PLAIN_HOST == "127.0.0.1"
        assert CLIENT_PLAIN_PORT > 0
        assert CLIENT_TOKEN_HOST == "127.0.0.1"
        assert CLIENT_TOKEN_PORT > 0
        assert isinstance(CLIENT_TLS_HOST, str)
        assert CLIENT_TLS_PORT > 0


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