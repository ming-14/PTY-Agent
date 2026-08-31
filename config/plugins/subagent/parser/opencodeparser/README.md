# opencodeparser

opencode（sst/opencode）会话解析器：从 `~/.local/share/opencode/` 本地 SQLite 存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（ses_xxx）自动查询 `opencode.db`，或 `--list` 列出全部会话
- **消息解析**：user / assistant / thinking（reasoning）/ tool_use / tool_result 全部内容类型
- **会话元数据**：model / model_provider / agent / version / cost / title / token 用量
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 模型名 / 版本号

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
```

## 输出结构

```json
{
  "session": { "id", "slug", "cwd", "title", "agent", "model", "model_provider",
               "variant", "version", "cost", "started_at",
               "usage": { "input_tokens", "output_tokens", "reasoning_tokens",
                          "cache_read_input_tokens", "cache_write_input_tokens" } },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model", "provider", "agent", "finish",
      "usage": { ... },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                   "is_denied", "is_error", "error", "output" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent", "context_tokens",
                  "cost_display", "screen_type", "model_display",
                  "cwd_display", "version_display" }
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

- fixture 离线测试（样本 SQLite 数据库 + 屏幕快照样本）恒执行
- 依赖真实 opencode 会话（`ses_fd3a39bd4ffe9Z2gEdw8ijXs3x`），会话不存在时相关测试自动跳过

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：SQLite 表结构、event sourcing 模式、TUI 布局、与 claudeparser/clineparser/codexparser/devinparser/workbuddyparser 的差异。