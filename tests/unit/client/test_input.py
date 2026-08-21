"""src/input/text.py 输入文本处理单元测试"""

import pytest

from src.input.text import (
    unescape_json_string,
    process_input,
    expand_control_characters,
    _expand_control_token,
)
from src.client.input import safe_print


class TestUnescapeJsonString:
    def test_no_backslash(self):
        assert unescape_json_string("hello") == "hello"

    def test_newline_escape(self):
        assert unescape_json_string("line1\\nline2") == "line1\nline2"

    def test_tab_escape(self):
        assert unescape_json_string("col1\\tcol2") == "col1\tcol2"

    def test_unicode_escape(self):
        result = unescape_json_string("\\u0041")
        assert result == "A"

    def test_invalid_json_escape(self):
        text = "C:\\Users\\rikka"
        result = unescape_json_string(text)
        assert result == text

    def test_raw_quotes_no_break_unescape(self):
        """含裸双引号的文本也能整体解码，不被引号破坏 JSON 解析"""
        result = unescape_json_string('import time\\nprint("line1")')
        assert result == 'import time\nprint("line1")'

    def test_empty_string(self):
        assert unescape_json_string("") == ""


class TestProcessInput:
    def test_raw_mode_no_escaping(self):
        result, pauses = process_input("C:\\Users", json_escaping=False)
        assert "C:\\Users" in result
        assert pauses == []

    def test_json_escaping_mode(self):
        result, pauses = process_input("line1\\nline2", json_escaping=True)
        assert result == "line1\nline2\r"
        assert pauses == []

    def test_default_eol_lf(self):
        result, _ = process_input("hello", send_eol="\n")
        assert result.endswith("\n")

    def test_eol_cr(self):
        result, _ = process_input("hello", send_eol="\r")
        assert result.endswith("\r")

    def test_eol_crlf(self):
        result, _ = process_input("hello", send_eol="\r\n")
        assert result.endswith("\r\n")

    def test_eol_none(self):
        result, _ = process_input("hello", send_eol="")
        assert result == "hello"

    def test_no_duplicate_eol(self):
        result, _ = process_input("hello\n", send_eol="\n")
        assert result == "hello\n"

    def test_no_duplicate_eol_cr(self):
        result, _ = process_input("hello\r", send_eol="\r")
        assert result == "hello\r"

    def test_pause_offsets_single_token(self):
        """单个控制序列 token 返回其结束偏移"""
        result, pauses = process_input("{esc}", json_escaping=True, send_eol="")
        assert result == "\x1b"
        assert pauses == [1]

    def test_pause_offsets_multiple_tokens(self):
        """多个 token 各自返回结束偏移（含中间普通文本）"""
        result, pauses = process_input(
            "{esc}:wq{enter}", json_escaping=True, send_eol=""
        )
        assert result == "\x1b:wq\r"
        assert pauses == [1, 5]

    def test_pause_offsets_raw_mode_empty(self):
        """raw 模式（json_escaping=False）不产生停顿偏移"""
        result, pauses = process_input("{esc}:wq{enter}", json_escaping=False, send_eol="")
        assert pauses == []
        assert result == "{esc}:wq{enter}"


class TestSafePrint:
    def test_print_ascii(self, capsys):
        safe_print("hello world")
        captured = capsys.readouterr()
        assert "hello world" in captured.out

    def test_print_unicode(self, capsys):
        safe_print("你好世界")
        captured = capsys.readouterr()
        assert "你好" in captured.out or len(captured.out) > 0


class TestExpandControlCharacters:
    def test_literal_braces(self):
        assert expand_control_characters("`{") == "{"
        assert expand_control_characters("`}") == "}"
        assert expand_control_characters("a`{b`}c") == "a{b}c"

    def test_unmatched_brace_left_literal(self):
        assert expand_control_characters("{ctrl+a") == "{ctrl+a"

    def test_ctrl_letter(self):
        assert expand_control_characters("{ctrl+a}") == "\x01"
        assert expand_control_characters("{CTRL+A}") == "\x01"
        assert expand_control_characters("{ctrl+c}") == "\x03"
        assert expand_control_characters("{ctrl+z}") == "\x1a"

    def test_ctrl_alt_shift_letter(self):
        assert expand_control_characters("{ctrl+alt+s}") == "\x1b\x13"
        assert expand_control_characters("{ctrl+alt+shift+s}") == "\x1b\x13"

    def test_alt_letter(self):
        assert expand_control_characters("{alt+a}") == "\x1ba"
        assert expand_control_characters("{alt+shift+b}") == "\x1bB"

    def test_special_keys(self):
        assert expand_control_characters("{enter}") == "\r"
        assert expand_control_characters("{ENTER}") == "\r"
        assert expand_control_characters("{tab}") == "\t"
        assert expand_control_characters("{esc}") == "\x1b"
        assert expand_control_characters("{escape}") == "\x1b"
        assert expand_control_characters("{backspace}") == "\x7f"
        assert expand_control_characters("{bs}") == "\x7f"
        assert expand_control_characters("{backtab}") == "\x1b[Z"
        assert expand_control_characters("{space}") == "\x20"
        assert expand_control_characters("{up}") == "\x1b[A"
        assert expand_control_characters("{down}") == "\x1b[B"
        assert expand_control_characters("{left}") == "\x1b[D"
        assert expand_control_characters("{right}") == "\x1b[C"
        assert expand_control_characters("{home}") == "\x1b[1~"
        assert expand_control_characters("{end}") == "\x1b[4~"
        assert expand_control_characters("{pageup}") == "\x1b[5~"
        assert expand_control_characters("{pagedown}") == "\x1b[6~"
        assert expand_control_characters("{insert}") == "\x1b[2~"
        assert expand_control_characters("{delete}") == "\x1b[3~"

    def test_function_keys(self):
        assert expand_control_characters("{f1}") == "\x1bOP"
        assert expand_control_characters("{f4}") == "\x1bOS"
        assert expand_control_characters("{f5}") == "\x1b[15~"
        assert expand_control_characters("{f12}") == "\x1b[24~"

    def test_mixed_text(self):
        assert expand_control_characters("abc{ctrl+c}def") == "abc\x03def"
        assert expand_control_characters("{up}{enter}{down}") == "\x1b[A\r\x1b[B"

    def test_unknown_token_raises(self):
        with pytest.raises(ValueError, match="无法识别的转义序列"):
            expand_control_characters("{foo}")

    def test_bare_letter_raises(self):
        with pytest.raises(ValueError, match="无法识别的转义序列"):
            expand_control_characters("{b}")

    def test_bare_letter_in_text_raises(self):
        with pytest.raises(ValueError, match="无法识别的转义序列"):
            expand_control_characters("a{b}c")

    def test_empty_braces_raises(self):
        with pytest.raises(ValueError, match="无法识别的转义序列"):
            expand_control_characters("{}")

    def test_bare_letter_hint_backtick(self):
        with pytest.raises(ValueError, match="`\\{"):
            expand_control_characters("a{b}c")


class TestProcessInputControlEscaping:
    def test_json_and_control_sequence(self):
        result, pauses = process_input("line1\\n{enter}", json_escaping=True, send_eol="")
        assert result == "line1\n\r"
        assert pauses == [7]

    def test_control_no_duplicate_eol(self):
        result, pauses = process_input("{enter}", json_escaping=True, send_eol="\r")
        assert result == "\r"
        assert pauses == [1]

    def test_raw_mode_preserves_control_syntax(self):
        result, pauses = process_input("{ctrl+a}", json_escaping=False, send_eol="")
        assert result == "{ctrl+a}"
        assert pauses == []
