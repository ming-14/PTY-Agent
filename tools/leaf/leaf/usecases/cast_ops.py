"""asciicast 文件操作用例：cat（拼接）与 convert（格式转换）。

cat：多个录制按顺序拼接，时间轴连续，尺寸变化处插入 resize 事件。
convert：输入 v3 → 输出 v3/raw/txt。
"""

import io
import logging
from typing import Optional

from leaf.adapters.castfile import open_from_path
from leaf.domain.asciicast import (
    Event, Output, Resize, Header, Version,
    V3Encoder, RawEncoder,
)

log = logging.getLogger("leaf.cast")


def _new_encoder():
    return V3Encoder()


def cat(files, output, output_format: Optional[str] = None) -> None:
    """拼接多个录制文件。

    files: 输入文件路径列表
    output: 输出路径（'-' = stdout）或文件对象
    """
    casts = [open_from_path(f) for f in files]
    header, version, _ = casts[0]
    enc = _new_encoder()

    if hasattr(output, "write"):
        out = output
    else:
        if output == "-":
            import sys
            out = sys.stdout.buffer
        else:
            out = open(output, "wb")

    try:
        out.write(enc.encode_header(header).encode("utf-8") + b"\n")
        time_offset = 0.0
        prev_cols, prev_rows = header.cols, header.rows

        for h, _ver, events in casts:
            if h.cols != prev_cols or h.rows != prev_rows:
                # 尺寸变化：插入 resize 事件锚定新尺寸
                ev = Event(time_offset, Resize(h.cols, h.rows))
                out.write(enc.encode_event_line(ev))
                prev_cols, prev_rows = h.cols, h.rows
            evs = list(events)  # 物化：需两次遍历（写 + 取末时间）
            for ev in evs:
                ev.time += time_offset
                out.write(enc.encode_event_line(ev))
            if evs:
                time_offset = evs[-1].time
        out.flush()
    finally:
        if not hasattr(output, "write") and output != "-":
            out.close()


def convert(input_path, output_path, output_format: str,
            overwrite: bool = False, text_term=None) -> None:
    """转换格式。

    input_path: 输入文件（本地/URL/zstd）
    output_path: 输出路径（'-' = stdout，.zst 后缀自动压缩）
    output_format: v3/raw/txt
    overwrite: 是否覆盖已存在文件
    text_term: txt 格式时传入 pywezterm.Terminal 渲染（None 则内部创建）
    """
    import os
    import sys

    if not hasattr(output_path, "write") and output_path != "-" \
            and not overwrite and os.path.exists(output_path):
        if os.path.getsize(output_path) > 0:
            raise ValueError("输出文件已存在，使用 overwrite=True 覆盖")

    compressed = isinstance(output_path, str) and output_path.lower().endswith(".zst")
    if compressed:
        try:
            import zstandard as zstd
        except ImportError:
            raise ValueError("zstandard 未安装，无法写 .zst 压缩文件")

    header, _version, events = open_from_path(input_path)

    if output_format == "txt":
        if text_term is None:
            from leaf.drivers import _engine
            _engine.ensure_engine()
            import pywezterm
            text_term = pywezterm.Terminal(header.cols, header.rows, scrollback=10000)
        for ev in events:
            if isinstance(ev.data, Output):
                text_term.feed(ev.data.data.encode("utf-8"))
            elif isinstance(ev.data, Resize):
                text_term.resize(ev.data.cols, ev.data.rows)
        sb = text_term.render_scrollback(False)
        vis = text_term.text()
        data = (sb + ("\n" if sb else "") + vis).encode("utf-8")
    elif output_format == "raw":
        enc = RawEncoder()
        buf = io.BytesIO()
        buf.write(enc.encode_header(header))
        for ev in events:
            buf.write(enc.encode_event(ev))
        data = buf.getvalue()
    elif output_format == "v3":
        enc = _new_encoder()
        buf = io.BytesIO()
        buf.write(enc.encode_header(header).encode("utf-8") + b"\n")
        for ev in events:
            buf.write(enc.encode_event_line(ev))
        data = buf.getvalue()
    else:
        raise ValueError(f"不支持的输出格式: {output_format}")

    if compressed:
        import zstandard as zstd
        data = zstd.ZstdCompressor().compress(data)

    if output_path == "-":
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    elif hasattr(output_path, "write"):
        output_path.write(data)
    else:
        with open(output_path, "wb") as f:
            f.write(data)