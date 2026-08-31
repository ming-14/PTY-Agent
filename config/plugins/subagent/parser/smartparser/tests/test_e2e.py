"""e2e 测试：端到端验证 smartparser。

测试策略：
- fixture JSONL 样本恒执行（离线）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from src.adapters import messages_jsonl, output, screen, session_locator
from src.entities import LiveState, Message, ParseResult, Session
from src.usecases import ParseSessionUseCase

SAMPLES_DIR = _ROOT / "tests" / "fixtures"
FIXTURE_JSONL = SAMPLES_DIR / "sample_session.jsonl"


# ── JSONL 消息解析 ────────────────────────────────────

def test_parse_messages():
    """验证 fixture 消息解析：数量、角色"""
    meta, messages = messages_jsonl.load_jsonl_with_meta(str(FIXTURE_JSONL))
    assert len(messages) == 4
    assert messages[0].role == "user"  # 第一条是 AI 消息（user）
    assert messages[1].role == "assistant"  # 人类回复
    for m in messages:
        assert m.items
        assert m.items[0].type == "text"


def test_content_types():
    """验证只有 text 类型"""
    meta, messages = messages_jsonl.load_jsonl_with_meta(str(FIXTURE_JSONL))
    for m in messages:
        for item in m.items:
            assert item.type == "text"
            assert item.text


def test_roles_alternate():
    """验证角色交替：user→assistant→user→assistant"""
    meta, messages = messages_jsonl.load_jsonl_with_meta(str(FIXTURE_JSONL))
    for i, m in enumerate(messages):
        expected = "user" if i % 2 == 0 else "assistant"
        assert m.role == expected, "msg %d: role=%s != %s" % (i, m.role, expected)


# ── 屏幕解析 ──────────────────────────────────────────

def test_screen_idle():
    """验证空闲状态解析"""
    text = ("─" * 50 + "\n Smart Chat — h1 \n" + "─" * 50 + "\n"
            "[You] hello\n" + "─" * 50 + "\n"
            "idle（等待 AI 消息）\n" + "─" * 50)
    st = screen.parse_screen_snapshot(text)
    assert st.ai_status == "idle"


def test_screen_working():
    """验证人类工作中状态（busy）"""
    text = ("─" * 50 + "\n Smart Chat — h1 \n" + "─" * 50 + "\n"
            "[You] 帮我检查 README\n" + "─" * 50 + "\n"
            "Smart工作中…\n" + "─" * 50)
    st = screen.parse_screen_snapshot(text)
    assert st.ai_status == "tool_running"


def test_screen_sent():
    """验证人类已提交状态（回合完成 → idle）"""
    text = ("─" * 50 + "\n Smart Chat — h1 \n" + "─" * 50 + "\n"
            "[Smart] 好的我看一下\n" + "─" * 50 + "\n"
            "Smart已回复\n" + "─" * 50)
    st = screen.parse_screen_snapshot(text)
    assert st.ai_status == "idle"


# ── 输出格式化 ────────────────────────────────────────

def test_output_json():
    """验证 JSON 输出格式"""
    meta, messages = messages_jsonl.load_jsonl_with_meta(str(FIXTURE_JSONL))
    session = Session(id="h-test-1", title="test")
    result = ParseResult(session=session, messages=messages)
    json_str = output.to_json(result)
    parsed = json.loads(json_str)
    assert "session" in parsed
    assert "messages" in parsed
    assert len(parsed["messages"]) == 4
    assert parsed["messages"][0]["role"] == "user"
    assert parsed["messages"][1]["role"] == "assistant"


# ── 用例层 ────────────────────────────────────────────

def test_usecase_fixture():
    """验证 ParseSessionUseCase 端到端"""
    uc = ParseSessionUseCase(data_dir=str(SAMPLES_DIR))
    result = uc.execute("sample_session")
    assert isinstance(result, ParseResult)
    assert result.session.id == "sample_session"
    assert len(result.messages) == 4
    assert result.live_state is None


def test_usecase_list():
    """验证列出会话"""
    uc = ParseSessionUseCase(data_dir=str(SAMPLES_DIR))
    sessions = uc.list_sessions()
    assert len(sessions) >= 1