# WorkBuddy (CodeBuddy Code) 解析器调研报告（workbuddyparser）

## 0. 核心结论

**WorkBuddy（CodeBuddy Code CLI，腾讯 fork 的 Claude Code）在本地存储完整结构化对话历史（JSONL 追加日志 + SQLite 索引）**，解析器采用**混合方案**：
- **JSONL 消息历史**（主源）：`~/.workbuddy/projects/<cwd-encoded>/<sessionId>.jsonl`
  — 完整消息 + 工具调用 + 思考 + 系统事件
- **SQLite 索引**（辅助）：`~/.workbuddy/workbuddy.db` — 会话元数据（title/mode/model/status）
- **运行会话索引**（会话定位）：`~/.workbuddy/sessions/<pid>.json` — pid → sessionId 映射
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/model/上下文百分比/权限模式/思考开关）

**启动命令**：`cbc`（npm 包 `@tencent-ai/codebuddy-code`，bin 别名 `cbc`、`codebuddy`、`codebuddy-code`）
- 当前版本：2.137.1
- 交互模式：`cbc`（默认 TUI）
- 非交互模式：`cbc -p --print`
- Web UI 模式：`cbc --serve --open`（浏览器打开 http://127.0.0.1:<port>）

本地存储位置：
- `~/.workbuddy/projects/<cwd-encoded>/<sessionId>.jsonl` — 消息历史（追加式，34 文件，3103 事件）
- `~/.workbuddy/sessions/<pid>.json` — 运行中会话元数据（pid → sessionId、cwd、status、kind、lastHeartbeat）
- `~/.workbuddy/workbuddy.db` — SQLite 索引（sessions / workspaces / automations / session_usage 等表）
- `~/.workbuddy/tasks/<sessionId>/<N>.json` — 任务定义
- `~/.workbuddy/workspace/sessions/<sessionId>/` — 沙箱工作区（fs / modify_backup / snapfile）
- `~/.workbuddy/local_storage/` — KV 存储（entry_*.info + wb_entry_*.info）
- `~/.workbuddy/traces/<pid>/` — 会话追踪（OpenTelemetry trace）
- `~/.workbuddy/settings.json` — 设置（sandbox policy / claw channel）
- `~/.workbuddy/app/` — Electron 桌面应用数据（sessions.json / window-state 等）
- `~/.workbuddy/logs/` — 日志
- `~/.workbuddy/shell-snapshots/` — Shell 快照
- `~/.workbuddy/memory/` — 记忆（按用户 UUID）
- `~/.workbuddy/plans/` — 计划（当前为空）
- `~/.workbuddy/skills/` — 技能定义
- `~/.workbuddy/plugins/` — 插件市场
- `~/.workbuddy/connectors/` — 连接器
- `~/.workbuddy/file-history/` — 文件备份历史
- `~/.workbuddy/artifact-index/` — 产物索引
- `~/.workbuddy/audit-log/` — 审计日志

cwd 编码规则：`C:\Users\alice\Desktop\PTY-Agent` → `c-Users-alice-Desktop-PTY-Agent`（与 Claude Code 相同）

## 1. 会话元数据（`~/.workbuddy/sessions/<pid>.json`）

### 1.1 interactive 会话（标准）

```json
{
  "pid": 12164,
  "lastHeartbeat": 1787421648380,
  "sessionId": "interactive-12164",
  "cwd": "C:\\Users\\alice\\AppData\\Local\\Temp\\workbuddy-host-cli\\__workbuddy_cli_host__-1-28ac3ac1",
  "startedAt": 1787421648367,
  "kind": "interactive",
  "url": "http://127.0.0.1:62061",
  "endpoint": "http://127.0.0.1:62061",
  "mode": "local",
  "version": "2.115.0",
  "os": "win32",
  "arch": "x64",
  "hostname": "DESKTOP-XXXXXXX",
  "updatedAt": 1787421648419
}
```

### 1.2 prewarm 会话（预启动进程池，需过滤跳过）

```json
{
  "pid": 10592,
  "lastHeartbeat": 1786811411278,
  "sessionId": "prewarm-wb-pool-1786811405518-05df90",
  "cwd": "C:\\Program Files\\WorkBuddy",
  "startedAt": 1786811411245,
  "kind": "prewarm",
  "meta": {
    "prewarmId": "wb-pool-1786811405518-05df90",
    "socketPath": "\\\\.\\pipe\\codebuddy-prewarm-wb-pool-1786811405518-05df90",
    "status": "idle"
  }
}
```

### 1.3 两种 sessionId 形态

| 形态 | 示例 | 说明 |
|------|------|------|
| `interactive-<pid>` | `interactive-12164` | 旧版/主机模式，cwd 在 Temp 目录 |
| UUID | `e6e83172-1f39-4da4-aa97-f88a5edbb27a` | 新版，cwd 为真实项目目录，有对应 jsonl 文件 |

**定位策略**：
1. 列出 `sessions/` 下全部 `<pid>.json`，过滤 `kind=prewarm`
2. 按 `lastHeartbeat` 降序，最近的会话排前面
3. 对 `sessionId` 为 UUID 的：在 `projects/<cwd-encoded>/` 下以 `<sessionId>.jsonl` 查找
4. 对 `interactive-<pid>` 的：检查 `cwd` 下的 Temp 目录（可能已清理）
5. 也可遍历 `projects/` 下全部 jsonl 文件作为备选

## 2. workbuddy.db SQLite 索引

`workbuddy.db` 提供会话元数据索引，9 张表：

### sessions 表（25 行）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 会话 UUID（与 jsonl 文件名一致，如 `bb9466e2-...`） |
| `cwd` | TEXT | 工作目录 |
| `user_id` | TEXT | 用户 UUID |
| `title` | TEXT | AI 生成的会话标题 |
| `custom_title` | TEXT | 用户自定义标题 |
| `status` | TEXT | `completed` / `archived` |
| `created_at` / `updated_at` / `deleted_at` | INTEGER | 时间戳（毫秒） |
| `is_playground` | INTEGER | 是否为 playground 模式 |
| `source_mode` | TEXT | `coding` |
| `mode` | TEXT | `craft`（模式） |
| `model` | TEXT | 模型 ID（如 `kimi-k3-1`, `hy3`, `deepseek-v4-flash`） |
| `permission_mode` | TEXT | `fullAccess` / `acceptEdits` / `bypassPermissions` / `default` / `plan` / `dontAsk` / `auto` |
| `last_activity_at` | INTEGER | 最后活动时间戳 |
| `use_sandbox_cli` | INTEGER | 是否使用沙箱 CLI |

### session_usage 表（22 行）

| 字段 | 说明 |
|------|------|
| `session_id` | 会话 UUID |
| `used` | 已使用 token |
| `size` | 上限大小 |
| `updated_at` | 更新时间 |

### 其他表

- `workspaces` — 工作区路径与最后打开时间
- `automations` — 自动化任务定义（循环定时任务）
- `automation_runs` / `automation_runtime_state` — 自动化运行状态
- `migration_meta` — 迁移元数据
- `__workbuddy_drizzle_migrations` — Drizzle 迁移记录

## 3. JSONL 消息历史结构（`projects/<cwd>/<sessionId>.jsonl`）

### 3.1 文件命名

- 常规会话：`<sessionId UUID>.jsonl`（如 `bb9466e2-1697-4b14-8999-5896d8a73bf9.jsonl`）
  - sessionId 与文件名一致（不含扩展名）
- 子代理会话：`agent-<hash>.jsonl`（如 `agent-c995cc41.jsonl`）
  - sessionId 在内容中，为 UUID

### 3.2 事件类型全集（34 文件 / 3103 事件总览）

| type | 数量 | 说明 |
|------|------|------|
| `function_call` | 916 | 工具调用 |
| `function_call_result` | 911 | 工具结果返回 |
| `reasoning` | 609 | 思考过程 |
| `message` | 494 | 用户/助手消息 |
| `file-history-snapshot` | 146 | 文件快照备份（忽略，非消息） |
| `ai-title` | 24 | AI 生成的会话标题 |
| `resend-fork-notice` | 3 | 分叉/重发通知 |

### 3.3 事件字段详解

#### message（494 事件）

```json
{
  "id": "898af8ca35714bce984896c5d9c6def2",
  "parentId": "77838f9a-1296-4b75-be60-6d8a2e302610",
  "timestamp": 1786940217522,
  "type": "message",
  "role": "assistant",                    // "user" | "assistant"
  "status": "completed",                  // "completed" | "incomplete" | <none>
  "content": [
    {
      "providerData": {"annotations": []},
      "type": "output_text",              // user: "input_text" | "image_blob_ref"
      "text": "Hi alice 👋..."
    }
  ],
  "providerData": {
    "messageId": "9c5fd85396334c6b9240d35c7795c3d1",
    "model": "hy3",                       // 模型 ID
    "requestModelId": "hy3",
    "requestModelName": "Hy3",
    "traceId": "95a3275f9055fd08c2983d8e837bc9a3",
    "conversationRequestId": "106eed2fd251492da66105f87bccbbf0",
    "agent": "cli",                        // "cli" | sub-agent name
    "rawUsage": {                          // token 用量（有时在 message 字段的 dict 中）
      "prompt_tokens": 34607,
      "completion_tokens": 1115,
      "total_tokens": 35722,
      "completion_tokens_details": { ... },
      "prompt_tokens_details": { ... }
    }
  },
  "sessionId": "e6e83172-1f39-4da4-aa97-f88a5edbb27a",
  "cwd": "c:\\Users\\alice\\Desktop\\test"
}
```

**注意**：
- `message` 字段（非 `message` 类型，而是 `message` 键）有时为 dict，包含 `content` 和 `rawUsage`
- `logicalParentId` 出现在摘要/重发后的 user 消息中，指向原始会话消息
- user 消息的 `content[].text` 可能包含 `<system-reminder>` 系统提示 + 用户真实输入
- user 消息的 `content[].type` 可能为 `image_blob_ref`（图片输入）

#### reasoning（609 事件）

```json
{
  "id": "454722b4-e1c2-4cad-af1c-fdd0232445f5",
  "parentId": "c146f199-8dd6-4696-bd3c-86476e50a5f4",
  "timestamp": 1784730215733,
  "type": "reasoning",
  "providerData": {
    "messageId": "9c5fd85396334c6b9240d35c7795c3d1",
    "model": "hy3",
    "requestModelId": "hy3",
    "requestModelName": "Hy3",
    ...
    "agent": "cli"
  },
  "content": [],                    // 通常为空数组
  "rawContent": [                   // 实际的思考内容
    {"type": "reasoning_text", "text": "The user just said..."}
  ],
  "status": "<none>",               // "<none>" | "incomplete"
  "sessionId": "bb9466e2-...",
  "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent"
}
```

#### function_call（916 事件）

```json
{
  "id": "cf3c36a9c29842e285f7577e9ba45325",
  "parentId": "75f48f41-be7a-4029-b0eb-d4611986c85c",
  "timestamp": 1784729491351,
  "type": "function_call",
  "name": "Bash",              // 工具名
  "callId": "chatcmpl-tool-a2bff92b4d88efb0",
  "arguments": "{\"command\": \"ls -la\", \"description\": \"List files\"}",
  "providerData": {
    "extra_fields": null,
    "reasoning": "The user wants me to look at...",
    "messageId": "cad3d3e21f184fbb9bcb6c803e7a90e7"
  },
  "status": "<none>",
  "sessionId": "...",
  "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent"
}
```

**工具全集（实测）**：

| 工具名 | 说明 | arguments 形状 |
|--------|------|----------------|
| `Bash` | 执行 shell 命令 | `{"command": str, "description": str}` |
| `Read` | 读取文件 | `{"file_path": str, "limit": int?}` |
| `Edit` | 编辑文件 | `{"file_path": str, "old_string": str, "new_string": str}` |
| `Write` | 写入文件 | `{"file_path": str, "content": str}` |
| `Glob` | 文件匹配 | `{"pattern": str, "path": str?}` |
| `Grep` | 搜索内容 | `{"pattern": str, "include": str?, "path": str?}` |
| `TaskCreate` | 创建任务 | `{"subject": str, "description": str}` |
| `TaskUpdate` | 更新任务 | `{"id": str, "status": str}` |
| `TaskOutput` | 任务输出 | `{"id": str}` |
| `TaskStop` | 停止任务 | `{"id": str}` |
| `Agent` | 启动子代理 | `{"profile": str, "task": str}` |
| `AskUserQuestion` | 向用户提问 | `{"question": str, "options": []?}` |
| `PowerShell` | 执行 PowerShell | `{"command": str}` |
| `DeferExecuteTool` | 延迟执行 | — |
| `ToolSearch` | 搜索工具 | — |
| `present_files` | 展示文件 | — |

#### function_call_result（911 事件）

```json
{
  "id": "3cf6a411-6fc4-486c-9c11-4676a886eb26",
  "parentId": "cf3c36a9c29842e285f7577e9ba45325",
  "timestamp": 1784729491351,
  "type": "function_call_result",
  "name": "Bash",
  "callId": "chatcmpl-tool-a2bff92b4d88efb0",
  "status": "completed",     // "completed" | "incomplete"
  "output": {
    "type": "text",
    "text": "Command: ls -la\nStdout: ...\nStderr: ...\nExit Code: 0\nSignal: (none)"
  },
  "sessionId": "...",
  "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent"
}
```

#### file-history-snapshot（146 事件）

```json
{
  "id": "c1812214-9b1c-4602-a3ea-cf325263aa3d",
  "timestamp": 1784730209898,
  "type": "file-history-snapshot",
  "isSnapshotUpdate": false,
  "snapshot": {
    "messageId": "c146f199-8dd6-4696-bd3c-86476e50a5f4",
    "trackedFileBackups": {}
  },
  "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent"
}
```

#### ai-title（24 事件）

```json
{
  "timestamp": 1784730211264,
  "type": "ai-title",
  "aiTitle": "修复网页终端调整大小后显示错乱",
  "sessionId": "bb9466e2-...",
  "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent"
}
```

### 3.4 轮次结构（parentId 链接）

```
message(user, parentId: null)           ← 用户输入，无 parentId 为回合开始
  ├── file-history-snapshot (不在链中)
  ├── ai-title (不在链中)
  └── reasoning (parentId: user.id)
      ├── message(assistant, parentId: reasoning.id)
      └── function_call (parentId: reasoning.id)
          └── function_call_result (parentId: function_call.id)
              ├── function_call_result (parentId: function_call.id)  ← 并行工具结果
              └── reasoning (parentId: last_result.id)
                  └── message(assistant, parentId: reasoning.id)  ← 下一轮思考
```

**关键规则**：
- 一条 user 消息开始一个新回合
- `file-history-snapshot` 和 `ai-title` 不在 parentId 链中，是独立事件
- 同一个 `reasoning` 的 parentId 后可跟多个 `function_call`（并行工具调用）
- 同一个 `function_call` 的 id 后可跟多个 `function_call_result`（多个并行结果）
- 无显式回合结束标记（如 Claude Code 的 `turn_duration` 或 Codex 的 `task_complete`）

### 3.5 子代理（agent-*.jsonl）

`agent-<hash>.jsonl` 文件记录子代理会话（`Agent` 工具产生）：
- 每个文件只有一个 user 消息（Agent 任务提示）
- 其余为 assistant 消息 + function_call + function_call_result + reasoning
- sessionId 在内容中，不匹配文件名
- 角色：1 user + N assistant
- 工具：Bash, TaskCreate, TaskUpdate, Write, Read, Grep, Edit 等

## 4. 屏幕快照格式（混合方案补充实时状态用）

### 4.1 WorkBuddy CodeBuddy Code TUI 布局（v2.137.1，实测）

```
╭─── CodeBuddy Code v2.137.1 ───────────────────────────────────────╮
│                                   │ Tips for getting started       │
│                                   │ Press / to use commands, @...  │
│          ████        ████         │ Run /init to create a...      │
│          ████████████████         │ ────────────────────────────── │
│        ████            ████       │ Recent activity                │
│        ████  ██    ██  ████       │ hihi                           │
│        ████  ██    ██  ████       │ run ls in current dir          │
│        ████            ████       │ ────────────────────────────── │
│          ████████████████         │ http://127.0.0.1:58422         │
│                                   │ Hy3 · internal Usage Billing   │
│                                   │ c:\Users\alice\Desktop\...     │
╰───────────────────────────────────────────────────────────────────╯

√ Hook SessionStart
  ⚠️ agent-browser 目前不支持 Windows 系统

────────────────────────────────────────────────────────────────────────
> hi
  hi

 The user just said "hi" twice. Let me respond briefly

● Hi alice. What can I help you with today?

> run ls in current dir

● Bash(ls -la)
  ⎿ total 89
    :\Program Files\Git\bin\bash.exedrwxr-xr-x ...
    ...+6 line (ctrl+o to expand)

● Current directory contents:
  - SKILL.md (31 KB)
  - app.py (300 B, executable)
  - bin/ — directory
  ...

────────────────────────────────────────────────────────────────────────
> Suggest a task to accomplish                                  ↵ send
────────────────────────────────────────────────────────────────────────
? for shortcuts  ← 1 agent
```

### 4.2 实时状态字段（JSONL 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区底部 | `✶ Waking… (N s · preparing/streaming · ↑ N tokens · esc to interrupt)` 或 `∴ Thinking...` = 工作中；`● Bash(...)` 后跟 `⎿` 输出 = 工具执行中；`Do you want to proceed?` 权限框 = 等待批准；无以上 = 空闲 |
| 输入框文字 | `>` 后文本 | `>` 后的文本，排除 placeholder `Suggest a task to accomplish ↵ send` |
| 模型名 | 欢迎页 header | `Hy3 · internal Usage Billing` 中 `Hy3` 部分；JSONL 的 `providerData.model` 为主源 |
| 工作目录 | 欢迎页 header | `c:\Users\alice\Desktop\pty-agent` 行 |
| 界面类型 | 欢迎页框 | 有 `╭─── CodeBuddy Code v2.137.1 ───╮` header = main；无 = conversation |
| 上下文百分比 | 状态栏 | 当前未显示上下文百分比（可能与 Claude Code 不同） |
| 思考开关 | 状态栏右侧 | `Thinking on (AltT to toggle)` 显示思考模式状态 |
| 权限模式 | 状态栏 | `⏵⏵ bypass permissions on (meta+m to cycle)` / `? for shortcuts`（默认）；bypassPermissions 启动时显示 |
| Web UI 端口 | 欢迎页 | `http://127.0.0.1:58422` 行 |

### 4.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `> <text>` | 用户输入回显（`>` 前缀） |
| `  <text>`（缩进 2 空格） | 用户输入续行 / 多行回显 |
| ` The user said...`（前导空格） | 思考文本（thinking） |
| `● <text>` | AI 回复正文 |
| `● Bash(cmd)` | 工具执行 |
| `  ⎿ <text>` | 工具输出（缩进） |
| `  ...+N line (ctrl+o to expand)` | 工具输出截断提示 |
| `✶ Waking… (N s · preparing · ↑ N tokens · esc to interrupt)` | 思考/工作状态 |
| `✶ Waking… (esc to interrupt)` | 工作中（简化版） |
| `✶ Waking… (N s · streaming · ↓ N tokens · esc to interrupt)` | 流式输出中 |
| `────────────────────────────────────────` | 分隔线 |
| `> Suggest a task to accomplish ↵ send` | 输入框 placeholder（空闲） |
| `? for shortcuts  ← 1 agent` | 状态栏（空闲/对话中） |
| `? for shortcuts  ← 1 agent  Thinking on (AltT to toggle)` | 状态栏（思考开启） |
| `  ← 1 agent` | 输入待提交（无 ? for shortcuts） |
| `Do you want to proceed?` + `> 1. Yes` + `2. Yes, and don't ask again...` + `3. No...` | 权限请求框（等待批准） |

### 4.4 斜杠命令（通过 skills 提供）

技能自带斜杠命令（如 `/commit`, `/review-pr`, `/pdf`, `/xlsx`, `/pptx`, `/docx`），无固定菜单列表。

### 4.5 首次运行信任对话框

首次在目录中运行 CodeBuddy Code 时显示信任对话框，解析器需处理：
```
╭──────────────────────────────────────────────────────────────────╮
│ Do you trust the files in this folder?                           │
│                                                                  │
│ c:\Users\alice\Desktop\pty-agent                                │
│                                                                  │
│   > 1. Trust folder only (pty-agent)                             │
│     2. Trust parent folder (Desktop/**)                          │
│     3. Trust folder and all subdirectories (pty-agent/**)        │
│     4. No, exit (escape)                                         │
│                                                                  │
│ Enter to confirm • Esc to exit                                   │
╰──────────────────────────────────────────────────────────────────╯
```

## 5. 已收集样本

### 5.1 JSONL 样本（来自存量数据）

| 会话 | 文件 | 事件数 | 说明 |
|------|------|--------|------|
| PTY-Agent 修复终端 Bug | `bb9466e2-...jsonl` | 173 | 完整交互，含 Glob/Read 工具、thinking |
| PTY-Agent 探索 | `9a472cb2-...jsonl` | ~100 | 含文件操作 |
| WSL 子系统查看 | `e6e83172-...jsonl` | 845 | 3.8 MB，含多轮对话，Bash/Read 工具 |
| 多子代理 | `agent-c995cc41.jsonl` | 407 | Bash 97 次调用，长会话 |
| 子代理 Read/Edit | `agent-a4fe643f.jsonl` | 206 | Read 34 次，Edit 14 次 |

### 5.2 屏幕快照样本（需 PTY-Agent 捕获）

| 场景 | 状态 | 已捕获 |
|------|------|--------|
| 欢迎页（首次信任对话框） | main | ✓ |
| 欢迎页（已信任，含 Recent activity） | main | ✓ |
| 对话中（空闲，含 placeholder） | conversation | ✓ |
| 输入待提交 | conversation | ✓ |
| 思考/工作状态 | working | ✓ |
| 流式输出中 | streaming | ✓ |
| 权限请求框 | awaiting_approval | ✓ |
| 窄屏 40x10 | conversation | ✓ |

## 6. 与 claudeparser / clineparser / codexparser / devinparser 的差异

| 维度 | claudeparser (Claude Code) | clineparser (Cline) | codexparser (Codex CLI) | devinparser (Devin CLI) | **workbuddyparser (WorkBuddy)** |
|------|------|------|------|------|------|
| 存储 | `<sessionId>.jsonl`（追加式 JSONL） | `<id>.json` + `.messages.json`（双 JSON） | `rollout-<ts>-<uuid>.jsonl`（追加式 JSONL） | `<session-name>.json`（ATIF-v1.7） | **`<sessionId>.jsonl`（追加式 JSONL，与 Claude Code 几乎相同）** |
| 会话 ID | UUID | 数字+随机串 | UUID | 形容词-名词 | **UUID / `interactive-<pid>`** |
| 会话定位 | `sessions/<pid>.json` 索引或列出 projects | session_id 直接目录 | 按日期目录 `YYYY/MM/DD/` 搜索 | `transcripts/<id>.json` 直接定位 | **`sessions/<pid>.json` 索引 + projects 遍历** |
| 时间戳 | ISO 字符串 | 毫秒 int | ISO 字符串 | ISO 8601 含时区 | **毫秒 int（Unix epoch ms）** |
| 用户输入 | 直接字符串或 content 数组 | `<user_input mode>` 标签 | `input_text` 类型 | `message` 字段 | **`input_text` 类型（与 Codex 类似）** |
| tool_use | `tool_use` content 项 | `tool_use` content 项 | `function_call` 独立事件 | `tool_calls` 数组 | **`function_call` 独立事件（与 Codex 类似）** |
| tool_result | 字符串 content + 顶层 toolUseResult | list/str 双形态 | `function_call_output` 独立事件（含 exit_code/duration） | `observation.results` 数组 | **`function_call_result` 独立事件（output 为 dict 含 Command/Stdout/Exit Code）** |
| 思考 | `thinking` content 项 | `thinking` content 项 | `reasoning` 独立事件（流式分片） | `reasoning_content` 字段 | **`reasoning` 独立事件（rawContent 数组）** |
| 消息聚合 | 每事件一条消息 | 每事件一条消息 | 按回合聚合 | 按 step 聚合 | **按 parentId 链聚合（类似 Claude Code）** |
| 多工具并行 | 单 tool_call | 单 tool_call | 单 function_call | 多个 tool_calls / results | **同一个 reasoning 下可跟多个 function_call（并行）** |
| 会话元数据 | mode/permission_mode/model/usage | model/workspace_root/team/usage | model_provider/approval_policy/sandbox/context_window | model/agent_mode/backend_type/title | **model/mode(permission_mode)/title/status/model + SQLite 辅助** |
| Token 用量 | `usage` 字段 | `metrics` 字段 | `token_count` 事件（目前 info=null） | `metrics` 字段 | **`providerData.rawUsage` 字段（message 消息中）** |
| 会话标题 | 无（需从首条消息推断） | 无 | title（SQLite） | title（首条消息） | **`ai-title` 事件 + `workbuddy.db sessions.title`** |
| 子代理 | 无 | 无 | 无 | `run_subagent` 工具 | **`agent-*.jsonl` 文件 + `Agent` 工具** |
| 屏幕状态 | 输入框 `>`、effort、权限模式 | 状态栏多行：模型/等级/进度条/Plan-Act | 输入框 `›`、`N% context left` | `❭` 输入框、`Context: N/N` | **输入框 `>`、`✶ Waking…` 状态行、权限对话框，无`context%`状态栏** |
| 系统消息 | 过滤 | 过滤 | 过滤 | 保留需过滤 | **过滤（`<system-reminder>` 开头）** |

## 7. 解析器返回结构建议

```json
{
  "session": {
    "id": "bb9466e2-1697-4b14-8999-5896d8a73bf9",
    "cwd": "c:\\Users\\alice\\Desktop\\PTY-Agent",
    "started_at": 1784896400355,
    "status": "completed",
    "model": "kimi-k3-1",
    "model_provider": null,
    "cli_version": "2.137.1",
    "mode": "craft",
    "permission_mode": "fullAccess",
    "source_mode": "coding",
    "title": "修复网页终端调整大小后显示错乱",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "total_tokens": 0
    }
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval",
    "input_text": "",
    "screen_type": "main|conversation",
    "model_display": "Hy3",
    "cwd_display": "c:\\Users\\alice\\Desktop\\pty-agent"
  },
  "messages": [
    {
      "id": "bb9466e2-...-u1",
      "role": "user",
      "ts": 1784896400355,
      "ts_iso": null,
      "items": [
        { "type": "text", "text": "帮我看一下..." }
      ]
    },
    {
      "id": "bb9466e2-...-a1",
      "role": "assistant",
      "ts": 1784896421540,
      "model": "kimi-k3-1",
      "usage": {
        "prompt_tokens": 34607,
        "completion_tokens": 1115,
        "total_tokens": 35722
      },
      "items": [
        { "type": "thinking", "text": "Let me understand the bug..." },
        { "type": "text", "text": "我先了解一下项目里..." },
        { "type": "tool_use", "tool_call_id": "chatcmpl-tool-...", "name": "Glob",
          "input": { "pattern": "**/*.py", "path": "src" } },
        { "type": "tool_result", "tool_call_id": "chatcmpl-tool-...", "name": "Glob",
          "success": true, "is_denied": false, "is_error": false, "error": null,
          "output": "src/main.py\nsrc/utils.py", "exit_code": 0, "duration_seconds": null }
      ]
    }
  ]
}
```

## 8. 已知注意事项

- **消息 ID**：JSONL 中事件用 `id`（UUID），但无显式消息序号。解析器生成序号 ID（`<sessionId>-uN` / `-aN`）。
- **user 消息过滤**：系统注入的 `system-reminder` 开头的 user 消息应过滤，不列入消息列表。
- **model 来源**：`providerData.model` 为模型 ID（如 `hy3`），`requestModelName` 为显示名（如 `Hy3`）。
- **Token 用量**：`rawUsage` 在 `message` 字段的 dict 中，也在 `message` 事件的 `providerData.rawUsage` 中（位置不统一）。
- **会话定位**：`sessions/<pid>.json` 中的 `sessionId` 可能为 `interactive-<pid>`（不匹配任何 jsonl 文件），也可能为 UUID（匹配 jsonl 文件名）。遍历 `projects/` 下全部 jsonl 文件作为备选方案。
- **prewarm 会话**：`kind=prewarm` 的会话是预启动进程池，不应计入会话列表。
- **运行中会话**：`lastHeartbeat` 可判断会话是否活跃，也可通过 `tasklist` 检查进程是否存活。
- **子代理**：`agent-*.jsonl` 文件应在消息列表中标记为 `agent` 来源或独立分组。
- **屏幕快照**：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` 抓取，注意 CodeBuddy TUI 是增量刷新，`--full` 可能导致 pyte 渲染重叠。
- **多尺寸**：窄屏（40x10/60x15）欢迎页框可能滚出屏幕，`screen_type` 用 `╭─── CodeBuddy Code` 判定 main；宽屏（200x50）placeholder 的 `↵ send` 位于行末，需排除。
- **增量渲染尺寸检测**：CodeBuddy TUI 增量刷新，CUP 序列不全屏覆盖，`_detect_size` 默认 120 列对宽屏不足。`parse_screen_snapshot` 支持显式 `columns/rows`；自动检测时以最长连续 `─` 分隔线行推断真实列数（分隔线恒横跨全宽）。多尺寸离线测试（`sz_40_10.txt`~`sz_200_50.txt`）用 PTY-Agent `read --default terminal-size WxH --keep-ansi -o` 抓取真实快照验证 5 种尺寸。
- **placeholder 输入**：当输入框为空且按 Enter 时，placeholder 文本（如 `Suggest a task to accomplish`）会被当作实际消息提交，解析器需排除 placeholder。