"""file glob 用例 —— rg 双引擎（design §4.5）

引擎1: bin/rg/rg.exe --files -L --null --glob <pattern>（cwd=搜索根）
   rg 的 --glob 遵循 gitignore 语义（不含 / 的 pattern 匹配任意深度）。
引擎2（降级）: os.walk + 逐段递归 glob 匹配（`**` 支持任意层含 0 层、
    `*` 不跨 /，pattern 无 / 时前置 `**/` 对齐 rg 全深度语义），SkipHidden 过滤。
两引擎统一按 modTime 排序（最新优先），上限 MAX_GLOB_FILES。
"""

import fnmatch
import logging
import os
import subprocess
from typing import List, Optional

from ...config.files import MAX_GLOB_FILES, RG_EXE
from .ignore import is_ignored

_logger = logging.getLogger("pty-daemon")


class GlobResult:
    """glob 结果：文件绝对路径列表 + 截断标记 + 使用的引擎（日志用）"""

    __slots__ = ("files", "truncated", "engine")

    def __init__(self, files: List[str], truncated: bool, engine: str):
        self.files = files
        self.truncated = truncated
        self.engine = engine


def _mtime_or_min(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _match_glob(pattern: str, rel_path: str) -> bool:
    """逐段递归 glob 匹配（相对路径统一 / 分隔）

    - `**` 匹配任意层段（含 0 层）
    - `*`/`?` 等按 fnmatch 段内语义，不跨 /（标准 glob 行为）
    """
    pat_segs = [s for s in pattern.split("/") if s]
    path_segs = [s for s in rel_path.split("/") if s]

    def rec(pi: int, si: int) -> bool:
        if pi == len(pat_segs):
            return si == len(path_segs)
        seg = pat_segs[pi]
        if seg == "**":
            return any(rec(pi + 1, s) for s in range(si, len(path_segs) + 1))
        if si >= len(path_segs):
            return False
        if fnmatch.fnmatchcase(path_segs[si], seg):
            return rec(pi + 1, si + 1)
        return False

    return rec(0, 0)


def _run_rg_engine(pattern: str, path: str) -> Optional[List[str]]:
    """rg --files 引擎；rg 缺失或非 0/1 退出返回 None 触发降级"""
    if RG_EXE is None:
        return None
    cmd = [RG_EXE, "--files", "-L", "--null", "--glob", pattern]
    try:
        proc = subprocess.run(cmd, cwd=path, capture_output=True,
                              encoding="utf-8", errors="replace")
    except OSError as e:
        _logger.warning("rg 启动失败，降级: %s", e)
        return None
    if proc.returncode not in (0, 1):
        _logger.warning("rg 退出码 %d，降级（stderr=%s）",
                        proc.returncode, proc.stderr.strip()[:200])
        return None
    rels = [p for p in proc.stdout.split("\x00") if p]
    return [os.path.normpath(os.path.join(path, rel)) for rel in rels]


def _run_fallback_engine(pattern: str, path: str) -> List[str]:
    """降级引擎：os.walk + 逐段递归 glob 匹配"""

    norm = pattern.replace("\\", "/")
    if "/" not in norm:
        # 对齐 rg gitignore 语义：不含 / 的 pattern 匹配任意深度
        norm = "**/" + norm

    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames
                       if not is_ignored(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if is_ignored(full):
                continue
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            if _match_glob(norm, rel):
                files.append(full)
    return files


def glob_files(pattern: str, path: str) -> GlobResult:
    """文件名匹配：rg 引擎优先，失败/缺失降级纯 Python

    Args:
        pattern: 路径 glob（如 "*.py"、"src/**/*.go"）
        path: 搜索根（绝对路径，CLI 侧已解析）

    Returns:
        GlobResult：文件按 modTime 最新优先，上限 MAX_GLOB_FILES
    """
    engine = _run_rg_engine(pattern, path)
    if engine is None:
        files = _run_fallback_engine(pattern, path)
        truncated = len(files) > MAX_GLOB_FILES
        files.sort(key=_mtime_or_min, reverse=True)
        result = GlobResult(files[:MAX_GLOB_FILES], truncated, "fallback")
        if truncated:
            _logger.info("glob 降级引擎截断: path=%s max=%d", path, MAX_GLOB_FILES)
        return result
    truncated = len(engine) > MAX_GLOB_FILES
    engine.sort(key=_mtime_or_min, reverse=True)
    return GlobResult(engine[:MAX_GLOB_FILES], truncated, "rg")