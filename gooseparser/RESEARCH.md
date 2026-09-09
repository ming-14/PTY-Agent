# Goose 解析器调研报告（gooseparser）

## 0. 核心结论

**Goose（Block/AAIF 开源 AI 代理，Rust 编写）在本地用 SQLite 存储完整结构化会话**，解析器采用**混合方案**：
- **SQLite 数据库**（主源）：`%APPDATA%\Block\goose\data\sessions\sessions.db` — 会话元数据 + 完整消息
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态 / 输入框 / 上下文百分比 / 会话标题 / 工作目录 / 耗时）

本地存储位置：
- `%APPDATA%\Block\goose\data\sessions\sessions.db` — 主数据库（sessions / messages / usage_ledger / schema_version）
- `%APPDATA%\Block\goose\data\sessions\sessions.db-wal` — WAL 日志
- `%APPDATA%\Block\goose\config\config.yaml` — 配置文件
- `%APPDATA%\Block\goose\data\history.txt` — 输入历史
- `%APPDATA%\Block\goose\data\projects.json` — 项目索引
- 环境变量 `GOOSE_PATH_ROOT` 可重定向根目录（未设置时用 etcetera：`Block/goose`）

启动方式（本机）：
```powershell
$Env:OPENAI_API_KEY="..."
$Env:OPENAI_BASE_URL="https://your-api-endpoint.com/v1"
$Env:GOOSE_PROVIDER="openai"
$Env:GOOSE_MODEL="sensenova-6.8-flash-lite"
& "<goose-install-dir>\goose.exe" @args
```

CLI 命令：
- `goose session` — 交互式会话（TUI）
- `goose session list` — 列出会话
- `goose session export --session-id <id> --format json` — 导出会话 JSON
- `goose run -t "..."` — 非交互执行
- `goose tui` — 终端 UI（npm 包 @aaif/goose，需 npx）

## 1. SQLite 数据库结构（sessions.db）

### 1.1 sessions 表（会话元数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 会话 ID（`YYYYMMDD_N` 序号型，如 `20260823_2`） |
| `name` | TEXT | 会话标题（首条消息生成） |
| `description` | TEXT | 描述（通常为空） |
| `user_set_name` | BOOLEAN | 是否用户自定义标题 |
| `session_type` | TEXT | `user` / `scheduled` 等 |
| `working_dir` | TEXT | 工作目录 |
| `created_at` / `updated_at` | TIMESTAMP | 创建/更新（UTC） |
| `extension_data` | TEXT | JSON：启用的扩展 + todo 内容 |
| `total_tokens` / `input_tokens` / `output_tokens` | INTEGER | 会话 token 用量（可能为 NULL） |
| `cache_read_tokens` / `cache_write_tokens` | INTEGER | 缓存 token |
| `accumulated_*` | INTEGER | 累计用量（含压缩前的历史） |
| `accumulated_cost` | REAL | 累计费用 |
| `schedule_id` / `recipe_json` / `user_recipe_values_json` | TEXT | 调度/配方 |
| `provider_name` | TEXT | 提供商（如 `openai`） |
| `model_config_json` | TEXT | 模型配置 JSON（model_name/context_limit 等） |
| `goose_mode` | TEXT | `auto` 等 |
| `archived_at` / `project_id` / `parent_session_id` | TEXT | 归档/项目/父会话 |

### 1.2 messages 表（消息内容）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增 |
| `message_id` | TEXT | 消息 UUID（如 `msg_de9a3ce5-...`） |
| `session_id` | TEXT | 会话 ID（FK → sessions.id） |
| `role` | TEXT | `user` / `assistant` |
| `content_json` | TEXT | **JSON 数组**：`MessageContentBlock[]` |
| `created_timestamp` | INTEGER | Unix 秒 |
| `timestamp` | TIMESTAMP | 默认当前时间 |
| `tokens` | INTEGER | 通常为 NULL |
| `metadata_json` | TEXT | **JSON**：`MessageMetadata`（可见性/推理/用量） |

### 1.3 usage_ledger 表（每次推理用量）

| 字段 | 说明 |
|------|------|
| `session_id` | 会话 ID |
| `created_timestamp` | Unix 秒 |
| `model` | 模型名 |
| `input_tokens` / `output_tokens` / `total_tokens` | token 用量 |
| `cache_read_tokens` / `cache_write_tokens` | 缓存 |
| `cost` / `cost_source` | 费用（provider_reported/estimated） |
| `is_compaction` | 是否压缩产生的记录 |

## 2. MessageContentBlock 类型全集（content_json 元素）

serde 用 `type` 标签 + camelCase：

| JSON `type` | 说明 | 关键字段 |
|------|------|----------|
| `text` | 普通文本 | `text` |
| `image` | 图片 | `data`, `mimeType` |
| `thinking` | 思考过程 | `thinking`, `signature` |
| `redactedThinking` | 已编辑思考 | `data` |
| `toolRequest` | 工具调用 | `id`, `toolCall{status, value{name, arguments}}` |
| `toolResponse` | 工具结果 | `id`, `toolResult{status, value{resultType, content[], structuredContent{stdout,stderr,exit_code}, isError}}` |
| `toolConfirmationRequest` | 工具确认请求 | `id`, `toolName`, `arguments` |
| `actionRequired` | 需用户操作 | `data{actionType, id, toolName...}` |
| `frontendToolRequest` | 前端工具请求 | `id`, `toolCall` |
| `systemNotification` | 系统通知 | `notificationType`, `msg` |
| `error` | 错误 | `kind`, `message` |

### 2.1 toolRequest 结构

```json
{
  "type": "toolRequest",
  "id": "call_a95dd8604464455697e28327",
  "toolCall": {
    "status": "success",
    "value": { "name": "shell", "arguments": { "command": "dir ..." } }
  },
  "_meta": { "goose_extension": "developer" }
}
```

### 2.2 toolResponse 结构

```json
{
  "type": "toolResponse",
  "id": "call_a95dd8604464455697e28327",
  "toolResult": {
    "status": "success",
    "value": {
      "resultType": "complete",
      "content": [{ "type": "text", "text": "..." }],
      "structuredContent": { "stdout": "...", "stderr": "", "exit_code": 0 },
      "isError": false
    }
  }
}
```

错误识别：
- `toolResult.status == "error"` 或 `value.isError == true` → 失败
- content 含 `denied by user` / `rejected by user` → 权限拒绝
- `structuredContent.exit_code != 0` → 命令失败

### 2.3 消息元数据（metadata_json）

```json
{
  "userVisible": true,      // 是否用户可见（UI 显示）
  "agentVisible": true,     // 是否进入 agent 上下文
  "turnContext": true,      // 是否 per-turn 上下文注入
  "inference": { "provider": "openai", "requestedModel": "sensenova-6.8-flash-lite" },
  "usage": { "inputTokens": 5526, "outputTokens": 42, "totalTokens": 5568,
             "cacheReadTokens": 0, "elapsedMs": 6468, "timeToFirstTokenMs": 6197 }
}
```

**关键过滤规则**：
- `userVisible=false` + `turnContext=true` 的消息是 `<turn-context>` 系统注入（含 current-time/working-directory），**不列入用户可见消息**
- `userVisible=false` 的其他消息是 agent-only（如隐藏的系统提示）

## 3. 屏幕快照格式（Goose session TUI，v1.47.0 实测）

### 3.1 界面整体布局

```
    __( O)>  ● new session · openai sensenova-6.8-flash-lite    ← 标题行：logo + 会话标题 + provider/model
   \____)    20260823_3 · C:\Users\<username>\Desktop\pty-agent       ← 会话 ID + 工作目录
     L L     goose is ready                                       ← 状态提示
  ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 0% 0/128k                                 ← 上下文进度条（百分比 + used/limit）
> Enter to send · Ctrl+J newline                                  ← 输入提示（placeholder）
```

对话中：
```
> 请用 Python 写一个贪吃蛇游戏                                ← 用户消息回显（> 前缀）

  ────────────────────────────────────────                     ← 分隔线
  ▸ todo_write todo                                            ← 工具调用行（▸ 前缀 + 工具名）
    content: - [ ] 编写贪吃蛇游戏...                            ← 工具参数（缩进）

✅ 贪吃蛇游戏已保存到 ...                                       ← AI 回复正文
  ⏱ 32.41s                                                     ← 回合耗时
  ━━╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ 9% 12k/128k                             ← 进度条（已用比例）
> Enter to send · Ctrl+J newline                                ← 输入提示
```

### 3.2 实时状态字段解析

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 底部 | `Enter to send · Ctrl+J newline` 存在 = idle；`▸ 工具名` 行 + 无完成 = tool_running；无输入提示 = 工作中 |
| 输入框文字 | `>` 后 | `>` 后的文本；placeholder `Enter to send · Ctrl+J newline` 不算 |
| 上下文百分比 | 进度条 | `N% used/limit` 提取百分比 |
| 模型名 | 标题行 | `openai sensenova-6.8-flash-lite` 中模型部分 |
| 工作目录 | 标题行 | 会话 ID 后 `· C:\...` 路径 |
| 会话标题 | 标题行 | `● new session` 或会话名称 |
| 耗时 | 进度条上方 | `⏱ N.NNs` |
| 界面类型 | 标题行 | 有 `__( O)>` logo = main（新会话）；有对话内容 = conversation |

### 3.3 消息类型视觉特征

| 前缀/特征 | 类型 |
|-----------|------|
| `> <text>` | 用户输入回显 |
| 无前缀文本 | AI 回复正文 |
| `  ▸ <tool_name>` | 工具调用 |
| `    <param>: <value>` | 工具参数（缩进 4 空格） |
| `  ──────` | 分隔线（消息分组） |
| `  ⏱ Ns` | 回合耗时 |
| `  ╌╌╌ N% N/Nk` | 上下文进度条（idle 时 ╌ 空心） |
| `  ━╌╌╌ N% N/Nk` | 上下文进度条（已用 ═ 实心） |
| `> Enter to send · Ctrl+J newline` | 输入框 placeholder（空闲） |
| `✅/❌` | 工具完成状态 |

### 3.4 多尺寸注意事项

- 40x10 / 80x24 / 200x50 均可用 PTY-Agent `--default terminal-size WxH` 抓取
- 窄屏下欢迎页 logo 仍可见（标题行恒在顶部）
- TUI 是增量刷新，`--keep-ansi` 保留完整 VT 序列，pyte 解析

## 4. 已收集样本

| 文件 | 场景 |
|------|------|
| `goose_s1_idle.txt` | 新会话空闲态（logo + placeholder + 进度条） |
| `goose_s1_done.txt` | 对话完成后（工具 + 回复 + 耗时 + 进度条） |
| `goose_s1_thinking.txt` | 思考/工具执行中（`▸ todo_write` 行） |
| `goose_s1_working.txt` | 工具执行中（多个 `▸` 工具行） |
| `goose_sz_40x10.txt` | 窄屏 40x10 |
| `goose_sz_80x24.txt` | 中屏 80x24 |
| `goose_sz_200x50.txt` | 宽屏 200x50 |
| `20260823_2.json` | 真实会话导出（SQLite → JSON，含 thinking/toolRequest/toolResponse） |

屏幕快照获取方式：PTY-Agent 终端模式 `exec <sid> -c 'powershell -File GOOSE.ps1 session'` 启动，
`read <sid> --keep-ansi -o <file>` 抓取。

## 5. 与 clineparser/claudeparser/codexparser/devinparser/workbuddyparser 的差异

| 维度 | 其他 5 个 parser | **gooseparser** |
|------|-----------------|-----------------|
| 存储 | JSONL / JSON 文件 | **SQLite 数据库**（sessions.db + WAL） |
| 会话 ID | UUID / 数字+随机串 | **`YYYYMMDD_N` 序号型** |
| 会话定位 | 文件系统遍历 | **SQL 查询**（按 id/名称搜索） |
| 时间戳 | 毫秒 int / ISO 字符串 | **Unix 秒 int**（`created` 字段） |
| 消息 ID | 生成序号 | **原生 UUID**（message_id） |
| 工具调用 | `tool_use` content 项 | **`toolRequest` block**（toolCall 含 status/value） |
| 工具结果 | `tool_result` content 项 | **`toolResponse` block**（toolResult 含 structuredContent stdout/stderr/exit_code） |
| 思考 | `thinking` content 项 | **`thinking` block**（含 signature 签名） |
| 消息可见性 | 无 | **userVisible/agentVisible/turnContext** 三重控制 |
| 系统消息 | 关键词过滤 | **`turnContext=true` + `userVisible=false`** 过滤 |
| 会话元数据 | 从文件提取 | **SQLite sessions 表**（一次 SELECT 全取） |
| Token 用量 | `usage`/`metrics` 字段 | **sessions 表列 + metadata_json.usage + usage_ledger** |
| 会话导出 | 无 | **`goose session export --format json`** 内建 |
| 屏幕状态 | 各自 TUI 布局 | **logo 标题行 + `>` 输入 + 进度条** |

## 6. 解析器返回结构建议

```json
{
  "session": {
    "id": "20260823_2", "name": "Desktop contents check",
    "cwd": "C:\\Users\\<username>", "started_at": "...", "status": "idle",
    "model": "sensenova-6.8-flash-lite", "provider": "openai",
    "goose_mode": "auto", "session_type": "user",
    "usage": { "input_tokens": 7713, "output_tokens": 963, "total_tokens": 8676,
               "cache_read_input_tokens": 4096, "cache_write_input_tokens": 0 },
    "accumulated_usage": { ... },
    "message_count": 7, "last_message_at": "..."
  },
  "messages": [
    {
      "id": "msg_xxx", "role": "user|assistant",
      "ts": 1787453404000, "ts_iso": "...",
      "user_visible": true,
      "model": "sensenova-6.8-flash-lite",
      "usage": { "inputTokens": 5526, "outputTokens": 42, ... },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "...", "signature": "" },
        { "type": "tool_use", "tool_use": { "tool_call_id": "...", "name": "shell", "input": {...} } },
        { "type": "tool_result", "tool_result": { "tool_call_id": "...", "name": "shell",
          "success": true, "is_denied": false, "is_error": false,
          "output": "...", "exit_code": 0 } }
      ]
    }
  ],
  "live_state": { "ai_status": "idle", "input_text": "", "context_percent": 0.0,
                  "screen_type": "main|conversation", "model_display": "...", "cwd_display": "...",
                  "session_title": "...", "elapsed_seconds": null }
}
```

## 7. 已知注意事项

- **会话 ID 规则**：`YYYYMMDD_N`（N 为该日序号），非 UUID；`--name` 可用名称匹配
- **消息 ID**：原生 `message_id` 列（UUID），content 无内嵌 ID
- **turn-context 注入**：每条用户消息后紧跟一条 `userVisible=false` 的 `<turn-context>` 消息，需过滤
- **空 assistant 消息**：真实数据中存在 content 仅 `\n\n` 的 assistant 消息（推理中间产物），是否保留需取舍
- **运行中会话**：Goose 无 pid 索引文件；运行中会话也在 sessions 表中（updated_at 最新），
  可通过 `goose session list` 或检查进程判断
- **WAL 模式**：数据库为 WAL journal mode，读取需处理 `-wal` 文件（sqlite3 自动合并）
- **SQLite 依赖**：Python 标准库 sqlite3 即可，无需额外依赖
- **extension_data**：含 todo 内容（`todo.v0.content`），可提取为会话备注
- **provider/model**：sessions 表 provider_name + model_config_json.model_name 为主源；
  TUI 标题行显示 `provider model` 供屏幕解析
- **多尺寸**：窄屏 logo 恒在顶部，输入提示恒在底部（两处均可定位）
