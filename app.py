#!/usr/bin/env python
"""PTY-Agent CLI 快捷入口 — 等同于 python -m src。"""

import sys
import os

_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.__main__ import main

if __name__ == "__main__":
    main()
