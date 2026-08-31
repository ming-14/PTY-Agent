"""asciicast v3 格式白盒测试：编解码、空闲限制、加速、zstd、cat、convert。"""

import io
import json
import os
import sys
import tempfile

# ---- 导入（先确保路径正确） ----
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from leaf.domain.asciicast import (
    Event, Output, Input, Resize, Marker, Exit, Header, Version,
    V3Decoder, V3Encoder,
    RawEncoder, open_cast, limit_idle_time, accelerate,
    _encode_v3_dt,
)
from leaf.adapters.castfile import open_from_path, CastFileWriter, CastError


# ---- 时间格式辅助 ----

class TestTimeFormat:
    def test_v3_dt(self):
        assert _encode_v3_dt(0.0) == "0.000"
        assert _encode_v3_dt(0.666) == "0.666"
        assert _encode_v3_dt(1.0) == "1.000"
        assert _encode_v3_dt(12.345) == "12.345"


# ---- v3 解码 ----

class TestV3Decode:
    def test_header(self):
        line = '{"version":3,"term":{"cols":100,"rows":50},"title":"t","command":"cmd"}'
        h, ver = V3Decoder.decode_header(line)
        assert ver == Version.V3
        assert h.cols == 100 and h.rows == 50
        assert h.title == "t" and h.command == "cmd"

    def test_event_output(self):
        ev = V3Decoder.decode_event('[1.230,"o","hello"]')
        assert abs(ev.time - 1.23) < 1e-9
        assert isinstance(ev.data, Output)
        assert ev.data.data == "hello"

    def test_event_resize(self):
        ev = V3Decoder.decode_event('[2.5,"r","80x40"]', prev_time=1.0)
        assert abs(ev.time - 3.5) < 1e-9
        assert isinstance(ev.data, Resize)
        assert ev.data.cols == 80 and ev.data.rows == 40

    def test_event_marker(self):
        ev = V3Decoder.decode_event('[0.5,"m","phase1"]')
        assert isinstance(ev.data, Marker)
        assert ev.data.label == "phase1"

    def test_event_exit(self):
        ev = V3Decoder.decode_event('[0.5,"x","0"]')
        assert isinstance(ev.data, Exit)
        assert ev.data.status == 0

    def test_delta_time_accumulation(self):
        prev = 0.0
        t1 = V3Decoder.decode_event('[1.0,"o","a"]', prev)
        prev = t1.time
        t2 = V3Decoder.decode_event('[0.5,"o","b"]', prev)
        assert abs(t2.time - 1.5) < 1e-9


# ---- v3 编码器 roundtrip ----

class TestV3Encoder:
    def test_roundtrip(self):
        """v3 编码后解码应与原事件一致"""
        header = Header(cols=80, rows=24, command="cmd.exe")
        enc = V3Encoder()
        hdr = enc.encode_header(header)
        events = [
            Event(0.0, Output("start\n")),
            Event(0.5, Output("more\n")),
            Event(1.5, Input("y\r")),
            Event(2.0, Resize(90, 30)),
            Event(5.0, Exit(1)),
        ]
        lines = [hdr + "\n"] + [enc.encode_event_line(e).decode() + "\n" for e in events]
        header2, ver2, events2 = open_cast(iter(lines))
        assert ver2 == Version.V3
        assert header2.cols == 80 and header2.rows == 24
        evs2 = list(events2)
        assert len(evs2) == 5
        # v3 使用量化，时间误差不超过 1ms
        for i, (e1, e2) in enumerate(zip(events, evs2)):
            assert abs(e1.time - e2.time) < 0.002, f"event[{i}] time diff: {abs(e1.time - e2.time)}"
            assert type(e1.data) == type(e2.data), f"event[{i}] type mismatch"
        assert isinstance(evs2[3].data, Resize) and evs2[3].data.cols == 90


# ---- 空闲时间限制与加速 ----

class TestTransforms:
    def test_limit_idle_time(self):
        events = [
            Event(0.0, Output("a")),
            Event(1.0, Output("b")),
            Event(3.5, Output("c")),
            Event(4.0, Output("d")),
            Event(7.5, Output("e")),
        ]
        result = list(limit_idle_time(iter(events), 2.0))
        expected = [0.0, 1.0, 3.0, 3.5, 5.5]
        for i, r in enumerate(result):
            assert abs(r.time - expected[i]) < 1e-9, f"idx {i}: {r.time} != {expected[i]}"

    def test_accelerate(self):
        events = [Event(0, Output("a")), Event(20e-6, Output("b")), Event(50e-6, Output("c"))]
        result = list(accelerate(iter(events), 2.0))
        expected = [0.0, 10e-6, 25e-6]
        for i, r in enumerate(result):
            assert abs(r.time - expected[i]) < 1e-12, f"idx {i}: {r.time}"
            assert r.data.data == events[i].data.data


# ---- CastFileWriter 集成 ----

class TestCastFileWriter:
    def test_write_v3_and_read(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=80, rows=24, title="test")
            writer = CastFileWriter(path, header)
            writer.write_event(Event(1.0, Output("hello\n")))
            writer.write_event(Event(2.0, Resize(100, 40)))
            writer.finish()
            # 读回
            h2, ver2, events = open_from_path(path)
            assert ver2 == Version.V3
            assert h2.cols == 80 and h2.rows == 24
            evs = list(events)
            assert len(evs) == 2
            assert isinstance(evs[0].data, Output)
            assert evs[0].data.data == "hello\n"
            assert isinstance(evs[1].data, Resize)
        finally:
            os.unlink(path)

    def test_append(self):
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=80, rows=24)
            w1 = CastFileWriter(path, header)
            w1.write_event(Event(1.0, Output("first")))
            w1.finish()
            # 追加：会写入一个 resize 锚事件（delta 0）作为追加部分的第一个事件
            w2 = CastFileWriter(path, header, append=True)
            w2.write_event(Event(2.0, Output("second")))
            w2.finish()
            # 读回
            h, ver, events = open_from_path(path)
            evs = list(events)
            # 文件内容：header + first(1.0) + resize锚(1.0) + second(3.0)
            assert len(evs) == 3
            assert isinstance(evs[0].data, Output) and evs[0].data.data == "first"
            assert isinstance(evs[1].data, Resize)
            assert isinstance(evs[2].data, Output) and evs[2].data.data == "second"
            assert abs(evs[0].time - 1.0) < 0.001
            assert abs(evs[1].time - 1.0) < 0.001  # resize 锚点在 first 末尾
            assert abs(evs[2].time - 3.0) < 0.001  # 1.0 + 2.0
        finally:
            os.unlink(path)


# ---- cat / convert ----

class TestCat:
    def test_cat_two_casts(self):
        from leaf.usecases.cast_ops import cat
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f1:
            p1 = f1.name
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f2:
            p2 = f2.name
        try:
            h = Header(cols=80, rows=24)
            w1 = CastFileWriter(p1, h)
            w1.write_event(Event(1.0, Output("a")))
            w1.finish()
            w2 = CastFileWriter(p2, h)
            w2.write_event(Event(2.0, Output("b")))
            w2.finish()
            buf = io.BytesIO()
            cat([p1, p2], buf, output_format="v3")
            buf.seek(0)
            # 读回验证绝对时间轴连续
            header2, ver2, events2 = open_cast(
                (l + "\n" for l in buf.getvalue().decode("utf-8").strip().split("\n")))
            evs = list(events2)
            assert len(evs) == 2
            assert evs[0].data.data == "a"
            assert abs(evs[0].time - 1.0) < 0.001
            assert evs[1].data.data == "b"
            assert abs(evs[1].time - 3.0) < 0.001  # 1.0 + 2.0
        finally:
            for p in (p1, p2):
                if os.path.exists(p):
                    os.unlink(p)


class TestConvert:
    def test_convert_to_raw(self):
        from leaf.usecases.cast_ops import convert
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            inp = f.name
        try:
            h = Header(cols=80, rows=24)
            w = CastFileWriter(inp, h)
            w.write_event(Event(1.0, Output("hello\r\n")))
            w.finish()
            buf = io.BytesIO()
            convert(inp, buf, "raw", overwrite=True)
            buf.seek(0)
            data = buf.getvalue()
            # raw 格式: \x1b[8;24;80t + "hello\r\n"
            assert data.startswith(b"\x1b[8;24;80t")
            assert b"hello\r\n" in data
        finally:
            if os.path.exists(inp):
                os.unlink(inp)

    def test_convert_v3_roundtrip(self):
        from leaf.usecases.cast_ops import convert
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            inp = f.name
        try:
            h = Header(cols=80, rows=24)
            w = CastFileWriter(inp, h)
            w.write_event(Event(1.0, Output("hello")))
            w.finish()
            buf = io.BytesIO()
            convert(inp, buf, "v3", overwrite=True)
            buf.seek(0)
            data = buf.getvalue().decode("utf-8")
            lines = data.strip().split("\n")
            assert len(lines) == 2
            h2 = json.loads(lines[0])
            assert h2["version"] == 3
            assert h2["term"]["cols"] == 80
        finally:
            if os.path.exists(inp):
                os.unlink(inp)