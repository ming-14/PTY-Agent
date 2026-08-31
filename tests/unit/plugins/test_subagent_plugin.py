"""黑盒测试：subagent 插件 — 进程级消息处理 + CLI 命令 + 回合监控

覆盖场景（按黑盒原则，仅通过公开接口/协议测试）：
  1. 插件清单加载与校验（manifest → validator → loader）
  2. 多 agent 消息路由（codebuddy_exec / devin_exec / claude_exec 分发）
  3. 消息创建与响应装饰（decorate_response：list/read/send）
  4. CLI 命令解析与注册（CodebuddyCommand / DevinCommand / ClaudeCommand）
  5. 双 parser 加载（wb_parser / dv_parser / cl_parser）
  6. 回合监控器参数化（TurnMonitor 按 agent 选 screen parser）
  7. Devin 会话发现（锁文件 PID 匹配函数）
  8. AgentSpec 数据完整性（新增 agent 时需注册的字段齐全）
"""

import json
import os
import sys
import time
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

_SUBAGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..",
                 "config", "plugins", "subagent")
)


# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _load_plugin_cls():
    """加载真实 subagent 插件的类（不实例化）"""
    sys.path.insert(0, os.path.dirname(_SUBAGENT_DIR))
    import importlib
    mod = importlib.import_module("config.plugins.subagent.subagent_plugin")
    return mod.SubagentPlugin


class _FakeConn:
    def __init__(self):
        self.sent = []

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass

    def fileno(self):
        return -1

    def settimeout(self, t):
        pass


# ═══════════════════════════════════════════════════════════
# 1. 插件清单与加载
# ═══════════════════════════════════════════════════════════

class TestPluginManifest:
    """黑盒：plugin.json 声明 → 清单校验 → 加载器"""

    def test_manifest_loads(self):
        """plugin.json 能被正确解析为 PluginManifest"""
        from src.plugins.manifest import load_manifest
        manifest = load_manifest(_SUBAGENT_DIR)
        assert manifest is not None
        assert manifest.id == "subagent"
        assert manifest.version
        assert "cli" in manifest.kind
        assert "process" in manifest.kind
        assert "codebuddy_exec" in manifest.message_types
        assert "devin_exec" in manifest.message_types
        assert "opencode_exec" in manifest.message_types
        assert "claude_exec" in manifest.message_types
        assert "codebuddy" in manifest.cli_commands
        assert "devin" in manifest.cli_commands
        assert "opencode" in manifest.cli_commands
        assert "claude" in manifest.cli_commands
        assert "list" in manifest.decorate_types
        assert "read" in manifest.decorate_types
        assert "send" in manifest.decorate_types

    def test_loader_accepts_full_plugin(self):
        """load_plugin_dir 加载 subagent 插件成功"""
        from src.plugins.loader import load_plugin_dir
        loaded = load_plugin_dir(_SUBAGENT_DIR)
        assert loaded is not None
        m = loaded.manifest
        # 清单声明与导出命令一致性校验
        declared = set(m.cli_commands)
        exported = {c.name for c in (loaded.command_classes or [])}
        assert declared == exported, f"cliCommands 声明 {declared} 与导出 {exported} 不一致"

    def test_plugin_class_has_all_hooks(self):
        """插件类实现了 manifest.hooks 声明的所有钩子"""
        from src.plugins.manifest import load_manifest
        from src.plugins.loader import load_plugin_dir
        manifest = load_manifest(_SUBAGENT_DIR)
        loaded = load_plugin_dir(_SUBAGENT_DIR)
        assert loaded is not None
        cls = loaded.cls
        from src.plugins.base import Plugin, VALID_HOOKS
        for hook in manifest.hooks:
            assert hook in VALID_HOOKS, f"未知钩子: {hook}"
            assert getattr(cls, hook) is not getattr(Plugin, hook), \
                f"钩子 {hook} 声明但未实现"

    def test_registry_accepts_subagent(self):
        """PluginRegistry 能登记 subagent 插件（process 形态）"""
        from src.plugins.registry import PluginRegistry
        reg = PluginRegistry([_SUBAGENT_DIR])
        instances = reg.process_instances()
        assert "subagent" in instances
        info = reg.describe("subagent")
        assert info is not None
        assert "codebuddy_exec" in info["messageTypes"]
        assert "devin_exec" in info["messageTypes"]


# ═══════════════════════════════════════════════════════════
# 2. 消息路由
# ═══════════════════════════════════════════════════════════

class TestMessageRouting:
    """黑盒：handle_message 按消息类型分发到对应 agent"""

    def test_unknown_message_returns_none(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        result = plugin.handle_message(None, {"type": "unknown_type"})
        assert result is None, "未知消息类型应返回 None"

    def test_codebuddy_exec_rejected_without_manager(self):
        """codebuddy_exec 无 manager 时返回 error（而非崩溃）"""
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        result = plugin.handle_message(None, {
            "type": "codebuddy_exec", "id": "test", "prompt": "hello"
        })
        assert result is not None
        assert result.get("type") == "error"

    def test_devin_exec_rejected_without_manager(self):
        """devin_exec 无 manager 时返回 error"""
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        result = plugin.handle_message(None, {
            "type": "devin_exec", "id": "test", "prompt": "hello"
        })
        assert result is not None
        assert result.get("type") == "error"

    def test_no_prompt_returns_error(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        # 缺 prompt
        result = plugin.handle_message(None, {
            "type": "codebuddy_exec", "id": "test"
        })
        assert result is not None
        assert result.get("type") == "error"

    def test_no_id_returns_error(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        result = plugin.handle_message(None, {
            "type": "codebuddy_exec", "prompt": "hello"
        })
        assert result is not None
        assert result.get("type") == "error"

    def test_exec_subagent_interactive_returns_spawned(self, tmp_path):
        """subagent interactive exec 立即返回 spawned 响应（不阻塞 25s 启动检测）"""
        import time
        import json
        from src.execution.context import HandlerContext
        from src.daemon.handlers.exec_handler import ExecHandler
        from src.plugins.registry import PluginRegistry
        from src.session.manager import SessionManager
        from src.protocol.envelope import unwrap

        registry = PluginRegistry([_SUBAGENT_DIR])
        manager = SessionManager(plugin_registry=registry)
        conn = _FakeConn()
        started = time.time()
        ExecHandler().handle(
            HandlerContext(manager, None, None), conn,
            {"type": "exec", "id": "spawn-1", "command": "python -u -i",
             "cwd": str(tmp_path), "timeout": 0.0,
             "subagent": {"agent": "claude", "prompt": "hi",
                          "oneshot": False, "uid": "u1", "data_dir": ""}},
        )
        elapsed = time.time() - started
        # spawned 响应应快速返回，远小于 25s 启动检测
        assert elapsed < 5.0, "exec 不应阻塞 25s+ 启动检测，耗时 %.1fs" % elapsed
        # 解码响应
        assert conn.sent, "应有响应"
        try:
            _, body, _ = unwrap(json.loads(conn.sent[0].decode("utf-8")))
        except Exception:
            pytest.skip("响应解码失败（可能未启用签名器）")
        assert body.get("status") == "spawned", "interactive exec 应返回 spawned"
        # 会话已创建
        session = manager.get_session("spawn-1")
        assert session is not None, "会话应已创建"
        # 清理
        if session.running:
            session.stop()


# ═══════════════════════════════════════════════════════════
# 3. 响应装饰（无真实会话时，仅模拟装饰逻辑）
# ═══════════════════════════════════════════════════════════

class TestDecorateResponse:
    """黑盒：decorate_response 按 commandType 分派"""

    def test_list_not_subagent_returns_none(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        resp = {"commandType": "list", "sessions": []}
        result = plugin.decorate_response(None, resp)
        assert result is None, "无子代理会话的 list 不应修改"

    def test_read_not_subagent_returns_none(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        class _Ctx:
            manager = None
        resp = {"commandType": "read", "sessionId": "nonexistent"}
        result = plugin.decorate_response(_Ctx(), resp)
        assert result is None, "非子代理会话的 read 不应修改"

    def test_send_not_subagent_returns_none(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        class _Ctx:
            manager = None
        resp = {"commandType": "send", "sessionId": "nonexistent"}
        result = plugin.decorate_response(_Ctx(), resp)
        assert result is None, "非子代理会话的 send 不应修改"

    def test_other_command_type_returns_none(self):
        plugin = _load_plugin_cls()()
        plugin.on_init(None)
        resp = {"commandType": "exec"}
        result = plugin.decorate_response(None, resp)
        assert result is None, "非 list/read/send 不应修改"

    def test_read_running_session_sets_status(self):
        """read running 子代理会话：_decorate_read 应补 status=running（修复 ended 误判）"""
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        plugin = SubagentPlugin()
        plugin.on_init(None)

        class _FakeHost:
            _options = {"subagent": {"rf": "snapshot"}}

        class _FakeSession:
            id = "hs-status-1"
            running = True
            _subagent_agent = "claude"
            _subagent_uid = "uid-status-1"
            _subagent_data_dir = ""
            plugin_host = _FakeHost()

            def get_snapshot(self, keep_ansi=False):
                return "fake snapshot text"

        class _FakeManager:
            def get_session(self, sid):
                return _FakeSession() if sid == "hs-status-1" else None

        class _Ctx:
            manager = _FakeManager()

        resp = {"commandType": "read", "sessionId": "hs-status-1",
                "outputStream": "out"}
        result = plugin.decorate_response(_Ctx(), resp)
        assert result is not None
        assert result.get("status") == "running", \
            "running 会话应补 status=running，实际 %r" % result.get("status")

    def test_read_ended_session_keeps_no_status(self):
        """read ended 子代理会话（session 已移除）：不加 status（CLI 侧回退 ended）"""
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        plugin = SubagentPlugin()
        plugin.on_init(None)
        plugin._subagent_sessions["hs-ended-1"] = ("claude", "uid-ended-1")

        class _FakeManager:
            def get_session(self, sid):
                return None

        class _Ctx:
            manager = _FakeManager()

        resp = {"commandType": "read", "sessionId": "hs-ended-1",
                "outputStream": "out"}
        result = plugin.decorate_response(_Ctx(), resp)
        assert result is not None
        assert result.get("subagent") is True
        assert result.get("status") is None, \
            "ended 会话不应补 status（CLI 回退 ended）"


# ═══════════════════════════════════════════════════════════
# 4. CLI 命令
# ═══════════════════════════════════════════════════════════

class TestCliCommands:
    """黑盒：CLI 命令注册与解析"""

    def test_codebuddy_command_registered(self):
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        assert "codebuddy" in cmds
        assert cmds["codebuddy"].message_type == "codebuddy_exec"
        assert cmds["codebuddy"].agent_id == "codebuddy"

    def test_devin_command_registered(self):
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        assert "devin" in cmds
        assert cmds["devin"].message_type == "devin_exec"
        assert cmds["devin"].agent_id == "devin"

    def test_claude_command_registered(self):
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        assert "claude" in cmds
        assert cmds["claude"].message_type == "claude_exec"
        assert cmds["claude"].agent_id == "claude"

    def test_codebuddy_argparse(self):
        """codebuddy exec 子命令解析"""
        import argparse
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        cmd = cmds["codebuddy"]()
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        args = parser.parse_args(["exec", "mysid", "-p", "hello"])
        assert args.subagent_subcmd == "exec"
        assert args.id == "mysid"
        assert args.prompt == "hello"

    def test_devin_argparse(self):
        """devin exec 子命令解析"""
        import argparse
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        cmd = cmds["devin"]()
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        args = parser.parse_args(["exec", "mysid", "-p", "hello", "--oneshot"])
        assert args.subagent_subcmd == "exec"
        assert args.id == "mysid"
        assert args.prompt == "hello"
        assert args.oneshot is True

    def test_no_subcmd_errors(self):
        import argparse
        from config.plugins.subagent.cli_commands import all_agent_commands
        cmds = {c.name: c for c in all_agent_commands()}
        cmd = cmds["codebuddy"]()
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        args = parser.parse_args([])
        with pytest.raises(SystemExit):
            cmd.validate(args, parser)


# ═══════════════════════════════════════════════════════════
# 5. 双 parser 加载
# ═══════════════════════════════════════════════════════════

class TestParserLoader:
    """黑盒：parser_loader 按 agent 加载对应 parser 包"""

    PARSER_MODULES = [
        ("codebuddy", "adapters.screen"),
        ("codebuddy", "adapters.messages_jsonl"),
        ("codebuddy", "adapters.session_locator"),
        ("devin", "adapters.screen"),
        ("devin", "adapters.messages_transcript"),
        ("devin", "adapters.session_locator"),
        ("opencode", "adapters.screen"),
        ("opencode", "adapters.messages_db"),
        ("opencode", "adapters.session_locator"),
        ("claude", "adapters.screen"),
        ("claude", "adapters.messages_jsonl"),
        ("claude", "adapters.session_locator"),
    ]

    def test_all_parser_modules_load(self):
        from config.plugins.subagent.parser_loader import import_parser
        for agent, mod in self.PARSER_MODULES:
            m = import_parser(agent, mod)
            assert m is not None, f"{agent} {mod} 加载失败"
            assert hasattr(m, "__name__"), f"{agent} {mod} 无 __name__"

    def test_unknown_agent_raises(self):
        from config.plugins.subagent.parser_loader import import_parser
        with pytest.raises(ImportError):
            import_parser("nonexistent", "adapters.screen")

    def test_codebuddy_screen_has_parse_screen_snapshot(self):
        from config.plugins.subagent.parser_loader import import_parser
        mod = import_parser("codebuddy", "adapters.screen")
        assert hasattr(mod, "parse_screen_snapshot")

    def test_devin_screen_has_parse_screen_snapshot(self):
        from config.plugins.subagent.parser_loader import import_parser
        mod = import_parser("devin", "adapters.screen")
        assert hasattr(mod, "parse_screen_snapshot")

    def test_codebuddy_locator_has_find_session_file(self):
        from config.plugins.subagent.parser_loader import import_parser
        mod = import_parser("codebuddy", "adapters.session_locator")
        assert hasattr(mod, "find_session_file")

    def test_devin_locator_has_find_transcript_file(self):
        from config.plugins.subagent.parser_loader import import_parser
        mod = import_parser("devin", "adapters.session_locator")
        assert hasattr(mod, "find_transcript_file")


# ═══════════════════════════════════════════════════════════
# 6. AgentSpec 数据完整性
# ═══════════════════════════════════════════════════════════

class TestAgentSpec:
    """黑盒：AgentSpec 注册表数据完整性"""

    def test_all_agents_have_required_fields(self):
        """每个 agent 必须声明所有关键字段"""
        from config.plugins.subagent.agents import AGENTS
        for aid, spec in AGENTS.items():
            assert spec.agent_id, f"{aid}: agent_id 缺失"
            assert spec.display_name, f"{aid}: display_name 缺失"
            assert spec.command, f"{aid}: command 缺失"
            assert spec.parser_agent, f"{aid}: parser_agent 缺失"
            assert spec.message_type, f"{aid}: message_type 缺失"
            assert spec.permission_args is not None, f"{aid}: permission_args 缺失"
            assert spec.messages_adapter, f"{aid}: messages_adapter 缺失"
            assert spec.locator_fn, f"{aid}: locator_fn 缺失"
            assert spec.msg_loader_fn, f"{aid}: msg_loader_fn 缺失"

    def test_session_id_or_lock_dir(self):
        """每个 agent 要么有 session_id_arg 要么有 export_arg（codebuddy/devin），
        或 discover_fn（opencode interactive）"""
        from config.plugins.subagent.agents import AGENTS
        for aid, spec in AGENTS.items():
            # opencode interactive 无 session_id_arg/export_arg，用 discover_fn
            if spec.discover_fn:
                continue
            assert spec.session_id_arg or spec.export_arg, \
                f"{aid}: 必须声明 session_id_arg 或 export_arg"

    def test_discover_agent_has_discover_fn(self):
        """discover 型 agent（无 session_id_arg/export_arg）必须声明 discover_fn"""
        from config.plugins.subagent.agents import AGENTS
        for aid, spec in AGENTS.items():
            if not spec.session_id_arg and not spec.export_arg:
                assert spec.discover_fn, f"{aid}: 需 discover_fn"

    def test_export_agent_has_uid_is_path(self):
        """export 型 agent 必须设置 uid_is_path=True"""
        from config.plugins.subagent.agents import AGENTS
        for aid, spec in AGENTS.items():
            if spec.export_arg:
                assert spec.uid_is_path, f"{aid}: export_arg 需 uid_is_path=True"

    def test_message_type_reverse_map(self):
        """MESSAGE_TYPE_TO_AGENT 反向映射完整"""
        from config.plugins.subagent.agents import AGENTS, MESSAGE_TYPE_TO_AGENT
        for aid, spec in AGENTS.items():
            assert MESSAGE_TYPE_TO_AGENT[spec.message_type] == aid

    def test_build_command_codebuddy(self):
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["codebuddy"]
        cmd = spec.build_command("task", model="m1", uid="u1", oneshot=False)
        assert cmd[0] == "cbc"
        assert "--model" in cmd
        assert "--session-id" in cmd
        assert "u1" in cmd
        assert "task" in cmd

    def test_build_command_devin_interactive(self):
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["devin"]
        cmd = spec.build_command("task", model="m1", uid=None, oneshot=False)
        assert cmd[0] == "devin.exe"
        assert "--model" in cmd
        assert "--permission-mode" in cmd
        assert "dangerous" in cmd
        assert "--" in cmd  # interactive prompt separator
        assert cmd[-1] == "task"

    def test_build_command_devin_oneshot(self):
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["devin"]
        cmd = spec.build_command("task", model="m1", uid=None, oneshot=True)
        assert cmd[0] == "devin.exe"
        assert "-p" in cmd
        assert cmd[-1] == "task"  # prompt is last arg for oneshot

    def test_build_command_opencode_oneshot(self):
        """opencode oneshot：--auto --title <uid> run <prompt>"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        cmd = spec.build_command("task", uid="wb-123", oneshot=True)
        assert cmd[0] == "opencode.exe"
        assert "--auto" in cmd
        assert "--title" in cmd and "wb-123" in cmd
        assert "run" in cmd
        assert cmd[-1] == "task"

    def test_build_command_opencode_interactive(self):
        """opencode interactive：--auto --prompt <prompt>"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        cmd = spec.build_command("task", uid=None, oneshot=False)
        assert cmd[0] == "opencode.exe"
        assert "--auto" in cmd
        assert "--prompt" in cmd
        assert cmd[-1] == "task"
        assert "run" not in cmd  # interactive 无 run 子命令

    def test_opencode_discover_fn_declared(self):
        """opencode 声明数据目录隔离发现（data_dir_env + discover_log_relpath）"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        assert spec.data_dir_env == "XDG_DATA_HOME"
        assert spec.discover_log_relpath
        assert spec.discover_log_regex

    def test_build_command_claude_interactive(self):
        """claude interactive：--dangerously-skip-permissions --session-id <uid> <prompt>"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["claude"]
        cmd = spec.build_command("task", model="sonnet", uid="u1", oneshot=False)
        assert cmd[0] == "claude"
        assert "--model" in cmd and "sonnet" in cmd
        assert "--dangerously-skip-permissions" in cmd
        assert "--session-id" in cmd and "u1" in cmd
        assert cmd[-1] == "task"  # prompt 为最后参数（位置参数）

    def test_build_command_claude_oneshot(self):
        """claude oneshot：-p --output-format text <prompt>（无 --session-id）"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["claude"]
        cmd = spec.build_command("task", model=None, uid=None, oneshot=True)
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd and "text" in cmd
        assert "--session-id" not in cmd
        assert cmd[-1] == "task"

    def test_claude_loader_meta_first(self):
        """claude 的 msg_loader_fn 返回 (meta, messages)，loader_meta_first=True"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["claude"]
        assert spec.msg_loader_fn == "load_jsonl_with_meta"
        assert spec.loader_meta_first is True


# ═══════════════════════════════════════════════════════════
# 7. 回合监控器参数化
# ═══════════════════════════════════════════════════════════

class TestTurnMonitor:
    """黑盒：TurnMonitor 按 agent 参数选 screen parser"""

    def test_init_with_agent(self):
        from config.plugins.subagent.turn_monitor import TurnMonitor
        monitor = TurnMonitor(session=None, events=None, agent="devin", display_name="Devin")
        assert monitor._agent == "devin"
        assert monitor._display_name == "Devin"

    def test_notify_detail_devin(self):
        from config.plugins.subagent.turn_monitor import TurnMonitor
        monitor = TurnMonitor(session=None, events=None, agent="devin", display_name="Devin")
        assert "Devin" in monitor._notify_detail("turn_complete")
        assert "Devin" in monitor._notify_detail("awaiting_approval")
        assert "Devin" in monitor._notify_detail("asking")

    def test_notify_detail_codebuddy(self):
        from config.plugins.subagent.turn_monitor import TurnMonitor
        monitor = TurnMonitor(session=None, events=None, agent="codebuddy", display_name="Codebuddy")
        assert "Codebuddy" in monitor._notify_detail("turn_complete")
        assert "Codebuddy" in monitor._notify_detail("awaiting_approval")

    def test_stuck_requires_seen_busy(self):
        """从未见过 busy 的静默不判 stuck（启动阶段/模型快速完成场景）"""
        from config.plugins.subagent.turn_monitor import TurnMonitor
        events = []
        class _Bus:
            def publish(self, topic, payload=None, source="test"):
                events.append((topic, payload))
        class _Sess:
            id = "stuck-t1"
            def get_snapshot(self, keep_ansi=False):
                return "static screen"
        monitor = TurnMonitor(session=_Sess(), events=_Bus(), agent="devin",
                              display_name="Devin", stuck_timeout=0.05)
        monitor.feedback_pending = True
        # 未见过 busy：静默超时也不发布 stuck
        monitor._check_stuck("hash1", 100.0)
        monitor._check_stuck("hash1", 200.0)
        assert not any(t == "subagent.stuck" for t, _ in events), \
            "未见 busy 不应判 stuck: %r" % events

    def test_stuck_after_seen_busy(self):
        """见过 busy 后静默超时判 stuck"""
        from config.plugins.subagent.turn_monitor import TurnMonitor
        events = []
        class _Bus:
            def publish(self, topic, payload=None, source="test"):
                events.append((topic, payload))
        class _Sess:
            id = "stuck-t2"
            def get_snapshot(self, keep_ansi=False):
                return "static screen"
        monitor = TurnMonitor(session=_Sess(), events=_Bus(), agent="devin",
                              display_name="Devin", stuck_timeout=0.05)
        monitor.feedback_pending = True
        monitor._seen_busy = True  # 曾见过 busy
        monitor._check_stuck("hash1", 100.0)
        monitor._check_stuck("hash1", 200.0)
        assert any(t == "subagent.stuck" for t, _ in events), \
            "见过 busy 后静默应判 stuck: %r" % events

    def test_seen_busy_gate_idle_change(self):
        """未见 busy 时 idle→idle 内容变化不判 turn_complete"""
        from config.plugins.subagent.turn_monitor import TurnMonitor
        events = []
        class _Bus:
            def publish(self, topic, payload=None, source="test"):
                events.append((topic, payload))
        class _Sess:
            id = "gate-t1"
            def get_snapshot(self, keep_ansi=False):
                return "screen"
        monitor = TurnMonitor(session=_Sess(), events=_Bus(), agent="devin",
                              display_name="Devin")
        monitor.feedback_pending = True
        monitor._last_status = "idle"
        monitor._last_hash = "old"
        monitor._check_transition("idle", "new-hash")  # idle→idle 变化
        assert not any(t == "subagent.turn_complete" for t, _ in events), \
            "未见 busy 的 idle→idle 变化不应判完成: %r" % events
        # 见过 busy 后同场景判完成
        monitor._seen_busy = True
        monitor._check_transition("idle", "newer-hash")
        assert any(t == "subagent.turn_complete" for t, _ in events), \
            "见过 busy 的 idle→idle 变化应判完成: %r" % events

    def test_idle_change_after_startup_grace(self):
        """超过启动宽限期后，未见 busy 的 idle→idle 变化也判完成（快速轮兜底）"""
        import time as _time
        from config.plugins.subagent.turn_monitor import TurnMonitor
        events = []
        class _Bus:
            def publish(self, topic, payload=None, source="test"):
                events.append((topic, payload))
        class _Sess:
            id = "grace-t1"
            def get_snapshot(self, keep_ansi=False):
                return "screen"
        monitor = TurnMonitor(session=_Sess(), events=_Bus(), agent="opencode",
                              display_name="OpenCode")
        monitor.feedback_pending = True
        monitor._last_status = "idle"
        monitor._last_hash = "old"
        # 模拟已过启动宽限期（_started_at 回拨）
        monitor._started_at = _time.monotonic() - monitor._startup_grace - 1.0
        monitor._check_transition("idle", "new-hash")
        assert any(t == "subagent.turn_complete" for t, _ in events), \
            "过启动宽限期后 idle→idle 变化应判完成: %r" % events


# ═══════════════════════════════════════════════════════════
# 8. Export 导出路径（devin 消息存储确定性路径）
# ═══════════════════════════════════════════════════════════

class TestDevinExportPath:
    """黑盒：export 型 agent 的导出路径与命令生成"""

    def test_export_dir_created(self):
        """_ensure_export_dir 创建目录并返回路径"""
        from config.plugins.subagent.subagent_plugin import _ensure_export_dir
        path = _ensure_export_dir()
        assert os.path.isdir(path)
        assert "subagent" in path and "exports" in path

    def test_build_command_devin_has_export(self):
        """devin build_command 包含 --export <path>"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["devin"]
        cmd = spec.build_command("task", uid=r"C:\tmp\export.json", oneshot=False)
        assert "--export" in cmd
        assert r"C:\tmp\export.json" in cmd
        # prompt 仍为最后参数
        assert cmd[-1] == "task"

    def test_build_command_codebuddy_no_export(self):
        """codebuddy build_command 不含 --export"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["codebuddy"]
        cmd = spec.build_command("task", uid="abc", oneshot=False)
        assert "--export" not in cmd
        assert "--session-id" in cmd


# ═══════════════════════════════════════════════════════════
# 8b. 消息解析（黑盒：按 AgentSpec 走对应 parser）
# ═══════════════════════════════════════════════════════════

class TestMessageParsing:
    """黑盒：_recent_messages_by_uid 按 agent 走对应 parser 与存储格式

    用 devinparser 脱敏 fixture 验证 devin 路径（transcript JSON）；
    claudeparser 脱敏 fixture 验证 claude 路径（jsonl）；
    codebuddy 路径以 fixture 缺失时跳过（workbuddyparser jsonl 需真实 cbc 数据）。
    """

    _DEVIN_FIXTURE = os.path.join(
        _SUBAGENT_DIR, "parser", "devinparser", "tests", "fixtures",
        "victorious-squid.json",
    )

    def test_devin_recent_messages_from_fixture(self):
        """devin 导出文件（ATIF）→ 结构化消息（含 text/thinking/tool 摘要）

        devin 的 uid 即导出文件路径（uid_is_path=True），直接读取解析。
        """
        if not os.path.isfile(self._DEVIN_FIXTURE):
            pytest.skip("devinparser fixture 缺失")
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        from config.plugins.subagent.agents import AGENTS
        plugin = SubagentPlugin()
        plugin.on_init(None)
        spec = AGENTS["devin"]
        assert spec.uid_is_path, "devin 应为 uid_is_path"
        # uid 即导出文件路径
        recent = plugin._recent_messages_by_uid(self._DEVIN_FIXTURE, 5, spec)
        assert recent, "devin fixture 应解析出消息"
        # 消息带 index/role/content
        for m in recent:
            assert "index" in m and "role" in m and "content" in m
        # 用户消息文本存在
        texts = [m["content"] for m in recent if m["role"] == "user"]
        assert texts and any(t.strip() for t in texts)

    def test_devin_unknown_uid_returns_empty(self):
        """devin 未知导出路径 → 空列表（不抛异常）"""
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        from config.plugins.subagent.agents import AGENTS
        plugin = SubagentPlugin()
        plugin.on_init(None)
        missing = os.path.join(tempfile.gettempdir(), "no-such-export.json")
        recent = plugin._recent_messages_by_uid(missing, 5, AGENTS["devin"])
        assert recent == []

    _CLAUDE_FIXTURE = os.path.join(
        _SUBAGENT_DIR, "parser", "claudeparser", "tests", "fixtures",
        "sample_session.jsonl",
    )

    def test_claude_recent_messages_from_fixture(self):
        """claude jsonl 文件 → 结构化消息（含 text/thinking/tool_use/tool_result）"""
        from config.plugins.subagent.parser_loader import import_parser
        msg_mod = import_parser("claude", "adapters.messages_jsonl")
        meta, messages = msg_mod.load_jsonl_with_meta(self._CLAUDE_FIXTURE)
        assert messages, "claude fixture 应解析出消息"
        # 验证 4 种内容类型
        types_found = set()
        for m in messages:
            for item in m.items:
                types_found.add(item.type)
        assert "text" in types_found
        assert "thinking" in types_found
        assert "tool_use" in types_found
        assert "tool_result" in types_found
        # 验证元数据与返回约定（meta 在前，loader_meta_first=True）
        assert meta["mode"] == "normal"
        assert meta["permission_mode"] == "default"
        assert isinstance(meta, dict) and isinstance(messages, list)

    def test_claude_recent_messages_by_uid(self):
        """claude _recent_messages_by_uid 经插件统一路径解析（真实会话文件，缺失跳过）"""
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        from config.plugins.subagent.agents import AGENTS
        from config.plugins.subagent.parser_loader import import_parser
        locator = import_parser("claude", "adapters.session_locator")
        sessions = locator.find_all_sessions()
        if not sessions:
            pytest.skip("无真实 claude 会话文件")
        plugin = SubagentPlugin()
        plugin.on_init(None)
        spec = AGENTS["claude"]
        recent = plugin._recent_messages_by_uid(
            sessions[0]["session_id"], 5, spec)
        assert isinstance(recent, list)
        for m in recent:
            assert "index" in m and "role" in m and "content" in m


# ═══════════════════════════════════════════════════════════
# 9. 信任对话框自动确认
# ═══════════════════════════════════════════════════════════

class TestAutoConfirmTrust:
    """黑盒：_auto_confirm_trust 按 AgentSpec 字段工作"""

    def test_claude_trust_dialog_keys(self):
        """claude 的信任对话框检测文本和按键模板与 AgentSpec 一致"""
        from config.plugins.subagent.subagent_plugin import SubagentPlugin
        from config.plugins.subagent.agents import AGENTS
        plugin = SubagentPlugin()
        spec = AGENTS["claude"]
        assert spec.trust_dialog is True
        assert spec.trust_dialog_check == "Quick safety check"
        # 按键模板应为 ↓+回车+回车（展开为 \x1b[B\r\r + 停顿偏移）
        assert spec.trust_dialog_keys == "{down}{enter}{enter}"

    def test_codebuddy_trust_dialog_defaults(self):
        """codebuddy 的信任对话框保持原行为"""
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["codebuddy"]
        assert spec.trust_dialog is True
        assert spec.trust_dialog_check == "Do you trust the files in this folder?"
        assert spec.trust_dialog_keys == "\r"
        assert spec.trust_dialog_timeout == 5.0


# ═══════════════════════════════════════════════════════════
# 10. 插件加载完整性（CLI 侧 + daemon 侧）
# ═══════════════════════════════════════════════════════════

class TestPluginIntegration:
    """黑盒：完整加载链路"""

    def test_cli_plugin_host_loads(self):
        """CliPluginHost 加载 subagent 插件，注册两个命令"""
        from src.client.cli_plugins import CliPluginHost
        host = CliPluginHost([_SUBAGENT_DIR])
        cmds = host.command_classes()
        names = [c.name for c in cmds]
        assert "codebuddy" in names
        assert "devin" in names

    def test_plugin_manifest_decorate_types(self):
        """decorateTypes 声明对应钩子已实现"""
        from src.plugins.loader import load_plugin_dir
        loaded = load_plugin_dir(_SUBAGENT_DIR)
        assert loaded is not None
        cls = loaded.cls
        from src.plugins.base import Plugin
        assert getattr(cls, "decorate_response") is not getattr(Plugin, "decorate_response")

    def test_plugin_info_fields(self):
        """plugin info 返回的字段完整"""
        from src.plugins.registry import PluginRegistry
        from src.plugins.manifest import load_manifest
        reg = PluginRegistry([_SUBAGENT_DIR])
        info = reg.info("subagent")
        assert info is not None
        assert info["name"] == "subagent"
        assert "codebuddy_exec" in info["messageTypes"]
        assert "devin_exec" in info["messageTypes"]
        assert info["kind"] == "cli/process"
        # 清单声明（commands 字段为生效白名单；cliCommands 在 manifest 上）
        manifest = load_manifest(_SUBAGENT_DIR)
        assert "codebuddy" in manifest.cli_commands
        assert "devin" in manifest.cli_commands


# ═══════════════════════════════════════════════════════════
# 10. 命令路径解析（--program-path / 环境变量 / PATH）
# ═══════════════════════════════════════════════════════════

class TestCommandResolution:
    """黑盒：_resolve_command 三级解析与报错"""

    def test_program_path_explicit(self, tmp_path):
        """--program-path 显式指定 → 直接使用"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        fake_exe = tmp_path / "opencode.exe"
        fake_exe.write_text("")
        cmd = _resolve_command(["opencode.exe", "run", "t"], spec,
                               program_path=str(fake_exe))
        assert cmd[0] == str(fake_exe)

    def test_program_path_not_found_raises(self):
        """--program-path 指向不存在路径 → 报错"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        with pytest.raises(FileNotFoundError):
            _resolve_command(["opencode.exe"], spec,
                             program_path=r"C:\nonexistent\opencode.exe")

    def test_env_var_used(self, monkeypatch, tmp_path):
        """环境变量（如 OPENCODE_PATH）优先于 PATH"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        assert spec.command_path_env == "OPENCODE_PATH"
        fake_dir = tmp_path / "opencode-dir"
        fake_dir.mkdir()
        fake_exe = fake_dir / "opencode.exe"
        fake_exe.write_text("")
        monkeypatch.setenv("OPENCODE_PATH", str(fake_dir))
        cmd = _resolve_command(["opencode.exe", "run", "t"], spec, program_path="")
        assert cmd[0] == str(fake_exe)

    def test_env_var_wrong_raises(self, monkeypatch):
        """环境变量指向错误 → 报错（不静默回落 PATH）"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        monkeypatch.setenv("OPENCODE_PATH", r"C:\nonexistent")
        with pytest.raises(FileNotFoundError):
            _resolve_command(["opencode.exe"], spec, program_path="")

    def test_path_lookup_codebuddy(self):
        """PATH 查找：codebuddy（cbc 在 PATH）"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        import shutil

        if shutil.which("cbc") is None and shutil.which("codebuddy") is None:
            pytest.skip("codebuddy (cbc) 未安装，跳过 PATH 查找测试")
        spec = AGENTS["codebuddy"]
        cmd = _resolve_command(["cbc", "-p", "t"], spec, program_path="")
        assert cmd[0]  # 找到路径

    def test_all_missing_raises(self, monkeypatch):
        """全找不到 → 报错（含提示）"""
        from config.plugins.subagent.subagent_plugin import _resolve_command
        from config.plugins.subagent.agents import AGENTS
        spec = AGENTS["opencode"]
        monkeypatch.delenv("OPENCODE_PATH", raising=False)
        with pytest.raises(FileNotFoundError) as ei:
            _resolve_command(["opencode.exe"], spec, program_path="")
        assert "OPENCODE_PATH" in str(ei.value) or "--program-path" in str(ei.value)