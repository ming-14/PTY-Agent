"""配置常量单元测试

验证配置常量正确性：固定监听端口、无 PID_FILE。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from src.config.common import DATA_DIR
from src.config.daemon import TOKEN_ENABLED, TOKEN_HOST, TOKEN_PORT, BASIC_ENABLED, TLS_ENABLED

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")


class TestDaemonConfig:
    """守护进程配置测试"""

    def test_token_listener(self):
        assert TOKEN_HOST == "127.0.0.1"
        assert TOKEN_PORT == 10520

    def test_listener_enabled_flags(self):
        assert isinstance(TOKEN_ENABLED, bool)
        assert isinstance(BASIC_ENABLED, bool)
        assert isinstance(TLS_ENABLED, bool)

    def test_data_dir_under_home(self):
        assert DATA_DIR == os.path.join(os.path.expanduser("~"), ".pty-agent")

    def test_no_pid_file_constant(self):
        import src.config as cfg
        assert not hasattr(cfg, "PID_FILE")


class TestNoPidFileOnDisk:
    """验证运行时不创建 PID 文件"""

    def test_pid_file_does_not_exist(self):
        pid_file = os.path.join(DATA_DIR, "daemon.pid")
        assert not os.path.exists(pid_file)


class TestDataDirConfig:
    """DATA_DIR（common.toml [paths]）自定义与派生路径验证

    配置常量在 import 时固化，自定义场景经子进程 + 临时配置目录
    （PTY_AGENT_CONFIG_DIR）隔离加载，避免污染本进程已固化的配置。
    """

    _PROBE = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from src.config import daemon, client\n"
        "print(json.dumps({\n"
        "  'data_dir': daemon.DATA_DIR,\n"
        "  'log_dir': daemon.LOG_DIR,\n"
        "  'tls_cert_file': daemon.TLS_CERT_FILE,\n"
        "  'auth_keys': daemon.PUBKEY_AUTHORIZED_KEYS,\n"
        "  'client_key': client.PUBKEY_PRIVATE_KEY_PATH,\n"
        "  'known_hosts': client.KNOWN_HOSTS_FILE,\n"
        "}))\n"
    )

    def _probe(self, common_content, env_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(_CONFIG_DIR):
                src = os.path.join(_CONFIG_DIR, name)
                dst = os.path.join(tmp, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            with open(os.path.join(tmp, "common.toml"), "w", encoding="utf-8") as f:
                f.write(common_content)
            env = os.environ.copy()
            env["PTY_AGENT_CONFIG_DIR"] = tmp
            if env_extra:
                env.update(env_extra)
            proc = subprocess.run(
                [sys.executable, "-c", self._PROBE, _PROJECT_ROOT],
                capture_output=True,
                text=True,
                env=env,
                cwd=_PROJECT_ROOT,
                timeout=30,
            )
            assert proc.returncode == 0, proc.stderr
            return json.loads(proc.stdout)

    def test_custom_data_dir_follows(self):
        """自定义 DATA_DIR（含 %VAR% 展开）后所有数据路径跟随"""
        result = self._probe(
            '[paths]\nDATA_DIR = "%PTY_TEST_VAR%/data"\n',
            env_extra={"PTY_TEST_VAR": "customdir"},
        )
        expected = os.path.normpath(os.path.join("customdir", "data"))
        assert result["data_dir"] == expected
        assert result["log_dir"] == os.path.join(expected, "logs")
        assert result["tls_cert_file"] == os.path.join(expected, "certs", "daemon.crt")
        assert result["auth_keys"] == os.path.join(expected, "authorized_keys")
        assert result["client_key"] == os.path.join(expected, "keys", "id_ed25519")
        assert result["known_hosts"] == os.path.join(expected, "known_hosts")

    def test_empty_data_dir_falls_back(self):
        """DATA_DIR 为空或缺失时回落默认 ~/.pty-agent"""
        for content in ("[paths]\nDATA_DIR = \"\"\n", "[terminal]\nDEFAULT_COLS = 80\n"):
            result = self._probe(content)
            default = os.path.join(os.path.expanduser("~"), ".pty-agent")
            assert result["data_dir"] == default
            assert result["log_dir"] == os.path.join(default, "logs")

    def test_explicit_path_wins_over_data_dir(self):
        """显式配置的认证路径（非空）优先于 DATA_DIR 派生"""
        result = self._probe(
            '[paths]\nDATA_DIR = "%PTY_TEST_VAR%/data"\n',
            env_extra={"PTY_TEST_VAR": "customdir"},
        )
        # 配置目录来自生产 common.toml 的 [paths] 覆盖，认证路径为空 → 走派生；
        # 此处直接验证 resolve_data_path 的显式优先分支
        from src.config._build import resolve_data_path

        explicit = resolve_data_path("~/custom/authorized_keys", result["data_dir"], "authorized_keys")
        assert explicit == os.path.normpath(os.path.expanduser("~/custom/authorized_keys"))