"""输出管线 — 按后端模式分流 reader 数据

reader 线程读取到的原始字节统一交给 pipeline.push(data)：

- StreamPipeline（subprocess 模式）：字节 → UTF-8 解码 → 文本行缓冲
- ScreenPipeline（pty 模式）   ：字节 → pyte 终端仿真 → 滚动历史 + 可见屏幕

两种管线均将结果写入 OutputBuffer（文本行级），触发匹配/读取/
增量输出全部基于文本，不在播放端处理 ANSI（subprocess 原样透传）。
"""

import logging
from typing import Optional

import pyte

from ...config import MAX_HISTORY_LINES
from ..encoding import decode_utf8
from .buffer import OutputBuffer
from .screen import ScrollbackScreen

_logger = logging.getLogger("pty-session")


class StreamPipeline:
    """subprocess 模式输出管线（纯管道文本流）"""

    def __init__(self, out_buf: OutputBuffer):
        self._buf = out_buf

    def push(self, data: bytes) -> None:
        """将读取到的原始字节解码后写入输出缓冲

        Args:
            data: 后端读取的原始字节。
        """
        if not data:
            return
        text = decode_utf8(data)
        if text:
            self._buf.append_text(text)


class ScreenPipeline:
    """pty 模式输出管线（pyte 终端仿真）"""

    def __init__(self, out_buf: OutputBuffer,
                 cols: int = 80, rows: int = 24,
                 scrollback: Optional[int] = None):
        self._screen = ScrollbackScreen(
            cols=cols, rows=rows,
            scrollback=scrollback if scrollback is not None
            else MAX_HISTORY_LINES,
        )
        self._stream = pyte.ByteStream(self._screen)
        self._buf = out_buf

    @property
    def screen(self) -> ScrollbackScreen:
        """底层屏幕对象（测试/调试用）"""
        return self._screen

    def push(self, data: bytes) -> None:
        """将读取到的原始字节喂给 pyte，并把新增历史与可见屏写入缓冲

        Args:
            data: 后端读取的原始字节。
        """
        if not data:
            return
        self._stream.feed(data)
        new_history = self._screen.drain_new_history()
        if new_history:
            self._buf.append_history(new_history)
        self._buf.replace_visible(self._screen.visible_lines())


try:
    from ...config import MAX_HISTORY_LINES as MAX_SCROLLBACK_DEFAULT
except Exception:  # pragma: no cover
    MAX_SCROLLBACK_DEFAULT = 10000