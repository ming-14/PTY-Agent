"""file grep 用例 —— rg 双引擎

引擎1: bin/rg/rg（Windows 为 rg.exe）--json [--glob include] <pattern> <path>
   用 rg 的真实 regex 语义；literal_text 时对 pattern 做 re.escape。
   --json 输出为结构化 NDJSON，解析 match 事件（path/line_number/lines.text），
   不受匹配行内容含冒号影响（文本解析 "path:line:content" 无法区分内容冒号）。
   忽略清单（settings.ignored_dirs）以排除 glob（--glob '!**/<dir>/**'）应用，
   与降级引擎一致。rg 退出码 1 = 无匹配，合法空结果；其他非 0 退出 → 降级。
引擎2（降级）: os.walk + 逐行 regex + SkipHidden 过滤，收集满上限提前停。
两引擎结果统一按文件 modTime 排序（最新优先），上限 settings.max_grep_matches。
"""

import fnmatch
import json
import logging
import os
import re
import subprocess
from typing import List, Optional

from src.files.settings import settings
from src.files.search.ignore import is_ignored

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


def _sort_by_mtime(matches: List[GrepMatch]) -> None:
    """按文件 modTime 最新优先排序（稳定，同 mtime 保持原顺序）"""
    matches.sort(key=lambda m: _mtime_or_min(m.path), reverse=True)


def _limit_matches(matches: List[GrepMatch]) -> GrepResult:
    truncated = len(matches) > settings.max_grep_matches
    return GrepResult(matches[:settings.max_grep_matches], truncated, "")


def _parse_json_matches(stdout: str) -> List[GrepMatch]:
    """解析 rg --json 输出为 GrepMatch 列表

    rg --json 输出为 NDJSON，match 事件结构：
        {"type":"match","data":{"path":{"text":"<path>"},
         "lines":{"text":"<整行>"},"line_number":N,...}}
    只取 type=match 事件；路径缺失/行号缺失的行跳过。
    内容用整行（lines.text），与匹配词位置无关，不受内容含冒号影响。
    """
    matches: List[GrepMatch] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            _logger.warning("grep 跳过非法 JSON 行: %r", line[:200])
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data") or {}
        path = (data.get("path") or {}).get("text")
        line_number = data.get("line_number")
        lines_text = (data.get("lines") or {}).get("text", "")
        if not path or line_number is None:
            continue
        matches.append(GrepMatch(path, int(line_number),
                                 lines_text.rstrip("\r\n")))
    return matches


def _run_rg_engine(pattern: str, path: str, include: Optional[str],
                   literal_text: bool) -> Optional[List[GrepMatch]]:
    """rg 引擎；rg 缺失或非 0/1 退出返回 None 触发降级

    用 --json 结构化输出规避文本 "path:line:content" 解析的冒号歧义；
    忽略清单以排除 glob 应用（--glob '!**/<dir>/**'），对齐降级引擎语义。
    """
    rg_exe = settings.rg_exe
    if rg_exe is None:
        return None
    pattern_arg = re.escape(pattern) if literal_text else pattern
    cmd = [rg_exe, "--json"]
    if include:
        cmd += ["--glob", include]
    for ignored in settings.ignored_dirs:
        cmd += ["--glob", "!**/%s/**" % ignored]
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
    return _parse_json_matches(proc.stdout)


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
