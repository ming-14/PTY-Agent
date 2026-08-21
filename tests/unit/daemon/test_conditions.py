"""daemon/conditions.py 返回条件统一声明 单元测试"""

from src.daemon.conditions import RequestContext, ReturnConditions
from src.protocol.reasons import Reason


class TestReturnConditions:
    def test_from_msg_defaults(self):
        cond = ReturnConditions.from_msg({"type": "read"})
        assert cond.trigger is None
        assert cond.timeout == 120.0
        assert cond.explicit_timeout is False
        assert cond.keep_ansi is False
        assert not cond.has_wait

    def test_from_msg_reads_conditions(self):
        cond = ReturnConditions.from_msg(
            {
                "trigger": ">>>",
                "newline": True,
                "fresh": True,
                "timeout": 30,
                "idle_timeout": 3,
                "idle_after_first_output": True,
                "full": True,
                "keep_ansi": True,
                "snapshot_diff": True,
            }
        )
        assert cond.trigger == ">>>"
        assert cond.newline is True
        assert cond.fresh is True
        assert cond.timeout == 30
        assert cond.idle_timeout == 3
        assert cond.idle_after_first is True
        assert cond.full and cond.keep_ansi and cond.snapshot_diff
        assert cond.has_trigger and cond.has_idle and cond.has_wait

    def test_has_wait_matrix(self):
        assert not ReturnConditions.from_msg({"type": "read"}).has_wait
        assert ReturnConditions.from_msg({"trigger": "x"}).has_wait
        assert ReturnConditions.from_msg({"idle_timeout": 1}).has_wait
        assert ReturnConditions.from_msg({"explicit_timeout": True}).has_wait


class TestReason:
    def test_values(self):
        assert Reason.MATCHED == "matched"
        assert Reason.PROGRAM_CRASHED == "program_crashed"


class TestRequestContext:
    def test_from_msg_defaults(self):
        req = RequestContext.from_msg({"type": "read"})
        assert req.id == ""
        assert req.command is None
        assert req.input == ""
        assert req.encoding is None
        assert req.mode == "pty"
        assert req.plugins == []
        assert req.cli_plugins == []
        assert req.lines is None
        assert req.grep is None
        assert req.offset is None
        assert req.action is None
        assert req.t_start is None
        assert req.cond.timeout == 120.0
        assert not req.cond.has_wait

    def test_from_msg_reads_common_fields(self):
        req = RequestContext.from_msg(
            {
                "id": "s1",
                "command": "ping",
                "input": "hi",
                "encoding": "gbk",
                "cwd": "C:/x",
                "env": {"A": "1"},
                "mode": "subprocess",
                "cols": 80,
                "rows": 24,
                "size": "80x24",
                "plugins": ["p1"],
                "cliPlugins": ["p2"],
                "lines": 10,
                "grep": "err",
                "column": 2,
                "offset": 5,
                "action": "click",
                "_t_start": 123.0,
                "trigger": ">>>",
                "timeout": 30,
                "idle_timeout": 3,
            }
        )
        assert req.id == "s1"
        assert req.command == "ping"
        assert req.input == "hi"
        assert req.encoding == "gbk"
        assert req.cwd == "C:/x"
        assert req.env == {"A": "1"}
        assert req.mode == "subprocess"
        assert req.cols == 80
        assert req.rows == 24
        assert req.size == "80x24"
        assert req.plugins == ["p1"]
        assert req.cli_plugins == ["p2"]
        assert req.lines == 10
        assert req.grep == "err"
        assert req.column == 2
        assert req.offset == 5
        assert req.action == "click"
        assert req.t_start == 123.0
        assert req.cond.trigger == ">>>"
        assert req.cond.timeout == 30
        assert req.cond.idle_timeout == 3