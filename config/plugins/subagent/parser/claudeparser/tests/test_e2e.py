"""e2e 测试：端到端验证 claudeparser。

测试策略：
- 用真实 Claude Code 会话（9b56c0c7-...）验证 JSONL 解析
- 用收集的屏幕快照样本验证 LiveState 解析
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
from src.entities import Message, ParseResult, Session, LiveState
from src.usecases import ParseSessionUseCase

# 测试用会话（pty-agent 通过 s.ps1 启动的 claude 测试会话）
TEST_SESSION_ID = "9b56c0c7-b398-444b-84c3-9d62108b6f3b"
CLAUDE_DIR = os.path.join(os.path.expanduser("~"), ".claude")

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
# 会话定位
# ──────────────────────────────────────────

def test_find_session_file():
    """验证按 sessionId 定位 jsonl 文件。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    assert path.endswith(f"{TEST_SESSION_ID}.jsonl")
    assert os.path.isfile(path)


def test_find_all_sessions():
    """验证列出全部会话（至少包含测试会话）。"""
    sessions = session_locator.find_all_sessions()
    ids = [s["session_id"] for s in sessions]
    assert TEST_SESSION_ID in ids
    # 按 mtime 倒序
    mt = [s["mtime"] for s in sessions]
    assert mt == sorted(mt, reverse=True)


# ──────────────────────────────────────────
# JSONL 消息历史解析
# ──────────────────────────────────────────

@skip_if_no_session
def test_parse_messages():
    """验证消息解析：数量、角色、时间戳。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)
    assert len(messages) > 0

    roles = {m.role for m in messages}
    assert "user" in roles
    assert "assistant" in roles

    for m in messages:
        assert m.id
        assert m.ts > 0
        assert m.ts_iso


@skip_if_no_session
def test_content_types():
    """验证 4 种内容类型都被解析。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)

    types_found = set()
    for m in messages:
        for item in m.items:
            types_found.add(item.type)

    assert "text" in types_found
    assert "thinking" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


@skip_if_no_session
def test_tool_use():
    """验证工具调用（Glob/Read）解析。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)

    tool_names = set()
    for m in messages:
        for item in m.items:
            if item.type == "tool_use" and item.tool_use:
                tool_names.add(item.tool_use.name)
                assert item.tool_use.tool_call_id
                assert item.tool_use.input

    assert "Glob" in tool_names
    assert "Read" in tool_names


@skip_if_no_session
def test_tool_result():
    """验证工具结果解析（tool_use_id 关联 + content）。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)

    tool_results = []
    for m in messages:
        for item in m.items:
            if item.type == "tool_result" and item.tool_result:
                tool_results.append(item.tool_result)

    assert len(tool_results) > 0
    for tr in tool_results:
        assert tr.tool_call_id
        assert tr.name
        assert tr.result is not None


@skip_if_no_session
def test_user_text_input():
    """验证用户文本输入解析（字符串形态 content）。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)

    user_texts = [m for m in messages if m.role == "user"
                  and m.items and m.items[0].type == "text"]
    assert user_texts  # 真实会话至少包含一条用户文本消息
    assert all((i.text or "") for m in user_texts for i in m.items if i.type == "text")


@skip_if_no_session
def test_assistant_usage_and_effort():
    """验证 assistant 消息的 usage 与 effort。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    messages = messages_jsonl.load_jsonl(path)

    assistant_msgs = [m for m in messages if m.role == "assistant"]
    assert assistant_msgs
    for m in assistant_msgs:
        assert m.model == "sensenova-6.8-flash-lite"
        assert m.effort in ("high", "medium", "low", "auto")
        assert m.usage is not None
        assert m.usage.input_tokens > 0


@skip_if_no_session
def test_session_meta():
    """验证会话元数据（mode/permission_mode/model）。"""
    path = session_locator.find_session_file(TEST_SESSION_ID)
    meta = messages_jsonl.load_meta(path)
    assert meta["mode"] == "normal"
    assert meta["permission_mode"] == "default"
    assert meta["model"] == "sensenova-6.8-flash-lite"


# ──────────────────────────────────────────
# 屏幕快照解析
# ──────────────────────────────────────────

def _load_sample(name):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"sample {name} not found")
    return path.read_text(encoding="utf-8")


def test_parse_screen_idle():
    """验证空闲态屏幕快照解析。"""
    vt_text = _load_sample("sample_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert isinstance(state, LiveState)
    assert state.ai_status == "idle"
    assert state.permission_mode  # 非空
    assert state.input_text == ""  # 空闲输入框为空


def test_parse_screen_has_status_bar():
    """验证状态栏权限模式识别。"""
    vt_text = _load_sample("sample_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert "manual mode" in state.permission_mode


def test_parse_screen_input_text():
    """验证输入框文字提取（模拟有输入的状态）。"""
    lines = [
        "some message content",
        "────────────────────────────────────────────────────────────────",
        "> 帮我看看桌面",
        "────────────────────────────────────────────────────────────────",
        "  ⏸ manual mode on · ? for shortcuts · ← for agents",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.input_text == "帮我看看桌面"


def test_parse_screen_input_pending():
    """验证待提交输入状态（输入框有文字，AI 空闲）。"""
    vt_text = _load_sample("sample_input_pending.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.input_text  # 输入框非空
    assert "manual mode" in state.permission_mode


# ──────────────────────────────────────────
# 多尺寸屏幕快照解析
# ──────────────────────────────────────────

# 各尺寸下预期解析结果（对话中状态）
_SIZE_EXPECTED = {
    "40x10":  {"screen": "conversation", "perm": "manual mode on"},
    "60x15":  {"screen": "conversation", "perm": "manual mode on"},
    "80x24":  {"screen": "conversation", "perm": "manual mode on"},
    "120x40": {"screen": "conversation", "perm": "manual mode on"},
    "200x50": {"screen": "conversation", "perm": "manual mode on"},
}


@pytest.mark.parametrize("size,expected", list(_SIZE_EXPECTED.items()))
def test_parse_screen_multi_size_conversation(size, expected):
    """验证不同终端尺寸下对话中状态解析正确性。

    Claude Code 状态栏按终端宽度自动换行/截断。
    """
    vt_text = _load_sample(f"sz_{size}.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle", f"{size}: ai_status mismatch"
    assert state.screen_type == expected["screen"], f"{size}: screen mismatch"
    assert expected["perm"] in state.permission_mode, f"{size}: perm mismatch"
    # 窄屏（40x10/60x15）欢迎页框滚出屏幕后 cwd 可能不可见；
    # 可见时须非空（宽屏应正确解析）
    if state.cwd_display:
        assert "\\" in state.cwd_display or state.cwd_display.startswith("~")


def test_parse_screen_awaiting_approval():
    """验证权限请求状态（awaiting_approval）。"""
    vt_text = _load_sample("sample_awaiting_approval.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_approval"


def test_parse_screen_working():
    """验证工具执行状态（tool_running）。"""
    vt_text = _load_sample("sample_working.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "tool_running"


def test_parse_screen_ask():
    """验证 AskUserQuestion 情景（awaiting_answer）。"""
    vt_text = _load_sample("sample_ask.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_answer"
    assert state.screen_type == "conversation"


@skip_if_no_session
def test_ask_user_question():
    """验证 AskUserQuestion 工具调用与结果解析（JSONL）。"""
    path = session_locator.find_session_file("f2341e7a-623a-49fa-896e-78b144e40bfc")
    if not path:
        pytest.skip("ask session not found")
    messages = messages_jsonl.load_jsonl(path)

    found_use = False
    found_result = False
    for m in messages:
        for item in m.items:
            if item.type == "tool_use" and item.tool_use and item.tool_use.name == "AskUserQuestion":
                found_use = True
                questions = item.tool_use.input.get("questions", [])
                assert questions
                assert questions[0]["question"]
                assert questions[0]["options"]
            if item.type == "tool_result" and item.tool_result and item.tool_result.name == "AskUserQuestion":
                found_result = True
                assert item.tool_result.success
                assert "Python" in str(item.tool_result.result)

    assert found_use, "AskUserQuestion tool_use not found"
    assert found_result, "AskUserQuestion tool_result not found"


# ──────────────────────────────────────────
# 用例层端到端
# ──────────────────────────────────────────

@skip_if_no_session
def test_usecase_end_to_end():
    """验证 ParseSessionUseCase 端到端（仅 JSONL）。"""
    uc = ParseSessionUseCase(claude_dir=CLAUDE_DIR)
    result = uc.execute(TEST_SESSION_ID)
    assert isinstance(result, ParseResult)
    assert result.session.id == TEST_SESSION_ID
    assert len(result.messages) > 0
    assert result.live_state is None


@skip_if_no_session
def test_usecase_with_screen():
    """验证 ParseSessionUseCase 端到端（JSONL + 屏幕快照）。"""
    vt_text = _load_sample("sample_idle.txt")
    uc = ParseSessionUseCase(claude_dir=CLAUDE_DIR)
    result = uc.execute(TEST_SESSION_ID, screen_snapshot=vt_text)
    assert result.live_state is not None
    assert result.live_state.ai_status == "idle"


@skip_if_no_session
def test_usecase_session_fields():
    """验证会话字段完整性。"""
    uc = ParseSessionUseCase(claude_dir=CLAUDE_DIR)
    result = uc.execute(TEST_SESSION_ID)
    s = result.session
    assert s.cwd  # 真实会话解析出工作目录（不硬编码具体路径）
    assert os.path.isabs(s.cwd) or s.cwd.startswith("~")
    assert s.model == "sensenova-6.8-flash-lite"
    assert s.mode == "normal"
    assert s.permission_mode == "default"
    assert s.usage.input_tokens > 0
    assert s.started_at


# ──────────────────────────────────────────
# 输出格式化
# ──────────────────────────────────────────

@skip_if_no_session
def test_output_json():
    """验证 JSON 输出格式。"""
    uc = ParseSessionUseCase(claude_dir=CLAUDE_DIR)
    result = uc.execute(TEST_SESSION_ID)
    json_str = output.to_json(result)
    parsed = json.loads(json_str)
    assert "session" in parsed
    assert "messages" in parsed
    assert parsed["session"]["id"] == TEST_SESSION_ID
    assert len(parsed["messages"]) > 0


@skip_if_no_session
def test_output_json_message_structure():
    """验证消息 JSON 结构（items 含 4 种类型）。"""
    uc = ParseSessionUseCase(claude_dir=CLAUDE_DIR)
    result = uc.execute(TEST_SESSION_ID)
    parsed = json.loads(output.to_json(result))

    types_found = set()
    for m in parsed["messages"]:
        for item in m["items"]:
            types_found.add(item["type"])
            if item["type"] == "tool_use":
                assert "tool_call_id" in item["tool_use"]
                assert "name" in item["tool_use"]
            if item["type"] == "tool_result":
                assert "tool_call_id" in item["tool_result"]
                assert "success" in item["tool_result"]

    assert "text" in types_found
    assert "thinking" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


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
                   "--claude-dir", CLAUDE_DIR])
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
    rc = main(["--list", "--claude-dir", CLAUDE_DIR])
    assert rc == 0


def test_cli_missing_session():
    """验证不存在的会话返回非零退出码。"""
    from src.cli import main
    rc = main(["00000000-0000-0000-0000-000000000000",
               "--claude-dir", CLAUDE_DIR])
    assert rc == 1
