# Codex CLI 解析器调研报告（codexparser）

## 0. 核心结论

**Codex CLI（OpenAI codex-cli）在本地存储完整结构化对话历史（JSONL 追加日志）**，解析器采用**混合方案**：
- **Rollout JSONL 文件**（主源）：`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`
  — 完整消息 + 工具调用 + 系统事件
- **SQLite 索引**（辅助）：`~/.codex/state_5.sqlite.threads` — 会话元数据（title/tokens/approval_mode/git）
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/上下文百分比/欢迎页信息）

本地存储位置：
- `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` — 消息历史（追加式）
- `~/.codex/state_5.sqlite` — 会话索引（threads 表）+ 工具/状态
- `~/.codex/thread_history_1.sqlite` — 线程组（thread_turns / thread_items）
- `~/.codex/logs_2.sqlite` — 日志
- `~/.codex/history.jsonl` — 输入历史（session_id/ts/text）
- `~/.codex/config.toml` — 设置（model/provider/base_url/env_key）
- `~/.codex/cap_sid` — 沙箱能力 SID 映射（内部，解析器无需处理）

## 1. Rollout JSONL 结构（`rollout-<ts>-<uuid>.jsonl`）

每行一个 JSON 事件，`type` 字段区分事件类型。实测样本（4 个 rollout / 35~37 行事件 / 完整交互）：

| type | payload.type | 说明 | 关键字段 |
|------|-------------|------|----------|
| `session_meta` | — | 会话元数据 | `id`, `timestamp`, `cwd`, `cli_version`, `instructions`, `source`, `model_provider` |
| `response_item` | `message` (role=user) | 用户消息 | `content[].type=input_text`, `text` |
| `response_item` | `message` (role=assistant) | 助手回复 | `content[].type=output_text`, `text` |
| `response_item` | `reasoning` | 思考过程（流式分片→合并） | `content[].type=reasoning_text`, `text` |
| `response_item` | `function_call` | 工具调用 | `name`, `arguments`(JSON 字符串), `call_id` |
| `response_item` | `function_call_output` | 工具结果 | `call_id`, `output`(字符串，常为 JSON，含 `metadata`) |
| `event_msg` | `user_message` | 用户输入事件 | `message`, `images` |
| `event_msg` | `agent_message` | 助手消息事件 | `message`（与 output_text 内容重复） |
| `event_msg` | `token_count` | Token 用量 | `info`（目前为 null，模型未返回），`rate_limits` |
| `event_msg` | `turn_aborted` | 用户中断回合 | `info` |
| `event_msg` | `task_started` | 回合开始 | `turn_id`, `started_at`, `model_context_window`(上下文窗口大小), `collaboration_mode_kind` |
| `event_msg` | `task_complete` | 回合结束（失败时带错误） | `turn_id`, `last_agent_message`, `error{message,codex_error_info}`, `duration_ms` |
| `event_msg` | `item_completed` | 单条消息完成（带消息 ID） | `thread_id`, `turn_id`, `item{type,id,content}` |
| `turn_context` | — | 回合上下文 | `cwd`, `approval_policy`, `sandbox_policy`, `model`, `summary`, `user_instructions`, `truncation_policy` |
| `world_state` | — | 环境状态快照 | `full`, `state: {agents_md, environments, filesystem, ...}`（部分会话） |

### 1.1 滚动记录目录结构

```
~/.codex/sessions/
  └── YYYY/
      └── MM/
          └── DD/
              ├── rollout-<ts>-<uuid1>.jsonl
              ├── rollout-<ts>-<uuid2>.jsonl
              └── ...
```

### 1.2 session_meta 事件

```json
{
  "timestamp": "2026-08-22T16:26:18.556Z",
  "type": "session_meta",
  "payload": {
    "id": "01a02a4b-...",
    "timestamp": "2026-08-22T16:26:18.519Z",
    "cwd": "C:\\Users\\<username>",
    "originator": "codex_cli_rs",
    "cli_version": "0.80.0",
    "instructions": "## Skills\nA skill is a set of...",
    "source": "cli",
    "model_provider": "sensenova"
  }
}
```

### 1.3 response_item 内部结构

**user message**（两条系统注入后跟真实用户输入）：
```json
{
  "payload": {
    "type": "message",
    "role": "user",
    "content": [{ "type": "input_text", "text": "hi" }]
  }
}
```

**assistant message**（含最终回复文本）：
```json
{
  "payload": {
    "type": "message",
    "role": "assistant",
    "content": [{ "type": "output_text", "text": "\n\nHey! How can I help you today?" }]
  }
}
```

**reasoning**（流式思考）：
```json
{
  "payload": {
    "type": "reasoning",
    "summary": [],
    "content": [
      { "type": "reasoning_text", "text": "The user wants" },
      { "type": "reasoning_text", "text": " me to look at the desktop" }
    ],
    "encrypted_content": null
  }
}
```

**function_call**（工具调用）：
```json
{
  "payload": {
    "type": "function_call",
    "name": "shell",
    "arguments": "{\"command\": [\"powershell.exe\", \"-Command\", \"Get-ChildItem...\"]}",
    "call_id": "call_a99249ac0e974f98b98d3fdc"
  }
}
```

**function_call_output**（工具结果）：
```json
{
  "payload": {
    "type": "function_call_output",
    "call_id": "call_a99249ac0e974f98b98d3fdc",
    "output": "{\"output\":\"\\r\\n\\r\\n    Ŀ¼: C:\\\\Users\\\\<username>\\\\Desktop\\\\r\\n...\", \"metadata\": {\"exit_code\": 0, \"duration_seconds\": 0.4}}"
  }
}
```

output 为 JSON 时含两键：
- `output`：工具实际输出文本（解析器提取为此字段）
- `metadata`：`{exit_code, duration_seconds}` — 工具执行结果与耗时
- 失败/拒绝时 output 可能是非 JSON 文本（如 `exec command rejected by user`）

### 1.4 工具全集（实测，codex-cli 0.80.0 + sensenova provider）

| 工具名 | 说明 |
|--------|------|
| `shell` | 执行 shell 命令（arguments 为 `{"command": [argv...]}` 或字符串） |
| `update_plan` | 维护计划（Plan 模式相关） |
| `view_image` | 查看图片 |
| `list_mcp_resources` / `list_mcp_resource_templates` / `read_mcp_resource` | MCP 资源工具 |

解析器对工具名通用处理（不硬编码工具列表）。

### 1.4 轮次结构（按 turn_context 边界）

一个完整回合的典型事件序列：
```
response_item message(role=user, input_text)  ← 用户输入
event_msg user_message                         ← 用户消息事件
turn_context                                   ← 回合上下文
response_item reasoning (streaming fragments)  ← 思考分片
response_item function_call                    ← 工具调用
response_item reasoning (merge)                ← 合并思考
response_item message(role=assistant, output_text)  ← 助手回复
event_msg token_count                          ← Token 计数
response_item function_call_output             ← 工具结果
turn_context                                   ← 下回合上下文
```

### 1.5 消息合并策略

- 每个响应中首个 `reasoning` 包含多个分片文本（`reasoning_text` 数组），其后可能跟一个合并版 `reasoning`（内容相同，但合为单条）。去重合并：取合并版 `reasoning_text.text` 作为 thinking 内容
- 多条 `reasoning` 连续出现时，取最后一个（合并版）为 thinking
- 系统注入的初始 user 消息（`# AGENTS.md instructions for ...` 和 `<environment_context>...</environment_context>`）应过滤，不列入消息列表

## 2. 会话元数据（SQLite 辅助）

`state_5.sqlite.threads` 表提供额外元数据：

| 字段 | 说明 |
|------|------|
| `id` | 会话 UUID（与 rollout 文件名一致） |
| `rollout_path` | rollout JSONL 完整路径 |
| `title` | 会话标题（首条消息） |
| `model` | 模型名 |
| `model_provider` | 模型提供商 |
| `cwd` | 工作目录 |
| `source` | `cli` / `app` |
| `approval_mode` | 权限模式（`on-request` 等） |
| `sandbox_policy` | 沙箱策略 JSON |
| `tokens_used` | Token 总数（目前为 0） |
| `cli_version` | CLI 版本 |
| `history_mode` | `paginated` / `legacy` |
| `created_at_ms` / `updated_at_ms` | 时间戳 |
| `git_sha` / `git_branch` / `git_origin_url` | Git 关联 |
| `is_pinned` | 是否置顶 |
| `thread_section_id` | 所属分类 |

解析器以 rollout JSONL 为主源，SQLite 为可选补充（未索引时自动降级）。

## 3. 屏幕快照格式（混合方案补充实时状态用）

### 3.1 Codex TUI 布局（v0.80.0，实测）

```
╭─────────────────────────────────────────────────╮
│ ✨ Update available! 0.80.0 -> 0.149.0          │  ← 更新横幅（可有可无）
│ ...                                             │
╰─────────────────────────────────────────────────╯

╭────────────────────────────────────────────────────────╮
│ >_ OpenAI Codex (v0.80.0)                              │  ← 欢迎页大框（首会话显示）
│ model:     sensenova-6.8-flash-lite   /model to change │
│ directory: ~\Desktop\pty-agent                         │
╰────────────────────────────────────────────────────────╯

  Tip: You can resume a previous conversation by running codex resume

⚠ Support for the "chat" wire API is deprecated...


› 看看桌面有什么                                          ← 用户消息回显

• 桌面内容如下：                                          ← AI 回复
  **文件夹（16个）**
  ...                                                    ← 回复正文

─ Worked for 39s ────────────────────────────────────────  ← 回合分隔线

◦ Working (36s • esc to interrupt)                       ← 工作中（思考/工具）
› Implement {feature}                                     ← 输入框（placeholder）
  100% context left · ? for shortcuts                     ← 状态栏
```

### 3.2 实时状态字段（JSONL 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区底部 | `Working` + `esc to interrupt` = 工作中；`Would you like to run` 权限框 = 等待批准；`✗ You canceled` = 被拒绝；`Conversation interrupted` = 中断；无以上 = 空闲 |
| 输入框文字 | `›` 后文本 | `›` 后的文本，排除 placeholder `Implement {feature}` |
| 上下文百分比 | 状态栏 | `N% context left` 提取百分比 |
| 模型名 | 欢迎页 header / turn_context | `model: xxx` 后的模型名；JSONL 的 turn_context.model 为主源 |
| 工作目录 | 欢迎页 header | `directory: xxx` 后的路径；JSONL 的 cwd 为主源 |
| 界面类型 | 欢迎页框 / 对话标记 | 有无 `>_ OpenAI Codex` header 框 = main；否则为 conversation |

### 3.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `› <text>` | 用户输入回显 |
| `• <text>` | AI 回复正文 |
| `• Ran <command>` | 工具执行完成 |
| `• Running <command>` | 工具执行中 |
| `└ <output>` | 工具输出（缩进） |
| `─ Worked for Ns ─...` | 回合完成分隔线 |
| `◦ Working (Ns • esc to interrupt)` | 思考/工具执行中（旋转动画） |
| `✗ You canceled...` | 权限拒绝 |
| `■ Conversation interrupted...` | 对话中断提示 |
| `Would you like to run the following command?` + 选项列表 | 权限请求框（等待批准） |
| `› 1. Yes, proceed (y)` … + `Press enter to confirm or esc to cancel` | 权限选项 |
| `Select Reasoning Level for ...` + 1-4 选项 + `Press enter to confirm or esc to go back` | 推理等级选择框 |
| 模型列表 + `Press enter to select reasoning effort, or esc to dismiss.` | 模型选择框 |
| `› Implement {feature}` | 输入框 placeholder（空闲） |
| `100% context left · ? for shortcuts` | 状态栏（空闲/对话中） |
| `  100% context left`（无 `? for shortcuts`） | 输入待提交/工作中（无快捷键提示） |

通用对话框判定：屏幕含 `Press enter to ...`（confirm/select/dismiss）或
`Would you like to run` 即为模态对话框（等待用户操作），AI 状态判定为 awaiting_approval，
输入框文字为空。

### 3.4 斜杠命令（输入 `/` 弹出菜单）

| 命令 | 说明 |
|------|------|
| `/model` | 选择模型与推理等级 |
| `/approvals` | 审批设置 |
| `/experimental` | 切换 beta 功能 |
| `/skills` | 使用 skills |
| `/review` | 审查当前改动 |
| `/new` | 对话中开新会话 |
| `/resume` | 恢复已保存会话 |
| `/init` | 创建 AGENTS.md |

### 3.5 会话生命周期事件（JSONL）

- `task_started`：回合开始，含 `model_context_window`（上下文窗口大小，如 258400）
- `task_complete`：回合结束；`error` 非空表示回合失败（如 provider 404），会话 status=failed
- `item_completed`：单条消息完成，携带消息 ID（`item.id`）；仅在部分会话出现
  （response_item 事件本身不带 ID，解析器使用生成的序号 ID）

## 4. 已收集样本

| 文件 | 场景 |
|------|------|
| sample_idle.txt | 欢迎页（header + 更新横幅 + placeholder + 状态栏） |
| sample_conversation.txt | 对话中（含完整回复 + 回合分隔线） |
| sample_conversation_idle.txt | 对话空闲态（placeholder + 状态栏） |
| sample_input_pending.txt | 待提交输入状态（输入框有文字，AI 空闲） |
| sample_thinking.txt | 思考/工作状态（`◦ Working` 行） |
| sample_working.txt | 工具执行中（`• Ran` + 工具输出 + `◦ Working`） |
| sample_awaiting_approval.txt | 权限请求状态（`Would you like to run...` 框） |
| sample_denied.txt | 拒绝状态（`✗ You canceled` + `■ Conversation interrupted`） |
| sample_slash.txt | 斜杠命令菜单（`/` 输入 + 命令列表） |
| sample_model_dialog.txt | 模型/推理等级选择对话框（`Select Model and Effort`） |
| sample_rollout.jsonl | 完整交互 rollout 副本（工具调用 + 权限拒绝 + 中断，46 事件） |
| sample_rollout_failed.jsonl | 失败会话 rollout 副本（provider 404，含 task_started/task_complete） |
| sz_40x10.txt ~ sz_200x50.txt | 5 种终端尺寸（40x10 / 60x15 / 80x24 / 120x40 / 200x50，对话中状态） |

屏幕快照获取方式：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>`（快照模式，Codex TUI 是增量刷新，`--full` 会导致 pyte 渲染重叠忽略 scrollback 拼合）。

### 4.1 多尺寸注意事项

- 窄屏（40x10/60x15）提示文本自动换行，但欢迎页 header 可能滚出屏幕
- `screen_type` 用 `>_ OpenAI Codex` 判定为主页（main），无 header 框时判 conversation
- 多尺寸下状态栏统一为 `100% context left · ? for shortcuts`，无额外变化

## 5. 与 clineparser/claudeparser 的差异

| 维度 | clineparser | claudeparser | codexparser |
|------|-------------|--------------|-------------|
| 存储 | `<id>.json` + `<id>.messages.json`（双 JSON） | `<sessionId>.jsonl`（单文件追加式） | `rollout-<ts>-<uuid>.jsonl`（追加式 JSONL） |
| 会话定位 | session_id 直接目录 | 需 sessions/<pid>.json 索引或列出 projects | 按日期目录 `YYYY/MM/DD/rollout-*.jsonl` 搜索 |
| 时间戳 | 毫秒 int | ISO 字符串 | ISO 字符串 |
| 用户输入 | `<user_input mode>` 标签包裹 | 直接字符串或 content 数组 | `input_text` 类型 |
| tool_use | `tool_use` content 项 | `tool_use` content 项 | `function_call` response_item |
| tool_result | content 中 list/str 双形态 | content 中字符串 | `function_call_output` 单独事件（含 exit_code/duration） |
| 思考 | `thinking` content 项 | `thinking` content 项 | `reasoning` 独立 response_item（流式分片） |
| 模式 | Plan/Act | normal/plan | 无独立模式切换（`update_plan` 工具 + approval_policy） |
| 权限 | 状态栏行3 | 状态栏左侧 + permission-mode 事件 | `approval_policy` + 权限请求对话框 |
| 上下文% | 状态栏进度条 ███ | 无（需自定义） | 状态栏 `100% context left` |
| Token 用量 | `metrics` 字段 | `usage` 字段 | `token_count` 事件（目前 info=null，模型未返回） |

## 5.1 已知注意事项

- **消息 ID**：response_item 事件本身不带 ID；`item_completed` 事件携带消息 ID 但仅部分会话
  出现。解析器使用生成的序号 ID（`<session_id>-uN` / `-aN`）
- **失败会话**：provider 返回错误时（如 `/responses` 404），rollout 含 `task_complete.error`，
  解析器将 session.status 置为 `failed` 并保留 `last_error`；`task_started.model_context_window`
  提供上下文窗口大小
- **wire_api 弃用**：当前 config.toml 未设置 wire_api（走 chat API）；CLI 提示 chat API 将弃用，
  更新到 `wire_api = "responses"` 后 rollout 结构可能变化，需重新验证
- **resume 会话**：`codex resume` 继续同一 thread，推测追加写入同一 rollout 文件
  （thread_history 表按字节偏移跟踪读取进度）；未直接实测，解析器按追加式处理
- **token_count.info**：实测始终为 null（sensenova provider 未返回用量）；
  `threads.tokens_used` 也为 0。上下文占用百分比以 TUI 状态栏 `N% context left` 为准

## 6. 解析器返回结构建议

```json
{
  "session": {
    "id": "01a02a54-...", "cwd": "...",
    "started_at": "...", "status": "idle|failed",
    "model": "sensenova-6.8-flash-lite",
    "cli_version": "0.80.0",
    "model_provider": "sensenova",
    "source": "cli",
    "approval_policy": "on-request",
    "sandbox_policy": { "type": "read-only" },
    "context_window": 258400,
    "last_error": "...",
    "title": "hi"
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval",
    "input_text": "",
    "context_percent": 100.0,
    "screen_type": "main|conversation",
    "model_display": "sensenova-6.8-flash-lite",
    "cwd_display": "~\\Desktop\\pty-agent"
  },
  "messages": [
    {
      "id": "...", "role": "user|assistant", "ts": 0, "ts_iso": "...",
      "model": "...",
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_call_id": "...", "name": "shell",
          "input": { "command": ["powershell.exe", "-Command", "..."] } },
        { "type": "tool_result", "tool_call_id": "...", "name": "shell",
          "success": true, "is_denied": false, "error": null,
          "output": "...", "exit_code": 0, "duration_seconds": 0.4 }
      ]
    }
  ]
}
```