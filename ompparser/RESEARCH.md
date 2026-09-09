# omp（oh-my-pi）解析器调研报告（ompparser）

## 0. 核心结论

**omp（oh-my-pi，can1357/oh-my-pi，当前 v17.4.2）在本地存储完整结构化对话历史（JSONL 追加日志 + 树结构）**，解析器采用**纯 JSONL 方案**：
- **JSONL 会话文件**（主源）：`<agentDir>/sessions/<encoded-cwd>/<timestamp>_<uuid>.jsonl`
  — 完整消息 + 工具调用 + 思考 + 会话元数据 + 分支/压缩条目
- **agent.db / history.db**（辅助）：`<agentDir>/` 下 — auth/settings/usage（无会话数据，session 历史索引）
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/模型/上下文百分比/思考等级，JSONL 中缺失的即时状态）

**启动命令**：`omp-windows.exe`（npm 包 `@oh-my-pi/pi-coding-agent`，当前 v17.4.2）
- 本机启动脚本：`OMP.ps1`（**必须用此启动**）
  - 设置 `PI_CODING_AGENT_DIR=%USERPROFILE%\__omp\agent`
  - 写入 `models.yml`（SenseNova OpenAI-compatible endpoint：`https://your-api-endpoint.com/v1`）
  - 执行 `omp-windows.exe --cwd (Get-Location).Path --model "sensenova-6.8-flash-lite" @args`
- 当前版本：17.4.2
- 交互模式：`omp`（默认 TUI，alt-screen 布局，编辑器 + 状态栏）

数据目录（受 `PI_CODING_AGENT_DIR` 环境变量控制，本机为 `__omp\agent`）：
- `<agentDir>/sessions/` — 会话 JSONL（按 cwd 编码目录组织）
- `<agentDir>/terminal-sessions/` — 终端面包屑（terminalId → 最后 session 文件）
- `<agentDir>/agent.db` — 认证/设置/模型用量 SQLite
- `<agentDir>/history.db` — 提示历史 SQLite（带 FTS 全文索引）
- `<agentDir>/models.yml` — 自定义 provider/model 定义
- `<agentDir>/models.db` — 模型缓存 SQLite
- `<agentDir>/config.yml` — 设置（theme/thinkingLevel/symbolPreset/composer 等）

cwd 编码规则（与 omp 的 session-paths.ts 一致）：
- home 下：`-<home-relative>`，如 `C:\Users\<username>\Desktop\ompparser` → `-Desktop-ompparser`；
  cwd == home 时为 `-`（单个横线）
- temp 下：`-tmp-<relative>`，如 `%TEMP%\foo` → `-tmp-foo`
- home/temp 外（绝对路径）：`--<encoded-abs>--`，如 `D:\proj` → `--D--proj--`

与 Claude Code 的 cwd 编码规则**不同**（Claude Code 用 `C--Users-<username>` 所有分隔符替换为 `-`，
omp 使用 home-relative 加 `-` 前缀）。

## 1. 会话文件格式（`<timestamp>_<uuid>.jsonl`）

### 1.1 文件命名与定位

- 会话目录：`<agentDir>/sessions/<encoded-cwd>/`
- 文件名：`<fileSafeTimestamp>_<uuid>.jsonl`
  - fileSafeTimestamp = ISO 时间戳的 `:` 与 `.` 替换为 `-`
  - 例：`2026-08-23T07-39-40-880Z_01a02d8f-99d0-7000-b705-a7da2caea32b.jsonl`
- 会话 ID = header 中的 `id` 字段（UUID，与文件名中的 uuid 一致）
- 会话定位：遍历 `sessions/` 下全部 `<encoded-cwd>/` 目录，读取每个文件的 header
- **无 SQLite 索引**（默认 JSONL 后端），**无 pid 锁文件**（与 Devin/WorkBuddy 不同）

### 1.2 首行：title slot（固定宽度可变槽位）

```json
{"type":"title","v":1,"title":"查看桌面内容","source":"auto","updatedAt":"2026-08-23T06:21:15.809Z","pad":"..."}
```

- 不参与树结构（无 id/parentId）
- `pad` 字段填充至固定宽度（256 字节），支持原地重写更新标题
- `source`：`auto`（AI 生成）或 `user`（用户设置）

### 1.3 Header（第二行）

```json
{"type":"session","version":3,"id":"01a02d8f-99d0-7000-b705-a7da2caea32b","timestamp":"2026-08-23T07:39:40.880Z","cwd":"C:\\Users\\<username>\\Desktop\\ompparser"}
```

- 有 parent（/fork /clone 创建）：额外 `"parentSession":"/path/to/original/session.jsonl"`
- 有 `providerPromptCacheKey`：prompt-cache 继承标识

### 1.4 Entry 基础结构

除 header 和 title slot 外所有条目都有：

```json
{"type":"<entry-type>","id":"8-char-hex","parentId":"<parent-id>|null","timestamp":"ISO 字符串","..."}
```

- 首条 entry 的 `parentId: null`
- 后续 entry 通过 `parentId` 指向前驱，形成**树结构**（支持 `/tree` 分支）

### 1.5 Entry 类型全集

| type | 说明 | 关键字段 |
|------|------|---------|
| `message` | 对话消息（主） | `message`: AgentMessage（role/user/assistant/toolResult） |
| `model_change` | 切换模型 | `model`: "provider/modelId"（与 pi 的 provider+modelId 不同） |
| `thinking_level_change` | 切换思考等级 | `thinkingLevel`, `configured`（auto 模式时） |
| `title_change` | 标题变更审计 | `title`, `source`(auto/user), `previousTitle?`, `trigger?` |
| `compaction` | 上下文压缩 | `summary`, `tokensBefore`, `firstKeptEntryId`, `details?`, `preserveData?` |
| `branch_summary` | 分支摘要 | `fromId`, `summary`, `details?` |
| `custom` | 扩展状态（不入上下文） | `customType`, `data` |
| `custom_message` | 扩展注入消息（入上下文） | `customType`, `content`, `display`, `details?` |
| `label` | 书签 | `targetId`, `label` |
| `ttsr_injection` | TTSR 规则注入 | `injectedRules` |
| `session_init` | 子代理初始化 | `systemPrompt`, `task`, `tools`, `agent`, `modelRole`, `outputSchema` |
| `mode_change` | 模式切换 | `mode`, `data` |
| `credential_pin` | OAuth 账户固定 | `provider`, `hash` |
| `reset_boundary` | 上下文重置边界 | 无 payload |
| `service_tier_change` | 服务层切换 | `serviceTier` |

## 2. AgentMessage 结构（message entry 的 message 字段）

### 2.1 Content Block 类型

```typescript
TextContent:     { type: "text", text: string }
ImageContent:    { type: "image", data: string /*base64*/, mimeType: string }
ThinkingContent: { type: "thinking", thinking: string, thinkingSignature?: string /*"reasoning"*/ }
ToolCall:        { type: "toolCall", id: string, name: string, arguments: Record<string, any>,
                   partialArgs?: string, streamIndex?: number, intent?: string }
```

### 2.2 消息角色

| role | content | 关键字段 | 说明 |
|------|---------|---------|------|
| `user` | string 或 (text\|image)[] | `attribution`(user/system), `timestamp`(ms) | 用户输入 |
| `assistant` | (text\|thinking\|toolCall\|image)[] | `api`, `provider`, `model`, `usage`, `stopReason`, `timestamp`(ms), `responseId`, `duration`, `ttft`, `contextSnapshot` | 助手回复 |
| `toolResult` | (text\|image)[] | `toolCallId`, `toolName`, `isError`, `timestamp`(ms), `details` | 工具结果 |

### 2.3 Usage 结构

```json
"usage": {
  "input": 2504, "output": 253, "cacheRead": 0, "cacheWrite": 0,
  "reasoning": 0, "totalTokens": 2757,
  "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0 }
}
```

### 2.4 真实样本（实测 2026-08-23）

```json
{"type":"message","id":"d4bfe326","parentId":"2049610c","timestamp":"2026-08-23T07:40:30.299Z",
 "message":{"role":"assistant",
   "content":[
     {"type":"thinking","thinking":"User just said \"hi\". Let me look at the repo...\n","thinkingSignature":"reasoning"},
     {"type":"text","text":"\n\n"},
     {"type":"toolCall","id":"call_d44d3b8231714876bc919437","name":"read",
      "arguments":{"path":"C:/Users/<username>/Desktop/ompparser","i":"List repo contents"},
      "partialArgs":"{\"path\": \"C:/Users/<username>/Desktop/ompparser\", \"i\": \"List repo contents\"}",
      "streamIndex":0,"intent":"List repo contents"}
   ],
   "api":"openai-completions","provider":"litellm","model":"sensenova-6.8-flash-lite",
   "usage":{"input":2504,"output":46,"cacheRead":0,"cacheWrite":0,"reasoning":0,"totalTokens":2550,
            "cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},
   "stopReason":"toolUse","timestamp":1787470826916,
   "responseId":"ce69f130-bcdb-40fa-b6c9-80beb3a79f2a","duration":7622.9,"ttft":6455.35,
   "contextSnapshot":{"promptTokens":2504,"nonMessageTokens":14425,"compactionEpoch":0}}}
```

```json
{"type":"message","id":"f6bd3a6e","parentId":"5cd6dac3","timestamp":"2026-08-23T07:40:30.346Z",
 "message":{"role":"toolResult","toolCallId":"call_d44d3b8231714876bc919437","toolName":"read",
   "content":[{"type":"text","text":"."}],
   "details":{"isDirectory":true,"resolvedPath":"C:\\Users\\<username>\\Desktop\\ompparser",
              "meta":{"source":{"type":"path","value":"C:\\Users\\<username>\\Desktop\\ompparser"}}},
   "isError":false,"timestamp":1787470830343}}
```

**要点**：
- 时间戳双形态：entry 层为 ISO 字符串，message 内部为 Unix 毫秒 int（优先使用）
- toolCall 的 `arguments` 是**对象**（不是 JSON 字符串），`partialArgs` 为流式原始 JSON 字符串
- toolResult 有 `details` 字段（含工具执行的元数据，如 `isDirectory`, `resolvedPath`）
- stopReason 取值：`stop` / `toolUse` / `length` / `error` / `aborted`
- `contextSnapshot`：记录压缩 epoch 和 prompt tokens，用于 prompt-cache 继承

## 3. 会话树与上下文构建

- entry 树通过 `id`/`parentId` 链接，leaf 为当前会话位置
- 子代理存储：`<sessionDir>/<SubId>.jsonl`（agent 会话文件嵌套在父 dir 中）
- advisor 转录：`__advisor.jsonl`（观察性数据，不入 LLM 上下文）

## 4. 屏幕快照格式（实时状态补充，实测 v17.4.2）

### 4.1 omp TUI 布局（unicode 符号预设，实测）

欢迎页（main）：
```
╭─── omp v17.4.2 ───────────────────────────────────────────────────╮
│                          │ Tips                                   │
│      Welcome back!       │ # for prompt actions                   │
│       ▀██████████▀       │ / for commands                         │
│        ╘██    ██         │ ! to run bash                          │
│         ██    ██         │ $ to run python                        │
│         ██    ██         │ ────────────────────────────────────── │
│        ▄██▄  ▄██▄        │ LSP Servers                           │
│ Sensenova 6.8 flash lite │ No LSP servers                        │
│         litellm          │ ────────────────────────────────────── │
│                          │ Recent sessions                        │
│                          │ • hi (3m ago)                          │
╰──────────────────────────┴────────────────────────────────────────╯
 Tip: Drop the word `ultrathink` in your message...

────────────────────────────────────────────────────────────────────
 Update Available
 New version 18.0.1 is available. Run: omp update
────────────────────────────────────────────────────────────────────

╭── π  > ⬢ Sensenova 6.8 flash lite · ◒ high > 📁 ~/Desktop/ompparser > ◫ 5.8%/262K ⟲ ▶──...─╮
╰─                                                                                            ─╯
```

对话中（conversation）：
```
 （thinking）
 User just said "hi"...

 ● Read C:/Users/<username>/Desktop/ompparser

  Inspecting content

 hi, <username>。这个仓库看起来几乎是空的...

╭── π  > ⬢ Sensenova 6.8 flash lite · ◒ high > 📁 ~/Desktop/ompparser > ◫ 7.7%/262K ⟲ ▶──...─╮
╰─                                                                                            ─╯
```

### 4.2 实时状态字段（屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区 | `⠋/⠙/⠹ Working… ⟦esc⟧` = working；`● ToolName` + `$ cmd` 框 = tool_running；无以上 = idle |
| 输入框文字 | 编辑器底边框 | `╰─ <text> ─╯` 中的 `<text>` |
| 模型名 | 状态栏（顶边框） | `⬢ Sensenova 6.8 flash lite` 中 `⬢` 后文本 |
| 思考等级 | 状态栏（顶边框） | `· ◒ high` 中 `◒` 后的等级：`high`/`xhigh`/`medium`/`low`/`max`/`minimal`/`off` |
| 工作目录 | 状态栏（顶边框） | `📁 ~/Desktop/ompparser` 中 `📁` 后文本 |
| 界面类型 | 顶框 | `╭─── omp v17.4.2` box = main；纯消息滚动 = conversation |
| 上下文百分比 | 状态栏 | `◫ 5.8%/262K` 中 `%` 前数字 |
| 上下文窗口 | 状态栏 | `◫ 5.8%/262K` 中 `K` 前数字（如 262K = 262144） |

### 4.3 视觉特征（屏幕解析参考）

| 特征 | 类型 |
|------|------|
| `╭─── omp v17.4.2` | 欢迎页大框（main 判定） |
| `Welcome back!` | 欢迎页左栏标题 |
| `╭── π  > ⬢ Model · ◒ level > 📁 path > ◫ N%/NNN ⟲ ▶──╮` | 编辑器顶边框（状态栏） |
| `╰─ input text ─╯` | 编辑器底边框（输入框） |
| `⠋/⠙/⠹/⠸/⠼/⠴/⠦/⠧/⠇/⠏ Working… ⟦esc⟧` | 工作中 spinner |
| `● ToolName(args)` | 工具执行状态行 |
| `ⓘ ToolName` | 工具信息行 |
| `$ cmd` | 工具命令（框内） |
| `╭─── ... ╮│ $ cmd ├─── Output ───┤ │ output │ ⟦Wall: 20.02s ...⟧ │ ╰───╯` | 工具执行框 |
| `⟦Ctrl+O: Expand⟧` | 折叠提示 |
| `\x1b[3m thinking`（斜体） | thinking 文本 |
| `⬢` | 模型图标 |
| `📁` | 路径图标 |
| `◫` | 上下文图标 |
| `⟲` | 自动压缩图标 |
| `▶` | powerline 端帽 |
| `π` | pi 图标 |

## 5. 会话元数据来源

| 字段 | 来源 |
|------|------|
| id | header.id（UUID） |
| cwd | header.cwd（反斜杠 Windows 路径） |
| started_at | header.timestamp（ISO） |
| model | 最新 `model_change` 的 `model`（"provider/modelId"） |
| model_provider | `model_change` 的 `model` 中 "/" 前部分 |
| thinking_level | 最新 `thinking_level_change` 的 `thinkingLevel` |
| title | 首行 `title` slot 或最新 `title_change` |
| usage | 全部 assistant/toolResult message 的 usage 聚合 |

## 6. 与 piparser（Pi）的差异

omp 是 Pi（@earendil-works/pi-coding-agent）的 fork，两者 JSONL 格式高度相似但有差异：

| 维度 | **ompparser (omp v17.4.2)** | piparser (Pi v0.84.2) |
|------|------|------|
| 存储 | `<ts>_<uuid>.jsonl`（JSONL 树） | `<ts>_<uuid>.jsonl`（JSONL 树） |
| 会话 ID | UUID | UUID |
| 会话定位 | 遍历 sessions 目录读 header | 遍历 sessions 目录读 header |
| cwd 编码 | `-Desktop-ompparser`（home-relative） | `--C--Users-<username>-Desktop-ompparser--`（绝对路径） |
| 首行 | `title` slot（固定宽度可变标题） | `session` header（直接开始） |
| 时间戳 | entry ISO + message ms | entry ISO + message ms |
| model_change | `model: "provider/modelId"`（组合） | `provider` + `modelId`（分开） |
| thinking_level_change | `thinkingLevel` + `configured` | `thinkingLevel` |
| title 变更 | `title_change` entry（追加审计，含 source/previousTitle） | `session_info` entry（仅 name） |
| user attribution | `attribution` 字段（user/system） | 无 |
| toolCall | `arguments` + `partialArgs` + `streamIndex` + `intent` | `arguments` 仅对象 |
| toolResult details | `details` 字段（isDirectory/resolvedPath 等） | 无 |
| assistant extra | `duration`, `ttft`, `contextSnapshot` | 无 |
| 子代理 | `agent-<hash>.jsonl` | 无 |
| advisor | `__advisor.jsonl` | 无 |
| TUI 状态栏 | 编辑器顶边框（`╭── π  > ⬢ Model · ◒ level > 📁 path > ◫ N%/NNN ⟲ ▶`） | 双行 footer（cwd 行 + stats 行） |
| TUI 输入框 | 编辑器底边框（`╰─ input ─╯`） | 反色块行（`▌`） |
| TUI 欢迎页 | 双列框（logo + welcome + Tips/LSP/Recent sessions） | 单列（`pi vN.N.N` + `[Context]` + 空输入框） |
| 版本号 | 17.4.2 | 0.84.2 |
| 启动方式 | `OMP.ps1` → `omp-windows.exe` | `PI.ps1` → `pi.exe` |

## 7. 辅助数据库

### 7.1 agent.db

agent.db 是 omp 的 SQLite 数据库，用于存储认证凭据、设置、模型用量等，**不存会话数据**。

主要表：`auth_credentials`, `cache`, `usage_history`, `clients`, `client_usage`, `model_perf`, `settings`, `meta`, `schema_version`, `model_usage`

### 7.2 history.db

history.db 存储提示历史（FTS 全文索引）：

```sql
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    cwd TEXT,
    session_id TEXT
);
```

### 7.3 models.db

models.db 缓存远程模型列表（provider/model catalog）。

## 8. 已知注意事项

- **JSONL 树结构**：同一文件内可有多分支，`parentId` 决定父子；解析时若只关心主对话链，按文件顺序输出即可（omp 追加写入保持顺序）
- **title slot 首行**：固定宽度 256 字节，`pad` 字段填充，原地重写更新标题；解析器需跳过 title slot 找 header
- **message 内 timestamp 与 entry timestamp 不一致**：entry 为写入时间 ISO，message 内为毫秒 int（通常更准确，差几 ms 到几秒）
- **toolCall.arguments 是对象**，非 JSON 字符串（与 Codex 的 `raw_arguments` 不同）；`partialArgs` 为流式原始 JSON 字符串
- **model 双重来源**：`model_change` entry 记录用户选择的模型，assistant message 记录实际使用的模型；`model_change` 的 model 为 "provider/modelId" 组合格式
- **无 SQLite 会话索引**：会话发现需遍历目录读 header（每文件读前几行，跳过 title slot）
- **无运行锁**：运行中判定需进程检测（omp-windows.exe 进程 + 最近 mtime）或 PTY-Agent 已有会话
- **cwd 编码**：home-relative `-Desktop-ompparser`，与 Pi 的 `--C--Users-...--` 不同；注意 Windows 大小写与分隔符
- **屏幕快照**：omp 使用 alt-screen 全屏刷新，状态栏在编辑器顶边框（与 Pi 双行 footer 不同）；输入框在底边框（`╰─ text ─╯`）
- **版本**：header 有 `version: 3`（v1 线性 / v2 树 / v3 hookMessage→custom 重命名）
- **子代理**：`agent-<hash>.jsonl` 文件在父 session 目录下，不应作为独立会话列出
- **terminal-sessions** 面包屑：`<agentDir>/terminal-sessions/<terminalId>` 文件，内容为 `cwd\nsessionFile\n[fresh]`，用于 `--continue` 自动恢复