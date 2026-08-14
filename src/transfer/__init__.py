"""文件传输包 —— CLI 侧驱动与双端共享定义

核心保留（框架层，客户端不运行插件）：
- common.py:     传输错误/清单条目结构（双端共享）
- scan.py:       目录树扫描（双端共享）
- client_upload.py / client_download.py: CLI 侧多帧传输驱动

daemon 侧业务（judge/map/daemon_upload/daemon_download）位于
config/plugins/files/ 插件，经本包的协议层与 client 侧对接。
"""
