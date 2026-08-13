"""临时复现脚本 v2：用 start_daemon 启动（生产方式），读日志文件找根因

配置拆分后需要同时写 common.toml / daemon.toml / client.toml 三个文件：
- common.toml: 共享配置（认证开关、公私钥基础路径）
- daemon.toml: 服务端 TLS 配置 + daemon 必需段
- client.toml: 客户端 TLS 配置 + 客户端必需段
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 三个 toml 路径（测试期间临时覆写，finally 中逐字节恢复）
_COMMON_TOML = _PROJECT_ROOT / "src" / "config" / "common.toml"
_DAEMON_TOML = _PROJECT_ROOT / "src" / "config" / "daemon.toml"
_CLIENT_TOML = _PROJECT_ROOT / "src" / "config" / "client.toml"

# 备份三个文件（finally 中恢复，避免污染生产配置）
backup_common = _COMMON_TOML.read_bytes()
backup_daemon = _DAEMON_TOML.read_bytes()
backup_client = _CLIENT_TOML.read_bytes()

try:
    # 1. 生成密钥对
    key_dir = _PROJECT_ROOT / "tests" / "e2e" / "_repro_keys"
    if key_dir.exists():
        shutil.rmtree(key_dir)
    key_dir.mkdir(parents=True)
    gen = subprocess.run(
        [sys.executable, "-m", "src", "keygen", "--key-dir", str(key_dir),
         "--comment", "repro@test"],
        cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=30, encoding="utf-8",
    )
    assert gen.returncode == 0, f"keygen 失败: {gen.stderr}"
    print(f"[REPRO] keygen OK")

    private_path = str(key_dir / "id_ed25519")
    public_path = str(key_dir / "id_ed25519.pub")
    public_line = Path(public_path).read_text(encoding="utf-8").strip()

    # 2. 写 authorized_keys
    ak_path = _PROJECT_ROOT / "tests" / "e2e" / "_repro_authorized_keys"
    ak_path.write_text(public_line + "\n", encoding="utf-8")

    # 路径用正斜杠避免 TOML 转义
    pk_path = private_path.replace("\\", "/")
    pub_path = public_path.replace("\\", "/")
    ak_path_str = str(ak_path).replace("\\", "/")
    key_dir_str = str(key_dir).replace("\\", "/")
    kh_path = f"{key_dir_str}/known_hosts"

    # 3. 写 common.toml（仅共享配置 + 公私钥基础路径，pubkey-only）
    common_config = f"""# 共有配置 —— Phase 5 e2e 复现

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
ENABLE_TOKEN_AUTH = false
ENABLE_PUBKEY_AUTH = true
CLIENT_AUTH_METHOD = "pubkey"
PUBKEY_ALGORITHM       = "ed25519"
PUBKEY_PRIVATE_KEY_PATH = "{pk_path}"
PUBKEY_PUBLIC_KEY_PATH  = "{pub_path}"
PUBKEY_AUTHORIZED_KEYS  = "{ak_path_str}"
PUBKEY_KEY_DIR          = "{key_dir_str}"
"""
    _COMMON_TOML.write_text(common_config, encoding="utf-8")
    print(f"[REPRO] common.toml 已写入（pubkey-only）")

    # 4. 写 daemon.toml（服务端 TLS 配置 + daemon 必需段）
    daemon_config = f"""# 守护进程配置 —— Phase 5 e2e 复现

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
PUBKEY_LISTEN_PORT     = 18767
TLS_CERT_DIR           = "{key_dir_str}/certs"
TLS_CERT_FILE          = "{key_dir_str}/certs/daemon.crt"
TLS_KEY_FILE           = "{key_dir_str}/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
"""
    _DAEMON_TOML.write_text(daemon_config, encoding="utf-8")
    print(f"[REPRO] daemon.toml 已写入（服务端 TLS 配置）")

    # 5. 写 client.toml（客户端 TLS 配置）
    client_config = f"""# 客户端配置 —— Phase 5 e2e 复现

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
DAEMON_REMOTE_HOST  = "127.0.0.1"
DAEMON_REMOTE_PORT  = 18767
KNOWN_HOSTS_FILE    = "{kh_path}"
TOFU_STRICT         = true
"""
    _CLIENT_TOML.write_text(client_config, encoding="utf-8")
    print(f"[REPRO] client.toml 已写入（客户端 TLS 配置）")

    # 6. 启动 daemon（用 start_daemon，生产方式）
    from src.client.lifecycle import start_daemon, _find_daemon_port, stop_daemon, is_running
    print(f"[REPRO] 启动 daemon（start_daemon）...")
    start_daemon()

    # 7. 等待就绪（pubkey-only 模式无 SHM，用 is_running + TLS 探测）
    port = None
    import socket as _socket
    for _ in range(60):
        port = _find_daemon_port()
        if port is not None:
            break
        # pubkey-only 模式：无 SHM，检查 TLS 端口可达
        if is_running():
            try:
                probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                probe.settimeout(0.5)
                probe.connect(("127.0.0.1", 18767))
                probe.close()
                print(f"[REPRO] TLS 端口 18767 已就绪（pubkey-only 模式）")
                break
            except (OSError, _socket.error):
                pass
        time.sleep(0.1)
    print(f"[REPRO] daemon port={port}, is_running={is_running()}")

    if port is None and not is_running():
        print(f"[REPRO] daemon 未就绪，读日志...")
    else:
        # 8. 调用 list（即使 port=None，pubkey-only 模式下 daemon 已通过 TLS 就绪）
        print(f"[REPRO] 调用 python -m src list ...")
        list_result = subprocess.run(
            [sys.executable, "-m", "src", "list"],
            cwd=str(_PROJECT_ROOT), capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        print(f"[REPRO] list returncode={list_result.returncode}")
        print(f"[REPRO] list stdout={list_result.stdout!r}")
        print(f"[REPRO] list stderr={list_result.stderr!r}")

    # 9. 找最新的 daemon 日志文件
    from src.config.daemon import LOG_DIR
    logs_dir = Path(LOG_DIR)
    log_files = sorted(logs_dir.glob("daemon-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if log_files:
        latest_log = log_files[0]
        print(f"[REPRO] 最新日志: {latest_log}")
        print(f"[REPRO] === daemon 日志（最后 80 行）===")
        log_lines = latest_log.read_text(encoding='utf-8', errors='replace').splitlines()
        for line in log_lines[-80:]:
            print(f"  {line}")

    # 10. 停止 daemon
    print(f"[REPRO] 停止 daemon ...")
    stop_daemon(force=True)
    for _ in range(30):
        if not is_running():
            break
        time.sleep(0.1)

finally:
    # 恢复三个 toml 文件（无论是否成功停止都恢复，避免污染生产配置）
    _COMMON_TOML.write_bytes(backup_common)
    _DAEMON_TOML.write_bytes(backup_daemon)
    _CLIENT_TOML.write_bytes(backup_client)
    print(f"[REPRO] common.toml / daemon.toml / client.toml 已恢复")
