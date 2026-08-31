# devinparser

Devin CLI 会话解析器：从 `%APPDATA%\\devin\\cli` 本地存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（如 `blend-pencil`）自动搜索 `%APPDATA%\\devin\\cli\\transcripts\\`，或 `--list` 列出全部会话
- **消息解析**：user / assistant / thinking / tool_use / tool_result 全部内容类型
- **会话元数据**：model / cli_version / agent_mode / backend_type / title / token 用量
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 界面类型

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
```

## 输出结构

```json
{
  "session": { "id", "started_at", "status", "model", "model_provider",
               "cli_version", "source", "cwd", "backend_type", "agent_mode",
               "title", "usage": { "total_prompt_tokens", "total_completion_tokens",
                                    "total_cached_tokens", "total_steps" } },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model",
      "metrics": { "prompt_tokens", "completion_tokens", "cached_tokens" },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                   "is_denied", "is_error", "error",
                                                   "output" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent",
                  "screen_type", "model_display", "cwd_display" }
}
```

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | transcript / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

- fixture 离线测试（屏幕快照样本 + 真实 transcript 副本）恒执行
- 依赖真实会话数据（`blend-pencil` 等），会话不存在时相关测试自动跳过

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：ATIF-v1.7 格式、SQLite 索引、TUI 布局、与 clineparser/claudeparser/codexparser 的差异。