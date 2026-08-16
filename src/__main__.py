r"""命令行交互式程序交互代理

通过伪终端（PTY）与交互式 CLI 程序双向通信。
守护进程以独立子进程运行，首次执行命令时自动启动。

子命令: start | stop | list | exec | send | read | kill | events | closewin | mouse | keygen

命令解析、注册与派发由 src/cli/ 命令子系统负责。
"""

from .cli import main

if __name__ == "__main__":
    main()
