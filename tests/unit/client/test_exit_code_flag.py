"""client/formatter.py 退出码标记单元测试 — error 响应提升为 exit 1"""

from src.client import formatter
from src.protocol.response import Response


class TestErrorWasPrinted:
    def test_initially_false(self, monkeypatch):
        monkeypatch.setattr(formatter, "_error_printed", False)
        assert formatter.error_was_printed() is False

    def test_error_response_sets_flag(self, monkeypatch):
        monkeypatch.setattr(formatter, "_error_printed", False)
        formatter.print_response(Response.error("boom"))
        assert formatter.error_was_printed() is True

    def test_success_response_keeps_flag(self, monkeypatch):
        monkeypatch.setattr(formatter, "_error_printed", False)
        formatter.print_response({"type": "status", "running": True})
        assert formatter.error_was_printed() is False

    def test_none_response_sets_flag(self, monkeypatch):
        monkeypatch.setattr(formatter, "_error_printed", False)
        formatter.print_response(None)
        assert formatter.error_was_printed() is True
