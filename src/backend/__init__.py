"""backend 包 — 运行后端

两种独立后端模式，不再混用回退链：

- SubprocessBackend  纯 stdin/stdout/stderr 管道子进程（默认模式）
- TtyBackend         真实终端（Windows ConPTY / Unix openpty）

创建入口见 factory.create_subprocess / factory.create_tty。
"""