"""游戏配置文件读写：历史最高分与胜利条件值。

配置文件 `.game2048`（INI 格式）位于项目根目录，首次启动时由 ensure()
自动创建并写入默认值（胜利条件默认取代码中的 logic.WIN_VALUE）：

    [game]
    best = 0
    win_value = 2048

- best：历史最高分，游戏自动更新。
- win_value：胜利条件，玩家可手动修改；修改后重启游戏生效。

旧版最高分文件 `.game2048_best` 的值会在创建新配置时自动迁移。
跨平台（Windows / Linux）；读写失败时静默忽略，不影响游戏。
"""

from __future__ import annotations

import configparser
import os

from . import logic

_FILE_NAME = ".game2048"
_LEGACY_FILE_NAME = ".game2048_best"  # 旧版最高分文件（迁移用）
_SECTION = "game"
_DEFAULT_BEST = 0

# 项目根目录：本文件位于 <根>/game2048/highscores.py，向上两级即项目根。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _path() -> str:
    return os.path.join(_ROOT, _FILE_NAME)


def ensure() -> None:
    """确保配置文件存在；不存在则创建并写入默认值。

    若存在旧版最高分文件 `.game2048_best`，其数值会迁移为初始 best。
    """
    path = _path()
    if os.path.exists(path):
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("# game2048 配置文件（INI）：best 为历史最高分，"
                    "win_value 为胜利条件值\n")
            f.write("[{}]\n".format(_SECTION))
            f.write("best = {}\n".format(_legacy_best()))
            f.write("win_value = {}\n".format(logic.WIN_VALUE))
    except OSError:
        pass


def best() -> int:
    """读取历史最高分；文件缺失或损坏时返回 0。"""
    return _read_int("best", _DEFAULT_BEST)


def save(value: int) -> None:
    """写入历史最高分（负值按 0 处理）。"""
    _write_int("best", max(0, int(value)))


def win_value() -> int:
    """读取胜利条件值；未配置、非法或小于 2 时返回代码默认 logic.WIN_VALUE。"""
    value = _read_int("win_value", logic.WIN_VALUE)
    return value if value >= 2 else logic.WIN_VALUE


def _read_int(key: str, default: int) -> int:
    try:
        ensure()
        cfg = configparser.ConfigParser()
        cfg.read(_path(), encoding="utf-8")
        return cfg.getint(_SECTION, key)
    except (OSError, configparser.Error, ValueError):
        return default


def _write_int(key: str, value: int) -> None:
    """写入单个键值，保留文件中其他配置（如 win_value）。"""
    try:
        ensure()
        cfg = configparser.ConfigParser()
        path = _path()
        cfg.read(path, encoding="utf-8")
        if not cfg.has_section(_SECTION):
            cfg.add_section(_SECTION)
        cfg.set(_SECTION, key, str(value))
        with open(path, "w", encoding="utf-8") as f:
            cfg.write(f)
    except (OSError, configparser.Error, ValueError):
        pass


def _legacy_package_path() -> str:
    """旧版最高分文件在包目录中的路径（用于一次性迁移）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _LEGACY_FILE_NAME)


def _legacy_best() -> int:
    """读取旧版最高分文件（.game2048_best）的值；不存在或损坏时返回 0。

    旧版文件可能位于包目录（旧代码用 __file__ 定位）或项目根目录，
    两处都尝试，取先找到的有效值。
    """
    for path in (_legacy_package_path(), os.path.join(_ROOT, _LEGACY_FILE_NAME)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return max(0, int(f.read().strip() or 0))
        except (OSError, ValueError):
            continue
    return 0
