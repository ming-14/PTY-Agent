# Kimi Code 解析器调研报告（kimicodeparser）

## 0. 核心结论

**Kimi Code CLI（月之暗面）在本地存储完整结构化会话数据（事件源 wire.jsonl + state.json 状态文件）**，解析器方案：

- **消息历史**（主源）：`~/.kimi-code/sessions/<workspaceId>/session_<UUID>/agents/main/wire.jsonl`
  — 事件源追加日志，含完整消息 + 工具调用 + 思考 + 回合元数据
- **会话元数据**：`state.json`（同目录）— title / cwd / createdAt / archived / lastTurnReason
- **会话索引**（定位）：`~/.kimi-code/session_index.jsonl` — sessionId → sessionDir + workDir
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态 / 输入框 / model / 上下文百分比 / 工作目录 / 版本号）

**启动命令**：`kimi`（`<kimi-install-dir>\kimi.exe`）
- 当前版本：0.38.0
- 交互模式：`kimi`（默认 TUI）
- 非交互模式：`kimi -p "prompt"` / `kimi --output-format stream-json`
- Web UI 模式：`kimi web`
- 会话恢复：`kimi -S <id>` / `kimi -c`（继续当前目录上次会话）
- 权限模式：`-y/--yolo`（自动批准常规工具）、`--auto`（完全自主）、`--plan`（计划模式）

本地存储位置（**默认目录 `~/.kimi-code/`**，可用环境变量 `KIMI_CODE_HOME` 覆盖）：
- `~/.kimi-code/config.toml` — 运行时配置（provider / model）
- `~/.kimi-code/tui.toml` — TUI 外观配置
- `~/.kimi-code/device_id` — 设备 UUID
- `~/.kimi-code/session_index.jsonl` — 会话索引
- `~/.kimi-code/workspaces.json` — 工作区列表
- `~/.kimi-code/sessions/<workspaceId>/session_<UUID>/state.json` — 会话状态
- `~/.kimi-code/sessions/<workspaceId>/session_<UUID>/agents/main/wire.jsonl` — 消息历史（事件源）
- `~/.kimi-code/sessions/<workspaceId>/session_<UUID>/logs/kimi-code.log` — 会话日志
- `~/.kimi-code/user-history/*.jsonl` — 用户斜杠命令历史
- `~/.kimi-code/logs/kimi-code.log` — 全局日志
- `~/.kimi-code/bin/` — 内置工具（fd.exe / kimi.exe）
- `~/.kimi-code/updates/` — 更新信息
- `~/.kimi-code/cache/` — 查询缓存（query-store）

workspaceId 编码：`wd_<用户名>_<hash>`（如 `wd_user_7c9f4e12a450`），是用户主目录的哈希。

## 1. 会话元数据（state.json）

### 1.1 新版格式（v2，毫秒时间戳，kimi 0.38.0）

```json
{
  "id": "session_00000000-0000-0000-0000-000000000001",
  "version": 2,
  "cwd": "C:/Users/<username>",
  "createdAt": 1787456046793,
  "updatedAt": 1787456124770,
  "archived": false,
  "agents": {
    "main": {
      "homedir": "C:/Users/<username>/__kimi/sessions/.../agents/main",
      "type": "main"
    }
  },
  "custom": {},
  "lastPrompt": "看看桌面有什么",
  "title": "hi",
  "titleKind": "replaceable",
  "lastTurnReason": "completed",
  "isCustomTitle": false
}
```

关键字段：
- `id`：`session_<UUID>`（与目录名一致）
- `cwd`：会话工作目录（注意是 `C:/` 正斜杠）
- `createdAt` / `updatedAt`：**毫秒时间戳**（Unix epoch ms）
- `title`：AI 生成的会话标题（`titleKind=replaceable`）或用户自定义（`isCustomTitle=true`）
- `lastTurnReason`：`completed` / `failed`（最后一次回合结果）
- `agents.main`：主代理同目录 `agents/main/`，内含 `wire.jsonl`

### 1.2 旧版格式（ISO 时间戳，kimi 0.23.x）

```json
{
  "createdAt": "2026-07-10T02:18:22.132Z",
  "updatedAt": "2026-07-10T02:18:22.132Z",
  "title": "New Session",
  "isCustomTitle": false,
  "agents": { "main": { "homedir": "...", "type": "main", "parentAgentId": null } },
  "custom": {},
  "workDir": "C:/Users/<username>"
}
```

差异：时间戳为 ISO 字符串、无 `id`/`version`/`cwd`/`lastPrompt`，工作目录字段名为 `workDir`。
旧版会话的 wire.jsonl 只含配置事件（metadata/config.update/tools.set_active_tools/permission.set_mode），无消息内容，解析器对这类会话返回空消息列表。

## 2. wire.jsonl 消息历史（事件源格式）

`agents/main/wire.jsonl` 是**事件源追加日志**，每行一个 JSON 事件，protocol_version 1.5。

### 2.1 事件类型全集（实测 2 个真实会话）

| type | 说明 |
|------|------|
| `metadata` | 协议版本（1.5）、创建时间 |
| `runtime.set_binding` | workspaceId / runtimeId / agentId 绑定 |
| `profile.bind` | systemPrompt / modelAlias / thinkingEffort / activeToolNames / subagents |
| `permission.set_mode` | 权限模式（manual / yolo / auto 等） |
| `prompt.accepted` | 用户提示被接受（promptId） |
| `turn.prompt` | 回合开始：用户输入（input 数组 + origin.kind） |
| `context.append_message` | **权威消息记录**（role / content / toolCalls / id） |
| `plugin.session_start` | 插件会话开始 |
| `llm.tools_snapshot` | 工具快照（activeToolNames） |
| `llm.request` | LLM 请求元数据（provider / model / modelAlias / thinkingEffort / maxTokens / messageCount / turnStep） |
| `usage.record` | token 用量（inputOther / output / inputCacheRead / inputCacheCreation，usageScope=turn） |
| `token_counting.measured` | token 计数快照 |
| `context.append_loop_event` | 循环事件（见 2.2），event.type ∈ step.begin / content.part / tool.call / tool.result / step.end |
| `turn.ended` | 回合结束（turnId / reason: completed\|failed / durationMs） |
| `token_counting.turn_recorded` | 回合 token 记录 |
| `interaction.request` | 交互请求（approval：toolName / action / display） |
| `interaction.resolved` | 交互响应（decision: approved / denied） |
| `permission.record_approval_result` | 权限批准结果记录 |

### 2.2 关键事件结构

#### context.append_message（权威消息）

```json
{
  "type": "context.append_message",
  "agentId": "main",
  "message": {
    "role": "user",
    "content": [{ "type": "text", "text": "看看桌面有什么" }],
    "toolCalls": [],
    "origin": { "kind": "user" },
    "id": "msg_01M0PAY8E5K6554GYTBEX1SBRZ"
  },
  "time": 1787456070095
}
```

- `message.role`：user（实测仅 user 消息以 append_message 落盘）
- `message.content`：content 数组（text 类型）
- `message.id`：`msg_<ULID>` 格式消息 ID
- assistant 内容由 loop_event 的 content.part 记录（见下）

#### context.append_loop_event（循环事件，含思考/文本/工具）

```json
{ "type": "context.append_loop_event", "agentId": "main",
  "event": { "type": "content.part", "uuid": "...", "turnId": "0", "step": 1,
             "stepUuid": "...", "part": { "type": "think", "think": "..." } },
  "time": 1787456060399 }

{ "type": "context.append_loop_event", "agentId": "main",
  "event": { "type": "content.part", "uuid": "...", "turnId": "0", "step": 1,
             "part": { "type": "text", "text": "\n\nHey! What can I help you with?" } },
  "time": 1787456060399 }

{ "type": "context.append_loop_event", "agentId": "main",
  "event": { "type": "tool.call", "uuid": "...", "turnId": "1", "step": 1,
             "stepUuid": "...", "toolCallId": "call_...", "name": "Bash",
             "args": { "command": "ls -la" } },
  "time": 1787456118927 }

{ "type": "context.append_loop_event", "agentId": "main",
  "event": { "type": "tool.result", "parentUuid": "<tool.call uuid>",
             "toolCallId": "call_...",
             "result": { "output": "...", "is_error": false } },
  "time": 1787456119134 }

{ "type": "context.append_loop_event", "agentId": "main",
  "event": { "type": "step.end", "uuid": "...", "turnId": "1", "step": 1,
             "finishReason": "end_turn|tool_use",
             "usage": { "inputOther": ..., "output": ..., "inputCacheRead": ... } },
  "time": 1787456124766 }
```

**关联规则**：
- `content.part` 的 `part.type` ∈ `think`（思考，字段 `think`）/ `text`（回复正文，字段 `text`）/ `tool`（工具）
- `tool.call` 与 `tool.result` 通过 `toolCallId` 关联（tool.result 另有 `parentUuid` 指向 tool.call 的 `uuid`）
- `turnId` 标识回合，`step` 标识回合内步骤

#### interaction.request / resolved（权限请求）

```json
{ "type": "interaction.request", "agentId": "main", "id": "approval_...",
  "kind": "approval", "toolCallId": "call_...",
  "request": { "id": "approval_...", "sessionId": "...", "toolName": "Bash",
               "action": "Running: ls -la", "display": { "kind": "command",
               "command": "ls -la", "cwd": "C:/Users/<username>", "language": "bash" } },
  "time": 1787456088030 }

{ "type": "interaction.resolved", "agentId": "main", "id": "approval_...",
  "response": { "decision": "approved" }, "time": 1787456118924 }
```

### 2.3 回合结构（turn.prompt / turn.ended 边界）

```
turn.prompt (input=[{type:text,text:"hi"}], origin.kind=user)  ← 回合开始
  ├── context.append_message (user, "hi")
  ├── llm.request (model / maxTokens / messageCount)
  ├── usage.record (inputOther/output/inputCacheRead)
  ├── context.append_loop_event: step.begin (turnId=0, step=1)
  │     ├── content.part (think)   ← 思考
  │     └── content.part (text)    ← 回复正文
  ├── context.append_loop_event: step.end (finishReason=end_turn)
  └── turn.ended (turnId=0, reason=completed, durationMs=13366)
```

工具回合：
```
turn.prompt (user, "看看桌面有什么")
  ├── context.append_message (user)
  ├── context.append_loop_event: step.begin (turnId=1, step=1)
  │     ├── content.part (think)
  │     └── content.part (text)
  ├── interaction.request (approval, Bash)
  ├── interaction.resolved (approved)
  ├── permission.record_approval_result (approved)
  ├── context.append_loop_event: tool.call (Bash, args={command})
  ├── context.append_loop_event: tool.result (output)
  ├── context.append_loop_event: step.end (finishReason=tool_use)
  ├── context.append_loop_event: step.begin (turnId=1, step=2)
  │     ├── content.part (think)
  │     └── content.part (text)
  ├── context.append_loop_event: step.end (finishReason=end_turn)
  └── turn.ended (turnId=1, reason=completed, durationMs=54674)
```

### 2.4 Token 用量

- `usage.record`：`usage.inputOther`（输入）/ `usage.output`（输出）/ `usage.inputCacheRead`（缓存命中）/ `usage.inputCacheCreation`（缓存写入），`usageScope=turn`
- `step.end.usage`：同结构（每步）
- `token_counting.measured` / `token_counting.turn_recorded`：`tokens` 总数

## 3. 屏幕快照格式（补充实时状态用）

### 3.1 Kimi Code TUI 布局（v0.38.0，120x40 实测 7 种状态）

#### 状态 1：欢迎页（main，无会话）
```
╭──────────────────────────────────────────────────────────────────────────────────╮
│  ▐█▛█▛█▌  Welcome to Kimi Code!                                                  │
│  ▐█████▌  Send /help for help information.                                        │
│  Directory: C:\Users\<username>\Desktop\kimicodeparser                                │
│  Session:                                                                          │
│  Model:     sensenova-6.8-flash-lite                                               │
│  Version:   0.38.0                                                               │
╰──────────────────────────────────────────────────────────────────────────────────╯

 ✦ Try Kimi Code Web UI - clearer task progress, visual sessions & settings management
   Run /web to continue your session in the browser

   No session yet — one will be created on your first message.

╭──────────────────────────────────────────────────────────────────────────────────╮
│ >                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────╯
 sensenova-6.8-flash-lite thinking  C:\Users\<username>\Desktop\kimicodeparser  tip  context: 0% (0/256k)
```

#### 状态 2：对话中空闲（conversation，🌑 月亮图标）
```
╭──────────────────────────────────────────────────────────────────────────────────╮
│  ▐█▛█▛█▌  Welcome to Kimi Code!                                                  │
│  ▐█████▌  Send /help for help information.                                        │
│  Directory: C:\Users\<username>\Desktop\kimicodeparser                                │
│  Session:   session_<UUID>                                                        │
│  Model:     sensenova-6.8-flash-lite                                               │
│  Version:   0.38.0                                                               │
╰──────────────────────────────────────────────────────────────────────────────────╯

 ✦ Try Kimi Code Web UI ...
 ╭──────────────────────────────────────────────────────────────────────────────────╮
 ✨ 1
  🌑 · Tip: /tasks to check progress and status for background tasks
 ──────────────────────────────────────────────────────────────────────────────────
   ❯ 1+1等于几？
   ↑ to edit · ctrl-s to steer immediately
╭──────────────────────────────────────────────────────────────────────────────────╮
│ >                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────╯
 sensenova-6.8-flash-lite thinking  C:\Users\<username>\Desktop\kimicodeparser  tip  context: 0% (0/256k)
```

#### 状态 3：工作中（working，⠋ Braille spinner）
```
 ╭─────────────────────────────────────────────────────────────────────────────────╮
 ✨ 帮我看看桌面的文件
 ● The user wants to list the files on the desktop...
  ⠋ working... · Tip: /plugins: manage plugins — try the "Kimi Datasource" for ...
 ● 当前目录 C:\Users\<username>\Desktop\kimicodeparser 的内容如下：
   ┌──────┬─────────────────────────┐
   │ 📁   │ .pytest_cache           │
   ...
╭──────────────────────────────────────────────────────────────────────────────────╮
│ >                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────╯
 sensenova-6.8-flash-lite thinking  C:\Users\<username>\Desktop\kimicodeparser  tip  context: 10% (24.5k/256k)
```

#### 状态 4：工具执行完成（tool_running）
```
 ● Ran a command
   $ pwd
   /c/Users/<username>/Desktop/kimicodeparser
   Approved: Running: pwd
```

#### 状态 5：工具被拒绝（rejected）
```
 ✗ Ran a command
   $ ls -la /c/Users/<username>/Desktop
   Tool "Bash" was not run because the user rejected the approval request.
   Rejected: Running: ls -la /c/Users/<username>/Desktop
```

#### 状态 6：批准对话框（awaiting_approval）
```
   ▶ Run this command?

   cwd: C:/Users/<username>/Desktop/kimicodeparser
   $ python --version 2>&1 || echo "no python"

   ▶ 1. Approve once
     2. Approve for this session
     3. Reject
     4. Reject with feedback

   ↑/↓ select · 1/2/3/4 choose · ↵ confirm
```

#### 状态 7：输入确认（input 确认，❯ 前缀）
```
   ❯ 1+1等于几？
   ↑ to edit · ctrl-s to steer immediately
```

### 3.2 实时状态字段

```
╭──────────────────────────────────────────────────────────────────────────────────╮
│                                                                                  │
│  ▐█▛█▛█▌  Welcome to Kimi Code!                                                  │
│  ▐█████▌  Run /login or /provider to get started.                                │
│                                                                                  │
│  Directory: C:\Users\<username>\Desktop\kimicodeparser                                │
│  Session:                                                                        │
│  Model:     not set, run /login or /provider                                     │
│  Version:   0.38.0                                                               │
│                                                                                  │
╰──────────────────────────────────────────────────────────────────────────────────╯

 ✦ Try Kimi Code Web UI - clearer task progress, visual sessions & settings management
   Run /web to continue your session in the browser

   No session yet — one will be created on your first message.
   Error: LLM not set, send "/login" to login

╭──────────────────────────────────────────────────────────────────────────────────╮
│ >                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────╯
 C:\Users\<username>\Desktop\kimicodeparser    /tasks to check progress...  context: 0%
```

### 3.2 实时状态字段

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区 + 状态栏 | 工作中：`⠋ working...` / `⠋ thinking...`（Braille spinner）；工具执行中：`● Running a command` 或 `● Ran a command`；等待批准：`▶ Run this command?` + 选项列表；空闲：`🌑` 月亮图标 或 无以上特征 |
| 输入框文字 | 输入框 `>` 后文本 | `>` 或 `│ >` 后文本，排除 placeholder |
| 模型名 | 欢迎页 header / 状态栏左侧 | 状态栏 `sensenova-6.8-flash-lite thinking` 提取 model 名（`thinking` 前）；欢迎页 `Model: <name>` 行兜底；`not set` 返回空 |
| 工作目录 | 欢迎页 header / 状态栏 | `Directory: <path>` 行；状态栏 `thinking` 后的路径段 |
| 界面类型 | 欢迎页框 | 有 `Welcome to Kimi Code!` + `╭──` 框 = main；有 `⠋`/`✨`/`●`/`✗`/`▶ Run this command?` = conversation |
| 上下文百分比 | 状态栏右侧 | `context: N%` 或 `context: N% (X/Y)` |
| 版本号 | 欢迎页 header | `Version: <ver>` 行 |
| 权限模式 | — | 配置文件 permission.set_mode 事件（manual / yolo / auto / plan）|

## 4. 已收集样本

| 会话 | 文件 | 说明 |
|------|------|------|
| 欢迎页（已配置模型） | `tests/fixtures/screen_welcome.txt` | 120x40，Model: sensenova-6.8-flash-lite，Version: 0.38.0 |
| 对话中空闲 | `tests/fixtures/screen_conversation_idle.txt` | 输入确认状态（`❯` 前缀 + `🌑` 月亮图标） |
| 工作中 | `tests/fixtures/screen_working.txt` | `⠋ working...` + 批准对话框，混合状态 |
| 批准对话框 | `tests/fixtures/screen_input_pending.txt` | `▶ Run this command?` + 1/2/3/4 选项，7 状态栏 |
| 工具被拒绝 | `tests/fixtures/screen_denied.txt` | `Rejected: Running: ls -la` |
| 完整对话历史 | `tests/fixtures/screen_conversation_full.txt` | 多轮对话 + 工具执行 |
| 多尺寸欢迎页 | `tests/fixtures/sz_40_10.txt` ~ `sz_200_50.txt` | 5 种尺寸（40x10 / 60x15 / 80x24 / 120x40 / 200x50） |
| 真实会话 | `__kimi/.../session_00000000-.../wire.jsonl` | 110KB，含 think/text/tool.call/tool.result/approval |
| 失败回合会话 | `session_00000001-.../wire.jsonl` | 回合 failed（MaxTokens 超限） |
| 旧版空会话 | `~/.kimi-code/.../session_00000002-.../wire.jsonl` | protocol 1.4，仅配置事件 |

## 5. 与 claudeparser / clineparser / codexparser / devinparser / workbuddyparser / opencodeparser 的差异

| 维度 | claudeparser (Claude Code) | codexparser (Codex CLI) | workbuddyparser (WorkBuddy) | **kimicodeparser (Kimi Code)** |
|------|------|------|------|------|
| 存储 | `<id>.jsonl`（事件追加） | `rollout-*.jsonl`（事件追加） | `<id>.jsonl`（事件追加） | **`wire.jsonl`（事件源追加，protocol 1.5）** |
| 会话 ID | UUID | UUID | UUID / interactive-<pid> | **`session_<UUID>`** |
| 会话定位 | 遍历 projects/ | 按日期目录搜索 | sessions/<pid>.json 索引 | **session_index.jsonl 索引 + sessions/ 遍历** |
| 时间戳 | ISO 字符串 | ISO 字符串 | 毫秒 int | **毫秒 int（state.json 新版）/ ISO（旧版）** |
| 用户输入 | user 事件 | input_text 类型 | input_text 类型 | **turn.prompt + context.append_message** |
| tool_use | tool_use content 项 | function_call 事件 | function_call 事件 | **loop_event tool.call** |
| tool_result | tool_result content 项 | function_call_output 事件 | function_call_result 事件 | **loop_event tool.result** |
| 思考 | thinking content 项 | reasoning 事件（分片） | reasoning 事件（rawContent） | **content.part type=think** |
| 消息聚合 | 每事件一条消息 | 按回合聚合 | 按 parentId 链聚合 | **按回合聚合（turnId）+ step 内合并** |
| 权限请求 | 无独立事件 | — | — | **interaction.request / resolved（approval）** |
| 会话元数据 | mode/permission_mode/model | model_provider/approval_policy | title/mode/model + SQLite | **title/cwd/createdAt/lastTurnReason + config.toml** |
| Token 用量 | message.usage | token_count 事件 | rawUsage 字段 | **usage.record（inputOther/output/cache）** |
| 会话标题 | 无（需推断） | title（SQLite） | ai-title 事件 | **state.json title 字段** |
| 子代理 | 无 | 无 | agent-*.jsonl | **agents/ 多目录 + subagents 列表** |

## 6. 解析器返回结构建议

```json
{
  "session": {
    "id": "session_00000000-0000-0000-0000-000000000001",
    "cwd": "C:/Users/<username>",
    "started_at": 1787456046793,
    "status": "completed",
    "model": "sensenova-6.8-flash-lite",
    "model_provider": "openai",
    "cli_version": "0.38.0",
    "title": "hi",
    "is_custom_title": false,
    "archived": false,
    "permission_mode": "manual",
    "usage": { "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_write_input_tokens": 0 }
  },
  "live_state": {
    "ai_status": "idle|working|awaiting_approval",
    "input_text": "",
    "screen_type": "main|conversation",
    "model_display": "",
    "cwd_display": "C:\\Users\\<username>\\Desktop\\kimicodeparser",
    "context_percent": 0,
    "version_display": "0.38.0"
  },
  "messages": [
    {
      "id": "msg_01M0PAY8E5K6554GYTBEX1SBRZ",
      "role": "user",
      "ts": 1787456070095,
      "ts_iso": "...",
      "items": [ { "type": "text", "text": "看看桌面有什么" } ]
    },
    {
      "id": "session_00000000-...-a1",
      "role": "assistant",
      "ts": 1787456087969,
      "model": "sensenova-6.8-flash-lite",
      "usage": { "input_tokens": ..., "output_tokens": ... },
      "items": [
        { "type": "thinking", "text": "The user wants to see..." },
        { "type": "text", "text": "桌面内容概览：..." },
        { "type": "tool_use", "tool_call_id": "call_...", "name": "Bash",
          "input": { "command": "ls -la" } },
        { "type": "tool_result", "tool_call_id": "call_...", "name": "Bash",
          "success": true, "is_denied": false, "is_error": false,
          "error": null, "output": "total 1019196\n..." }
      ]
    }
  ]
}
```

## 7. 已知注意事项

- **默认目录**：`~/.kimi-code/`，解析器默认使用该目录；可通过 `KIMI_CODE_HOME` 环境变量或 CLI 参数覆盖（调试用）
- **时间戳双形态**：state.json 新版毫秒 int、旧版 ISO 字符串；wire.jsonl 恒为毫秒 int（`time` 字段）
- **assistant 消息无独立 append_message**：内容全在 loop_event content.part 中，需按 turnId+step 聚合
- **turnId 为字符串**（"0"/"1"）非数字；step 为数字
- **消息 ID**：user 消息用 `message.id`（msg_xxx）；assistant 消息需生成序号 ID（`<sessionId>-aN`）
- **model 来源**：`llm.request.model`（模型别名 `__kimi_env_model__` 需映射）或 config.toml
- **权限拒绝**：interaction.resolved decision 非 approved 时对应 tool.result 视为 is_denied
- **系统注入**：`prompt.accepted` 事件非用户消息；`origin.kind` 非 user 的 prompt 需过滤
- **屏幕快照**：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` 抓取；欢迎页有蓝色框 header（`╭─╮`），输入框为灰色框（`│ >`），状态栏右侧 `context: N% (X/Y)`
- **批准对话框**：`▶ Run this command?` + `▶ 1. Approve once` 等 4 选项 + `↑/↓ select · 1/2/3/4 choose · ↵ confirm` 提示；在消息区显示，不是浮层
- **输入确认**：发送消息前显示 `❯ <文本>` + `↑ to edit · ctrl-s to steer immediately` 确认框（manual 模式）
- **月亮图标**：`🌑`/`🌘`/`🌗`/`🌖`/`🌕` 表示空闲；`⠋` 等 Braille spinner 表示工作中
- **旧版兼容**：protocol 1.4 会话仅配置事件，返回空消息列表；state.json 旧版字段名不同（workDir vs cwd）
