"""录制编排：捕获输出/输入/尺寸/标记/退出事件 → 写 asciicast。

依赖端口协议（CastWriterPort），不接触具体框架。
支持暂停/恢复（F12）：暂停期间输出与输入被丢弃，时间轴冻结。
"""

import logging
import time
from typing import Optional

from leaf.domain.asciicast import (
    Event, Output, Input, Resize, Marker, Exit,
)
from leaf.usecases.ports import RecorderPort

log = logging.getLogger("leaf.recorder")


class Utf8Decoder:
    """增量 UTF-8 解码：喂入字节，返回完整字符；跨块的不完整序列缓存。

    asciinema 的 Utf8Decoder 语义：非法字节替换为 U+FFFD。
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> str:
        out = []
        self._buf.extend(data)
        while self._buf:
            try:
                s = bytes(self._buf).decode("utf-8")
                out.append(s)
                self._buf.clear()
                break
            except UnicodeDecodeError as e:
                valid = e.start
                if valid > 0:
                    out.append(bytes(self._buf[:valid]).decode("utf-8"))
                    del self._buf[:valid]
                    continue
                if e.reason == "unexpected end of data":
                    break  # 不完整序列，等更多字节
                # 非法字节：替换并丢弃
                out.append("\ufffd")
                del self._buf[: e.end - e.start or 1]
        return "".join(out)


class Recorder:
    """录制器（RecorderPort 实现）：接收事件并投递到 writer。

    用法：
        recorder = Recorder(writer)
        recorder.output(data)   # 子进程输出字节
        recorder.input(data)    # 键盘输入字节
        recorder.resize(cols, rows)
        recorder.marker(label)
        recorder.exit(status)
        recorder.toggle_pause() # F12
        recorder.finish()
    """

    def __init__(self, writer):
        self._writer = writer
        self._epoch = time.monotonic()
        self._paused = False
        self._pause_time = 0.0
        self._time_offset = 0.0
        self._out_decoder = Utf8Decoder()
        self._in_decoder = Utf8Decoder()

    def _now(self) -> float:
        """当前录制时间：暂停期间冻结在暂停点，恢复时累计偏移"""
        if self._paused:
            return self._pause_time
        return time.monotonic() - self._epoch - self._time_offset

    def toggle_pause(self) -> bool:
        """暂停/恢复录制（F12）；返回暂停后的状态（True=已暂停）"""
        if self._paused:
            self._paused = False
            # 暂停期间的时间不算入录制：epoch 前移
            self._epoch = time.monotonic() - (self._pause_time + self._time_offset)
            log.info("recording resumed")
        else:
            self._pause_time = self._now()
            self._paused = True
            log.info("recording paused")
        return self._paused

    def output(self, data: bytes) -> None:
        """记录子进程输出字节（原始 ANSI/文本）"""
        if not data or self._paused:
            return
        text = self._out_decoder.feed(data)
        if text:
            self._writer.write_event(Event(self._now(), Output(text)))

    def input(self, data: bytes) -> None:
        """记录键盘输入字节"""
        if not data or self._paused:
            return
        text = self._in_decoder.feed(data)
        if text:
            self._writer.write_event(Event(self._now(), Input(text)))

    def resize(self, cols: int, rows: int) -> None:
        """记录终端尺寸变化（暂停期间也记录：恢复后需重锚尺寸）"""
        self._writer.write_event(Event(self._now(), Resize(cols, rows)))

    def marker(self, label: str = "") -> None:
        """记录标记（暂停期间也可加标记，与 asciinema 一致）"""
        self._writer.write_event(Event(self._now(), Marker(label)))

    def exit(self, status: int = 0) -> None:
        """记录会话结束"""
        self._writer.write_event(Event(self._now(), Exit(status)))

    def finish(self) -> None:
        """完成录制（刷新 & 关闭文件）"""
        self._writer.finish()