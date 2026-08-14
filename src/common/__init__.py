"""跨侧共享工具层 —— Client 与 Daemon 均依赖的纯 OS 级工具

与 config/protocol/auth/ipc 同属共享层，不含任何业务逻辑：
- process.py: 进程存在性探测（pid_exists）
- shells.py: 系统可用 shell 探测与格式化（平台分支）
"""
