"""protocol/envelope.py 单元测试 — 信封 + 分组载荷"""

from src.protocol.envelope import (
    PROTO,
    flatten,
    group_request,
    request,
    response,
    split_response,
    unsplit_response,
    unwrap,
    wrap_response,
)


class TestGroupRequest:
    def test_terminal_cmd_grouped(self):
        payload = group_request(
            "exec",
            {
                "type": "exec",
                "id": "py",
                "command": ["python", "-u", "-i"],
                "kind": None,
                "trigger": ">>>",
                "timeout": 120,
                "idle_timeout": 3,
                "full": False,
                "keep_ansi": True,
                "encoding": "utf-8",
            },
        )
        assert payload["op"]["id"] == "py"
        assert payload["op"]["command"] == ["python", "-u", "-i"]
        assert payload["condition"]["trigger"] == ">>>"
        assert payload["condition"]["timeout"] == 120
        assert payload["condition"]["idle_timeout"] == 3
        assert payload["output"]["keep_ansi"] is True
        assert payload["io"]["encoding"] == "utf-8"

    def test_non_terminal_op_verbatim(self):
        payload = group_request("list", {"type": "list"})
        assert payload == {"op": {"type": "list"}}


class TestFlatten:
    def test_round_trip(self):
        flat = {
            "type": "read",
            "id": "s1",
            "trigger": ">>>",
            "newline": True,
            "timeout": 10,
            "lines": "5",
            "grep": "err",
            "offset": 3,
            "encoding": "gbk",
        }
        payload = group_request("read", flat)
        body = flatten(payload)
        for k, v in flat.items():
            assert body[k] == v, k

    def test_non_grouped_payload_passthrough(self):
        body = {"commandType": "list", "sessions": []}
        assert flatten(body) == body
        assert flatten(None) == {}


class TestEnvelope:
    def test_request_shape(self):
        env = request("exec", {"type": "exec", "id": "py", "command": ["python"]})
        assert env["proto"] == PROTO
        assert env["dir"] == "request"
        assert env["type"] == "exec"
        assert env["mid"]
        assert env["ts"]
        assert env["kind"] == "session"
        assert env["payload"]["op"]["id"] == "py"

    def test_request_kind_default(self):
        assert request("list", {"type": "list"})["kind"] == "list"

    def test_response_shape(self):
        env = response("exec", {"commandType": "exec"}, mid="m1")
        assert env["dir"] == "response"
        assert env["mid"] == "m1"
        assert env["payload"]["commandType"] == "exec"

    def test_unwrap_request(self):
        flat = {"type": "read", "id": "s1", "trigger": ">>>", "lines": "5"}
        env = request("read", flat)
        type_, body, envelope = unwrap(env)
        assert type_ == "read"
        assert body["id"] == "s1"
        assert body["trigger"] == ">>>"
        assert body["lines"] == "5"
        assert envelope is env and env["dir"] == "request"

    def test_unwrap_response(self):
        env = response("exec", {"commandType": "exec", "outputStream": "hi"})
        type_, body, envelope = unwrap(env)
        assert type_ == "exec"
        assert body["outputStream"] == "hi"

    def test_unwrap_flat(self):
        type_, body, envelope = unwrap({"type": "ping"})
        assert type_ == "ping"
        assert body == {"type": "ping"}
        assert envelope is None


class TestResponseGrouping:
    def _session_body(self):
        return {
            "commandType": "exec",
            "sessionId": "py",
            "uid": "u1",
            "outputStream": ">>> ",
            "outputOffset": 64,
            "triggerReturnReason": "matched",
            "program": {"running": True, "ptyType": "wezterm"},
            "hint": "ok",
        }

    def test_wrap_response_session_grouped(self):
        env = wrap_response(self._session_body())
        assert env["dir"] == "response"
        assert env["type"] == "exec"
        assert env["payload"]["data"]["outputStream"] == ">>> "
        assert env["payload"]["state"]["triggerReturnReason"] == "matched"
        assert env["payload"]["meta"]["hint"] == "ok"

    def test_response_round_trip(self):
        body = self._session_body()
        env = wrap_response(body)
        type_, out, _ = unwrap(env)
        assert type_ == "exec"
        for k, v in body.items():
            assert out[k] == v, k

    def test_wrap_response_file_handshake_exempt(self):
        raw = {"commandType": "file_upload_start"}
        assert wrap_response(raw) is raw

    def test_wrap_response_pong_exempt(self):
        pong = {"type": "pong"}
        assert wrap_response(pong) is pong

    def test_wrap_response_non_session(self):
        env = wrap_response({"commandType": "list", "sessions": []})
        assert env["type"] == "list"
        assert env["payload"]["sessions"] == []

    def test_split_unknown_keys_to_meta(self):
        grouped = split_response({"commandType": "x", "custom": 1})
        assert grouped["meta"]["custom"] == 1