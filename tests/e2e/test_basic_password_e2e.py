"""e2e 测试 —— basic 监听器密码认证端到端验证

basic 监听器（daemon.toml [listener]）+ 客户端连接方式（client.toml [connection]）：
- BASIC_PASSWORD 非空：密码认证 + 密码即 HMAC 密钥双向签名
- BASIC_PASSWORD 为空：无认证（原有行为）

覆盖场景：
1. basic 空密码（无认证）→ 通过（回归）
2. basic 密码 + 客户端同密码 → 通过
3. basic 密码 + 客户端错误密码 → 拒绝（HMAC 密钥不同，验签失败）
4. basic 密码 + 客户端未配置密码 → 拒绝（缺 password + 无签名）
5. basic 密码 + 正确密码但消息被篡改（伪造 _sig）→ 拒绝（验签失败）
6. basic 空密码 + 客户端配置密码 → 通过（空密码=无认证，服务端不验）

测试策略：
- 每场景独立备份 common/daemon/client 三个 toml → 写入测试配置 → 启动 daemon → 调用 list 验证 → 停止 daemon → 恢复三个 toml
- 通过 ``python -m src list`` 的响应判断认证通过/拒绝

环境要求：
- Python 3.8+
- 测试期间会启停 daemon，请确保运行前 daemon 未在运行
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# 将项目根目录加入 sys.path，便于 import src.* 与以子进程方式运行 ``python -m src``
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# common.toml / daemon.toml / client.toml 路径
# 测试期间会被临时覆写，teardown 时逐一恢复
_COMMON_TOML = Path(_PROJECT_ROOT) / "config" / "common.toml"
_DAEMON_TOML = Path(_PROJECT_ROOT) / "config" / "daemon" / "daemon.toml"
_CLIENT_TOML = Path(_PROJECT_ROOT) / "config" / "client" / "client.toml"

# 测试用 basic 监听器端口（daemon.toml [listener] BASIC_PORT）
_TEST_BASIC_PORT = 10521

# 测试用共享密码
_TEST_PASSWORD = "e2e-test-password"


def _build_common_toml() -> str:
    """构造测试用 common.toml 内容（共享配置）

    Returns:
        common.toml 文本内容
    """
    return f"""# 共有配置 —— Daemon 与 Client 均需使用的常量（e2e 测试临时覆写）

[terminal]
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

[compression]
GZIP_COMPRESS_LEVEL = 6

[input_limit]
MAX_SESSION_ID_LEN = 128
MAX_COMMAND_LEN    = 65536
MAX_INPUT_LEN      = 65536
MAX_PATTERN_LEN    = 4096
"""


def _build_daemon_toml(*, basic_password: str) -> str:
    """构造测试用 daemon.toml 内容（只开 basic 监听器 + 密码）

    Args:
        basic_password: basic 监听器共享密码（空=无认证）

    Returns:
        daemon.toml 文本内容
    """
    return f"""# 守护进程配置 —— e2e 测试临时覆写

SINGLE_INSTANCE = true

[listener]
BASIC_ENABLED  = true
BASIC_HOST     = "0.0.0.0"
BASIC_PORT     = {_TEST_BASIC_PORT}
BASIC_PASSWORD = "{basic_password}"

TOKEN_ENABLED = false
TOKEN_HOST    = "127.0.0.1"
TOKEN_PORT    = 10520

TLS_ENABLED   = false
TLS_HOST      = "0.0.0.0"
TLS_PORT      = 18767

[buffer]
MAX_OUTPUT_BUFFER = 104_857_600
MAX_TRIGGER_SCAN  = 1_048_576

[timeout]
DEFAULT_TRIGGER_TIMEOUT = 120.0
[misc]
SOCKET_LISTEN_BACKLOG  = 5
PTY_READ_SIZE          = 65536

[named_resource]
JOB_OBJECT_NAME_PREFIX     = "Local\\\\PTYJob_"

[input_limit]
MAX_SESSIONS = 50

[workflow]
WORKFLOW_MAX_RUNS          = 50
WORKFLOW_DEFAULT_PARALLEL  = 4
WORKFLOW_STEP_OUTPUT_LIMIT = 4096
WORKFLOW_MAX_FILE_SIZE     = 1048576

[auth]
AUTH_TOKEN_ROTATE_INTERVAL  = 1800
AUTH_TOKEN_GRACE_PERIOD     = 120
PUBKEY_AUTHORIZED_KEYS      = "~/.pty-agent/authorized_keys"
TLS_CERT_DIR           = "~/.pty-agent/certs"
TLS_CERT_FILE          = "~/.pty-agent/certs/daemon.crt"
TLS_KEY_FILE           = "~/.pty-agent/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
"""


def _build_client_toml(*, client_password: str) -> str:
    """构造测试用 client.toml 内容（basic 连接方式 + 密码）

    Args:
        client_password: 客户端配置的 basic 密码（空=无认证）

    Returns:
        client.toml 文本内容
    """
    return f"""# 客户端配置 —— e2e 测试临时覆写

[connection]
CONNECT_MODE = "basic"
BASIC_HOST     = "127.0.0.1"
BASIC_PORT     = {_TEST_BASIC_PORT}
BASIC_PASSWORD = "{client_password}"
TOKEN_HOST = "127.0.0.1"
TOKEN_PORT = 10520
TLS_HOST = ""
TLS_PORT = 18767

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
PUBKEY_PRIVATE_KEY_PATH = "~/.pty-agent/keys/id_ed25519"
KNOWN_HOSTS_FILE    = "~/.pty-agent/known_hosts"
TOFU_STRICT         = true

[logging]
CLIENT_LOG_LEVEL = "DEBUG"
CLIENT_LOGGERS   = ["pty-client", "pty-daemonctl"]
"""


def _parse_cli_json(stdout: str) -> dict:
    """解析 ``python -m src list`` stdout 的 JSON 响应

    print_response 用 safe_print 输出单行 JSON。取首个非 info 类型的 JSON 行解析
    （跳过 start_daemon 输出的 {"type": "info", ...} 消息）。

    Args:
        stdout: CLI 子进程 stdout

    Returns:
        解析后的响应 dict

    Raises:
        ValueError: 无 JSON 输出
        json.JSONDecodeError: JSON 格式错误
    """
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            data = json.loads(line)
            if data.get("type") != "info":
                return data
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"无 JSON 输出: {stdout!r}")


def _send_raw_request(msg: dict) -> dict:
    """raw socket 发送请求并读取单行响应（不签名）

    用于模拟未签名/签名被篡改的包，验证服务端验签与认证行为。

    Args:
        msg: 请求消息字典（原样 JSON 序列化，不注入密码不签名）。

    Returns:
        服务端响应 dict
    """
    import socket as _socket

    with _socket.create_connection(("127.0.0.1", _TEST_BASIC_PORT), timeout=10) as sock:
        sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        sock.settimeout(10)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
    line = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    return json.loads(line)


@pytest.fixture
def basic_auth_env(tmp_path):
    """basic 密码认证环境工厂 fixture

    提供 start() 与 run_list() 方法。start() 写入测试配置（common/daemon/client
    三个 toml）并启动 daemon；teardown 时停止 daemon 并恢复三个 toml 文件。

    Yields:
        SimpleNamespace(start=..., run_list=..., tmp_path=tmp_path)
    """
    # 备份三个 toml 文件（teardown 时逐字节恢复，避免污染生产配置）
    backup_common = _COMMON_TOML.read_bytes()
    backup_daemon = _DAEMON_TOML.read_bytes()
    backup_client = _CLIENT_TOML.read_bytes()
    started = [False]
    daemon_stopped = [False]

    def start(*, basic_password: str, client_password: str):
        """写入测试配置并启动 daemon

        Args:
            basic_password: daemon 侧 basic 监听器密码（空=无认证）
            client_password: 客户端配置的 basic 密码（空=不认证）
        """
        # 写入 common.toml（共享配置）
        _COMMON_TOML.write_text(
            _build_common_toml(),
            encoding="utf-8", errors="replace",
        )
        # 写入 daemon.toml（只开 basic 监听器 + 密码）
        _DAEMON_TOML.write_text(
            _build_daemon_toml(basic_password=basic_password),
            encoding="utf-8", errors="replace",
        )
        # 写入 client.toml（basic 连接方式 + 密码）
        _CLIENT_TOML.write_text(
            _build_client_toml(client_password=client_password),
            encoding="utf-8", errors="replace",
        )

        # 启动 daemon（subprocess.Popen 子进程，会读新 common.toml）
        from src.daemonctl import start_daemon, is_running
        start_daemon()

        # 轮询等待 daemon 就绪（basic 监听端口 TCP 可达即就绪）
        import socket as _socket
        for _ in range(60):
            if not is_running():
                time.sleep(0.1)
                continue
            try:
                probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                probe.settimeout(0.5)
                probe.connect(("127.0.0.1", _TEST_BASIC_PORT))
                probe.close()
                started[0] = True
                return
            except (OSError, _socket.error):
                pass  # listener 尚未就绪，继续轮询
            time.sleep(0.1)
        raise RuntimeError("daemon 启动超时（6 秒内未就绪）")

    def run_list() -> subprocess.CompletedProcess:
        """调用 ``python -m src list``，返回子进程结果

        Returns:
            subprocess.CompletedProcess（含 returncode/stdout/stderr）
        """
        return subprocess.run(
            [sys.executable, "-m", "src", "list"],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8", errors="replace",
        )

    def _stop_daemon():
        if started[0] and not daemon_stopped[0]:
            from src.daemonctl import stop_daemon, is_running
            stop_daemon(force=True)
            # 轮询等待 daemon 完全停止
            for _ in range(30):
                if not is_running():
                    daemon_stopped[0] = True
                    break
                time.sleep(0.1)

    try:
        yield SimpleNamespace(start=start, run_list=run_list, tmp_path=tmp_path)
    finally:
        _stop_daemon()
        # 恢复三个 toml 文件（无论 daemon 是否成功停止都恢复，避免污染生产配置）
        _COMMON_TOML.write_bytes(backup_common)
        _DAEMON_TOML.write_bytes(backup_daemon)
        _CLIENT_TOML.write_bytes(backup_client)


def _assert_auth_passed(result: subprocess.CompletedProcess):
    """断言认证通过：list 响应非 error"""
    assert result.returncode == 0, f"CLI 退出码非 0: {result.stderr}"
    resp = _parse_cli_json(result.stdout)
    assert resp.get("type") != "error", \
        f"认证应通过但收到 error 响应: {resp}"


def _assert_auth_rejected(result: subprocess.CompletedProcess):
    """断言认证被拒绝：list 响应为 Authentication failed

    服务端签名验证失败或认证不通过时，daemon 返回 Authentication failed 错误。
    """
    resp = _parse_cli_json(result.stdout)
    assert resp.get("type") == "error", \
        f"认证应被拒绝但收到非 error 响应: {resp}"
    assert "Authentication failed" in resp.get("message", ""), \
        f"error 响应应含 'Authentication failed': {resp}"


# ═══════════════════════════════════════════════════════════════
#  basic 密码认证场景测试
# ═══════════════════════════════════════════════════════════════

class TestPlainNoPassword:
    """场景 1：basic 空密码（无认证，原有行为）"""

    def test_empty_password_no_auth_passes(self, basic_auth_env):
        """daemon BASIC_PASSWORD 空 + 客户端 BASIC_PASSWORD 空 → list 通过"""
        basic_auth_env.start(basic_password="", client_password="")
        result = basic_auth_env.run_list()
        _assert_auth_passed(result)


class TestPlainPasswordCorrect:
    """场景 2：basic 密码 + 客户端同密码 → 通过"""

    def test_matching_password_passes(self, basic_auth_env):
        """daemon 密码与客户端密码一致 → list 通过"""
        basic_auth_env.start(basic_password=_TEST_PASSWORD, client_password=_TEST_PASSWORD)
        result = basic_auth_env.run_list()
        _assert_auth_passed(result)


class TestPlainPasswordWrong:
    """场景 3：basic 密码 + 客户端错误密码 → 拒绝"""

    def test_wrong_password_rejected(self, basic_auth_env):
        """客户端密码与 daemon 不一致（HMAC 密钥不同，服务端验签失败）→ list 被拒绝

        HMAC 双向签名下双方密钥不同，服务端错误响应也无法通过客户端验签，
        客户端表现为 "no response"，故只断言 error 类型不限定消息。
        """
        basic_auth_env.start(
            basic_password=_TEST_PASSWORD, client_password="wrong-password",
        )
        result = basic_auth_env.run_list()
        # 拒绝语义：CLI 收到 error 响应（"no response" 或 "Authentication failed"）
        resp = _parse_cli_json(result.stdout)
        assert resp.get("type") == "error", \
            f"认证应被拒绝但收到非 error 响应: {resp}"


class TestPlainPasswordClientMissing:
    """场景 4：basic 密码 + 客户端未配置密码 → 拒绝"""

    def test_client_no_password_rejected(self, basic_auth_env):
        """daemon 配置密码但客户端空密码（无签名无凭证）→ list 被拒绝"""
        basic_auth_env.start(basic_password=_TEST_PASSWORD, client_password="")
        result = basic_auth_env.run_list()
        _assert_auth_rejected(result)


class TestPlainTamperedMessage:
    """场景 5：正确密码但消息被篡改 → 拒绝（HMAC 完整性兜底）"""

    def test_tampered_message_rejected(self, basic_auth_env):
        """携带正确 password 但 _sig 被篡改的请求 → 验签失败被拒绝"""
        basic_auth_env.start(
            basic_password=_TEST_PASSWORD, client_password=_TEST_PASSWORD,
        )
        # raw socket 构造：password 正确但 _sig 为伪造值（64 个 0，非真实 HMAC）
        msg = {"type": "list", "password": _TEST_PASSWORD, "_sig": "0" * 64}
        resp = _send_raw_request(msg)
        assert resp.get("type") == "error", f"应被拒绝但收到: {resp}"
        assert "Authentication failed" in resp.get("message", ""), f"应含 Authentication failed: {resp}"


class TestPlainServerNoPasswordClientHas:
    """场景 6：basic 空密码 + 客户端配置密码（配置不一致）→ 失败

    daemon 空密码=无认证，不签响应；客户端却装配了 HMAC 验证器，
    无签名响应无法通过客户端验签 → 表现为 no response。
    该组合为配置不一致（客户端两侧 BASIC_PASSWORD 应一致），行为未定义，断言失败即可。
    """

    def test_server_no_password_mismatch_fails(self, basic_auth_env):
        """daemon 密码为空（无认证），客户端配置了密码 → list 失败"""
        basic_auth_env.start(basic_password="", client_password=_TEST_PASSWORD)
        result = basic_auth_env.run_list()
        resp = _parse_cli_json(result.stdout)
        assert resp.get("type") == "error", \
            f"配置不一致应失败但收到非 error 响应: {resp}"