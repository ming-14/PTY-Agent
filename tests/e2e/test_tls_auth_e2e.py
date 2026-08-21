"""e2e 测试 —— TLS 认证端到端验证

聚焦 test_pubkey_auth_e2e.py 未覆盖的 TLS 特有场景：
1. TOFU 首次信任 — 首次 TLS 连接后 known_hosts 文件写入指纹
2. TOFU 指纹匹配 — 二次连接指纹验证通过
3. TOFU 指纹不匹配 — 证书重新生成后连接被拒绝
4. 跨机 stop daemon — CONNECT_MODE=tls 停止远程 daemon
5. token + tls 同时工作 — token 客户端连 token 监听器 + pubkey 客户端连 tls 监听器

测试策略：
- 自定义 tls_env fixture 支持多次启停 daemon（TOFU mismatch 需要重启）
- 每个测试独立备份/恢复 common/daemon/client 三个 toml
- 通过子进程 python -m src list/stop 验证真实 CLI 链路
- known_hosts 文件内容验证 TOFU 信任存储

环境要求：
- Python 3.8+
- 测试期间会启停 daemon，请确保运行前 daemon 未在运行
"""

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
def _cfg_path(*parts: str) -> Path:
    """按配置目录解析文件路径（隔离临时目录优先，兜底生产 config/）"""
    iso = os.environ.get("PTY_AGENT_CONFIG_DIR")
    base = Path(iso) if iso else Path(_PROJECT_ROOT) / "config"
    return base.joinpath(*parts)

# 测试用 TLS 监听器端口（daemon.toml [listener] TLS_PORT 与 client.toml [connection] TLS_PORT 一致）
_TEST_TLS_PORT = 18767


# ═══════════════════════════════════════════════════════════════
#  辅助函数（与 test_pubkey_auth_e2e.py 结构一致，保持测试独立性）
# ═══════════════════════════════════════════════════════════════

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
    single_instance: bool = True,
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
        single_instance: 单实例互斥锁开关（false 仅 basic/tls 场景生效）

    Returns:
        daemon.toml 文本内容
    """
    return f"""# 守护进程配置 —— e2e 测试临时覆写

SINGLE_INSTANCE = {str(single_instance).lower()}

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
        errors="replace",
    )
    assert result.returncode == 0, f"keygen 失败: {result.stderr}"

    private_path = os.path.join(key_dir, "id_ed25519")
    public_path = private_path + ".pub"
    public_line = Path(public_path).read_text(encoding="utf-8").strip()
    return private_path, public_path, public_line


def _assert_auth_passed(result: subprocess.CompletedProcess):
    """断言认证通过：list 退出码 0 且 stderr 无认证失败

    presenter 层：内容走 stdout、元信息/错误走 stderr、错误以退出码非 0 结束。
    """
    assert result.returncode == 0, f"CLI 退出码非 0: stderr={result.stderr!r} stdout={result.stdout!r}"
    assert "Authentication failed" not in result.stderr, \
        f"认证应通过但 stderr 出现认证失败: {result.stderr!r}"


# ═══════════════════════════════════════════════════════════════
#  Fixture
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def tls_env(tmp_path, config_reloader):
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
    backup_common = _cfg_path("common.toml").read_bytes()
    backup_daemon = _cfg_path("daemon", "daemon.toml").read_bytes()
    backup_client = _cfg_path("client", "client.toml").read_bytes()
    daemon_running = [False]
    listener_flags = {"basic": False, "token": False, "tls": False}

    def write_config(
        *,
        enable_token: bool,
        enable_pubkey: bool,
        client_auth_method: str,
        private_key_path: str,
        authorized_keys_path: str,
        authorized_keys_content: str = "",
        single_instance: bool = True,
    ):
        """写入 common/daemon/client 三个 toml + authorized_keys 文件

        三监听器架构：daemon.toml [listener] 段独立 enabled，client.toml
        [connection] 段 CONNECT_MODE 选择连接。旧开关语义映射：
        enable_token → token 监听器；enable_pubkey → tls 监听器；
        两者都关 → basic 监听器。连接方式 token/pubkey/none → token/tls/basic。

        路径参数使用正斜杠避免 TOML 转义。authorized_keys 空内容也会写入文件
        （触发 fail-closed 走 load_authorized_keys 路径）。

        Args:
            enable_token: 是否启用 token 监听器
            enable_pubkey: 是否启用 tls 监听器
            client_auth_method: 连接方式（"token" / "pubkey" / "none"）
            private_key_path: 客户端私钥绝对路径
            authorized_keys_path: 服务端 authorized_keys 绝对路径
            authorized_keys_content: authorized_keys 文件内容
            single_instance: 单实例互斥锁开关（false 仅 basic/tls 场景生效）
        """
        known_hosts_path = str(Path(private_key_path).parent / "known_hosts")
        pk_path = private_key_path.replace("\\", "/")
        ak_path_str = authorized_keys_path.replace("\\", "/")
        kh_path = known_hosts_path.replace("\\", "/")

        basic = not enable_token and not enable_pubkey
        connect_mode = {
            "token": "token",
            "pubkey": "tls",
            "none": "basic",
        }[client_auth_method]
        listener_flags["basic"] = basic
        listener_flags["token"] = enable_token
        listener_flags["tls"] = enable_pubkey

        # 写入 common.toml（共享配置）
        _cfg_path("common.toml").write_text(_build_common_toml(), encoding="utf-8")
        # 写入 daemon.toml（三监听器 + 服务端认证配置）
        _cfg_path("daemon", "daemon.toml").write_text(
            _build_daemon_toml(
                basic=basic,
                token=enable_token,
                tls=enable_pubkey,
                private_key_path=pk_path,
                authorized_keys_path=ak_path_str,
                single_instance=single_instance,
            ),
            encoding="utf-8",
        )
        # 写入 client.toml（连接方式 + 客户端认证配置）
        _cfg_path("client", "client.toml").write_text(
            _build_client_toml(
                connect_mode=connect_mode,
                private_key_path=pk_path,
                known_hosts_path=kh_path,
            ),
            encoding="utf-8",
        )

        ak_path = Path(authorized_keys_path)
        ak_path.parent.mkdir(parents=True, exist_ok=True)
        ak_path.write_text(authorized_keys_content, encoding="utf-8")
        # 写入测试 config 后重载进程内 config（见 conftest.reload_config）
        config_reloader()

    def start():
        """启动 daemon 并等待就绪

        任一台启用监听器 TCP 可达即就绪（TLS 监听器也经 TCP 探测）；
        不依赖单实例锁（SINGLE_INSTANCE=false 多实例场景下锁不存在）。

        Raises:
            RuntimeError: daemon 6 秒内未就绪
        """
        from src.daemonctl import start_daemon
        start_daemon()

        import socket as _socket
        probe_ports = []
        if listener_flags["basic"]:
            probe_ports.append(10521)
        if listener_flags["token"]:
            probe_ports.append(10520)
        if listener_flags["tls"]:
            probe_ports.append(_TEST_TLS_PORT)
        for _ in range(60):
            for port in probe_ports:
                try:
                    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(("127.0.0.1", port))
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
        from src.daemonctl import is_running, _stop_daemon_force, _cleanup_credentials
        if is_running():
            _stop_daemon_force()
            for _ in range(30):
                if not is_running():
                    break
                time.sleep(0.1)
        _cleanup_credentials()
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
            errors="replace",
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
        try:
            if daemon_running[0]:
                stop()
        finally:
            # 恢复三个 toml 文件（无论 daemon 是否成功停止都恢复，避免污染生产配置）
            _cfg_path("common.toml").write_bytes(backup_common)
            _cfg_path("daemon", "daemon.toml").write_bytes(backup_daemon)
            _cfg_path("client", "client.toml").write_bytes(backup_client)


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
    """跨机 stop daemon — CONNECT_MODE=tls 停止远程 daemon"""

    def test_tls_stop_daemon(self, tls_env):
        """python -m src stop 通过 TLS 停止远程 daemon

        验证步骤：
        1. 生成密钥对，配置 tls 监听器
        2. 启动 daemon（TLS 监听）
        3. 运行 python -m src stop（CONNECT_MODE=tls → TLS stop 路由）
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
        from src.daemonctl import is_running
        for _ in range(30):
            if not is_running():
                break
            time.sleep(0.1)
        assert not is_running(), "daemon 应已通过 TLS stop 停止"

        # 标记 daemon 已停止，避免 fixture teardown 重复 stop
        # （is_running() 返回 False 后 stop() 是 no-op）


# ═══════════════════════════════════════════════════════════════
#  无锁多实例测试
# ═══════════════════════════════════════════════════════════════

class TestNoSingleInstance:
    """SINGLE_INSTANCE=false —— 无锁多实例并存（仅 basic/tls 场景）"""

    def test_tls_no_lock_multi_instance(self, tls_env):
        """无锁模式：不创建互斥锁，同机双实例并存

        验证步骤：
        1. SINGLE_INSTANCE=false + tls 监听器，启动 daemon
        2. 互斥锁不存在（is_running()=False），但 TLS 正常服务
        3. 改 daemon.toml 端口，再启动第二个实例 → 两实例并存均可访问
        4. 逐个经 TLS stop 清理
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)
        second_port = _TEST_TLS_PORT + 1  # 18768

        tls_env.write_config(
            enable_token=False,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
            single_instance=False,
        )
        tls_env.start()

        # 无锁模式：互斥锁不存在，但 daemon 经 TLS 正常服务
        from src.daemonctl import is_running
        assert is_running() is False, "SINGLE_INSTANCE=false 时不应存在互斥锁"
        result = tls_env.run_cli("list")
        _assert_auth_passed(result)

        try:
            # 同机并发第二实例：改 daemon.toml 端口 + client 指向 + 再次启动
            daemon_content = _cfg_path("daemon", "daemon.toml").read_text(encoding="utf-8")
            daemon_content = re.sub(
                r"(TLS_PORT\s*=\s*)\d+", rf"\g<1>{second_port}", daemon_content
            )
            _cfg_path("daemon", "daemon.toml").write_text(daemon_content, encoding="utf-8")
            client_content = _cfg_path("client", "client.toml").read_text(encoding="utf-8")
            client_content = re.sub(
                r"(TLS_PORT\s*=\s*)\d+", rf"\g<1>{second_port}", client_content
            )
            _cfg_path("client", "client.toml").write_text(client_content, encoding="utf-8")

            tls_env.start()

            # 第二实例可达（stop 前先 TOFU 信任其证书指纹）
            result2 = tls_env.run_cli("list")
            _assert_auth_passed(result2)

            # 第一实例仍可达（18767）
            first_client = _cfg_path("client", "client.toml").read_text(encoding="utf-8")
            first_client = re.sub(
                r"(TLS_PORT\s*=\s*)\d+", rf"\g<1>{_TEST_TLS_PORT}", first_client
            )
            _cfg_path("client", "client.toml").write_text(first_client, encoding="utf-8")
            result1 = tls_env.run_cli("list")
            _assert_auth_passed(result1)

            # 清理：先停第二实例（client 指向 18768），再停第一实例（18767）
            _cfg_path("client", "client.toml").write_text(client_content, encoding="utf-8")
            stop2 = tls_env.run_cli("stop")
            assert stop2.returncode == 0, f"第二实例 stop 失败: {stop2.stderr}"
            _cfg_path("client", "client.toml").write_text(first_client, encoding="utf-8")
            stop1 = tls_env.run_cli("stop")
            assert stop1.returncode == 0, f"第一实例 stop 失败: {stop1.stderr}"

            # 两端口均应已释放
            import socket as _socket
            for port in (_TEST_TLS_PORT, second_port):
                try:
                    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(("127.0.0.1", port))
                    probe.close()
                    assert False, f"端口 {port} 应已释放（daemon 未停干净）"
                except (OSError, _socket.error):
                    pass
        finally:
            # 兜底清理：残留实例逐个 stop（client 指向最后已知端口）
            if is_running() is False:
                for port in (second_port, _TEST_TLS_PORT):
                    try:
                        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        probe.settimeout(0.3)
                        probe.connect(("127.0.0.1", port))
                        probe.close()
                        c = _cfg_path("client", "client.toml").read_text(encoding="utf-8")
                        c = re.sub(r"(TLS_PORT\s*=\s*)\d+", rf"\g<1>{port}", c)
                        _cfg_path("client", "client.toml").write_text(c, encoding="utf-8")
                        subprocess.run(
                            [sys.executable, "-m", "src", "stop"],
                            cwd=_PROJECT_ROOT, capture_output=True, text=True,
                            timeout=30, encoding="utf-8", errors="replace",
                        )
                    except (OSError, socket.error):
                        pass


# ═══════════════════════════════════════════════════════════════
#  token + tls 监听器同时工作测试
# ═══════════════════════════════════════════════════════════════

class TestDualPortSimultaneous:
    """token + tls 监听器同时工作 — 客户端按 CONNECT_MODE 选连"""

    def test_both_ports_work_simultaneously(self, tls_env):
        """token + tls：token 客户端连 token 监听器 + pubkey 客户端连 tls 监听器

        验证步骤：
        1. 生成密钥对，配置 token + tls 监听器
        2. CONNECT_MODE=token，启动 daemon
        3. 运行 list（token → token 监听器 → HMAC 认证 → 通过）
        4. 重写配置 CONNECT_MODE=tls（daemon 仍在运行，两个监听器都在）
        5. 运行 list（tls → tls 监听器 → Ed25519 认证 → 通过）

        关键点：daemon 启动时同时创建 token/tls 两个 Listener，
        客户端根据 CONNECT_MODE 选择连接端口。
        """
        private_path, _, public_line = _generate_keypair(tls_env.tmp_path)

        # 第一次写配置：CONNECT_MODE=token（连 token 监听器）
        tls_env.write_config(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="token",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )
        tls_env.start()

        # token 模式：连 token 监听器
        result_token = tls_env.run_cli("list")
        _assert_auth_passed(result_token)

        # 重写配置：CONNECT_MODE=tls（连 tls 监听器）
        # daemon 仍在运行，两个 Listener 都在监听
        tls_env.write_config(
            enable_token=True,
            enable_pubkey=True,
            client_auth_method="pubkey",
            private_key_path=private_path,
            authorized_keys_path=str(tls_env.tmp_path / "authorized_keys"),
            authorized_keys_content=public_line + "\n",
        )

        # tls 模式：连 tls 监听器
        result_pubkey = tls_env.run_cli("list")
        _assert_auth_passed(result_pubkey)
