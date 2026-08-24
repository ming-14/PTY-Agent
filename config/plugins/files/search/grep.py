"""file grep 用例 —— rg 双引擎

引擎1: bin/rg/rg（Windows 为 rg.exe）-H -n --no-heading [--glob include] <pattern> <path>
   用 rg 的真实 regex 语义；literal_text 时对 pattern 做 re.escape。
   rg 退出码 1 = 无匹配，合法空结果；其他非 0 退出（含 rg 缺失）→ 降级。
引擎2（降级）: os.walk + 逐行 regex + SkipHidden 过滤，收集满上限提前停。
两引擎结果统一按文件 modTime 排序（最新优先），上限 settings.max_grep_matches。
"""

import fnmatch
import logging
import os
import re
import subprocess
from typing import List, Optional

from config.plugins.files.settings import settings
from config.plugins.files.search.ignore import is_ignored

_logger = logging.getLogger("pty-daemon")


class GrepMatch:
    """单条匹配：文件绝对路径 + 1-based 行号 + 行内容"""

    __slots__ = ("path", "line_number", "content")

    def __init__(self, path: str, line_number: int, content: str):
        self.path = path
        self.line_number = line_number
        self.content = content


class GrepResult:
    """grep 结果：匹配列表 + 截断标记 + 使用的引擎（日志用）"""

    __slots__ = ("matches", "truncated", "engine")

    def __init__(self, matches: List[GrepMatch], truncated: bool, engine: str):
        self.matches = matches
        self.truncated = truncated
        self.engine = engine


def _mtime_or_min(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _parse_match(line: str) -> Optional[GrepMatch]:
    """解析 rg 输出行 "path:line:content"

    Windows 路径含冒号（C:\\...），故从右侧解析：
    最右冒号截 content，其次截 line，剩余为 path。
    """
    first = line.rfind(":")
    if first < 0:
        return None
    rest = line[:first]
    second = rest.rfind(":")
    if second < 0:
        return None
    lineno = rest[second + 1:]
    if not lineno.isdigit():
        return None
    return GrepMatch(rest[:second], int(lineno), line[first + 1:])


def _sort_by_mtime(matches: List[GrepMatch]) -> None:
    """按文件 modTime 最新优先排序（稳定，同 mtime 保持原顺序）"""
    matches.sort(key=lambda m: _mtime_or_min(m.path), reverse=True)


def _limit_matches(matches: List[GrepMatch]) -> GrepResult:
    truncated = len(matches) > settings.max_grep_matches
    return GrepResult(matches[:settings.max_grep_matches], truncated, "")


def _run_rg_engine(pattern: str, path: str, include: Optional[str],
                   literal_text: bool) -> Optional[List[GrepMatch]]:
    """rg 引擎；rg 缺失或非 0/1 退出返回 None 触发降级"""
    rg_exe = settings.rg_exe
    if rg_exe is None:
        return None
    pattern_arg = re.escape(pattern) if literal_text else pattern
    cmd = [rg_exe, "-H", "-n", "--no-heading"]
    if include:
        cmd += ["--glob", include]
    cmd += [pattern_arg, path]
    try:
        proc = subprocess.run(cmd, capture_output=True,
                              encoding="utf-8", errors="replace")
    except OSError as e:
        _logger.warning("rg 启动失败，降级: %s", e)
        return None
    if proc.returncode not in (0, 1):
        _logger.warning("rg 退出码 %d，降级（stderr=%s）",
                        proc.returncode, proc.stderr.strip()[:200])
        return None
    matches = []
    for line in proc.stdout.splitlines():
        parsed = _parse_match(line)
        if parsed is not None:
            matches.append(parsed)
    return matches


def _run_fallback_engine(pattern: str, path: str, include: Optional[str],
                         literal_text: bool) -> List[GrepMatch]:
    """降级引擎：os.walk + 逐行 regex，SkipHidden 过滤，满上限提前停"""
    regex = re.compile(pattern) if not literal_text else re.compile(re.escape(pattern))
    matches: List[GrepMatch] = []
    stopped = False  # 命中上限后停止遍历
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames
                       if not is_ignored(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_ignored(full):
                continue
            if include and not fnmatch.fnmatchcase(name, include):
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append(GrepMatch(full, lineno, line.rstrip("\r\n")))
                            if len(matches) > settings.max_grep_matches:
                                stopped = True
                                break
            except OSError as e:
                _logger.warning("grep 跳过不可读文件 %s: %s", full, e)
            if stopped:
                break
        if stopped:
            break
    return matches


def grep_files(pattern: str, path: str, include: Optional[str] = None,
               literal_text: bool = False) -> GrepResult:
    """内容搜索：rg 引擎优先，失败/缺失降级纯 Python

    Args:
        pattern: 正则（literal_text=True 时按字面量处理）
        path: 搜索根（绝对路径，命令处理层已解析）
        include: 可选文件名 glob 过滤
        literal_text: 按字面量匹配

    Returns:
        GrepResult：匹配按 modTime 最新优先，上限 settings.max_grep_matches
    """
    engine = _run_rg_engine(pattern, path, include, literal_text)
    if engine is None:
        matches = _run_fallback_engine(pattern, path, include, literal_text)
        _sort_by_mtime(matches)
        result = _limit_matches(matches)
        result.engine = "fallback"
        if result.truncated:
            _logger.info("grep 降级引擎截断: path=%s max=%d", path, settings.max_grep_matches)
        return result
    _sort_by_mtime(engine)
    result = _limit_matches(engine)
    result.engine = "rg"
    return result