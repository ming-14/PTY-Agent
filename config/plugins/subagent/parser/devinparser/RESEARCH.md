# Devin CLI 解析器调研报告（devinparser）

## 0. 核心结论

**Devin CLI（Cognition devin）在本地存储完整结构化对话历史（ATIF JSON 格式）**，解析器采用**混合方案**：
- **Transcript JSON 文件**（主源）：`%APPDATA%\devin\cli\transcripts\<session-name>.json`
  — 完整消息 + 工具调用 + 思考 + 会话元数据
- **SQLite 索引**（辅助）：`%APPDATA%\devin\cli\sessions.db` — 会话元数据 + 消息树
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/上下文百分比，transcript 中缺少的即时状态）

本地存储位置：
- `%APPDATA%\devin\cli\transcripts\<session-name>.json` — 消息历史（ATIF-v1.7 格式）
- `%APPDATA%\devin\cli\sessions.db` — SQLite 会话索引（sessions / message_nodes / tool_call_state / prompt_history）
- `%APPDATA%\devin\cli\session_locks\<name>.lock` — 运行中会话锁（内容为 PID）
- `%APPDATA%\devin\cli\summaries\history_<hash>.md` — 会话摘要
- `%APPDATA%\devin\cli\logs\` — 日志文件
- `%APPDATA%\devin\cli\app_state.json` — 应用状态
- `%APPDATA%\devin\cli\trusted_workspaces.json` — 受信任工作区
- `%APPDATA%\devin\cli\plugins\discovered.json` — 发现插件
- `%APPDATA%\devin\config.json` — 用户配置（org_id / theme_mode）
- `%APPDATA%\devin\credentials.toml` — 认证凭据

## 1. Transcript JSON 结构（`<session-name>.json`）

### 1.1 顶层结构

```json
{
  "schema_version": "ATIF-v1.7",
  "session_id": "blend-pencil",
  "agent": {
    "name": "devin",
    "version": "3000.4.16",
    "model_name": "SWE-1.6 Slow",
    "tool_definitions": [ ... ],
    "extra": { ... }
  },
  "steps": [ <step>, ... ],
  "final_metrics": {
    "total_prompt_tokens": 12345,
    "total_completion_tokens": 6789,
    "total_cached_tokens": 2345,
    "total_steps": 41
  }
}
```

### 1.2 Step 结构

每条 step 是对话中的一个事件：

| 字段 | 类型 | system | user | agent | 说明 |
|------|------|--------|------|-------|------|
| `step_id` | int | ✓ | ✓ | ✓ | 从 1 递增 |
| `timestamp` | ISO 8601 | ✓ | ✓ | ✓ | 含时区（如 `2026-08-15T06:10:11.032257100+00:00`） |
| `source` | str | `"system"` | `"user"` | `"agent"` | 消息来源 |
| `message` | str | ✓ | ✓ | ✓ | 文本内容 |
| `model_name` | str | | | ✓ | 模型名（如 `"SWE-1.6 Slow"`） |
| `reasoning_content` | str | | | ✓ | 思考过程（markdown 文本） |
| `tool_calls` | list | | | ✓ | `[{tool_call_id, function_name, arguments}]` |
| `observation` | dict | | | ✓ | `{results: [{source_call_id, content}]}` |
| `metrics` | dict | | | ✓ | `{prompt_tokens, completion_tokens, cached_tokens}` |
| `extra` | dict | ✓ | ✓ | ✓ | `{generation_model, telemetry: {source, operation}}` |

#### 1.2.1 tool_calls 结构

```json
{
  "tool_call_id": "call_041b7fd8d9a64117b1e54fbc",
  "function_name": "exec",
  "arguments": {
    "command": "ls \"C:\\path\\to\\dir\""
  }
}
```

一个 step 可包含**多个 tool_calls**（并行执行，按 `source_call_id` 匹配 observation）。

#### 1.2.2 observation 结构

```json
{
  "results": [
    {
      "source_call_id": "call_041b7fd8d9a64117b1e54fbc",
      "content": "Output from command ...\n\nExit code: 0"
    }
  ]
}
```

`results` 可与 tool_calls 一一对应（通过 `source_call_id`），也可为空（无工具结果时缺失）。

### 1.3 消息聚合策略

- **system** 消息：系统提示、指令、技能定义等，不产生产品可见消息
- **user** 消息：用户输入，`message` 为用户文本
- **agent** 消息：AI 回复，包含：
  - `message` → 回复文本（`type: text`）
  - `reasoning_content` → 思考过程（`type: thinking`）
  - `tool_calls` → 工具调用（`type: tool_use`）
  - `observation.results` → 工具结果（`type: tool_result`，通过 `source_call_id` 匹配）
- 每条 agent step 聚合为一条 `assistant` 消息，其内的所有 items 按序排列

### 1.4 工具全集（实测，devin 3000.4.16）

| 工具名 | 说明 | arguments 形状 |
|--------|------|---------------|
| `exec` | 执行 shell 命令 | `{command: str}` |
| `read` | 读取文件 | `{file_path: str}` |
| `edit` | 编辑文件 | `{file_path, old_string, new_string, replace_all?}` |
| `webfetch` | 获取网页 | `{url: str}` |
| `list` | 列出目录 | `{path: str}` |
| `grep` | 搜索文件内容 | `{pattern, path?, include?}` |
| `glob` | 文件匹配 | `{pattern, path?}` |
| `browser_preview` | 浏览器预览 | `{html?, port?}` |
| `close_browser_preview` | 关闭预览 | `{preview_id}` |
| `update_plan` | 更新任务计划 | `{plan: [{step, status}], explanation?}` |
| `notebook_read` | 读取 Jupyter notebook | `{notebook_path}` |
| `run_subagent` | 运行子代理 | `{profile, task, background?}` |
| `skill` | 执行 skill | `{name, arguments?}` |
| `ask_user` | 向用户提问 | `{question, options?}` |
| `write` | 写入文件 | `{file_path, content}` |

## 2. SQLite 索引（`sessions.db`）

### 2.1 sessions 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 会话 ID（如 `blend-pencil`） |
| `working_directory` | TEXT | 工作目录 |
| `backend_type` | TEXT | 后端类型（`windsurf` / `codex` 等） |
| `model` | TEXT | 模型名 |
| `agent_mode` | TEXT | 模式（`normal` / `bypass` / `plan`） |
| `created_at` | INTEGER | 创建时间（Unix 秒） |
| `last_activity_at` | INTEGER | 最后活动时间 |
| `title` | TEXT | 会话标题（首条消息） |
| `main_chain_id` | INTEGER | 主消息链 ID |
| `shell_last_seen_index` | INTEGER | Shell 最后可见索引 |
| `cogs_json` | TEXT | COGS JSON |
| `workspace_dirs` | TEXT | 工作区目录列表 |
| `hidden` | INTEGER | 是否隐藏 |
| `metadata` | TEXT | 额外元数据 |

### 2.2 message_nodes 表

树形消息结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `row_id` | INTEGER PK | 自增 |
| `session_id` | TEXT | 会话 ID |
| `node_id` | INTEGER | 节点 ID（会话内唯一） |
| `parent_node_id` | INTEGER | 父节点 ID（NULL 为根） |
| `chat_message` | TEXT | JSON 消息（含 `message_id`, `role`, `content`, `tool_calls`, `thinking`, `metadata`） |
| `created_at` | INTEGER | 创建时间 |
| `metadata` | TEXT | 额外元数据 |

`chat_message` JSON 结构：
```json
{
  "message_id": "672be268-20a8-4b49-818f-8bb75570b974",
  "role": "assistant" | "user" | "tool",
  "content": "文本内容",
  "tool_calls": [],
  "thinking": { "thinking": "思考文本", "signature": "" },
  "metadata": {
    "num_tokens": 527,
    "finish_reason": "stop",
    "metrics": {
      "ttft_ms": 1124,
      "total_time_ms": 3399,
      "input_tokens": 6623,
      "output_tokens": 527,
      "cache_read_tokens": 15584,
      "cache_creation_tokens": null,
      "tpot_ms": 4.32,
      "tokens_per_sec": 231.6
    },
    "generation_model": "swe-1-6-slow",
    "telemetry": { "source": "assistant", "operation": "inference" }
  }
}
```

### 2.3 tool_call_state 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | TEXT | 会话 ID |
| `tool_call_id` | TEXT | 工具调用 ID |
| `tool_call_json` | TEXT | 初始 ToolCall 事件 JSON |
| `tool_call_update_json` | TEXT | 完成 ToolCallUpdate JSON |

### 2.4 prompt_history 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK | 自增 |
| `content` | TEXT | 输入内容 |
| `timestamp` | INTEGER | 时间戳 |
| `session_id` | TEXT | 会话 ID |
| `is_shell` | INTEGER | 是否为 Shell 输入 |

## 3. 会话定位

### 3.1 存储布局

```
%APPDATA%\devin\cli\
├── transcripts\
│   ├── blend-pencil.json        ← 会话消息历史
│   ├── elemental-branch.json
│   └── ...
├── sessions.db                  ← SQLite 索引
├── session_locks\
│   ├── blend-pencil.lock        ← PID
│   └── ...
├── summaries\
│   ├── history_<hash>.md        ← 会话摘要
│   └── ...
├── logs\
│   ├── devin_20260815-*.log.gz
│   └── ...
└── app_state.json
```

### 3.2 会话 ID 命名

会话 ID 为 形容词-名词 组合：
- `blend-pencil`、`elemental-branch`、`feline-income`、`spiced-xylophone` 等
- 非 UUID，非数字
- 通过 `session_locks/<name>.lock` 文件确定运行中会话（内容为 PID）
- 无运行中会话时，lock 文件仍遗留（记录上次 PID）

### 3.3 定位策略

1. 按 session_id 在 `transcripts/<session_id>.json` 直接定位（最简单）
2. 从 `sessions.db` 查询所有会话（`SELECT * FROM sessions ORDER BY last_activity_at DESC`）
3. 遍历 `transcripts/` 目录全部 `.json` 文件

## 4. 屏幕快照格式（混合方案补充实时状态用）

### 4.1 Devin TUI 布局（v3000.4.16，实测）

```
[Logo 区：⠀⣴⣾⣶⡄ / Devin CLI / v3000.4.16]     ← 欢迎页
[Free plan, use /upgrade ... 100% remaining]       ← 配额行
[消息区（含 scrollback）]
❭ <用户输入>                                      ← 用户消息回显（❭ 前缀）
<AI 回复>                                          ← 助手回复（无前缀）
● Running command / ● Ran command                 ← 工具执行
  └ <输出> / └ Exited with code 0
⠦⠀ Thinking · Ns (esc twice to interrupt) ...     ← 思考中（Braille 旋转 + 秒数）
[权限请求框]
❭ 1 Yes  (Approve once)                            ← 权限选项（❭ 选中项 / · 未选中）
· 2 Yes, allow ...
↑↓ select · ↵ confirm · esc cancel                 ← 导航提示
──────────────────────────────────────────────────  ← 分隔线
❭ Ask Devin to build features, fix bugs, ...       ← 输入框（placeholder 空闲）
  / ❭ Guide Devin while it works                   ← 输入框（工作中提示）
──────────────────────────────────────────────────
SWE-1.6 Slow ... Context: 13k / 200k tokens (6%)   ← 状态栏
⚠︎ Unsupported terminal ...                        ← 警告/更新横幅（可有可无）
```

### 4.2 实时状态字段（transcript 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区底部 | `Thinking · Ns` 或 `(esc twice to interrupt)` = 思考中；`● Running` = 工具执行中；权限框（`↑↓ select · ↵ confirm` / `Approve once`）= 等待批准；**提问框（`↑↓ navigate · ↵ select` / `switch question` / `Other (type your own)`）= asking**；无以上 = 空闲 |
| 输入框文字 | 分隔线间 `❭` 行 | `❭` 后的文本；placeholder（空闲 `Ask Devin to build features...` / 工作中 `Guide Devin while it works`）不算实际输入 |
| 上下文百分比 | 状态栏 | `Context: 13k / 200k tokens (6%)` 提取百分比 |
| 模型名 | 状态栏行首 | `SWE-1.6 Slow`（两个空格以上分隔右侧提示） |
| 界面类型 | Logo 区 / 对话标记 | 有 `Devin CLI` + `v3000.` logo 框 = main；有对话标记（权限框/提问框/思考/工具行）= conversation |

### 4.2.1 模态框两种类型（实测区分）

| 特征 | 权限框（awaiting_approval） | 提问框（asking，ask_user_question） |
|------|---------------------------|-------------------------------------|
| 触发 | 工具需要批准（如 exec） | AI 主动提问（多选/单选） |
| 标题行 | 无 | `── AI工具 · 使用场景 · ... ──`（多问题导航） |
| 选项格式 | `❭ 1 Yes  (Approve once)` / `· 2 Yes, allow ...` | `❭ 1 Claude Code` + 描述行 / `· 2 Cursor` / `· Other (type your own)` |
| 导航提示 | `↑↓ select · ↵ confirm · esc cancel` | `↑↓ navigate · ↵ select · e select+type · ←→ switch question · ? help me out · esc cancel` |
| 底部提示 | 无 | `? Not ready to answer, help me out!` |
| 输入框/状态栏 | 模态框覆盖，不可见 | 模态框覆盖，不可见（model_display 为空） |

### 4.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `❭ <text>` | 用户输入回显 / 输入框 |
| 无前缀文本 | AI 回复正文 |
| `● Running command` + `└ $ <cmd>` | 工具执行中 |
| `● Ran command` / `● Updated todo list` / `● Read <file>` / `● Found files matching` | 工具执行完成 |
| `  │ <line>` / `└ Exited with code N` | 工具输出 / 退出码 |
| `⠦⠀ Thinking · Ns (esc twice to interrupt) · (Nc · ctrl+o for details · alt+t to toggle)` | 思考中 |
| `❭ 1 Yes  (Approve once)` + `· 2 ...` + `↑↓ select · ↵ confirm · esc cancel` | 权限请求框（等待批准） |
| `SWE-1.6 Slow ... Context: 13k / 200k tokens (6%)` | 状态栏（空闲/对话中） |
| `SWE-1.6 Slow ... Press Ctrl+L to clear the screen` | 状态栏（工作中，无 Context） |
| `⚠︎ Unsupported terminal` / `Update vX.Y.Z available!` | 横幅（可有可无） |

## 5. 已收集样本

| 样本 | 来源 | 说明 |
|------|------|------|
| `elemental-branch.json` | 真实 transcript | 21 steps，含多 tool_calls + 多 observation（裁剪为 18 steps fixture） |
| `victorious-squid.json` | 真实 transcript | 15 steps，含文件读取操作 |
| `sample_idle.txt` | 真实屏幕快照 | 欢迎页（logo + placeholder + 状态栏） |
| `sample_conversation_idle.txt` | 真实屏幕快照 | 对话空闲态（placeholder + Context 状态栏） |
| `sample_awaiting_approval.txt` | 真实屏幕快照 | 权限请求状态（`❭ 1 Yes` 选项框） |
| `sample_asking.txt` | 真实屏幕快照 | 提问框状态（ask_user_question，`↑↓ navigate` 多选） |
| `sample_working.txt` | 真实屏幕快照 | 思考/工作状态（`⠦ Thinking` + 工具完成行） |
| `sample_input_pending.txt` | 真实屏幕快照 | 输入待提交状态（输入框有文字，AI 空闲） |
| `sample_denied.txt` | 真实屏幕快照 | 权限拒绝状态（`✗ Tool execution was rejected`） |
| `sz_40x10.txt` | 真实屏幕快照 | 窄屏（40x10，欢迎页） |
| `sz_60x15.txt` | 真实屏幕快照 | 小屏（60x15，欢迎页） |
| `sz_80x24.txt` | 真实屏幕快照 | 中屏（80x24，欢迎页） |
| `sz_120x40.txt` | 真实屏幕快照 | 宽屏（120x40，欢迎页） |
| `sz_200x50.txt` | 真实屏幕快照 | 超宽屏（200x50，欢迎页） |
| `sessions.db` | 真实 SQLite | 35 条会话索引 |

## 6. 解析器返回结构建议

```json
{
  "session": {
    "id": "blend-pencil",
    "started_at": "2026-08-15T06:10:11.032257100+00:00",
    "status": "idle",
    "model": "SWE-1.6 Slow",
    "model_provider": "cognition",
    "cli_version": "3000.4.16",
    "cwd": "C:\\Users\\alice\\Desktop",
    "backend_type": "windsurf",
    "agent_mode": "normal",
    "title": "示例会话标题",
    "total_prompt_tokens": 12345,
    "total_completion_tokens": 6789,
    "total_cached_tokens": 2345,
    "total_steps": 41
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval",
    "input_text": "",
    "context_percent": 0.0,
    "screen_type": "main|conversation",
    "model_display": "",
    "cwd_display": ""
  },
  "messages": [
    {
      "id": "blend-pencil-u1",
      "role": "user",
      "ts": 1752108611000,
      "ts_iso": "2026-08-15T06:14:01.654456100+00:00",
      "items": [
        { "type": "text", "text": "帮我看看这个项目，哪个方案最合适" }
      ]
    },
    {
      "id": "blend-pencil-a1",
      "role": "assistant",
      "ts": 1752108614000,
      "ts_iso": "2026-08-15T06:14:04.856856700+00:00",
      "model": "SWE-1.6 Slow",
      "metrics": { "prompt_tokens": 13622, "completion_tokens": 111, "cached_tokens": 1792 },
      "items": [
        { "type": "thinking", "text": "用户想知道..." },
        { "type": "text", "text": "我来帮你查看..." },
        { "type": "tool_use", "tool_call_id": "call_...", "name": "exec",
          "input": { "command": "ls \"C:\\path\"" } },
        { "type": "tool_result", "tool_call_id": "call_...", "name": "exec",
          "success": true, "is_denied": false, "is_error": false,
          "output": "Output from command...\n\nExit code: 0" }
      ]
    }
  ]
}
```

## 7. 与 clineparser / claudeparser / codexparser 的差异

| 维度 | clineparser | claudeparser | codexparser | **devinparser** |
|------|-------------|-------------|-------------|-----------------|
| 存储 | `<id>.json` + `.messages.json`（双 JSON） | `<sessionId>.jsonl`（追加式 JSONL） | `rollout-<ts>-<uuid>.jsonl`（追加式 JSONL） | **`<session-name>.json`**（ATIF-v1.7，单 JSON + SQLite 索引） |
| 会话 ID | 数字+随机串 | UUID | UUID | **形容词-名词**（如 `blend-pencil`） |
| 会话定位 | session_id 直接目录 | 需 sessions/<pid>.json 索引或列出 projects | 按日期目录 `YYYY/MM/DD/rollout-*.jsonl` 搜索 | **transcripts/<id>.json 直接定位 + sessions.db 查询** |
| 时间戳 | 毫秒 int | ISO 字符串 | ISO 字符串 | **ISO 8601 含时区** |
| 用户输入 | `<user_input mode>` 标签包裹 | 直接字符串或 content 数组 | `input_text` 类型 | **`message` 字段** |
| tool_use | `tool_use` content 项 | `tool_use` content 项 | `function_call` 独立事件 | **`tool_calls` 数组（含 function_name + arguments）** |
| tool_result | list/str 双形态 | 字符串 content + 顶层 toolUseResult | `function_call_output` 独立事件（含 exit_code/duration） | **`observation.results` 数组（按 source_call_id 匹配）** |
| 思考 | `thinking` content 项 | `thinking` content 项 | `reasoning` 独立事件（流式分片） | **`reasoning_content` 字段** |
| 消息聚合 | 每事件一条消息 | 每事件一条消息 | 按回合聚合 | **按 step 聚合**（每条 agent step 含完整上下文） |
| 多工具并行 | 单 tool_call | 单 tool_call | 单 function_call | **多个 tool_calls / results 一一对应** |
| 会话元数据 | model/workspace_root/team/usage | mode/permission_mode/model/usage | model_provider/approval_policy/sandbox/context_window | **model/agent_mode/backend_type/title/metrics** |
| 屏幕状态 | 状态栏多行：模型/等级/进度条/Plan-Act | 输入框 `>`、effort、权限模式 | 输入框 `›`、`N% context left`、对话框判定 | **待验证** |
| 系统消息 | 过滤 | 过滤 | 过滤 | **保留 system 步（系统提示等）**——需过滤或标记 |

## 8. 已知注意事项

- **消息排序**：steps 按 `step_id` 递增排序，天然有序
- **system 消息**：会话开头有大量系统提示（角色设定、工具定义、技能文档），需过滤不列入消息列表，可选保留在额外字段
- **多工具并行**：一个 step 可含多个 tool_calls，observation.results 通过 source_call_id 一一对应
- **无工具结果**：某些 agent step 仅有 text 输出，无工具调用（纯回复）
- **无思考**：某些 agent step 仅有 tool_calls 无 reasoning_content（快速工具调用）
- **会话结束**：无显式结束标记，以最后一条 step 为准
- **运行中会话**：`session_locks/<name>.lock` 文件存有 PID，但进程退出后 lock 文件仍残留（需检查进程是否存在）
- **SQLite 辅助**：sessions.db 提供额外元数据（title/agent_mode/backend_type），message_nodes 提供更丰富的 metrics/thinking 详情
- **屏幕快照**：当前缺少实际 Devin TUI 屏幕快照样本，待 PTY-Agent 可运行后补充（需 PTY-Agent daemon 在非沙箱环境运行，`read <sid> --keep-ansi -o <file>` 抓取）
- **resume 会话**：`devin -r <session-id>` 可恢复会话，推测追加写入同一 transcript 文件