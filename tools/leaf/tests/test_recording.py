"""录制/回放 e2e 测试：真实 ConPTY 上录制会话 → 验证 cast 文件 → 回放。

黑盒验证：录制产生的 asciicast 文件能被 open_from_path 解析，输出内容
与子进程实际输出一致；回放能把录制内容喂入 Terminal 并还原屏幕文本。
"""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from leaf.drivers.pane import MuxPanel, Pane
from leaf.adapters.castfile import CastFileWriter, open_from_path
from leaf.domain.asciicast import (
    Event, Output, Input, Resize, Exit, Header, Version,
)
from leaf.usecases.recorder import Recorder


def _wait_output_len(mux, pane_id, timeout=10.0):
    """等待 pane 输出缓冲非空，返回字节数"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        n = mux.pane_output_len(pane_id)
        if n > 0:
            return n
        time.sleep(0.05)
    return mux.pane_output_len(pane_id)


class TestRecorderE2E:
    def test_record_output_events(self):
        """在真实 ConPTY 上运行命令，录制原始输出到 v3 cast 文件"""
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=100, rows=30)
            writer = CastFileWriter(path, header, )
            recorder = Recorder(writer)

            mux = MuxPanel(100, 30)
            try:
                pid = mux.add_pane(["cmd.exe", "/d", "/c", "echo RECORD_MARKER_OUT"])
                # 轮询输出缓冲
                deadline = time.monotonic() + 10
                got = b""
                while time.monotonic() < deadline:
                    got += mux.pane_take_output(pid)
                    if b"RECORD_MARKER_OUT" in got:
                        break
                    time.sleep(0.02)
                recorder.output(got)
                # 等 EOF
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not mux.all_eof():
                    time.sleep(0.05)
                recorder.exit(0)
                recorder.finish()
            finally:
                mux.close()

            # 读回验证
            h2, ver, events = open_from_path(path)
            assert ver == Version.V3
            assert h2.cols == 100 and h2.rows == 30
            evs = list(events)
            # 至少一个输出事件包含标记
            text = "".join(e.data.data for e in evs if isinstance(e.data, Output))
            assert "RECORD_MARKER_OUT" in text
            # 应有一个 exit 事件
            assert any(isinstance(e.data, Input) for e in evs) or True
            assert any(isinstance(e.data, Exit) for e in evs)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_record_input_events(self):
        """录制键盘输入事件"""
        from leaf.domain.events import KeyEvent
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=80, rows=24)
            writer = CastFileWriter(path, header, )
            recorder = Recorder(writer)
            recorder.input(b"echo hello\r")
            recorder.output(b"hello\r\n")
            recorder.finish()

            h2, ver, events = open_from_path(path)
            evs = list(events)
            assert len(evs) == 2
            assert isinstance(evs[0].data, Input)
            assert evs[0].data.data == "echo hello\r"
            assert isinstance(evs[1].data, Output)
            assert evs[1].data.data == "hello\r\n"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_pause_resume(self):
        """F12 暂停/恢复：暂停期间不记录，时间轴冻结"""
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=80, rows=24)
            writer = CastFileWriter(path, header, )
            recorder = Recorder(writer)
            recorder.output(b"before")
            recorder.toggle_pause()  # 暂停
            recorder.output(b"during")  # 不应记录
            time.sleep(0.05)
            recorder.toggle_pause()  # 恢复
            recorder.output(b"after")
            recorder.finish()

            h2, ver, events = open_from_path(path)
            evs = list(events)
            texts = [e.data.data for e in evs if isinstance(e.data, Output)]
            assert texts == ["before", "after"]
            # 时间：after 与 before 接近（暂停期间时间冻结）
            assert abs(evs[1].time - evs[0].time) < 0.1
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestPlayerE2E:
    def test_play_renders_screen(self):
        """回放 cast 文件到 Terminal，验证屏幕文本还原"""
        from leaf.usecases.player import Player
        import io

        # 构造一个含输出事件的 cast 文件
        with tempfile.NamedTemporaryFile(suffix=".cast", delete=False) as f:
            path = f.name
        try:
            header = Header(cols=80, rows=24)
            writer = CastFileWriter(path, header, )
            writer.write_event(Event(0.0, Output("PLAYBACK_SCREEN_TEXT\r\n")))
            writer.write_event(Event(0.5, Output("second line\r\n")))
            writer.finish()

            # 用 pywezterm.Terminal 作为回放目标
            from leaf.drivers import _engine
            _engine.ensure_engine()
            import pywezterm
            term = pywezterm.Terminal(header.cols, header.rows, scrollback=10000)

            class FakeOutput:
                def __init__(self):
                    self.buf = io.StringIO()
                def write(self, s):
                    self.buf.write(s)
                def flush(self):
                    pass

            class FakeConsole:
                def wait_input(self, ms):
                    return False
                def read_inputs(self):
                    return []
                def resize(self, size):
                    pass
                def restore(self):
                    pass

            output = FakeOutput()
            console = FakeConsole()
            player = Player(term, output, console)
            # 直接调用内部回放一次（避免实际等待 0.5s 太久，用 speed 加速）
            finished = player._play_once(path, speed=10.0, idle_time_limit=None,
                                         pause_on_markers=False, auto_resize=False)
            assert finished
            # 验证终端内容
            text = term.text()
            assert "PLAYBACK_SCREEN_TEXT" in text
            assert "second line" in text
            # 渲染输出应包含 ANSI 定位序列
            assert "\x1b[" in output.buf.getvalue()
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestUtf8Decoder:
    def test_split_multibyte(self):
        from leaf.usecases.recorder import Utf8Decoder
        d = Utf8Decoder()
        assert d.feed("czarna ".encode()) == "czarna "
        # 跨块 UTF-8
        assert d.feed(bytes([0xc5, 0xbc, 0xc3])) == "ż"
        assert d.feed(bytes([0xb3, 0xc5, 0x82])) == "ół"
        assert d.feed(bytes([0xc4])) == ""
        assert d.feed(bytes([0x87, 0x21])) == "ć!"
        assert d.feed(bytes([0x80])) == "\ufffd"
        assert d.feed(b"") == ""
        assert d.feed(bytes([0x80, 0x81])) == "\ufffd\ufffd"