"""test 配置：添加项目根目录到 sys.path 以便导入 src 包"""
import importlib
import logging
import shutil
import sys
import os
from pathlib import Path

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_bin_dir = os.path.join(_project_root, "bin")
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)

# 需在 sys.path 设置之后导入（依赖项目根路径解析 src 包）
from src.protocol.message import Message


# 进程内 config 重载链：e2e 测试改 config 文件后，进程内已 import 的模块仍持有
# 旧 config 引用（from ..config.client import X 绑定值，reload config 不更新 X）。
# 按依赖顺序 reload：被依赖的 config 子模块先，持有 config 引用的模块后。
# 仅 reload 已加载的模块（生产模块未 import 时跳过）。
_RELOAD_CHAIN = (
    "src.config._loader",
    "src.config.common",
    "src.config.shared",
    "src.config.client",
    "src.config.daemon",
    "src.ipc.single_instance",
    "src.ipc.shm",
    "src.common.shells",
    "src.auth.token",
    "src.auth.password",
    "src.client.msg",
    "src.execution.conditions",
    "src.execution.filtering",
    "src.execution.output_policy",
    "src.execution.response",
    "src.execution.utils",
    "src.execution.execution",
    "src.session.buffer",
    "src.session.trigger_matcher",
    "src.session.events_history",
    "src.client.daemonctl",
    "src.client.presenter",
    "src.client.connection",
    "src.client.defaults",
    "src.client.commands",
    "src.client.transport",
)


def reload_config():
    """重载进程内 config 及所有持有 config 引用的模块

    e2e 测试改 config 文件后调用：config 子模块的 lru_cache + 模块级 globals
    在 import 时固定，写文件不触发重读。此函数按依赖顺序 reload 已加载的
    模块，使进程内 config 与磁盘一致。生产环境不调用（config 不运行时改）。
    """
    for name in _RELOAD_CHAIN:
        mod = sys.modules.get(name)
        if mod is not None:
            importlib.reload(mod)


@pytest.fixture
def config_reloader():
    """返回 reload_config 函数，供 e2e fixture 在写 config 文件后调用

    用 fixture 注入而非直接 import，避免测试文件依赖 conftest 路径。
    """
    return reload_config


@pytest.fixture(autouse=True)
def _isolate_logging():
    """autouse: 给 root logger 挂 NullHandler，防止测试日志输出到 stderr 干扰

    业务模块的 logger propagate=False 时不传播到 root，WARNING+ 日志会走
    logging.lastResort 输出到 stderr。此 fixture 不禁用日志（保留 caplog 能力），
    仅在 root 挂 NullHandler 兜底。
    """
    root = logging.getLogger()
    if not any(isinstance(h, logging.NullHandler) for h in root.handlers):
        root.addHandler(logging.NullHandler())
    yield


@pytest.fixture(autouse=True)
def _clear_message_signers():
    """teardown 时清除 Message 线程局部签名器，防止跨测试污染（e2e 测试的 stop_daemon 可能设置主线程签名器）"""
    yield
    Message.set_outbound_signer(None)
    Message.set_inbound_verifier(None)
    try:
        del Message._tls.response_wrapper
    except AttributeError:
        pass


@pytest.fixture(scope="session", autouse=True)
def iso_config(tmp_path_factory):
    """e2e 配置隔离：把 config/ 完整复制到临时目录，经 PTY_AGENT_CONFIG_DIR
    重定向配置加载；测试写入的 common/daemon/client toml 全部落在隔离目录，
    生产配置永不被触碰（此前测试直接改写生产 config，进程被强杀时 teardown
    未执行导致污染残留且后续无法自愈）。

    环境变量必须在调用方（daemon 子进程 / CLI 子进程）中生效：子进程继承
    os.environ，加载器（src/config/_loader.py）由此定位配置目录。
    """
    src_cfg = Path(_project_root) / "config"
    iso_dir = tmp_path_factory.mktemp("pty-agent-config")
    shutil.copytree(src_cfg, iso_dir, dirs_exist_ok=True)
    os.environ["PTY_AGENT_CONFIG_DIR"] = str(iso_dir)
    try:
        yield iso_dir
    finally:
        os.environ.pop("PTY_AGENT_CONFIG_DIR", None)


@pytest.fixture(scope="module")
def web_daemon():
    """web e2e 守护进程 fixture：未运行时自动启动，测试后停止自启实例

    web e2e（test_web_*、test_resize_cursor_sync）直接连
    ws://127.0.0.1:18766（daemon 内嵌 Web 服务器），与其他 e2e
    （basic/pubkey/tls/workflow）自启 daemon 的模式对齐，不再要求外部
    手动 `python -m src start`（CI/无人值守环境没有常驻 daemon）。

    - daemon 未运行 → start_daemon() 启动，等待 web 端口就绪，teardown 停止
    - daemon 已在运行（用户手动启动）→ 复用，不停止
    - wezterm-py 未编译 / daemon 启动失败 → 跳过（环境不具备，不误报失败）
    """
    import socket as _socket
    import time as _time

    from src.client.daemonctl import is_running, start_daemon, stop_daemon
    from src.pty.wezterm_pty import _HAS_WEZTERM

    if not _HAS_WEZTERM:
        pytest.skip("wezterm-py 不可用（未编译），跳过 web e2e")

    started = False
    if not is_running():
        start_daemon()
        started = True
    # 等待 web 端口就绪（token 单实例锁先于 web 服务器，须探测 18766）
    deadline = _time.monotonic() + 15
    while _time.monotonic() < deadline:
        try:
            with _socket.create_connection(("127.0.0.1", 18766), timeout=0.5):
                break
        except OSError:
            _time.sleep(0.2)
    else:
        if started:
            try:
                stop_daemon(force=True)
            except Exception:
                pass
        pytest.skip("daemon web 端口未就绪，跳过 web e2e")
    try:
        yield
    finally:
        if started:
            try:
                stop_daemon(force=True)
            except Exception:
                pass
