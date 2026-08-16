"""config/plugins/ai 单测 —— CLI 级 AI 分析插件

覆盖：插件声明、CliPluginHost 挂载（activate 后钩子自动派发；未挂载不参与）、
transform_response 的 responseOutput/fileOutput 两模式、失败回退、错误响应放行。
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, _PROJECT_ROOT)

from config.plugins.ai import AiPlugin  # noqa: E402
from src.client.cli_plugins import CliPluginHost  # noqa: E402
from src.plugins.base import Plugin  # noqa: E402


class _FakeClient:
    name = "fake-client"


def _make_resp(output="hello", uid="u1", type_="exec"):
    return {"type": type_, "outputStream": output, "uid": uid}


def _host_with_ai(plugin=None, client=None):
    host = CliPluginHost([], client or _FakeClient())
    host._plugins = [plugin or AiPlugin()]
    return host


def _mock_aichat(code=0, output="AI result"):
    mod = MagicMock()
    mod.run_aichat_capture.return_value = (code, output)
    mod.DEFAULT_CONFIG = "config.yaml"
    return mod


class TestAiPluginDecl:
    """插件声明：CLI 级 + 挂载后自动派发钩子"""

    def test_kind_and_commands(self):
        p = AiPlugin()
        assert p.kind == "cli"
        assert set(p.commands) == {"exec", "send", "read", "mouse"}
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
        """未挂载的插件不参与钩子；commands 限制生效命令"""
        p = AiPlugin()
        host = _host_with_ai(p)
        # 挂载但命令不在 commands 列表：不派发（ai 声明了 exec/send/read/mouse）
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
            "src.client.renderer.render_to_file", return_value=None
        ) as mock_render:
            resp = _make_resp(output="text", uid="u1")
            host.transform_response("exec", resp)
        mock_render.assert_called_once()
        assert resp["aiFileWritten"] is True
        assert resp["outputStream"] == "AI result"
        args = mock_load.return_value.run_aichat_capture.call_args[0][0]
        assert "out.txt" in args

    def test_render_failure_skips_ai(self):
        p = AiPlugin()
        host = _host_with_ai(p)
        host.activate(["ai"])
        host.set_output_path("out.txt")
        with patch(
            "config.plugins.ai._load_aichat", return_value=_mock_aichat()
        ) as mock_load, patch(
            "src.client.renderer.render_to_file", return_value="boom"
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
