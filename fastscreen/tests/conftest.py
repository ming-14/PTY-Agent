import os
import sys

# fastscreencore 包位于仓库根，加入 sys.path 以便测试导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# CI runner（如 GH Actions Windows）stdout/stderr 可能默认为 cp1252，
# 强制 UTF-8 避免测试输出中的非 ASCII 字符触发 UnicodeEncodeError。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from fastscreencore import CaptureEngine, TargetType, CaptureMethod
