# kimicodeparser

Kimi Code CLI 会话解析器：从 `~/.kimi-code/` 本地存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（`session_<UUID>`）自动查询 `session_index.jsonl` 或遍历 `sessions/` 目录，或 `--list` 列出全部会话
- **消息解析**：user / assistant / thinking（think）/ tool_use（tool.call）/ tool_result（tool.result）全部内容类型
- **会话元数据**：model / model_provider / cli_version / title / cwd / permission_mode / token 用量
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 模型名 / 版本号 / 工作目录

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

# 指定自定义 ~/.kimi-code 目录（KIMI_CODE_HOME 环境变量优先）
python -m src <session-id> --kimi-dir /path/to/.kimi-code
```

## 输出结构

```json
{
  "session": { "id", "cwd", "started_at", "status", "model", "model_provider",
               "cli_version", "title", "is_custom_title", "archived",
               "permission_mode", "usage": { "input_tokens", "output_tokens",
                                             "cache_read_input_tokens",
                                             "cache_write_input_tokens" } },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model", "usage": { ... },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                   "is_denied", "is_error", "error",
                                                   "output", "output_text", "approved" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "screen_type", "model_display",
                  "cwd_display", "context_percent", "version_display" }
}
```

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | wire.jsonl 解析 / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 存储格式

Kimi Code 将数据存储在 `~/.kimi-code/`（可通过 `KIMI_CODE_HOME` 环境变量覆盖）：

```
~/.kimi-code/
  session_index.jsonl          # 会话索引（sessionId → sessionDir + workDir）
  sessions/<workspaceId>/
    session_<UUID>/
      state.json               # 会话元数据（title / cwd / createdAt / lastTurnReason）
      agents/main/
        wire.jsonl             # 事件源消息历史
```

消息历史存储为事件源格式（每行一个 JSON 事件，protocol_version 1.5），事件类型包括：
- `turn.prompt` / `turn.ended`：回合边界
- `context.append_message`：权威消息记录（user）
- `context.append_loop_event`：循环事件（think / text / tool.call / tool.result）
- `interaction.request` / `interaction.resolved`：权限请求
- `usage.record`：token 用量

详见 [RESEARCH.md](RESEARCH.md)。

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

- fixture 离线测试（样本 wire.jsonl + 屏幕快照）恒执行
- 依赖真实 Kimi Code 会话（`session_00000000-...`），会话不存在时相关测试自动跳过

## 与其他解析器的差异

见 [RESEARCH.md](RESEARCH.md) 第 5 节：与 claudeparser / clineparser / codexparser / devinparser / workbuddyparser / opencodeparser 的完整对比。