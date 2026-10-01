"""protocol 包 — 客户端与守护进程之间的线协议与共享内存信箱

message/request 定义报文编解码，shm/shm_utils 提供跨进程信箱，
daemon_utils 负责守护进程信息区与启动/心跳判定。
"""
