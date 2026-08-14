"""unified diff 生成

标准库 difflib.unified_diff，header 仿 udiff 格式 `--- a/<path>` / `+++ b/<path>`。
generate_diff 返回 (diff 文本, 新增行数, 删除行数)：
- diff 文本供呈现/日志
- 行数统计基于 SequenceMatcher opcodes（避免 difflib 对无尾部换行行
  输出 "-a+a" 粘连标记导致的误统计）
"""

import difflib
from typing import Tuple


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
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    additions = 0
    removals = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removals += i2 - i1
        if tag in ("replace", "insert"):
            additions += j2 - j1

    text = "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="a/" + path,
        tofile="b/" + path,
    ))
    return text, additions, removals