"""P1 uid 主标识改造单元测试。

覆盖：
- SessionManager：活跃表按 uid 键控、uid/sid 双查找、同名 sid 复用不串扰
- ConnectionContext：订阅/回调/解码器/持有按 uid 隔离
- AdaptiveLockService：锁按 uid 隔离
- HistoryStore：同名 sid 归档不覆盖（uid 主键）、旧库迁移
"""

import os
import time
import uuid

import pytest

from src.session.manager import SessionManager
from src.web.application.adaptive_lock import AdaptiveLockService
from src.web.infrastructure.repositories.history_store import HistoryStore
from src.web.infrastructure.web.connection_context import WebSocketConnectionContext


# ── SessionManager ──────────────────────────────────────────────

class _StubSession:
    """Session 桩：仅保留 manager 用到的字段与方法。"""

    _seq = 0

    def __init__(self, session_id, command, **kwargs):
        _StubSession._seq += 1
        self.id = session_id
        self.uid = f"uid-{_StubSession._seq}-{uuid.uuid4().hex[:6]}"
        self.command = command
        self.running = True
        self.start_time = time.time()
        self.exit_code = None
        self.error_message = None
        self.mode = kwargs.get("mode", "pty")
        self.pty_type = "conpty"
        self._pre_held = False
        self._stopped = False

        class _Pub:
            def add_on_end_callback(self, cb):
                pass

        self._publisher = _Pub()

    def pre_hold(self):
        self._pre_held = True

    def start(self):
        pass

    def stop(self):
        self._stopped = True

    def release_creation_hold(self):
        pass

    def release_components(self):
        pass

    @property
    def publisher(self):
        return self._publisher


@pytest.fixture
def stub_session_cls(monkeypatch):
    """把 manager 命名空间中的 Session 替换为桩，避免真实 PTY 创建。

    manager 模块通过 `from .session import Session` 绑定了名字，
    须 patch 该模块属性而非 src.session.session 模块。
    """
    monkeypatch.setattr("src.session.manager.Session", _StubSession)
    return _StubSession


class TestSessionManagerUidKeying:
    def test_registry_keyed_by_uid_and_sid_resolvable(self, stub_session_cls):
        m = SessionManager()
        s = m.create_session("cmd", ["cmd"])
        assert m._sessions[s.uid] is s           # 活跃表按 uid 索引
        assert m._sid_index["cmd"] == s.uid      # sid 索引
        assert m.get_session(s.uid) is s         # uid 查找
        assert m.get_session("cmd") is s         # sid 查找
        assert m.get_by_uid(s.uid) is s
        assert m.resolve_sid("cmd") == s.uid
        assert m.resolve_sid("nonexist") is None

    def test_same_sid_rejected_while_active(self, stub_session_cls):
        m = SessionManager()
        m.create_session("cmd", ["cmd"])
        with pytest.raises(KeyError):
            m.create_session("cmd", ["cmd2"])

    def test_same_sid_reuse_after_remove_no_pollution(self, stub_session_cls):
        m = SessionManager()
        s1 = m.create_session("cmd", ["cmd"])
        m.remove_session(s1.uid)
        s2 = m.create_session("cmd", ["cmd"])
        # 两个会话 uid 不同，且旧会话已完全移除
        assert s1.uid != s2.uid
        assert m.get_by_uid(s1.uid) is None
        assert m.get_by_uid(s2.uid) is s2
        assert m.resolve_sid("cmd") == s2.uid

    def test_remove_by_sid_works(self, stub_session_cls):
        m = SessionManager()
        s = m.create_session("cmd", ["cmd"])
        m.remove_session("cmd")
        assert m.get_session("cmd") is None
        assert s._stopped

    def test_natural_end_removes_sid_index(self, stub_session_cls):
        m = SessionManager()
        s = m.create_session("cmd", ["cmd"])
        # 模拟读者线程自然结束回调
        m._on_session_ended(s)
        assert m.get_session("cmd") is None
        assert m.resolve_sid("cmd") is None
        assert m.get_by_uid(s.uid) is None

    def test_callbacks_carry_uid_and_sid(self, stub_session_cls):
        m = SessionManager()
        events = []
        m.set_on_session_created(lambda uid, sid: events.append(("created", uid, sid)))
        m.set_on_session_removed(
            lambda uid, sid, code, err: events.append(("removed", uid, sid))
        )
        s = m.create_session("cmd", ["cmd"])
        m.remove_session(s.uid)
        assert ("created", s.uid, "cmd") in events
        assert ("removed", s.uid, "cmd") in events

    def test_list_sessions_includes_uid(self, stub_session_cls):
        m = SessionManager()
        s = m.create_session("cmd", ["cmd"])
        rows = m.list_sessions()
        assert rows[0]["uid"] == s.uid
        assert rows[0]["id"] == "cmd"

    def test_start_failure_cleans_registry(self, stub_session_cls, monkeypatch):
        m = SessionManager()

        class _FailSession(_StubSession):
            def start(self):
                raise RuntimeError("boom")

        monkeypatch.setattr("src.session.manager.Session", _FailSession)
        with pytest.raises(RuntimeError):
            m.create_session("cmd", ["cmd"])
        assert m.get_session("cmd") is None
        assert m.resolve_sid("cmd") is None
        assert len(m._sessions) == 0


# ── ConnectionContext ───────────────────────────────────────────

class TestConnectionContextUidKeying:
    def test_subscription_isolated_by_uid(self):
        ctx = WebSocketConnectionContext()
        ctx.add_subscription("uid-a")
        ctx.add_subscription("uid-b")
        assert ctx.subscribed_session_ids == {"uid-a", "uid-b"}
        ctx.remove_subscription("uid-a")
        assert ctx.subscribed_session_ids == {"uid-b"}

    def test_callbacks_isolated_by_uid(self):
        ctx = WebSocketConnectionContext()
        ctx.add_subscription("uid-a")
        ctx.add_subscription("uid-b")
        ctx.set_callbacks("uid-a", {"output": "cb-a"})
        ctx.set_callbacks("uid-b", {"output": "cb-b"})
        assert ctx.get_callbacks("uid-a")["output"] == "cb-a"
        assert ctx.get_callbacks("uid-b")["output"] == "cb-b"
        ctx.clear_callbacks("uid-a")
        assert ctx.get_callbacks("uid-a") == {}
        assert ctx.get_callbacks("uid-b")["output"] == "cb-b"

    def test_same_sid_reuse_does_not_collide(self):
        """同名 sid 先后两个会话（uid 不同）：订阅/回调互不串扰。"""
        ctx = WebSocketConnectionContext()
        ctx.add_subscription("uid-1")
        ctx.set_callbacks("uid-1", {"output": "old"})
        ctx.add_subscription("uid-2")
        ctx.set_callbacks("uid-2", {"output": "new"})
        assert ctx.get_callbacks("uid-1")["output"] == "old"
        assert ctx.get_callbacks("uid-2")["output"] == "new"
        ctx.remove_subscription("uid-1")
        assert "uid-2" in ctx.subscribed_session_ids

    def test_decoder_and_held_by_uid(self):
        ctx = WebSocketConnectionContext()
        ctx.set_decoder("uid-a", "decoder-a")
        assert ctx.get_decoder("uid-a") == "decoder-a"
        assert ctx.get_decoder("uid-b") is None
        obj = object()
        ctx.add_held_session("uid-a", obj)
        assert ctx.pop_held_session("uid-a") is obj
        assert ctx.pop_held_session("uid-a") is None

    def test_clear_all(self):
        ctx = WebSocketConnectionContext()
        ctx.add_subscription("uid-a")
        ctx.add_held_session("uid-a", object())
        ctx.set_callbacks("uid-a", {"output": "cb"})
        ctx.clear_all_subscriptions()
        assert ctx.subscribed_session_ids == set()
        assert ctx.get_callbacks("uid-a") == {}
        assert ctx.pop_held_session("uid-a") is None


# ── AdaptiveLockService ─────────────────────────────────────────

class TestAdaptiveLockUidKeying:
    def test_lock_isolated_by_uid(self):
        lock = AdaptiveLockService()
        lock.acquire("uid-1", "client-a")
        lock.acquire("uid-2", "client-b")
        assert lock.get_owner("uid-1") == "client-a"
        assert lock.get_owner("uid-2") == "client-b"
        # 释放 A 不影响 B
        lock.release("uid-1", "client-a")
        assert lock.get_owner("uid-1") is None
        assert lock.get_owner("uid-2") == "client-b"

    def test_same_sid_reuse_lock_not_transferred(self):
        lock = AdaptiveLockService()
        lock.acquire("uid-old", "client-a")
        # 同名新会话（新 uid）：锁状态独立，旧锁不继承
        assert lock.get_owner("uid-new") is None
        lock.acquire("uid-new", "client-b")
        assert lock.get_owner("uid-old") == "client-a"
        assert lock.get_owner("uid-new") == "client-b"


# ── HistoryStore ────────────────────────────────────────────────

class _FakeSession:
    """HistoryStore.archive_session 用最小桩。"""

    def __init__(self, sid, uid, command, start=1000.0, cols=100, rows=30):
        self.id = sid
        self.uid = uid
        self.command = command
        self.cols = cols
        self.rows = rows
        self.start_time = start
        self.exit_code = 0
        self.error_message = None
        self.encoding = "utf-8"
        self.mode = "pty"
        self.pty_type = "conpty"

        class _Buf:
            def get_slice(self, n):
                return b""

        self.output_buffer = _Buf()
        self._err_buf = None

    def export_screen_buffer(self):
        return {}

    def get_snapshot(self, keep_ansi=False):
        return ""

    def get_all_events(self):
        return []


class TestHistoryStoreUidArchive:
    @pytest.fixture
    def store(self, tmp_path):
        return HistoryStore(db_path=str(tmp_path / "test_history.db"))

    def test_same_sid_sessions_do_not_overwrite(self, store):
        a = _FakeSession("cmd", "uid-A", "cmd /c echo A", start=1000.0)
        b = _FakeSession("cmd", "uid-B", "cmd /c echo B", start=2000.0)
        assert store.archive_session(a, tag="history")
        assert store.archive_session(b, tag="history")
        rows = store.list_sessions()
        assert len(rows) == 2, "同名 sid 两条历史都应保留"
        uids = {r["uid"] for r in rows}
        assert uids == {"uid-A", "uid-B"}

    def test_detail_by_uid_returns_correct_record(self, store):
        a = _FakeSession("cmd", "uid-A", "cmd /c echo A", start=1000.0)
        b = _FakeSession("cmd", "uid-B", "cmd /c echo B", start=2000.0)
        store.archive_session(a)
        store.archive_session(b)
        da = store.get_session_detail("uid-A")
        db = store.get_session_detail("uid-B")
        assert da["command"] == "cmd /c echo A"
        assert db["command"] == "cmd /c echo B"
        # sid 回退查找返回最新一条
        dl = store.get_session_detail("cmd")
        assert dl["uid"] == "uid-B"

    def test_delete_by_uid_only_removes_target(self, store):
        a = _FakeSession("cmd", "uid-A", "cmd /c echo A", start=1000.0)
        b = _FakeSession("cmd", "uid-B", "cmd /c echo B", start=2000.0)
        store.archive_session(a)
        store.archive_session(b)
        assert store.delete_session("uid-A")
        rows = store.list_sessions()
        assert len(rows) == 1
        assert rows[0]["uid"] == "uid-B"

    def test_resubscribe_same_uid_replaces_own_record(self, store):
        """同一 uid 重复归档：替换自身记录（不产生重复）。"""
        a1 = _FakeSession("cmd", "uid-A", "cmd /c echo A1", start=1000.0)
        a2 = _FakeSession("cmd", "uid-A", "cmd /c echo A2", start=3000.0)
        store.archive_session(a1)
        store.archive_session(a2)
        rows = store.list_sessions()
        assert len(rows) == 1
        assert rows[0]["command"] == "cmd /c echo A2"


class TestHistoryStoreMigration:
    def test_legacy_schema_migrates_to_uid_pk(self, tmp_path):
        """旧库（id 主键）启动时自动迁移为 uid 主键，数据与子表保留。"""
        import sqlite3

        db = tmp_path / "legacy.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                command TEXT NOT NULL,
                pty_type TEXT NOT NULL,
                cols INTEGER DEFAULT 80,
                rows INTEGER DEFAULT 24,
                start_time REAL NOT NULL,
                end_time REAL,
                exit_code INTEGER,
                error_message TEXT,
                encoding TEXT DEFAULT 'utf-8',
                tag TEXT DEFAULT 'ended'
            );
            CREATE TABLE session_output (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                stream TEXT NOT NULL,
                data_gz BLOB NOT NULL,
                original_length INTEGER NOT NULL
            );
            CREATE TABLE session_screen (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                buffer_json_gz BLOB,
                snapshot_text TEXT
            );
            CREATE TABLE session_events (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                events_json TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO sessions (id,command,pty_type,start_time,end_time,exit_code) VALUES (?,?,?,?,?,?)",
            ("cmd", "cmd /c echo legacy", "conpty", 1000.0, 2000.0, 0),
        )
        conn.execute(
            "INSERT INTO session_output (session_id,stream,data_gz,original_length) VALUES (?,?,?,?)",
            ("cmd", "pty", b"gz-bytes", 9),
        )
        conn.commit()
        conn.close()

        store = HistoryStore(db_path=str(db))
        rows = store.list_sessions()
        assert len(rows) == 1
        assert rows[0]["id"] == "cmd"
        assert rows[0]["uid"].startswith("legacy-cmd-")
        # 子表数据随迁移保留（按 uid 可查）
        detail = store.get_session_detail(rows[0]["uid"])
        assert detail is not None
        assert detail["outputGzOriginalLen"] == 9

    def test_migration_idempotent(self, tmp_path):
        """迁移后再次打开不重复迁移。"""
        import sqlite3

        db = tmp_path / "legacy2.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, command TEXT NOT NULL, "
            "pty_type TEXT NOT NULL, cols INTEGER DEFAULT 80, rows INTEGER DEFAULT 24, "
            "start_time REAL NOT NULL, end_time REAL, exit_code INTEGER, "
            "error_message TEXT, encoding TEXT DEFAULT 'utf-8', tag TEXT DEFAULT 'ended')"
        )
        conn.execute(
            "INSERT INTO sessions (id,command,pty_type,start_time) VALUES ('x','echo x','conpty',1.0)"
        )
        conn.commit()
        conn.close()

        store1 = HistoryStore(db_path=str(db))
        store1.list_sessions()
        store2 = HistoryStore(db_path=str(db))
        rows = store2.list_sessions()
        assert len(rows) == 1
        assert rows[0]["uid"].startswith("legacy-x-")

    def test_partial_migration_children_rebuilt(self, tmp_path):
        """部分迁移中间态：sessions 已是 uid 主键但子表 FK 仍指向 id 时，
        再次初始化应重建子表并保留数据。"""
        import sqlite3

        db = tmp_path / "partial.db"
        conn = sqlite3.connect(str(db))
        # 模拟早期版本迁移产物：sessions uid 主键 + 子表 FK 指向 id
        conn.executescript("""
            CREATE TABLE sessions (
                uid TEXT PRIMARY KEY, id TEXT NOT NULL, command TEXT NOT NULL,
                pty_type TEXT NOT NULL, cols INTEGER DEFAULT 80, rows INTEGER DEFAULT 24,
                start_time REAL NOT NULL, end_time REAL, exit_code INTEGER,
                error_message TEXT, encoding TEXT DEFAULT 'utf-8', tag TEXT DEFAULT 'ended'
            );
            CREATE TABLE session_output (
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                stream TEXT NOT NULL, data_gz BLOB NOT NULL, original_length INTEGER NOT NULL
            );
        """)
        conn.execute(
            "INSERT INTO sessions (uid,id,command,pty_type,start_time,end_time) VALUES (?,?,?,?,?,?)",
            ("uid-legacy", "cmd", "echo", "conpty", 1000.0, 2000.0),
        )
        conn.execute(
            "INSERT INTO session_output (session_id,stream,data_gz,original_length) VALUES (?,?,?,?)",
            ("uid-legacy", "pty", b"gz", 9),
        )
        conn.commit()
        conn.close()

        store = HistoryStore(db_path=str(db))
        # 子表 FK 应已重建指向 uid，且数据保留
        conn2 = sqlite3.connect(str(db))
        fks = conn2.execute("PRAGMA foreign_key_list(session_output)").fetchall()
        conn2.close()
        assert any(fk[2] == "sessions" and fk[4] == "uid" for fk in fks), (
            f"子表 FK 应指向 sessions(uid)，实际 {fks}"
        )
        detail = store.get_session_detail("uid-legacy")
        assert detail is not None
        assert detail["outputGzOriginalLen"] == 9
