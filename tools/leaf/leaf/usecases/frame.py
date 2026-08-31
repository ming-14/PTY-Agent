"""帧渲染编排用例：render_loop 通过 Mux 门面增量渲染。

渲染不再手写 build_diff/cell_line/prev_view_top——整屏差分由 pywezterm.Mux
（Surface 合成 + 脏行驱动）承担：render_loop 只做编排（节流、清屏收敛帧、
组装状态栏文本、写回焦点光标），输出经注入的 OutputSink。
"""

import threading
import time
from typing import Optional, TypedDict

from leaf.usecases.ports import MuxPanelPort, RecorderSlot

MIN_FRAME = 0.008  # 渲染限速：两次 render 最短间隔（≤125fps），防风暴打满渲染
# 兜底轮询：正常由 Mux 新输出回调（set render_event）驱动，无输入场景
# （如 headless）靠此轮询保证录制/显示不饿死
RENDER_POLL = 0.01


class RenderState(TypedDict, total=False):
    """渲染线程与主线程的共享契约（dict+lock 由组合根持有）"""

    cols: int
    rows: int
    focus: int
    render_event: threading.Event
    force_full: bool  # 由 render_loop pop，默认 False


def build_status(mux: MuxPanelPort, cols: int, rows: int, focus: int,
                 recording: bool = False, blink: bool = False) -> str:
    """组装底部状态栏文本（焦点、各 pane 尺寸、录制指示）"""
    sizes = mux.pane_sizes()
    left = "{}x{}".format(sizes[0][0], sizes[0][1]) if len(sizes) > 0 else "?x?"
    right = "{}x{}".format(sizes[1][0], sizes[1][1]) if len(sizes) > 1 else "?x?"
    focus_name = mux.name(focus) if mux.pane_count() else "?"
    s = " F9 切换焦点   F10 退出----焦点:{}    左:{}  │  右:{}".format(
        focus_name, left, right
    )
    if recording:
        dot = "·" if blink else " "
        s += f"  {dot} REC"
    return s


def render_loop(mux: MuxPanelPort, output, state: RenderState, lock, stop_event: threading.Event,
                recorder_slot: Optional[RecorderSlot] = None) -> None:
    """渲染线程主循环：节流限速、收敛帧清屏、状态栏刷新、增量字节写回、焦点光标。

    recorder_slot: 可选，F8 动态切换录制器（线程安全容器，每帧取用）。
    """
    prev_status = None
    last_frame = 0.0
    last_cursor = None
    while not stop_event.is_set():
        state["render_event"].wait(RENDER_POLL)
        state["render_event"].clear()
        # 每帧在 wait 唤醒后取当前录制器（F8 可能在前一帧后切换了 slot，
        # 在 wait 之后取确保 F8 开始录制的第一帧就能取到新录制器）
        recorder = recorder_slot.get() if recorder_slot else None
        with lock:
            force_full = state.pop("force_full", False)
            cols, rows, focus = state["cols"], state["rows"], state["focus"]
        if force_full:
            time.sleep(0.03)
            output.write("\x1b[3J")  # 仅清 scrollback（清屏/定位由 render 全量帧自带）
            prev_status = None
            last_cursor = None
            if recorder is not None:
                mux.force_repaint()  # 强制下一帧全量，录制连续
        now = time.monotonic()
        delay = last_frame + MIN_FRAME - now
        if delay > 0:
            time.sleep(delay)
        last_frame = time.monotonic()
        status = build_status(mux, cols, rows, focus,
                              recording=recorder is not None,
                              blink=int(now / 0.5) % 2 == 0)
        if status != prev_status:
            mux.set_status(status)
            prev_status = status
        data, cr, cc, cv = mux.render()
        cursor = (cr, cc, cv)
        if not data and cursor == last_cursor:
            continue  # 无变化帧：不输出（避免持续写 stdout 饿死宿主输入处理）
        if data:
            output.write(data)
        cursor_seq = mux.cursor_seq(cr, cc, cv)
        output.write(cursor_seq)
        if recorder is not None:
            combined = (bytes(data) if data else b"") + cursor_seq.encode("utf-8")
            recorder.output(combined)
        output.flush()
        last_cursor = cursor