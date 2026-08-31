"""asciicast v3 格式：纯领域层，定义事件/头/编解码器（v3 + raw + txt）。

零 I/O 零外部依赖，可独立测试。
"""

import json
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple, Union

# zstd 魔数（文件以该字节开头时走 zstd 解压）
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


# ---- 版本枚举 ----
class Version:
    V3 = 3


# ---- 事件数据变体（Union 风格） ----
@dataclass
class Output:
    data: str

@dataclass
class Input:
    data: str

@dataclass
class Resize:
    cols: int
    rows: int

@dataclass
class Marker:
    label: str

@dataclass
class Exit:
    status: int

@dataclass
class Other:
    code: str
    data: str


EventData = Union[Output, Input, Resize, Marker, Exit, Other]


@dataclass
class Event:
    """一个 asciicast 事件：时间戳（秒）+ 事件数据"""
    time: float
    data: EventData


@dataclass
class Header:
    """asciicast 头信息"""
    cols: int = 80
    rows: int = 24
    term_type: Optional[str] = None
    term_version: Optional[str] = None
    theme: Optional[dict] = None  # {"fg":"#rrggbb","bg":"#rrggbb","palette":"#rrggbb:...:..."}
    timestamp: Optional[int] = None  # unix epoch seconds
    idle_time_limit: Optional[float] = None
    command: Optional[str] = None
    title: Optional[str] = None
    env: Optional[Dict[str, str]] = None


# ---- 事件编码辅助 ----

def _encode_v3_dt(dt: float) -> str:
    """v3 格式 delta 时间：秒.毫秒（精确 3 位）。

    >>> _encode_v3_dt(0.0)
    '0.000'
    >>> _encode_v3_dt(0.666)
    '0.666'
    >>> _encode_v3_dt(1.0)
    '1.000'
    >>> _encode_v3_dt(12.345)
    '12.345'
    """
    ms = int(round(dt * 1000))
    secs = ms // 1000
    millis = ms % 1000
    return f"{secs}.{millis:03d}"


def _event_code_and_data(data: EventData) -> Tuple[str, str]:
    """事件数据类型 → (单字符码, 字符串载荷)"""
    if isinstance(data, Output):
        return ("o", data.data)
    elif isinstance(data, Input):
        return ("i", data.data)
    elif isinstance(data, Resize):
        return ("r", f"{data.cols}x{data.rows}")
    elif isinstance(data, Marker):
        return ("m", data.label)
    elif isinstance(data, Exit):
        return ("x", str(data.status))
    elif isinstance(data, Other):
        return (data.code, data.data)
    raise ValueError(f"未知事件类型: {type(data).__name__}")


def _parse_resize(s: str) -> Resize:
    """resize 事件载荷 'WxH' → Resize"""
    parts = s.split("x", 1)
    if len(parts) != 2:
        raise ValueError(f"无效 resize 尺寸: {s!r}")
    return Resize(cols=int(parts[0]), rows=int(parts[1]))


# ---- 时间量化器（Bresenham 误差扩散，用于 v3 编码） ----

class Quantizer:
    """基于 Bresenham 算法的误差扩散量化器。

    确保任意时刻的累积误差 < Q/2。
    """

    def __init__(self, q: int):
        self.q = q
        self.error = 0

    def next(self, value: int) -> int:
        corrected = value + self.error
        steps = (corrected + self.q // 2) // self.q
        quantized = steps * self.q
        self.error = corrected - quantized
        return quantized


# ---- v3 编解码 ----

class V3Decoder:
    """asciicast v3 格式解码器（JSONL，相对时间，毫秒精度）。"""

    @staticmethod
    def decode_header(line: str) -> Tuple[Header, int]:
        """解析第一行头 JSON → (Header, version)"""
        h = json.loads(line)
        if h.get("version") != 3:
            raise ValueError(f"not an asciicast v3 file: version={h.get('version')}")
        term = h.get("term", {})
        theme = None
        if "theme" in term:
            th = term["theme"]
            theme = {"fg": th["fg"], "bg": th["bg"], "palette": th.get("palette", "")}
        header = Header(
            cols=term.get("cols", 80),
            rows=term.get("rows", 24),
            term_type=term.get("type"),
            term_version=term.get("version"),
            theme=theme,
            timestamp=h.get("timestamp"),
            idle_time_limit=h.get("idle_time_limit"),
            command=h.get("command"),
            title=h.get("title"),
            env=h.get("env"),
        )
        return header, h["version"]

    @staticmethod
    def decode_event(line: str, prev_time: float = 0.0) -> Event:
        """解析一行事件 JSON，prev_time 为累积时间（v3 事件时间是 delta）"""
        ev = json.loads(line)
        dt = float(ev[0])  # delta 秒（"S.mmm" 字符串 → float）
        code = ev[1]
        data = ev[2]
        t = prev_time + dt
        if code == "o":
            return Event(t, Output(data))
        elif code == "i":
            return Event(t, Input(data))
        elif code == "r":
            return Event(t, _parse_resize(data))
        elif code == "m":
            return Event(t, Marker(data))
        elif code == "x":
            return Event(t, Exit(int(data) if data else 0))
        else:
            return Event(t, Other(code, data))


class V3Encoder:
    """asciicast v3 格式编码器（相对 delta 时间，毫秒精度，量化）。"""

    def __init__(self, time_quantum_ms: int = 1):
        self._prev_time = 0.0
        self._quantizer = Quantizer(1_000_000)  # 1ms 纳秒量化

    def encode_header(self, header: Header) -> str:
        term = {"cols": header.cols, "rows": header.rows}
        if header.term_type is not None:
            term["type"] = header.term_type
        if header.term_version is not None:
            term["version"] = header.term_version
        if header.theme is not None:
            term["theme"] = header.theme
        obj = {"version": 3, "term": term}
        if header.timestamp is not None:
            obj["timestamp"] = header.timestamp
        if header.idle_time_limit is not None:
            obj["idle_time_limit"] = header.idle_time_limit
        if header.command is not None:
            obj["command"] = header.command
        if header.title is not None:
            obj["title"] = header.title
        if header.env:
            obj["env"] = header.env
        return json.dumps(obj, separators=(",", ":"))

    def encode_event(self, event: Event) -> str:
        code, data = _event_code_and_data(event.data)
        dt_ns = int((event.time - self._prev_time) * 1_000_000_000)
        dt_quantized_ns = self._quantizer.next(dt_ns)
        dt_sec = dt_quantized_ns / 1_000_000_000
        self._prev_time = event.time
        ts = _encode_v3_dt(dt_sec)
        return f"[{ts}, {json.dumps(code)}, {json.dumps(data)}]"

    def encode_event_line(self, event: Event) -> bytes:
        return (self.encode_event(event) + "\n").encode("utf-8")


# ---- 文件打开 ----

def is_zstd(data: bytes) -> bool:
    """检查文件头部是否为 zstd 魔数"""
    return data[:4] == ZSTD_MAGIC


def open_cast(lines: Iterator[str]) -> Tuple[Header, Version, Iterator[Event]]:
    """从文本行迭代器读取 asciicast v3。

    返回 (header, version, events_iterator)。
    """
    first = next(lines)
    header, _ = V3Decoder.decode_header(first)
    return header, Version.V3, _v3_events(lines)


def _v3_events(lines: Iterator[str]):
    prev_time = 0.0
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        event = V3Decoder.decode_event(line, prev_time)
        prev_time = event.time
        yield event


# ---- 空闲时间限制与加速 ----

def limit_idle_time(events: Iterator[Event], limit: float) -> Iterator[Event]:
    """限制空闲时间：超过 limit 秒的间隔被截断至 limit 秒。

    各事件之前的超长间隔减去超出部分，后续事件时间前移。
    """
    limit_ns = int(limit * 1_000_000_000)
    prev_time_ns = 0
    offset_ns = 0
    for event in events:
        curr_ns = int(event.time * 1_000_000_000)
        delay = curr_ns - prev_time_ns
        if delay > limit_ns:
            offset_ns += delay - limit_ns
        prev_time_ns = curr_ns
        new_time = (curr_ns - offset_ns) / 1_000_000_000
        yield Event(new_time, event.data)


def accelerate(events: Iterator[Event], speed: float) -> Iterator[Event]:
    """加速/减速：按 speed 倍数压缩时间轴。"""
    for event in events:
        yield Event(event.time / speed, event.data)


# ---- 原始 / 文本编码器（用于 convert） ----

class RawEncoder:
    """raw 格式：仅输出数据，\x1b[8;rows;colst 头 + 全部 Output 数据。"""

    def encode_header(self, header: Header) -> bytes:
        return f"\x1b[8;{header.rows};{header.cols}t".encode("utf-8")

    def encode_event(self, event: Event) -> bytes:
        if isinstance(event.data, Output):
            return event.data.data.encode("utf-8")
        if isinstance(event.data, Resize):
            # 嵌入 resize 为 \x1b[8;rows;colst
            return f"\x1b[8;{event.data.rows};{event.data.cols}t".encode("utf-8")
        return b""