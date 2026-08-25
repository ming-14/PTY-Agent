# files 插件说明

本插件提供文件工具消息（file_read / file_write / file_edit / file_grep / file_glob / file_upload / file_download），经客户端 `file` 命令或直接发送消息调用。

## 使用要点

- **file_read**：读取文件（带行号）；先读后写（read-before-write）
- **file_write**：覆盖写/新建；已存在文件需先 `file_read`，否则拒绝（防止覆盖未读文件）
- **file_edit**：唯一匹配替换（old 在文件中唯一出现）；`--old` 空 = 新建，`--new` 空 = 删除
- **file_grep**：内容搜索（rg 引擎优先，自动降级纯 Python）
- **file_glob**：文件名匹配
- **file_upload / file_download**：二进制传输（多帧协议，支持大文件）

## 路径规则

- 路径相对 `cwd_session` 指定的会话工作目录解析
- 路径过长/非法参数会返回错误

## 文件状态机

写入前自动检查文件是否已被读取；直接编辑未读文件会触发 `file_read` 前置提示。