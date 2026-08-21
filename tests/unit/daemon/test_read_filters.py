"""daemon/filtering.py 过滤工具单元测试 — lines/grep/column 组合语义"""

from src.daemon.filtering import apply_lines_grep, filter_snapshot_lines


class _NullConn:
    """吞掉错误发送的假连接（apply_lines_grep 出错路径用）"""

    def send(self, *a, **k):
        pass


class TestFilterSnapshotLines:
    """PTY 快照行过滤（lines → grep → column 依次作用于同一行列表）"""

    def test_lines_last_n(self):
        out = filter_snapshot_lines("a\nb\nc\nd", 2)
        assert out == "c\nd"

    def test_lines_range(self):
        out = filter_snapshot_lines("a\nb\nc\nd", "2:3")
        assert out == "b\nc"

    def test_grep(self):
        # grep 命中行标注行号（0-based，对齐 snapshot-diff）
        out = filter_snapshot_lines("alpha one\nbeta two\nalpha three", None, None, "alpha")
        assert out == "0:alpha one\n2:alpha three"

    def test_grep_after_lines(self):
        # -l 先行切片、grep 后过滤，行号保留原输出序号
        out = filter_snapshot_lines("a1\nb2\nc3", 2, None, r"\d")
        assert out == "1:b2\n2:c3"

    def test_column_takes_char(self):
        # 第 N 列 = 行内第 N 个字符（1-based，终端格语义）
        out = filter_snapshot_lines("alpha\nbeta", None, 2)
        assert out == "l\ne"

    def test_column_out_of_range_empty(self):
        out = filter_snapshot_lines("ab\ncd", None, 5)
        assert out == "\n"

    def test_all_combined(self):
        # lines → grep → column 依次作用于同一行列表；grep 命中行保留原序号
        out = filter_snapshot_lines(
            "a1 x\nb2 y\nc3 z", 2, column_param=2, grep="^b"
        )
        assert out == "1:2"


class TestApplyLinesGrep:
    """子进程输出行过滤（增量/等待/已结束路径共用）"""

    def test_passthrough_when_no_filters(self):
        assert apply_lines_grep("x\ny", None, None, _NullConn()) == "x\ny"
        assert apply_lines_grep("x\ny", None, None, _NullConn(), column_param=None) == "x\ny"

    def test_column_only(self):
        """仅 --column（无 -l/-g）也必须生效（此前早退分支跳过）"""
        out = apply_lines_grep(
            "alpha one\nbeta two", None, None, _NullConn(), column_param=1
        )
        assert out == "a\nb"

    def test_column_with_grep(self):
        out = apply_lines_grep(
            "alpha one\nbeta two\nalpha three", None, "alpha", _NullConn(), column_param=3
        )
        assert out == "p\np"

    def test_invalid_regex_returns_none_and_sends_error(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.daemon.handlers.utils.Message.send",
            lambda sock, obj, **k: sent.append(obj),
        )
        out = apply_lines_grep("x", None, "[invalid", object())
        assert out is None
        assert sent and sent[0].get("type") == "error"
