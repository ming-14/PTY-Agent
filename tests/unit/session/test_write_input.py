"""session write_input 控制序列分段停顿单元测试

覆盖 {esc}:wq{enter} 场景：pause_offsets 触发分段写入，
段间停顿 50ms，保证控制序列与后续字节分隔（防终端组合键误解析）。
"""

import time

from src.session.session import Session


class _FakePty:
    """记录写入字节的假 PTY"""

    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


def _make_session(sid="write-input-test"):
    s = Session(sid, "vim")
    s._pty = _FakePty()
    s.running = True
    return s


class TestWriteInputPauseOffsets:
    def test_no_pause_offsets_single_write(self):
        """无停顿偏移时一次写入完整数据"""
        s = _make_session()
        s.write_input("hello\r")
        assert len(s._pty.writes) == 1
        assert s._pty.writes[0] == "hello\r"

    def test_pause_offsets_split_segments(self):
        """停顿偏移把输入切分为多段，完整数据不变、末尾偏移规整跳过"""
        s = _make_session()
        s.write_input("\x1b:wq\r", pause_offsets=[1, 5])
        joined = "".join(s._pty.writes)
        assert joined == "\x1b:wq\r"
        assert len(s._pty.writes) == 2

    def test_segment_pause_interval(self):
        """段间有停顿（模拟按键间隔）"""
        s = _make_session()
        start = time.monotonic()
        s.write_input("\x1b:wq\r", pause_offsets=[1, 5])
        elapsed = time.monotonic() - start
        # 1 个有效间隔 × 50ms（末尾偏移 5==len(data) 规整跳过）
        assert elapsed >= 0.04

    def test_offset_normalization(self):
        """乱序/越界偏移自动规整，不影响正确切分"""
        s = _make_session()
        s.write_input("\x1b:wq\r", pause_offsets=[5, 99, 1, -3])
        joined = "".join(s._pty.writes)
        assert joined == "\x1b:wq\r"

    def test_bytes_data_ignores_offsets(self):
        """bytes 输入忽略 pause_offsets（单次写入）"""
        s = _make_session()
        s.write_input(b"\x1b:wq\r", pause_offsets=[1, 5])
        assert len(s._pty.writes) == 1
        assert s._pty.writes[0] == b"\x1b:wq\r"

    def test_pause_offsets_empty_list_single_write(self):
        """空偏移列表等价于无停顿，一次写入"""
        s = _make_session()
        s.write_input("abc", pause_offsets=[])
        assert len(s._pty.writes) == 1
        assert s._pty.writes[0] == "abc"