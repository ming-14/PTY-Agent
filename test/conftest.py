"""test 配置：把源码目录（src/）加入 sys.path 以便导入 pty_agent 包"""
import sys
import os

# 将 src/ 添加到 Python 路径（使 import pty_agent.xxx 可用，无需先安装）
_src_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
