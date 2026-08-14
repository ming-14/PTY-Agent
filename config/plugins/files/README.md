# files — 文件工具插件

进程级插件:守护进程启动时单例加载,接管文件工具消息类型,响应形状与原内置 `file_*` handler 逐字段一致,客户端零改动。

## 接管的消息类型

| 消息类型 | 功能 | 需要 I/O |
|---------|------|---------|
| `file_read` | 读取文件(带行号,大小/行数/图片限制,相似名建议) | 否 |
| `file_write` | 覆盖写/新建(读前检查状态机,落历史版本链) | 否 |
| `file_edit` | 唯一匹配替换/删除/新建(create/replace/delete 三分支) | 否 |
| `file_grep` | 内容搜索(rg 引擎优先,缺失/失败降级纯 Python) | 否 |
| `file_glob` | 文件名匹配(同上双引擎) | 否 |
| `file_upload_start` | 多帧上传(握手 → MANIFEST → PLAN → 逐文件) | 是 |
| `file_download_start` | 多帧下载(握手 → MANIFEST → PLAN → 逐文件) | 是 |

## 结构

```
config/plugins/files/
  __init__.py       # plugin 导出（loader 入口）
  files_plugin.py   # FilesPlugin(声明 + handle_message 分发)
  config.py files.toml  # 插件配置（业务参数自包含）
  errors.py paths.py state.py history.py diff.py permission.py  # 公共模块
  read/             # file read 用例（reader.py + 聚合导出）
  write/            # file write / edit 用例（writer.py + 聚合导出）
  search/           # file grep / glob 用例 + 忽略过滤（grep.py glob_.py ignore.py）
  transfer/         # 传输（judge.py map.py transfer.py + 聚合导出）
```

- 路径基准:按 `cwd_session` 的会话 cwd 解析(不操作该会话)
- 共享状态:进程级单例持有 `FileRecordStore`(read-before-write)、`TransferMap`(SQLite 传输映射)、`FileHistoryStore`(版本链)
- 配置:本插件 `files.toml`(业务参数:读/写/搜索限制、忽略清单、rg 位置);传输协议参数(`TRANSFER_*`)是 daemon-CLI 通信契约,由核心 `src/config/transfer.py` 提供
- 依赖:核心 `src/config`(common/transfer)、`src/protocol`(帧编解码)、`src/transfer`(扫描/错误定义/CLI 驱动)

## 声明

```python
class FilesPlugin(Plugin):
    triggers = []            # 进程级:不参与会话挂载
    message_types = [...]    # 接管的消息类型
    needs_io = True          # upload/download 多帧协议
```

## 测试

- `tests/unit/daemon/test_file_*_handler.py`:插件消息级测试(handle_message 直调)
- `tests/unit/daemon/test_file_upload_download.py`:loopback TCP 全链路(注入 :memory: 依赖)
- `tests/unit/files/`:各用例业务单测
