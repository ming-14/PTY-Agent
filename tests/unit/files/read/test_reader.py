"""src/files/read/reader.py 单元测试 — file read 读取用例"""

import os
import pytest

from src.files.read.reader import (
    read_file,
    is_image_file,
    suggest_similar,
    ReadResult,
)
from src.files.errors import FileToolError


@pytest.fixture
def tmp_text(tmp_path):
    """生成多行测试文件: line0..line4"""
    p = tmp_path / "sample.txt"
    p.write_text("\n".join("line%d" % i for i in range(5)) + "\n", encoding="utf-8")
    return str(p)


class TestReadFile:
    def test_basic_read(self, tmp_text):
        result = read_file(tmp_text)
        assert isinstance(result, ReadResult)
        assert result.total_lines == 5
        assert result.truncated is False
        assert "     0|line0" in result.content
        assert "     4|line4" in result.content

    def test_offset(self, tmp_text):
        result = read_file(tmp_text, offset=2, limit=2)
        assert "     2|line2" in result.content
        assert "     3|line3" in result.content
        assert "line0" not in result.content

    def test_truncated_flag_and_hint(self, tmp_text):
        result = read_file(tmp_text, offset=0, limit=3)
        assert result.truncated is True
        assert "File has more lines" in result.content

    def test_negative_offset_clamped(self, tmp_text):
        result = read_file(tmp_text, offset=-5)
        assert "     0|line0" in result.content

    def test_large_line_number_width(self, tmp_path):
        # 100010 行短内容，让 100000 行号触发宽格式分支且不超大小上限
        p = tmp_path / "many.txt"
        p.write_bytes(b"\n".join(b"x" for _ in range(100010)))
        result = read_file(p, offset=100000, limit=1)
        assert "100000|x" in result.content

    def test_long_line_truncated(self, tmp_path):
        p = tmp_path / "long.txt"
        p.write_text("A" * 5000 + "\n", encoding="utf-8")
        result = read_file(p, limit=10)
        assert "..." in result.content
        assert len(result.content.strip()) < 2010

    def test_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_file(str(tmp_path / "missing.txt"))

    def test_directory_rejected(self, tmp_path):
        with pytest.raises(FileToolError):
            read_file(str(tmp_path))

    def test_image_rejected(self, tmp_path):
        p = tmp_path / "a.png"
        p.write_bytes(b"fake")
        with pytest.raises(FileToolError):
            read_file(str(p))

    def test_too_large(self, tmp_path):
        from src.files.settings import settings
        p = tmp_path / "big.txt"
        p.write_bytes(b"x" * (settings.max_read_size + 1))
        with pytest.raises(FileToolError):
            read_file(str(p))

    def test_non_utf8(self, tmp_path):
        p = tmp_path / "gbk.txt"
        p.write_bytes("中文内容".encode("gbk"))
        with pytest.raises(UnicodeDecodeError):
            read_file(str(p))

    def test_crlf_stripped(self, tmp_path):
        p = tmp_path / "crlf.txt"
        p.write_text("a\r\nb\r\n", encoding="utf-8")
        result = read_file(str(p))
        assert "\r" not in result.content


class TestIsImageFile:
    @pytest.mark.parametrize("name", ["a.jpg", "b.JPG", "c.png", "d.svg", "e.webp"])
    def test_image(self, name):
        assert is_image_file("/x/" + name) is True

    @pytest.mark.parametrize("name", ["a.txt", "b.py", "c.md"])
    def test_not_image(self, name):
        assert is_image_file("/x/" + name) is False


class TestSuggestSimilar:
    def test_suggests_within_dir(self, tmp_path):
        (tmp_path / "user_info.py").write_text("", encoding="utf-8")
        (tmp_path / "user_info_old.py").write_text("", encoding="utf-8")
        (tmp_path / "other.py").write_text("", encoding="utf-8")
        p = str(tmp_path / "user_info")
        suggestions = suggest_similar(p)
        assert any("user_info.py" in s for s in suggestions)
        assert any("user_info_old.py" in s for s in suggestions)
        assert len(suggestions) <= 3

    def test_no_suggestions(self, tmp_path):
        (tmp_path / "only.py").write_text("", encoding="utf-8")
        assert suggest_similar(str(tmp_path / "zzz")) == []

    def test_missing_dir(self, tmp_path):
        assert suggest_similar(str(tmp_path / "no" / "such" / "dir")) == []

    def test_typo_suggestion(self, tmp_path):
        # 形近但非互为子串（hello_wrld vs hello_world）：
        # difflib 相似度匹配应触发建议（子串包含判定不触发）
        (tmp_path / "hello_world.py").write_text("", encoding="utf-8")
        suggestions = suggest_similar(str(tmp_path / "hello_wrld.py"))
        assert any("hello_world.py" in s for s in suggestions)