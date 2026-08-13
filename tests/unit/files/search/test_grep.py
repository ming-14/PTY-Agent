"""grep 用例单元测试 —— rg 引擎（mock）与降级引擎（真实文件）"""

import os

import pytest

from src.files.search import grep
from src.files.search.grep import (
    GrepMatch,
    _parse_match,
    grep_files,
)


class TestParseMatch:
    def test_windows_path_with_colons(self):
        # Windows 盘符冒号从右侧解析不会干扰
        m = _parse_match("C:\\proj\\a.txt:42:hello world")
        assert m is not None
        assert m.path == "C:\\proj\\a.txt"
        assert m.line_number == 42
        assert m.content == "hello world"

    def test_unix_path(self):
        m = _parse_match("/x/y/b.py:3:def f")
        assert m.path == "/x/y/b.py"
        assert m.line_number == 3
        assert m.content == "def f"

    def test_malformed_lines_skipped(self):
        assert _parse_match("no-colon-here") is None
        assert _parse_match("path:not-a-number:content") is None


class TestGrepFallbackEngine:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", None)

    def test_basic_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("line1 foo\nline2\n", encoding="utf-8")
        result = grep_files("foo", str(tmp_path))
        assert len(result.matches) == 1
        assert result.matches[0].path == str(tmp_path / "a.txt")
        assert result.matches[0].line_number == 1
        assert result.engine == "fallback"

    def test_literal_text(self, tmp_path):
        (tmp_path / "a.txt").write_text("a.b c\n", encoding="utf-8")
        assert len(grep_files("a.b", str(tmp_path)).matches) == 1
        assert len(grep_files("a.b", str(tmp_path), literal_text=True).matches) == 1
        assert len(grep_files("ab", str(tmp_path), literal_text=True).matches) == 0

    def test_skips_hidden_and_ignored_dirs(self, tmp_path):
        (tmp_path / "visible.txt").write_text("key\n", encoding="utf-8")
        (tmp_path / ".hidden.txt").write_text("key\n", encoding="utf-8")
        sub = tmp_path / "node_modules"
        sub.mkdir()
        (sub / "x.txt").write_text("key\n", encoding="utf-8")
        result = grep_files("key", str(tmp_path))
        paths = [m.path for m in result.matches]
        assert paths == [str(tmp_path / "visible.txt")]

    def test_include_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("key\n", encoding="utf-8")
        (tmp_path / "a.txt").write_text("key\n", encoding="utf-8")
        assert len(grep_files("key", str(tmp_path), include="*.py").matches) == 1

    def test_recursive(self, tmp_path):
        sub = tmp_path / "deep" / "deeper"
        sub.mkdir(parents=True)
        (sub / "x.py").write_text("key\n", encoding="utf-8")
        assert len(grep_files("key", str(tmp_path)).matches) == 1

    def test_truncated(self, tmp_path):
        for i in range(150):
            (tmp_path / ("f%03d.txt" % i)).write_text("needle\n", encoding="utf-8")
        result = grep_files("needle", str(tmp_path))
        assert result.truncated is True
        assert len(result.matches) <= 100

    def test_no_match(self, tmp_path):
        (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
        result = grep_files("zzz", str(tmp_path))
        assert result.matches == []
        assert result.truncated is False

    def test_sorted_by_mtime_newest_first(self, tmp_path):
        (tmp_path / "old.txt").write_text("key\n", encoding="utf-8")
        (tmp_path / "new.txt").write_text("key\n", encoding="utf-8")
        old_t = os.path.getmtime(str(tmp_path / "old.txt"))
        new_t = os.path.getmtime(str(tmp_path / "new.txt"))
        # 不同 mtime 才能验证排序（无法保证写入顺序 mtime 递增）
        os.utime(str(tmp_path / "old.txt"), (old_t - 100,) * 2)
        os.utime(str(tmp_path / "new.txt"), (new_t + 100,) * 2)
        result = grep_files("key", str(tmp_path))
        assert result.matches[0].path == str(tmp_path / "new.txt")


class TestGrepRgEngine:
    @pytest.fixture
    def mock_rg(self, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", "rg.exe")

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 0
                stdout = "C:\\a\\x.txt:2:needle\nC:\\a\\x.txt:5:needle again\n"
                stderr = ""
            return _P()
        monkeypatch.setattr(grep.subprocess, "run", _run)
        return _run

    def test_rg_engine_parses_and_sorts(self, tmp_path, mock_rg):
        result = grep_files("needle", str(tmp_path))
        assert result.engine == "rg"
        # mock 输出按行序解析（同 mtime 稳定排序）
        assert len(result.matches) == 2
        assert result.matches[0].path == "C:\\a\\x.txt"
        assert result.matches[0].line_number == 2

    def test_rg_command_contains_glob_and_escape(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", "rg.exe")
        captured = {}

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            captured["cmd"] = cmd
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            return _P()
        monkeypatch.setattr(grep.subprocess, "run", _run)
        grep_files("a.b", str(tmp_path), include="*.py", literal_text=True)
        assert "--glob" in captured["cmd"]
        assert "*.py" in captured["cmd"]
        assert "a\\.b" in captured["cmd"]  # literal 转义

    def test_rg_error_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", "rg.exe")

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 2
                stdout = ""
                stderr = "error"
            return _P()
        monkeypatch.setattr(grep.subprocess, "run", _run)
        (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
        result = grep_files("needle", str(tmp_path))
        assert result.engine == "fallback"
        assert len(result.matches) == 1

    def test_rg_missing_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.grep.RG_EXE", None)
        (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
        result = grep_files("needle", str(tmp_path))
        assert result.engine == "fallback"