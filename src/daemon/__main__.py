"""守护进程入口 — python -m src.daemon 启动"""

from .lifecycle import main

if __name__ == "__main__":
    main()
