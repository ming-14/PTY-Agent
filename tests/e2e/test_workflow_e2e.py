"""e2e 测试 —— workflow 脚本编排端到端验证

覆盖场景（走真实 daemon + CLI 子进程链路）：
1. 串行 workflow：exec(python REPL) → send(变量插值) → read(条件判定) → wait，
   验证步骤 done 状态、output 内容、trigger 命中
2. 并行步骤：depends_on: [] 的步骤并行执行（总耗时显著小于串行和）
3. 失败传播：on_error=fail 时依赖失败的步骤 skipped，workflow 状态 failed
4. if 条件为假 → 步骤跳过
5. workflow cancel：等待中的步骤被取消，run 状态 cancelled
6. 非法定义文件 → run 返回定义错误

测试策略：
- 备份 common/daemon/client 三个 toml → 写入测试配置（token 模式 + [workflow] 段）
  → 启动 daemon（token 模式自动 SHM 分发认证）→ 以子进程跑 workflow 命令
  → 停止 daemon → 恢复 toml
"""

import os
import sys
import time
from pathlib import Path

import pytest

# 将项目根目录加入 sys.path，便于 import src.* 与以子进程方式运行 ``python -m src``
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_COMMON_TOML = Path(_PROJECT_ROOT) / "config" / "common.toml"
_DAEMON_TOML = Path(_PROJECT_ROOT) / "config" / "daemon" / "daemon.toml"
_CLIENT_TOML = Path(_PROJECT_ROOT) / "config" / "client" / "client.toml"


def _build_common_toml() -> str:
    return """# 共有配置（e2e 测试临时覆写）
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


def _build_daemon_toml() -> str:
    return """# 守护进程配置（e2e 测试临时覆写）
SINGLE_INSTANCE = true

[listener]
BASIC_ENABLED  = false
BASIC_HOST     = "0.0.0.0"
BASIC_PORT     = 10521
BASIC_PASSWORD = ""

TOKEN_ENABLED = true
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
JOB_OBJECT_NAME_PREFIX = "Local\\\\PTYJob_"

[input_limit]
MAX_SESSIONS = 50

[workflow]
WORKFLOW_MAX_RUNS         = 50
WORKFLOW_DEFAULT_PARALLEL = 4
WORKFLOW_STEP_OUTPUT_LIMIT = 4096
WORKFLOW_MAX_FILE_SIZE   = 1048576

[auth]
AUTH_TOKEN_ROTATE_INTERVAL = 1800
AUTH_TOKEN_GRACE_PERIOD    = 120
PUBKEY_ALGORITHM       = "ed25519"
PUBKEY_AUTHORIZED_KEYS = "~/.pty-agent/authorized_keys"
PUBKEY_KEY_DIR         = "~/.pty-agent/keys"
TLS_CERT_DIR           = "~/.pty-agent/certs"
TLS_CERT_FILE          = "~/.pty-agent/certs/daemon.crt"
TLS_KEY_FILE           = "~/.pty-agent/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
"""


def _build_client_toml() -> str:
    # 注意：不得包含 [logging] 段 —— 日志配置已拆分到 config/client/logging.toml
    # （测试不覆写该文件），重复定义 CLIENT_LOG_LEVEL 会触发配置合并冲突
    return """# 客户端配置（e2e 测试临时覆写）
[connection]
CONNECT_MODE = "token"
BASIC_HOST     = "127.0.0.1"
BASIC_PORT     = 10521
BASIC_PASSWORD = ""
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
"""


def _run_cli(*args: str, timeout: float = 60) -> dict:
    """进程内走真实 wire 协议调用 workflow 命令，返回扁平响应 body

    presenter 重构后 CLI 不再输出 JSON（紧凑文本），结构化断言改走
    Client._send_recv —— 仍是真实 daemon + 真实 TCP/SHM 认证链路，
    仅跳过 CLI 呈现层（呈现层由单测/其他 e2e 覆盖）。
    """
    from src.client.transport import Client
    from src.protocol.message import Message

    # 进程内多 Client 实例复用同一 pytest 进程：_load_signer_and_providers 幂等
    # （Message 全局签名器已设则跳过），第二次起的 Client 会丢失凭证提供者导致
    # 请求不带 token 被 daemon 拒绝。每次调用前重置全局签名器，等价于新 CLI 进程。
    Message.set_outbound_signer(None)
    Message.set_inbound_verifier(None)

    client = Client()
    action = args[1]
    if action == "run":
        text = Path(args[2]).read_text(encoding="utf-8")
        return client._send_recv(
            {"type": "workflow", "action": "run", "definition": text},
            autostart=True,
        )
    if action == "show":
        return client._send_recv(
            {"type": "workflow", "action": "show", "runId": args[2]},
            autostart=True,
        )
    if action == "cancel":
        return client._send_recv(
            {"type": "workflow", "action": "cancel", "runId": args[2]},
            autostart=True,
        )
    if action == "list":
        return client._send_recv({"type": "workflow", "action": "list"}, autostart=True)
    raise ValueError(f"未知 workflow 子命令: {args}")


@pytest.fixture
def workflow_env(tmp_path):
    """workflow e2e 环境：备份 toml → token 模式 daemon → teardown 恢复

    覆写与恢复均在 try/finally 内：任何一步（含 import）失败都必须还原配置，
    避免测试临时配置泄漏到工作区。
    """
    # import 提前：若配置/环境本身有问题，在覆写任何文件之前就失败
    from src.daemonctl import is_running, start_daemon, stop_daemon

    backup_common = _COMMON_TOML.read_bytes()
    backup_daemon = _DAEMON_TOML.read_bytes()
    backup_client = _CLIENT_TOML.read_bytes()

    def _restore():
        _COMMON_TOML.write_bytes(backup_common)
        _DAEMON_TOML.write_bytes(backup_daemon)
        _CLIENT_TOML.write_bytes(backup_client)

    try:
        _COMMON_TOML.write_text(_build_common_toml(), encoding="utf-8", errors="replace")
        _DAEMON_TOML.write_text(_build_daemon_toml(), encoding="utf-8", errors="replace")
        _CLIENT_TOML.write_text(_build_client_toml(), encoding="utf-8", errors="replace")

        start_daemon()
        try:
            # 等待 daemon 就绪：单实例锁 + token 端口 TCP 可达（锁先于 SHM 写入，
            # 仅等锁会让进程内 Client 连上时 HMAC 密钥可能尚未发布到 SHM）
            import socket as _socket

            ready = False
            for _ in range(100):
                if not is_running():
                    time.sleep(0.1)
                    continue
                try:
                    probe = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    probe.settimeout(0.5)
                    probe.connect(("127.0.0.1", 10520))
                    probe.close()
                    ready = True
                    break
                except OSError:
                    time.sleep(0.1)
            assert ready, "daemon 未就绪"
            yield SimpleNamespace(tmp_path=tmp_path)
        finally:
            try:
                stop_daemon(force=True)
            except Exception:
                pass
    finally:
        _restore()


from types import SimpleNamespace  # noqa: E402

_SERIAL_YAML = """name: e2e-serial
vars:
  msg: hello-workflow
steps:
  - id: py
    type: exec
    session: e2e-py
    command: "python -u -i"
    trigger: ">>>"
    timeout: 30
  - id: send
    type: send
    session: e2e-py
    input: "print('{{vars.msg}}')"
    trigger: "hello-workflow"
    timeout: 30
  - id: read_back
    type: read
    session: e2e-py
    lines: 8
    if: "send.reason == 'trigger_matched'"
  - id: tail
    type: wait
    seconds: 1
"""

_PARALLEL_YAML = """name: e2e-parallel
steps:
  - id: p1
    type: wait
    seconds: 2
  - id: p2
    type: wait
    seconds: 2
    depends_on: []
  - id: p3
    type: wait
    seconds: 2
    depends_on: []
  - id: after
    type: wait
    seconds: 1
    depends_on: [p1, p2, p3]
"""

_FAIL_YAML = """name: e2e-fail
steps:
  - id: bad
    type: exec
    session: e2e-bad
    command: "definitely_not_exists_cmd_xyz"
    timeout: 10
  - id: orphan
    type: wait
    seconds: 1
    depends_on: [bad]
"""

_DEF_ERROR_YAML = """name: e2e-def-error
steps:
  - id: bad
    type: nope
    session: x
"""


def _wait_run(run_id: str, timeout: float = 40) -> dict:
    """轮询 workflow show 直到运行进入终态，返回最终 snapshot"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = _run_cli("workflow", "show", run_id)
        run = resp["run"]
        if run["status"] != "running":
            return run
        time.sleep(0.3)
    raise TimeoutError("workflow %s 未在 %ss 内结束" % (run_id, timeout))


def test_workflow_serial(workflow_env):
    """串行 workflow：变量插值 + trigger + 条件判定 + wait 全链路"""
    wf_file = workflow_env.tmp_path / "serial.yaml"
    wf_file.write_text(_SERIAL_YAML, encoding="utf-8")

    resp = _run_cli("workflow", "run", str(wf_file))
    assert resp["status"] == "started"
    run_id = resp["runId"]

    run = _wait_run(run_id)
    assert run["status"] == "done"
    assert {s["status"] for s in run["steps"]} == {"done"}

    by_id = {s["id"]: s for s in run["steps"]}
    assert by_id["py"]["reason"] == "trigger_matched"
    assert ">>>" in by_id["py"]["output"]  # REPL 启动成功
    assert by_id["send"]["reason"] == "trigger_matched"
    assert "hello-workflow" in by_id["send"]["output"]  # 变量插值生效
    assert by_id["read_back"]["status"] == "done"  # if 条件为真未跳过
    assert by_id["tail"]["reason"] == "ok"


def test_workflow_condition_skip(workflow_env):
    """if 条件为假 → 步骤跳过"""
    wf_file = workflow_env.tmp_path / "cond.yaml"
    wf_file.write_text(
        _SERIAL_YAML.replace(
            "if: \"send.reason == 'trigger_matched'\"",
            "if: \"send.reason == 'never'\"",
        ),
        encoding="utf-8",
    )
    resp = _run_cli("workflow", "run", str(wf_file))
    run = _wait_run(resp["runId"])
    by_id = {s["id"]: s for s in run["steps"]}
    assert by_id["read_back"]["status"] == "skipped"
    assert "if 条件为假" in by_id["read_back"]["note"]


def test_workflow_condition_lowercase_false(workflow_env):
    """if: "false"（YAML/JSON 小写习惯）→ skipped 而非求值错误 FAILED"""
    wf_file = workflow_env.tmp_path / "cond_false.yaml"
    wf_file.write_text(
        _SERIAL_YAML.replace(
            "if: \"send.reason == 'trigger_matched'\"", "if: \"false\""
        ),
        encoding="utf-8",
    )
    resp = _run_cli("workflow", "run", str(wf_file))
    run = _wait_run(resp["runId"])
    assert run["status"] == "done"  # 不应因条件求值失败而 failed
    by_id = {s["id"]: s for s in run["steps"]}
    assert by_id["read_back"]["status"] == "skipped"


def test_workflow_parallel(workflow_env):
    """depends_on 空列表 → 无依赖，步骤并行执行（总耗时 < 串行和）"""
    wf_file = workflow_env.tmp_path / "parallel.yaml"
    wf_file.write_text(_PARALLEL_YAML, encoding="utf-8")
    resp = _run_cli("workflow", "run", str(wf_file))
    run = _wait_run(resp["runId"])
    assert run["status"] == "done"
    by_id = {s["id"]: s for s in run["steps"]}
    # 3 个 2s 步骤并行：同时开始
    pids = ("p1", "p2", "p3")
    starts = sorted(s["started_at"] for k, s in by_id.items() if k in pids)
    assert starts[2] - starts[0] < 0.5, "并行步骤未同时开始: %s" % starts
    # 总耗时（start → after 结束）应 < 串行 3*2+1=7s + 余量
    total = by_id["after"]["ended_at"] - by_id["p1"]["started_at"]
    assert total < 5.5, "并行未生效，总耗时 %.2fs" % total


def test_workflow_fail_propagation(workflow_env):
    """on_error=fail：依赖失败的步骤 skipped，workflow 标记 failed"""
    wf_file = workflow_env.tmp_path / "fail.yaml"
    wf_file.write_text(_FAIL_YAML, encoding="utf-8")
    resp = _run_cli("workflow", "run", str(wf_file))
    run = _wait_run(resp["runId"], timeout=30)
    assert run["status"] == "failed"
    by_id = {s["id"]: s for s in run["steps"]}
    assert by_id["bad"]["status"] == "failed"
    assert by_id["bad"]["attempts"] == 1  # retry 默认 0，一次尝试即失败
    assert by_id["orphan"]["status"] == "skipped"


def test_workflow_cancel(workflow_env):
    """运行中取消：执行中步骤 cancelled，未开始步骤 skipped（对齐文档）"""
    wf_file = workflow_env.tmp_path / "cancel.yaml"
    wf_file.write_text(
        """name: e2e-cancel
steps:
  - id: long_wait
    type: wait
    seconds: 60
  - id: never_start
    type: wait
    seconds: 1
    depends_on: [long_wait]
""",
        encoding="utf-8",
    )
    resp = _run_cli("workflow", "run", str(wf_file))
    run_id = resp["runId"]
    time.sleep(1.0)
    cancel_resp = _run_cli("workflow", "cancel", run_id)
    assert cancel_resp["status"] in ("cancelling",)
    run = _wait_run(run_id, timeout=15)
    assert run["status"] == "cancelled"
    by_id = {s["id"]: s for s in run["steps"]}
    assert by_id["long_wait"]["status"] == "cancelled"
    assert by_id["never_start"]["status"] == "skipped"


def test_workflow_definition_error(workflow_env):
    """非法定义（type 不存在）→ run 返回定义错误，不产生运行"""
    wf_file = workflow_env.tmp_path / "bad.yaml"
    wf_file.write_text(_DEF_ERROR_YAML, encoding="utf-8")
    resp = _run_cli("workflow", "run", str(wf_file))
    assert resp["type"] == "error"
    assert "定义错误" in resp["message"]


def test_workflow_list(workflow_env):
    """workflow list 返回运行列表"""
    resp = _run_cli("workflow", "list")
    assert resp["type"] == "workflow"
    assert isinstance(resp["runs"], list)