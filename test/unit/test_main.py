"""__main__.py CLI 入口单元测试

测试参数解析、配置键转换、引号修复。
"""

import argparse

import pytest

from src.__main__ import (
    _parse_default_key,
    _format_config_key,
    build_parser,
    main,
)


class TestParseDefaultKey:
    """_parse_default_key 测试"""

    def test_hyphen_to_underscore(self):
        assert _parse_default_key("idle-timeout") == "idle_timeout"

    def test_keep_underscore(self):
        assert _parse_default_key("timeout") == "timeout"

    def test_no_hyphen(self):
        assert _parse_default_key("timeout") == "timeout"


class TestFormatConfigKey:
    """_format_config_key 测试"""

    def test_underscore_to_hyphen(self):
        assert _format_config_key("idle_timeout") == "idle-timeout"

    def test_no_underscore(self):
        assert _format_config_key("timeout") == "timeout"


class TestBuildParser:
    """build_parser 测试"""

    def test_parser_created(self):
        """解析器创建成功"""
        parser = build_parser()
        assert parser is not None

    def test_parse_exec(self):
        """解析 exec 子命令"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "echo hello"])
        assert args.subcmd == "exec"
        assert args.id == "test-id"
        assert args.command == "echo hello"

    def test_parse_send(self):
        """解析 send 子命令（输入经 -i 选项给出）"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input text"])
        assert args.subcmd == "send"
        assert args.id == "test-id"
        assert args.input == "input text"

    def test_parse_read(self):
        """解析 read 子命令"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id"])
        assert args.subcmd == "read"
        assert args.id == "test-id"

    def test_parse_list(self):
        """解析 list 子命令"""
        parser = build_parser()
        args = parser.parse_args(["list"])
        assert args.subcmd == "list"

    def test_parse_remove(self):
        """解析 remove 子命令"""
        parser = build_parser()
        args = parser.parse_args(["remove", "test-id"])
        assert args.subcmd == "remove"
        assert args.id == "test-id"

    def test_parse_closewin(self):
        """解析 closewin 子命令"""
        parser = build_parser()
        args = parser.parse_args(["closewin", "test-id", "0x1234"])
        assert args.subcmd == "closewin"
        assert args.id == "test-id"
        assert args.hwnd == 0x1234

    def test_parse_exec_with_trigger(self):
        """解析 exec 带 trigger"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "-t", ">>>"])
        assert args.trigger == ">>>"

    def test_parse_exec_with_timeout(self):
        """解析 exec 带 timeout"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--timeout", "30"])
        assert args.timeout == 30.0

    def test_parse_default_config(self):
        """解析 --default 配置（子命令级别）"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--default", "timeout", "30"])
        assert args.default == ["timeout", "30"]

    def test_parse_show_config(self):
        """解析 --show-config"""
        parser = build_parser()
        args = parser.parse_args(["--show-config"])
        assert args.show_config == ""

    def test_parse_show_config_with_key(self):
        """解析 --show-config timeout"""
        parser = build_parser()
        args = parser.parse_args(["--show-config", "timeout"])
        assert args.show_config == "timeout"

    def test_parse_start(self):
        """解析 start 子命令"""
        parser = build_parser()
        args = parser.parse_args(["start"])
        assert args.subcmd == "start"

    def test_parse_stop(self):
        """解析 stop 子命令"""
        parser = build_parser()
        args = parser.parse_args(["stop"])
        assert args.subcmd == "stop"

    def test_parse_read_with_lines(self):
        """解析 read --lines"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--lines", "10"])
        assert args.lines == "10"

    def test_parse_read_with_grep(self):
        """解析 read --grep"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--grep", "Error"])
        assert args.grep == "Error"

    def test_parse_exec_with_pty(self):
        """解析 exec --pty"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--pty"])
        assert args.pty is True

    def test_parse_exec_with_shell(self):
        """解析 exec --shell"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--shell", "pwsh"])
        assert args.shell == "pwsh"

    def test_parse_exec_with_idle_timeout(self):
        """解析 exec --idle-timeout"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--idle-timeout", "5"])
        assert args.idle_timeout == 5.0

    def test_parse_closewin_decimal_hwnd(self):
        """解析 closewin 十进制 hwnd"""
        parser = build_parser()
        args = parser.parse_args(["closewin", "test-id", "305419896"])
        assert args.hwnd == 305419896

    def test_parse_no_debug_global(self):
        """解析全局 --no-debug"""
        parser = build_parser()
        args = parser.parse_args(["--no-debug", "exec", "test-id", "-c", "python"])
        assert args.no_debug is True


    def test_parse_default_debug(self):
        """解析 --default debug off"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--default", "debug", "off"])
        assert args.default == ["debug", "off"]

    def test_no_debug_default_false(self):
        """默认 no_debug 为 False"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python"])
        assert args.no_debug is False


class TestSendInputOption:
    """send 子命令输入选项（-i/--input）契约测试"""

    def _send_parser(self):
        parser = build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        return sub.choices["send"]

    def test_send_input_only_positional_is_id(self):
        """send 仅剩 id 一个位置参数（输入文本不再走位置参数）"""
        sp = self._send_parser()
        positional = [a.dest for a in sp._positionals._group_actions]
        assert positional == ["id"]

    def test_send_supports_both_spellings(self):
        """-i 与 --input 均为已注册选项"""
        sp = self._send_parser()
        assert "-i" in sp._option_string_actions
        assert "--input" in sp._option_string_actions

    def test_parse_send_long_form(self):
        """--input 长格式等价于 -i"""
        parser = build_parser()
        args = parser.parse_args(["send", "s1", "--input", "print(1)"])
        assert args.input == "print(1)"

    def test_parse_send_equals_form(self):
        """--input=<content> 形式可用"""
        parser = build_parser()
        args = parser.parse_args(["send", "s1", "--input=print(1)"])
        assert args.input == "print(1)"

    def test_parse_send_dash_content(self):
        """以 - 开头的输入用 -i=<content> 形式可正确送达"""
        parser = build_parser()
        args = parser.parse_args(["send", "s1", "-i=--help"])
        assert args.input == "--help"

    def test_parse_send_empty_content(self):
        """空输入合法（仅提交行尾），区别于未给出 -i"""
        parser = build_parser()
        args = parser.parse_args(["send", "s1", "-i", ""])
        assert args.input == ""

    def test_parse_send_with_other_options(self):
        """-i 与其他选项混用，位置前后均可"""
        parser = build_parser()
        args = parser.parse_args(
            ["send", "s1", "-i", "print(1)", "-t", ">>>", "--timeout", "5"])
        assert args.input == "print(1)"
        assert args.trigger == ">>>"
        assert args.timeout == 5.0
        args2 = parser.parse_args(
            ["send", "s1", "-t", ">>>", "-i", "print(1)"])
        assert args2.input == "print(1)"

    def test_send_multiline_content_preserved(self):
        """多行输入原样保留（不转义、不改写）"""
        parser = build_parser()
        args = parser.parse_args(["send", "s1", "-i", "a\n    b"])
        assert args.input == "a\n    b"

    def test_positional_input_rejected(self):
        """旧的位置参数写法直接报错，无兼容路径"""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["send", "s1", "print(1)"])
        assert exc.value.code == 2

    def test_main_requires_input(self, monkeypatch, capsys):
        """缺少 -i 时 main() 报错退出（不构造客户端、不起守护进程）"""
        monkeypatch.setattr("sys.argv", ["app.py", "send", "s1"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
        assert "--input/-i" in capsys.readouterr().err