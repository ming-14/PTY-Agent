# Crush 解析器调研报告（crushparser）

## 0. 核心结论

**Crush（charmbracelet/crush，Go 编写的 AI 编码 CLI）在本地存储完整结构化对话历史（SQLite）**，解析器采用**混合方案**：
- **SQLite 数据库**（主源）：`<project>/.crush/crush.db`
  — sessions / messages / files / read_files 四表，完整消息 + 工具调用 + 思考 + token 用量
- **项目索引**（会话定位）：`%CRUSH_GLOBAL_DATA%/projects.json` — path → data_dir 映射
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态 / 输入框 / 上下文百分比 / 模型 / 费用，SQLite 中缺失或不即时）

启动方式：`C:\Users\alice\Desktop\CRUSH.ps1`（唯一启动方式，设置 `CRUSH_GLOBAL_CONFIG` /
`CRUSH_GLOBAL_DATA` / `CRUSH_SKILLS_DIR` / API key / base_url 后执行
`C:\UserProgram\AI-Launcher\Crush\crush.exe @args`）。

当前版本：v0.90.0（有 v0.91.0 更新提示）。

本地存储位置：
- `%USERPROFILE%\__crush\data\projects.json` — 项目注册表（path → data_dir）
- `%USERPROFILE%\__crush\crush.json` — 全局配置（models / providers）
- `<project>\.crush\crush.db` — 项目 SQLite 数据库（sessions/messages/files/read_files）
- `<project>\.crush\logs\crush.log` — 运行日志
- `<project>\.crush\init` — 初始化标记（空文件）
- `<project>\.crush\.gitignore` — 内容为 `*\n`
- `%USERPROFILE%\__crush\data\crush.json` — 全局数据配置（`{"options":{"tui":{"transparent":true}}}`）
- `%USERPROFILE%\__crush\data\crush.json.lock` — 全局数据锁
- `%USERPROFILE%\__crush\skills\<name>\SKILL.md` — 全局技能目录

**data_dir 解析规则**（config.setDefaults）：
1. `--data-dir` 显式指定 > 配置文件 `data_directory` > 默认 `.crush`
2. 默认路径：`fsext.LookupClosestBounded(cwd, projectBoundary, ".crush")` 向上查找最近的 `.crush` 目录（git 根边界内），找不到则 `cwd/.crush`
3. 相对路径按 cwd 解析；绝对路径原样使用

**注意**：数据库按**项目**隔离。`~/.crush` 是 `C:\Users\alice` 项目的数据库；
在 `C:\Users\alice\Desktop\crushparser` 下启动会使用 `C:\Users\alice\Desktop\crushparser\.crush`（尚无数据）。
解析器必须支持跨项目定位：先查 projects.json，再在全部 data_dir 中查找会话。

## 1. SQLite 数据库结构（`crush.db`）

### 1.1 表全景（6 表：4 业务表 + 2 系统表）

| 表名 | 行数（实测） | 说明 |
|------|------|------|
| `sessions` | 1 | 会话元数据 |
| `messages` | 6 | 消息（物化视图，parts JSON） |
| `files` | 0 | 会话文件快照（版本化） |
| `read_files` | 0 | 会话读取文件记录 |
| `goose_db_version` | 7 | 迁移版本 |
| `sqlite_sequence` | 0 | SQLite 内部 |

### 1.2 sessions 表

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    parent_session_id TEXT,
    title TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0 CHECK (message_count >= 0),
    prompt_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (prompt_tokens >= 0),
    completion_tokens  INTEGER NOT NULL DEFAULT 0 CHECK (completion_tokens>= 0),
    cost REAL NOT NULL DEFAULT 0.0 CHECK (cost >= 0.0),
    updated_at INTEGER NOT NULL,  -- Unix timestamp（秒，注释误写毫秒）
    created_at INTEGER NOT NULL,  -- Unix timestamp（秒）
    summary_message_id TEXT,      -- 迁移 20250515105448 追加
    todos TEXT                    -- 迁移 20250812000000 追加，JSON 数组
);
```

- **时间戳单位为秒**：`internal/cmd/session.go` 用 `time.Unix(s.CreatedAt, 0)` 解析；
  `internal/message/content.go` 用 `time.Now().Unix()` 写入。SQL 注释 "milliseconds" 是历史遗留误导。
- `updated_at` 由触发器自动更新为 `strftime('%s', 'now')`。
- `todos` JSON 数组：`[{"content": "...", "status": "pending|in_progress|completed", "active_form": "..."}]`。
- 会话 ID 形态：
  - UUID（标准会话）：`716187c5-046d-46a4-b8ce-00a33067b620`
  - `title-<parentSessionID>`（标题生成内部会话）
  - `<messageID>$$<toolCallID>`（agent tool 子会话，session.IsAgentToolSession）
- **无 cwd / model / status 字段**：会话元数据中不存工作目录和模型，这些需从全局配置 / 消息 / 屏幕快照获取。

### 1.3 messages 表

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,           -- user | assistant | tool | system
    parts TEXT NOT NULL default '[]',   -- JSON 数组，见 1.4
    model TEXT,
    created_at INTEGER NOT NULL,  -- Unix timestamp（秒）
    updated_at INTEGER NOT NULL,  -- Unix timestamp（秒）
    finished_at INTEGER,          -- 消息完成时间（秒），Finish part 的 time
    provider TEXT,                -- 迁移 20250627000000 追加
    is_summary_message INTEGER NOT NULL DEFAULT 0  -- 迁移 20250810000000 追加
);
```

索引：`idx_messages_session_id`（session_id）、`idx_messages_created_at`（created_at）。
触发器：插入/删除消息自动增减 sessions.message_count。

### 1.4 parts JSON 结构（关键）

每行消息的 `parts` 是 JSON 数组，每个元素形如 `{"type": "<类型>", "data": {...}}`，类型全集（8 种，源码 `internal/message/message.go` marshalParts/unmarshalParts）：

| type | data 字段 | 说明 |
|------|-----------|------|
| `text` | `text` | 正文文本 |
| `reasoning` | `thinking`, `signature`, `thought_signature`, `tool_id`, `responses_data`, `started_at`, `finished_at` | 思考过程 |
| `image_url` | `url`, `detail` | 图片引用 |
| `binary` | `path`, `mime_type`, `data` | 二进制附件（base64） |
| `tool_call` | `id`, `name`, `input`(JSON 字符串), `provider_executed`, `finished` | 工具调用 |
| `tool_result` | `tool_call_id`, `name`, `content`, `data`, `mime_type`, `metadata`, `is_error` | 工具结果 |
| `finish` | `reason`, `time`(秒), `message`, `details` | 结束标记 |
| `shell_command` | `command`, `output`, `exit_code` | bang 模式 shell 命令 |

- **role=user 消息**自动带 `finish{reason:"stop", time:0}`（CreateMessage 逻辑）
- **role=tool 消息**：parts 为 `tool_result`（可多个）
- **role=assistant 消息**：text / reasoning / tool_call / finish 任意组合，finish 的 reason：
  `end_turn` / `max_tokens` / `tool_use` / `canceled` / `error` / `content_filter` / `unknown`
- finish 的 `time` = finished_at（秒）

### 1.5 files / read_files 表

```sql
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,  -- 秒
    updated_at INTEGER NOT NULL,  -- 秒
    FOREIGN KEY (session_id) REFERENCES sessions (id) ON DELETE CASCADE,
    UNIQUE(path, session_id, version)
);
CREATE TABLE read_files (
    session_id TEXT NOT NULL CHECK (session_id != ''),
    path TEXT NOT NULL CHECK (path != ''),
    read_at INTEGER NOT NULL,  -- Unix timestamp in seconds
    PRIMARY KEY (path, session_id)
);
```

### 1.6 迁移历史（7 个）

| 迁移 | 内容 |
|------|------|
| 20250424200609_initial | 建 sessions/messages/files 表 + 触发器 |
| 20250515105448_add_summary_message_id | sessions 加 summary_message_id |
| 20250624000000_add_created_at_indexes | 加 created_at 索引 |
| 20250627000000_add_provider_to_messages | messages 加 provider |
| 20250810000000_add_is_summary_message | messages 加 is_summary_message |
| 20250812000000_add_todos_to_sessions | sessions 加 todos |
| 20260127000000_add_read_files_table | 建 read_files 表 |

## 2. 会话定位

### 2.1 项目注册表 projects.json

`%CRUSH_GLOBAL_DATA%/projects.json`（实测）：

```json
{
  "projects": [
    {
      "path": "C:\\Users\\alice",
      "data_dir": "C:\\Users\\alice\\.crush",
      "last_accessed": "2026-08-23T06:18:33.1729368Z"
    },
    {
      "path": "C:\\Users\\alice\\Desktop",
      "data_dir": "C:\\Users\\alice\\Desktop\\.crush",
      "last_accessed": "2026-08-20T03:33:09.3115294Z"
    }
  ]
}
```

按 last_accessed 降序。会话查找流程：
1. 若用户显式指定 data_dir（`--data-dir`）：只查该目录
2. 否则遍历 projects.json 所有 data_dir 的 crush.db
3. 每个库内按 session ID（UUID / 全 hash / hash 前缀）匹配

### 2.2 会话 ID 解析

`internal/cmd/session.go resolveSessionID`：UUID 直查 → 失败则对所有会话算 XXH3 hash
（`session.HashID` = xxh3 的 hex 字符串）匹配全 hash 或前缀；多匹配报歧义。
命令行展示用 hash 前 7 位。

### 2.3 运行中会话

- **无 PID 索引文件**（不像 Claude Code 的 `~/.claude/sessions/<pid>.json`）
- 判断运行中：`crush.db` 的 `datadirlock.go` 有锁（文件锁），但锁文件不常驻磁盘
- 实际定位运行中会话：最近的会话（sessions 按 updated_at 降序第一条）最可能是当前活跃会话

## 3. 屏幕快照格式（混合方案补充实时状态用）

### 3.1 TUI 整体布局（v0.90.0，实测 120x40）

```
╱╱╱╱╱╱ Charm™                    v0.90.0 ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
╱╱╱╱╱╱ ▄▀▀▀▀ █▀▀▀▀▀▀▀▀▄ █   █ ▄▀▀▀▀ █   █ ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
╱╱╱╱╱╱ █     █▀▀▀▀▀▀▀▀▄ █   █ ▀▀▀▀█ █▀▀▀█ ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
╱╱╱╱╱╱  ▀▀▀▀ ▀        ▀  ▀▀▀  ▀▀▀▀  ▀   ▀ ╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱╱
  ~\Desktop\crushparser            ← 欢迎页：工作目录
  ◇  via SenseNova                 ← 模型/提供商
  LSPs    MCPs    Skills           ← 三列（欢迎页）
  ...（技能列表）
  > Ready!                         ← 输入框（提示符 >）
  :::                              ← 输入框装饰
  tab focus chat • / or ctrl+p commands • ...  ← 底部状态栏
```

**对话中布局**（右侧边栏出现）：

```
[左侧：消息区（scrollback）]          [右侧边栏（~36 列）]
 │ hello                              ╱╱╱╱╱╱ Charm™ v0.90.0
   Hello! How can I help?             （logo）
   ◇  via SenseNova in 7s ────────    Untitled Session      ← 会话标题
 │ list files ...                     ~\Desktop\crushparser  ← 工作目录
   ✓ List .                           ◇  via SenseNova       ← 模型
      The directory tree ...          6% (16.5K) $0.00       ← 上下文% (token) $费用
   There's only one file:             Modified Files ──────
   •  sample_reply.txt                None
   ◇  via SenseNova in 34s ─────      LSPs ─────────────────
                                      None
                                      MCPs ─────────────────
 > Ready for instructions             None
 :::                                  Skills ───────────────
                                      ● agent-browser
 tab focus chat • ...                 ● bbdown
```

### 3.2 实时状态字段（SQLite 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 输入框行 `>` 后 / 消息区底部 | `> Working...` / `> Processing...` = 输出中；`> Ready!` / `> Ready for instructions` / `> Ready?` = 空闲；`Permission Required` 弹窗 + `Requesting permission...` = 等待批准 |
| 输入框文字 | `>` 后 | `>` 与 `:::` 之间的文本（占位符不算） |
| 上下文占用百分比 | 侧边栏 | `6% (16.5K) $0.00` 中第一个 `N%` |
| 上下文 token | 侧边栏 | `6% (16.5K)` 中括号内（K 单位） |
| 费用 | 侧边栏 | `$0.00` |
| 模型 | 侧边栏 | `◇  via SenseNova` 的 `via` 后 |
| 工作目录 | 侧边栏 | `~\Desktop\crushparser`（`~` = 用户主目录） |
| 会话标题 | 侧边栏 | `Untitled Session`（欢迎页无标题时为 `New Session`） |
| 版本号 | 侧边栏/顶部 | `v0.90.0` |

### 3.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `│` 开头行 | 用户消息（左侧竖线） |
| 无前缀正文 | AI 回复（markdown 渲染） |
| `◇  via <provider> in <Ns> ──` | 模型回复完成行（含耗时秒数） |
| `✓ <ToolName> <args...>` | 工具调用（成功，绿色勾） |
| `✗` / `!` 前缀 | 工具失败/错误 |
| `● <ToolName> ...` | 工具调用待处理/进行中 |
| `Job (Start) PID` | bash 后台任务 |
| `Permission Required` 弹窗 | 权限请求（等待批准），选项 Allow / Allow for Session / Deny |
| `Requesting permission...` | 权限请求中 |
| `esc cancel • ...` | 工作/处理中状态栏（区别于空闲的 `tab focus chat • ...`） |

### 3.4 屏幕快照获取

PTY-Agent 终端模式（snapshot 模式，**不带 `--full`**）：
```
python app.py exec crush -c 'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\alice\Desktop\CRUSH.ps1"' --size 120x40
python app.py send crush -i "<msg>" -t "Ready" --timeout 60 -o snap.txt
python app.py read crush -t "Ready" -o snap.txt
```

注意：
- Crush 是增量刷新 TUI，`--full` 会返回追加流导致 pyte 渲染重叠，须用 snapshot 模式
- 状态栏判定：`esc cancel` 开头 = 忙碌；`tab focus chat` 开头 = 空闲；`←/→ choose • enter confirm` = 权限弹窗
- 多尺寸调整：`--default terminal-size NxN` 对运行中会话即刻生效，然后 `read -o` 抓取

### 3.5 多尺寸布局差异（实测 40x10 / 60x15 / 80x24 / 120x40 / 200x50）

**Crush 在宽度 < 120 时切换为紧凑模式**（无右侧边栏）：

| 尺寸 | 布局 | 侧边栏 | cwd / 上下文% 位置 |
|------|------|--------|-------------------|
| 40x10 | 紧凑 | 无 | 头部行 `Charm™ CRUSH ╱╱╱ <cwd>…`（cwd 被终端截断） |
| 60x15 | 紧凑 | 无 | 头部行 `Charm™ CRUSH ╱╱╱ <cwd> • <pct>% • ...` |
| 80x24 | 紧凑 | 无 | 头部行同 60x15 |
| 120x40 | 宽屏 | 有（col ~88） | 侧边栏（标题/cwd/模型/上下文%/$费用） |
| 200x50 | 宽屏 | 有（col ~163） | 侧边栏 |

紧凑头部行格式：
```
   Charm™ CRUSH ╱╱╱ ~\Desktop\crushparser • 6% • ctrl+d o…
```
- `╱╱╱` 后到 ` • ` 前 = cwd
- ` • <N>% ` = 上下文占用百分比
- 输入框占位符紧凑模式为 `> Ready...`（宽屏为 `> Ready!` / `> Ready?` / `> Ready for instructions`）

解析器处理策略：
- **尺寸自动检测**（`src/infra/vt.py _detect_size`）：纯文本快照（无 CUP 定位序列）时，
  取最长可见行宽度为列数下限（200x50 需 ~194 列才能容纳侧边栏 col163），
  取行数为行数下限（200x50 有 49 行，默认 40 行会导致 pyte 行错位）
- **宽屏模式**：检测侧边栏起始列（锚定关键词 New Session / Untitled Session / Modified Files / LSPs / MCPs / Skills 的出现列），
  按列切片提取标题/cwd/模型/上下文
- **紧凑模式**：无侧边栏锚定时，从 `Charm™ CRUSH` 头部行解析 cwd 和上下文%

## 4. Agent 工具全集（26+，扫描源码 internal/agent/tools/）

| 工具 | 说明 | input 键 |
|------|------|----------|
| `ls` | 列出目录 | `depth`, `path`, `full` |
| `bash` | 执行 shell 命令 | `command`, `timeout`, `cwd`, `background` |
| `edit` | 精确编辑 | `path`, `old_text`, `new_text`, `replace_all` |
| `edit_whitespace` | 空白敏感编辑 | 同上 |
| `multiedit` | 多文件编辑 | `edits[]` |
| `write` | 写文件 | `path`, `content`, `create_only` |
| `view` | 查看文件 | `path`, `offset`, `limit` |
| `glob` | glob 匹配 | `pattern`, `path` |
| `grep` / `rg` | 搜索 | `query`, `path` |
| `search` | 语义搜索 | `query`, `path` |
| `fetch` | 抓取 URL | `url`, `max_length` |
| `web_fetch` / `web_search` | 网络 | `url` / `query` |
| `sourcegraph` | 代码搜索 | `query` |
| `question` | 提问用户 | `question`, `options` |
| `todos` | 待办管理 | `action`, `content` |
| `diagnostics` | LSP 诊断 | `path` |
| `crush_info` / `crush_logs` | 自身信息 | — |
| `job_kill` / `job_output` | 后台任务 | `pid` / `job_id` |
| `download` | 下载 | `url`, `path` |
| `lsp_*` | LSP 操作 | — |
| `mcp-tools` / `read_mcp_resource` | MCP | — |
| `references` | 符号引用 | — |

## 5. 已收集样本

| 文件 | 场景 |
|------|------|
| `sample_reply.txt` | 120x40 简单回复后（宽屏空闲态，`> Ready?`） |
| `sample_tool.txt` | 120x40 工具调用完成后（`✓ List .`，`> Ready for instructions`） |
| `sample_bash_done.txt` | 120x40 bash 权限请求弹窗（`Permission Required`，`Requesting permission...`） |
| `sz_40x10.txt` | 40x10 紧凑模式空闲态（cwd 被截断，`> Ready...`） |
| `sz_60x15.txt` | 60x15 紧凑模式空闲态（头部行含 cwd + 6% + WARN 行） |
| `sz_80x24.txt` | 80x24 紧凑模式空闲态（同 60x15） |
| `sz_200x50.txt` | 200x50 宽屏空闲态（侧边栏完整含标题） |
| 实时抓取 | 思考中（`> Working...` + `esc cancel`）、工具执行中（`> Processing...`） |

测试会话：PTY-Agent 会话 `crush_test`（120x40），启动命令为 CRUSH.ps1。
真实会话数据：`C:\Users\alice\.crush\crush.db`（会话 `716187c5-046d-46a4-b8ce-00a33067b620`，6 条消息）。

## 6. 解析器返回结构建议

```json
{
  "session": {
    "id": "716187c5-...", "parent_session_id": null, "title": "Untitled Session",
    "started_at": "2026-08-23T06:18:40+08:00", "updated_at": "...",
    "message_count": 6, "prompt_tokens": 17113, "completion_tokens": 809,
    "cost": 0.0, "summary_message_id": null, "todos": [],
    "data_dir": "C:\\Users\\alice\\.crush"
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running|awaiting_approval",
    "input_text": "", "context_percent": 6, "context_tokens": 16500,
    "cost_display": "$0.00", "model_display": "SenseNova",
    "title": "Untitled Session", "cwd_display": "~\\Desktop\\crushparser",
    "version_display": "v0.90.0", "screen_type": "main|conversation"
  },
  "messages": [
    {
      "id": "...", "role": "user|assistant|tool|system", "ts": 0, "ts_iso": "...",
      "model": "...", "provider": "...", "finished_at": null,
      "items": [
        { "type": "text", "text": "..." },
        { "type": "thinking", "text": "...", "signature": "..." },
        { "type": "tool_use", "tool_call_id": "...", "name": "ls",
          "input": { "depth": 2, "path": "..." }, "provider_executed": false,
          "finished": true },
        { "type": "tool_result", "tool_call_id": "...", "name": "ls",
          "success": true, "is_error": false, "error": null,
          "content": "...", "metadata": "...", "mime_type": "", "data": "" },
        { "type": "finish", "reason": "end_turn", "time": 0 }
      ]
    }
  ]
}
```

## 7. 与其它 parser 的差异

| 维度 | clineparser | claudeparser | crushparser |
|------|-------------|--------------|-------------|
| 存储 | 双 JSON | JSONL 追加 | **SQLite 单库**（sessions/messages/files） |
| 会话定位 | session_id 直查目录 | sessions/<pid>.json + projects 扫描 | **projects.json 注册表 + 多库遍历** |
| 数据隔离 | 单机单目录 | 单机单目录 | **按项目隔离**（每项目一个 .crush/crush.db） |
| 时间戳 | 毫秒 int | ISO 字符串 | **秒 int**（注释误导为毫秒） |
| 消息聚合 | 单消息单角色 | parentUuid 链 | **parts 数组内嵌 finish**（无独立轮次链） |
| tool_result | list/str 双形态 | 字符串 + toolUseResult | **独立 tool 角色消息** + tool_call/tool_result parts |
| 会话 ID | `1787394905536_gt0qi` | UUID | **UUID / XXH3 hash 前缀** |
| 实时状态 | 屏幕底部状态栏 | 屏幕状态栏 | **右侧边栏**（上下文%/token/$费用/模型/标题/cwd） |
| 运行会话索引 | 无 | sessions/<pid>.json | **无 PID 索引**（最近会话即活跃会话） |
