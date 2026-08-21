"""输出过滤 — 行/列/grep 过滤与 ANSI 剥离

从 handlers/utils.py 拆出的纯输出过滤逻辑，供 handler 与 daemon 流程层共用。
只依赖 output/ 与 protocol/ansi，不依赖 session 与 handlers，方向向下：
handlers/execution → filtering。
"""

import re
from typing import Optional

from ..output import safe_regex_search
from ..protocol.ansi import strip_ansi
from ..protocol.message import Message
from ..protocol.response import Response


def _filter_indexed(lines, lines_param, grep, column) -> list:
    """统一过滤核心：返回 ``[(原序号, 文本)]``（保留行在原输出中的 0-based 序）

    非法参数抛 ValueError（错误语义由各调用方/包装器决定，与旧 _apply_line_filters 一致）。
    保留原序号供 grep 命中行标注行号（对齐 snapshot-diff 的 ``下标:内容`` 标注）。
    """
    idx_lines = list(enumerate(lines))
    if lines_param is not None:
        if isinstance(lines_param, int):
            idx_lines = idx_lines[-lines_param:] if lines_param > 0 else []
        elif isinstance(lines_param, str) and ":" in lines_param:
            parts = lines_param.split(":", 1)
            try:
                start = int(parts[0]) if parts[0] else 1
                end = int(parts[1]) if parts[1] else len(idx_lines)
                start = max(start, 1)
                idx_lines = idx_lines[start - 1 : end]
            except (ValueError, IndexError):
                raise ValueError(f"Invalid line range: {lines_param}")
        else:
            try:
                n = int(lines_param)
                idx_lines = idx_lines[-n:] if n > 0 else []
            except (ValueError, TypeError):
                raise ValueError(f"Invalid lines parameter: {lines_param}")
    if grep:
        try:
            pat = re.compile(grep)
            idx_lines = [
                (i, l) for i, l in idx_lines if safe_regex_search(pat, l)
            ]
        except re.error:
            raise ValueError(f"Invalid regex: {grep}")
    if column is not None:
        col_idx = column - 1
        idx_lines = [
            (i, l[col_idx] if 0 <= col_idx < len(l) else "") for i, l in idx_lines
        ]
    return idx_lines


def _apply_line_filters(lines, lines_param, grep, column):
    """对行列表应用 lines/grep/column 过滤（统一核心算法，非法参数抛 ValueError）

    返回纯文本行列表（不标注行号）；标注由 filter_snapshot_lines 的 grep 路径负责。
    """
    return [l for _i, l in _filter_indexed(lines, lines_param, grep, column)]


def filter_snapshot_lines(
    output: str, lines_param, column_param=None, grep=None
) -> str:
    """快照路径过滤（静默：非法参数返回空串）

    grep 命中行标注行号（``下标:内容``，0-based，与 snapshot-diff 一致），
    便于定位匹配行在输出中的位置；lines/column 不标注。
    """
    if not output:
        return output
    try:
        idx_lines = _filter_indexed(
            output.splitlines(), lines_param, grep, column_param
        )
    except ValueError:
        return ""
    if grep:
        return "\n".join(f"{i}:{l}" for i, l in idx_lines)
    return "\n".join(l for _i, l in idx_lines)


def apply_lines_grep(
    output: str, lines_param, grep, conn, column_param=None
) -> Optional[str]:
    """输出/子进程路径过滤（报错版：非法参数发 error 并返回 None）"""
    if not lines_param and not grep and column_param is None:
        return output

    try:
        return "\n".join(
            _apply_line_filters(output.splitlines(), lines_param, grep, column_param)
        )
    except ValueError as e:
        Message.send(conn, Response.error(str(e)))
        return None


def strip_if_needed(output: str, msg: dict) -> str:
    if not msg.get("keep_ansi"):
        return strip_ansi(output)
    return output