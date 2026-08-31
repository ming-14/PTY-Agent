"""输入路由用例的单元测试（leaf.usecases.input）——拖拽状态机、选区路由与焦点切换。

路由统一交给 Mux 门面（MuxPanelPort）；此处用假门面记录 key_down/mouse/scroll/
set_focus/set_split_col/resize/selection 调用，验证用例层只做事件归一化分发与状态机。
"""

from typing import List

from leaf.domain.events import KeyEvent, MouseEvent, ResizeEvent
from leaf.usecases.input import handle_events, handle_key, handle_mouse


class _FakeConsole:
    """预置领域事件流的假控制台"""

    def __init__(self, events: List, size=(118, 30)):
        self._events = list(events)
        self._size = size

    def read_inputs(self):
        out, self._events = self._events, []
        return out

    def size(self):
        return self._size


class _FakeClipboard:
    """ClipboardPort 假实现：记录读写内容"""

    def __init__(self):
        self.written = []
        self._read = ""

    def read(self):
        return self._read

    def write(self, text):
        self.written.append(text)


class _FakeMux:
    """MuxPanelPort 假门面：记录 key/mouse/scroll/focus/split/resize/selection 调用"""

    def __init__(self, sep_col=59, mouse_grabbed=False):
        self.sep_col = sep_col
        self.focus = 0
        self._grabbed = mouse_grabbed
        self.key_calls = []
        self.mouse_calls = []
        self.scroll_calls = []
        self.focus_calls = []
        self.split_calls = []
        self.resized = None
        self.selection_calls = []
        self.pastes = []
        self.selection_text_value = "SEL_TEXT"
        self._selection_active = False

    def set_focus(self, pane_id):
        self.focus = pane_id
        self.focus_calls.append(pane_id)

    def focused(self):
        return self.focus

    def split_col(self):
        return self.sep_col

    def set_split_col(self, split_col):
        self.split_calls.append(split_col)

    def resize(self, cols, rows):
        self.resized = (cols, rows)

    def key_down(self, key, mods):
        self.key_calls.append(("down", key, mods))
        return b"\x1b[down:" + key.encode()

    def key_up(self, key, mods):
        self.key_calls.append(("up", key, mods))
        return b""

    def mouse(self, x, y, kind, button, mods):
        self.mouse_calls.append((x, y, kind, button))
        return b""

    def pane_at(self, x, y):
        return 0 if x <= self.sep_col else 1

    def pane_is_mouse_grabbed(self, pane_id):
        return self._grabbed

    def scroll_pane(self, pane_id, delta):
        self.scroll_calls.append((pane_id, delta))

    def scroll_to_bottom(self):
        pass

    def set_status(self, text):
        pass

    def render(self):
        return b"", 0, 0, True

    def cursor_seq(self, row, col, visible):
        return "\x1b[{};{}H{}".format(row + 1, col + 1, "\x1b[?25h" if visible else "\x1b[?25l")

    # ---- 选区 / 粘贴 ----
    def pane_selection_set(self, pane_id, ax, ay, ex, ey):
        self.selection_calls.append(("set", pane_id, ax, ay, ex, ey))
        self._selection_active = True

    def pane_selection_select_word(self, pane_id, x, y):
        self.selection_calls.append(("word", pane_id, x, y))

    def pane_selection_select_line(self, pane_id, x, y):
        self.selection_calls.append(("line", pane_id, x, y))

    def pane_selection_text(self, pane_id):
        return self.selection_text_value

    def pane_selection_active(self, pane_id):
        return self._selection_active

    def pane_selection_clear(self, pane_id):
        self._selection_active = False

    def set_focus_selection_callback(self, callback):
        self.selection_callback = callback

    def send_paste(self, text):
        self.pastes.append(text)


def _mouse(x, y, kind, button, mods=0, count=1):
    return MouseEvent(x, y, kind, button, mods, count)


def _run(mux, events, focus=0, cols=118, rows=30, split_col=59, drag=None,
         last_move=0.0, sel_drag=None, clipboard=None):
    """handle_events 便捷封装：默认参数 + 解包 10 元组"""
    return handle_events(
        mux, _FakeConsole(events, size=(cols, rows)), focus, cols, rows,
        split_col, drag, last_move, sel_drag, clipboard,
    )


# ---- handle_key ---------------------------------------------------------


def test_handle_key_route_to_focus():
    mux = _FakeMux()
    focus, exit_ = handle_key(mux, 0, KeyEvent("a", 0, True))
    assert ("down", "a", 0) in mux.key_calls and ("up", "a", 0) in mux.key_calls
    assert focus == 0 and not exit_ and mux.focus == 0


def test_handle_key_f9_switches_focus():
    mux = _FakeMux()
    focus, exit_ = handle_key(mux, 0, KeyEvent("F9", 0, True))
    assert focus == 1 and mux.focus == 1 and not exit_
    focus, exit_ = handle_key(mux, 1, KeyEvent("F9", 0, True))
    assert focus == 0 and mux.focus == 0 and not exit_


def test_handle_key_f8_toggles_recording():
    mux = _FakeMux()
    toggled = []
    def toggle():
        toggled.append(1)
    focus, exit_ = handle_key(mux, 0, KeyEvent("F8", 0, True), toggle_recording=toggle)
    assert len(toggled) == 1
    assert not exit_ and mux.key_calls == []  # F8 不下发给 pane


def test_handle_key_f8_forwards_without_callback():
    mux = _FakeMux()
    focus, exit_ = handle_key(mux, 0, KeyEvent("F8", 0, True))
    assert not exit_ and ("down", "F8", 0) in mux.key_calls


def test_handle_key_f10_exits():
    focus, exit_ = handle_key(_FakeMux(), 0, KeyEvent("F10", 0, True))
    assert exit_ is True


def test_handle_key_f9_with_mods_not_swallow():
    # 带修饰符的 F9 应转发给程序而不是切焦点
    mux = _FakeMux()
    focus, exit_ = handle_key(mux, 0, KeyEvent("F9", 8, True))
    assert not exit_ and mux.focus == 0 and ("down", "F9", 8) in mux.key_calls


def test_handle_key_ctrl_v_pastes():
    # Ctrl+V → 读系统剪贴板 → send_paste（模式感知）
    mux = _FakeMux()
    clip = _FakeClipboard()
    clip._read = "粘贴内容"
    focus, exit_ = handle_key(mux, 0, KeyEvent("v", 8, True), clipboard=clip)
    assert mux.pastes == ["粘贴内容"], mux.pastes
    assert not exit_ and mux.key_calls == []  # 不转发普通按键


def test_handle_key_ctrl_v_empty_clipboard():
    mux = _FakeMux()
    clip = _FakeClipboard()
    focus, exit_ = handle_key(mux, 0, KeyEvent("v", 8, True), clipboard=clip)
    assert mux.pastes == []  # 空剪贴板不粘贴


# ---- handle_events：分割线拖拽 -------------------------------------------


def test_handle_events_split_drag():
    # 拖拽分割线：press(命中 sep_col) → move → move → release，split_col 随 move 更新
    mux = _FakeMux(sep_col=59)
    focus, exit_, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag = _run(
        mux, [
            _mouse(59, 5, "press", "left"),
            _mouse(63, 5, "move", "left"),
            _mouse(67, 5, "move", "left"),
            _mouse(67, 5, "release", "left"),
        ],
    )
    assert split_col == 67  # 59 + (67-59) 起算
    assert drag is None  # release 后结束
    assert dirty
    assert focus == 0  # 拖拽不切焦点
    assert mux.split_calls == [63, 67]  # 仅 move 时预览更新分割位置
    assert mux.resized == (118, 30)  # release 才一次 resize 到位


def test_handle_events_split_release_force_full():
    focus, exit_, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag = _run(
        _FakeMux(), [_mouse(67, 5, "release", "left")], drag=(59, 59),
    )
    assert drag is None
    assert force_full is True


def test_handle_events_hover_forwarded():
    # 悬停（无按钮 move）转给命中的窗格（整屏坐标），不切焦点
    mux = _FakeMux(sep_col=59)
    focus, exit_, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag = _run(
        mux, [_mouse(70, 5, "move", "none")],
    )
    assert focus == 0  # 悬停不切焦点
    assert (70, 5, "move", "none") in mux.mouse_calls  # 整屏坐标直接交给 Mux 换算


def test_handle_events_press_focuses_hit():
    # 点击命中右窗格 → 切焦点到该窗格
    mux = _FakeMux(sep_col=59)
    focus, exit_, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag = _run(
        mux, [_mouse(70, 5, "press", "left")],
    )
    assert focus == 1 and mux.focus == 1 and mux.focus_calls == [1]


def test_handle_events_resize_force_full():
    focus, exit_, cols, rows, dirty, split_col, drag, last_move, force_full, sel_drag = _run(
        _FakeMux(), [ResizeEvent()], cols=118, rows=30, split_col=59,
    )
    assert force_full is True and dirty
    assert (cols, rows) == (118, 30)


def test_wheel_route_by_mouse_grabbed():
    """滚轮路由：应用接管鼠标则转发，否则宿主代滚（宿主代滚 ±3 行）"""
    # 未接管鼠标 → 宿主代滚命中窗格
    mux = _FakeMux(mouse_grabbed=False)
    focus = handle_mouse(mux, 0, 118, 30, _mouse(30, 5, "scroll", "wheel_up"))
    assert mux.scroll_calls == [(0, 3)], mux.scroll_calls
    assert mux.mouse_calls == []
    focus = handle_mouse(mux, 0, 118, 30, _mouse(30, 5, "scroll", "wheel_down"))
    assert mux.scroll_calls == [(0, 3), (0, -3)]
    # 右窗格滚轮宿主代滚到右窗格
    focus = handle_mouse(mux, 0, 118, 30, _mouse(70, 5, "scroll", "wheel_up"))
    assert mux.scroll_calls[-1] == (1, 3)

    # 接管鼠标 → 转发应用（整屏坐标交给 Mux）
    mux2 = _FakeMux(mouse_grabbed=True)
    focus = handle_mouse(mux2, 0, 118, 30, _mouse(30, 5, "scroll", "wheel_up"))
    assert (30, 5, "scroll", "wheel_up") in mux2.mouse_calls
    assert mux2.scroll_calls == []


# ---- handle_events：文本选区 ----------------------------------------------


def test_selection_press_starts_drag():
    """左键 press 命中 pane（未 grab）→ pane_selection_set 起点（anchor=end）"""
    mux = _FakeMux(mouse_grabbed=False)
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [_mouse(30, 5, "press", "left")],
    )
    assert sel_drag == (0, 30, 5), sel_drag
    assert mux.selection_calls == [("set", 0, 30, 5, 30, 5)], mux.selection_calls
    assert dirty
    assert mux.focus == 0  # 切焦点到命中 pane


def test_selection_move_updates_end():
    """按住左键 move → pane_selection_set 更新终点"""
    mux = _FakeMux(mouse_grabbed=False)
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [
            _mouse(30, 5, "press", "left"),
            _mouse(45, 8, "move", "left"),
        ],
    )
    assert mux.selection_calls[-1] == ("set", 0, 30, 5, 45, 8), mux.selection_calls


def test_selection_release_copies_to_clipboard():
    """release → 取选区文本写系统剪贴板，清空选区状态"""
    mux = _FakeMux(mouse_grabbed=False)
    clip = _FakeClipboard()
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [
            _mouse(30, 5, "press", "left"),
            _mouse(45, 8, "move", "left"),
            _mouse(45, 8, "release", "left"),
        ], clipboard=clip,
    )
    assert clip.written == ["SEL_TEXT"], clip.written
    assert sel_drag is None
    assert dirty


def test_selection_double_click_word():
    """双击（count=2）→ select_word"""
    mux = _FakeMux(mouse_grabbed=False)
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [
            _mouse(30, 5, "press", "left", 0, 2),
        ],
    )
    assert ("word", 0, 30, 5) in mux.selection_calls, mux.selection_calls
    assert sel_drag is None  # 双击不进入拖拽


def test_selection_triple_click_line():
    """三击（count=3）→ select_line"""
    mux = _FakeMux(mouse_grabbed=False)
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [
            _mouse(30, 5, "press", "left", 0, 3),
        ],
    )
    assert ("line", 0, 30, 5) in mux.selection_calls, mux.selection_calls
    assert sel_drag is None


def test_selection_grab_does_not_select():
    """应用接管鼠标（vim）：press 不选区，转发给应用"""
    mux = _FakeMux(mouse_grabbed=True)
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [_mouse(30, 5, "press", "left")],
    )
    assert sel_drag is None
    assert mux.selection_calls == [], mux.selection_calls
    assert (30, 5, "press", "left") in mux.mouse_calls


def test_selection_release_without_drag_forwards():
    """无选区拖拽的 release → 转发应用（不复制）"""
    mux = _FakeMux(mouse_grabbed=False)
    clip = _FakeClipboard()
    _, _, _, _, dirty, _, _, _, _, sel_drag = _run(
        mux, [_mouse(30, 5, "release", "left")], clipboard=clip,
    )
    assert clip.written == []
    assert (30, 5, "release", "left") in mux.mouse_calls
