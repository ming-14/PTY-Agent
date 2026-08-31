"""输出格式化适配器：将 ParseResult 转为 JSON 字典/字符串。"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict

from ..entities import ParseResult


def _clean_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """递归清理字典：移除 None 值和空容器（保留 0/False）。"""
    cleaned = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict):
            v = _clean_dict(v)
            if not v:
                continue
        elif isinstance(v, list):
            v = [_clean_dict(i) if isinstance(i, dict) else i for i in v]
            if not v:
                continue
        cleaned[k] = v
    return cleaned


def to_dict(result: ParseResult) -> Dict[str, Any]:
    """将 ParseResult 转为可 JSON 序列化的字典。"""
    data = {
        "session": _clean_dict(asdict(result.session)),
        "messages": [_clean_dict(asdict(m)) for m in result.messages],
    }
    if result.live_state is not None:
        data["live_state"] = _clean_dict(asdict(result.live_state))
    return data


def to_json(result: ParseResult, indent: int = 2, ensure_ascii: bool = False) -> str:
    """将 ParseResult 序列化为 JSON 字符串。"""
    return json.dumps(to_dict(result), indent=indent, ensure_ascii=ensure_ascii)