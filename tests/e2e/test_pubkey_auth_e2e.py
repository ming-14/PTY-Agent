"""e2e 测试 —— 认证端到端验证

三监听器架构（daemon.toml [listener]）+ 客户端连接方式（client.toml [connection]）：
- token 监听器 → 明文 + Token/HMAC（SHM 分发凭据）
- tls 监听器 → TLS + Ed25519 pubkey（TOFU 验证）
- basic 监听器 → 明文无认证

覆盖场景：
1. 只开 token+HMAC（回归，现有行为不变）
2. 只开公私钥，合法私钥 → 通过（TLS 客户端）
3. 只开公私钥，私钥不在 authorized_keys → 拒绝（TLS 客户端）
4. 只开公私钥，authorized_keys 为空 → fail-closed 拒绝（TLS 客户端）
5. token+tls，客户端选 token → 通过（连接 token 监听器）
6. token+tls，客户端选 pubkey → 通过（TLS 客户端）
7. token+tls，客户端选 pubkey，未授权 → 拒绝（TLS 客户端）
8. token+tls，客户端选 token，pubkey 未授权 → 仍通过
9. 只开 basic，无认证 → 通过
10. 配置不一致：客户端选 token 但服务端只开 tls → 失败
11. OpenSSH 兼容：ssh-keygen 生成的密钥走完整 daemon 链路 → 通过（需 ssh-keygen）

测试策略：
- 每场景独立备份 common/daemon/client 三个 toml → 写入测试配置 → 启动 daemon → 调用 list 验证 → 停止 daemon → 恢复三个 toml
- 用临时密钥目录（tmp_path）避免污染 ~/.pty-agent/keys
- 通过 ``python -m src list`` 的响应判断认证通过/拒绝（list 走认证链路但不创建会话）
- pubkey 场景：客户端按 CONNECT_MODE=tls 连接 127.0.0.1:18767，TOFU 自动信任首次证书指纹

环境要求：
- Python 3.8+
- 场景 11 需要系统安装 ssh-keygen（否则 skip）
- 测试期间会启停 daemon，请确保运行前 daemon 未在运行
"""

import json
import os
import shutil
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

# ssh-keygen 可执行文件路径（场景 11 用，不存在则 skip）
_SSH_KEYGEN = shutil.which("ssh-keygen")

# 测试用 TLS 监听器端口（daemon.toml [listener] TLS_PORT 与 client.toml [connection] TLS_PORT 一致）
_TEST_TLS_PORT = 18767


def _build_common_toml() -> str:
    """构造测试用 common.toml 内容（共享配置）

    只含 Daemon 与 Client 共享的常量：terminal / compression / input_limit。
    认证开关已移除：监听位置在 daemon.toml [listener] 段，
    客户端连接方式在 client.toml [connection] 段。

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


def _build_daemon_toml(
    *,
    basic: bool,
    token: bool,
    tls: bool,
    private_key_path: str,
    tls_port: int = _TEST_TLS_PORT,
    authorized_keys_path: str = "~/.pty-agent/authorized_keys",
) -> str:
    """构造测试用 daemon.toml 内容（三监听器配置）

    [listener] 段：basic / token / tls 三段独立 enabled + 监听位置。
    [auth] 段携带 TLS 服务端配置（证书路径与有效期）与授权公钥列表。

    Args:
        basic: 明文无认证监听器 enabled
        token: Token + HMAC 监听器 enabled
        tls: TLS + pubkey 监听器 enabled
        private_key_path: 客户端私钥路径（用于推导同级 certs/ 证书目录）
        tls_port: TLS Listener 监听端口
        authorized_keys_path: 服务端授权公钥列表路径

    Returns:
        daemon.toml 文本内容
    """
    return f"""# 守护进程配置 —— e2e 测试临时覆写

SINGLE_INSTANCE = true

[listener]
BASIC_ENABLED  = {str(basic).lower()}
BASIC_HOST     = "0.0.0.0"
BASIC_PORT     = 10521
BASIC_PASSWORD = ""

TOKEN_ENABLED = {str(token).lower()}
TOKEN_HOST    = "127.0.0.1"
TOKEN_PORT    = 10520

TLS_ENABLED   = {str(tls).lower()}
TLS_HOST      = "0.0.0.0"
TLS_PORT      = {tls_port}

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
PUBKEY_AUTHORIZED_KEYS      = "{authorized_keys_path}"
# TLS 服务端配置
TLS_CERT_DIR           = "{os.path.dirname(private_key_path)}/certs"
TLS_CERT_FILE          = "{os.path.dirname(private_key_path)}/certs/daemon.crt"
TLS_KEY_FILE           = "{os.path.dirname(private_key_path)}/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
"""


def _build_client_toml(
    *,
    connect_mode: str,
    private_key_path: str,
    known_hosts_path: str,
    tls_port: int = _TEST_TLS_PORT,
) -> str:
    """构造测试用 client.toml 内容（连接方式 + 客户端认证）

    [connection] 段：CONNECT_MODE 决定连接哪个监听器（basic/token/tls），
    各模式监听位置独立配置。tls 模式还需 [auth] 段私钥与 TOFU。

    Args:
        connect_mode: 客户端连接方式（"basic" / "token" / "tls"）
        private_key_path: 客户端私钥路径（tls 模式）
        known_hosts_path: TOFU 信任存储文件路径
        tls_port: 远程 daemon TLS 监听器端口

    Returns:
        client.toml 文本内容
    """
    # 注意：不得包含 [logging] 段 —— 日志配置已拆分到 config/client/logging.toml
    # （测试不覆写该文件），重复定义 CLIENT_LOG_LEVEL 会触发配置合并冲突
    tls_host = '"127.0.0.1"' if connect_mode == "tls" else '""'
    return f"""# 客户端配置 —— e2e 测试临时覆写

[connection]
CONNECT_MODE = "{connect_mode}"
BASIC_HOST     = "127.0.0.1"
BASIC_PORT     = 10521
BASIC_PASSWORD = ""
TOKEN_HOST = "127.0.0.1"
TOKEN_PORT = 10520
TLS_HOST = {tls_host}
TLS_PORT = {tls_port}

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
PUBKEY_PRIVATE_KEY_PATH = "{private_key_path}"
KNOWN_HOSTS_FILE    = "{known_hosts_path}"
TOFU_STRICT         = true
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
    # 优先返回非 info 类型的 JSON 行（跳过 start_daemon 的 info 消息）
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            data = json.loads(line)
            if data.get("type") != "info":
                return data
    # 回退：返回首个 JSON 行
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"无 JSON 输出: {stdout!r}")


@pytest.fixture
def auth_env(tmp_path):
    """认证环境工厂 fixture

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

    def start(
        *,
        enable_token: bool,
        enable_pubkey: bool,
        client_auth_method: str = "token",
        private_key_path: str,
        authorized_keys_path: str,
        authorized_keys_content: str = "",
    ):
        """写入测试配置并启动 daemon

        同时写入 common.toml / daemon.toml / client.toml 三个文件：
        - common.toml: 共享配置（无认证开关）
        - daemon.toml: [listener] 段三监听器 enabled + TLS/授权配置
        - client.toml: [connection] 段连接方式 + 客户端认证配置

        旧认证开关语义映射到三监听器：
        - enable_token → daemon token 监听器；client CONNECT_MODE=token
        - enable_pubkey → daemon tls 监听器；client CONNECT_MODE=tls
        - 两者都关 → daemon basic 监听器；client CONNECT_MODE=basic

        Args:
            enable_token: 是否启用 token 监听器
            enable_pubkey: 是否启用 tls 监听器
            client_auth_method: 连接方式（"token" / "pubkey" / "none"）
            private_key_path: 客户端私钥绝对路径
            authorized_keys_path: 服务端 authorized_keys 绝对路径
            authorized_keys_content: authorized_keys 文件内容（空字符串=空文件触发 fail-closed）
        """
        # 推导 known_hosts 路径（与密钥同目录）
        known_hosts_path = str(Path(private_key_path).parent / "known_hosts")

        # 路径用正斜杠避免 TOML 转义
        pk_path = private_key_path.replace("\\", "/")
        ak_path_str = authorized_keys_path.replace("\\", "/")
        kh_path = known_hosts_path.replace("\\", "/")

        # 监听器 enabled 组合：token / tls / 都关=basic
        basic = not enable_token and not enable_pubkey
        # 客户端连接方式：token→token, pubkey→tls, none→basic
        connect_mode = {
            "token": "token",
            "pubkey": "tls",
            "none": "basic",
        }[client_auth_method]

        # 写入 common.toml（共享配置）
        _COMMON_TOML.write_text(
            _build_common_toml(),
            encoding="utf-8", errors="replace",
        )
        # 写入 daemon.toml（三监听器 + 服务端认证配置）
        _DAEMON_TOML.write_text(
            _build_daemon_toml(
                basic=basic,
                token=enable_token,
                tls=enable_pubkey,
                private_key_path=pk_path,
                authorized_keys_path=ak_path_str,
            ),
            encoding="utf-8", errors="replace",
        )
        # 写入 client.toml（连接方式 + 客户端认证配置）
        _CLIENT_TOML.write_text(
            _build_client_toml(
                connect_mode=connect_mode,
                private_key_path=pk_path,
                known_hosts_path=kh_path,
            ),
            encoding="utf-8", errors="replace",
        )

        # 确保 authorized_keys 文件存在（空内容也写，触发 fail-closed 走 load_authorized_keys 路径）
        ak_path = Path(authorized_keys_path)
        ak_path.parent.mkdir(parents=True, exist_ok=True)
        ak_path.write_text(authorized_keys_content, encoding="utf-8")

        # 启动 daemon（subprocess.Popen 子进程，会读新 common.toml）
        from src.daemonctl import start_daemon, _find_daemon_port, is_running
        start_daemon()

        # 轮询等待 daemon 就绪（任一启用监听器 TCP 可达即就绪）
        import socket as _socket
        probe_ports = []
        if basic:
            probe_ports.append(10521)
        if enable_token:
            probe_ports.append(10520)
        if enable_pubkey:
            probe_ports.append(_TEST_TLS_PORT)
        for _ in range(60):
            if not is_running():
                time.sleep(0.1)
                continue
            # TLS-only 模式：无 SHM，用监听端口 TCP 可达探测
            for port in probe_ports:
                try:
                    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(("127.0.0.1", port))
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


def _generate_keypair(tmp_path: Path, key_dir_name: str = "keys") -> tuple:
    """用项目 keygen 生成密钥对，返回 (私钥路径, 公钥路径, 公钥行内容)

    Args:
        tmp_path: 临时目录
        key_dir_name: 密钥子目录名（默认 "keys"，不同密钥对用不同名字避免冲突）

    Returns:
        (private_key_path, public_key_path, public_key_line) 三元组
    """
    key_dir = str(tmp_path / key_dir_name)
    result = subprocess.run(
        [sys.executable, "-m", "src", "keygen", "--key-dir", key_dir,
         "--comment", "e2e@test"],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"keygen 失败: {result.stderr}"

    private_path = os.path.join(key_dir, "id_ed25519")
    public_path = private_path + ".pub"
    public_line = Path(public_path).read_text(encoding="utf-8").strip()
    return private_path, public_path, public_line


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


def _assert_request_failed(result: subprocess.CompletedProcess):
    """断言请求失败：list 响应为 error（不限定具体消息）

    用于配置不一致等客户端侧错误 —— 错误在发送请求前由客户端检测，
    消息为配置提示而非服务端 Authentication failed。
    """
    resp = _parse_cli_json(result.stdout)
    assert resp.get("type") == "error", \
        f"请求应失败但收到非 error 响应: {resp}"


# ═══════════════════════════════════════════════════════════════
#  认证场景测试（OR 语义 + 客户端单选）
# ═══════════════════════════════════════════════════════════════

class TestTokenOnlyRegression:
    """场景 1：只开 token+HMAC（回归，现有行为不变）"""

    def test_token_only_list_passes(self, auth_env):
        """token 监听器开启，CONNECT_MODE=token → list 通过"""
        auth_env.start(
            enable_token=True,
            enable_pubkey=False,
            client_auth_method="token",
            private_key_path=str(auth_env.tmp_path / "keys" / "id_ed25519"),
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content="",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestPubkeyOnlyValidKey:
    """场景 2：只开公私钥，合法私钥 → 通过（TLS 客户端）"""

    def test_pubkey_only_valid_key_passes(self, auth_env):
        """tls 监听器开启，CONNECT_MODE=tls（合法私钥）→ list 通过"""
        private_path, _, public_line = _generate_keypair(auth_env.tmp_path)

        auth_env.start(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestPubkeyOnlyUnauthorizedKey:
    """场景 3：只开公私钥，私钥不在 authorized_keys → 拒绝（TLS 客户端）"""

    def test_pubkey_only_unauthorized_key_rejected(self, auth_env):
        """客户端私钥对应的公钥不在 authorized_keys → list 被拒绝"""
        private_path, _, _ = _generate_keypair(auth_env.tmp_path, key_dir_name="client_keys")
        _, _, other_public_line = _generate_keypair(auth_env.tmp_path, key_dir_name="other_keys")

        auth_env.start(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=other_public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_rejected(result)


class TestPubkeyOnlyEmptyAuthorizedKeys:
    """场景 4：只开公私钥，authorized_keys 为空 → fail-closed 拒绝（TLS 客户端）"""

    def test_pubkey_only_empty_authorized_keys_rejected(self, auth_env):
        """authorized_keys 文件存在但为空 → fail-closed，所有公私钥请求被拒绝"""
        private_path, _, _ = _generate_keypair(auth_env.tmp_path)

        auth_env.start(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content="",
        )
        result = auth_env.run_list()
        _assert_auth_rejected(result)


class TestBothOrClientToken:
    """场景 5：双端口，客户端选 token → 通过（连接明文端口）"""

    def test_both_or_client_token_passes(self, auth_env):
        """服务端双开 OR，客户端选 token → HMAC 签名通过（即使 pubkey 也合法）"""
        private_path, _, public_line = _generate_keypair(auth_env.tmp_path)

        auth_env.start(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="token",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestBothOrClientPubkeyValid:
    """场景 6：双端口，客户端选 pubkey，合法 → 通过（TLS 客户端）"""

    def test_both_or_client_pubkey_valid_passes(self, auth_env):
        """服务端双开 OR，客户端选 pubkey，合法私钥 → 通过"""
        private_path, _, public_line = _generate_keypair(auth_env.tmp_path)

        auth_env.start(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestBothOrClientPubkeyUnauthorized:
    """场景 7：双端口，客户端选 pubkey，未授权 → 拒绝（TLS 客户端）"""

    def test_both_or_client_pubkey_unauthorized_rejected(self, auth_env):
        """客户端选 pubkey 但公钥未授权 → 拒绝（OR 无 _sig 回退）"""
        private_path, _, _ = _generate_keypair(auth_env.tmp_path, key_dir_name="client_keys")
        _, _, other_public_line = _generate_keypair(auth_env.tmp_path, key_dir_name="other_keys")

        auth_env.start(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=other_public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_rejected(result)


class TestBothOrClientTokenPubkeyUnauthorized:
    """场景 8：双端口，客户端选 token，pubkey 未授权 → 仍通过（连接明文端口）"""

    def test_both_or_client_token_pubkey_unauthorized_passes(self, auth_env):
        """客户端选 token，pubkey 未授权 → 仍通过（客户端没发 _sig_ed25519，OR 无关 pubkey 状态）"""
        private_path, _, _ = _generate_keypair(auth_env.tmp_path, key_dir_name="client_keys")
        _, _, other_public_line = _generate_keypair(auth_env.tmp_path, key_dir_name="other_keys")

        auth_env.start(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="token",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=other_public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestBothDisabledNoAuth:
    """场景 9：都关，无认证 → 通过"""

    def test_both_disabled_list_passes(self, auth_env):
        """basic 监听器开启，CONNECT_MODE=basic → list 通过"""
        auth_env.start(
            enable_token=False,
            enable_pubkey=False,
            client_auth_method="none",
            private_key_path=str(auth_env.tmp_path / "keys" / "id_ed25519"),
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content="",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)


class TestConfigMismatch:
    """场景 10：配置不一致 → 失败"""

    def test_client_token_server_pubkey_fails(self, auth_env):
        """客户端选 token 但服务端只开 pubkey → 失败"""
        private_path, _, public_line = _generate_keypair(auth_env.tmp_path)

        auth_env.start(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="token",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_request_failed(result)


@pytest.mark.skipif(_SSH_KEYGEN is None, reason="系统未安装 ssh-keygen，跳过 OpenSSH 兼容 e2e")
class TestOpenSshCompatFullDaemon:
    """场景 11：OpenSSH 兼容 —— ssh-keygen 生成的密钥走完整 daemon 链路"""

    def test_ssh_keygen_generated_key_authenticates(self, auth_env):
        """ssh-keygen -t ed25519 生成的密钥应能通过项目 daemon 认证"""
        key_dir = auth_env.tmp_path / "ssh_keys"
        key_dir.mkdir(parents=True, exist_ok=True)
        private_path = str(key_dir / "id_ed25519")
        public_path = private_path + ".pub"

        gen = subprocess.run(
            [_SSH_KEYGEN, "-t", "ed25519", "-N", "", "-f", private_path,
             "-C", "ssh-keygen@e2e"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8", errors="replace",
        )
        assert gen.returncode == 0, f"ssh-keygen 生成失败: {gen.stderr}"

        public_line = Path(public_path).read_text(encoding="utf-8").strip()

        auth_env.start(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(auth_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        result = auth_env.run_list()
        _assert_auth_passed(result)
