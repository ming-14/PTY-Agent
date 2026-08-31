"""file read 用例 —— 读取文件内容并输出（带行号）

- 单文件大小上限 settings.max_read_size；默认读取 settings.default_read_limit 行
- 超长行截断显示（settings.max_line_length）；图片扩展名识别
- 文件不存在时提供同目录相似名建议（包含/被包含匹配，最多 3 条）
- 成功读取后由命令处理层调用 FileRecordStore.record_read 刷新状态机
"""

import difflib
import logging
import os
from typing import List, Optional

from src.files.settings import settings
from src.files.errors import FileToolError

_logger = logging.getLogger("pty-daemon")

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp"})


class ReadResult:
    """读取结果：格式化文本 + 总行数 + 截断标记"""

    __slots__ = ("content", "total_lines", "truncated")

    def __init__(self, content: str, total_lines: int, truncated: bool):
        self.content = content
        self.total_lines = total_lines
        self.truncated = truncated


def is_image_file(path: str) -> bool:
    """按扩展名识别图片文件（不验证内容）"""
    return os.path.splitext(path)[1].lower() in _IMAGE_EXTS


def suggest_similar(path: str, max_suggestions: int = 3) -> List[str]:
    """同目录下相似文件名建议（difflib 相似度匹配，不区分大小写）

    用标准库 difflib.get_close_matches 替代子串包含判定：
    子串判定对形近但非互含的名字（hello_wrld vs hello_world）不触发，
    difflib 基于最长连续匹配块计算相似度，覆盖拼写错误场景。
    """
    directory = os.path.dirname(path)
    base = os.path.basename(path)
    try:
        entries = [n for n in os.listdir(directory) if n not in (".", "..")]
    except OSError:
        return []
    # 不区分大小写：以 lower 形态计算相似度，映射回原名
    lower_map = {n.lower(): n for n in entries}
    close = difflib.get_close_matches(base.lower(), list(lower_map),
                                      n=max_suggestions, cutoff=0.6)
    return [os.path.join(directory, lower_map[c]) for c in close]


def read_file(path: str, offset: int = 0, limit: int = 0) -> ReadResult:
    """读取文件并格式化为带行号文本

    Args:
        path: 绝对路径（命令处理层已解析）
        offset: 起始行（0-based）；负值视为 0
        limit: 读取行数；<=0 使用配置默认行数

    Raises:
        FileToolError: 图片/过大/非 UTF-8 等业务错误
        FileNotFoundError: 文件不存在
        OSError: 其他 IO 错误
    """
    if is_image_file(path):
        raise FileToolError("This is an image file: %s" % path)

    file_info = os.stat(path)  # FileNotFoundError/OSError 直接上抛
    if os.path.isdir(path):
        raise FileToolError("Path is a directory, not a file: %s" % path)
    if file_info.st_size > settings.max_read_size:
        raise FileToolError(
            "File is too large (%d bytes). Maximum size is %d bytes"
            % (file_info.st_size, settings.max_read_size)
        )

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = settings.default_read_limit

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    total_lines = len(lines)
    selected = lines[offset:offset + limit]
    truncated = (offset + limit) < total_lines

    body = _format_lines(selected, offset)
    if truncated:
        body += "\n\n(File has more lines. Use '--offset' to read beyond line %d)" % (offset + len(selected))
    return ReadResult(content=body, total_lines=total_lines, truncated=truncated)


def _format_lines(lines: List[str], start_line: int) -> str:
    """行号前缀 + 超长截断 + \r 清理"""
    formatted = []
    for i, line in enumerate(lines):
        line = line.rstrip("\n").rstrip("\r")
        if len(line) > settings.max_line_length:
            line = line[:settings.max_line_length] + "..."
        num = i + start_line
        if num >= 100000:
            line_num = "%d|" % num
        else:
            line_num = "%6d|" % num
        formatted.append(line_num + line)
    return "\n".join(formatted)
