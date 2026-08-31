"""通知功能的客户端侧测试：wait 通知列表 / notify_waiting reason / 通知计数 hint"""

import io

from src.client.presenter import present
from src.client.result import SessionResult, WaitResult, from_response


def _run_present(result):
    out, err = io.StringIO(), io.StringIO()
    ok = present(result, out=out, err=err)
    return ok, out.getvalue(), err.getvalue()


class TestWaitNotifications:
    def test_from_response_wait_with_notifications(self):
        r = from_response(
            {
                "type": "wait",
                "timeout": 5.0,
                "elapsed": 0.01,
                "notifications": [
                    {"nid": "abc123", "sessionId": "py",
                     "triggerReturnReason": "program_ended", "createdAt": "2025-01-01T00:00:00.00"},
                ],
            }
        )
        assert isinstance(r, WaitResult)
        assert r.notifications and r.notifications[0]["nid"] == "abc123"
        assert r.notifications[0]["triggerReturnReason"] == "program_ended"

    def test_render_wait_with_notifications(self):
        r = from_response(
            {
                "type": "wait",
                "timeout": 5.0,
                "elapsed": 0.01,
                "notifications": [
                    {"nid": "abc123", "sessionId": "py",
                     "triggerReturnReason": "program_ended", "createdAt": "2025-01-01T00:00:00.00"},
                ],
            }
        )
        ok, out, _ = _run_present(r)
        assert ok
        assert "abc123" in out
        assert "py" in out
        assert "ended" in out

    def test_render_wait_no_notifications(self):
        r = from_response({"type": "wait", "timeout": 5.0, "elapsed": 5.0})
        ok, out, _ = _run_present(r)
        assert ok
        assert "[wait · ok · 5.00s] waited" in out


class TestNotifyWaitingReason:
    def test_session_reason_tag(self):
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "py",
                "outputStream": "",
                "triggerReturnReason": "notify_waiting",
                "program": {"running": True, "ptyType": "wezterm"},
            }
        )
        assert isinstance(r, SessionResult)
        ok, out, _ = _run_present(r)
        assert ok
        assert "[exec · notify" in out
        assert "py" in out


class TestPendingNotifCountHint:
    def test_session_hint(self):
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "py",
                "outputStream": "hello",
                "triggerReturnReason": "ok",
                "program": {"running": True, "ptyType": "wezterm"},
                "pendingNotifCount": 2,
            }
        )
        ok, _, err = _run_present(r)
        assert ok
        assert "2 个通知完成" in err

    def test_no_hint_when_zero(self):
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "py",
                "outputStream": "hello",
                "triggerReturnReason": "ok",
                "program": {"running": True, "ptyType": "wezterm"},
            }
        )
        ok, out, err = _run_present(r)
        assert ok
        assert "通知完成" not in out
        assert "通知完成" not in err

    def test_wait_with_notifications_no_dup_hint(self):
        """wait 已展示通知列表时不重复附加计数提示"""
        r = from_response(
            {
                "type": "wait",
                "timeout": 1.0,
                "elapsed": 0.0,
                "notifications": [
                    {"nid": "abc", "sessionId": "py",
                     "triggerReturnReason": "ok", "createdAt": "t"},
                ],
                "pendingNotifCount": 1,
            }
        )
        ok, out, err = _run_present(r)
        assert ok
        # wait 展示通知列表时不附加"通知完成"计数提示（stdout/stderr 均无）
        assert "通知完成" not in out
        assert "通知完成" not in err
