"""unified diff 生成

标准库 difflib.unified_diff，header 仿 udiff 格式 `--- a/<path>` / `+++ b/<path>`。
generate_diff 返回 (diff 文本, 新增行数, 删除行数)：
- diff 文本供呈现/日志
- 行数统计基于 SequenceMatcher opcodes（避免 difflib 对无尾部换行行
  输出 "-a+a" 粘连标记导致的误统计）

性能：只建一次 SequenceMatcher，统计与 diff 文本共用同一组 opcodes
（标准库 unified_diff 会内部重建 matcher 二次 O(n²) 计算）。
"""

import difflib
from typing import Tuple

# 每侧上下文行数（对齐 difflib.unified_diff 默认 n=3）
_CONTEXT = 3


def _format_range_unified(start: int, stop: int) -> str:
    """行范围格式化（对齐 difflib._format_range_unified）"""
    beginning = start + 1  # 行号从 1 开始
    length = stop - start
    if length == 1:
        return str(beginning)
    if not length:
        beginning -= 1  # 空范围：起始行号为范围前一行
    return "%d,%d" % (beginning, length)


def _split_groups(opcodes, n: int):
    """把 opcodes 切分为 unified diff 组（上下文 n 行，对齐 difflib 组切分）

    超过 2n 行的相等段作为组边界：前 n 行归入当前组尾部上下文，
    后 n 行作为下一组头部上下文；组内必须含至少一个变更 opcode。
    """
    groups = []
    cur = []
    for op in opcodes:
        tag, i1, i2, j1, j2 = op
        if tag == "equal":
            length = i2 - i1
            if length > 2 * n:
                if cur:
                    cur.append((tag, i1, i1 + n, j1, j1 + n))
                    groups.append(cur)
                    cur = []
                if n > 0:
                    cur = [(tag, i2 - n, i2, j2 - n, j2)]
            else:
                cur.append(op)
        else:
            cur.append(op)
    if cur:
        groups.append(cur)
    return [g for g in groups if any(o[0] != "equal" for o in g)]


def _render_diff_text(before_keep: list, after_keep: list, matcher,
                      path: str) -> str:
    """基于已有 matcher 的 opcodes 渲染 unified diff 文本

    before_keep/after_keep 为 splitlines(keepends=True) 行（输出保留原行尾），
    opcodes 索引与不带 keepends 的行列表一一对应（行数相同）。
    equal 段逐行比对 keepends 内容：无尾部换行行在统计 matcher 中视为
    相等但实际行尾不同，复现 difflib 的 "-a+a" 粘连标记行为。
    """
    out = ["--- a/%s\n" % path, "+++ b/%s\n" % path]
    for group in _split_groups(matcher.get_opcodes(), _CONTEXT):
        first, last = group[0], group[-1]
        out.append(
            "@@ -%s +%s @@\n"
            % (_format_range_unified(first[1], last[2]),
               _format_range_unified(first[3], last[4]))
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                # 行数一一对应；行尾不同（无尾部换行）时按 delete+insert 输出
                for k in range(i2 - i1):
                    if before_keep[i1 + k] != after_keep[j1 + k]:
                        out.append("-" + before_keep[i1 + k])
                        out.append("+" + after_keep[j1 + k])
                    else:
                        out.append(" " + before_keep[i1 + k])
            elif tag in ("replace", "delete"):
                for line in before_keep[i1:i2]:
                    out.append("-" + line)
            if tag in ("replace", "insert"):
                for line in after_keep[j1:j2]:
                    out.append("+" + line)
    return "".join(out)


def generate_diff(before: str, after: str, path: str) -> Tuple[str, int, int]:
    """生成两条内容间的 unified diff 与 +/- 行统计

    Args:
        before: 写前的内容（新文件为 ""）
        after: 写后的内容
        path: 文件路径（用于 header，仿 udiff 的 a/ b/ 惯例）

    Returns:
        (diff 文本, additions, removals)；内容相同返回 ("", 0, 0)
    """
    if before == after:
        return "", 0, 0
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    # 单一 matcher：统计与文本渲染共用 opcodes，避免二次 O(n²)
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    additions = 0
    removals = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removals += i2 - i1
        if tag in ("replace", "insert"):
            additions += j2 - j1

    text = _render_diff_text(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        matcher,
        path,
    )
    return text, additions, removals
