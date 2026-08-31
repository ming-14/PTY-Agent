"""config/plugins/ai 单测 —— CLI 级 AI 分析插件

覆盖：插件声明、CliPluginHost 挂载（activate 后钩子自动派发；未挂载不参与）、
transform_response 的 responseOutput/fileOutput 两模式、失败回退、错误响应放行。
"""

import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from config.plugins.ai import AiPlugin  # noqa: E402
from src.client.cli_plugins import CliPluginHost  # noqa: E402
from src.plugins.loader import load_plugin_dir  # noqa: E402
from tests.helpers import make_manifest  # noqa: E402

_AI_PATH = os.path.join(_PROJECT_ROOT, "config", "plugins", "ai")


class _FakeClient:
    name = "fake-client"


def _make_resp(output="hello", uid="u1", type_="exec"):
    return {"type": type_, "outputStream": output, "uid": uid}


def _ai_manifest():
    loaded = load_plugin_dir(_AI_PATH)
    if loaded is not None:
        return loaded.manifest
    return make_manifest("ai", kind="cli", commands=["exec", "send", "read", "mouse"])


def _host_with_ai(plugin=None, client=None):
    host = CliPluginHost([], client or _FakeClient())
    p = plugin or AiPlugin()
    manifest = _ai_manifest()
    p.name = manifest.id  # 加载器注入类属性，直连实例化时手动补
    p.manifest = manifest
    host._plugins = [p]
    host._engine.register(p, p.manifest)
    return host


def _mock_aichat(code=0, output="AI result"):
    mod = MagicMock()
    mod.run_aichat_capture.return_value = (code, output)
    mod.DEFAULT_CONFIG = "config.yaml"
    mod.load_settings.return_value = {"prompt": "分析", "timeout": 120}
    return mod


class TestAiPluginDecl:
    """插件声明：CLI 级 + 挂载后自动派发钩子"""

    def test_kind_and_commands(self):
        manifest = _ai_manifest()
        assert manifest.kind == ["cli"]
        assert set(manifest.commands) == {"exec", "send", "read", "mouse"}
        # 未挂载时钩子不被调用（宿主按挂载状态过滤）
        host = _host_with_ai()
        resp = _make_resp()
        assert host.transform_response("exec", resp) == resp

    def test_activate_dispatches_hooks(self):
        """挂载（activate）后钩子自动派发"""
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("config.plugins.ai._load_aichat", return_value=_mock_aichat()):
            resp = _make_resp(output="text")
            host.transform_response("exec", resp)
        assert resp["outputStream"] == "AI result"

    def test_commands_filter(self):
        """挂载但命令不在 commands 列表：不派发（ai 声明了 exec/send/read/mouse）"""
        p = AiPlugin()
        host = _host_with_ai(p)
        with patch("config.plugins.ai._load_aichat") as mock_load:
            resp = _make_resp(output="text")
            host.activate(["ai"])
            host.transform_response("wait", resp)
        mock_load.assert_not_called()


class TestResponseOutput:
    """无 -o：outputStream 拼进 prompt，写临时文件喂 AI"""

    def test_success_overwrites_output_stream(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("config.plugins.ai._load_aichat", return_value=_mock_aichat()):
            resp = _make_resp(output="original", uid="u1")
            host.transform_response("exec", resp)
        assert resp["outputStream"] == "AI result"
        assert "warning" not in resp

    def test_session_args_used(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        mock_mod = _mock_aichat()
        with patch("config.plugins.ai._load_aichat", return_value=mock_mod):
            resp = _make_resp(output="text", uid="my-uid")
            host.transform_response("exec", resp)
        args = mock_mod.run_aichat_capture.call_args[0][0]
        assert args[0] == "--session"
        assert args[1] == "my-uid"
        assert "-f" in args

    def test_empty_output_skips(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("config.plugins.ai._load_aichat") as mock_load:
            resp = _make_resp(output="   ")
            host.transform_response("exec", resp)
        mock_load.assert_not_called()
        assert resp is not None


class TestFileOutput:
    """有 -o：渲染文件后喂 AI，置 aiFileWritten 供主程序跳过重复写入"""

    def test_renders_and_sets_marker(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        host.set_output_path("out.txt")
        with patch(
            "config.plugins.ai._load_aichat", return_value=_mock_aichat()
        ) as mock_load, patch(
            "config.plugins.ai.os.makedirs", return_value=None
        ) as mock_makedirs, patch(
            "config.plugins.ai.open", mock_open()
        ) as mock_file:
            resp = _make_resp(output="text", uid="u1")
            host.transform_response("exec", resp)
        assert resp["aiFileWritten"] is True
        assert resp["outputStream"] == "AI result"
        args = mock_load.return_value.run_aichat_capture.call_args[0][0]
        assert "out.txt" in args

    def test_render_failure_skips_ai(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        host.set_output_path("out.txt")
        # 模拟文件写入失败（os.makedirs 抛异常）
        with patch(
            "config.plugins.ai._load_aichat", return_value=_mock_aichat()
        ) as mock_load, patch(
            "config.plugins.ai.os.makedirs", side_effect=OSError("permission denied")
        ):
            resp = _make_resp(output="text")
            host.transform_response("exec", resp)
        mock_load.return_value.run_aichat_capture.assert_not_called()
        assert resp["outputStream"] == "text"
        assert "warning" in resp


class TestFallback:
    """失败回退：非零/空输出/异常 → 保留原响应并追加 warning"""

    def test_nonzero_fallback(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch(
            "config.plugins.ai._load_aichat",
            return_value=_mock_aichat(code=1, output=""),
        ):
            resp = _make_resp(output="text")
            host.transform_response("exec", resp)
        assert resp["outputStream"] == "text"
        assert "warning" in resp

    def test_exception_fallback(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.side_effect = RuntimeError("conn")
        mock_mod.load_settings.return_value = {"prompt": "分析", "timeout": 120}
        with patch("config.plugins.ai._load_aichat", return_value=mock_mod):
            resp = _make_resp(output="text")
            host.transform_response("exec", resp)
        assert resp["outputStream"] == "text"
        assert "warning" in resp

    def test_config_unavailable(self):
        """config 不可用（load_settings 返回 None）→ 回退原响应 + warning"""
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        mock_mod = MagicMock()
        mock_mod.load_settings.return_value = None
        with patch("config.plugins.ai._load_aichat", return_value=mock_mod):
            resp = _make_resp(output="text")
            host.transform_response("exec", resp)
        assert resp["outputStream"] == "text"
        assert "warning" in resp


class TestPassthrough:
    """不应分析的响应：错误响应 / 无 outputStream"""

    def test_error_resp_unchanged(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        resp = {"type": "error", "message": "failed"}
        assert host.transform_response("exec", resp) == resp

    def test_no_output_stream_unchanged(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        resp = {"type": "result", "outputOffset": 5}
        assert host.transform_response("exec", resp) == resp


class TestCheckRequest:
    """check_request：aichat 缺失时拒绝 exec，其余命令放行"""

    def test_missing_aichat_rejects_exec(self):
        """aichat.exe 缺失 + exec → check_request 返回拒绝理由"""
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("os.path.exists", return_value=False):
            reason = host.check_request("exec", {"type": "exec"})
        assert isinstance(reason, str)
        assert "aichat" in reason

    def test_missing_aichat_allows_other_commands(self):
        """aichat.exe 缺失 + read/send/mouse → 放行（会话读写不阻断）"""
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("os.path.exists", return_value=False):
            for cmd in ("read", "send", "mouse"):
                assert host.check_request(cmd, {"type": cmd}) is None, cmd

    def test_present_aichat_allows(self):
        """aichat.exe 存在 → check_request 返回 None（放行）"""
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        with patch("os.path.exists", return_value=True):
            assert host.check_request("exec", {"type": "exec"}) is None

    def test_not_activated_passes_through(self):
        """未挂载（未 activate）→ 不参与检查，放行"""
        p = AiPlugin()
        host = _host_with_ai(p)  # 未 activate
        with patch("os.path.exists", return_value=False):
            assert host.check_request("exec", {"type": "exec"}) is None
