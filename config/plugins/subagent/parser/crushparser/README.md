# crushparser

Crush（charmbracelet/crush）会话解析器：从本地 SQLite 存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（UUID）自动遍历 `%CRUSH_GLOBAL_DATA%/projects.json` 注册的全部项目数据库，支持 XXH3-128 hash / hash 前缀匹配；`--list` 列出全部会话
- **消息解析**：user / assistant / tool / system 全部角色，text / thinking / tool_use / tool_result / finish / shell_command 全部内容类型
- **会话元数据**：title / prompt_tokens / completion_tokens / cost / todos / 时间戳（秒）
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 模型 / 费用 / 会话标题

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

# 列出运行中会话
python -m src --list-running

# 指定数据目录（跳过 projects.json 遍历）
python -m src <session-id> --data-dir C:\Users\alice\.crush
```

## 输出结构

```json
{
  "session": { "id", "parent_session_id", "title", "message_count",
               "prompt_tokens", "completion_tokens", "cost",
               "started_at", "updated_at", "summary_message_id",
               "todos", "data_dir" },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model", "provider",
      "finished_at", "is_summary_message",
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input",
                                             "provider_executed", "finished" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name",
                                                   "success", "is_error",
                                                   "is_denied", "error",
                                                   "content", "data",
                                                   "mime_type", "metadata" } },
        { "type": "finish", "finish": { "reason", "time", "message", "details" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent", "context_tokens",
                  "cost_display", "screen_type", "model_display",
                  "cwd_display", "title", "version_display" }
}
```

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | SQLite 消息解析 / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

- fixture 离线测试（屏幕快照样本）恒执行
- 依赖真实 Crush 会话（`716187c5-...`），会话不存在时相关测试自动跳过

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：SQLite 表结构（sessions/messages/files/read_files）、
parts JSON 格式、TUI 布局、projects.json 会话定位、与 clineparser/claudeparser/codexparser/devinparser/workbuddyparser/opencodeparser 的差异。
