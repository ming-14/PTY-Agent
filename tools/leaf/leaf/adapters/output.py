"""输出适配：渲染帧写出的目标抽象。

StdoutSink 渲染帧 → stdout（TUI 全屏重绘的唯一输出通道）
"""

import sys


class StdoutSink:
    """OutputSink 实现：默认包装 sys.stdout。

    Mux render 产出 bytes（增量 ANSI 序列），光标/清场序列为 str——
    write 按类型分派，bytes 走流底下的二进制缓冲，str 走文本流。
    """

    def __init__(self, stream=None):
        self._stream = stream or sys.stdout

    def write(self, s) -> None:
        if isinstance(s, bytes):
            self._stream.buffer.write(s)
        else:
            self._stream.write(s)

    def flush(self) -> None:
        self._stream.flush()
