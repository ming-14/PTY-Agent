"""回放编排：读 asciicast 文件 → 终端模拟 → 实时渲染到宿主终端。

支持速度、循环、空闲限制、标记暂停、键盘控制（space=暂停 .=步进 ]=下一标记
ctrl+c=退出）。渲染用 pywezterm.Terminal + render_ansi() 全量重绘。
"""

import logging
import time
from typing import Optional

from leaf.adapters.castfile import open_from_path
from leaf.domain.asciicast import (
    Event, Output, Resize, Marker,
    limit_idle_time, accelerate,
)

log = logging.getLogger("leaf.player")

MIN_FRAME = 0.016  # 渲染限速 ~60fps


class Player:
    """回放器：读取 asciicast 事件序列，按时间喂入终端并渲染到宿主。

    term: pywezterm.Terminal 实例（用于喂数据 + 渲染 ANSI）
    output: OutputSink（write/flush 到宿主终端）
    console: ConsolePort（读输入键）
    """

    def __init__(self, term, output, console):
        self._term = term
        self._output = output
        self._console = console

    def play(self, path: str, speed: float = 1.0,
             idle_time_limit: Optional[float] = None,
             pause_on_markers: bool = False,
             loop: bool = False,
             auto_resize: bool = False) -> bool:
        """回放指定文件；返回 True=正常结束，False=用户中断"""
        self._output.write("\x1b[?25l\x1b[2J\x1b[H")
        self._output.flush()

        while True:
            finished = self._play_once(path, speed, idle_time_limit,
                                       pause_on_markers, auto_resize)
            if not loop or not finished:
                return finished

    def _play_once(self, path: str, speed: float,
                   idle_time_limit: Optional[float],
                   pause_on_markers: bool,
                   auto_resize: bool) -> bool:
        """回放一次，返回 True=正常结束"""
        header, _version, events = open_from_path(path)

        if idle_time_limit is not None:
            events = limit_idle_time(events, idle_time_limit)
        if speed != 1.0:
            events = accelerate(events, speed)

        self._term.resize(header.cols, header.rows)
        if auto_resize:
            self._console.resize((header.cols, header.rows))

        paused = False
        pause_epoch = 0.0
        epoch = time.monotonic()
        last_frame = 0.0
        event_iter = iter(events)

        def _render():
            nonlocal last_frame
            now = time.monotonic()
            if now - last_frame < MIN_FRAME:
                return
            last_frame = now
            ansi = self._term.render_ansi(include_cursor=True)
            self._output.write("\x1b[H" + ansi)
            self._output.flush()

        def _wait_key(timeout: float) -> Optional[bytes]:
            """读取一个键，超时返回 None"""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = int((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    return None
                if self._console.wait_input(remaining):
                    for ev in self._console.read_inputs():
                        if hasattr(ev, "key") and hasattr(ev, "down") and ev.down:
                            key = ev.key
                            if key == "Space":
                                return b" "
                            elif key == "Enter":
                                return b"\r"
                            elif key == "Esc":
                                return b"\x1b"
                            elif key == "Backspace":
                                return b"\x7f"
                            elif key == "Tab":
                                return b"\t"
                            elif key == "CtrlC":
                                return b"\x03"
                            elif len(key) == 1:
                                return key.encode("utf-8")
                return None
            return None

        # 逐事件处理
        for event in event_iter:
            if paused:
                # 暂停：等待按键
                while True:
                    ch = _wait_key(0.1)
                    if ch is None:
                        continue
                    if ch == b" ":
                        paused = False
                        # 恢复：epoch 前移（时间轴从暂停点继续）
                        epoch = time.monotonic() - pause_epoch
                        break
                    elif ch == b".":
                        # 单步：处理当前事件后继续暂停
                        self._feed_event(event)
                        self._render_full()
                        break
                    elif ch == b"]":
                        # 跳到下一标记：消费事件直到遇到 Marker
                        self._feed_event(event)
                        for ev2 in event_iter:
                            self._feed_event(ev2)
                            if isinstance(ev2.data, Marker):
                                break
                        self._render_full()
                        break
                    elif ch == b"\x03":
                        return False
                continue

            # 等待到事件时间
            target_time = event.time / speed
            delay = target_time - (time.monotonic() - epoch)
            if delay > 0:
                end = time.monotonic() + delay
                while time.monotonic() < end:
                    ch = _wait_key(0.016)
                    if ch is not None:
                        if ch == b" ":
                            paused = True
                            pause_epoch = time.monotonic() - epoch
                            break
                        elif ch == b"\x03":
                            return False
                    if time.monotonic() >= end:
                        break

            if paused:
                continue

            self._feed_event(event)
            _render()

            if pause_on_markers and isinstance(event.data, Marker):
                paused = True
                pause_epoch = time.monotonic() - epoch

        # 回放结束
        self._output.write("\r\n")
        self._output.flush()
        return True

    def _feed_event(self, event: Event) -> None:
        if isinstance(event.data, Output):
            self._term.feed(event.data.data.encode("utf-8"))
        elif isinstance(event.data, Resize):
            self._term.resize(event.data.cols, event.data.rows)
        # Input 在回放中忽略；Marker/Exit 不由终端处理

    def _render_full(self) -> None:
        """全量重绘"""
        ansi = self._term.render_ansi(include_cursor=True)
        self._output.write("\x1b[H" + ansi)
        self._output.flush()