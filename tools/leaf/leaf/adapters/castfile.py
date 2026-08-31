"""asciicast 文件读写适配：打开（v3 + zstd）与写出（后台线程）。

打开路径：本地文件或 http(s) URL（下载到临时文件）。
写出：编码器 + 后台写线程，调用方只投递事件不阻塞渲染。
"""

import io
import os
import queue
import tempfile
import threading
import urllib.request
from typing import Iterator, Optional, Tuple

from leaf.domain.asciicast import (
    Event, Resize as ResizeEvent, Header, Version, ZSTD_MAGIC,
    V3Decoder, V3Encoder, is_zstd,
)

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover
    zstd = None


class CastError(Exception):
    """asciicast 文件读写错误"""


# ---- 打开 ----

def get_local_path(path: str) -> str:
    """本地文件原样返回；http(s) URL 下载到临时文件并返回其路径"""
    if path.startswith("http://") or path.startswith("https://"):
        with urllib.request.urlopen(path) as resp:
            data = resp.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".cast") as f:
            f.write(data)
            return f.name
    return path


def _make_events(reader):
    """事件迭代器（v3 delta 时间）：逐行读取，迭代结束时关闭 reader。"""
    try:
        prev_time = 0.0
        for line in reader:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            event = V3Decoder.decode_event(line, prev_time)
            prev_time = event.time
            yield event
    finally:
        reader.close()


def open_from_path(path: str):
    """打开 asciicast v3 文件，返回 (header, version, events迭代器)。

    支持本地路径 / http(s) URL；文件可为 zstd 压缩（自动检测魔数解压）。
    迭代器在消费完所有事件后自动关闭文件。
    """
    path = get_local_path(path)
    f = open(path, "rb")
    head = f.read(4)
    f.seek(0)
    if head == ZSTD_MAGIC:
        if zstd is None:
            f.close()
            raise CastError("zstandard 未安装，无法读取 zstd 压缩的 asciicast")
        reader = io.TextIOWrapper(
            zstd.ZstdDecompressor().stream_reader(f), encoding="utf-8"
        )
    else:
        reader = io.TextIOWrapper(f, encoding="utf-8")

    first = reader.readline()
    if not first:
        reader.close()
        raise CastError("empty asciicast file")
    first = first.rstrip("\n")
    header, _ = V3Decoder.decode_header(first)
    return header, Version.V3, _make_events(reader)


def get_duration(path: str) -> float:
    """读取最后一个事件的时间（秒）"""
    _, _, events = open_from_path(path)
    last = 0.0
    for ev in events:
        last = ev.time
    return last


# ---- 写出 ----

class CastFileWriter:
    """asciicast v3 写出器：后台线程消费事件队列，编码写文件。

    - 支持 append：写入前先读现有文件末尾时间作为 time_offset，
      且不写 header（追加时用 resize 事件锚定新尺寸）；
    - 支持 zstd 压缩（路径 .zst 结尾）；
    - finish() 等待后台线程把队列写空后关闭。
    """

    def __init__(self, path: str, header: Header,
                 append: bool = False, overwrite: bool = False):
        self._path = path
        self._q: "queue.Queue" = queue.Queue(maxsize=4096)
        self._stop = threading.Event()
        self._error: Optional[Exception] = None

        if append:
            if not os.path.exists(path):
                append = False
            else:
                try:
                    self._offset = get_duration(path)
                except Exception as e:
                    raise CastError(f"can't append: {e}")
                existing = self._detect_version(path)
                if existing != Version.V3:
                    raise CastError("can't append: 现有文件不是 asciicast v3")
        else:
            self._offset = 0.0

        mode = "ab" if append else "wb"
        self._file = open(path, mode, buffering=0)
        self._compressed = path.lower().endswith(".zst")
        if self._compressed:
            if zstd is None:
                self._file.close()
                raise CastError("zstandard 未安装，无法写 zstd 压缩的 asciicast")
            self._raw = self._file
            self._file = zstd.ZstdCompressor().stream_writer(self._file, closefd=False)
        else:
            self._raw = None

        self._enc = V3Encoder()

        # 写 header（append 模式不写 header，写一个 resize 锚事件）
        if append:
            self._file.write(self._enc.encode_event_line(
                Event(0.0, ResizeEvent(header.cols, header.rows))
            ))
            self._file.flush()
        else:
            hdr = self._enc.encode_header(header)
            self._file.write(hdr.encode("utf-8") + b"\n")
            self._file.flush()

        self._thread = threading.Thread(target=self._run, name="cast-writer", daemon=True)
        self._thread.start()

    def _detect_version(self, path: str) -> int:
        try:
            _, ver, _ = open_from_path(path)
            return ver
        except Exception:
            raise CastError(f"can't append: {path} 不是合法的 asciicast 文件")

    def _run(self) -> None:
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                self._file.write(item)
                self._file.flush()
        except Exception as e:
            self._error = e
        finally:
            self._file.close()

    def write_event(self, event: Event) -> None:
        """投递一个事件到后台写入队列。"""
        if self._error is not None:
            raise CastError(str(self._error))
        line = self._enc.encode_event_line(event)
        self._q.put(line)

    def finish(self, timeout: float = 5.0) -> None:
        """等待后台线程写完所有事件后关闭。"""
        self._q.put(None)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise CastError("writer thread did not finish")
        if self._error is not None:
            raise CastError(str(self._error))