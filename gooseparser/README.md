# gooseparser

Goose 会话解析器：从 SQLite 本地存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（`YYYYMMDD_N`）直接查询 SQLite，或 `--list` 列出全部会话，或 `--name` 按名称模糊搜索
- **消息解析**：user / assistant / thinking / tool_use（toolRequest）/ tool_result（toolResponse）全部内容类型
- **会话元数据**：model / provider / goose_mode / session_type / usage（token 累计）/ accumulated_usage / extension_data
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 工作目录 / 耗时

## 安装

```bash
pip install -r requirements.txt
```

## 用法

```bash
# 解析指定会话（输出到 stdout，UTF-8）
python -m src <session-id>

# 输出到文件
python -m src <session-id> -o result.json

# 附带屏幕快照解析实时状态（PTY-Agent --keep-ansi 输出）
python -m src <session-id> --screen snapshot.txt

# 列出全部会话
python -m src --list

# 按名称搜索
python -m src --name "Desktop"
```

## 输出结构

```json
{
  "session": { "id", "name", "cwd", "started_at", "updated_at", "status", "model", "provider",
               "goose_mode", "session_type", "usage": {...}, "accumulated_usage": {...},
               "extension_data": {...} },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model", "user_visible",
      "usage": { "input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens",
                 "cache_write_input_tokens", "elapsed_ms", "time_to_first_token_ms" },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                   "is_denied", "is_error", "error",
                                                   "output", "exit_code" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent", "screen_type",
                  "model_display", "cwd_display", "session_title", "elapsed_seconds" }
}
```

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | SQLite / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：SQLite 表结构、MessageContentBlock 类型全集、TUI 布局、
与 clineparser/claudeparser/codexparser/devinparser/workbuddyparser 的差异。

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

依赖真实会话数据（`20260823_2`），会话不存在时相关测试自动跳过。