"""启动窗格的应用服务：argv[0] 为裸程序名时解析为绝对 exe 路径。

Windows 无法直接 spawn .bat，必须解析为可执行文件绝对路径。
"""

import os
import shutil
from typing import List


def resolve_program(argv: List[str], exe_name: str) -> List[str]:
    """argv[0] 为裸程序名时解析为绝对 exe 路径；已含路径分隔符则原样返回"""
    if os.path.sep not in argv[0] and os.path.altsep not in argv[0]:
        found = shutil.which(exe_name) or shutil.which(argv[0])
        if found:
            argv = [found] + argv[1:]
    return argv
