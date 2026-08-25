"""pytest 根配置：项目根加入 sys.path（替代各测试文件顶部的 path hack）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
