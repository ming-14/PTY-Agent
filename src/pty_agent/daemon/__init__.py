"""daemon 包 — 后台守护进程

入口为 __main__（`python -m pty_agent.daemon`），转调 lifecycle.main()；
server 提供信箱循环，handler 解析并执行请求。
"""
