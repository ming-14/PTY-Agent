"""grep 用例单元测试 —— rg 引擎（mock）与降级引擎（真实文件）"""

import json
import os

import pytest

from src.files.search import grep
from src.files.search.grep import (
    GrepMatch,
    grep_files,
)


class TestGrepFallbackEngine:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", None)

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
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", "rg.exe")

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 0
                stdout = "\n".join([
                    json.dumps({"type": "begin", "data": {"path": {"text": "C:\\a\\x.txt"}}}),
                    json.dumps({"type": "match", "data": {
                        "path": {"text": "C:\\a\\x.txt"},
                        "line_number": 2,
                        "lines": {"text": "needle\n"},
                    }}),
                    json.dumps({"type": "match", "data": {
                        "path": {"text": "C:\\a\\x.txt"},
                        "line_number": 5,
                        "lines": {"text": "needle again\n"},
                    }}),
                    json.dumps({"type": "end", "data": {"path": {"text": "C:\\a\\x.txt"}}}),
                ])
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

    def test_rg_engine_content_with_colon_not_dropped(self, tmp_path, monkeypatch):
        """回归：匹配行内容含冒号（如 def f(): pass）不得被丢弃"""
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", "rg.exe")

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 0
                stdout = json.dumps({"type": "match", "data": {
                    "path": {"text": "C:\\a\\mod.py"},
                    "line_number": 2,
                    "lines": {"text": "def helper(): pass\n"},
                }})
                stderr = ""
            return _P()
        monkeypatch.setattr(grep.subprocess, "run", _run)
        result = grep_files("def", str(tmp_path))
        assert len(result.matches) == 1
        assert result.matches[0].content == "def helper(): pass"

    def test_rg_command_contains_glob_and_escape(self, tmp_path, monkeypatch):
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", "rg.exe")
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
        assert "--json" in captured["cmd"]
        assert "--glob" in captured["cmd"]
        assert "*.py" in captured["cmd"]
        assert "a\\.b" in captured["cmd"]  # literal 转义

    def test_rg_command_applies_ignored_dirs_globs(self, tmp_path, monkeypatch):
        """回归：rg 引擎必须以排除 glob 应用忽略清单（对齐降级引擎）"""
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", "rg.exe")
        captured = {}

        def _run(cmd, capture_output=None, encoding=None, errors=None):
            captured["cmd"] = cmd
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            return _P()
        monkeypatch.setattr(grep.subprocess, "run", _run)
        grep_files("needle", str(tmp_path))
        ignore_globs = [c for c in captured["cmd"] if c.startswith("!**/")]
        assert "!**/node_modules/**" in ignore_globs
        assert "!**/vendor/**" in ignore_globs

    def test_rg_error_falls_back(self, tmp_path, monkeypatch):
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", "rg.exe")

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
        import src.files.settings as _s
        monkeypatch.setattr(_s.settings, "rg_exe", None)
        (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
        result = grep_files("needle", str(tmp_path))
        assert result.engine == "fallback"