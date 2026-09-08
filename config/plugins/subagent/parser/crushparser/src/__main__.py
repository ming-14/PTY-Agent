"""包入口：支持 python -m src 调用。"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())