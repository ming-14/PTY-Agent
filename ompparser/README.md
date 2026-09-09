# ompparser

omp（oh-my-pi）会话解析器：从 `%PI_CODING_AGENT_DIR%` 本地 JSONL 存储解析会话状态与完整消息历史。

## 功能

- **会话定位**：按 sessionId（UUID）自动搜索 `<agentDir>/sessions/*/`，或 `--list` 列出全部会话
- **消息解析**：user / assistant / toolResult 全部内容类型（text / thinking / toolCall / tool_result）
- **会话元数据**：model / model_provider / thinking_level / title / usage（token 累计）
- **实时状态**：可选屏幕快照（PTY-Agent `--keep-ansi` 输出）→ AI 状态 / 输入框 / 上下文百分比 / 模型名 / 思考等级

## 启动

必须使用 `OMP.ps1` 启动 omp（设定正确的 `PI_CODING_AGENT_DIR` 环境变量），
不能用其他方式启动。

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
  "session": { "id", "cwd", "started_at", "status", "model", "model_provider",
               "thinking_level", "title", "usage": {...} },
  "messages": [
    { "id", "role", "ts", "ts_iso", "model", "provider", "api", "stop_reason",
      "usage": {...},
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_use": { "tool_call_id", "name", "input" } },
        { "type": "tool_result", "tool_result": { "tool_call_id", "name", "success",
                                                   "is_denied", "is_error", "error", "output" } }
      ] }
  ],
  "live_state": { "ai_status", "input_text", "context_percent", "context_window",
                  "screen_type", "model_display", "thinking_level", "cwd_display" }
}
```

## 架构（洋葱模型）

| 层 | 文件 | 职责 |
|---|---|---|
| 实体层 | `src/entities.py` | 纯数据类 |
| 用例层 | `src/usecases.py` | 编排解析流程 |
| 适配器层 | `src/adapters/` | JSONL / 会话定位 / 屏幕 / 输出 |
| 框架层 | `src/cli.py`, `src/infra/` | CLI、VT 渲染、日志 |

## 测试

```bash
python -m pytest tests/test_e2e.py -v
```

- fixture 离线测试（样本 JSONL + 屏幕快照样本）恒执行
- 依赖真实会话数据（`01a02d8f-...`），会话不存在时相关测试自动跳过

## 存储格式调研

见 [RESEARCH.md](RESEARCH.md)：omp JSONL 事件结构、cwd 编码、TUI 布局、与 piparser 的差异。