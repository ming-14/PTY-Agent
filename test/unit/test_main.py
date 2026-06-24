"""__main__.py CLI 入口单元测试

测试参数解析、配置键转换、时间补全、引号修复。
"""

import pytest

from src.__main__ import (
    _parse_default_key,
    _format_config_key,
    _maybe_expand_time,
    build_parser,
)


class TestParseDefaultKey:
    """_parse_default_key 测试"""

    def test_hyphen_to_underscore(self):
        assert _parse_default_key("output-by-natural-language") == "output_by_natural_language"

    def test_keep_underscore(self):
        assert _parse_default_key("timeout") == "timeout"

    def test_keep_ansi(self):
        assert _parse_default_key("keep-ansi") == "keep_ansi"


class TestFormatConfigKey:
    """_format_config_key 测试"""

    def test_underscore_to_hyphen(self):
        assert _format_config_key("output_by_natural_language") == "output-by-natural-language"

    def test_no_underscore(self):
        assert _format_config_key("timeout") == "timeout"


class TestMaybeExpandTime:
    """_maybe_expand_time 测试"""

    def test_none_returns_none(self):
        assert _maybe_expand_time(None) is None

    def test_full_iso_passthrough(self):
        """完整 ISO 8601 直接通过"""
        result = _maybe_expand_time("2026-06-07T18:00:00+08:00")
        assert "2026-06-07" in result
        assert "18:00" in result

    def test_utc_z_suffix(self):
        """Z 后缀转换为 +00:00"""
        result = _maybe_expand_time("2026-06-07T18:00:00Z")
        assert result is not None

    def test_hhmm_expands(self):
        """HH:MM 补全当天日期"""
        result = _maybe_expand_time("18:00")
        assert "T18:00" in result

    def test_hhmmss_expands(self):
        """HH:MM:SS 补全当天日期"""
        result = _maybe_expand_time("18:30:00")
        assert "T18:30:00" in result


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
        """解析 send 子命令"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "input text"])
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

    def test_parse_kill(self):
        """解析 kill 子命令"""
        parser = build_parser()
        args = parser.parse_args(["kill", "test-id"])
        assert args.subcmd == "kill"
        assert args.id == "test-id"

    def test_parse_events(self):
        """解析 events 子命令"""
        parser = build_parser()
        args = parser.parse_args(["events", "test-id", "--last", "5"])
        assert args.subcmd == "events"
        assert args.id == "test-id"
        assert args.last == 5

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

    def test_parse_events_with_since(self):
        """解析 events --since"""
        parser = build_parser()
        args = parser.parse_args(["events", "test-id", "--since", "18:00"])
        assert args.since == "18:00"

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