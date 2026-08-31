"""消息历史 JSONL 解析适配器。

将 smartagent.py 服务端写入的 <sid>.jsonl 解析为 Message 列表。

事件类型（smartagent.py 唯一写点）：
- ai_message    AI 发来的消息（role=user，AI 视角的"用户"）
- human_message 人类提交的回复（role=assistant，子代理视角）
- system        系统提示（如启动/结束标记，过滤不列为消息）

JSONL 位置：<temp>/smartagent_subagent/<sid>.jsonl（tempfile.gettempdir()）
"""
from __future__ import annotations

import json
import os
from typing import List, Tuple

from ..entities import Message, MessageItem, Usage
from ..infra.logging import get_logger

_log = get_logger("messages_jsonl")


def _parse_events(events: List[dict]) -> Tuple[List[Message], dict]:
    """从事件列表构建消息列表与会话元数据。

    Returns:
        (messages, meta)
    """
    meta: dict = {"title": "", "role": "", "status": ""}
    messages: List[Message] = []
    for idx, ev in enumerate(events):
        etype = ev.get("type", "")
        text = str(ev.get("text", ""))
        ts = int(ev.get("ts", 0) or 0)
        if etype == "ai_message":
            messages.append(Message(
                id="msg-%d" % idx,
                role="user",               # AI 发来的消息（AI 视角的"用户"）
                ts=ts,
                ts_iso="",
                items=[MessageItem(type="text", text=text)],
            ))
        elif etype == "human_message":
            messages.append(Message(
                id="msg-%d" % idx,
                role="assistant",          # 人类回复（子代理视角）
                ts=ts,
                ts_iso="",
                items=[MessageItem(type="text", text=text)],
            ))
        elif etype == "system":
            meta.setdefault("status", str(ev.get("text", "")))
    # 元数据：首条 ai_message 文本作 title
    if not meta["title"]:
        for m in messages:
            if m.role == "user" and m.items and m.items[0].text:
                meta["title"] = m.items[0].text[:60]
                break
    return messages, meta


def parse_jsonl(data: str) -> Tuple[List[Message], dict]:
    """从 jsonl 文本解析消息列表与会话元数据。"""
    events: List[dict] = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            _log.debug("skip unparseable jsonl line: %s", e)
    return _parse_events(events)


def load_jsonl(path: str) -> Tuple[List[Message], dict]:
    """从文件路径加载消息历史与会话元数据（插件统一加载接口）。

    Returns:
        (messages, meta)：与 devin/opencode/claude 约定一致（loader_meta_first=True）
    """
    _log.debug("loading messages from %s", path)
    if not os.path.isfile(path):
        return [], {}
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonl(f.read())


def load_jsonl_with_meta(path: str) -> Tuple[dict, List[Message]]:
    """从文件路径加载会话元数据与消息历史（loader_meta_first=True 约定）。"""
    messages, meta = load_jsonl(path)
    return meta, messages