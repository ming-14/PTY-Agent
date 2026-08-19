"""client/result.py + client/presenter.py 单元测试"""

import io
import sys

import pytest

from src.client.presenter import (
    error_seen,
    error_was_printed,
    present,
    set_debug_mode,
)
from src.client.result import ErrorResult, ListResult, MessageResult, SessionResult, StatusResult, from_response


class TestFromResponse:
    def test_none(self):
        r = from_response(None)
        assert isinstance(r, ErrorResult) and not r.ok

    def test_error(self):
        r = from_response({"type": "error", "message": "boom"})
        assert isinstance(r, ErrorResult) and not r.ok and r.message == "boom"

    def test_error_code_classified(self):
        assert from_response({"type": "error", "message": "Session not found"}).code == "NOT_FOUND"
        assert from_response({"type": "error", "message": "会话不存在"}).code == "NOT_FOUND"
        assert from_response({"type": "error", "message": "Authentication failed"}).code == "UNAUTHORIZED"
        # 服务端显式 code 优先于分类
        r = from_response({"type": "error", "message": "boom", "code": "CUSTOM"})
        assert r.code == "CUSTOM"

    def test_info_warning(self):
        assert isinstance(from_response({"type": "info", "message": "hi"}), MessageResult)
        assert isinstance(from_response({"type": "warning", "message": "w"}), MessageResult)

    def test_session(self):
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "py",
                "outputStream": ">>> ",
                "triggerReturnReason": "matched",
                "program": {"running": True, "ptyType": "wezterm"},
                "hint": "ok",
            }
        )
        assert isinstance(r, SessionResult)
        assert r.output == ">>> " and r.running and r.kind == "session"

    def test_status_and_list(self):
        assert isinstance(from_response({"type": "status", "running": True}), StatusResult)
        r = from_response({"commandType": "list", "sessions": [{"id": "s"}]})
        assert isinstance(r, ListResult)


def _cap(fn):
    out, err = io.StringIO(), io.StringIO()
    fn(out, err)
    return out.getvalue(), err.getvalue()


class TestPresenter:
    def _reset(self):
        set_debug_mode(False)
        import src.client.presenter as m

        m._error_seen = False

    def test_session_content_stdout_meta_stderr(self):
        self._reset()
        out, err = _cap(
            lambda o, e: present(
                from_response(
                    {
                        "commandType": "read",
                        "sessionId": "py",
                        "outputStream": "hello\n",
                        "triggerReturnReason": "matched",
                        "program": {"running": True, "ptyType": "wezterm"},
                        "hint": "trigger matched",
                    }
                ),
                o,
                e,
            )
        )
        assert "hello" in out
        # 元信息（状态行/hint）去 stderr，不去 stdout
        assert "trigger matched" not in out
        assert "py" in err
        assert not error_seen()

    def test_error_goes_stderr_and_sets_flag(self):
        self._reset()
        out, err = _cap(lambda o, e: present(ErrorResult(message="boom"), o, e))
        assert out == ""
        assert "boom" in err
        assert error_seen() and error_was_printed()

    def test_info_goes_stderr_config_stdout(self):
        self._reset()
        out, err = _cap(lambda o, e: present(MessageResult(msg_type="info", text="hi"), o, e))
        assert err == "hi\n" and out == ""
        out, err = _cap(lambda o, e: present(MessageResult(msg_type="config", text="cfg"), o, e))
        assert out == "cfg\n" and err == ""

    def _session_with_debug(self):
        return from_response(
            {
                "commandType": "read",
                "sessionId": "s",
                "outputStream": "x",
                "triggerReturnReason": "ok",
                "program": {"running": True},
                "debugInformation": {"processes": [1234]},
            }
        )

    def test_debug_hidden_when_disabled(self):
        self._reset()
        set_debug_mode(False)
        out, err = _cap(lambda o, e: present(self._session_with_debug(), o, e))
        assert "1234" not in err

    def test_debug_shown_when_enabled(self):
        self._reset()
        set_debug_mode(True)
        out, err = _cap(lambda o, e: present(self._session_with_debug(), o, e))
        assert "1234" in err