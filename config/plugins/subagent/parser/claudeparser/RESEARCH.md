# Claude Code 解析器调研报告（claudeparser）

## 0. 核心结论

**claude 在本地存储完整结构化对话历史（JSONL 追加日志）**，解析器采用**混合方案**：
- **JSONL 消息历史**（主源）：`~/.claude/projects/<cwd-encoded>/<sessionId>.jsonl`
  — 完整消息 + 工具调用 + usage + 系统事件
- **运行会话索引**（会话定位）：`~/.claude/sessions/<pid>.json` — pid → sessionId 映射
- **屏幕快照**（补充）：pty-agent `--full --keep-ansi` 输出 → 实时状态
  （AI 状态/输入框/权限模式/effort，JSONL 中部分缺失或不即时）

本地存储位置：
- `~/.claude/projects/<cwd-encoded>/<sessionId>.jsonl` — 消息历史（追加式）
- `~/.claude/sessions/<pid>.json` — 运行中会话元数据（pid → sessionId、cwd、status）
- `~/.claude/history.jsonl` — 输入历史（display/timestamp/project/sessionId）
- `~/.claude/settings.json` — 设置（model 等）
- cwd 编码规则：`C:\Users\alice` → `C--Users-alice`（盘符冒号→`-`，`\`→`-`）

## 1. 会话元数据（`~/.claude/sessions/<pid>.json`）

```json
{
  "pid": 1724,
  "sessionId": "74cc5917-43d1-4806-909f-273e43eb0c0c",
  "cwd": "C:\\Users\\alice\\Desktop",
  "startedAt": 1787408647124,
  "procStart": "134318820911843489",
  "version": "2.1.239",
  "peerProtocol": 1,
  "peerFeatures": ["notify_idle"],
  "kind": "interactive",
  "entrypoint": "cli",
  "name": "desktop-5a",
  "nameSource": "derived",
  "status": "idle",             // idle | running | ...
  "updatedAt": 1787408647044,
  "statusUpdatedAt": 1787408647044
}
```

注意：**jsonl 中没有 started_at 顶层字段**；会话开始时间可从 sessions/<pid>.json 的
startedAt 或 jsonl 首条消息 timestamp 获得。jsonl 各事件也带 timestamp（ISO 字符串）。

## 2. JSONL 消息历史结构（`<sessionId>.jsonl`）

每行一个 JSON 事件，`type` 字段区分事件类型。实测样本（31 行 / 一轮完整交互）：

| type | 说明 | 关键字段 |
|------|------|----------|
| `mode` | 模式 | `mode`（normal/plan/auto 等） |
| `permission-mode` | 权限模式 | `permissionMode`（default 等） |
| `atis-latch` | ATIS | `atis` |
| `last-prompt` | 最后提示 | `leafUuid` |
| `file-history-snapshot` | 文件快照 | `snapshot.trackedFileBackups` |
| `user` | 用户消息 | `message.content`（str 或数组）；`promptSource`（typed）；`origin.kind`（human） |
| `assistant` | 模型回复 | `message.content[]`；`message.model`；`message.usage`；`effort` |
| `system` | 系统事件 | `subtype`（turn_duration 等） |
| `attachment` | 附加信息 | `attachment.type`（agent_listing_delta / total_tokens_reminder 等） |

### 2.1 user 消息两种形态

- **字符串形态**（typed 输入）：`message.content` 是 `"帮我看看 ..."` 字符串
- **数组形态**（含 tool_result）：`message.content: [{type: "tool_result", ...}]`

user 消息附加字段：`promptSource: "typed"`、`origin: {kind: "human"}`、
`userType: "external"`、`cwd`、`gitBranch`。

### 2.2 assistant 消息 content 项类型（4种）

| type | 字段 | 说明 |
|------|------|------|
| `thinking` | `thinking`（+`signature`） | 思考过程 |
| `text` | `text` | 正文 |
| `tool_use` | `id`(call_id), `name`, `input` | 工具调用 |
| `tool_result`（在 user 消息里） | `tool_use_id`, `content` | 工具结果 |

assistant 消息附加字段：`message.model`、`message.usage`（token 明细）、
`message.stop_reason`（tool_use / end_turn 等）、顶层 `effort`（high 等）。

### 2.3 工具类型（实测样本）

| 工具 | input 键 | tool_result content 形态 | 附加结构 |
|------|----------|--------------------------|----------|
| `Glob` | `{pattern, path}` | 换行分隔的文件路径字符串 | 顶层 `toolUseResult: {filenames[], durationMs, numFiles, truncated}` |
| `Read` | `{file_path, limit?}` | 文件内容文本 | 顶层 `toolUseResult` |

tool_result 的 user 消息顶层还有 `sourceToolAssistantUUID`（对应 assistant 消息 uuid）、
`toolUseResult`（结构化结果，含 durationMs）。

### 2.4 轮次结构（parentUuid 链接）

每条事件有 `uuid`，下一条事件 `parentUuid` 指向前一条（单链表）。
- user 输入 → assistant（thinking → text → tool_use*）→ user(tool_result) → assistant ...
- 一轮结束由 `system{subtype: "turn_duration", durationMs, messageCount}` 标记

## 3. 屏幕快照格式（混合方案补充实时状态用）

### 3.1 Claude Code TUI 布局（v2.1.240，实测）

```
[提示横幅（未知模型警告等，可有可无）]
╭─── Claude Code v2.1.240 ─────╮   ← 欢迎页大框（首会话显示）
│   sensenova-6.8-flash-lite · API Usage Billing │
│               ~\Desktop\example-project              │
╰──────────────────────────────╯
[消息区（含 scrollback）：用户输入回显 / ● 回复 / ✢ 状态行]
──────────────────────────────────────────────────  ← 分隔线
> <输入框>                                        ← 输入框（提示符 >）
──────────────────────────────────────────────────
  ⏸ manual mode on · ? for shortcuts · ← for agents   ← 状态栏（左）
                          ● high · /effort            ← 状态栏（右，effort）
```

### 3.2 实时状态字段（JSONL 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区底部 | `● Thinking for Xs...` = 思考中；`✢ Hullaballooing…` = 工具执行；`Do you want to proceed?` 权限框 = 等待批准；无 = 空闲 |
| 输入框文字 | `>` 后 | `>` 后的文本 |
| effort | 状态栏右侧 | `● high · /effort` 中 `●` 后的等级 |
| 权限模式 | 状态栏左侧 | `⏸ manual mode on` / `auto-accept edits` 等 |
| 模式 | 状态栏/JSONL | Claude Code 模式（normal/plan），JSONL `mode` 事件为准 |
| 工作目录 | 欢迎页/状态栏 | `~\Desktop\example-project` 或 JSONL `cwd` |
| token 占用 | JSONL usage | 各 assistant 消息 usage 累加；上下文百分比需自定义（Claude Code TUI 默认不显示进度条） |

### 3.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `>` 后文本 | 用户输入回显 |
| `●` 行 | AI 回复正文（深色圆点前缀） |
| `✢`/`✻` 旋转/状态行 | 工具执行中 / 思考中 / 完成 |
| `Thinking for Xs...` | 思考中（黄色圆点 ●） |
| `Do you want to proceed?` + 选项列表 | 权限请求框（等待批准） |
| 状态栏 `⏸ manual mode on` | 权限模式 |

## 4. 已收集样本

| 文件 | 场景 |
|------|------|
| sample_idle.txt | 空闲态（对话中，输入框空，状态栏可见） |
| sample_input_pending.txt | 待提交输入状态（输入框有文字，AI 空闲） |
| sample_awaiting_approval.txt | 权限请求状态（Do you want to proceed? 框） |
| sample_ask.txt | AskUserQuestion 提问（`[ ] header` + 选项列表 + `Enter to select`） |
| sample_working.txt | 工具执行中（`* Booping…` / `esc to interrupt`） |
| sz_40x10.txt ~ sz_200x50.txt | 5 种终端尺寸（40x10 / 60x15 / 80x24 / 120x40 / 200x50，对话中状态） |
| 会话 9b56c0c7-...jsonl | 完整 2 轮交互（Glob + Read 工具、thinking、权限批准、turn_duration） |
| 会话 f2341e7a-...jsonl | AskUserQuestion 交互（提问 → 选择 Python → 回复） |

屏幕快照获取方式：pty-agent 终端模式 `read <sid> --keep-ansi -o <file>`（**snapshot 模式**，
不带 `--full`——Claude Code TUI 是增量刷新，`--full` 返回追加流会导致 pyte 渲染重叠）。
多尺寸用 `--default terminal-size WxH` 对运行中会话即时调整后抓取。

测试会话：`claudetest`（pty-agent，通过 `s.ps1` shell 包裹启动）

### 4.1 多尺寸解析注意事项（实测）

| 现象 | 处理 |
|------|------|
| 窄屏（40x10/60x15）欢迎页顶框滚出屏幕 | screen_type 用底框 `╰────` 补充判定 |
| 宽屏（200x50）输入框行混入 `─` 残留 | input_text 过滤纯分隔符 |
| 200 列时框内横线被 `─{10,}` 误匹配 | 分隔线正则锚定 `^\s*─{10,}\s*$`（整行纯 ─） |
| 对话中残留欢迎页框 | 有对话标记（权限框/思考/工具行）时优先判 conversation |
| effort 显示非常驻 | 主来源 JSONL `effort` 字段；屏幕尽力解析 |

## 5. 解析器返回结构建议

```json
{
  "session": {
    "id": "9b56c0c7-...", "cwd": "C:\\Users\\alice\\Desktop\\example-project",
    "started_at": "...", "status": "idle",
    "model": "sensenova-6.8-flash-lite", "version": "2.1.240",
    "mode": "normal", "permission_mode": "default",
    "git_branch": "HEAD", "pid": 1724,
    "usage": { "input_tokens": 0, "output_tokens": 0,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0 }
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval",
    "input_text": "", "effort": "high", "permission_mode": "...",
    "mode": "normal|plan", "screen_type": "main|conversation"
  },
  "messages": [
    {
      "id": "...", "role": "user|assistant", "ts": 0, "ts_iso": "...",
      "model": "...", "effort": "high", "prompt_source": "typed",
      "usage": { ... },
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "..." },
        { "type": "tool_use", "tool_call_id": "...", "name": "Read",
          "input": { "file_path": "..." } },
        { "type": "tool_result", "tool_call_id": "...", "name": "Read",
          "success": true, "is_denied": false, "error": null,
          "result": "..." }
      ]
    }
  ]
}
```

## 6. 与 example-project 的差异

| 维度 | example-project | claudeparser |
|------|-------------|--------------|
| 存储 | `<id>.json` + `<id>.messages.json`（双 JSON） | `<sessionId>.jsonl`（单文件追加式） |
| 会话定位 | session_id 直接定位目录 | 需 sessions/<pid>.json 索引 或 列出 projects 下全部 jsonl |
| 时间戳 | 毫秒 int | ISO 字符串 |
| 用户输入 | `<user_input mode>` 标签包裹 | 直接字符串 或 content 数组 |
| tool_result | list/str 双形态 | 多为字符串 content + 顶层 toolUseResult |
| 模式 | Plan/Act | normal/plan（mode 事件） |
| effort | 思考等级 (xhigh) | high/medium/low（assistant 事件 + 状态栏） |
| 权限模式 | 状态栏行3 | 状态栏左侧 + permission-mode 事件 |
