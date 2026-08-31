"""pywezterm 引擎加载：把 vendored 产物目录注入 sys.path。

与宿主工程共用同一份构建产物（bin/pywezterm）。
优先环境变量 PYWEZTERM_DIR 显式指定；否则向上遍历工作区祖先目录查找。
pane/console 驱动统一经此加载，避免各模块重复注入。
"""

import os
import sys

_loaded = False


def ensure_engine() -> None:
    """注入 vendored pywezterm 路径到 sys.path（幂等，首个驱动调用）"""
    global _loaded
    if _loaded:
        return
    candidates = []
    env_dir = os.environ.get("PYWEZTERM_DIR")
    if env_dir:
        candidates.append(env_dir)
    this_dir = os.path.dirname(os.path.abspath(__file__))
    parent = this_dir
    while True:
        # 检查 <parent>/bin/pywezterm（与 leaf 同级目录的 bin）
        candidate = os.path.join(parent, "bin", "pywezterm")
        if os.path.isdir(candidate):
            candidates.append(candidate)
            break
        # 检查 <parent>/PTY-Agent/bin/pywezterm（旧嵌套布局）
        candidate = os.path.join(parent, "PTY-Agent", "bin", "pywezterm")
        if os.path.isdir(candidate):
            candidates.append(candidate)
            break
        up = os.path.dirname(parent)
        if up == parent:
            break
        parent = up
    # 逆序插入：第一个候选（env_dir）优先级最高（最后插入 = 最前搜到）
    for d in reversed(candidates):
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)
    _loaded = True