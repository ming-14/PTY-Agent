"""OutputBuffer 单元测试（文本行级）

测试线程安全文本行级输出缓冲区：追加、裁剪、读取、游标、线程安全。
"""

import threading

from src.session.output.buffer import OutputBuffer


class TestOutputBufferAppend:
    """OutputBuffer 文本追加测试"""

    def test_append_text_stored(self):
        buf = OutputBuffer()
        buf.append_text("hello")
        assert buf.get_all_lines() == ["hello"]
        assert buf.get_full() == "hello"

    def test_append_multiple_lines(self):
        buf = OutputBuffer()
        buf.append_text("a\nb\n")
        assert buf.get_all_lines() == ["a", "b", ""]
        assert buf.get_full() == "a\nb\n"

    def test_append_partial_then_complete(self):
        """部分行拼接：partial 进入 tail，下一块补全"""
        buf = OutputBuffer()
        buf.append_text("hello")
        assert buf.get_all_lines() == ["hello"]
        buf.append_text(" world\n")
        assert buf.get_all_lines() == ["hello world", ""]
        assert buf.get_full() == "hello world\n"

    def test_append_increments_read_cycle(self):
        buf = OutputBuffer()
        assert buf.read_cycle == 0
        buf.append_text("a")
        assert buf.read_cycle == 1
        buf.append_text("b")
        assert buf.read_cycle == 2

    def test_append_sets_first_output_event(self):
        buf = OutputBuffer()
        assert not buf.first_output_event.is_set()
        buf.append_text("data")
        assert buf.first_output_event.is_set()

    def test_append_empty_noop(self):
        buf = OutputBuffer()
        buf.append_text("")
        assert buf.get_full() == ""
        assert buf.read_cycle == 0


class TestHistoryVisible:
    """滚动历史 + 可见屏幕（pty 模式）"""

    def test_append_history_and_replace_visible(self):
        buf = OutputBuffer()
        buf.append_history(["l1", "l2"])
        buf.replace_visible(["v1", "v2"])
        assert buf.get_all_lines() == ["l1", "l2", "v1", "v2"]
        assert buf.get_full() == "l1\nl2\nv1\nv2"
        assert buf.get_visible_lines() == ["v1", "v2"]
        assert buf.get_visible() == "v1\nv2"

    def test_replace_visible_same_no_cycle(self):
        buf = OutputBuffer()
        buf.replace_visible(["a"])
        c = buf.read_cycle
        buf.replace_visible(["a"])   # 相同 => 不递增
        assert buf.read_cycle == c

    def test_replace_visible_changed_cycles(self):
        buf = OutputBuffer()
        c = buf.read_cycle
        buf.replace_visible(["a", "b"])
        assert buf.read_cycle > c

    def test_line_count(self):
        buf = OutputBuffer()
        buf.append_history(["a", "b"])
        assert buf.line_count == 2
        buf.replace_visible(["c"])
        assert buf.line_count == 3


class TestCursor:
    """游标增量语义"""

    def test_since_cursor(self):
        buf = OutputBuffer()
        buf.append_text("one\ntwo\n")
        buf.mark_cursor()
        buf.append_text("three\n")
        assert buf.get_since_cursor() == "three\n"

    def test_reset_cursor_full(self):
        buf = OutputBuffer()
        buf.append_text("one\n")
        buf.mark_cursor()
        buf.reset_cursor()
        assert buf.get_since_cursor() == "one\n"

    def test_cursor_with_visible(self):
        buf = OutputBuffer()
        buf.append_history(["h1"])
        buf.replace_visible(["v1"])
        buf.mark_cursor()
        # 之后可见区更新也计入增量
        buf.replace_visible(["v2"])
        since = buf.get_since_cursor()
        assert "v2" in since


class TestOutputBufferTrim:
    """超限裁剪"""

    def test_line_trim(self):
        buf = OutputBuffer(max_lines=3, max_chars=10000)
        buf.append_text("a\nb\nc\nd\ne\n")
        lines = buf.get_all_lines()
        assert len(lines) <= 3
        assert "a" not in lines

    def test_char_trim(self):
        buf = OutputBuffer(max_lines=1000, max_chars=10)
        buf.append_text("x" * 8 + "\n" + "y" * 8 + "\n")
        # 头部被裁剪，游标跟随
        assert len(buf.get_all_lines()[0]) <= 10
        assert buf.cursor == 0


class TestOutputBufferProperties:
    """属性测试"""

    def test_lock_returns_rlock(self):
        buf = OutputBuffer()
        assert isinstance(buf.lock, type(threading.RLock()))

    def test_raw_returns_mapping(self):
        buf = OutputBuffer()
        buf.append_text("hi")
        raw = buf.raw
        assert "history" in raw and "visible" in raw


class TestOutputBufferConcurrency:
    """并发安全"""

    def test_concurrent_appends(self):
        buf = OutputBuffer(max_lines=100000)
        n_threads = 5
        n_appends = 100

        def worker():
            for _ in range(n_appends):
                buf.append_text("x\n")

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert buf.line_count > 0
