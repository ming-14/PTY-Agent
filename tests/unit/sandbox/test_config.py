"""src/config/sandbox.py 配置加载单元测试"""

from src.config import sandbox as sbx


class TestSandboxConfigDefaults:
    def test_enabled_current(self):
        # 沙箱会话为显式 opt-in；当前本地配置为开启
        assert sbx.ENABLED is True

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