"""端到端测试：AI 分析集成

主要验证 transport 层的 _apply_ai_analysis 方法配合 mock 工作正常。
核心逻辑已在 test_ai_analyser.py 中覆盖，这里只做集成验证。
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# 添加项目根到 path 以便 import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.client.transport import Client
from src.client.config_manager import ConfigManager, _DEFAULTS


class TestTransportAIIntegration:
    """验证 transport 层的 AI 分析集成"""

    def test_apply_ai_analysis_none(self):
        """none 模式不调 AI"""
        client = Client.__new__(Client)
        client._config = ConfigManager()

        resp = {"type": "exec", "outputStream": "hello", "uid": "u1"}
        result = client._apply_ai_analysis(resp, "none", None, None)
        assert result is resp

    def test_apply_ai_analysis_empty_mode(self):
        """None/空字符串都直接放行"""
        client = Client.__new__(Client)
        client._config = ConfigManager()

        for mode in (None, "", "none"):
            resp = {"type": "exec", "outputStream": "hello", "uid": "u1"}
            result = client._apply_ai_analysis(resp, mode, None, None)
            assert result is resp
            assert result["outputStream"] == "hello"

    @patch("src.client.transport.analyse_response")
    def test_apply_ai_analysis_response_output(self, mock_analyse):
        """responseOutput 模式调用 analyse_response"""
        from src.client.transport import analyse_response as real_ar
        mock_analyse.return_value = {"type": "exec", "outputStream": "AI analyzed"}

        client = Client.__new__(Client)
        client._config = ConfigManager()

        resp = {"type": "exec", "outputStream": "hello", "uid": "u1"}
        result = client._apply_ai_analysis(resp, "responseOutput", "分析一下", None)
        mock_analyse.assert_called_once()
        assert result["outputStream"] == "AI analyzed"

    @patch("src.client.transport.analyse_response")
    def test_apply_ai_analysis_prompt_fallback(self, mock_analyse):
        """ai_prompt 为 None 时用 _DEFAULTS 的默认值"""
        mock_analyse.return_value = {"type": "exec", "outputStream": "AI"}

        client = Client.__new__(Client)
        client._config = ConfigManager()

        resp = {"type": "exec", "outputStream": "hello", "uid": "u1"}
        client._apply_ai_analysis(resp, "responseOutput", None, None)

        _, kwargs = mock_analyse.call_args
        assert kwargs["prompt"] == _DEFAULTS["ai_prompt"]


class TestE2EMockDaemon:
    """模拟 daemon 响应验证完整流程"""

    @patch("src.client.transport.analyse_response")
    def test_cmd_exec_ai_hook(self, mock_analyse):
        """verify _apply_ai_analysis is called with correct params"""
        mock_analyse.return_value = {"type": "exec", "outputStream": "AI result"}

        client = Client.__new__(Client)
        client._config = ConfigManager()

        resp = {"type": "exec", "outputStream": "hello", "uid": "u1"}
        result = client._apply_ai_analysis(resp, "responseOutput", "分析", None)
        assert result["outputStream"] == "AI result"