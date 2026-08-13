"""glob 用例单元测试 —— rg 引擎（mock）与降级引擎（真实文件）"""

import os

import pytest

from src.files.search import glob_
from src.files.search.glob_ import glob_files


class TestGlobFallbackEngine:
    @pytest.fixture(autouse=True)
    def force_fallback(self, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", None)

    def _make_tree(self, tmp_path):
        (tmp_path / "top.py").write_text("", encoding="utf-8")
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "mod.py").write_text("", encoding="utf-8")
        (sub / "data.txt").write_text("", encoding="utf-8")
        deep = sub / "deep"
        deep.mkdir()
        (deep / "inner.go").write_text("", encoding="utf-8")
        return tmp_path

    def test_recursive_star(self, tmp_path):
        self._make_tree(tmp_path)
        result = glob_files("*.py", str(tmp_path))
        # fnmatch 的 * 匹配含 / 的路径：任意深度（与 rg --glob gitignore 语义一致）
        assert set(result.files) == {str(tmp_path / "top.py"), str(tmp_path / "src" / "mod.py")}

    def test_relative_path_pattern(self, tmp_path):
        self._make_tree(tmp_path)
        result = glob_files("src/data.txt", str(tmp_path))
        assert result.files == [str(tmp_path / "src" / "data.txt")]

    def test_double_star_abs(self, tmp_path):
        self._make_tree(tmp_path)
        result = glob_files("src/**/*.py", str(tmp_path))
        assert result.files == [str(tmp_path / "src" / "mod.py")]

    def test_skips_hidden_and_ignored_dirs(self, tmp_path):
        (tmp_path / "visible.py").write_text("", encoding="utf-8")
        (tmp_path / ".hidden.py").write_text("", encoding="utf-8")
        sub = tmp_path / "node_modules"
        sub.mkdir()
        (sub / "x.py").write_text("", encoding="utf-8")
        result = glob_files("*.py", str(tmp_path))
        assert result.files == [str(tmp_path / "visible.py")]

    def test_truncated(self, tmp_path):
        for i in range(150):
            (tmp_path / ("f%03d.txt" % i)).write_text("", encoding="utf-8")
        result = glob_files("*.txt", str(tmp_path))
        assert result.truncated is True
        assert len(result.files) <= 100

    def test_sorted_by_mtime_newest_first(self, tmp_path):
        (tmp_path / "old.txt").write_text("", encoding="utf-8")
        (tmp_path / "new.txt").write_text("", encoding="utf-8")
        os.utime(str(tmp_path / "old.txt"), (os.path.getmtime(str(tmp_path / "old.txt")) - 100,) * 2)
        os.utime(str(tmp_path / "new.txt"), (os.path.getmtime(str(tmp_path / "new.txt")) + 100,) * 2)
        result = glob_files("*.txt", str(tmp_path))
        assert result.files[0] == str(tmp_path / "new.txt")

    def test_no_match(self, tmp_path):
        (tmp_path / "a.txt").write_text("", encoding="utf-8")
        result = glob_files("*.zzz", str(tmp_path))
        assert result.files == []
        assert result.truncated is False


class TestGlobRgEngine:
    @pytest.fixture
    def mock_rg(self, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", "rg.exe")

        def _run(cmd, cwd=None, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 0
                # rg --files --null 输出相对 cwd 的路径，\x00 分隔
                stdout = "src/mod.py\x00src/deep/inner.go\x00top.py\x00"
                stderr = ""
            return _P()
        monkeypatch.setattr(glob_.subprocess, "run", _run)

    def test_joins_absolute_paths(self, tmp_path, mock_rg):
        result = glob_files("*.py", str(tmp_path))
        assert result.engine == "rg"
        assert set(result.files) == {
            os.path.normpath(str(tmp_path / "src" / "mod.py")),
            os.path.normpath(str(tmp_path / "src" / "deep" / "inner.go")),
            os.path.normpath(str(tmp_path / "top.py")),
        }

    def test_rg_command_contains_glob(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", "rg.exe")
        captured = {}

        def _run(cmd, cwd=None, capture_output=None, encoding=None, errors=None):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            class _P:
                returncode = 0
                stdout = ""
                stderr = ""
            return _P()
        monkeypatch.setattr(glob_.subprocess, "run", _run)
        glob_files("*.py", str(tmp_path))
        assert captured["cmd"] == ["rg.exe", "--files", "-L", "--null", "--glob", "*.py"]
        assert captured["cwd"] == str(tmp_path)

    def test_rg_error_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", "rg.exe")

        def _run(cmd, cwd=None, capture_output=None, encoding=None, errors=None):
            class _P:
                returncode = 2
                stdout = ""
                stderr = "error"
            return _P()
        monkeypatch.setattr(glob_.subprocess, "run", _run)
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        result = glob_files("*.py", str(tmp_path))
        assert result.engine == "fallback"
        assert result.files == [str(tmp_path / "a.py")]

    def test_rg_missing_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.files.search.glob_.RG_EXE", None)
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        result = glob_files("*.py", str(tmp_path))
        assert result.engine == "fallback"