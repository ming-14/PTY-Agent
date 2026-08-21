"""client/result.py + client/presenter.py 单元测试"""

import io
import shutil
import sys

import pytest

from src.client.presenter import (
    error_seen,
    present,
    set_debug_mode,
)
from src.client.result import ErrorResult, ListResult, MessageResult, SessionResult, StatusResult, from_response


class TestFromResponse:
    def test_none(self):
        r = from_response(None)
        assert isinstance(r, ErrorResult) and not r.ok

    def test_error(self):
        r = from_response({"type": "error", "message": "boom"})
        assert isinstance(r, ErrorResult) and not r.ok and r.message == "boom"

    def test_error_code_classified(self):
        assert from_response({"type": "error", "message": "Session not found"}).code == "NOT_FOUND"
        assert from_response({"type": "error", "message": "会话不存在"}).code == "NOT_FOUND"
        assert from_response({"type": "error", "message": "Authentication failed"}).code == "UNAUTHORIZED"
        # 服务端显式 code 优先于分类
        r = from_response({"type": "error", "message": "boom", "code": "CUSTOM"})
        assert r.code == "CUSTOM"

    def test_info_warning(self):
        assert isinstance(from_response({"type": "info", "message": "hi"}), MessageResult)
        assert isinstance(from_response({"type": "warning", "message": "w"}), MessageResult)

    def test_session(self):
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "py",
                "outputStream": ">>> ",
                "triggerReturnReason": "matched",
                "program": {"running": True, "ptyType": "wezterm"},
                "hint": "ok",
            }
        )
        assert isinstance(r, SessionResult)
        assert r.output == ">>> " and r.running and r.kind == "session"

    def test_status_and_list(self):
        assert isinstance(from_response({"type": "status", "running": True}), StatusResult)
        r = from_response({"commandType": "list", "sessions": [{"id": "s"}]})
        assert isinstance(r, ListResult)


def _cap(fn):
    out, err = io.StringIO(), io.StringIO()
    fn(out, err)
    return out.getvalue(), err.getvalue()


class TestPresenter:
    def _reset(self):
        set_debug_mode(False)
        import src.client.presenter as m

        m._error_seen = False

    def test_mouse_cursor_result_line(self):
        """_get_cursor_location → [cursor] col=.. row=..（结果正文，非 cursor: 前缀）"""
        self._reset()
        r = from_response(
            {
                "commandType": "mouse",
                "sessionId": "t_py",
                "cursor": {"col": 5, "row": 23},
                "triggerReturnReason": "ok",
                "program": {"running": True, "ptyType": "conpty", "mode": "pty"},
            }
        )
        out, _ = _cap(lambda o, e: present(r, o, e))
        assert "[cursor] col=5 row=23" in out
        assert "cursor: col=" not in out

    def test_session_content_stdout_meta_stderr(self):
        self._reset()
        out, err = _cap(
            lambda o, e: present(
                from_response(
                    {
                        "commandType": "read",
                        "sessionId": "py",
                        "outputStream": "hello\n",
                        "triggerReturnReason": "matched",
                        "program": {"running": True, "ptyType": "wezterm"},
                        "hint": "trigger matched",
                    }
                ),
                o,
                e,
            )
        )
        assert "hello" in out
        # 分隔线包住内容
        assert "─" in out
        # 状态行 → stdout 底部（与内容同流，顺序确定）
        assert "py" in out
        assert "py" not in err
        # hint 统一以 (hit: ...) 追加在 stdout 末尾
        assert "(hit: trigger matched)" in out
        assert "trigger matched" not in err
        assert not error_seen()

    def test_session_separator_format_and_elapsed(self):
        self._reset()
        r = from_response(
            {
                "commandType": "send",
                "sessionId": "test_py",
                "outputStream": "56088\n>>> ",
                "triggerReturnReason": "matched",
                "format": "snapshot",
                "program": {
                    "running": True,
                    "ptyType": "wezterm",
                    "debugInformation": {"elapsedMs": 520.0},
                },
            }
        )
        out, err = _cap(lambda o, e: present(r, o, e))
        lines = out.splitlines()
        seps = [l for l in lines if l.startswith("─")]
        # 顶/底两条对齐分隔线，顶线带格式标签，总宽对齐终端列数
        assert len(seps) >= 2
        assert "snapshot" in seps[0]
        cols = shutil.get_terminal_size((80, 24)).columns
        assert len(seps[0]) == len(seps[-1]) == cols
        # 内容夹在两条分隔线之间，状态行在底分隔线之后
        assert "56088" in out
        assert lines[-1].startswith("[send · matched · 0.52s]")
        assert "[send" not in err

    def test_error_goes_stderr_and_sets_flag(self):
        self._reset()
        out, err = _cap(lambda o, e: present(ErrorResult(message="boom"), o, e))
        assert out == ""
        assert "boom" in err
        assert error_seen()

    def test_info_goes_stderr_config_stdout(self):
        self._reset()
        out, err = _cap(lambda o, e: present(MessageResult(msg_type="info", text="hi"), o, e))
        assert err == "(PTY-Agent message: hi)\n" and out == ""
        out, err = _cap(lambda o, e: present(MessageResult(msg_type="config", text="cfg"), o, e))
        assert out == "cfg\n" and err == ""

    def _session_with_debug(self):
        return from_response(
            {
                "commandType": "read",
                "sessionId": "s",
                "outputStream": "x",
                "triggerReturnReason": "ok",
                "program": {"running": True, "debugInformation": {"processes": [1234]}},
            }
        )

    def test_debug_hidden_when_disabled(self):
        self._reset()
        set_debug_mode(False)
        out, err = _cap(lambda o, e: present(self._session_with_debug(), o, e))
        assert "1234" not in out

    def test_debug_shown_when_enabled(self):
        self._reset()
        set_debug_mode(True)
        out, err = _cap(lambda o, e: present(self._session_with_debug(), o, e))
        # debug 信息走 stdout 末尾（状态行之后的分块），且带 debug 分区框
        assert "1234" in out
        assert "debug" in out

    def test_events_hint_appended_as_hit_stdout(self):
        self._reset()
        r = from_response(
            {
                "commandType": "events",
                "sessionId": "s",
                "pendingEvents": [
                    {"time": "2026-08-20T08:02:14.82", "type": "process_spawn", "pid": 18184}
                ],
                "count": 1,
                "hint": "Only unconsumed events are shown. Use -l <N> to view the full event history.",
            }
        )
        out, err = _cap(lambda o, e: present(r, o, e))
        assert "process_spawn" in out
        # 表头 key/events/pid + 分隔线
        assert "key" in out.splitlines()[0]
        assert "events" in out.splitlines()[0]
        assert "pid" in out.splitlines()[0]
        assert out.rstrip().endswith("(hit: Only unconsumed events are shown. Use -l <N> to view the full event history.)")
        assert "hit:" not in err

    def test_mouse_grep_multiple_matches_message_before_box(self):
        self._reset()
        r = from_response(
            {
                "commandType": "mouse",
                "sessionId": "bb-py1",
                "action": "click",
                "grep": "delta test",
                "performed": False,
                "message": "Multiple matches found; please specify coordinates or a more specific pattern",
                "matches": [
                    {"start": {"row": 11, "col": 12}, "end": {"row": 11, "col": 21}},
                    {"start": {"row": 12, "col": 1}, "end": {"row": 12, "col": 10}},
                ],
                "outputStream": ">>> print('delta test')\ndelta test\n>>> ",
                "triggerReturnReason": "ok",
                "format": "match:delta test",
                "program": {
                    "running": True,
                    "ptyType": "wezterm",
                    "debugInformation": {"elapsedMs": 12.0},
                },
            }
        )
        out, err = _cap(lambda o, e: present(r, o, e))
        # 命中列表是结果正文（非 hit），放分隔线前
        assert '[grep "delta test"] row=11 col=12..21' in out
        assert '[grep "delta test"] row=12 col=1..10' in out
        assert "hit:" not in out
        # 命中列表在顶部分隔线之前
        first_sep = next(i for i, l in enumerate(out.splitlines()) if l.startswith("─"))
        match_idx = next(i for i, l in enumerate(out.splitlines()) if l.startswith("[grep"))
        assert match_idx < first_sep
        # 未执行消息 → stderr，(PTY-Agent message: Operation not performed. ...)
        assert "(PTY-Agent message: Operation not performed. Multiple matches found; please specify coordinates or a more specific pattern.)" in err
        # 快照内容 + 状态行仍在
        assert "delta test" in out
        assert "[mouse · ok" in out

    def test_mouse_grep_query_matches_are_result(self):
        self._reset()
        r = from_response(
            {
                "commandType": "mouse",
                "sessionId": "s",
                "action": "grep",
                "grep": "foo",
                "performed": False,
                "matches": [{"start": {"row": 3, "col": 5}, "end": {"row": 3, "col": 8}}],
                "outputStream": "",
                "triggerReturnReason": "ok",
                "format": "match:foo",
                "program": {"running": True, "ptyType": "wezterm"},
            }
        )
        out, err = _cap(lambda o, e: present(r, o, e))
        assert '[grep "foo"] row=3 col=5..8' in out
        # 查询动作无匹配 → No match found. 消息（非 Operation not performed）
        assert "Operation not performed" not in err

    def test_status_line_includes_mode(self):
        self._reset()
        for mode, want in (("pty", "pty"), ("subprocess", "subprocess")):
            r = from_response(
                {
                    "commandType": "read",
                    "sessionId": "s",
                    "outputStream": "x\n",
                    "triggerReturnReason": "ok",
                    "program": {"running": True, "ptyType": "conpty" if mode == "pty" else "subprocess", "mode": mode},
                }
            )
            out, _ = _cap(lambda o, e: present(r, o, e))
            assert out.splitlines()[-1].endswith("  " + want)

    def test_crash_message_before_no_output_no_hit(self):
        self._reset()
        r = from_response(
            {
                "commandType": "exec",
                "sessionId": "bb-crash",
                "outputStream": "",
                "triggerReturnReason": "program_crashed",
                "program": {
                    "running": False,
                    "exitCode": 7,
                    "ptyType": "conpty",
                    "mode": "pty",
                },
            }
        )
        out, err = _cap(lambda o, e: present(r, o, e))
        # 崩溃消息 → (PTY-Agent message: ...)，位于 No output. 之前
        assert "(PTY-Agent message: Program crashed with exit code: 7.)" in err
        assert "(PTY-Agent message: No output.)" in out
        err_idx = err.find("Program crashed")
        out_idx = out.find("No output.")
        assert err_idx != -1 and out_idx != -1
        # 不再有 (hit: Program crashed ...)
        assert "hit: Program crashed" not in out and "hit: Program crashed" not in err
        # 状态行带 mode 与崩溃退出码
        assert "[exec · crashed(exit_code: 7)" in out
        assert out.splitlines()[-1].endswith("pty")