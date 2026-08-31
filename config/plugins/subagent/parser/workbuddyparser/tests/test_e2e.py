"""e2e 测试：端到端验证 workbuddyparser。

测试策略：
- 用 fixture 样本（sample_session.jsonl + 屏幕快照）恒执行
- 用真实 WorkBuddy 会话（bb9466e2-...）验证 JSONL 解析
- 验证 CLI 端到端调用
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# 确保能导入 src
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.adapters import messages_jsonl, output, screen, session_locator
from src.entities import Message, ParseResult, Session, LiveState, ToolUse, ToolResult
from src.usecases import ParseSessionUseCase

# 测试用会话（PTY-Agent 启动的 WorkBuddy 测试会话，位于
# ~/.workbuddy/projects/c-Users-alice-Desktop-PTY-Agent/）
TEST_SESSION_ID = "bb9466e2-1697-4b14-8999-5896d8a73bf9"
WORKBUDDY_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy")

# 样本文件目录
SAMPLES_DIR = _ROOT / "tests" / "fixtures"


def _session_available():
    try:
        session_locator.find_session_file(TEST_SESSION_ID)
        return True
    except FileNotFoundError:
        return False


skip_if_no_session = pytest.mark.skipif(
    not _session_available(), reason=f"test session {TEST_SESSION_ID} not found"
)


# ──────────────────────────────────────────
# JSONL fixture 解析（恒执行）
# ──────────────────────────────────────────

def _load_jsonl_fixture():
    path = SAMPLES_DIR / "sample_session.jsonl"
    return path.read_text(encoding="utf-8")


def test_fixture_parse_messages():
    """验证 fixture 消息解析：数量、角色。

    fixture 含 2 个回合，每个回合有 2 个 assistant response cycle
    （reasoning→message+tool→result），共 6 条消息。
    """
    messages, meta = messages_jsonl.parse_jsonl(_load_jsonl_fixture())
    assert len(messages) == 6  # u1, a1, a2, u2, a3, a4

    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "assistant", "user", "assistant", "assistant"]

    for m in messages:
        assert m.ts > 0


def test_fixture_content_types():
    """验证 4 种内容类型都被解析。"""
    messages, _ = messages_jsonl.parse_jsonl(_load_jsonl_fixture())

    types_found = set()
    for m in messages:
        for item in m.items:
            types_found.add(item.type)

    assert "text" in types_found
    assert "thinking" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


def test_fixture_tool_use():
    """验证工具调用（Glob/Read）解析。"""
    messages, _ = messages_jsonl.parse_jsonl(_load_jsonl_fixture())

    tool_names = set()
    for m in messages:
        for item in m.items:
            if item.type == "tool_use" and item.tool_use:
                tool_names.add(item.tool_use.name)
                assert item.tool_use.tool_call_id
                assert item.tool_use.input

    assert "Glob" in tool_names
    assert "Read" in tool_names


def test_fixture_tool_result():
    """验证工具结果解析（callId 关联 + output）。"""
    messages, _ = messages_jsonl.parse_jsonl(_load_jsonl_fixture())

    tool_results = []
    for m in messages:
        for item in m.items:
            if item.type == "tool_result" and item.tool_result:
                tool_results.append(item.tool_result)

    assert len(tool_results) == 2
    for tr in tool_results:
        assert tr.tool_call_id
        assert tr.name in ("Glob", "Read")
        assert tr.output_text
        assert tr.success


def test_fixture_usage():
    """验证 assistant 消息的 usage 解析（message 事件 + function_call 挂 rawUsage）。"""
    messages, _ = messages_jsonl.parse_jsonl(_load_jsonl_fixture())

    assistant_msgs = [m for m in messages if m.role == "assistant"]
    assert len(assistant_msgs) == 4
    for m in assistant_msgs:
        assert m.model == "hy3"
    # message 事件与 function_call 事件都可能挂 rawUsage
    with_usage = [m for m in assistant_msgs if m.usage is not None]
    assert len(with_usage) >= 3
    for m in with_usage:
        assert m.usage.input_tokens > 0
        assert m.usage.output_tokens > 0
        assert m.usage.total_tokens > 0


def test_fixture_meta():
    """验证会话元数据（title/model/started_at）。"""
    messages, meta = messages_jsonl.parse_jsonl(_load_jsonl_fixture())
    assert meta["title"] == "修复网页终端调整大小后显示错乱"
    assert meta["model"] == "hy3"
    assert meta["started_at"] == str(messages[0].ts)


# ──────────────────────────────────────────
# 会话定位
# ──────────────────────────────────────────

@skip_if_no_session
def test_find_session_file():
    """验证按 sessionId 定位 jsonl 文件。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    assert path.endswith(f"{TEST_SESSION_ID}.jsonl")
    assert os.path.isfile(path)


@skip_if_no_session
def test_find_all_sessions():
    """验证列出全部会话（至少包含测试会话）。"""
    sessions = session_locator.find_all_sessions()
    ids = [s["session_id"] for s in sessions]
    assert TEST_SESSION_ID in ids
    # 按 mtime 倒序
    mt = [s["mtime"] for s in sessions]
    assert mt == sorted(mt, reverse=True)


def test_list_running_filters_prewarm():
    """验证运行中会话列表过滤 prewarm。"""
    sessions = session_locator.list_running_sessions(WORKBUDDY_DIR)
    for s in sessions:
        assert s.get("kind") != "prewarm"


@skip_if_no_session
def test_db_meta():
    """验证 workbuddy.db 元数据读取。"""
    meta = session_locator.load_db_meta(TEST_SESSION_ID)
    if meta is not None:
        assert meta["id"] == TEST_SESSION_ID
        assert "cwd" in meta
        assert "mode" in meta


# ──────────────────────────────────────────
# 屏幕快照解析
# ──────────────────────────────────────────

def _load_sample(name):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"sample {name} not found")
    return path.read_text(encoding="utf-8")


def test_parse_screen_idle():
    """验证空闲态（欢迎页 main）屏幕快照解析。"""
    vt_text = _load_sample("sample_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert isinstance(state, LiveState)
    assert state.ai_status == "idle"
    assert state.screen_type == "main"
    assert state.input_text == ""  # 空闲输入框为空（placeholder 不算）


def test_parse_screen_conversation_idle():
    """验证对话空闲态（conversation）。"""
    vt_text = _load_sample("sample_conversation_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.screen_type == "conversation"
    assert state.input_text == ""  # placeholder 不算输入


def test_parse_screen_input_pending():
    """验证待提交输入状态（输入框有文字，AI 空闲）。"""
    vt_text = _load_sample("sample_input_pending.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.input_text == "run ls in current dir"


def test_parse_screen_working():
    """验证思考/工作状态（thinking）。"""
    vt_text = _load_sample("sample_working.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "thinking"
    assert state.thinking_on == "Thinking on"


def test_parse_screen_awaiting_approval():
    """验证权限请求状态（awaiting_approval）。"""
    vt_text = _load_sample("sample_awaiting_approval.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_approval"


def test_parse_screen_lines_input():
    """验证 parse_screen_lines 输入框提取。"""
    lines = [
        "some message content",
        "────────────────────────────────────────────────────────────────",
        "> 帮我看看桌面",
        "────────────────────────────────────────────────────────────────",
        "? for shortcuts  ← 1 agent",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.input_text == "帮我看看桌面"
    assert state.ai_status == "idle"


def test_parse_screen_bypass_permission_mode():
    """验证 bypass 权限模式的状态栏解析。"""
    vt_text = _load_sample("sample_bypass_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.permission_mode == "bypass permissions on"
    assert state.screen_type == "conversation"


def test_parse_screen_asking():
    """验证 AI 提问对话框（AskUserQuestion）识别为 asking 状态。"""
    vt_text = _load_sample("sample_asking.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "asking"
    assert state.screen_type == "conversation"


# ──────────────────────────────────────────
# 多尺寸屏幕快照解析（真实 PTY-Agent 抓取）
# ──────────────────────────────────────────

# 各尺寸下预期解析结果（空闲对话状态）
# CodeBuddy 增量渲染不含全屏 CUP，需显式传入终端尺寸
_SIZE_EXPECTED = {
    "40_10":  {"cols": 40,  "rows": 10, "screen": "conversation"},
    "60_15":  {"cols": 60,  "rows": 15, "screen": "conversation"},
    "80_24":  {"cols": 80,  "rows": 24, "screen": "conversation"},
    "120_40": {"cols": 120, "rows": 40, "screen": "conversation"},
    "200_50": {"cols": 200, "rows": 50, "screen": "conversation"},
}


@pytest.mark.parametrize("size,expected", list(_SIZE_EXPECTED.items()))
def test_parse_screen_multi_size_conversation(size, expected):
    """验证不同终端尺寸下空闲对话状态解析正确性。"""
    vt_text = _load_sample(f"sz_{size}.txt")
    state = screen.parse_screen_snapshot(
        vt_text, columns=expected["cols"], rows=expected["rows"])
    assert state.ai_status == "idle", f"{size}: ai_status mismatch"
    assert state.screen_type == expected["screen"], f"{size}: screen mismatch"
    # 输入框为空（placeholder 不算输入）
    assert state.input_text == "", f"{size}: input_text mismatch"


def test_parse_screen_multi_size_input_text():
    """验证不同尺寸下输入框文字提取（窄屏 placeholder 处理）。"""
    vt_text = _load_sample("sz_200_50.txt")
    state = screen.parse_screen_snapshot(vt_text, columns=200, rows=50)
    # 200x50 欢迎页可见时 placeholder 不产生输入
    assert state.input_text == ""


# ──────────────────────────────────────────
# 用例层端到端
# ──────────────────────────────────────────

@skip_if_no_session
def test_usecase_end_to_end():
    """验证 ParseSessionUseCase 端到端（仅 JSONL）。"""
    uc = ParseSessionUseCase(workbuddy_dir=WORKBUDDY_DIR)
    result = uc.execute(TEST_SESSION_ID)
    assert isinstance(result, ParseResult)
    assert result.session.id == TEST_SESSION_ID
    assert len(result.messages) > 0
    assert result.live_state is None


@skip_if_no_session
def test_usecase_with_screen():
    """验证 ParseSessionUseCase 端到端（JSONL + 屏幕快照）。"""
    vt_text = _load_sample("sample_idle.txt")
    uc = ParseSessionUseCase(workbuddy_dir=WORKBUDDY_DIR)
    result = uc.execute(TEST_SESSION_ID, screen_snapshot=vt_text)
    assert result.live_state is not None
    assert result.live_state.ai_status == "idle"


@skip_if_no_session
def test_usecase_session_fields():
    """验证会话字段完整性。"""
    uc = ParseSessionUseCase(workbuddy_dir=WORKBUDDY_DIR)
    result = uc.execute(TEST_SESSION_ID)
    s = result.session
    assert s.cwd
    assert s.model
    assert s.started_at
    assert s.title  # ai-title 事件


# ──────────────────────────────────────────
# 输出格式化
# ──────────────────────────────────────────

def test_output_json_fixture():
    """验证 fixture 的 JSON 输出格式。"""
    messages, meta = messages_jsonl.parse_jsonl(_load_jsonl_fixture())
    session = Session(id="bb9466e2-1697-4b14-8999-5896d8a73bf9", title=meta.get("title", ""))
    result = ParseResult(session=session, messages=messages)
    json_str = output.to_json(result)
    parsed = json.loads(json_str)
    assert "session" in parsed
    assert "messages" in parsed
    assert parsed["session"]["id"] == "bb9466e2-1697-4b14-8999-5896d8a73bf9"
    assert len(parsed["messages"]) == 6

    # tool_use / tool_result 结构
    for m in parsed["messages"]:
        for item in m["items"]:
            if item["type"] == "tool_use":
                assert "tool_call_id" in item["tool_use"]
                assert "name" in item["tool_use"]
                assert "input" in item["tool_use"]
            if item["type"] == "tool_result":
                assert "tool_call_id" in item["tool_result"]
                assert "success" in item["tool_result"]
                assert "output_text" in item["tool_result"]


@skip_if_no_session
def test_output_json():
    """验证真实会话 JSON 输出格式。"""
    uc = ParseSessionUseCase(workbuddy_dir=WORKBUDDY_DIR)
    result = uc.execute(TEST_SESSION_ID)
    json_str = output.to_json(result)
    parsed = json.loads(json_str)
    assert "session" in parsed
    assert "messages" in parsed
    assert parsed["session"]["id"] == TEST_SESSION_ID
    assert len(parsed["messages"]) > 0


# ──────────────────────────────────────────
# CLI 端到端
# ──────────────────────────────────────────

@skip_if_no_session
def test_cli():
    """验证 CLI 端到端调用。"""
    from src.cli import main
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        out_path = f.name

    try:
        rc = main([TEST_SESSION_ID, "-o", out_path,
                   "--workbuddy-dir", WORKBUDDY_DIR])
        assert rc == 0
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["session"]["id"] == TEST_SESSION_ID
        assert len(data["messages"]) > 0
    finally:
        os.unlink(out_path)


def test_cli_list():
    """验证 CLI --list 列出会话。"""
    from src.cli import main
    rc = main(["--list", "--workbuddy-dir", WORKBUDDY_DIR])
    assert rc == 0


def test_cli_list_running():
    """验证 CLI --list-running 列出运行中会话。"""
    from src.cli import main
    rc = main(["--list-running", "--workbuddy-dir", WORKBUDDY_DIR])
    assert rc == 0


def test_cli_missing_session():
    """验证不存在的会话返回非零退出码。"""
    from src.cli import main
    rc = main(["00000000-0000-0000-0000-000000000000",
               "--workbuddy-dir", WORKBUDDY_DIR])
    assert rc == 1
