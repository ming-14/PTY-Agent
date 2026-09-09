# codexparser

OpenAI Codex CLI 会话解析器：从 `~/.codex` 本地存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（UUID）自动搜索 `~/.codex/sessions/YYYY/MM/DD/`，或 `--list` 列出全部会话
- **消息解析**：user / assistant / thinking（reasoning）/ tool_use（function_call）/ tool_result（function_call_output）
- **会话元数据**：model / model_provider / cli_version / cwd / approval_policy / sandbox_policy / title
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
  "session": { "id", "started_at", "status", "model", "model_provider", "cli_version",
               "source", "cwd", "title", "approval_policy", "sandbox_policy",
               "context_window", "last_error" },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model",
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input", "raw_arguments" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                  "is_denied", "is_error", "error",
                                                  "output", "exit_code", "duration_seconds" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent", "screen_type",
                  "model_display", "cwd_display" }
}
```

消息按回合聚合：每条真实用户输入对应一条 user 消息，其后同一回合内的
reasoning / function_call / function_call_output / output_text 聚合为一条 assistant 消息
（thinking 去重：流式分片与合并版只保留一次）。

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | rollout / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

- fixture 离线测试（屏幕快照样本 + 真实 rollout 副本）恒执行
- 依赖真实会话数据（`01a02a54-...`），会话不存在时相关测试自动跳过

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：rollout JSONL 事件结构、SQLite 索引、TUI 布局、与 clineparser/claudeparser 的差异。
