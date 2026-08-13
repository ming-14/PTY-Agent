"""client/formatter.py 单元测试"""

import json
import pytest

from src.client.formatter import print_response, set_debug_mode, _strip_debug_info


class TestSetDebugMode:
    def test_set_debug_mode_false(self):
        set_debug_mode(False)
        import src.client.formatter as m
        assert m._SHOW_DEBUG is False

    def test_set_debug_mode_true(self):
        set_debug_mode(True)
        import src.client.formatter as m
        assert m._SHOW_DEBUG is True


class TestStripDebugInfo:
    def test_strip_from_dict(self):
        obj = {"type": "result", "debugInformation": {"processes": []}}
        result = _strip_debug_info(obj)
        assert "debugInformation" not in result
        assert "type" in result

    def test_strip_nested(self):
        obj = {"a": {"debugInformation": "x"}, "b": 1}
        result = _strip_debug_info(obj)
        assert "debugInformation" not in result["a"]

    def test_strip_from_list(self):
        obj = [{"debugInformation": "x"}, {"type": "ok"}]
        result = _strip_debug_info(obj)
        assert "debugInformation" not in result[0]

    def test_no_debug_info(self):
        obj = {"type": "result", "output": "hello"}
        result = _strip_debug_info(obj)
        assert result == obj


class TestPrintResponse:
    def test_none_response(self, capsys, monkeypatch):
        monkeypatch.setattr("src.client.formatter._SHOW_DEBUG", True)
        print_response(None)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["type"] == "error"
        assert "not responding" in data["message"]

    def test_normal_response(self, capsys, monkeypatch):
        monkeypatch.setattr("src.client.formatter._SHOW_DEBUG", True)
        print_response({"type": "result", "output": "hello"})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["type"] == "result"

    def test_debug_stripped_when_disabled(self, capsys, monkeypatch):
        monkeypatch.setattr("src.client.formatter._SHOW_DEBUG", False)
        print_response({"type": "result", "debugInformation": {"x": 1}})
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "debugInformation" not in data
