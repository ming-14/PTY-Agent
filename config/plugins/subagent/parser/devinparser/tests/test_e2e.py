"""devinparser 端到端测试。

测试依赖：
- tests/fixtures/*.json — 真实 transcript 副本（离线）
- tests/fixtures/*.txt — 真实屏幕快照（PTY-Agent 抓取）
- 真实会话数据（%APPDATA%\\devin\\cli\\transcripts\\<id>.json），不存在时跳过
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 确保 src 在导入路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.entities import (
    LiveState, Message, MessageItem, ParseResult, Session, ToolUse, ToolResult,
)
from src.adapters import messages_transcript, screen, session_locator, output
from src.usecases import ParseSessionUseCase


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TRANSCRIPT_FIXTURES = sorted(FIXTURES_DIR.glob("*.json"))
SCREEN_FIXTURES = sorted(FIXTURES_DIR.glob("sample_*.txt"))
SZ_FIXTURES = sorted(FIXTURES_DIR.glob("sz_*.txt"))

# 真实会话 ID（用于真实数据测试，在 CI 或离线环境自动跳过）
REAL_SESSION_ID = "blend-pencil"


# ── 辅助函数 ──

def load_fixture(name: str) -> dict:
    """加载 fixture JSON 文件。"""
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_screen_fixture(name: str) -> str:
    """加载 fixture 屏幕快照文本（VT 序列）。"""
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Transcript 解析测试 ──

class TestTranscriptParsing:
    """测试 transcript JSON 解析。"""

    def test_parse_elemental_branch(self):
        """解析完整 transcript fixture：验证消息结构。"""
        data = load_fixture("elemental-branch.json")
        assert data["schema_version"] == "ATIF-v1.7"
        assert data["session_id"] == "elemental-branch"

        meta, messages = messages_transcript.parse_transcript(data)

        # 元数据
        assert meta["id"] == "elemental-branch"
        assert meta["model"] == "SWE-1.6 Slow"
        assert meta["cli_version"] == "3000.4.16"

        # 消息列表（过滤了 system 消息）
        assert len(messages) > 0
        for msg in messages:
            assert msg.role in ("user", "assistant")
            assert msg.ts > 0
            assert msg.ts_iso

    def test_parse_user_message(self):
        """user 消息解析。"""
        data = load_fixture("victorious-squid.json")
        meta, messages = messages_transcript.parse_transcript(data)

        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) > 0
        # 首条 user 消息应有文本
        first_user = user_msgs[0]
        assert len(first_user.items) > 0
        assert first_user.items[0].type == "text"
        assert first_user.items[0].text

    def test_parse_assistant_message(self):
        """assistant 消息解析：text / thinking / tool_use / tool_result 四种类型。"""
        data = load_fixture("victorious-squid.json")
        meta, messages = messages_transcript.parse_transcript(data)

        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) > 0

        for msg in assistant_msgs:
            assert msg.role == "assistant"
            assert msg.model
            assert msg.model == "SWE-1.6 Slow"

    def test_parse_tool_calls_and_observation(self):
        """tool_calls → tool_use / observation → tool_result 映射。"""
        data = load_fixture("elemental-branch.json")
        meta, messages = messages_transcript.parse_transcript(data)

        # 找到含 tool_use 的消息
        tool_msgs = [m for m in messages if m.role == "assistant" and any(
            i.type == "tool_use" for i in m.items
        )]
        assert len(tool_msgs) > 0

        for msg in tool_msgs:
            types = [i.type for i in msg.items]
            assert "tool_use" in types
            # 验证 tool_use 结构
            for item in msg.items:
                if item.type == "tool_use":
                    assert item.tool_use is not None
                    assert item.tool_use.tool_call_id
                    assert item.tool_use.name
                if item.type == "tool_result":
                    assert item.tool_result is not None
                    assert item.tool_result.tool_call_id
                    assert item.tool_result.output

    def test_parse_thinking(self):
        """reasoning_content → thinking 映射。"""
        data = load_fixture("elemental-branch.json")
        meta, messages = messages_transcript.parse_transcript(data)

        # 找到含 thinking 的消息
        thinking_msgs = [m for m in messages if m.role == "assistant" and any(
            i.type == "thinking" for i in m.items
        )]
        assert len(thinking_msgs) > 0

        for msg in thinking_msgs:
            for item in msg.items:
                if item.type == "thinking":
                    assert item.text
                    assert len(item.text) > 10

    def test_parse_metrics(self):
        """metrics 解析。"""
        data = load_fixture("elemental-branch.json")
        meta, messages = messages_transcript.parse_transcript(data)

        assistant_msgs = [m for m in messages if m.role == "assistant"]
        msgs_with_metrics = [m for m in assistant_msgs if m.metrics is not None]
        if msgs_with_metrics:
            metrics = msgs_with_metrics[0].metrics
            assert hasattr(metrics, "prompt_tokens")
            assert hasattr(metrics, "completion_tokens")
            assert hasattr(metrics, "cached_tokens")

    def test_no_system_messages(self):
        """system 消息被过滤，不加入消息列表。"""
        data = load_fixture("elemental-branch.json")
        meta, messages = messages_transcript.parse_transcript(data)

        for msg in messages:
            assert msg.role != "system"

    def test_timestamp_format(self):
        """时间戳转换为毫秒 int 且保留 ISO。"""
        data = load_fixture("victorious-squid.json")
        meta, messages = messages_transcript.parse_transcript(data)

        for msg in messages:
            assert isinstance(msg.ts, int)
            assert msg.ts > 0
            assert isinstance(msg.ts_iso, str)
            assert "T" in msg.ts_iso  # ISO 格式


# ── 屏幕快照解析测试 ──

class TestScreenParsing:
    """测试屏幕快照解析。"""

    def test_parse_idle(self):
        """欢迎页（空闲态）解析。"""
        vt = load_screen_fixture("sample_idle.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.screen_type == "main"
        assert state.model_display
        # 输入框为 placeholder 时为空
        assert state.input_text == ""

    def test_parse_conversation_idle(self):
        """对话空闲态解析。"""
        vt = load_screen_fixture("sample_conversation_idle.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.screen_type == "conversation"
        # 输入框为 placeholder 时为空
        assert state.input_text == ""

    def test_parse_thinking(self):
        """思考中状态解析。"""
        vt = load_screen_fixture("sample_working.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "thinking"
        assert state.screen_type == "conversation"

    def test_parse_awaiting_approval(self):
        """权限请求状态解析。"""
        vt = load_screen_fixture("sample_awaiting_approval.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "awaiting_approval"
        assert state.screen_type == "conversation"

    def test_parse_context_percent(self):
        """上下文百分比解析。"""
        vt = load_screen_fixture("sample_conversation_idle.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.context_percent > 0
        assert state.context_percent <= 100

    def test_parse_model_display(self):
        """模型名解析。"""
        vt = load_screen_fixture("sample_idle.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.model_display
        assert "SWE" in state.model_display or "Slow" in state.model_display

    def test_parse_narrow_screen(self):
        """窄屏（40x10）解析。"""
        vt = load_screen_fixture("sz_40x10.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.screen_type == "main"
        assert state.input_text == ""
        assert state.model_display

    def test_parse_small_screen(self):
        """小屏（60x15）解析：placeholder 尾部混入分隔线 ─。"""
        vt = load_screen_fixture("sz_60x15.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.screen_type == "main"
        assert state.input_text == ""  # placeholder 被过滤
        assert state.model_display

    def test_parse_medium_screen(self):
        """中屏（80x24）解析。"""
        vt = load_screen_fixture("sz_80x24.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.screen_type == "main"
        assert state.input_text == ""
        assert state.model_display

    def test_parse_wide_screen(self):
        """宽屏（120x40 / 200x50）解析。"""
        for name in ("sz_120x40.txt", "sz_200x50.txt"):
            vt = load_screen_fixture(name)
            state = screen.parse_screen_snapshot(vt)
            assert state.ai_status == "idle"
            assert state.screen_type == "main"
            assert state.input_text == ""
            assert state.model_display

    def test_parse_input_pending(self):
        """输入待提交状态：输入框有文字，AI 空闲。"""
        vt = load_screen_fixture("sample_input_pending.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"
        assert state.input_text == "帮我看看桌面"
        assert state.screen_type == "main"  # 欢迎页输入待提交（与其他项目一致）

    def test_parse_denied(self):
        """权限拒绝状态。"""
        vt = load_screen_fixture("sample_denied.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "idle"  # 拒绝后回到空闲
        assert state.screen_type == "conversation"
        assert state.context_percent > 0

    def test_parse_asking(self):
        """提问框状态（ask_user_question）。"""
        vt = load_screen_fixture("sample_asking.txt")
        state = screen.parse_screen_snapshot(vt)
        assert state.ai_status == "asking"
        assert state.screen_type == "conversation"


# ── 会话定位测试 ──

class TestSessionLocator:
    """测试会话定位。"""

    def test_find_all_sessions_fixtures(self):
        """从 fixtures 目录推断会话列表（降级模式）。"""
        # 使用自定义 devin_home 指向 fixtures 目录
        sessions = session_locator._scan_transcripts_dir(str(FIXTURES_DIR))
        assert len(sessions) >= 2
        ids = [s["session_id"] for s in sessions]
        assert "elemental-branch" in ids
        assert "victorious-squid" in ids

    def test_find_all_sessions_real(self):
        """从真实 SQLite 读取会话列表。"""
        sessions = session_locator.find_all_sessions()
        if not sessions:
            return  # 离线环境跳过
        assert len(sessions) > 0
        for s in sessions:
            assert "session_id" in s
            assert s["session_id"]

    def test_find_transcript_real(self):
        """查找真实会话 transcript。"""
        if not os.path.isdir(
            os.path.join(os.environ.get("APPDATA", ""), "devin", "cli", "transcripts")
        ):
            return  # 离线环境跳过
        try:
            path = session_locator.find_transcript_file(REAL_SESSION_ID)
            assert path.endswith(".json")
            assert os.path.isfile(path)
        except FileNotFoundError:
            pass  # 会话不存在时跳过


# ── 输出格式化测试 ──

class TestOutput:
    """测试 JSON 输出格式化。"""

    def test_parse_roundtrip(self):
        """解析 transcript → JSON 序列化 → 可反序列化。"""
        data = load_fixture("victorious-squid.json")
        meta, messages = messages_transcript.parse_transcript(data)

        from src.entities import Session, Usage
        session = Session(
            id=meta.get("id", ""),
            model=meta.get("model", ""),
            cli_version=meta.get("cli_version", ""),
            source=meta.get("source", ""),
        )

        result = ParseResult(session=session, messages=messages)
        json_str = output.to_json(result)
        parsed = json.loads(json_str)

        assert "session" in parsed
        assert "messages" in parsed
        assert parsed["session"]["id"] == "victorious-squid"
        assert len(parsed["messages"]) > 0

    def test_live_state_in_output(self):
        """live_state 在输出中正确出现。"""
        from src.entities import Session, Usage
        session = Session(id="test", model="test")
        live_state = LiveState(ai_status="idle", context_percent=50.0)
        result = ParseResult(session=session, messages=[], live_state=live_state)
        json_str = output.to_json(result)
        parsed = json.loads(json_str)
        assert "live_state" in parsed
        assert parsed["live_state"]["ai_status"] == "idle"
        assert parsed["live_state"]["context_percent"] == 50.0


# ── 集成测试 ──

class TestIntegration:
    """集成测试：完整解析流程。"""

    def test_full_parse_with_live_state(self):
        """完整解析：transcript + 屏幕快照 → ParseResult。"""
        data = load_fixture("victorious-squid.json")
        vt = load_screen_fixture("sample_conversation_idle.txt")

        meta, messages = messages_transcript.parse_transcript(data)
        live_state = screen.parse_screen_snapshot(vt)

        from src.entities import Session, Usage
        session = Session(
            id=meta.get("id", ""),
            model=meta.get("model", ""),
            cli_version=meta.get("cli_version", ""),
            source=meta.get("source", ""),
        )

        result = ParseResult(session=session, messages=messages, live_state=live_state)
        assert result.session.id == "victorious-squid"
        assert len(result.messages) > 0
        assert result.live_state is not None
        assert result.live_state.ai_status in ("idle", "thinking", "tool_running", "awaiting_approval")

        # 验证 JSON 序列化
        json_str = output.to_json(result)
        parsed = json.loads(json_str)
        assert parsed["session"]["id"] == "victorious-squid"
        assert "live_state" in parsed

    def test_real_session_parse(self):
        """真实会话解析（需真实数据存在）。"""
        try:
            path = session_locator.find_transcript_file(REAL_SESSION_ID)
        except FileNotFoundError:
            return  # 会话不存在时跳过

        meta, messages = messages_transcript.load_transcript(path)
        assert len(messages) > 0

        # 验证消息顺序
        roles = [m.role for m in messages]
        assert "user" in roles
        assert "assistant" in roles

        # 验证首条消息为 user
        first = messages[0]
        assert first.role == "user"
        assert first.items[0].type == "text"
        assert first.items[0].text