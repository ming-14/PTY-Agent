"""输出适配：渲染帧写出的目标抽象。

StdoutSink 渲染帧 → stdout（TUI 全屏重绘的唯一输出通道）
NullSink 丢弃渲染字节（headless 录制：渲染循环仍驱动 Mux 合成，输出丢弃）
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


class NullSink:
    """OutputSink 实现：丢弃全部渲染字节（headless 录制用）。

    渲染循环照常驱动 Mux 合成（录制依赖 render 输出），仅宿主侧不显示。
    """

    def write(self, s) -> None:
        pass

    def flush(self) -> None:
        pass