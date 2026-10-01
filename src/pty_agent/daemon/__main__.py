"""守护进程入口 — python -m pty_agent.daemon 启动"""

from .lifecycle import main

if __name__ == "__main__":
    main()
