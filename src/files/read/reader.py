"""file read 用例 —— 读取文件内容并输出（带行号）

仿 opencode internal/llm/tools/view.go：
- 单文件大小上限 MAX_READ_SIZE；默认读取 DEFAULT_READ_LIMIT 行
- 超长行截断显示（MAX_LINE_LENGTH）；图片扩展名识别
- 文件不存在时提供同目录相似名建议（包含/被包含匹配，最多 3 条）
- 成功读取后由 handler 调用 FileRecordStore.record_read 刷新状态机
"""

import logging
import os
from typing import List, Optional

from ...config.files import MAX_READ_SIZE, DEFAULT_READ_LIMIT, MAX_LINE_LENGTH
from ..errors import FileToolError

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
    """同目录下相似文件名建议（名称互相包含且不区分大小写）"""
    directory = os.path.dirname(path)
    base = os.path.basename(path)
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    suggestions = []
    for name in entries:
        if name in (".", ".."):
            continue
        if base.lower() in name.lower() or name.lower() in base.lower():
            suggestions.append(os.path.join(directory, name))
            if len(suggestions) >= max_suggestions:
                break
    return suggestions


def read_file(path: str, offset: int = 0, limit: int = 0) -> ReadResult:
    """读取文件并格式化为带行号文本

    Args:
        path: 绝对路径（CLI 侧已解析）
        offset: 起始行（0-based）；负值视为 0
        limit: 读取行数；<=0 使用 DEFAULT_READ_LIMIT

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
    if file_info.st_size > MAX_READ_SIZE:
        raise FileToolError(
            "File is too large (%d bytes). Maximum size is %d bytes"
            % (file_info.st_size, MAX_READ_SIZE)
        )

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = DEFAULT_READ_LIMIT

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
    """行号前缀 + 超长截断 + \r 清理（仿 view.go addLineNumbers）"""
    formatted = []
    for i, line in enumerate(lines):
        line = line.rstrip("\n").rstrip("\r")
        if len(line) > MAX_LINE_LENGTH:
            line = line[:MAX_LINE_LENGTH] + "..."
        num = i + start_line
        if num >= 100000:
            line_num = "%d|" % num
        else:
            line_num = "%6d|" % num
        formatted.append(line_num + line)
    return "\n".join(formatted)