"""后端鼠标追踪模式变化推送单元测试（P3）。

验证 Threads._process_chunk 在终端模型鼠标追踪状态变化时
推送 mouse_mode 事件（web 端权威同步），且不产生重复/虚假事件。
"""

import threading

from src.session.threads import Threads, Components
from src.process.base import PendingEvent


class _Buf:
    def __init__(self):
        self.lock = threading.Lock()
        self.length = 0

    def append(self, data):
        return True


class _Trig:
    has_pattern = False

    def on_data_appended(self, t):
        pass

    def check(self, buf):
        pass


class _Screen:
    """可切换模式的 TerminalScreen 替身"""

    def __init__(self):
        self.mode = False
        self.fed = 0

    def feed(self, data):
        self.fed += 1

    def is_mouse_tracking(self):
        return self.mode

    def drain_terminal_response(self):
        return b""


class _Pty:
    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)

    def read(self, n):
        return b""  # EOF：读者循环据此干净退出

    def drain(self, n):
        return b""


class _Noop:
    """读者/监控线程退出路径所需的最小 no-op 替身"""

    def check(self, *a, **kw):
        pass

    def drain_notifications(self):
        pass

    def check_events(self, force=False, **kw):
        pass

    def get_root_exit_code(self):
        return None

    def get_process_list(self):
        return []


class _Session:
    def __init__(self):
        self.events = []

        class _Pub:
            def notify_subscribers(self, data, stream):
                pass

        self._publisher = _Pub()

    def _on_event(self, ev):
        self.events.append(ev)


def _make_comp(screen_mode=False, with_thread_stubs=False):
    sess = _Session()
    comp = Components(
        pty_provider=lambda: _Pty(),
        out_buf=_Buf(),
        err_buf=None,
        trig_mat=_Trig(),
        proc_mon=_Noop() if with_thread_stubs else None,
        tracker=_Noop() if with_thread_stubs else None,
        gui_detector=_Noop() if with_thread_stubs else None,
        screen=_Screen(),
        session_id="s1",
        on_exit=lambda *a: None,
        session_ref=lambda: sess,
        plugin_host=None,
        mode="pty",
    )
    comp.screen.mode = screen_mode
    return comp, sess


def _process(comp, data=b"x"):
    Threads(comp)._process_chunk(comp, _Pty(), data, "stdout", False)


def test_mode_on_change_pushes_event():
    comp, sess = _make_comp(screen_mode=False)
    t = Threads(comp)
    t._last_mouse_mode = False  # 基线
    comp.screen.mode = True     # 应用启用鼠标追踪（如 vim 启动）
    t._process_chunk(comp, _Pty(), b"\x1b[?1002h", "stdout", False)
    assert len(sess.events) == 1
    ev = sess.events[0]
    assert isinstance(ev, PendingEvent)
    assert ev.type == "mouse_mode"
    assert ev.detail == {"enabled": True}


def test_mode_off_change_pushes_event():
    comp, sess = _make_comp(screen_mode=True)
    t = Threads(comp)
    t._last_mouse_mode = True
    comp.screen.mode = False    # 应用退出鼠标追踪（如 vim 退出）
    t._process_chunk(comp, _Pty(), b"\x1b[?1002l", "stdout", False)
    assert len(sess.events) == 1
    assert sess.events[0].type == "mouse_mode"
    assert sess.events[0].detail == {"enabled": False}


def test_no_change_no_event():
    comp, sess = _make_comp(screen_mode=True)
    t = Threads(comp)
    t._last_mouse_mode = True
    t._process_chunk(comp, _Pty(), b"plain output", "stdout", False)
    assert sess.events == []


def test_repeated_same_state_no_duplicate():
    """状态不变时不重复推送（防抖：模式只在变化时发一次）"""
    comp, sess = _make_comp(screen_mode=False)
    t = Threads(comp)
    t._last_mouse_mode = False
    comp.screen.mode = True
    t._process_chunk(comp, _Pty(), b"a", "stdout", False)
    t._process_chunk(comp, _Pty(), b"b", "stdout", False)  # 仍为 True
    assert len(sess.events) == 1


def test_start_initializes_baseline_no_spurious():
    """start() 初始化基线：模式未变化时首个 feed 不产生虚假事件"""
    comp, sess = _make_comp(screen_mode=False, with_thread_stubs=True)
    t = Threads(comp)
    t.start()
    assert t._last_mouse_mode is False
    t._process_chunk(comp, _Pty(), b"\x1b[?1002h", "stdout", False)
    # 屏幕模式未变（stub 不因 feed 变化），无事件
    assert sess.events == []
    t.stop()
