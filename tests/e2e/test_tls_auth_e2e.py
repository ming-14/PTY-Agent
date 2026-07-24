"""Phase 6 e2e 测试 —— TLS 认证端到端验证

聚焦 test_pubkey_auth_e2e.py 未覆盖的 TLS 特有场景：
1. TOFU 首次信任 — 首次 TLS 连接后 known_hosts 文件写入指纹
2. TOFU 指纹匹配 — 二次连接指纹验证通过
3. TOFU 指纹不匹配 — 证书重新生成后连接被拒绝
4. 跨机 stop daemon — TLS 模式停止远程 daemon
5. 双端口同时工作 — 明文 token + TLS pubkey 并行

测试策略：
- 自定义 tls_env fixture 支持多次启停 daemon（TOFU mismatch 需要重启）
- 每个测试独立备份/恢复 common/daemon/client 三个 toml
- 通过子进程 python -m src list/stop 验证真实 CLI 链路
- known_hosts 文件内容验证 TOFU 信任存储

环境要求：
- Python 3.8+
- 测试期间会启停 daemon，请确保运行前 daemon 未在运行
"""

import json
import os
import re
import shutil
import socket
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
_COMMON_TOML = Path(_PROJECT_ROOT) / "src" / "config" / "common.toml"
_DAEMON_TOML = Path(_PROJECT_ROOT) / "src" / "config" / "daemon.toml"
_CLIENT_TOML = Path(_PROJECT_ROOT) / "src" / "config" / "client.toml"

# 测试用 TLS 端口（daemon.toml PUBKEY_LISTEN_PORT 与 client.toml DAEMON_REMOTE_PORT 一致）
_TEST_TLS_PORT = 18767


# ═══════════════════════════════════════════════════════════════
#  辅助函数（与 test_pubkey_auth_e2e.py 结构一致，保持测试独立性）
# ═══════════════════════════════════════════════════════════════

def _build_common_toml(
    *,
    enable_token: bool,
    enable_pubkey: bool,
    client_auth_method: str = "token",
    private_key_path: str = "~/.pty-agent/keys/id_ed25519",
    authorized_keys_path: str = "~/.pty-agent/authorized_keys",
) -> str:
    """构造测试用 common.toml 内容（仅共享配置）

    配置拆分后 common.toml 仅保留 Daemon 与 Client 共享的常量：
    terminal / network / compression / input_limit / 认证开关与公私钥基础路径。
    TLS 服务端配置移至 daemon.toml，TLS 客户端配置移至 client.toml。

    Args:
        enable_token: ENABLE_TOKEN_AUTH 值
        enable_pubkey: ENABLE_PUBKEY_AUTH 值
        client_auth_method: CLIENT_AUTH_METHOD 值（"token" / "pubkey" / "none"）
        private_key_path: 客户端私钥路径（PUBKEY_PRIVATE_KEY_PATH）
        authorized_keys_path: 服务端授权公钥列表路径（PUBKEY_AUTHORIZED_KEYS）

    Returns:
        common.toml 文本内容
    """
    return f"""# 共有配置 —— Daemon 与 Client 均需使用的常量（Phase 6 TLS e2e 测试临时覆写）

[terminal]
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

[network]
DAEMON_HOST = "127.0.0.1"

[compression]
GZIP_COMPRESS_LEVEL = 6

[input_limit]
MAX_SESSION_ID_LEN = 128
MAX_COMMAND_LEN    = 65536
MAX_INPUT_LEN      = 65536
MAX_PATTERN_LEN    = 4096

[auth]
ENABLE_TOKEN_AUTH = {str(enable_token).lower()}
ENABLE_PUBKEY_AUTH = {str(enable_pubkey).lower()}
CLIENT_AUTH_METHOD = "{client_auth_method}"
PUBKEY_ALGORITHM       = "ed25519"
PUBKEY_PRIVATE_KEY_PATH = "{private_key_path}"
PUBKEY_PUBLIC_KEY_PATH  = "{private_key_path}.pub"
PUBKEY_AUTHORIZED_KEYS  = "{authorized_keys_path}"
PUBKEY_KEY_DIR          = "{os.path.dirname(private_key_path)}"
"""


def _build_daemon_toml(
    *,
    private_key_path: str,
    tls_port: int = _TEST_TLS_PORT,
) -> str:
    """构造测试用 daemon.toml 内容（服务端 TLS 配置）

    包含 daemon 必需的 network/buffer/timeout/misc/daemon_start/named_resource/
    shared_memory/input_limit/auth 段，其中 [auth] 段携带 TLS 服务端配置
    （PUBKEY_LISTEN_HOST/PORT、证书路径与有效期等）。

    Args:
        private_key_path: 客户端私钥路径（用于推导同级 certs/ 证书目录）
        tls_port: TLS Listener 监听端口

    Returns:
        daemon.toml 文本内容
    """
    return f"""# 守护进程配置 —— Phase 6 TLS e2e 测试临时覆写

[network]
DEFAULT_DAEMON_PORT = 18765

[buffer]
MAX_OUTPUT_BUFFER = 104_857_600
MAX_TRIGGER_SCAN  = 1_048_576

[timeout]
DEFAULT_TRIGGER_TIMEOUT = 120.0
DAEMON_START_TIMEOUT    = 3.0
PING_TIMEOUT            = 1.0
STOP_TIMEOUT            = 3.0

[misc]
SOCKET_LISTEN_BACKLOG  = 5
SOCKET_RECV_BUFSIZE    = 4096
PTY_READ_SIZE          = 65536
MAX_MESSAGE_LENGTH     = 1_048_576

[daemon_start]
DAEMON_START_POLL_INTERVAL    = 0.3
PROCESS_EXIT_WAIT_RETRIES     = 10
PROCESS_EXIT_WAIT_INTERVAL    = 0.1

[named_resource]
SINGLE_INSTANCE_MUTEX_NAME = "Local\\\\PTYAgentSingleInstance"
JOB_OBJECT_NAME_PREFIX     = "Local\\\\PTYJob_"

[input_limit]
MAX_SESSIONS = 50

[shared_memory]
MMAP_NAME = "Local\\\\PTYAgentDaemon"
MMAP_SIZE = 32

[auth]
AUTH_TOKEN_NAME             = "Local\\\\PTYAgentAuth"
AUTH_TOKEN_SIZE             = 64
AUTH_TOKEN_ROTATE_INTERVAL  = 1800
AUTH_TOKEN_GRACE_PERIOD     = 120
HMAC_KEY_NAME               = "Local\\\\PTYAgentHmac"
HMAC_KEY_SIZE               = 64
# TLS 服务端配置
PUBKEY_LISTEN_HOST     = "0.0.0.0"
PUBKEY_LISTEN_PORT     = {tls_port}
TLS_CERT_DIR           = "{os.path.dirname(private_key_path)}/certs"
TLS_CERT_FILE          = "{os.path.dirname(private_key_path)}/certs/daemon.crt"
TLS_KEY_FILE           = "{os.path.dirname(private_key_path)}/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
"""


def _build_client_toml(
    *,
    known_hosts_path: str,
    tls_port: int = _TEST_TLS_PORT,
) -> str:
    """构造测试用 client.toml 内容（客户端 TLS 配置）

    包含客户端必需的 timeout/auth 段，其中 [auth] 段携带 TLS 客户端配置
    （DAEMON_REMOTE_HOST/PORT、known_hosts 路径、TOFU 严格模式）。

    Args:
        known_hosts_path: TOFU 信任存储文件路径
        tls_port: 远程 daemon TLS 端口

    Returns:
        client.toml 文本内容
    """
    return f"""# 客户端配置 —— Phase 6 TLS e2e 测试临时覆写

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
DAEMON_REMOTE_HOST  = "127.0.0.1"
DAEMON_REMOTE_PORT  = {tls_port}
KNOWN_HOSTS_FILE    = "{known_hosts_path}"
TOFU_STRICT         = true
"""


def _parse_cli_json(stdout: str) -> dict:
    """解析 CLI stdout 的 JSON 响应

    跳过 start_daemon 输出的 {"type": "info"} 消息，返回首个非 info JSON 行。

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
    # 回退：返回首个 JSON 行
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError(f"无 JSON 输出: {stdout!r}")


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
        encoding="utf-8",
    )
    assert result.returncode == 0, f"keygen 失败: {result.stderr}"

    private_path = os.path.join(key_dir, "id_ed25519")
    public_path = private_path + ".pub"
    public_line = Path(public_path).read_text(encoding="utf-8").strip()
    return private_path, public_path, public_line


def _assert_auth_passed(result: subprocess.CompletedProcess):
    """断言认证通过：list 响应非 error"""
    assert result.returncode == 0, f"CLI 退出码非 0: stderr={result.stderr!r} stdout={result.stdout!r}"
    resp = _parse_cli_json(result.stdout)
    assert resp.get("type") != "error", \
        f"认证应通过但收到 error 响应: {resp}"


# ═══════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tls_env(tmp_path):
    """TLS 测试环境 — 支持多次启停 daemon 与证书管理

    与 test_pubkey_auth_e2e.py 的 auth_env 不同，本 fixture 提供更细粒度的控制：
    - write_config(): 写入 common/daemon/client 三个 toml + authorized_keys（可多次调用，每次覆写）
    - start(): 启动 daemon 并等待就绪（可多次调用，支持明文/TLS/双端口模式）
    - stop(): 停止 daemon（force-kill 回退确保 TOFU mismatch 时仍能停止）
    - run_cli(): 运行 CLI 子进程（list / stop 等）
    - delete_certs(): 删除证书文件（强制下次 daemon 启动生成新证书）

    teardown 时确保 daemon 停止并恢复三个 toml 文件。

    Yields:
        SimpleNamespace(write_config, start, stop, run_cli, delete_certs, tmp_path)
    """
    # 备份三个 toml 文件（teardown 时逐字节恢复，避免污染生产配置）
    backup_common = _COMMON_TOML.read_bytes()
    backup_daemon = _DAEMON_TOML.read_bytes()
    backup_client = _CLIENT_TOML.read_bytes()
    daemon_running = [False]

    def write_config(
        *,
        enable_token: bool,
        enable_pubkey: bool,
        client_auth_method: str,
        private_key_path: str,
        authorized_keys_path: str,
        authorized_keys_content: str = "",
    ):
        """写入 common/daemon/client 三个 toml + authorized_keys 文件

        配置拆分后同时写入三个文件：
        - common.toml: 共享配置（认证开关、公私钥基础路径）
        - daemon.toml: 服务端 TLS 配置 + daemon 必需段
        - client.toml: 客户端 TLS 配置 + 客户端必需段

        路径参数使用正斜杠避免 TOML 转义。authorized_keys 空内容也会写入文件
        （触发 fail-closed 走 load_authorized_keys 路径）。

        Args:
            enable_token: ENABLE_TOKEN_AUTH
            enable_pubkey: ENABLE_PUBKEY_AUTH
            client_auth_method: CLIENT_AUTH_METHOD（"token" / "pubkey" / "none"）
            private_key_path: 客户端私钥绝对路径
            authorized_keys_path: 服务端 authorized_keys 绝对路径
            authorized_keys_content: authorized_keys 文件内容
        """
        known_hosts_path = str(Path(private_key_path).parent / "known_hosts")
        pk_path = private_key_path.replace("\\", "/")
        ak_path_str = authorized_keys_path.replace("\\", "/")
        kh_path = known_hosts_path.replace("\\", "/")

        # 写入 common.toml（共享配置）
        _COMMON_TOML.write_text(
            _build_common_toml(
                enable_token=enable_token,
                enable_pubkey=enable_pubkey,
                client_auth_method=client_auth_method,
                private_key_path=pk_path,
                authorized_keys_path=ak_path_str,
            ),
            encoding="utf-8",
        )
        # 写入 daemon.toml（服务端 TLS 配置）
        _DAEMON_TOML.write_text(
            _build_daemon_toml(private_key_path=pk_path),
            encoding="utf-8",
        )
        # 写入 client.toml（客户端 TLS 配置）
        _CLIENT_TOML.write_text(
            _build_client_toml(known_hosts_path=kh_path),
            encoding="utf-8",
        )

        ak_path = Path(authorized_keys_path)
        ak_path.parent.mkdir(parents=True, exist_ok=True)
        ak_path.write_text(authorized_keys_content, encoding="utf-8")

    def start():
        """启动 daemon 并等待就绪

        自动检测就绪方式：
        - 明文/双端口模式：_find_daemon_port 通过 SHM + TCP ping 验证
        - pubkey-only 模式：is_running (mutex) + TLS 端口探测验证

        Raises:
            RuntimeError: daemon 6 秒内未就绪
        """
        from src.daemon.lifecycle import start_daemon, _find_daemon_port, is_running
        start_daemon()

        import socket as _socket
        for _ in range(60):
            # 明文/双端口模式：SHM 可用
            if _find_daemon_port() is not None:
                daemon_running[0] = True
                return
            # pubkey-only 模式：无 SHM，检查 mutex + TLS 端口
            if is_running():
                try:
                    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(("127.0.0.1", _TEST_TLS_PORT))
                    probe.close()
                    daemon_running[0] = True
                    return
                except (OSError, _socket.error):
                    pass
            time.sleep(0.1)
        raise RuntimeError("daemon 启动超时（6 秒内未就绪）")

    def stop():
        """停止 daemon — 直接 force-kill 确保 teardown 可靠

        不依赖 stop_daemon() 的 TLS 路由（TOFU mismatch 时 TLS stop 会失败）。
        通过互斥锁定位 PID 并 force-kill，兼容所有模式。
        """
        from src.daemon.lifecycle import is_running, _stop_daemon_force, _cleanup_port
        if is_running():
            _stop_daemon_force()
            for _ in range(30):
                if not is_running():
                    break
                time.sleep(0.1)
        _cleanup_port()
        daemon_running[0] = False

    def run_cli(*args: str) -> subprocess.CompletedProcess:
        """运行 python -m src <args> 子进程

        Args:
            *args: 透传给 CLI 的参数（如 "list", "stop"）

        Returns:
            subprocess.CompletedProcess（含 returncode/stdout/stderr）
        """
        return subprocess.run(
            [sys.executable, "-m", "src", *args],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
        )

    def delete_certs(private_key_path: str):
        """删除证书文件（强制下次 daemon 启动生成新证书）

        删除整个 certs 目录，确保 daemon.crt 和 daemon.key 都被清除。
        用于 TOFU mismatch 测试：删除证书 → 重启 daemon → 新证书 → 指纹不同。

        Args:
            private_key_path: 私钥路径（证书目录为其同级 certs/ 子目录）
        """
        cert_dir = Path(private_key_path).parent / "certs"
        if cert_dir.exists():
            shutil.rmtree(cert_dir)

    try:
        yield SimpleNamespace(
            write_config=write_config,
            start=start,
            stop=stop,
            run_cli=run_cli,
            delete_certs=delete_certs,
            tmp_path=tmp_path,
        )
    finally:
        if daemon_running[0]:
            stop()
        # 恢复三个 toml 文件（无论 daemon 是否成功停止都恢复，避免污染生产配置）
        _COMMON_TOML.write_bytes(backup_common)
        _DAEMON_TOML.write_bytes(backup_daemon)
        _CLIENT_TOML.write_bytes(backup_client)


# ═══════════════════════════════════════════════════════════════
#  TOFU 生命周期测试
# ═══════════════════════════════════════════════════════════════

class TestTofuFirstTrust:
    """TOFU 首次信任 — 首次 TLS 连接后 known_hosts 文件写入指纹"""

    def test_known_hosts_written_after_first_connect(self, tls_env):
        """首次 TLS 连接 → known_hosts 文件包含 127.0.0.1:18767 的 sha256 指纹

        验证步骤：
        1. 生成密钥对，配置 pubkey-only 模式
        2. 确保 known_hosts 文件不存在（首次连接）
        3. 启动 daemon，运行 list（TLS 连接 → TOFU 首次信任）
        4. 读取 known_hosts 文件，验证包含 127.0.0.1:18767 条目
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)
        known_hosts_path = tls_env.tmp_path / "keys" / "known_hosts"

        # 确保 known_hosts 不存在（首次连接前）
        assert not known_hosts_path.exists(), "known_hosts 不应存在（首次连接前）"

        tls_env.write_config(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        result = tls_env.run_cli("list")
        _assert_auth_passed(result)

        # 验证 known_hosts 文件已写入
        assert known_hosts_path.exists(), "known_hosts 文件应已创建（TOFU 首次信任）"

        # 验证文件内容包含 127.0.0.1:18767 的 sha256 指纹
        content = known_hosts_path.read_text(encoding="utf-8")
        pattern = re.compile(r"127\.0\.0\.1:18767\s+sha256:[0-9a-f]+")
        assert pattern.search(content), \
            f"known_hosts 应包含 127.0.0.1:18767 的 sha256 指纹: {content!r}"


class TestTofuFingerprintMatch:
    """TOFU 指纹匹配 — 二次连接指纹验证通过"""

    def test_second_connect_fingerprint_match(self, tls_env):
        """两次 TLS 连接均成功（首次 TOFU 信任 + 二次指纹匹配）

        验证步骤：
        1. 生成密钥对，配置 pubkey-only 模式
        2. 启动 daemon
        3. 第一次 list → TOFU 首次信任 → 通过
        4. 第二次 list → 指纹匹配 → 通过
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)

        tls_env.write_config(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        # 第一次连接：TOFU 首次信任
        result1 = tls_env.run_cli("list")
        _assert_auth_passed(result1)

        # 第二次连接：指纹匹配
        result2 = tls_env.run_cli("list")
        _assert_auth_passed(result2)


class TestTofuFingerprintMismatch:
    """TOFU 指纹不匹配 — 证书重新生成后连接被拒绝"""

    def test_cert_regen_rejected_by_tofu(self, tls_env):
        """证书重新生成后 → 指纹不匹配 → 连接被拒绝

        验证步骤：
        1. 生成密钥对，配置 pubkey-only 模式
        2. 启动 daemon（生成证书 F1）
        3. 第一次 list → TOFU 首次信任 → known_hosts 记录 F1
        4. 停止 daemon，删除证书文件
        5. 重启 daemon（生成新证书 F2，F2 ≠ F1）
        6. 第二次 list → TOFU 指纹不匹配 → 连接被拒绝
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)

        tls_env.write_config(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        # 第一次连接：TOFU 首次信任 → 通过
        result1 = tls_env.run_cli("list")
        _assert_auth_passed(result1)

        # 停止 daemon，删除证书文件
        tls_env.stop()
        tls_env.delete_certs(private_path)

        # 重启 daemon（生成新证书，指纹不同）
        tls_env.start()

        # 第二次连接：TOFU 指纹不匹配 → 被拒绝
        result2 = tls_env.run_cli("list")

        # TLS 连接失败时 __main__ 捕获异常并输出 error JSON，退出码 1
        assert result2.returncode != 0, \
            f"指纹不匹配应导致连接失败: stdout={result2.stdout!r}"
        # 错误消息应包含指纹不匹配提示
        combined = result2.stdout + result2.stderr
        assert "证书指纹不匹配" in combined or "指纹不匹配" in combined, \
            f"错误输出应含 '证书指纹不匹配': stdout={result2.stdout!r} stderr={result2.stderr!r}"


# ═══════════════════════════════════════════════════════════════
#  跨机 stop daemon 测试
# ═══════════════════════════════════════════════════════════════

class TestCrossMachineStop:
    """跨机 stop daemon — TLS 模式停止远程 daemon"""

    def test_tls_stop_daemon(self, tls_env):
        """python -m src stop 通过 TLS 停止远程 daemon

        验证步骤：
        1. 生成密钥对，配置 pubkey-only 模式
        2. 启动 daemon（TLS 监听）
        3. 运行 python -m src stop（CLIENT_AUTH_METHOD=pubkey → TLS stop 路由）
        4. 验证 daemon 已停止（is_running() 返回 False）
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)

        tls_env.write_config(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        # 先通过 list 建立 TOFU 信任（stop 也会 TOFU 信任，但先连接更接近真实场景）
        result_list = tls_env.run_cli("list")
        _assert_auth_passed(result_list)

        # 通过 TLS stop 停止 daemon
        result_stop = tls_env.run_cli("stop")
        assert result_stop.returncode == 0, \
            f"stop 应成功: stdout={result_stop.stdout!r} stderr={result_stop.stderr!r}"

        # 验证 daemon 已停止
        from src.daemon.lifecycle import is_running
        for _ in range(30):
            if not is_running():
                break
            time.sleep(0.1)
        assert not is_running(), "daemon 应已通过 TLS stop 停止"

        # 标记 daemon 已停止，避免 fixture teardown 重复 stop
        # （is_running() 返回 False 后 stop() 是 no-op）


# ═══════════════════════════════════════════════════════════════
#  双端口同时工作测试
# ═══════════════════════════════════════════════════════════════

class TestDualPortSimultaneous:
    """双端口同时工作 — 明文 token + TLS pubkey 并行"""

    def test_both_ports_work_simultaneously(self, tls_env):
        """双端口模式：token 客户端连明文端口 + pubkey 客户端连 TLS 端口

        验证步骤：
        1. 生成密钥对，配置双端口模式（token + pubkey 同时开启）
        2. CLIENT_AUTH_METHOD=token，启动 daemon
        3. 运行 list（token → 明文端口 → HMAC 认证 → 通过）
        4. 重写配置 CLIENT_AUTH_METHOD=pubkey（daemon 仍在运行，双端口监听中）
        5. 运行 list（pubkey → TLS 端口 → Ed25519 认证 → 通过）

        关键点：daemon 启动时同时创建明文和 TLS 两个 Listener，
        客户端根据 CLIENT_AUTH_METHOD 选择连接哪个端口。
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)

        # 第一次写配置：CLIENT_AUTH_METHOD=token（连明文端口）
        tls_env.write_config(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="token",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        # token 模式：连明文端口
        result_token = tls_env.run_cli("list")
        _assert_auth_passed(result_token)

        # 重写配置：CLIENT_AUTH_METHOD=pubkey（连 TLS 端口）
        # daemon 仍在运行，两个 Listener 都在监听
        tls_env.write_config(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )

        # pubkey 模式：连 TLS 端口
        result_pubkey = tls_env.run_cli("list")
        _assert_auth_passed(result_pubkey)
