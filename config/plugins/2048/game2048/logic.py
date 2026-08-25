"""2048 游戏核心逻辑（纯逻辑，无任何 I/O，可独立测试）。

规则：
- 4x4 棋盘，格子值为 2 的幂。
- 每次有效移动后，在随机空位生成新块：90% 为 2，10% 为 4。
- 相同数字相邻时可合并，一次移动中每个块最多只参与一次合并。
- 棋盘被填满且无法再合并时，游戏结束；出现 win_value（默认 2048，可配置）时获胜（可继续）。
"""

from __future__ import annotations

import random
from typing import List, Optional, Sequence, Tuple

SIZE = 4
START_TILES = 2
WIN_VALUE = 2048  # 胜利条件默认值（可用 .game2048 配置文件覆盖）
SPAWN_FOUR_PROB = 0.1

UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
DIRECTIONS = (UP, DOWN, LEFT, RIGHT)

PLAYING = "playing"
WON = "won"
LOST = "lost"


class Game:
    """一个 2048 游戏局。

    参数:
        size: 棋盘边长（默认 4）。
        seed: 随机种子，便于测试复现；为 None 时使用系统随机源。
        win_value: 胜利条件值，达到该数字即触发成就（默认 logic.WIN_VALUE）。
    """

    def __init__(self, size: int = SIZE, seed: Optional[int] = None,
                 win_value: int = WIN_VALUE) -> None:
        if size < 2:
            raise ValueError("棋盘边长至少为 2")
        if win_value < 2:
            raise ValueError("胜利条件至少为 2")
        self.size = size
        self.win_value = win_value
        self.score = 0
        self._rng = random.Random(seed)
        self.board: List[List[int]] = [[0] * size for _ in range(size)]
        for _ in range(START_TILES):
            self._spawn_tile()

    @classmethod
    def from_state(cls, board, score: int, win_value: int = WIN_VALUE) -> "Game":
        """从存档恢复游戏局（不生成初始块、不重置分数）

        入参校验由调用方负责（savegame.decode 已校验结构）；
        棋盘按行复制，避免与存档数据共享可变引用。
        """
        game = cls.__new__(cls)
        game.size = len(board)
        game.win_value = win_value
        game.score = score
        game._rng = random.Random()
        game.board = [list(row) for row in board]
        return game

    # ------------------------------------------------------------------ #
    # 移动                                                               #
    # ------------------------------------------------------------------ #
    def move(self, direction: str) -> bool:
        """向 direction 方向移动一格。

        返回:
            bool: 棋盘是否发生变化。没有可移动/可合并的块时返回 False。
        """
        if direction not in DIRECTIONS:
            raise ValueError("未知方向: {!r}，可选 {}".format(direction, DIRECTIONS))

        had_win = self.status() == WON
        lines = self._collect_lines(direction)
        moved = False
        new_lines: List[List[int]] = []
        for line in lines:
            slid, gain, line_moved = _slide(line, self.size)
            moved = moved or line_moved
            self.score += gain
            new_lines.append(slid)

        if moved:
            self._write_lines(direction, new_lines)
            self.just_win = (not had_win) and self.status() == WON
            self._spawn_tile()
        else:
            self.just_win = False
        return moved

    def _collect_lines(self, direction: str) -> List[List[int]]:
        """按移动方向抽取行/列，且统一为“头部优先”朝向。"""
        if direction in (LEFT, RIGHT):
            lines = [list(row) for row in self.board]
        else:  # UP / DOWN
            lines = [[self.board[r][c] for r in range(self.size)]
                     for c in range(self.size)]
        if direction in (RIGHT, DOWN):
            lines = [list(reversed(line)) for line in lines]
        return lines

    def _write_lines(self, direction: str, lines: List[List[int]]) -> None:
        """把滑动后的行/列写回棋盘（与 _collect_lines 互逆）。"""
        if direction in (RIGHT, DOWN):
            lines = [list(reversed(line)) for line in lines]
        if direction in (LEFT, RIGHT):
            for r, line in enumerate(lines):
                self.board[r] = list(line)
        else:
            for c in range(self.size):
                for r in range(self.size):
                    self.board[r][c] = lines[c][r]

    # ------------------------------------------------------------------ #
    # 状态                                                               #
    # ------------------------------------------------------------------ #
    def status(self) -> str:
        """返回当前状态：PLAYING / WON / LOST 之一。"""
        if any(v >= self.win_value for row in self.board for v in row):
            return WON
        if not self._has_empty() and not self._has_merge():
            return LOST
        return PLAYING

    def _has_empty(self) -> bool:
        return any(v == 0 for row in self.board for v in row)

    def _has_merge(self) -> bool:
        for r in range(self.size):
            for c in range(self.size):
                v = self.board[r][c]
                if v == 0:
                    continue
                if c + 1 < self.size and self.board[r][c + 1] == v:
                    return True
                if r + 1 < self.size and self.board[r + 1][c] == v:
                    return True
        return False

    # ------------------------------------------------------------------ #
    # 内部工具                                                           #
    # ------------------------------------------------------------------ #
    def _spawn_tile(self) -> None:
        empty = [(r, c) for r in range(self.size) for c in range(self.size)
                 if self.board[r][c] == 0]
        if not empty:
            return
        r, c = self._rng.choice(empty)
        self.board[r][c] = 4 if self._rng.random() < SPAWN_FOUR_PROB else 2


def _slide(line: Sequence[int], size: int) -> Tuple[List[int], int, bool]:
    """将一行按“头部优先”滑动并合并（经典 2048 单行逻辑）。

    返回:
        (滑动后的行, 本行新增分数, 该行是否发生了变化)
    """
    tiles = [v for v in line if v != 0]
    merged: List[int] = []
    gain = 0
    i = 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            merged.append(tiles[i] * 2)
            gain += tiles[i] * 2
            i += 2
        else:
            merged.append(tiles[i])
            i += 1
    merged += [0] * (size - len(merged))
    return merged, gain, merged != list(line)
