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
        assert args.default == [["timeout", "30"]]

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
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--no-debug"])
        assert args.no_debug is True


    def test_parse_default_debug(self):
        """解析 --default debug off"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--default", "debug", "off"])
        assert args.default == [["debug", "off"]]

    def test_no_debug_default_false(self):
        """默认 no_debug 为 False"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python"])
        assert args.no_debug is False

    def test_parse_exec_snapshot_mode(self):
        """解析 exec --snapshot-mode"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--snapshot-mode"])
        assert args.snapshot_mode is True

    def test_parse_send_snapshot(self):
        """解析 send --snapshot"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input", "--snapshot"])
        assert args.snapshot is True

    def test_parse_read_snapshot(self):
        """解析 read --snapshot"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--snapshot"])
        assert args.snapshot is True

    def test_parse_exec_with_env(self):
        """解析 exec --env"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--env", "KEY=VALUE"])
        assert args.env == ["KEY=VALUE"]

    def test_parse_send_send_eol(self):
        """解析 send --send-eol"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input", "--send-eol", "cr"])
        assert args.send_eol == "cr"

    def test_parse_exec_force_pty_mode(self):
        """解析 exec --force-pty-mode"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd", "--force-pty-mode"])
        assert args.force_pty_mode is True

    def test_parse_exec_with_cwd(self):
        """解析 exec --cwd"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "python", "--cwd", "/tmp"])
        assert args.cwd == "/tmp"

    def test_parse_read_with_offset(self):
        """解析 read --offset"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--offset", "1024"])
        assert args.offset == 1024

    def test_parse_exec_response_format(self):
        """解析 exec --response-format"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd", "--response-format", "svg"])
        assert args.response_format == "svg"

    def test_parse_exec_response_format_default(self):
        """exec --response-format 默认为 None"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd"])
        assert args.response_format is None

    def test_parse_send_response_format(self):
        """解析 send --response-format"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input", "--response-format", "svg"])
        assert args.response_format == "svg"

    def test_parse_read_response_format(self):
        """解析 read --response-format"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--response-format", "stream"])
        assert args.response_format == "stream"

    def test_parse_exec_svg_compression_level(self):
        """解析 exec --svg-compression-level"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd", "--svg-compression-level", "1"])
        assert args.svg_compression_level == 1

    def test_parse_send_svg_compression_level(self):
        """解析 send --svg-compression-level"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input", "--svg-compression-level", "0"])
        assert args.svg_compression_level == 0

    def test_parse_read_svg_compression_level(self):
        """解析 read --svg-compression-level"""
        parser = build_parser()
        args = parser.parse_args(["read", "test-id", "--svg-compression-level", "2"])
        assert args.svg_compression_level == 2

    def test_parse_exec_svg_compression_level_default(self):
        """exec --svg-compression-level 默认为 None"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd"])
        assert args.svg_compression_level is None

    def test_parse_send_output(self):
        """解析 send --output/-o"""
        parser = build_parser()
        args = parser.parse_args(["send", "test-id", "-i", "input", "-o", "out.svg"])
        assert args.output_path == "out.svg"

    def test_parse_default_response_format(self):
        """解析 --default response-format svg"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd", "--default", "response-format", "svg"])
        assert args.default == [["response-format", "svg"]]

    def test_parse_default_svg_compression_level(self):
        """解析 --default svg-compression-level 1"""
        parser = build_parser()
        args = parser.parse_args(["exec", "test-id", "-c", "cmd", "--default", "svg-compression-level", "1"])
        assert args.default == [["svg-compression-level", "1"]]

    def test_parse_mouse_click(self):
        """解析 mouse click"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "click", "10,5", "--button", "right", "--ctrl"])
        assert args.subcmd == "mouse"
        assert args.action == "click"
        assert args.args == ["10,5"]
        assert args.button == "right"
        assert args.ctrl is True

    def test_parse_mouse_drag(self):
        """解析 mouse drag"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "drag", "1,1", "3,3", "--shift"])
        assert args.action == "drag"
        assert args.args == ["1,1", "3,3"]
        assert args.shift is True

    def test_parse_mouse_scroll(self):
        """解析 mouse scroll"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "scroll", "10,10", "down", "3"])
        assert args.action == "scroll"
        assert args.args == ["10,10", "down", "3"]

    def test_parse_mouse_hover(self):
        """解析 mouse hover"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "hover", "5,5", "--alt"])
        assert args.action == "hover"
        assert args.args == ["5,5"]
        assert args.alt is True

    def test_parse_mouse_press(self):
        """解析 mouse press"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "press", "2,2", "1.5", "--button", "middle"])
        assert args.action == "press"
        assert args.args == ["2,2", "1.5"]
        assert args.button == "middle"

    def test_parse_mouse_grep(self):
        """解析 mouse grep"""
        parser = build_parser()
        args = parser.parse_args(["mouse", "test-id", "grep", "pattern"])
        assert args.action == "grep"
        assert args.args == ["pattern"]

    def test_parse_mouse_with_output_options(self):
        """解析 mouse 输出控制选项"""
        parser = build_parser()
        args = parser.parse_args([
            "mouse", "test-id", "click", "10,5",
            "-t", ">>>", "--timeout", "5", "--snapshot", "-s",
            "--output", "out.svg", "--response-format", "svg",
        ])
        assert args.action == "click"
        assert args.trigger == ">>>"
        assert args.timeout == 5.0
        assert args.snapshot is True
        assert args.snapshot_diff is True
        assert args.output_path == "out.svg"
        assert args.response_format == "svg"