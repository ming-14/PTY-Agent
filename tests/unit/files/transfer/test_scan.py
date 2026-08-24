"""传输目录扫描单元测试 —— 单文件/目录树/上限"""

import os

import pytest

from src.config.transfer import TRANSFER_MAX_FILES
from src.client.transfer.common import ENTRY_DIR, ENTRY_FILE
from src.client.transfer.scan import scan_tree


class TestScanTree:
    def test_single_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_bytes(b"hello")
        entries = scan_tree(str(f))
        assert len(entries) == 1
        assert entries[0]["relpath"] == ""
        assert entries[0]["kind"] == ENTRY_FILE
        assert entries[0]["size"] == 5
        assert entries[0]["mtime"] > 0

    def test_dir_tree_with_empty_dir(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "nested").mkdir()
        (tmp_path / "sub" / "a.txt").write_bytes(b"x")
        (tmp_path / "b.log").write_bytes(b"yy")
        entries = scan_tree(str(tmp_path))
        kinds = {e["relpath"]: e["kind"] for e in entries}
        assert kinds[""] == ENTRY_DIR
        assert kinds["sub"] == ENTRY_DIR
        assert kinds["sub/nested"] == ENTRY_DIR
        assert kinds["sub/a.txt"] == ENTRY_FILE
        assert kinds["b.log"] == ENTRY_FILE
        # 全量不过滤：隐藏文件/目录也包含
        (tmp_path / ".hidden").write_bytes(b"h")
        (tmp_path / ".git").mkdir()
        entries = scan_tree(str(tmp_path))
        rels = {e["relpath"] for e in entries}
        assert ".hidden" in rels
        assert ".git" in rels

    def test_file_size_and_mtime(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"12345")
        st = os.stat(str(f))
        entries = scan_tree(str(f))
        assert entries[0]["size"] == st.st_size
        assert entries[0]["mtime"] == st.st_mtime

    def test_missing_path_raises(self, tmp_path):
        with pytest.raises(Exception):
            scan_tree(str(tmp_path / "nope"))

    def test_max_entries_guard(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.client.transfer.scan.TRANSFER_MAX_FILES", 3)
        for i in range(10):
            (tmp_path / ("f%d.txt" % i)).write_bytes(b"x")
        with pytest.raises(Exception, match="too many entries"):
            scan_tree(str(tmp_path))