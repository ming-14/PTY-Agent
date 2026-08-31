"""file 子命令 CLI 链路测试 — parser 注册与 client.cmd_file_read"""

import os
import pytest

from src.cli.commands import register_all
from src.cli.commands.file import _resolve_cli_content
from src.cli.registry import CommandRegistry
from src.client.transport import Client


def _build_parser():
    """经命令注册表构建完整解析器"""
    registry = CommandRegistry()
    register_all(registry)
    return registry.build_parser(prog="pty-agent", description="", epilog="")


class TestFileReadParser:
    def test_parse_minimal(self):
        args = _build_parser().parse_args(["file", "read", "-s", "sid", "C:/x/a.txt"])
        assert args.subcmd == "file"
        assert args.file_subcmd == "read"
        assert args.path == "C:/x/a.txt"
        assert args.offset is None
        assert args.limit is None

    def test_parse_full_options(self):
        args = _build_parser().parse_args(
            ["file", "read", "-s", "sid", "a.txt", "--offset", "10", "--limit", "5"])
        assert args.offset == 10
        assert args.limit == 5

    def test_missing_path_errors(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["file", "read"])

    def test_missing_cwd_session_errors(self):
        # -s/--cwd-session 必填
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["file", "read", "C:/x/a.txt"])

    def test_cwd_session_short_flag(self):
        args = _build_parser().parse_args(["file", "read", "-s", "sid", "C:/x/a.txt"])
        assert args.cwd_session == "sid"

    def test_unknown_subcommand_errors(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["file", "unknown"])


class TestFileWriteParser:
    def test_parse_content(self):
        args = _build_parser().parse_args(["file", "write", "-s", "sid", "a.txt", "--content", "hello"])
        assert args.file_subcmd == "write"
        assert args.path == "a.txt"
        assert args.content == "hello"

    def test_content_optional_in_parser(self):
        # parser 不强制 --content（stdin/默认值场景由 main 分支处理）
        args = _build_parser().parse_args(["file", "write", "-s", "sid", "a.txt"])
        assert args.content is None

    def test_parse_content_file(self):
        args = _build_parser().parse_args(
            ["file", "write", "-s", "sid", "a.txt", "--content-file", "big.txt"])
        assert args.content is None
        assert args.content_file == "big.txt"


class TestFileEditParser:
    def test_parse_replace(self):
        args = _build_parser().parse_args(
            ["file", "edit", "-s", "sid", "a.txt", "--old", "x", "--new", "y"])
        assert args.file_subcmd == "edit"
        assert args.old == "x"
        assert args.new == "y"

    def test_defaults_empty(self):
        # --old/--new 缺省为空串（delete/create 分支）
        args = _build_parser().parse_args(["file", "edit", "-s", "sid", "a.txt"])
        assert args.old == ""
        assert args.new == ""

    def test_parse_old_new_file(self):
        args = _build_parser().parse_args(
            ["file", "edit", "-s", "sid", "a.txt", "--old-file", "o.txt", "--new-file", "n.txt"])
        assert args.old_file == "o.txt"
        assert args.new_file == "n.txt"


class TestResolveCliContent:
    def test_inline_only(self):
        assert _resolve_cli_content("abc", None, "--old", "--old-file") == "abc"

    def test_file_only(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_bytes("\u4f60\u597d\r\nabc\r\n".encode("utf-8"))
        # CRLF 规范化为 LF，对齐 daemon universal newlines 视图
        assert _resolve_cli_content(None, str(f), "--content", "--content-file") == "\u4f60\u597d\nabc\n"

    def test_empty_inline_with_file_takes_file(self, tmp_path):
        # --old "" 与 --old-file 同时给 → 空串视为未提供，取文件
        f = tmp_path / "o.txt"
        f.write_text("payload", encoding="utf-8")
        assert _resolve_cli_content("", str(f), "--old", "--old-file") == "payload"

    def test_mutually_exclusive(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError, match="mutually exclusive"):
            _resolve_cli_content("x", str(f), "--content", "--content-file")

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            _resolve_cli_content(None, "C:/no/such/file.txt", "--content", "--content-file")

    def test_gbk_file_raises_utf8_error(self, tmp_path):
        f = tmp_path / "gbk.txt"
        f.write_bytes("\u4f60\u597d".encode("gbk"))
        with pytest.raises(ValueError, match="not valid UTF-8"):
            _resolve_cli_content(None, str(f), "--content", "--content-file")

    def test_inline_only_none_returns_none(self):
        # write 未给内容 → None，daemon 报 content is required
        assert _resolve_cli_content(None, None, "--content", "--content-file") is None


class TestFileGrepGlobParser:
    def test_grep_with_optional_path(self):
        args = _build_parser().parse_args(["file", "grep", "-s", "sid", "foo"])
        assert args.file_subcmd == "grep"
        assert args.pattern == "foo"
        assert args.path is None
        assert args.include is None
        assert args.literal_text is False

    def test_grep_full_options(self):
        args = _build_parser().parse_args(
            ["file", "grep", "-s", "sid", "foo", "src", "--include", "*.py", "--literal-text"])
        assert args.path == "src"
        assert args.include == "*.py"
        assert args.literal_text is True

    def test_glob(self):
        args = _build_parser().parse_args(["file", "glob", "-s", "sid", "**/*.go", "src"])
        assert args.file_subcmd == "glob"
        assert args.pattern == "**/*.go"
        assert args.path == "src"

    def test_missing_pattern_errors(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["file", "grep"])


class TestCmdFileRead:
    def test_sends_path_unchanged_with_cwd_session(self, monkeypatch):
        sent = []
        responses = []
        monkeypatch.setattr(
            "src.client.presenter.print_response",
            lambda r: responses.append(r))
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_read("~/x/a.txt", cwd_session="sid", offset=2, limit=3)
        msg = sent[0]
        assert msg["type"] == "file_read"
        assert msg["path"] == "~/x/a.txt"  # 原样传输，daemon 按会话 cwd 解析
        assert msg["cwd_session"] == "sid"
        assert msg["offset"] == 2
        assert msg["limit"] == 3
        assert responses[0]["type"] == "result"

    def test_omits_unset_options(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv",
            lambda self, msg: (sent.append(msg), {})[1])
        client = Client()
        client.cmd_file_read("a.txt", cwd_session="sid")
        msg = sent[0]
        assert "offset" not in msg
        assert "limit" not in msg


class TestCmdFileWrite:
    def test_sends_path_and_content(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_write("~/x/b.txt", cwd_session="sid", content="payload")
        msg = sent[0]
        assert msg["type"] == "file_write"
        assert msg["path"] == "~/x/b.txt"
        assert msg["cwd_session"] == "sid"
        assert msg["content"] == "payload"


class TestCmdFileEdit:
    def test_sends_normalized_branch(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_edit("a.txt", cwd_session="sid", old=None, new="x")
        msg = sent[0]
        assert msg["type"] == "file_edit"
        assert msg["path"] == "a.txt"
        assert msg["cwd_session"] == "sid"
        assert msg["old"] == ""  # None 归一为空串（create 分支）
        assert msg["new"] == "x"
        assert "path" in msg


class TestCmdFileGrepGlob:
    def test_grep_defaults_path_omitted(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_grep("foo", cwd_session="sid")
        msg = sent[0]
        assert msg["type"] == "file_grep"
        assert msg["cwd_session"] == "sid"
        assert "path" not in msg  # 缺省 = 会话 cwd，由 daemon 解析
        assert "include" not in msg

    def test_grep_passes_relative_path(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_grep("foo", cwd_session="sid", path="src",
                             include="*.py", literal_text=True)
        msg = sent[0]
        assert msg["path"] == "src"  # 原样传输，daemon 按会话 cwd 展开
        assert msg["include"] == "*.py"
        assert msg["literal_text"] is True

    def test_glob_defaults_path_omitted(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.client.presenter.print_response", lambda r: None)
        monkeypatch.setattr(
            Client, "_send_recv", lambda self, msg: (sent.append(msg), {"type": "result"})[1])
        client = Client()
        client.cmd_file_glob("**/*.py", cwd_session="sid")
        msg = sent[0]
        assert msg["type"] == "file_glob"
        assert msg["cwd_session"] == "sid"
        assert "path" not in msg