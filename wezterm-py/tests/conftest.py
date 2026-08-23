# wezterm-py 测试共用配置：把 vendored 产物目录注入 sys.path，使测试
# import pywezterm 使用同一份构建产物，不依赖 pip 安装，也避免与任何
# site-packages 旧版本混用。
#
# 产物目录解析顺序：
# 1. 环境变量 PYWEZTERM_DIR 显式指定（其他项目用自己的产物时设置）
# 2. 默认 <wezterm-py 上级>/bin/pywezterm（BUILD.ps1 默认部署位置，
#    供 leaf/PTY-Agent 共用）

import os
import sys

_vendor = None
_env_dir = os.environ.get("PYWEZTERM_DIR")
if _env_dir:
    _vendor = _env_dir
else:
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.normpath(os.path.join(_here, "..", ".."))
    _vendor = os.path.join(_root, "bin", "pywezterm")
if _vendor and os.path.isdir(_vendor) and _vendor not in sys.path:
    sys.path.insert(0, _vendor)
