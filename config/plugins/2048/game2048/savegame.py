"""游戏进度存档：.game2048 配置文件的 [game] state 键（base64 编码）

进度 = 棋盘 + 分数，JSON 序列化后 base64 编码写入 `.game2048` 的
[game] state 键（与 best/win_value 并存），启动时读取恢复：

    state = <base64(JSON: {"board": [[...]], "score": N})>

- 每次有效移动后调用 save()；restart / 终局时调用 clear()
- 文件缺失、损坏、结构非法均视为无存档，不影响游戏
"""

from __future__ import annotations

import base64
import configparser
import json
import os

from . import logic

_FILE_NAME = ".game2048"
_SECTION = "game"
_STATE_KEY = "state"
_SIZE = logic.SIZE

# 项目根目录：本文件位于 <根>/game2048/savegame.py，向上两级即项目根。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path() -> str:
    return os.path.join(_ROOT, _FILE_NAME)


def _cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read(_path(), encoding="utf-8")
    return cfg


def encode(board, score: int) -> str:
    """棋盘 + 分数 → base64 字符串（JSON 载荷，紧凑分隔符）"""
    payload = json.dumps(
        {"board": board, "score": int(score)}, separators=(",", ":")
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def decode(text: str):
    """base64 字符串 → (board, score)；非法/结构不符返回 None"""
    try:
        data = json.loads(base64.b64decode(text.encode("ascii")).decode("utf-8"))
        board, score = data["board"], data["score"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None
    valid = (
        isinstance(board, list)
        and len(board) == _SIZE
        and isinstance(score, int)
        and score >= 0
        and all(
            isinstance(row, list)
            and len(row) == _SIZE
            and all(isinstance(v, int) and v >= 0 for v in row)
            for row in board
        )
    )
    return (board, score) if valid else None


def save(board, score: int) -> None:
    """写入存档（写失败静默，不影响游戏）"""
    try:
        cfg = _cfg()
        if not cfg.has_section(_SECTION):
            cfg.add_section(_SECTION)
        cfg.set(_SECTION, _STATE_KEY, encode(board, score))
        with open(_path(), "w", encoding="utf-8") as f:
            cfg.write(f)
    except (OSError, configparser.Error):
        pass


def load():
    """读取存档 (board, score)；无存档/损坏返回 None"""
    try:
        text = _cfg().get(_SECTION, _STATE_KEY)
    except (configparser.Error, OSError):
        return None
    return decode(text)


def clear() -> None:
    """删除存档（restart / 终局时调用）"""
    try:
        cfg = _cfg()
        if cfg.has_option(_SECTION, _STATE_KEY):
            cfg.remove_option(_SECTION, _STATE_KEY)
            with open(_path(), "w", encoding="utf-8") as f:
                cfg.write(f)
    except (OSError, configparser.Error):
        pass
