"""daemon/notifications.py — NotificationManager 单元测试"""

import select
import time

import pytest

from src.daemon.notifications import NotificationManager, build_notify_waiting_response, _default_notify_detail
from src.protocol.reasons import Reason


class _FakeSession:
    """build_notify_waiting_response 的最小 Session 桩（仅 pty 快照路径）"""

    def __init__(self, sid="s1", mode="pty", running=True):
        self.id = sid
        self.uid = "uid-" + sid
        self.mode = mode
        self.running = running
        self.command = "echo hi"
        self.start_time = time.time()
        self.exit_code = None
        self.error_message = None
        self.pty_type = "wezterm"
        self.output_offset = 0
        self.processes = []
        self.gui_windows = []
        self.client_config = {}

    def get_snapshot(self, keep_ansi=False):
        return ">>> hello"

    def get_full_snapshot(self, keep_ansi=False):
        return ">>> hello\nmore"

    def get_output_with_offset(self, from_offset=0, encoding=None):
        return "out", 4

    def read_base(self, full=False):
        return 0

    def get_all_events(self):
        return []

    def consume_events(self):
        return []


class _FakeManager:
    def get_session(self, sid):
        return None


class _FakeCtx:
    def __init__(self):
        self.manager = _FakeManager()
        self.server = None
        self.authenticator = None


class TestNotificationManager:
    def test_publish_and_list(self):
        nm = NotificationManager()
        nid = nm.publish({"commandType": "exec", "sessionId": "py", "triggerReturnReason": "program_ended"})
        assert isinstance(nid, str) and len(nid) == 32  # uuid4().hex
        lst = nm.pending_list()
        assert len(lst) == 1
        assert lst[0]["nid"] == nid
        assert lst[0]["sessionId"] == "py"
        assert lst[0]["triggerReturnReason"] == "program_ended"
        assert lst[0]["detail"] == "py已结束"
        assert lst[0]["createdAt"]
        nm.close()

    def test_detail_reason_mapping(self):
        """命令通知详情按 triggerReturnReason 映射"""
        cases = [
            ("trigger_matched", "py已完成"),
            ("trigger_timeout", "py超时"),
            ("idle_timeout", "py空闲超时"),
            ("program_ended", "py已结束"),
            ("program_crashed", "py已崩溃"),
            ("gui_detected", "py检测到窗口"),
            ("cancelled", "py已取消"),
            ("unknown_reason", "py已完成"),  # 未知原因兜底为已完成
        ]
        nm = NotificationManager()
        for reason, expected in cases:
            nm.publish({"commandType": "exec", "sessionId": "py", "triggerReturnReason": reason})
        details = [n["detail"] for n in nm.pending_list()]
        assert details == [e for _, e in cases]
        nm.close()

    def test_detail_custom_priority(self):
        """发布方自带 detail 优先于自动映射"""
        nm = NotificationManager()
        nm.publish({"commandType": "subagent_turn_complete", "sessionId": "dev",
                    "triggerReturnReason": "turn_complete",
                    "detail": "SubAgent: Codebuddy已完成"})
        lst = nm.pending_list()
        assert lst[0]["detail"] == "SubAgent: Codebuddy已完成"
        nm.close()

    def test_get_by_nid_full_response(self):
        nm = NotificationManager()
        resp = {"commandType": "exec", "sessionId": "py", "outputStream": "ok",
                "triggerReturnReason": "trigger_matched"}
        nid = nm.publish(resp)
        assert nm.get_by_nid(nid) == resp
        assert nm.get_by_nid("nonexistent") is None
        nm.close()

    def test_per_session_cap_fifo_evict(self):
        nm = NotificationManager(max_per_session=2)
        nids = []
        for i in range(3):
            nids.append(nm.publish({"commandType": "exec", "sessionId": "s",
                                    "triggerReturnReason": "ok", "i": i}))
        # 每会话限 2：最旧一条被淘汰，剩最新两条
        lst = nm.pending_list()
        assert [n["nid"] for n in lst] == nids[1:]
        assert nm.get_by_nid(nids[0]) is None
        assert nm.get_by_nid(nids[1]) is not None
        assert nm.get_by_nid(nids[2]) is not None
        nm.close()

    def test_multi_session_independent_evict(self):
        """淘汰只删本会话最旧，不影响其他会话"""
        nm = NotificationManager(max_per_session=1)
        a1 = nm.publish({"commandType": "exec", "sessionId": "a", "i": 1})
        b1 = nm.publish({"commandType": "exec", "sessionId": "b", "i": 1})
        a2 = nm.publish({"commandType": "exec", "sessionId": "a", "i": 2})
        lst = nm.pending_list()
        # a 会话淘汰 a1，保留 a2 + b1
        assert {n["nid"] for n in lst} == {a2, b1}
        nm.close()

    def test_pending_count(self):
        nm = NotificationManager()
        assert nm.pending_count() == 0
        nm.publish({"commandType": "exec", "sessionId": "s"})
        nm.publish({"commandType": "send", "sessionId": "s"})
        assert nm.pending_count() == 2
        nm.close()

    def test_wake_fd_selectable(self):
        """发布后唤醒通道可读，select 立即返回（wait handler 依赖此机制）"""
        nm = NotificationManager()
        r, _, _ = select.select([nm.wake_fd], [], [], 0)
        assert not r  # 初始不可读
        nm.publish({"commandType": "exec", "sessionId": "s"})
        r, _, _ = select.select([nm.wake_fd], [], [], 0.5)
        assert nm.wake_fd in r
        nm.drain()
        r, _, _ = select.select([nm.wake_fd], [], [], 0)
        assert not r  # drain 后清空
        nm.close()

    def test_close(self):
        nm = NotificationManager()
        nm.publish({"commandType": "exec", "sessionId": "s"})
        nm.close()
        nm.close()  # 幂等


class TestBuildNotifyWaitingResponse:
    def test_pty_reason(self):
        ctx = _FakeCtx()
        sess = _FakeSession()
        resp = build_notify_waiting_response(ctx, sess, {"_t_start": time.monotonic()}, "exec")
        assert resp["commandType"] == "exec"
        assert resp["sessionId"] == "s1"
        assert resp["triggerReturnReason"] == Reason.NOTIFY_WAITING
        assert ">>> hello" in resp["outputStream"]
        assert resp["format"] == "snapshot"

    def test_subprocess_incremental(self):
        ctx = _FakeCtx()
        sess = _FakeSession(mode="subprocess")
        resp = build_notify_waiting_response(ctx, sess, {"_t_start": time.monotonic()}, "read")
        assert resp["commandType"] == "read"
        assert resp["triggerReturnReason"] == Reason.NOTIFY_WAITING
        assert resp["format"] == "diff"


class TestAutoConsumeCommands:
    """黑盒：dispatcher 通知自动消费的 commandType 白名单（dispatch 请求到达时消费）"""

    def test_auto_consume_whitelist_import(self):
        """_AUTO_CONSUME_COMMANDS 常量可导入且包含操作型命令"""
        from src.daemon.handlers.dispatcher import _AUTO_CONSUME_COMMANDS
        for cmd in ("exec", "send", "read", "mouse", "kill"):
            assert cmd in _AUTO_CONSUME_COMMANDS, f"{cmd} 应在白名单"

    def test_plugin_ls_not_in_whitelist(self):
        """plugin ls 不在白名单中（查询型，不应消费通知）"""
        from src.daemon.handlers.dispatcher import _AUTO_CONSUME_COMMANDS
        assert "plugin" not in _AUTO_CONSUME_COMMANDS

    def test_auto_consume_logic_consumes(self):
        """模拟 dispatcher 的 dispatch 时消费逻辑：操作型命令请求到达即消费"""
        from src.daemon.handlers.dispatcher import _AUTO_CONSUME_COMMANDS
        nm = NotificationManager()
        nm.publish({"commandType": "read", "sessionId": "s1", "triggerReturnReason": "ok"})
        # 模拟 dispatcher dispatch() 逻辑（请求到达时，无 spawned 概念）
        session_id = "s1"
        msg_type = "read"
        if session_id and msg_type in _AUTO_CONSUME_COMMANDS:
            nm.consume_by_session(session_id)
        assert nm.pending_count() == 0, "read 请求到达应消费通知"

    def test_auto_consume_logic_skips_plugin(self):
        """plugin ls 请求不应消费通知"""
        from src.daemon.handlers.dispatcher import _AUTO_CONSUME_COMMANDS
        nm = NotificationManager()
        nm.publish({"commandType": "plugin", "sessionId": "s1", "action": "ls"})
        session_id = "s1"
        msg_type = "plugin"
        if session_id and msg_type in _AUTO_CONSUME_COMMANDS:
            nm.consume_by_session(session_id)
        assert nm.pending_count() == 1, "plugin ls 不应消费通知"

    def test_auto_consume_missing_sid_skips(self):
        """无 sessionId 的请求不消费任何通知（未指定会话）"""
        from src.daemon.handlers.dispatcher import _AUTO_CONSUME_COMMANDS
        nm = NotificationManager()
        nm.publish({"commandType": "exec", "sessionId": "s1", "triggerReturnReason": "ok"})
        session_id = ""
        msg_type = "send"
        if session_id and msg_type in _AUTO_CONSUME_COMMANDS:
            nm.consume_by_session(session_id)
        assert nm.pending_count() == 1, "无 sessionId 不应消费"
