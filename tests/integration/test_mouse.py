"""鼠标动作集成测试

验证 Session.perform_mouse_action 能把 SGR 序列写入 PTY。
"""

import pytest

from src.session.session import Session


class _FakePty:
    """最小 PTY mock，记录写入数据并伪装为 conpty"""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def get_type(self):
        return "win-conpty"


@pytest.fixture
def fake_session():
    sess = Session("mouse-test", ["python"])
    sess._pty = _FakePty()
    sess.running = True
    return sess


class TestSessionPerformMouseAction:
    def test_click_writes_sgr(self, fake_session):
        result = fake_session.perform_mouse_action({
            "action": "click",
            "coords": {"col": 5, "row": 10},
            "button": "left",
            "count": 1,
        })
        assert result["performed"] is True
        assert len(fake_session._pty.written) == 2
        assert fake_session._pty.written[0] == b"\x1b[<0;5;10M"
        assert fake_session._pty.written[1] == b"\x1b[<3;5;10m"

    def test_not_running_rejected(self):
        sess = Session("dead-test", ["cmd"])
        sess._pty = _FakePty()
        sess.running = False
        result = sess.perform_mouse_action({
            "action": "click",
            "coords": {"col": 1, "row": 1},
        })
        assert result["performed"] is False
        assert "not running" in result["error"]

    def test_drag_writes_motion_events(self, fake_session):
        result = fake_session.perform_mouse_action({
            "action": "drag",
            "coords": {"col": 1, "row": 1},
            "to": {"col": 3, "row": 1},
            "button": "left",
        })
        assert result["performed"] is True
        written = fake_session._pty.written
        assert len(written) == 4
        assert written[0] == b"\x1b[<0;1;1M"
        assert written[1] == b"\x1b[<32;2;1M"
        assert written[2] == b"\x1b[<32;3;1M"
        assert written[3] == b"\x1b[<3;3;1m"

    def test_scroll_writes_repeated_events(self, fake_session):
        result = fake_session.perform_mouse_action({
            "action": "scroll",
            "coords": {"col": 10, "row": 10},
            "direction": "up",
            "times": 2,
        })
        assert result["performed"] is True
        assert len(fake_session._pty.written) == 4
