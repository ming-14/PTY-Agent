# opencode 解析器调研报告（opencodeparser）

## 0. 核心结论

**opencode（sst/opencode）在本地存储完整结构化对话历史（SQLite 数据库 + event sourcing）**，解析器采用**混合方案**：
- **SQLite 数据库**（主源）：`~/.local/share/opencode/opencode.db`
  — session / message / part 三表物化视图，完整消息历史 + 工具调用 + 思考 + token 用量
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/上下文百分比/界面类型）

本地存储位置（`~/.local/share/opencode/`）：
- `opencode.db` — SQLite 主存储（475 MB，含 146 会话 / 11019 消息 / 42892 parts）
- `log/opencode.log` — 运行日志
- `storage/session_diff/` — 会话差异快照
- `system/` — 内置技能定义
- `tool-output/` — 工具输出缓存
- `snapshot/` — 屏幕快照
- `settings.json` — 安全设置（folderTrust）
- `auth.json` — 认证数据

配置目录：`~/.config/opencode/`（opencode.jsonc 56KB，含 provider/plugin 配置）

## 1. 启动命令与版本

```
opencode
```

- 版本：1.18.21
- 底层：Bun 1.3.14 编译的 TypeScript 应用
- 默认 agent：`build`
- 默认模型：`x-preview-f-free`（provider: OpenCode Zen）

## 2. SQLite 数据库结构（`opencode.db`）

### 2.1 表全景（24 表）

| 表名 | 行数 | 说明 |
|------|------|------|
| `session` | 146 | 会话元数据 |
| `message` | 11019 | 消息（物化视图） |
| `part` | 42892 | 消息内容项 |
| `event` | 166486 | 事件溯源日志 |
| `event_sequence` | 144 | 会话事件序号 |
| `project` | 2 | 项目配置 |
| `todo` | 281 | 待办事项 |
| `cag_message_extra` | - | 消息额外元数据 |
| `cag_session_extra` | - | 会话额外元数据 |
| `session_input` | - | 会话输入 |
| `session_context_epoch` | - | 上下文快照 |
| 其他 | 0 | migration/account/credential/workspace 等 |

### 2.2 session 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 会话 ID（ses_xxx） |
| `project_id` | TEXT | 项目 ID（global 或 git hash） |
| `parent_id` | TEXT | 父会话 ID（子代理） |
| `slug` | TEXT | 会话别名（如 `proud-lagoon`） |
| `directory` | TEXT | 工作目录 |
| `path` | TEXT | 相对路径 |
| `title` | TEXT | 会话标题 |
| `version` | TEXT | opencode 版本 |
| `agent` | TEXT | agent 类型（build / explore 等） |
| `model` | TEXT | JSON 格式 `{"id":"...","providerID":"...","variant":"..."}` |
| `cost` | REAL | 总费用 |
| `tokens_input` | INTEGER | 输入 token 数 |
| `tokens_output` | INTEGER | 输出 token 数 |
| `tokens_reasoning` | INTEGER | 思考 token 数 |
| `tokens_cache_read` | INTEGER | 缓存读取 token 数 |
| `tokens_cache_write` | INTEGER | 缓存写入 token 数 |
| `permission` | TEXT | 权限规则 JSON |
| `time_created` | INTEGER | 创建时间（毫秒） |
| `time_updated` | INTEGER | 最后更新时间 |
| `time_archived` | INTEGER | 归档时间 |

### 2.3 message 表

`message.data` 为 JSON 字符串，关键字段：

| 字段 | 类型 | user | assistant | 说明 |
|------|------|:----:|:---------:|------|
| `role` | str | ✓ | ✓ | user / assistant |
| `parentID` | str | ✓ | ✓ | 父消息 ID |
| `time.created` | int | ✓ | ✓ | 创建时间戳 |
| `agent` | str | ✓ | ✓ | agent 类型 |
| `model`/`modelID` | str | | ✓ | 模型 ID |
| `providerID` | str | | ✓ | 提供商 ID |
| `variant` | str | | ✓ | 模型变体（max 等） |
| `mode` | str | | ✓ | 模式（build） |
| `path.cwd` | str | | ✓ | 工作目录 |
| `path.root` | str | | ✓ | 项目根目录 |
| `cost` | float | | ✓ | 本轮费用 |
| `tokens` | dict | | ✓ | `{input, output, reasoning, cache:{read, write}}` |
| `finish` | str | | ✓ | 结束原因（tool-calls / stop 等） |
| `summary.diffs` | list | ✓ | | 文件变更摘要 |

### 2.4 part 表

`part.data` 为 JSON 字符串，`type` 字段区分类型：

| type | 角色 | 数量 | 说明 | 关键字段 |
|------|:----:|:----:|------|----------|
| `tool` | assistant | 11626 | 工具调用与结果 | `tool`, `callID`, `state{status,input,output,error}` |
| `step-start` | assistant | 10214 | 生命周期开始（过滤） | |
| `step-finish` | assistant | 10086 | 生命周期结束（过滤） | `reason`(tool-calls/error/aborted), `tokens`, `cost` |
| `text` | assistant | 4595 | 回复正文 | `text` |
| `reasoning` | assistant | 5664 | 思考过程 | `text`, `time{start,end}` |
| `text` | user | 704 | 用户输入 | `text` |
| `file` | user | 2 | 附件（图片等） | `mime`, `filename`, `url` |

### 2.5 tool 详细结构

```json
{
  "type": "tool",
  "tool": "bash|read|edit|write|grep|glob|question|...",
  "callID": "call_xxx",
  "state": {
    "status": "completed|error|running",
    "input": { ... },
    "output": "...",
    "error": "error message",
    "time": { "start": ..., "end": ... }
  }
}
```

| 工具名 | 次数 | 说明 |
|--------|:----:|------|
| `bash` | 4756 | 执行 shell 命令 |
| `read` | 2781 | 读取文件 |
| `edit` | 2136 | 编辑文件 |
| `grep` | 752 | 搜索文件内容 |
| `write` | 750 | 写入文件 |
| `glob` | 176 | 文件匹配 |
| `todowrite` | 101 | 更新待办 |
| `compress` | 65 | 上下文压缩 |
| `question` | 57 | 向用户提问 |
| `task` | 29 | 子代理任务 |
| `webfetch` | 15 | 网页获取 |
| `invalid` | 13 | 无效工具调用 |

### 2.6 消息聚合规则

1. 每条 message 行 → 一条 Message（user / assistant）
2. 所有 part 按 `time_created` 排序聚合到对应 message
3. 过滤 step-start / step-finish（生命周期事件）
4. 完成的 tool part 同时输出 tool_use（调用）+ tool_result（结果）
5. reasoning → thinking（思考文本）
6. 用户消息中的 `<system-reminder>` 前缀系统消息已过滤（opencode 不生成此类消息，但保留过滤逻辑）

### 2.7 会话导出（CLI 参考）

`opencode export <sessionID>` 输出 JSON 格式：
```json
{
  "info": { "id", "slug", "projectID", "directory", "path", "title", "agent", "model", "version", "summary", "cost", "tokens", "time" },
  "messages": [
    { "info": { ... }, "parts": [ { "type": "text", "text": "..." }, ... ] }
  ]
}
```

可用于交叉验证解析器输出。

## 3. 屏幕快照格式（混合方案补充实时状态用）

### 3.1 opencode TUI 布局（v1.18.21，实测）

**欢迎页（main）**：
```
                                                       ▄
                     █▀▀█ █▀▀█ █▀▀█ █▀▀▄ █▀▀▀ █▀▀█ █▀▀█ █▀▀█
                     █  █ █  █ █▀▀▀ █  █ █    █  █ █  █ █▀▀▀
                     ▀▀▀▀ █▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀ ▀▀▀▀
  ┃
  ┃  Ask anything... "Fix broken tests"
  ┃
  ┃  Build · Ox Alpha Free (Unlimited) OpenCode Zen · max
  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀
  tab agents  ctrl+p commands
  ~\Desktop\opencodeparser                                1.18.21
```

**对话中（conversation）**：
```
  ┃  你好，请简单介绍一下你自己
                                              Context
  + Thought: 2.9s                             10,721 tokens
                                               1% used
  你好，alice！我是 opencode...                $0.00 spent
                                               LSP
  ┃                                            LSPs are disabled
  ┃  请用 dir 命令列出当前目录内容

  ┃  $ dir
  ┃  Directory: ...
  [工具输出]
  ┃  Build · Ox Alpha Free (Unlimited) OpenCode Zen · max
  ╹▀▀▀▀... (separator)
  C:\Users\alice\Desktop\opencodeparser  11.0K (1%)  ctrl+p commands    • OpenCode 1.18.21
```

**权限请求（awaiting_approval）**：
```
  ┃  △ Permission required
  ┃    ← Access external directory C:\Temp
  ┃  Patterns
  ┃  - C:\temp\*
  ┃   Allow once   Allow always   Reject    ctrl+f fullscreen  ⇆ select  enter confirm
```

**提问框（question 工具，awaiting_answer）**：
```
  ┃  alice，opencodeparser 目前已具备...下一步优先开发哪个方向？
  ┃
  ┃  1. watch 实时监控模式
  ┃     轮询 SQLite + 屏幕快照，持续输出运行中会话的实时状态...
  ┃  2. Markdown/文本渲染输出
  ┃     ...
  ┃  3. schema 版本兼容加固
  ┃     ...
  ┃  4. Type your own answer
  ┃  ↑↓ select  enter submit  esc dismiss
```

### 3.2 实时状态字段（JSONL 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区/底部 | `△ Permission required` = awaiting_approval；`↑↓ select` / `enter submit` / `esc dismiss` / `Type your own answer` = awaiting_answer（question 提问）；`■⬝⬝⬝⬝⬝⬝⬝ esc interrupt` = working（有 `+ Thought:` 时 = thinking；有工具行时 = tool_running）；无以上 = idle |
| 输入框文字 | 底部 `┃` 后 | `┃ Ask anything...` = placeholder（空）；`┃ <text>` = 实际输入 |
| 上下文百分比 | 状态栏 / 右侧面板 | 状态栏 `(N%)` 或右侧面板 `N% used` |
| 费用 | 右侧面板 | `$X.XX spent` |
| 界面类型 | 全屏 | 有 `█▀▀█` LOGO 且无对话标记 = main；否则 = conversation |
| 模型名 | 底部 `┃ Build · <model> · OpenCode` | 解析 `Build · <name> · OpenCode` |
| 版本号 | 状态栏行尾 | `• OpenCode X.Y.Z` 或裸 `X.Y.Z`（可能被终端宽度裁剪，需从原始 VT 文本提取） |

### 3.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `┃ <text>` | 用户输入回显（`>` 前缀） |
| `+ Thought: Xs` | 思考过程完成标记 |
| 无前缀文本 | AI 回复正文 |
| `$ <command>` | 工具执行命令 |
| `← Write <file>`, `→ Read <file>`, `← Edit <file>` | 工具调用 |
| `# Wrote <file>` | 文件写入完成 |
| `▣  Build · ... · Xs` | 步骤完成标记 |
| `■⬝⬝⬝⬝⬝⬝⬝  esc interrupt` | 思考/工作状态（进度条） |
| `△ Permission required` + `Allow once` / `Allow always` / `Reject` | 权限请求框（awaiting_approval） |
| `↑↓ select` + `enter submit` + `esc dismiss` + 编号选项 | question 提问框（awaiting_answer） |
| `╹▀▀▀▀...` | 底部分隔线 |

## 4. 已收集样本

| 文件 | 场景 | 来源 |
|------|------|------|
| `sample_idle.txt` | 欢迎页（main，含 LOGO + placeholder + 版本号） | PTY-Agent 屏幕快照 |
| `sample_input_pending.txt` | 输入待提交（欢迎页 + 输入框有文字） | PTY-Agent 屏幕快照 |
| `sample_conversation_idle.txt` | 对话空闲态（含工具输出 + 右侧面板 + 状态栏） | PTY-Agent 屏幕快照 |
| `sample_working.txt` | 工作中（进度条 + esc interrupt） | PTY-Agent 屏幕快照 |
| `sample_awaiting_approval.txt` | 权限请求（Permission required 框） | PTY-Agent 屏幕快照 |
| `sample_ask.txt` | question 提问（编号选项 + ↑↓ select 导航） | PTY-Agent 屏幕快照 |
| `sz_40x10.txt` ~ `sz_200x50.txt` | 5 种终端尺寸对话空闲态 | PTY-Agent 屏幕快照 |
| `sample_opencode.db` | 真实会话 SQLite 数据库（1 会话 / 13 消息 / 37 parts） | 从真实库提取 |

## 5. 与 claudeparser / clineparser / codexparser / devinparser / workbuddyparser 的差异

| 维度 | 其他解析器 | **opencodeparser** |
|------|-----------|-------------------|
| 存储 | JSONL / JSON / ATIF | **SQLite DB（session/message/part 三表）** |
| 会话 ID | UUID / 数字+随机 / 形容词-名词 | **ses_xxx**（如 `ses_ffecfe685ffeGCr3ZSBObXSlhu`） |
| 会话定位 | 文件系统扫描 / JSON 索引 | **SQLite 查询**（`SELECT * FROM session`） |
| 时间戳 | 毫秒 int / ISO 字符串 | **毫秒 int**（message.data.time.created） |
| 用户输入 | 字符串 / content 数组 / input_text 标签 | **part.type=text**（text 字段） |
| tool_use | content 项 / function_call 独立事件 | **tool part**（state.input 为调用参数） |
| tool_result | content 项 / function_call_output 独立事件 | **tool part**（state.output 为结果，工具调用与结果同体） |
| 思考 | thinking content 项 / reasoning 独立事件 | **reasoning part**（text 字段） |
| 消息聚合 | 每事件 / 按回合 / 按 step | **按 message 行聚合**（message 表 + part 表） |
| Token 用量 | message.usage / metrics 字段 | **message.data.tokens**（input/output/reasoning/cache.read/write） |
| 运行中会话 | sessions/<pid>.json / session_locks | **part 表 status=running**（进行中的工具调用） |
| 子代理 | Agent 工具 / agent-*.jsonl | **parent_id 链**（session.parent_id 指向父会话） |

## 6. 已知注意事项

- **消息 ID**：message 表有 `id`（msg_xxx），part 表有 `id`（prt_xxx），无显式序号。解析器使用 message.id 作为消息 ID。
- **step-start / step-finish**：为内部生命周期事件，不产生消息内容项，需要过滤。
- **tool 调用与结果同体**：一个 tool part 同时承载调用参数（state.input）和结果（state.output），解析时同时输出 tool_use 和 tool_result 两个 item。
- **模型信息**：session.model 为 JSON 字符串（`{"id":"...","providerID":"...","variant":"..."}`），需解析。
- **版本号提取**：状态栏版本号可能超出终端宽度（120 列），pyte 渲染后行尾被裁剪，需从原始 VT 文本直接搜索。
- **运行中会话**：通过查询 part 表 state.status='running' 判定，无需外部进程锁。
- **屏幕快照**：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` 抓取，opencode TUI 是增量刷新，`--full` 可能导致 pyte 渲染重叠。
- **placeholder 输入**：输入框 `Ask anything...` 为 placeholder，不视为实际输入。

## 7. 解析器返回结构

```json
{
  "session": {
    "id": "ses_ffecfe685ffeGCr3ZSBObXSlhu",
    "slug": "proud-lagoon",
    "cwd": "C:\\Users\\alice\\Desktop",
    "title": "查看 PTY-Agent 项目",
    "agent": "build",
    "model": "deepseek-v4-flash-free",
    "model_provider": "OpenCode Zen",
    "variant": "max",
    "version": "1.18.11",
    "cost": 0.0,
    "started_at": "1786726324603",
    "usage": {
      "input_tokens": 3333821,
      "output_tokens": 253742,
      "reasoning_tokens": 108148,
      "cache_read_input_tokens": 163500928,
      "cache_write_input_tokens": 0,
      "total_cost": 0.0
    }
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval|awaiting_answer",
    "input_text": "",
    "context_percent": 0.0,
    "context_tokens": 0,
    "cost_display": "",
    "screen_type": "main|conversation",
    "model_display": "Ox Alpha Free",
    "cwd_display": "~\\Desktop\\opencodeparser",
    "version_display": "1.18.21"
  },
  "messages": [
    {
      "id": "msg_xxx",
      "role": "user",
      "ts": 1786726324645,
      "ts_iso": "2026-08-14T16:52:04.645Z",
      "items": [
        { "type": "text", "text": "帮我看看这个项目" },
        { "type": "thinking", "text": "The user wants me to look at..." },
        { "type": "tool_use", "tool_use": { "tool_call_id": "call_xxx", "name": "read", "input": { "filePath": "..." } } },
        { "type": "tool_result", "tool_result": { "tool_call_id": "call_xxx", "name": "read", "success": true, "is_denied": false, "is_error": false, "output": "..." } }
      ],
      "model": "deepseek-v4-flash-free",
      "provider": "OpenCode Zen",
      "agent": "build",
      "finish": "tool-calls",
      "usage": { "input_tokens": 8931, "output_tokens": 86, "reasoning_tokens": 0, "cache_read_input_tokens": 0, "cache_write_input_tokens": 0 }
    }
  ]
}
```