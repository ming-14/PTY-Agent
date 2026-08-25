"""配置环境变量覆写单元测试

验证 PTY_AGENT_<key> 环境变量覆写：优先级 环境变量 > 文件。
- 核心转换逻辑直接测 _loader.apply_env_overrides
- 各模块集成经子进程 + 临时配置目录（PTY_AGENT_CONFIG_DIR）隔离验证，
  避免污染本进程已固化的配置（常量在 import 时固化）。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from src.config import _loader

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")

# 探针环境清理：剥离 PTY_AGENT_*（保留 CONFIG_DIR 重定向），保证测试封闭
_ENV_BASE = {
    k: v for k, v in os.environ.items()
    if not k.startswith("PTY_AGENT_") or k == "PTY_AGENT_CONFIG_DIR"
}


class TestApplyEnvOverrides:
    """apply_env_overrides 核心转换逻辑"""

    def test_no_env_unchanged(self, monkeypatch):
        cfg = {"ENABLE_WEB": True, "TOKEN_PORT": 10520, "DATA_DIR": "x"}
        for key in ("ENABLE_WEB", "TOKEN_PORT", "DATA_DIR"):
            monkeypatch.delenv("PTY_AGENT_" + key, raising=False)
        assert _loader.apply_env_overrides(cfg) == cfg

    def test_bool_forms(self, monkeypatch):
        cfg = {"ENABLE_WEB": False}
        for raw, expected in (
            ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
            ("false", False), ("0", False), ("no", False), ("off", False),
        ):
            monkeypatch.setenv("PTY_AGENT_ENABLE_WEB", raw)
            out = _loader.apply_env_overrides(cfg)
            assert out["ENABLE_WEB"] is expected, raw

    def test_int_float_str(self, monkeypatch):
        monkeypatch.setenv("PTY_AGENT_TOKEN_PORT", "9999")
        monkeypatch.setenv("PTY_AGENT_PING_TIMEOUT", "2.5")
        monkeypatch.setenv("PTY_AGENT_CONNECT_MODE", "tls")
        cfg = {"TOKEN_PORT": 10520, "PING_TIMEOUT": 1.0, "CONNECT_MODE": "token"}
        out = _loader.apply_env_overrides(cfg)
        assert out["TOKEN_PORT"] == 9999
        assert out["PING_TIMEOUT"] == 2.5
        assert out["CONNECT_MODE"] == "tls"

    def test_json_list(self, monkeypatch):
        monkeypatch.setenv(
            "PTY_AGENT_ISOLATION_NET_ALLOWLIST",
            '[{"ip": "1.2.3.4", "port": 80, "protocol": "tcp"}]',
        )
        cfg = {"ISOLATION_NET_ALLOWLIST": []}
        out = _loader.apply_env_overrides(cfg)
        assert out["ISOLATION_NET_ALLOWLIST"] == [
            {"ip": "1.2.3.4", "port": 80, "protocol": "tcp"}
        ]

    def test_invalid_value_warns_and_keeps_file(self, monkeypatch):
        monkeypatch.setenv("PTY_AGENT_TOKEN_PORT", "abc")
        cfg = {"TOKEN_PORT": 10520}
        with pytest.warns(UserWarning, match="PTY_AGENT_TOKEN_PORT"):
            out = _loader.apply_env_overrides(cfg)
        assert out["TOKEN_PORT"] == 10520

    def test_input_not_mutated(self, monkeypatch):
        monkeypatch.setenv("PTY_AGENT_TOKEN_PORT", "9999")
        cfg = {"TOKEN_PORT": 10520}
        _loader.apply_env_overrides(cfg)
        assert cfg["TOKEN_PORT"] == 10520

    def test_custom_prefix(self, monkeypatch):
        monkeypatch.setenv("MY_PREFIX_TOKEN_PORT", "9999")
        cfg = {"TOKEN_PORT": 10520}
        out = _loader.apply_env_overrides(cfg, prefix="MY_PREFIX_")
        assert out["TOKEN_PORT"] == 9999

    def test_unknown_env_ignored(self, monkeypatch):
        monkeypatch.setenv("PTY_AGENT_NO_SUCH_KEY", "1")
        cfg = {"TOKEN_PORT": 10520}
        assert _loader.apply_env_overrides(cfg) == cfg


class TestEnvOverrideIntegration:
    """各 config 模块经环境变量覆写（子进程 + 临时配置目录隔离）"""

    _PROBE = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from src.config import daemon, client, sandbox\n"
        "from src.config.transfer import TRANSFER_CHUNK_SIZE\n"
        "print(json.dumps({\n"
        "  'data_dir': daemon.DATA_DIR,\n"
        "  'token_port': daemon.TOKEN_PORT,\n"
        "  'enable_web': daemon.ENABLE_WEB,\n"
        "  'auth_keys': daemon.PUBKEY_AUTHORIZED_KEYS,\n"
        "  'connect_mode': client.CONNECT_MODE,\n"
        "  'chunk': TRANSFER_CHUNK_SIZE,\n"
        "  'sbx_enabled': sandbox.ENABLED,\n"
        "  'sbx_quota_mem': sandbox.QUOTA.get('memory_mb'),\n"
        "  'sbx_net_policy': sandbox.ISOLATION.get('net_policy'),\n"
        "}))\n"
    )

    def _probe(self, env_extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(_CONFIG_DIR):
                src = os.path.join(_CONFIG_DIR, name)
                dst = os.path.join(tmp, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            env = dict(_ENV_BASE)
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

    def test_scalar_overrides(self):
        result = self._probe({
            "PTY_AGENT_TOKEN_PORT": "9999",
            "PTY_AGENT_ENABLE_WEB": "false",
            "PTY_AGENT_CONNECT_MODE": "tls",
            "PTY_AGENT_TRANSFER_CHUNK_SIZE": "1024",
        })
        assert result["token_port"] == 9999
        assert result["enable_web"] is False
        assert result["connect_mode"] == "tls"
        assert result["chunk"] == 1024

    def test_data_dir_override_expands(self):
        result = self._probe({
            "PTY_AGENT_DATA_DIR": "%PTY_TEST_DIR%/data",
            "PTY_TEST_DIR": "custom",
        })
        assert result["data_dir"] == os.path.normpath(os.path.join("custom", "data"))

    def test_auth_path_override(self):
        result = self._probe({"PTY_AGENT_PUBKEY_AUTHORIZED_KEYS": "~/mykeys"})
        assert result["auth_keys"] == os.path.normpath(os.path.expanduser("~/mykeys"))

    def test_sandbox_overrides(self):
        result = self._probe({
            "PTY_AGENT_SANDBOX_ENABLED": "true",
            "PTY_AGENT_QUOTA_MEMORY_MB": "512",
            "PTY_AGENT_ISOLATION_NET_POLICY": "allowlist",
        })
        assert result["sbx_enabled"] is True
        assert result["sbx_quota_mem"] == 512
        assert result["sbx_net_policy"] == "allowlist"

    def test_web_defaults_overridable_when_file_missing(self):
        """web.toml 缺失时兜底默认值同样可被环境变量覆写"""
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(_CONFIG_DIR):
                src = os.path.join(_CONFIG_DIR, name)
                dst = os.path.join(tmp, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            os.remove(os.path.join(tmp, "daemon", "web.toml"))
            env = dict(_ENV_BASE)
            env["PTY_AGENT_CONFIG_DIR"] = tmp
            env["PTY_AGENT_ENABLE_WEB"] = "true"
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); "
                 "from src.config import daemon; print(daemon.ENABLE_WEB)",
                 _PROJECT_ROOT],
                capture_output=True,
                text=True,
                env=env,
                cwd=_PROJECT_ROOT,
                timeout=30,
            )
            assert proc.returncode == 0, proc.stderr
            assert proc.stdout.strip() == "True"
