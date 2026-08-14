"""测试 AI 分析模块（src/client/ai_analyser.py）

通过 mock run_aichat_capture 验证 analyse_response 的三种模式。
"""

import importlib.util
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# 直接文件加载 ai_analyser（避免包相对导入）
_AI_ANALYSER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "client", "ai_analyser.py")

spec = importlib.util.spec_from_file_location("ai_analyser_test", _AI_ANALYSER_PATH)
ai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai)


def _make_resp(output="test output", uid="u1", type_="exec"):
    return {"type": type_, "outputStream": output, "uid": uid}


class TestAnalyseResponseNone:
    """none 模式：直接返回原 resp"""

    def test_none_mode_passthrough(self):
        ai._reset_aichat_cache()
        resp = _make_resp()
        result = ai.analyse_response(resp, "none", "prompt", None, 10)
        assert result is resp
        assert resp["outputStream"] == "test output"


class TestAnalyseResponseResponseOutput:
    """responseOutput 模式（使用临时文件 -f 避免命令行编码问题）"""

    @patch.object(ai, "_load_aichat")
    def test_success(self, mock_load):
        ai._reset_aichat_cache()
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (0, "AI analysis result")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="original text", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析一下", None, 10)

        # 验证 run_aichat_capture 被调用
        mock_mod.run_aichat_capture.assert_called_once()
        call_args, call_kwargs = mock_mod.run_aichat_capture.call_args
        aichat_args = call_args[0]
        # 验证 session args 在 prompt 之前（flags before positional）
        assert aichat_args[0] == "--session"
        assert aichat_args[1] == "u1"
        # 验证使用 -f 临时文件（避免命令行参数编码问题）
        assert "-f" in aichat_args
        # 验证 prompt 参数存在
        assert "分析一下" in aichat_args
        # 验证输出
        assert result["outputStream"] == "AI analysis result"

    @patch.object(ai, "_load_aichat")
    def test_success_no_uid(self, mock_load):
        ai._reset_aichat_cache()
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (0, "AI result")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid=None)
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        call_args = mock_mod.run_aichat_capture.call_args[0][0]
        assert "--session" not in call_args
        # 无 uid 时仍用 -f 临时文件
        assert "-f" in call_args
        assert result["outputStream"] == "AI result"

    @patch.object(ai, "_load_aichat")
    def test_empty_output_skips_ai(self, mock_load):
        """outputStream 为空时不调 AI"""
        mock_mod = MagicMock()
        mock_load.return_value = mock_mod

        resp = _make_resp(output="", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        mock_mod.run_aichat_capture.assert_not_called()
        assert result is resp

    @patch.object(ai, "_load_aichat")
    def test_failure_fallback(self, mock_load):
        """AI 返回非零时回退原 resp"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (1, "")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        assert result is resp
        assert "warning" in resp

    @patch.object(ai, "_load_aichat")
    def test_failure_empty_output(self, mock_load):
        """AI 输出为空时回退"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (0, "   ")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        assert result is resp
        assert "warning" in resp

    @patch.object(ai, "_load_aichat")
    def test_exception_fallback(self, mock_load):
        """AI 调用抛异常时回退"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.side_effect = RuntimeError("connection failed")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        assert result is resp
        assert "warning" in resp
        assert "exception" in resp["warning"].lower()

    @patch.object(ai, "_load_aichat")
    def test_with_uid_session(self, mock_load):
        """uid 存在时 session args 在 prompt 前"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (0, "分析结果")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="my-uid-123")
        ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        args = mock_mod.run_aichat_capture.call_args[0][0]
        # session args 在 prompt 前
        assert args[0] == "--session"
        assert args[1] == "my-uid-123"
        assert "--save-session" in args
        # -f 在 session args 之后
        assert "-f" in args


class TestAnalyseFileOutput:
    """fileOutput 模式"""

    @patch.object(ai, "_load_aichat")
    def test_success(self, mock_load):
        """fileOutput 成功：session args 在 prompt 前，读文件"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (0, "AI file result")
        mock_load.return_value = mock_mod

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write("file content")
            tmp_path = tmp.name

        try:
            resp = _make_resp(output="original", uid="u1")
            result = ai.analyse_response(resp, "fileOutput", "分析", tmp_path, 10)

            args = mock_mod.run_aichat_capture.call_args[0][0]
            # session args 在 prompt 前
            assert args[0] == "--session"
            assert args[1] == "u1"
            # -f 在 session args 之后
            assert "-f" in args
            assert tmp_path in args
            assert result["outputStream"] == "AI file result"
        finally:
            os.unlink(tmp_path)

    @patch.object(ai, "_load_aichat")
    def test_missing_output_file(self, mock_load):
        """文件不存在时回退"""
        mock_mod = MagicMock()
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "fileOutput", "分析", "/nonexistent/file.txt", 10)

        mock_mod.run_aichat_capture.assert_not_called()
        assert result is resp
        assert "warning" in resp
        assert "not found" in resp["warning"]

    def test_no_output_file(self):
        """fileOutput 模式缺少 output_file 参数时回退"""
        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "fileOutput", "分析", None, 10)

        assert result is resp
        assert "warning" in resp
        assert "requires -o" in resp["warning"]


class TestAnalyseEdgeCases:
    """边界情况"""

    def test_error_resp_passthrough(self):
        """type=error 的 response 直接放行"""
        resp = {"type": "error", "message": "something failed"}
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)
        assert result is resp
        assert "warning" not in resp

    def test_unknown_mode(self):
        """未知模式回退"""
        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "unknown", "分析", None, 10)
        assert result is resp
        assert "warning" in resp

    @patch.object(ai, "_load_aichat")
    def test_timeout(self, mock_load):
        """超时场景（run_aichat_capture 返回非零）"""
        mock_mod = MagicMock()
        mock_mod.run_aichat_capture.return_value = (1, "")
        mock_load.return_value = mock_mod

        resp = _make_resp(output="text", uid="u1")
        result = ai.analyse_response(resp, "responseOutput", "分析", None, 10)

        assert result is resp
        assert "warning" in resp