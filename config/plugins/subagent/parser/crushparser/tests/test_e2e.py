"""e2e 测试：端到端验证 crushparser。

测试策略：
- 用 fixture 屏幕快照样本 + 真实 crush.db 副本恒执行（若存在）
- 用真实 Crush 会话（716187c5-...）验证端到端（会话存在时）
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

# 测试用会话（调研时创建的真实 Crush 会话）
TEST_SESSION_ID = "716187c5-046d-46a4-b8ce-00a33067b620"
REAL_DATA_DIR = os.path.join(os.path.expanduser("~"), ".crush")

# 样本文件目录
SAMPLES_DIR = _ROOT / "tests" / "fixtures"


def _session_available():
    try:
        return session_locator.find_session(TEST_SESSION_ID) is not None
    except Exception:
        return False


skip_if_no_session = pytest.mark.skipif(
    not _session_available(), reason=f"test session {TEST_SESSION_ID} not found"
)


# ──────────────────────────────────────────
# XXH3 hash
# ──────────────────────────────────────────

def test_hash_session_id():
    """验证 XXH3-128 hash 与 Go zeebo/xxh3 一致（32 位 hex）。"""
    h = session_locator.hash_session_id(TEST_SESSION_ID)
    assert len(h) == 32
    assert h == "e22e38e0f182bbfe44db216bfd430f3e"


# ──────────────────────────────────────────
# 会话定位
# ──────────────────────────────────────────

def test_find_session_real():
    """验证按 UUID 查询真实会话。"""
    s = session_locator.find_session(TEST_SESSION_ID)
    if s is None:
        pytest.skip(f"test session {TEST_SESSION_ID} not found")
    assert s["id"] == TEST_SESSION_ID
    assert s["data_dir"]


def test_find_session_by_hash_prefix():
    """验证按 hash 前缀查询。"""
    s = session_locator.find_session("e22e38e")
    if s is None:
        pytest.skip(f"test session {TEST_SESSION_ID} not found")
    assert s["id"] == TEST_SESSION_ID


def test_find_all_sessions():
    """验证列出全部会话。"""
    sessions = session_locator.find_all_sessions()
    if not sessions:
        pytest.skip("no sessions found")
    ids = [s["session_id"] for s in sessions]
    assert TEST_SESSION_ID in ids
    # 按 updated_at 倒序
    mt = [s["updated_at"] or 0 for s in sessions]
    assert mt == sorted(mt, reverse=True)


def test_list_running_sessions():
    """验证列出运行中会话（无内部会话）。"""
    sessions = session_locator.list_running_sessions()
    for s in sessions:
        assert "$$" not in s["session_id"]
        assert not s["session_id"].startswith("title-")


# ──────────────────────────────────────────
# 消息解析（真实 DB，会话存在时）
# ──────────────────────────────────────────

def _load_real_messages():
    srow = session_locator.find_session(TEST_SESSION_ID)
    if srow is None:
        pytest.skip(f"test session {TEST_SESSION_ID} not found")
    db_path = os.path.join(srow["data_dir"], "crush.db")
    con = session_locator.open_db(db_path)
    try:
        return messages_db.load_session_messages(con, TEST_SESSION_ID)
    finally:
        con.close()


@skip_if_no_session
def test_real_messages():
    """验证真实消息解析：数量、角色。"""
    messages, usage = _load_real_messages()
    assert len(messages) > 0
    roles = {m.role for m in messages}
    assert "user" in roles
    assert "assistant" in roles
    assert "tool" in roles


@skip_if_no_session
def test_real_content_types():
    """验证内容类型解析。"""
    messages, _ = _load_real_messages()
    types_found = set()
    for m in messages:
        for item in m.items:
            types_found.add(item.type)
    assert "text" in types_found
    assert "tool_use" in types_found
    assert "tool_result" in types_found


@skip_if_no_session
def test_real_user_text():
    """验证用户消息文本解析。"""
    messages, _ = _load_real_messages()
    user_texts = []
    for m in messages:
        if m.role == "user":
            for item in m.items:
                if item.type == "text":
                    user_texts.append(item.text)
    assert user_texts  # 非空
    assert any("hi" in t.lower() for t in user_texts)


@skip_if_no_session
def test_real_tool_use_result():
    """验证工具调用与结果。"""
    messages, _ = _load_real_messages()
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


@skip_if_no_session
def test_real_finish_part():
    """验证 finish part 解析。"""
    messages, _ = _load_real_messages()
    finishes = []
    for m in messages:
        for item in m.items:
            if item.type == "finish" and item.finish:
                finishes.append(item.finish)
    assert finishes
    reasons = {f.reason for f in finishes}
    assert "stop" in reasons or "end_turn" in reasons


@skip_if_no_session
def test_real_usage_aggregate():
    """验证会话级 usage。"""
    _, usage = _load_real_messages()
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0


@skip_if_no_session
def test_real_ts_iso():
    """验证时间戳转换（秒）。"""
    messages, _ = _load_real_messages()
    for m in messages:
        assert m.ts > 0
        assert m.ts_iso


# ──────────────────────────────────────────
# 屏幕快照解析（fixture 恒执行）
# ──────────────────────────────────────────

def _load_sample(name):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"sample {name} not found")
    return path.read_text(encoding="utf-8")


def test_parse_screen_reply_idle():
    """验证简单回复后（conversation 空闲）。"""
    vt_text = _load_sample("sample_reply.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert isinstance(state, LiveState)
    assert state.ai_status == "idle"
    assert state.screen_type == "conversation"
    assert state.input_text == ""  # 占位符不算输入
    assert state.version_display  # v0.90.0
    assert state.model_display  # SenseNova
    assert state.cwd_display  # ~\Desktop\crushparser
    assert state.title  # New Session


def test_parse_screen_tool_done():
    """验证工具调用完成后（conversation 空闲）。"""
    vt_text = _load_sample("sample_tool.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "idle"
    assert state.screen_type == "conversation"
    assert state.input_text == ""  # "Ready for instructions" 是占位符
    assert state.context_percent > 0
    assert state.context_tokens > 0
    assert state.cost_display  # $0.00


def test_parse_screen_awaiting_approval():
    """验证权限请求状态（awaiting_approval）。"""
    vt_text = _load_sample("sample_bash_done.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_approval"
    assert state.screen_type == "conversation"


def test_parse_screen_awaiting_answer():
    """验证 question 工具提问状态（awaiting_answer）。"""
    vt_text = _load_sample("sample_ask.txt")
    state = screen.parse_screen_snapshot(vt_text)
    assert state.ai_status == "awaiting_answer"
    assert state.screen_type == "conversation"
    assert state.input_text == ""  # question 表单下不提取输入
    assert state.cwd_display  # 侧边栏 cwd 应存在
    assert state.model_display  # 模型名应存在


# ──────────────────────────────────────────
# 多尺寸屏幕快照解析（不指定 columns/rows 自动检测）
# ──────────────────────────────────────────

# 各尺寸下预期解析结果（对话空闲状态）
# - 40x10 极窄：紧凑模式，cwd 被终端截断（~\\Desktop\\crushpa…）
# - 60x15 / 80x24：紧凑模式，头部行含 cwd + 上下文%
# - 200x50：宽屏模式，侧边栏完整（含 title）
_SIZE_EXPECTED = {
    "sz_40x10.txt": {"ai": "idle", "screen": "conversation", "input": "",
                     "cwd": "~\\Desktop\\crushpa", "pct": None, "model": ""},
    "sz_60x15.txt": {"ai": "idle", "screen": "conversation", "input": "",
                     "cwd": "~\\Desktop\\crushparser", "pct": 6.0, "model": "SenseNova"},
    "sz_80x24.txt": {"ai": "idle", "screen": "conversation", "input": "",
                     "cwd": "~\\Desktop\\crushparser", "pct": 6.0, "model": "SenseNova"},
    "sz_200x50.txt": {"ai": "idle", "screen": "conversation", "input": "",
                      "cwd": "~\\Desktop\\crushparser", "pct": 6.0,
                      "model": "SenseNova", "title": "Untitled Session"},
}


@pytest.mark.parametrize("fname,expected", list(_SIZE_EXPECTED.items()))
def test_parse_screen_multi_size(fname, expected):
    """验证不同终端尺寸下空闲对话状态解析正确性（自动检测尺寸）。"""
    vt_text = _load_sample(fname)
    state = screen.parse_screen_snapshot(vt_text)  # 不指定 columns/rows
    assert state.ai_status == expected["ai"], f"{fname}: ai_status mismatch"
    assert state.screen_type == expected["screen"], f"{fname}: screen mismatch"
    assert state.input_text == expected["input"], f"{fname}: input mismatch"
    if expected.get("cwd"):
        assert state.cwd_display.startswith(expected["cwd"]), \
            f"{fname}: cwd mismatch: {state.cwd_display!r}"
    if expected.get("pct") is not None:
        assert abs(state.context_percent - expected["pct"]) < 0.01, \
            f"{fname}: pct mismatch: {state.context_percent}"
    if expected.get("model"):
        assert state.model_display == expected["model"], \
            f"{fname}: model mismatch: {state.model_display!r}"
    if expected.get("title"):
        assert state.title == expected["title"], \
            f"{fname}: title mismatch: {state.title!r}"


def test_parse_screen_compact_header():
    """验证紧凑模式头部行解析（cwd + 上下文% 在同一行）。"""
    lines = [
        "   Charm™ CRUSH ╱╱╱ ~\\Desktop\\crushparser • 6% • ctrl+d o…",
        "",
        "    WARN  User denied permission",
        "",
        "   ◇  via SenseNova in 56m24s ───────────────────────────",
        "",
        "   > Ready...",
        " :::",
        " :::",
        " tab focus chat • / or ctrl+p commands • ctrl+l models …",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.ai_status == "idle"
    assert state.input_text == ""  # "Ready..." 是占位符
    assert state.cwd_display == "~\\Desktop\\crushparser"
    assert abs(state.context_percent - 6.0) < 0.01
    assert state.model_display  # 由消息区元数据行补充


def test_parse_screen_lines_input():
    """验证输入框文字提取。"""
    lines = [
        " │ hello",
        "   Hello! How can I help?",
        "   ◇  via SenseNova in 7s ─────────────────────",
        " > 帮我看看桌面",
        " :::",
        " tab focus chat • / or ctrl+p commands • ctrl+l models • ctrl+c quit",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.input_text == "帮我看看桌面"
    assert state.ai_status == "idle"


def test_parse_screen_working():
    """验证工作中状态（Working...）。"""
    lines = [
        " │ think about a complex algorithm",
        "   !^b32^~0a_*b$€~ 7s",
        " > Working...",
        " :::",
        " esc cancel • tab focus chat • / or ctrl+p commands",
    ]
    state = screen.parse_screen_lines(lines)
    assert state.ai_status in ("thinking", "tool_running")


# ──────────────────────────────────────────
# 用例层端到端（真实 DB）
# ──────────────────────────────────────────

@skip_if_no_session
def test_usecase_end_to_end_real():
    """验证 ParseSessionUseCase 端到端（仅 DB）。"""
    uc = ParseSessionUseCase()
    result = uc.execute(TEST_SESSION_ID)
    assert isinstance(result, ParseResult)
    assert result.session.id == TEST_SESSION_ID
    assert len(result.messages) > 0
    assert result.live_state is None


@skip_if_no_session
def test_usecase_with_screen():
    """验证 ParseSessionUseCase 端到端（DB + 屏幕快照）。"""
    vt_text = _load_sample("sample_reply.txt")
    uc = ParseSessionUseCase()
    result = uc.execute(TEST_SESSION_ID, screen_snapshot=vt_text)
    assert result.live_state is not None
    assert result.live_state.ai_status == "idle"


@skip_if_no_session
def test_usecase_session_fields():
    """验证会话字段完整性。"""
    uc = ParseSessionUseCase()
    result = uc.execute(TEST_SESSION_ID)
    s = result.session
    assert s.title
    assert s.data_dir
    assert s.started_at
    assert s.message_count > 0


# ──────────────────────────────────────────
# 输出格式化
# ──────────────────────────────────────────

@skip_if_no_session
def test_output_json():
    """验证 JSON 输出格式。"""
    uc = ParseSessionUseCase()
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
        rc = main([TEST_SESSION_ID, "-o", out_path])
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
    rc = main(["--list"])
    assert rc == 0


def test_cli_missing_session():
    """验证不存在的会话返回非零退出码。"""
    from src.cli import main
    rc = main(["00000000-0000-0000-0000-000000000000"])
    assert rc == 1