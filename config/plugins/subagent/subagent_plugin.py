"""子代理 daemon 侧 + CLI 侧插件（通用多 agent）

设计原则：
- 基础设施是公共的：exec 流程、回合监控、响应装饰、CLI 命令注册
- 解析器是独有的：每个 agent 走自己的 parser（workbuddyparser / devinparser /
  opencodeparser / claudeparser）
- 不硬编码任何 agent：agent 差异全部声明在 agents.py 的 AgentSpec 注册表中，
  新增 agent 只需注册一条 AgentSpec，本文件无需改动

消息处理（双路径）：
- ExecHandler 路径（interactive）：CLI 发标准 exec 消息 + subagent 元数据，
  ExecHandler 创建会话后调 on_session_created，后台初始化（启动检测/信任
  确认/回合监控），exec 响应立即返回 spawned（不阻塞）。**推荐路径。**
- handle_message 路径（oneshot）：CLI 发 <agent>_exec 消息（oneshot=True），
  插件创建 subprocess 会话并轮询等待退出。**仅 oneshot 使用。**

响应装饰（decorate_response）：
- read：子代理会话时附加 live_state（snapshot）或替换为结构化消息（message）
- list：追加 subagent_status 字段（按 agent 选 screen parser）

CLI 钩子（before_request / render_response）：
- read：将 -l 注入 pluginOptions，供 decorate_response 读取消息数

监控（TurnMonitor）：
- 后台线程轮询屏幕 ai_status，event 驱动回合完成/卡权限检测
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import uuid
from typing import Optional

from src.plugins.base import Plugin
from src.protocol.response import Response
from src.config.common import DATA_DIR

from .agents import AGENTS, MESSAGE_TYPE_TO_AGENT, AgentSpec
from .parser_loader import import_parser

_logger = None


def _summarize_args(args) -> str:
    """工具调用参数摘要：取关键字段，限制长度"""
    if not args:
        return ""
    if isinstance(args, dict):
        parts = []
        for key in ("command", "file_path", "pattern", "path", "subject", "question"):
            if key in args and args[key]:
                parts.append(str(args[key])[:120])
                break
        if not parts:
            parts = [str(v)[:120] for v in list(args.values())[:2]]
        return ": " + " ".join(parts)
    return ": " + str(args)[:120]


def _get_logger():
    global _logger
    if _logger is None:
        import logging
        _logger = logging.getLogger("pty-daemon")
    return _logger


# ── 导出目录（export 型 agent 的消息文件路径） ─────────────

_SUBAGENT_EXPORT_DIR = os.path.join(DATA_DIR, "plugins", "subagent", "exports")

# 未知 agent 的渲染兜底显示名
_DEFAULT_DISPLAY_NAME = "SubAgent"


def _ensure_export_dir() -> str:
    """确保导出目录存在，返回路径"""
    os.makedirs(_SUBAGENT_EXPORT_DIR, exist_ok=True)
    return _SUBAGENT_EXPORT_DIR


def _cleanup_data_dir(data_dir: str) -> None:
    """清理独立数据目录（spawn 失败/会话结束时调用，忽略错误）"""
    if not data_dir:
        return
    try:
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)
    except Exception:
        pass


def _cleanup_stale_data_dirs(agent_id: str, max_age_s: float = 3600) -> None:
    """清理过期的独立数据目录（每次 spawn 时调用，防累积）

    ended 会话读消息仍需 data_dir（保留到过期时间），过期后无引用价值。
    """
    try:
        import glob
        pattern = os.path.join(
            tempfile.gettempdir(), "subagent-" + agent_id + "-*")
        now = time.time()
        for d in glob.glob(pattern):
            try:
                if now - os.path.getmtime(d) > max_age_s:
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
    except Exception:
        pass


def _resolve_command(command: list, spec: AgentSpec, program_path: str = "") -> list:
    """解析命令可执行文件路径：--program-path > 环境变量 > PATH

    Args:
        command: 原始命令列表
        spec: AgentSpec（含 command_path_env 声明）
        program_path: 用户通过 --program-path 显式指定的路径

    Returns:
        解析后的完整命令列表

    Raises:
        FileNotFoundError: 所有路径都找不到
    """
    if not command:
        return command
    exe = command[0]

    # 1. --program-path 显式指定
    if program_path:
        if os.path.isfile(program_path):
            return [program_path] + command[1:]
        raise FileNotFoundError(
            f"--program-path 指定的路径不存在: {program_path}"
        )

    # 2. 环境变量（如 OPENCODE_PATH）
    if spec.command_path_env:
        env_path = os.environ.get(spec.command_path_env, "")
        if env_path:
            cand = os.path.join(env_path, exe) if os.path.isdir(env_path) else env_path
            if os.path.isfile(cand):
                return [cand] + command[1:]
            raise FileNotFoundError(
                f"环境变量 {spec.command_path_env}={env_path} 中找不到可执行文件 {exe}"
            )

    # 3. PATH 查找
    found = shutil.which(exe)
    if found:
        return [found] + command[1:]

    # 4. 全部找不到 → 报错
    raise FileNotFoundError(
        f"找不到可执行文件: {exe}"
        + (f"。请设置环境变量 {spec.command_path_env} 或使用 --program-path 指定路径" if spec.command_path_env else "")
    )


class SubagentPlugin(Plugin):
    """子代理插件 —— 进程级消息处理（通用多 agent）"""

    def on_init(self, ctx) -> None:
        # 会话 → TurnMonitor 映射（exec interactive 启动，session.ended 清理）
        self._monitors = {}
        # 子代理会话 sid → (agent, uid) 映射（即使 ended 也能识别，供 decorate_response 用）
        self._subagent_sessions: dict = {}
        # 子代理会话 sid → 独立数据目录（ended 后 CLI 读消息仍需 data_dir）
        self._subagent_data_dirs: dict = {}
        # CLI 侧缓存：最近一次 read 请求的 --rf 和 -l 值（供 render_response 用）
        self._last_rf = "snapshot"
        self._last_lines = 5
        self._last_lines_given = False

    def handle_message(self, ctx, msg: dict):
        mtype = msg.get("type", "")
        agent_id = MESSAGE_TYPE_TO_AGENT.get(mtype)
        if agent_id is None:
            return None
        try:
            return self._exec(ctx, msg, AGENTS[agent_id])
        except Exception as e:
            _get_logger().exception("subagent 插件处理异常: %s", e)
            return Response.error(str(e))

    def on_bus_event(self, ctx, event) -> None:
        """订阅 session.ended：清理对应子代理的 TurnMonitor"""
        if event.topic != "session.ended":
            return
        sid = (event.payload or {}).get("sessionId", "")
        if not sid:
            return
        monitor = self._monitors.pop(sid, None)
        if monitor is not None:
            monitor.stop()
            _get_logger().info("子代理会话结束，停止监控 sid=%s", sid)

    # ── exec ────────────────────────────────────────────────

    def on_session_created(self, ctx, session, msg) -> None:
        """ExecHandler 创建会话后回调：标记子代理会话 + 启动后台初始化

        子代理 exec 走通用流程（CLI 发标准 exec 消息 + msg["subagent"] 元数据，
        ExecHandler 创建会话后调此钩子）。本插件在此标记会话归属；interactive
        的启动检测/信任确认/回合监控放入后台线程（ExecHandler 立即返回 spawned
        响应，避免阻塞导致首回合通知被 exec 响应自动消费）。
        """
        sub = msg.get("subagent") or {}
        agent_id = sub.get("agent", "")
        if not agent_id or agent_id not in AGENTS:
            return
        spec = AGENTS[agent_id]
        sid = getattr(session, "id", "") or ""
        oneshot = bool(sub.get("oneshot", False))
        prompt = sub.get("prompt", "") or ""
        uid = sub.get("uid", "") or ""
        data_dir = sub.get("data_dir", "") or ""
        log = _get_logger()

        # 标记该会话为子代理会话（供 decorate_response 识别；sid→(agent,uid) 记录，
        # 即使会话 ended 也能识别）
        session._subagent_agent = spec.agent_id
        session.add_common_mark("subagent")  # 来源标记：子代理创建
        if uid:
            session._subagent_uid = uid
        session._subagent_prompt = prompt
        session._subagent_data_dir = data_dir
        self._subagent_sessions[sid] = (spec.agent_id, uid or "")
        if data_dir:
            self._subagent_data_dirs[sid] = data_dir

        if oneshot:
            # subprocess 模式 stdin=PIPE 保持打开，部分 agent（如 opencode run）
            # 检测到 stdin 打开后等待输入不退出，关闭 stdin 使其正常执行并退出
            try:
                session._pty._proc.stdin.close()
            except Exception:
                pass
            return  # oneshot 阻塞等待由 ExecHandler 处理

        # interactive：后台初始化（发现 → 启动检测 → 信任确认 → 回合监控），
        # 不阻塞 exec 响应（ExecHandler 立即返回 spawned，避免通知被消费）
        import threading
        threading.Thread(
            target=self._init_session_async,
            args=(ctx, session, spec, prompt, uid),
            name="subagent-init-%s" % sid,
            daemon=True,
        ).start()

    def _init_session_async(self, ctx, session, spec: AgentSpec,
                            prompt: str, uid: str) -> None:
        """后台初始化子代理会话：立即启动回合监控 → 发现 → 信任确认 → 启动检测

        与旧同步路径（_exec）等价，但异步执行不阻塞 exec 响应：
        - 回合监控**立即启动**（spawn 后马上轮询）：模型快速回复时能捕获
          busy→idle 转换发布真实 turn_complete；_seen_busy 门控保证信任
          对话框等启动阶段不会产生假阳性。
        - 异步发现会话 ID（不阻塞）
        - **信任对话框自动确认优先**：对话框是启动第一道关卡，先确认它
          claude 才进欢迎页。若放到启动检测（25s 超时）之后，对话框期间
          欢迎页标记不出现，启动检测会拖满 25s 才轮到 8s 信任窗口，用户
          长时间看到对话框未自动确认。
        - 启动阶段检测模型错误（失败则移除会话，monitor 随 session.ended 清理）
        异常隔离：只记日志，不中断 daemon 主流程。
        """
        sid = getattr(session, "id", "") or ""
        log = _get_logger()
        try:
            # 回合监控立即启动（不等待启动检测/信任确认）：
            # 若等 25s+8s 后才启动，模型快速回复（<33s）已完成，
            # 监控看不到 busy→idle，stuck 检测会误报"程序未反应"
            self._start_monitor(session, ctx, spec)

            # 异步发现会话 ID（不阻塞 spawn 响应）
            if not uid and (spec.discover_log_relpath or spec.discover_fn):
                self._start_discover_async(sid, session, prompt, spec)

            # 信任对话框自动确认（优先于启动检测：对话框期间欢迎页标记
            # 不出现，若后置会等 25s 启动检测超时才开始确认）
            if spec.trust_dialog:
                self._auto_confirm_trust(session, spec,
                                         timeout=spec.trust_dialog_timeout)

            # 启动阶段检测模型错误（失败则移除会话，并发布错误通知）
            err = self._detect_startup_errors(session, spec)
            if err:
                log.warning("子代理启动失败: sid=%s agent=%s err=%s",
                            sid, spec.agent_id, err)
                # 发布错误通知（让 wait/notice 能收到启动失败信息）
                try:
                    nm = getattr(ctx, "notify_manager", None)
                    if nm is None:
                        registry = getattr(getattr(ctx, "manager", None), "plugin_registry", None)
                        if registry is not None:
                            env = getattr(registry, "environment", None)
                            if env is not None:
                                nm = getattr(env, "notify_manager", None)
                    if nm is not None:
                        nm.publish({
                            "commandType": "subagent_startup_failed",
                            "sessionId": sid,
                            "triggerReturnReason": "startup_failed",
                            "detail": f"子代理 {spec.display_name} 启动失败: {err}",
                            "outputStream": session.get_snapshot(keep_ansi=False) or "",
                        })
                except Exception:
                    log.exception("发布启动失败通知异常: sid=%s", sid)
                # remove_session 内部会 stop + 归档 history（不能先手动 stop：
                # stop 会触发自然结束归档 tag=ended，随后 remove_session 变 no-op）
                try:
                    ctx.manager.remove_session(sid)
                except Exception:
                    pass
                return

            # 信任对话框自动确认（monitor 已启动但 _seen_busy 门控防假阳性）
            if spec.trust_dialog:
                self._auto_confirm_trust(session, spec,
                                         timeout=spec.trust_dialog_timeout)
        except Exception:
            log.exception("子代理后台初始化异常: sid=%s agent=%s", sid, spec.agent_id)

    # ── exec（oneshot 专用路径；interactive 走 ExecHandler + on_session_created） ──

    def _exec(self, ctx, msg: dict, spec: AgentSpec) -> dict:
        """oneshot 子代理执行（handle_message 路由，CLI --oneshot 专用）

        interactive 已改走 ExecHandler 通用流程（标准 exec 消息 + subagent
        元数据 → on_session_created 后台初始化），本路径只处理 oneshot：
        创建 subprocess 会话 → 关闭 stdin → 轮询等待退出并返回输出。
        """
        sid = msg.get("id", "")
        if not sid:
            return Response.error("'id' is required")
        prompt = msg.get("prompt", "")
        if not prompt:
            return Response.error("'prompt' is required")
        cwd = msg.get("cwd") or None
        model = msg.get("model") or None
        oneshot = msg.get("oneshot", False)
        if not oneshot:
            return Response.error("interactive 模式请走 exec 命令（--plugin 会话流程）")

        # 会话 ID / 导出路径策略（声明在 AgentSpec）：
        # - 有 session_id_arg（codebuddy）：显式随机 uuid，消息文件可定位
        # - 有 session_id_arg_oneshot_only（opencode）：仅 oneshot 使用，interactive 用 discover
        # - 有 export_arg（devin）：生成确定性导出路径，spawn 时加 --export
        uid = None
        if spec.session_id_arg:
            if not spec.session_id_arg_oneshot_only or oneshot:
                uid = str(uuid.uuid4())
        if uid is None and spec.export_arg:
            export_dir = _ensure_export_dir()
            uid = os.path.join(export_dir, f"{uuid.uuid4()}.json")

        command = spec.build_command(prompt=prompt, model=model, uid=uid, oneshot=oneshot)
        # 解析命令路径：--program-path > 环境变量 > PATH
        program_path = msg.get("programPath", "") or ""
        try:
            command = _resolve_command(command, spec, program_path)
        except FileNotFoundError as e:
            return Response.error(str(e))
        # 声明了 wrap_shell 的 agent（如 Windows 下 .cmd shim 的 cbc）需经 shell 包装；
        # 其他平台/命令不包装，避免 shell 对 prompt 引号重新解析
        if spec.wrap_shell:
            from src.config.common import IS_WINDOWS
            if IS_WINDOWS:
                from src.common.shells import wrap_command
                command = wrap_command(command, spec.wrap_shell)
        log = _get_logger()
        log.info("subagent exec: sid=%s agent=%s cmd=%s oneshot=%s",
                 sid, spec.agent_id, command, oneshot)

        if ctx.manager is None:
            return Response.error("manager not available")

        # check_ended_session：同 sid 已结束则拒绝（与 ExecHandler 通用流程一致）
        from src.execution.utils import check_ended_session
        if check_ended_session(ctx.manager, sid):
            return Response.error(f"Session '{sid}' ended, kill and re-exec to restart")

        # 数据目录隔离（声明 data_dir_env 的 agent，如 opencode）：
        # 每个 spawn 用独立数据目录（环境变量指向），日志/数据库天然隔离，
        # 并发安全且屏幕无输出污染；discover/消息读取用同一 data_dir。
        data_dir = ""
        env_extra = {"TERM": "xterm-256color"}
        if spec.data_dir_env:
            try:
                # 先清理过期隔离目录（ended 会话读消息保留 <1h 后无引用价值）
                _cleanup_stale_data_dirs(spec.agent_id)
                data_dir = tempfile.mkdtemp(prefix="subagent-" + spec.agent_id + "-")
                env_extra[spec.data_dir_env] = data_dir
            except Exception as e:
                log.warning("data_dir 隔离创建失败，回退默认目录: %s", e)
                data_dir = ""

        # 创建 subprocess 会话（oneshot 专用：-p 纯文本输出，无终端，拿全量 stdout）
        try:
            session = ctx.manager.create_session(
                session_id=sid,
                command=command,
                cwd=cwd,
                env=env_extra,
                mode="subprocess",
            )
        except (KeyError, ValueError) as e:
            if data_dir:
                _cleanup_data_dir(data_dir)
            return Response.error(str(e))

        # 标记该会话为子代理会话（供 decorate_response 识别；sid→(agent,uid) 记录，
        # 即使会话 ended 也能识别）
        session._subagent_agent = spec.agent_id
        session.add_common_mark("subagent")  # 来源标记：子代理创建
        if uid:
            session._subagent_uid = uid
        # 存储 prompt 供 discover 和惰性发现使用
        session._subagent_prompt = prompt
        # 存储独立数据目录（discover 日志读取 + 消息读取传参）
        session._subagent_data_dir = data_dir
        self._subagent_sessions[sid] = (spec.agent_id, uid or "")
        if data_dir:
            self._subagent_data_dirs[sid] = data_dir

        # subprocess 模式 stdin=PIPE 保持打开，部分 agent（如 opencode run）
        # 检测到 stdin 打开后等待输入不退出，关闭 stdin 使其正常执行并退出
        try:
            session._pty._proc.stdin.close()
        except Exception:
            pass
        return self._wait_and_return(session, sid, spec)

    def _discover_session(self, session, prompt: str, spec: AgentSpec,
                          timeout: float = 30.0) -> Optional[str]:
        """发现会话 ID：优先独立日志文件（并发安全、无污染），其次 discover_fn（DB 查询）

        用于 interactive 模式无显式 uid 的 agent（如 opencode）：
        spawn TUI 后自动创建会话，从独立日志文件提取 session.id。
        """
        import re
        # 方式 0：独立数据目录日志文件（agent 日志写入独立路径，并发安全、无屏幕污染）
        data_dir = getattr(session, "_subagent_data_dir", "") or ""
        if spec.discover_log_relpath and data_dir:
            log_path = os.path.join(data_dir, spec.discover_log_relpath)
            pattern = re.compile(spec.discover_log_regex)
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if os.path.isfile(log_path):
                        with open(log_path, "r", errors="replace") as f:
                            content = f.read()
                        m = pattern.search(content)
                        if m:
                            _get_logger().info("子代理会话发现(日志文件): sid=%s session=%s",
                                               getattr(session, "id", ""), m.group(1))
                            return m.group(1)
                except Exception as e:
                    _get_logger().warning("%s log discover 异常: %s", spec.agent_id, e)
                time.sleep(0.5)
            return None

        # 方式 1：discover_fn（DB 查询，如 prompt 匹配）
        if not spec.discover_fn:
            return None
        try:
            locator = import_parser(spec.parser_agent, "adapters.session_locator")
            fn = getattr(locator, spec.discover_fn)
        except Exception as e:
            _get_logger().warning("%s discover 导入失败: %s", spec.agent_id, e)
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                sid = fn(prompt)
                if sid:
                    return sid
            except FileNotFoundError:
                pass
            except Exception as e:
                _get_logger().warning("%s discover 查询异常: %s", spec.agent_id, e)
            time.sleep(0.5)
        return None

    def _start_discover_async(self, sid: str, session, prompt: str,
                              spec: AgentSpec) -> None:
        """后台线程轮询发现会话 ID（不阻塞 spawn 响应）"""
        import threading

        def _run():
            found = self._discover_session(session, prompt, spec)
            if found:
                try:
                    session._subagent_uid = found
                except Exception:
                    pass
                entry = self._subagent_sessions.get(sid)
                if entry:
                    self._subagent_sessions[sid] = (entry[0], found)
                _get_logger().info("子代理会话发现: sid=%s agent=%s session=%s",
                                   sid, spec.agent_id, found)
            else:
                _get_logger().warning("子代理会话发现超时 sid=%s agent=%s", sid, spec.agent_id)

        t = threading.Thread(target=_run, name=f"subagent-discover-{sid}", daemon=True)
        t.start()

    def _start_monitor(self, session, ctx, spec: AgentSpec) -> None:
        """为会话启动 TurnMonitor（发布回合事件到 EventBus + 通知）"""
        log = _get_logger()
        try:
            from .turn_monitor import TurnMonitor
            # ctx 可能是 HandlerContext（ExecHandler）或 ProcessPluginContext
            # （handle_message），events/notify_manager 优先从 ctx 读取，
            # 回退到 plugin_registry.environment
            events = getattr(ctx, "events", None)
            notify_mgr = getattr(ctx, "notify_manager", None)
            if events is None and notify_mgr is None:
                registry = getattr(getattr(ctx, "manager", None), "plugin_registry", None)
                if registry is not None:
                    env = getattr(registry, "environment", None)
                    if env is not None:
                        events = getattr(env, "events", None) or events
                        notify_mgr = getattr(env, "notify_manager", None) or notify_mgr
            log.info("TurnMonitor 启动: sid=%s agent=%s events=%s notify=%s", session.id,
                     spec.agent_id, events is not None, notify_mgr is not None)
            monitor = TurnMonitor(session, events, notify_manager=notify_mgr,
                                  agent=spec.parser_agent, display_name=spec.display_name)
            monitor.start()
            self._monitors[session.id] = monitor
        except Exception as e:
            log.warning("TurnMonitor 启动失败 sid=%s: %s", session.id, e)

    def _detect_startup_errors(self, session, spec: AgentSpec, timeout: float = 25.0) -> str | None:
        """启动阶段轮询屏幕检测模型错误；返回错误消息，无错误返回 None

        先等屏幕出现内容（agent 启动 + 模型检查），再检测模型无效提示
        （关键词声明在 AgentSpec.startup_error_hints）。
        错误提示与正常欢迎页都含输入框，故只在明确的欢迎页标记出现且无错误时提前返回。
        """
        import time as _time
        started = _time.time()
        while _time.time() - started < timeout:
            try:
                snapshot = session.get_snapshot(keep_ansi=False)
                if snapshot.strip():
                    if not session.running:
                        return "子代理进程提前退出"
                    for hint in spec.startup_error_hints:
                        if hint in snapshot:
                            return "模型不存在或不可用：请使用 --model 指定有效模型"
                    # 明确欢迎页（品牌框出现）且无错误 → 正常就绪
                    if all(mark in snapshot for mark in spec.welcome_marks):
                        return None
            except Exception:
                pass
            _time.sleep(0.3)
        return None

    def _auto_confirm_trust(self, session, spec: AgentSpec, timeout: float = 5.0) -> None:
        """轮询屏幕检测信任对话框并自动确认（检测文本/按键模板按 AgentSpec 声明）

        各 agent 信任对话框形态不同：
        - codebuddy：默认选中 "Trust folder only"，发回车确认即可
        - claude：默认选中 "No, exit"，需先 ↓ 选信任再回车（可能还需 bypass 确认）
        按键模板经 expand_control_characters_full 展开为分段写入（pause_offsets），
        与 CLI 侧 {down}/{enter} 模板语义一致，避免控制序列一次性写入被 TUI 误解析。
        超时未出现则忽略（已信任过）。
        """
        import time as _time
        from src.input.text import expand_control_characters_full

        check = spec.trust_dialog_check
        keys = spec.trust_dialog_keys
        # 展开按键模板（如 "{down}{enter}{enter}" → "\x1b[B\r\r" + pauses=[3,4,5]）
        text, pauses = expand_control_characters_full(keys, enter_eol="\r")
        started = _time.time()
        while _time.time() - started < timeout:
            try:
                snapshot = session.get_snapshot(keep_ansi=False)
                if check in snapshot:
                    _get_logger().info(
                        "检测到信任对话框，自动确认 sid=%s agent=%s keys=%r",
                        session.id, spec.agent_id, keys,
                    )
                    session.write_input(text, pause_offsets=pauses)
                    return
            except Exception:
                pass
            _time.sleep(0.3)

    def _wait_and_return(self, session, sid: str, spec: AgentSpec) -> dict:
        """等待子进程退出，返回输出"""
        poll_interval = 0.2
        max_wait = 300  # 5 分钟超时
        waited = 0
        while session.running and waited < max_wait:
            time.sleep(poll_interval)
            waited += 1

        output = session.get_output()  # subprocess 模式无终端，全量输出缓冲
        exit_code = session.exit_code
        status = "completed" if exit_code == 0 else "failed"
        log = _get_logger()
        log.info("subagent exec done: sid=%s agent=%s exit=%s", sid, spec.agent_id, exit_code)

        return {
            "commandType": spec.message_type,
            "sessionId": sid,
            "status": status,
            "exitCode": exit_code,
            "outputStream": output,
            "duration_ms": int(waited * poll_interval * 1000),
        }

    # ── read 辅助（decorate_response 用） ──────────────────

    def _spec_for_session(self, session) -> Optional[AgentSpec]:
        """从 session 获取 AgentSpec；非子代理会话或无注册返回 None"""
        agent_id = getattr(session, "_subagent_agent", "") or ""
        return AGENTS.get(agent_id)

    def _parse_live_state(self, session) -> dict | None:
        """用 agent 对应 screen parser 解析当前屏幕快照 → LiveState"""
        spec = self._spec_for_session(session)
        if spec is None:
            return None
        try:
            screen_mod = import_parser(spec.parser_agent, "adapters.screen")
        except Exception as e:
            _get_logger().warning("%s screen 导入失败: %s", spec.agent_id, e)
            return None
        try:
            vt_text = session.get_snapshot(keep_ansi=True)
            if not vt_text:
                return None
            live = screen_mod.parse_screen_snapshot(vt_text)
            result = {
                "ai_status": live.ai_status,
                "input_text": live.input_text,
                "screen_type": live.screen_type,
                "model_display": live.model_display,
                "cwd_display": live.cwd_display,
            }
            # agent 特有字段（声明在 AgentSpec.live_state_fields）
            for extra in spec.live_state_fields:
                val = getattr(live, extra, None)
                if val:
                    result[extra] = val
            return result
        except Exception as e:
            _get_logger().warning("live_state 解析失败: %s", e)
            return None

    def _recent_messages(self, session, lines: int) -> list:
        """从 agent 对应消息存储解析最近 N 条消息（靠 session._subagent_uid 定位）"""
        spec = self._spec_for_session(session)
        if spec is None:
            return []
        uid = getattr(session, "_subagent_uid", "") if session is not None else ""
        # uid 未就绪（异步发现未完成）且声明了 discover：惰性同步发现一次
        if not uid and (spec.discover_log_relpath or spec.discover_fn) and session is not None:
            prompt = getattr(session, "_subagent_prompt", "") or ""
            uid = self._discover_session(session, prompt, spec, timeout=3.0) or ""
            if uid:
                try:
                    session._subagent_uid = uid
                except Exception:
                    pass
        return self._recent_messages_by_uid(uid, lines, spec, session)

    def _recent_messages_by_uid(self, uid: str, lines: int, spec: AgentSpec, session=None,
                                _data_dir: str = "") -> list:
        """从 agent 对应消息存储解析最近 N 条消息

        消息格式与定位方式全部声明在 AgentSpec：
        - uid_is_path（devin）：uid 即确定性导出文件路径，直接读取
        - 否则（codebuddy）：uid 为会话 ID，经 locator_fn 定位消息文件
        - msg_loader_fn：消息加载函数
        - 数据目录隔离（data_dir_env）：定位/加载函数传独立 data_dir
        """
        log = _get_logger()
        try:
            msg_parser = import_parser(spec.parser_agent, "adapters." + spec.messages_adapter)
        except Exception as e:
            log.warning("%s 导入失败: %s", spec.agent_id, e)
            return []
        if not uid:
            return []
        # 独立数据目录（spawn 时创建的隔离目录；空=默认存储位置）
        data_dir = _data_dir or ""
        if not data_dir and session is not None:
            data_dir = getattr(session, "_subagent_data_dir", "") or ""
        # parser 期望的 data_dir = 隔离根 + agent 数据子目录（如 opencode 数据
        # 在 $XDG_DATA_HOME/opencode/，opencode.db 位于其下）
        if data_dir and spec.data_dir_subdir:
            data_dir = os.path.join(data_dir, spec.data_dir_subdir)

        # 定位消息文件：uid_is_path 直接读导出文件；否则经 locator 按会话 ID 定位
        if spec.uid_is_path:
            path = uid
            log.info("读取 %s 导出文件: %s", spec.agent_id, path)
            if not os.path.isfile(path):
                log.info("%s 导出文件不存在（%s），可能尚未写入", spec.agent_id, path)
                return []
        else:
            try:
                locator = import_parser(spec.parser_agent, "adapters.session_locator")
                if spec.data_dir_env:
                    path = getattr(locator, spec.locator_fn)(uid, data_dir)
                else:
                    path = getattr(locator, spec.locator_fn)(uid)
                log.info("读取 %s: %s", spec.agent_id, path)
            except FileNotFoundError:
                log.info("%s 文件不存在（uid=%s），可能尚未写入", spec.agent_id, uid)
                return []
            except Exception as e:
                log.warning("%s 定位失败: %s", spec.agent_id, e)
                return []

        # 解析消息（加载函数返回顺序按 AgentSpec.loader_meta_first 区分：
        # False → (entities, meta)，True → (meta, entities)）
        try:
            if spec.data_dir_env:
                loaded = getattr(msg_parser, spec.msg_loader_fn)(path, data_dir)
            else:
                loaded = getattr(msg_parser, spec.msg_loader_fn)(path)
            if spec.loader_meta_first:
                _meta, msg_entities = loaded
            else:
                msg_entities, _meta = loaded
        except Exception as e:
            log.warning("%s 解析失败: %s", spec.agent_id, e)
            return []

        result = []
        try:
            start = max(0, len(msg_entities) - lines)
            for idx, m in enumerate(msg_entities[start:], start=start):
                abs_idx = idx + 1
                role = m.role
                for item in m.items:
                    if item.type == "text":
                        result.append({"index": abs_idx, "role": role, "content": item.text or ""})
                    elif item.type == "thinking":
                        text = item.text or ""
                        parts = [p for p in text.split("\n") if p.strip()]
                        count = len(parts)
                        result.append({"index": abs_idx, "role": role, "content": f"[thinking]" + (f" {count} 段" if count else "")})
                    elif item.type == "tool_use" and item.tool_use:
                        t = item.tool_use
                        args_summary = _summarize_args(t.input)
                        result.append({"index": abs_idx, "role": role, "content": f"[tool] {t.name}{args_summary}"})
                    elif item.type == "tool_result" and item.tool_result:
                        tr = item.tool_result
                        icon = "✓" if tr.success else "✗"
                        # 各 parser ToolResult 字段可能不同（如 exit_code 非必有）
                        exit_code = getattr(tr, "exit_code", None)
                        status = f"exit {exit_code}" if exit_code is not None else ""
                        result.append({"index": abs_idx, "role": role, "content": f"[tool_result] {tr.name} {icon}" + (f" {status}" if status else "")})
        except Exception as e:
            log.warning("_recent_messages 处理异常: %s", e)
            return []
        return result

    # ── 响应装饰 ───────────────────────────────────────────

    def decorate_response(self, ctx, resp: dict) -> dict | None:
        """装饰内置命令的响应（按 manifest.decorateTypes 匹配）

        支持：
        - list：给子代理会话补 subagent_status 字段
        - read：子代理会话时附加 live_state（snapshot）或替换为结构化消息（message）
        - send：子代理会话时标记 feedback_pending + subagent_send（CLI 简化渲染）
        """
        ctype = resp.get("commandType")
        if ctype == "list":
            return self._decorate_list(ctx, resp)
        if ctype == "read":
            return self._decorate_read(ctx, resp)
        if ctype == "send":
            return self._decorate_send(ctx, resp)
        return None

    def _decorate_list(self, ctx, resp: dict) -> dict | None:
        """list：给子代理会话补 subagent_status"""
        sessions = resp.get("sessions")
        if not sessions:
            return None
        manager = ctx.manager
        if manager is None:
            return None
        modified = False
        for s in sessions:
            sid = s.get("id")
            if not sid:
                continue
            # 跳过 ended 历史会话（running=False），避免同名 sid 查活跃 session 误装饰
            if not s.get("running"):
                continue
            session = manager.get_session(sid)
            if session is None:
                continue
            spec = self._spec_for_session(session)
            if spec is None:
                continue
            try:
                vt_text = session.get_snapshot(keep_ansi=True)
                if not vt_text:
                    continue
                screen_mod = import_parser(spec.parser_agent, "adapters.screen")
                live = screen_mod.parse_screen_snapshot(vt_text)
                s["subagent_status"] = live.ai_status
                modified = True
            except Exception as e:
                _get_logger().warning("list 装饰 ai_status 失败 sid=%s: %s", sid, e)
        return resp if modified else None

    def _decorate_read(self, ctx, resp: dict) -> dict | None:
        """read：子代理会话时附加 live_state 或替换为结构化消息

        支持 ended 会话（session 已从 manager 移除，但 sid 在 _subagent_sessions
        集合中）：message 模式从消息存储解析；snapshot 模式无屏幕，走系统输出。
        """
        sid = resp.get("sessionId", "")
        if not sid:
            return None
        manager = ctx.manager
        if manager is None:
            return None
        session = manager.get_session(sid)
        entry = self._subagent_sessions.get(sid)
        is_subagent = False
        agent_id = ""
        uid = ""
        if session is not None:
            agent_id = getattr(session, "_subagent_agent", "") or ""
            is_subagent = bool(agent_id)
            uid = getattr(session, "_subagent_uid", "") or ""
            # 补上 status 字段（build_result 无顶层 status，CLI 渲染用）
            resp["status"] = "running" if session.running else "ended"
        if not is_subagent and entry is None:
            return None
        if entry:
            agent_id = entry[0]
            uid = entry[1]

        # ended 会话无 session 对象（plugin_host 选项丢失）：给响应加子代理标记
        # 与 uid/agent，CLI 侧 render_response 用自己的 --rf 参数决定渲染
        if session is None:
            resp["subagent"] = True
            resp["subagent_agent"] = agent_id
            if uid:
                resp["subagent_uid"] = uid
            # 独立数据目录透传（CLI 侧读 ended 会话消息需要）
            if self._subagent_sessions.get(sid):
                stored_agent, stored_uid = self._subagent_sessions[sid]
                if stored_agent == agent_id:
                    stored_data = self._subagent_data_dirs.get(sid, "")
                    if stored_data:
                        resp["subagent_data_dir"] = stored_data
            return resp

        # 读取插件选项（--rf 来自 cliOptions，lines 来自 before_request 注入）
        opts = {}
        if session is not None:
            opts = session.plugin_host._options.get("subagent", {})
        rf = opts.get("rf", "snapshot")
        lines = int(opts.get("lines", 5))

        # --rf message 必须带 -l/--lines（否则无法确定消息数）
        if rf == "message" and "lines" not in opts:
            return Response.error("--rf message 需要 -l/--lines 指定消息数")

        # 解析实时状态（仅运行中会话有屏幕）
        live = None
        if session is not None and session.running:
            try:
                live = self._parse_live_state(session)
            except Exception as e:
                _get_logger().warning("parse_live_state 异常: %s", e)
                live = None

        if rf == "message":
            # 替换为结构化消息（解析只需 uid，ended 会话同样可用）
            recent = self._recent_messages(session, lines)
            # 保留 commandType=read 让 CLI 能识别类型
            resp["recent"] = recent
            # 清除 outputStream（用 recent 替代）
            resp.pop("outputStream", None)
            if live:
                resp["live_state"] = live
            return resp

        # snapshot（默认）：附加 live_state，保留系统输出
        if live:
            resp["live_state"] = live
        return resp

    def _decorate_send(self, ctx, resp: dict) -> dict | None:
        """send：子代理会话时标记 feedback_pending + 标记响应供 CLI 简化渲染"""
        sid = resp.get("sessionId", "")
        if not sid:
            return None
        manager = ctx.manager
        if manager is None:
            return None
        session = manager.get_session(sid)
        if session is None:
            return None
        agent_id = getattr(session, "_subagent_agent", "") or ""
        if not agent_id:
            return None
        monitor = self._monitors.get(sid)
        if monitor is not None:
            monitor.feedback_pending = True
            _get_logger().info("send feedback_pending sid=%s", sid)
        # 标记：CLI render_response 渲染简洁确认消息（需 agent 名），而非系统屏幕快照
        resp["subagent_send"] = True
        resp["subagent_agent"] = agent_id
        return resp

    # ── CLI 钩子 ───────────────────────────────────────────

    def before_request(self, ctx, msg: dict):
        """CLI 侧：read 时将 -l 注入 pluginOptions，并缓存 rf/lines 供 render_response 用"""
        if msg.get("type") == "read":
            po = msg.get("pluginOptions", {})
            sub = po.get("subagent", {})
            # 缓存 --rf 和 -l 值（render_response 渲染 ended 子代理会话时用）
            self._last_rf = sub.get("rf", "snapshot")
            self._last_lines = int(sub.get("lines", 5))
            self._last_lines_given = "lines" in msg
            if "lines" in msg:
                sub = po.setdefault("subagent", {})
                sub["lines"] = msg["lines"]
                self._last_lines = int(msg["lines"])
                return msg
        return None

    def render_response(self, ctx, resp: dict) -> str | None:
        """CLI 侧渲染：list 子代理 STATE 列 + read 子代理响应

        ctx 实际是 self._last_command（字符串，最近命令类型）。
        """
        import sys
        import shutil

        cmd = ctx if isinstance(ctx, str) else getattr(ctx, "command", "")
        ctype = resp.get("commandType", "")

        # list：子代理 STATE 列显示 subagent_status
        if cmd == "list" and ctype == "list":
            from .cli_commands import _render_list_with_ai_status
            return _render_list_with_ai_status(resp)

        # read + 子代理响应（有 recent/live_state 或 ended 子代理标记）
        if ctype == "read" and (resp.get("recent") or resp.get("live_state") or resp.get("subagent")):
            # --rf message 必须带 -l/--lines（含 ended 会话）
            if getattr(self, "_last_rf", "snapshot") == "message" and not getattr(self, "_last_lines_given", False):
                from src.client.msg import fmt_message
                import sys as _sys
                _sys.stderr.write(fmt_message("--rf message 需要 -l/--lines 指定消息数") + "\n")
                return ""
            return self._render_read_response(resp)

        # send + 子代理会话：简洁确认消息（替代系统屏幕快照渲染）
        if ctype == "send" and resp.get("subagent_send"):
            agent_id = resp.get("subagent_agent", "")
            display = _DEFAULT_DISPLAY_NAME if agent_id not in AGENTS else AGENTS[agent_id].display_name
            return f"(SubAgent-{display} Message: 发送成功，完成后会发通知)\n"

        return None

    def _render_read_response(self, resp: dict) -> str:
        """渲染 read 子代理响应（snapshot + state 或 message 列表）"""
        import sys
        import shutil

        sid = resp.get("sessionId", "")
        running = resp.get("status") == "running"
        state = "running" if running else "ended"
        cols = shutil.get_terminal_size((80, 24)).columns
        live = resp.get("live_state", {})
        ai_status = live.get("ai_status", "") if live else ""
        agent_id = resp.get("subagent_agent", "")
        spec = AGENTS.get(agent_id)

        def _sep(label=""):
            if label:
                inner = f" {label} "
                dashes = cols - sum(2 if ord(c) > 127 else 1 for c in inner)
                dashes = max(dashes, 0)
                left = dashes // 2
                return "─" * left + inner + "─" * (dashes - left)
            return "─" * cols

        # ended 子代理会话 + --rf message：从消息存储获取结构化消息
        if resp.get("subagent") and not resp.get("recent") and getattr(self, "_last_rf", "snapshot") == "message":
            lines = int(getattr(self, "_last_lines", 5))
            recent = []
            if spec is not None:
                data_dir = resp.get("subagent_data_dir", "") or ""
                recent = self._recent_messages_by_uid(
                    resp.get("subagent_uid", ""), lines, spec, _data_dir=data_dir)
            if recent:
                content_lines = []
                for m in recent:
                    idx = m.get("index")
                    role = m.get("role", "")
                    content = m.get("content", "")
                    prefix = f"{idx}:" if idx else ""
                    content_lines.append(f"  {prefix}{role:<9} → {content}")
                content = "\n".join(content_lines)
            else:
                content = "(no messages)"
            title = "message"
            hit = True
        elif "recent" in resp:
            msgs = resp.get("recent", [])
            content_lines = []
            for m in msgs:
                idx = m.get("index")
                role = m.get("role", "")
                content = m.get("content", "")
                prefix = f"{idx}:" if idx else ""
                content_lines.append(f"  {prefix}{role:<9} → {content}")
            content = "\n".join(content_lines) if content_lines else "(no messages)"
            title = "message"
            hit = True
        else:
            # snapshot 模式：屏幕快照
            content = resp.get("outputStream", "")
            title = "snapshot"
            hit = False

        # 状态标签
        tags = []
        if ai_status:
            tags.append(f"subagent_{ai_status}")
        if live.get("input_text"):
            tags.append(f"input: {live['input_text']}")
        if live.get("permission_mode"):
            tags.append(f"perm: {live['permission_mode']}")
        if live.get("context_percent"):
            tags.append(f"ctx: {live['context_percent']}%")

        parts = [_sep(title)]
        if content:
            parts.append(content.rstrip())
        parts.append(_sep())
        tag_str = " · ".join(tags) if tags else ""
        state_tag = f"subagent_{ai_status}" if ai_status else state
        agent_tag = f"  [{spec.display_name}]" if spec else ""
        parts.append(f"[read · {tag_str}]  {sid}  {state_tag}{agent_tag}")
        if hit:
            parts.append("(hit: 本消息是全部输出完成的消息，正在输出的不会被记录，欲获取屏幕实时状态，请使用 --rf snapshot)")
        return "\n".join(parts) + "\n"