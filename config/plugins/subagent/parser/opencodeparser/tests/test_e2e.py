"""e2e 测试：端到端验证 opencodeparser。

测试策略：
- 用 fixture DB（sample_opencode.db）+ 屏幕快照样本恒执行
- 用真实 opencode 会话（ses_fd3a39bd4ffe9Z2gEdw8ijXs3x）验证端到端（不存在时跳过）
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

from src.adapters import messages_db, output, screen, session_locator
from src.entities import LiveState, Message, ParseResult, Session, ToolUse
from src.usecases import ParseSessionUseCase

# 测试用会话（调研时创建的 opencode 测试会话）
TEST_SESSION_ID = "ses_fd3a39bd4ffe9Z2gEdw8ijXs3x"
DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode")

# 样本文件目录
SAMPLES_DIR = _ROOT / "tests" / "fixtures"
FIXTURE_DB = SAMPLES_DIR / "sample_opencode.db"
FIXTURE_DATA_DIR = str(SAMPLES_DIR)  # 目录含 sample_opencode.db


def _session_available():
    try:
        session_locator.find_session(TEST_SESSION_ID)
        return True
    except (FileNotFoundError, KeyError):
        return False


skip_if_no_session = pytest.mark.skipif(
    not _session_available(), reason=f"test session {TEST_SESSION_ID} not found"
)


# ──────────────────────────────────────────
# fixture DB 消息解析（恒执行）
# ──────────────────────────────────────────

def _load_fixture_messages():
    con = session_locator.open_db(FIXTURE_DATA_DIR)
    try:
        return messages_db.load_session_messages(con, TEST_SESSION_ID)
    finally:
        con.close()


def test_fixture_messages():
    """验证 fixture 消息解析：数量、角色。"""
    messages, usage = _load_fixture_messages()
    assert len(messages) > 0
    roles = {m.role for m in messages}
    assert "user" in roles
    assert "assistant" in roles


def test_fixture_content_types():
    """验证 4 种内容类型都被解析。"""
    messages, _ = _load_fixture_messages()
    types_found = set()
    for m in messages:
        for item in m.items:
            types_found.add(item.type)
    assert "text" in types_found
    assert "thinking" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


def test_fixture_user_text():
    """验证用户消息文本解析。"""
    messages, _ = _load_fixture_messages()
    user_texts = []
    for m in messages:
        if m.role == "user":
            for item in m.items:
                if item.type == "text":
                    user_texts.append(item.text)
    assert user_texts  # 非空
    assert any("你好" in t or "dir" in t or "test" in t for t in user_texts)


def test_fixture_thinking():
    """验证思考（reasoning part）解析为 thinking。"""
    messages, _ = _load_fixture_messages()
    thinking = []
    for m in messages:
        for item in m.items:
            if item.type == "thinking":
                thinking.append(item.text or "")
    assert thinking  # 至少一条 thinking


def test_fixture_tool_use_result():
    """验证工具调用与结果（tool part）。"""
    messages, _ = _load_fixture_messages()
    tool_uses = []
    tool_results = []
    for m in messages:
        for item in m.items:
            if item.type == "tool_use" and item.tool_use:
                tool_uses.append(item.tool_use)
            if item.type == "tool_result" and item.tool_result:
                tool_results.append(item.tool_result)
    assert tool_uses
    assert tool_results
    for tu in tool_uses:
        assert tu.tool_call_id
        assert tu.name
    for tr in tool_results:
        assert tr.tool_call_id
        assert tr.name


def test_fixture_usage_aggregate():
    """验证会话级 usage 聚合。"""
    messages, usage = _load_fixture_messages()
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


def test_fixture_no_step_parts():
    """验证 step-start / step-finish 被过滤。"""
    messages, _ = _load_fixture_messages()
    for m in messages:
        for item in m.items:
            assert item.type not in ("step-start", "step-finish")


def test_fixture_ts_iso():
    """验证时间戳转换。"""
    messages, _ = _load_fixture_messages()
    for m in messages:
        assert m.ts > 0
        assert m.ts_iso


# ──────────────────────────────────────────
# 会话定位（fixture DB）
# ──────────────────────────────────────────

def test_find_session_fixture():
    """验证按 session_id 查询会话。"""
    s = session_locator.find_session(TEST_SESSION_ID, FIXTURE_DATA_DIR)
    assert s is not None
    assert s["id"] == TEST_SESSION_ID
    assert s["title"]


def test_find_all_sessions_fixture():
    """验证列出全部会话。"""
    sessions = session_locator.find_all_sessions(FIXTURE_DATA_DIR)
    ids = [s["session_id"] for s in sessions]
    assert TEST_SESSION_ID in ids


# ──────────────────────────────────────────
# 屏幕快照解析（fixture 恒执行）
# ──────────────────────────────────────────

def _load_sample(name):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"sample {name} not found")
    return path.read_text(encoding="utf-8")


def test_parse_screen_idle():
    """验证欢迎页（main）解析。"""
    vt_text = _load_sample("sample_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert isinstance(state, LiveState)
    assert state.ai_status == "idle"
    assert state.screen_type == "main"
    assert state.input_text == ""  # placeholder 不算输入
    assert state.version_display  # 1.18.21


def test_parse_screen_input_pending():
    """验证输入待提交状态（输入框有文字，AI 空闲）。"""
    vt_text = _load_sample("sample_input_pending.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.screen_type == "main"
    assert state.input_text  # 非空


def test_parse_screen_conversation_idle():
    """验证对话空闲态（conversation）。"""
    vt_text = _load_sample("sample_conversation_idle.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.screen_type == "conversation"


# ──────────────────────────────────────────
# 多尺寸屏幕快照解析
# ──────────────────────────────────────────

# 各尺寸下预期解析结果（对话空闲状态）
# 40x10 极窄屏下 opencode 渲染重叠（Build 行与输入框重叠），
# input 无法可靠提取，但 ai_status/screen_type 应正确
_SIZE_EXPECTED = {
    "40x10":  {"screen": "conversation", "model": False},
    "60x15":  {"screen": "conversation", "model": True},
    "80x24":  {"screen": "conversation", "model": True},
    "120x40": {"screen": "conversation", "model": True},
    "200x50": {"screen": "conversation", "model": True},
}


@pytest.mark.parametrize("size,expected", list(_SIZE_EXPECTED.items()))
def test_parse_screen_multi_size_conversation(size, expected):
    """验证不同终端尺寸下空闲对话状态解析正确性。"""
    vt_text = _load_sample(f"sz_{size}.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle", f"{size}: ai_status mismatch"
    assert state.screen_type == expected["screen"], f"{size}: screen mismatch"
    if expected["model"]:
        assert state.model_display, f"{size}: model_display should be non-empty"
        assert state.cwd_display, f"{size}: cwd_display should be non-empty"


def test_parse_screen_right_panel():
    """验证宽屏右侧栏（Context 面板）字段提取。

    200x50 宽屏下右侧栏可见：
    ```
    Context
    10,556 tokens
    1% used
    $0.00 spent
    LSP / LSPs are disabled
    ```
    """
    vt_text = _load_sample("sz_200x50.txt")
    state = screen.parse_screen_snapshot(vt_text)
    # 右侧栏在 200x50 下可见
    assert state.context_tokens > 0, f"context_tokens mismatch: {state.context_tokens}"
    assert state.context_percent > 0, f"context_percent mismatch: {state.context_percent}"
    assert state.cost_display, f"cost_display empty: {state.cost_display!r}"


def test_parse_screen_working():
    """验证工作中状态。"""
    vt_text = _load_sample("sample_working.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status in ("thinking", "tool_running")


def test_parse_screen_awaiting_approval():
    """验证权限请求状态（awaiting_approval）。"""
    vt_text = _load_sample("sample_awaiting_approval.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_approval"


def test_parse_screen_ask():
    """验证 question 工具提问状态（awaiting_answer）。"""
    vt_text = _load_sample("sample_ask.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_answer"
    assert state.screen_type == "conversation"


def test_parse_screen_lines_input():
    """验证 parse_screen_lines 输入框提取。"""
    lines = [
        "some message content",
        "╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
        "┃ 帮我看看桌面",
        "╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
        "C:\\Users\\alice\\Desktop\\opencodeparser   10.7K (1%)  ctrl+p commands    • OpenCode 1.18.21",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.input_text == "帮我看看桌面"
    assert state.ai_status == "idle"


# ──────────────────────────────────────────
# 用例层端到端（fixture DB）
# ──────────────────────────────────────────

def test_usecase_fixture():
    """验证 ParseSessionUseCase 端到端（仅 DB）。"""
    uc = ParseSessionUseCase(data_dir=FIXTURE_DATA_DIR)
    result = uc.execute(TEST_SESSION_ID)
    assert isinstance(result, ParseResult)
    assert result.session.id == TEST_SESSION_ID
    assert len(result.messages) > 0
    assert result.live_state is None


def test_usecase_fixture_with_screen():
    """验证 ParseSessionUseCase 端到端（DB + 屏幕快照）。"""
    vt_text = _load_sample("sample_conversation_idle.txt")
    uc = ParseSessionUseCase(data_dir=FIXTURE_DATA_DIR)
    result = uc.execute(TEST_SESSION_ID, screen_snapshot=vt_text)
    assert result.live_state is not None
    assert result.live_state.ai_status == "idle"


def test_usecase_fixture_session_fields():
    """验证会话字段完整性。"""
    uc = ParseSessionUseCase(data_dir=FIXTURE_DATA_DIR)
    result = uc.execute(TEST_SESSION_ID)
    s = result.session
    assert s.cwd
    assert s.title
    assert s.agent
    assert s.model
    assert s.started_at
    assert s.usage.input_tokens > 0


# ──────────────────────────────────────────
# 输出格式化
# ──────────────────────────────────────────

def test_output_json_fixture():
    """验证 fixture 的 JSON 输出格式。"""
    uc = ParseSessionUseCase(data_dir=FIXTURE_DATA_DIR)
    result = uc.execute(TEST_SESSION_ID)
    json_str = output.to_json(result)
    parsed = json.loads(json_str)
    assert "session" in parsed
    assert "messages" in parsed
    assert parsed["session"]["id"] == TEST_SESSION_ID
    assert len(parsed["messages"]) > 0

    # 内容类型齐全
    types_found = set()
    for m in parsed["messages"]:
        for item in m["items"]:
            types_found.add(item["type"])
            if item["type"] == "tool_use":
                assert "tool_call_id" in item["tool_use"]
                assert "name" in item["tool_use"]
                assert "input" in item["tool_use"]
            if item["type"] == "tool_result":
                assert "tool_call_id" in item["tool_result"]
                assert "success" in item["tool_result"]
    assert "text" in types_found
    assert "thinking" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


# ──────────────────────────────────────────
# CLI 端到端（fixture DB）
# ──────────────────────────────────────────

def test_cli_fixture():
    """验证 CLI 端到端调用（fixture DB）。"""
    from src.cli import main
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        out_path = f.name

    try:
        rc = main([TEST_SESSION_ID, "-o", out_path,
                   "--data-dir", FIXTURE_DATA_DIR])
        assert rc == 0
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["session"]["id"] == TEST_SESSION_ID
        assert len(data["messages"]) > 0
    finally:
        os.unlink(out_path)


def test_cli_list_fixture():
    """验证 CLI --list 列出会话（fixture DB）。"""
    from src.cli import main
    rc = main(["--list", "--data-dir", FIXTURE_DATA_DIR])
    assert rc == 0


def test_cli_missing_session():
    """验证不存在的会话返回非零退出码。"""
    from src.cli import main
    rc = main(["ses_00000000000000000000000000",
               "--data-dir", FIXTURE_DATA_DIR])
    assert rc == 1


# ──────────────────────────────────────────
# 真实会话端到端（会话存在时）
# ──────────────────────────────────────────

@skip_if_no_session
def test_usecase_end_to_end_real():
    """验证真实会话端到端。"""
    uc = ParseSessionUseCase(data_dir=DATA_DIR)
    result = uc.execute(TEST_SESSION_ID)
    assert isinstance(result, ParseResult)
    assert result.session.id == TEST_SESSION_ID
    assert len(result.messages) > 0


@skip_if_no_session
def test_find_all_sessions_real():
    """验证列出全部真实会话。"""
    sessions = session_locator.find_all_sessions(DATA_DIR)
    ids = [s["session_id"] for s in sessions]
    assert TEST_SESSION_ID in ids
    # 按 time_updated 倒序
    mt = [s["time_updated"] for s in sessions]
    assert mt == sorted(mt, reverse=True)