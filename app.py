"""pty-agent 快捷入口

用法:
  app.py             *显示帮助
  app.py ...         执行命令
"""

import sys
import os

_REQUIRED_VERSION = (3, 8)
if sys.version_info < _REQUIRED_VERSION:
    print(
        f"错误: 需要 Python {_REQUIRED_VERSION[0]}.{_REQUIRED_VERSION[1]}+，"
        f"当前版本 {sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}",
        file=sys.stderr,
    )
    sys.exit(1)

_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)


def main():
    """转调原始 CLI，所有行为由 src/__main__ 决定"""
    from src.__main__ import main as _cli_main  # noqa

    # 将 argv[0] 设为 app.py，确保 help 显示正确命令名
    old_argv = sys.argv
    sys.argv = ["app.py"] + sys.argv[1:]
    try:
        _cli_main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    main()
