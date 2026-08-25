"""帧渲染编排用例：render_loop 通过 Mux 门面增量渲染。

渲染不再手写 build_diff/cell_line/prev_view_top——整屏差分由 pywezterm.Mux
（Surface 合成 + 脏行驱动）承担：render_loop 只做编排（节流、清屏收敛帧、
组装状态栏文本、写回焦点光标），输出经注入的 OutputSink。
"""

import threading
import time
from typing import TypedDict

from leaf.usecases.ports import MuxPanelPort

MIN_FRAME = 0.008  # 渲染限速：两次 render 最短间隔（≤125fps），防风暴打满渲染
RENDER_POLL = 0.05  # 无输入时也定期渲染，以反映 Mux reader 线程喂入的后台 pane 输出


class RenderState(TypedDict, total=False):
    """渲染线程与主线程的共享契约（dict+lock 由组合根持有）"""

    cols: int
    rows: int
    focus: int
    render_event: threading.Event
    force_full: bool  # 由 render_loop pop，默认 False


def build_status(mux: MuxPanelPort, cols: int, rows: int, focus: int) -> str:
    """组装底部状态栏文本（焦点、各 pane 尺寸）"""
    sizes = mux.pane_sizes()
    left = "{}x{}".format(sizes[0][0], sizes[0][1]) if len(sizes) > 0 else "?x?"
    right = "{}x{}".format(sizes[1][0], sizes[1][1]) if len(sizes) > 1 else "?x?"
    focus_name = mux.name(focus) if mux.pane_count() else "?"
    return " F9 切换焦点   F10 退出----焦点:{}    左:{}  │  右:{}".format(
        focus_name, left, right
    )


def render_loop(mux: MuxPanelPort, output, state: RenderState, lock, stop_event: threading.Event) -> None:
    """渲染线程主循环：节流限速、收敛帧清屏、状态栏刷新、增量字节写回、焦点光标。

    无变化帧不输出任何字节（内容未变且光标未动则静默）：
    - 持续输出（每帧光标序列）会饿死 ConPTY 宿主的输入管道处理，导致
      键盘/鼠标事件无法被读取（OpenConsole 输入与输出的处理冲突）；
    - 后台 pane 输出（data 非空）或焦点/光标变化时仍即时输出。
    """
    prev_status = None
    last_frame = 0.0
    last_cursor = None
    while not stop_event.is_set():
        state["render_event"].wait(RENDER_POLL)
        state["render_event"].clear()
        with lock:
            force_full = state.pop("force_full", False)
            cols, rows, focus = state["cols"], state["rows"], state["focus"]
        if force_full:
            time.sleep(0.03)
            output.write("\x1b[3J")  # 仅清 scrollback（清屏/定位由 render 全量帧自带）
            prev_status = None
            last_cursor = None
        now = time.monotonic()
        delay = last_frame + MIN_FRAME - now
        if delay > 0:
            time.sleep(delay)
        last_frame = time.monotonic()
        status = build_status(mux, cols, rows, focus)
        if status != prev_status:
            mux.set_status(status)
            prev_status = status
        data, cr, cc, cv = mux.render()
        cursor = (cr, cc, cv)
        if not data and cursor == last_cursor:
            continue  # 无变化帧：不输出（避免持续写 stdout 饿死宿主输入处理）
        if data:
            output.write(data)
        output.write(mux.cursor_seq(cr, cc, cv))
        output.flush()
        last_cursor = cursor