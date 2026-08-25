"""极简文件日志：把调试信息追加写到脚本目录的 debug.log。

用于在真实终端排查输入/拖拽问题（终端 alt screen 会清掉屏幕输出，
故写到文件而非屏幕）。
"""

import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log")


def log(msg: str) -> None:
    try:
        with open(_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass