"""diff 用例单元测试 —— generate_diff 文本与 +/- 统计"""

from src.files.diff import generate_diff


class TestGenerateDiff:
    def test_identical_content_returns_empty(self):
        text, add, rem = generate_diff("a\nb\n", "a\nb\n", "x/y.txt")
        assert text == ""
        assert add == 0
        assert rem == 0

    def test_header_format(self):
        text, _, _ = generate_diff("one\n", "one\ntwo\n", "C:/proj/a.txt")
        assert "--- a/C:/proj/a.txt" in text
        assert "+++ b/C:/proj/a.txt" in text

    def test_count_additions_and_removals(self):
        text, add, rem = generate_diff("a\nb\nc\n", "a\nB\nc\nd\n", "f.txt")
        assert add == 2  # B 替换 b 计 -1/+1，追加 d 计 +1
        assert rem == 1

    def test_no_trailing_newline(self):
        text, add, rem = generate_diff("a", "a\nb", "f.txt")
        assert add == 1
        assert rem == 0
        # difflib 对无尾部换行的行输出粘连标记（无 "\ No newline" 标记），
        # 统计已走 opcodes，不受影响
        assert "-a+a" in text