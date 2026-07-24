"""test 配置：添加项目根目录到 sys.path 以便导入 src 包"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_bin_dir = os.path.join(_project_root, "bin")
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)
