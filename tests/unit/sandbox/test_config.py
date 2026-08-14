"""src/config/sandbox.py 配置加载单元测试"""

import pytest

from src.config import sandbox as sbx

# sandbox.toml 为可选配置文件，不存在时本测试模块无意义
pytestmark = pytest.mark.skipif(not sbx.CONFIG_LOADED, reason="sandbox.toml 不存在")


class TestSandboxConfigDefaults:
    def test_enabled_current(self):
        # 沙箱会话为显式 opt-in；ENABLED 与配置文件的 boolean 开关一致
        assert isinstance(sbx.ENABLED, bool)

    def test_log_level(self):
        assert sbx.LOG_LEVEL in ("trace", "debug", "info", "warn", "error")

    def test_quota_keys(self):
        assert "memory_mb" in sbx.QUOTA
        assert "max_processes" in sbx.QUOTA
        assert "crash_silent" in sbx.QUOTA


class TestSandboxConfigIsolation:
    """Phase 16 schema：仅 net_policy / net_allowlist / clipboard_isolate 三键"""

    def test_keys_exact(self):
        # 键集合精确对齐：不允许旧 schema 字段（fs_mode/capabilities/path_rules）残留
        assert set(sbx.ISOLATION) == {"net_policy", "net_allowlist",
                                      "clipboard_isolate"}

    def test_net_policy_valid(self):
        assert sbx.ISOLATION["net_policy"] in ("unrestricted", "allowlist")

    def test_net_allowlist_list(self):
        assert isinstance(sbx.ISOLATION["net_allowlist"], list)

    def test_clipboard_isolate_bool(self):
        assert isinstance(sbx.ISOLATION["clipboard_isolate"], bool)