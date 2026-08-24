"""会话管理器 — SessionManager

管理所有 PTY 会话的创建、获取、列出、移除和批量停止。

身份模型：会话以 uid（uuid4，Session 构造时生成）为主键唯一索引；
sid（用户自定义名）经 _sid_index 映射到 uid，同一时刻一个 sid 只能对应
一个活跃会话（CLI 等旧调用方仍可按 sid 查找，web 层按 uid 操作）。
"""

import threading
from typing import Callable, Optional

from ..config.daemon import MAX_SESSIONS
from .session import Session
from ..logging import get_logger, bind, unbind

_logger = get_logger("pty-session")


class SessionManager:
    """会话管理器

    负责会话 CRUD 操作和生命周期管理，线程安全。
    """

    def __init__(self, history_store=None, plugin_registry=None):
        # 主注册表：uid → Session（uid 为唯一稳定标识，sid 可复用）
        self._sessions: dict[str, Session] = {}
        # sid → uid 索引（一个 sid 同一时刻最多对应一个活跃会话）
        self._sid_index: dict[str, str] = {}
        self._lock = threading.Lock()
        self._history_store = history_store
        self.plugin_registry = plugin_registry
        # set-default 全局默认配置：守护进程内存记忆（daemon 重启即清空）。
        # 影响之后新建会话的默认值；--default/显式参数仍按优先级覆盖。
        self._global_defaults: dict = {}
        self._global_defaults_lock = threading.Lock()
        self._on_session_created: Optional[Callable[[str, str], None]] = None
        self._on_session_removed: Optional[
            Callable[[str, str, Optional[int], Optional[str]], None]
        ] = None

    # ── set-default 全局默认（daemon 内存记忆） ──────────────

    def set_global_default(self, key: str, value) -> None:
        """设置全局默认配置（set-default 命令落点，仅内存，重启即清空）"""
        with self._global_defaults_lock:
            self._global_defaults[key] = value

    def get_global_defaults(self) -> dict:
        """返回全局默认配置副本（供请求处理时合并）"""
        with self._global_defaults_lock:
            return dict(self._global_defaults)

    def set_on_session_created(self, cb: Optional[Callable[[str, str], None]]):
        """设置会话创建回调（参数：uid, sid）"""
        self._on_session_created = cb

    def set_on_session_removed(
        self, cb: Optional[Callable[[str, str, Optional[int], Optional[str]], None]]
    ):
        """设置会话移除回调（参数：uid, sid, exit_code, error_message）"""
        self._on_session_removed = cb

    def create_session(
        self,
        session_id: str,
        command,
        encoding: Optional[str] = None,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        plugins: Optional[list] = None,
        cli_plugins: Optional[list] = None,
        mode: str = "pty",
    ) -> Session:
        if not session_id or not isinstance(session_id, str):
            raise ValueError("会话 ID 必须为非空字符串")
        _ctx_token = bind(session_id=session_id)
        try:
            _logger.debug(
                "create_session: sid=%r cmd=%r cwd=%r cols=%s rows=%s plugins=%s cli_plugins=%s mode=%s",
                session_id,
                command,
                cwd,
                cols,
                rows,
                plugins,
                cli_plugins,
                mode,
            )
            with self._lock:
                if session_id in self._sid_index:
                    _logger.warning(
                        "create_session: session already exists sid=%r", session_id
                    )
                    raise KeyError(f"会话 '{session_id}' 已存在")
                if len(self._sessions) >= MAX_SESSIONS:
                    _logger.warning(
                        "create_session: max sessions reached (%d)", MAX_SESSIONS
                    )
                    raise ValueError(
                        f"会话数已达上限 ({MAX_SESSIONS})，请先 kill 不需要的会话"
                    )
                s = Session(
                    session_id,
                    command,
                    encoding=encoding,
                    cwd=cwd,
                    env=env,
                    cols=cols,
                    rows=rows,
                    cli_plugins=cli_plugins,
                    mode=mode,
                    plugin_env=(
                        self.plugin_registry.environment
                        if self.plugin_registry is not None
                        else None
                    ),
                )
                self._sessions[s.uid] = s
                self._sid_index[session_id] = s.uid
            if plugins:
                self._attach_plugins(s, plugins)
            # 会话创建事件发布到 daemon 事件总线（session.created）
            self._publish_session_event(
                "session.created",
                {
                    "sessionId": s.id,
                    "uid": s.uid,
                    "command": (
                        command if isinstance(command, str) else " ".join(command)
                    ),
                    "mode": mode,
                },
            )
            s._publisher.add_on_end_callback(lambda sess: self._on_session_ended(sess))
            # 创建期预持有：把"create_session 返回 → 调用方首个 hold"之间的
            # 空窗并入持有。子进程在 start 期间快速退出时 reader 线程会走完
            # 结束生命周期并触发 release_components；若无预持有，缓冲在
            # handler 尚未持有会话的空窗内即被释放，handler 随后访问崩溃。
            # 预持有待调用方首个 hold() 进入时消费。
            s.pre_hold()
            _logger.info("create_session: starting sid=%r cmd=%r", session_id, command)
            try:
                s.start()
            except Exception:
                # start 失败：会话不会交接给 handler，撤销预持有归还计数
                # （stop 之后调用：stop 期间可能仍有线程访问组件）
                _logger.warning(
                    "create_session: start failed sid=%r, removing tombstone", session_id
                )
                with self._lock:
                    self._sessions.pop(s.uid, None)
                    if self._sid_index.get(session_id) == s.uid:
                        self._sid_index.pop(session_id, None)
                try:
                    s.stop()
                except Exception:
                    pass
                s.release_creation_hold()
                raise
            _logger.info(
                "create_session: started sid=%r uid=%s pty=%s",
                session_id,
                s.uid,
                s.pty_type,
            )
            if self._on_session_created:
                try:
                    self._on_session_created(s.uid, s.id)
                except Exception:
                    _logger.exception("on_session_created callback error")
            return s
        finally:
            unbind(_ctx_token)

    def _attach_plugins(self, session: Session, plugins: list) -> None:
        """按名解析并挂载插件到会话（未知插件名跳过并记日志，不影响会话）"""
        if self.plugin_registry is None:
            _logger.warning("插件系统未启用，忽略插件: %s", plugins)
            return
        instances = []
        for name in plugins:
            inst = self.plugin_registry.instantiate(name)
            if inst is None:
                _logger.warning("插件未加载，跳过: %s", name)
                continue
            instances.append(inst)
        if instances:
            session.plugin_host.attach_many(instances)

    def match_auto_load(self, command, cwd, env) -> list:
        """按 exec 请求字段匹配自动加载条件，返回命中插件名列表"""
        if self.plugin_registry is None:
            return []
        return self.plugin_registry.match_auto_load(command, cwd, env)

    def _publish_session_event(self, topic: str, payload: dict) -> None:
        """发布会话事件到插件事件总线（插件系统未启用时静默跳过）"""
        if self.plugin_registry is None:
            return
        try:
            self.plugin_registry.environment.events.publish(
                topic, payload, source="manager"
            )
        except Exception:
            _logger.exception("会话事件发布失败: %s", topic)

    def _lookup(self, identifier: str) -> Optional[Session]:
        """按 uid 或 sid 查找会话（内部，须在持锁上下文中调用）"""
        s = self._sessions.get(identifier)
        if s is not None:
            return s
        uid = self._sid_index.get(identifier)
        if uid is not None:
            return self._sessions.get(uid)
        return None

    def get_session(self, identifier: str) -> Optional[Session]:
        """获取指定会话（兼容 uid 或 sid 两种标识）

        Args:
            identifier: 会话 uid 或 sid。

        Returns:
            Session 实例，不存在时返回 None。
        """
        with self._lock:
            return self._lookup(identifier)

    def get_by_uid(self, uid: str) -> Optional[Session]:
        """按 uid 精确获取会话（web 层主路径）"""
        with self._lock:
            return self._sessions.get(uid)

    def resolve_sid(self, sid: str) -> Optional[str]:
        """解析 sid 对应的活跃会话 uid，不存在时返回 None"""
        with self._lock:
            return self._sid_index.get(sid)

    def list_sessions(self) -> list:
        """列出所有会话（含已结束但未移除的）

        Returns:
            dict 列表，每项包含 id/uid/command/running/startTime 字段。
        """
        with self._lock:
            return [
                {
                    "id": s.id,
                    "uid": s.uid,
                    "command": (
                        s.command if isinstance(s.command, str) else " ".join(s.command)
                    ),
                    "running": s.running,
                    "startTime": s.start_time,
                }
                for s in self._sessions.values()
            ]

    def _on_session_ended(self, session):
        """会话自然结束时的回调：移除活跃列表、释放资源、归档、广播事件

        注意：此回调在读者线程（notify_end 同步广播）内执行，
        session.stop 已支持由当前线程调用（见 threads.Threads.stop）。
        """
        with self._lock:
            if session.uid not in self._sessions:
                return  # 已被 remove_session 处理
            self._sessions.pop(session.uid, None)
            if self._sid_index.get(session.id) == session.uid:
                self._sid_index.pop(session.id, None)

        # 释放会话资源（含沙箱进程），避免自然结束的会话泄漏
        try:
            session.stop()
            _logger.info("会话 '%s' 自然结束，资源已释放", session.id)
        except Exception as e:
            _logger.warning("会话 '%s' 自然结束释放资源时异常: %s", session.id, e)

        if self._history_store:
            try:
                self._history_store.archive_session(session, tag="ended")
            except Exception as e:
                _logger.warning("归档会话 '%s' 时异常: %s", session.id, e)

        if self._on_session_removed:
            try:
                self._on_session_removed(
                    session.uid, session.id, session.exit_code, session.error_message
                )
            except Exception:
                _logger.exception("on_session_removed callback error")

        # 最终移除：断开会话组件循环引用，让对象图可被引用计数立即回收
        session.release_components()

    def remove_session(self, identifier: str):
        """移除并停止指定会话（移除前持久化到历史）

        Args:
            identifier: 会话 uid 或 sid。
        """
        import time as _time

        _t0 = _time.monotonic()
        _logger.info("remove_session: id=%r", identifier)
        with self._lock:
            s = self._lookup(identifier)
            if s is not None:
                self._sessions.pop(s.uid, None)
                if self._sid_index.get(s.id) == s.uid:
                    self._sid_index.pop(s.id, None)
        if s:
            if self._history_store:
                try:
                    self._history_store.archive_session(s, tag="history")
                    _logger.debug(
                        "remove_session: archived sid=%r took %.3fs",
                        s.id,
                        _time.monotonic() - _t0,
                    )
                except Exception as e:
                    _logger.warning("持久化会话 '%s' 时异常: %s", s.id, e)
            _t1 = _time.monotonic()
            try:
                s.stop()
                _logger.info(
                    "remove_session: stopped sid=%r stop took %.3fs",
                    s.id,
                    _time.monotonic() - _t1,
                )
            except Exception as e:
                _logger.warning("移除会话 '%s' 时异常: %s", s.id, e)
            _t2 = _time.monotonic()
            if self._on_session_removed:
                try:
                    self._on_session_removed(
                        s.uid, s.id, s.exit_code, s.error_message
                    )
                except Exception:
                    _logger.exception("on_session_removed callback error")
            # 最终移除：断开会话组件循环引用，让对象图可被引用计数立即回收
            s.release_components()
            _logger.info(
                "remove_session: done sid=%r total %.3fs on_removed %.3fs",
                s.id,
                _time.monotonic() - _t0,
                _time.monotonic() - _t2,
            )

    def stop_all(self):
        """停止所有会话"""
        with self._lock:
            uids = list(self._sessions.keys())
        _logger.info("stop_all: stopping %d sessions", len(uids))
        for uid in uids:
            self.remove_session(uid)
