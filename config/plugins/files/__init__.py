"""files 插件 —— 进程级文件工具插件

接管消息类型：
- file_read / file_write / file_edit / file_grep / file_glob：无状态工具
- file_upload_start / file_download_start：多帧传输（需 I/O 通道）

结构（按工具域分包）：
- 根级：公共模块（errors / paths / state / history / diff / permission）
- read/：file read 用例
- write/：file write / edit 用例
- search/：file grep / glob 用例与忽略过滤
- transfer/：upload / download 传输（判定 / 映射 / daemon 侧帧协议）

声明即契约：needs_io=True（upload/download 多帧协议）。
"""

from config.plugins.files.files_plugin import FilesPlugin

plugin = FilesPlugin
