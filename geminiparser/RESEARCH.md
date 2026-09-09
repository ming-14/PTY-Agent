# Gemini CLI 解析器调研报告（geminiparser）

## 0. 核心结论

**Gemini CLI（google-gemini/gemini-cli，npm 包 @google/gemini-cli）在本地存储完整结构化对话历史（JSONL 追加日志）**，解析器采用**混合方案**：

- **JSONL 会话文件**（主源）：`~/.gemini/tmp/<project>/chats/session-<ts>-<uuid8>.jsonl`
  — 完整消息 + 思考 + 工具调用 + 工具结果 + 会话元数据
- **用户输入日志**（辅助）：`~/.gemini/tmp/<project>/logs.json` — 纯用户输入列表（无回复）
- **屏幕快照**（补充）：PTY-Agent 终端模式 → 实时状态
  （AI 状态/输入框/模型/上下文百分比，JSONL 中缺少的即时状态）

本地存储位置（`~/.gemini/`，即 `%USERPROFILE%\.gemini`）：
- `tmp/<project>/chats/session-<ts>-<uuid8>.jsonl` — 主会话消息历史（追加式 JSONL）
- `tmp/<project>/chats/<main-uuid>/<subagent-uuid>.jsonl` — 子代理会话（kind=subagent）
- `tmp/<project>/<session-uuid>/plans/*.md` — 计划文件（plan mode 产物）
- `tmp/<project>/logs.json` — 用户输入日志（数组）
- `tmp/<project>/.project_root` — 项目工作区根路径（纯文本）
- `tmp/background-processes/background-<pid>.log` — 后台进程输出
- `history/<project>/.project_root` — 历史项目根（历史查询用）
- `settings.json` — 设置（model / security.auth / ui / modelConfigs）
- `projects.json` — 工作区目录 → 项目名映射
- `state.json` — UI 状态（tips / 启动警告计数）
- `.env` — API 配置（GOOGLE_GEMINI_BASE_URL / GEMINI_API_KEY / GEMINI_MODEL）
- `google_accounts.json` / `installation_id` / `trustedFolders.json` — 认证与信任

启动命令：`gemini`（npm 全局安装，bin → `node_modules\@google\gemini-cli\bundle\gemini.js`）
- 实测版本：**v0.56.0**（2026-08-23）
- 源码仓库：`C:\Users\<username>\Desktop\gemini-cli-main`（packages/cli/src）
- 交互模式：`gemini`（默认 TUI）
- 非交互：`gemini -p "prompt"`（实测子进程模式无 TTY 时 exit 42）

## 1. 会话文件命名与定位

```
tmp/<project>/chats/session-2026-08-22T19-26-00000000.jsonl
                          │        │         │
                          │        │         └─ sessionId UUID 前 8 位
                          │        └─ 启动时间（冒号→-）
                          └─ 固定前缀 session-
```

- **文件名最后一段 = sessionId 的 UUID 前 8 位**（实测 18 个文件全部匹配）
- 完整 sessionId 在文件首行 JSON 的 `sessionId` 字段
- `<project>` 目录名：`projects.json` 中 `{cwd → 项目名}` 映射（如 `c:\users\<username>\desktop\a` → `a`）；与 cline/opencode 不同，**没有目录编码**（Claude 是 `C--Users-<username>` 风格）
- 会话定位：遍历 `tmp/*/chats/session-*.jsonl`，读首行拿 sessionId
- 子代理会话：位于 `<main-session>/<subagent-uuid>.jsonl`（`chats/<主会话uuid>/<子代理uuid>.jsonl`），首行 `kind: "subagent"` + `directories` 字段；主会话 invoke_agent 的 toolCall 带 `agentId` 与之对应

## 2. JSONL 事件结构（核心）

每行一个 JSON 事件，两类：**状态更新（$set）** 与 **消息/事件**。

### 2.1 首行：会话元数据

```json
{"sessionId": "00000000-0000-0000-0000-000000000001",
 "projectHash": "0c85cb7d...", "startTime": "2026-08-22T19:26:21.900Z",
 "lastUpdated": "2026-08-22T19:26:21.900Z", "kind": "main"}
```

- `kind`：`main`（主会话）/ `subagent`（子代理，额外有 `directories` 数组）

### 2.2 `$set` 状态更新

| 键 | 次数占比 | 说明 |
|----|---------|------|
| `lastUpdated` | 多数 | 时间戳心跳，无解析价值 |
| `messages` | 少量 | **完整消息数组物化快照**（初始 1 条 = session_context；会话中途会定期写入 15/54/106 条等完整快照，可作一致性校验/兜底源） |
| `memoryScratchpad` | 少量 | 工作流记忆：`{version, workflowSummary, toolSequence[], touchedPaths[]}` |
| `summary` | 极少 | 会话摘要文本 |

`$set.messages` 快照中的消息结构与追加事件完全一致（见下）。

### 2.3 消息事件

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 消息 UUID（**同 id 可能重复出现**，见去重规则） |
| `timestamp` | ISO string | 时间戳（毫秒精度，带时区） |
| `type` | enum | `user` / `gemini` / `info` / `error` |
| `content` | 混合 | user: `[{text}]` 或 `[{functionResponse}]`；gemini: 字符串 |
| `thoughts` | array | gemini 思考流：`[{subject, description, timestamp}]`（分片） |
| `tokens` | object/null | `{input, output, cached, thoughts, tool, total}`（多数为 null） |
| `model` | string | 模型名 |
| `toolCalls` | array | gemini 工具调用数组（见 2.5） |
| `displayContent` | string | 用户消息引用文件时注入的文件内容（`@path` 展开） |

**type 取值语义**：
- `user`：用户输入（`content[].text`）或工具结果回传（`content[].functionResponse`）
- `gemini`：助手消息（content 为字符串；带 toolCalls 时含工具调用+结果）
- `info`：提示事件（如 `"Request cancelled."`、token 超限警告、`"Response truncated"`）
- `error`：API 错误（如 `"[API Error: exception TypeError: fetch failed sending request]"`）

### 2.4 关键：同 id 消息去重

**同一消息 id 会先出现「纯文本版」，后出现「带 toolCalls 完整版」**（流式期间先写 content，完成后补 toolCalls 重写）。实测 235 条 gemini 消息中 106 条带 toolCalls，均有重复。解析时**按 id 去重，保留最后出现版本**。

### 2.5 toolCalls 结构（工具调用+结果同体）

```json
{"id": "list_directory__list_directory_1787427129800_0",
 "name": "list_directory",
 "args": {"dir_path": "C:\\Users\\<username>\\Desktop\\a"},
 "result": [{"functionResponse": {"id": "...", "name": "list_directory",
    "response": {"output": "Directory ... is empty."}}}],
 "status": "success",            // success / error / cancelled
 "timestamp": "...",
 "resultDisplay": "...",         // 展示文本（error 时为错误信息）
 "description": "...",
 "displayName": "ReadFolder",    // 工具显示名
 "renderOutputAsMarkdown": true}
```

- `response.output` 成功文本；`response.error` 失败原因
- `status`：`success` / `error` / `cancelled`（用户取消）
- `resultDisplay`：文件 diff 时为对象（`{fileDiff, fileName, filePath, diffStat...}`），文本时为字符串

**工具结果同步出现在一条 user 消息中**：gemini 消息带 toolCalls 后，紧跟一条 `type: "user"` 消息，`content` 为 `[{functionResponse: {...}}, ...]`（同 id 的 functionResponse 列表），即回传给模型的工具结果。解析时两者二选一即可（**推荐从 user 消息的 functionResponse 提取工具结果**，或从 toolCalls 内嵌 result 提取）。

### 2.6 实测工具清单（17 种）

`update_topic / google_web_search / list_directory / list_background_processes / web_fetch / glob / run_shell_command / write_file / read_file / grep_search / replace / ask_user / activate_skill / invoke_agent / enter_plan_mode / exit_plan_mode / read_background_output`

- `ask_user`：`args.questions[]`，成功结果 `response.output = '{"answers":{"0":"选项 A"}}'`
- `invoke_agent`：成功结果含 `agentId` + `resultDisplay.isSubagentProgress`（子代理活动日志）；终止原因 `GOAL` / `MAX_TURNS` / `ERROR_NO_COMPLETE_TASK_CALL`
- `enter_plan_mode` / `exit_plan_mode`：计划文件名 + 计划存储路径（`tmp/<project>/<session-uuid>/plans/`）
- 子代理内部工具：`get_internal_docs` / `complete_task` 等（不在主会话 toolCalls 中，需读子代理会话文件）

### 2.7 tokens 结构

```json
"tokens": {"input": 10168, "output": 41, "cached": 0, "thoughts": 0, "tool": 0, "total": 10209}
```

实测多数消息 tokens 为 null（模型未返回），聚合时需容错。字段语义与 opencode 的 `{input, output, reasoning, cache}` 不完全一致：gemini 用 `cached / thoughts / tool / total`。

## 3. 会话元数据来源

| 字段 | 来源 |
|------|------|
| id | 首行 `sessionId` |
| started_at | 首行 `startTime` |
| cwd | `tmp/<project>/.project_root`（工作区根） |
| title | 无显式字段；`$set.summary` 或首条真实用户消息文本 |
| model | 各消息 `model` 字段（取首个非空） |
| usage | 消息 tokens 聚合（多数为 null → 可能全 0） |
| status | 无显式字段；由最后事件推断（error 事件 → failed；info cancelled → 中断） |

## 4. 屏幕快照解析（实时状态）

### 4.1 TUI 布局（实测 v0.56.0，120x40 / 200x50）

```
 ▝▜▄     Gemini CLI v0.56.0                    ← 顶部 logo + 版本（含认证状态行）
   ▝▜▄
  ▗▟▀    Authenticated with gemini-api-key /auth
[消息区（含 scrollback）]
 ⠹ Thinking... (esc to cancel, 1m 12s)          ← AI 状态行（spinner + 计时）
 ✕ [API Error: ...]                             ← 错误行
                                                                ? for shortcuts  ← 右上角
──────────────────────────────────────────────  ← 分隔线（─ 连续）
 Shift+Tab to accept edits                       ← 工具编辑提示（固定行）
──────────────────────────────────────────────
 >   Type your message or @path/to/file          ← 输入框（提示符 > + placeholder）
──────────────────────────────────────────────
 workspace (/directory)  sandbox       /model    ← 状态栏（左：三个标签）
 ~\Desktop\a             no sandbox    sensenova-6.8-flash-lite   ✖ 1 error (F12)  ← 值行
```

### 4.2 状态识别要点

| 状态 | 特征 |
|------|------|
| idle（欢迎页） | `▝▜▄ Gemini CLI v0.56.0` logo + `Authenticated with ...` + `Type your message or @path/to/file`，无消息 |
| idle（对话中） | 消息区有历史对话 + 输入框 `> Type your message` + 状态栏 |
| thinking | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` 旋转动画 + `Thinking... (esc to cancel, Ns)`（`(esc to cancel)` 是进行中标记；时间显示 `7s` / `1m 12s` / `5m 23s`） |
| tool_running | 工具执行行/进度条 + `esc to interrupt` 类提示 |
| awaiting_answer | `╭─ Answer Questions ─╮` 对话框 + `Enter to select · ↑/↓ to navigate · Esc to cancel` |
| awaiting_approval | `╭─ ? Shell/WriteFile/Edit ... ─╮` 对话框 + `Allow once` / `Allow for this session` 选项 |
| error | `✕ [API Error: ...]` 行 + 状态栏右侧 `✖ N error (F12 for details)` |
| subagent_running | `╭─ ≡ Running Agent... (ctrl+o to expand) ─╮` + `! agent名 · 工具名 描述` |
| subagent_completed | `╭─ ≡ Agent Completed (ctrl+o to expand) ─╮` + `✓ agent名 · 💭 Finished Early (TIMEOUT/GOAL)` |
| model_dialog | `╭─ Select Model ─╮` + `Auto` / `Manual` 选项 + `Remember model: false (Tab to toggle)` |
| reverse_search | 输入框 `(r:)` 前缀 + 历史列表 + `(1/47)` 计数 |
| plan_mode | 分隔线旁 `plan Shift+Tab to manual` 标记 |
| auto_accept | 分隔线旁 `auto-accept edits Shift+Tab to manual` 标记 |

### 4.3 对话框类型详解

| 类型 | 标题 | 选项 | 底部提示 |
|------|------|------|---------|
| **Ask User** | `Answer Questions` | `● N. label` + description | `Enter to select · ↑/↓ to navigate · Esc to cancel` |
| **权限确认（Shell）** | `? Shell <command>` | `1. Allow once` / `2. Allow for this session` / `3. No, suggest changes (esc)` | — |
| **权限确认（WriteFile）** | `? WriteFile <path>` | `1. Allow once` / `2. Allow for session` / `3. Modify with external editor` / `4. No, suggest changes (esc)` | — |
| **权限确认（Edit）** | `? Edit <path>: <summary>` | 同上 4 选项 | — |
| **计划确认** | `Ready to start implementation?` | `1. Yes, automatically accept edits` / `2. Yes, manually accept edits` / `3. Type your feedback...` | `Enter to select · ↑/↓ to navigate · Ctrl+G to edit plan · Esc to cancel` |
| **模型选择** | `Select Model` | `1. Auto` / `2. Manual (model name)` | `Remember model for future sessions: false (Press Tab to toggle)` + `(Press Esc to close)` |

### 4.4 工具批准/结果展示形态

- **WriteFile 批准前**：`? WriteFile <path>` + 内容预览框（行号+文本）
- **WriteFile 批准后**：`✓ WriteFile <path> → Accepted (+N, -0)` + 行号+内容
- **Edit 批准前**：`? Edit <path>: <summary>` + diff 预览（`N - 旧行` / `N + 新行`）
- **Edit 批准后**：`✓ Edit <path> → Accepted (+N, -M)` + diff 行
- **Shell 批准前**：`? Shell <command>` + 命令预览框（嵌套边框 `╭─╮`）
- **ReadFile 结果**：`✓ ReadFile <path>` + 内容行
- **Ask User 结果**：`✓ Ask User` + `User answered:` + `问题 → 答案`
- **Enter Plan Mode 结果**：`✓ Enter Plan Mode <description>`
- **Exit Plan Mode 结果**：`✓ Exit Plan Mode Requesting plan approval for: <path>` + `Plan approved: <path>`

### 4.5 输入与导航

- 输入框：`> ` 前缀 + placeholder `Type your message or @path/to/file`
- **多行输入**：`> 第一行` / `   第二行`（续行缩进 4 空格）
- **反向搜索**：`(r:)` 前缀 + 历史列表（`(1/47)` 计数）
- **提交**：单 Enter | **换行**：Shift+Enter / Ctrl+Enter / Alt+Enter / Ctrl+J
- **清行**：Ctrl+U
- **右上角**：`? for shortcuts`（始终可见）

### 4.6 状态栏

三标签固定布局：
```
workspace (/directory)  sandbox  /model
~\Desktop\a             no sandbox  sensenova-6.8-flash-lite  ✖ N error (F12)
```

### 4.7 输入发送机制（实测 + 源码确认）

源码 `packages/cli/src/ui/key/keyBindings.ts`：
- **`Command.SUBMIT` = 单 `enter`**（注释：must exclude shift to allow shift+enter for newline）
- `Command.NEWLINE` = `shift+enter` / `ctrl+enter` / `alt+enter` / `ctrl+j`

**发送经验**：
- `send -i "文本"` 的 `\r` 行尾**不一定可靠触发提交**（消息可能滞留输入框）
- 可靠方式：`advsend <sid> -i "文本{enter}" -e none`（显式 `{enter}` 提交）或
  `send <sid> -i "文本"` 后接 `advsend <sid> -i "{enter}" -e none`
- 输入框有残留时先 `advsend <sid> -i "{ctrl+u}" -e none` 清行
- 多行输入：`advsend <sid> -i "第一行{shift+enter}第二行{enter}" -e none`
- 对话框选择：`advsend <sid> -i "N" -e none` + `advsend <sid> -i "{enter}" -e none`

## 5. 与其它解析器差异

| 维度 | geminiparser | claudeparser/workbuddyparser | codexparser | devinparser | opencodeparser |
|------|-------------|------------------------------|-------------|-------------|----------------|
| 存储 | JSONL 追加日志 | JSONL 追加日志 | JSONL rollout | ATIF JSON | SQLite |
| 目录 | `tmp/<project>/chats/` | `projects/<cwd-encoded>/` | `sessions/YYYY/MM/DD/` | `transcripts/` | 单 db |
| 会话 id | UUID（文件名尾 8 位） | UUID（文件名全） | UUID（rollout 文件名） | 单词（blend-pencil） | ses_xxx |
| 思考 | `thoughts[]`（分片） | 同左 | `reasoning[]` | `reasoning_content` | `reasoning` part |
| 工具 | `toolCalls[]` 内嵌结果 + user 消息 functionResponse | 双形态（list/str） | function_call + output 分离 | tool_calls + observation | tool part 同体 |
| tokens | `{input,output,cached,thoughts,tool,total}` 多数 null | input/output/cache_read... | 无（token_count 事件） | prompt/completion/cached | input/output/reasoning/cache |
| 去重 | **同 id 消息流式重写（保留最后）** | — | reasoning 全文去重 | — | — |
| 子代理 | `chats/<main>/<sub>.jsonl`（kind=subagent） | — | — | — | agent 字段 |

**gemini 独有**：
1. 同 id 消息重写去重（流式期间先文本后 toolCalls）
2. toolCalls 结果与 user 消息 functionResponse **双写**（互为冗余）
3. `$set.messages` 定期完整快照（可作兜底/校验）
4. 子代理会话独立文件 + `invoke_agent.agentId` 关联
5. 无 SQLite 索引，会话定位全靠扫文件名

## 6. 边界情况与注意事项

1. **消息 id 重复**：同 id 保留最后出现（toolCalls 完整版覆盖纯文本版）
2. **tokens 多数 null**：usage 聚合需容错，可能全 0
3. **初始 session_context**：首条 user 消息是 `<session_context>...</session_context>` 系统注入，应过滤（类似 codex 的 AGENTS.md 注入）
4. **`@file` 引用**：user 消息 content 多个 text 项（用户文本 + `--- Content from referenced files ---` + 文件内容），`displayContent` 字段存完整注入
5. **`$set.messages` 快照**：是物化视图，含历史全部消息（含 session_context）；与追加事件联合可交叉校验
6. **info/error 事件**：不是消息，单独收集为会话状态（cancelled/error 标记）
7. **子代理会话**：`kind: "subagent"`，解析主会话时可选展开（通过 invoke_agent.agentId → `chats/<主id>/<子id>.jsonl`）
8. **projectHash**：SHA-256（疑似 cwd 规范化哈希），可用于校验 project 归属，解析不依赖
9. **文件编码 UTF-8**：Windows 下读取用 utf-8；日志中中文正常
10. **运行中会话**：无独立索引（claude 有 sessions/<pid>.json，gemini 没有）；可用文件 mtime + `logs.json` 推断活跃状态

## 7. 调研过程记录

- 2026-08-23：定位 npm 包 @google/gemini-cli v0.56.0；盘点 `~/.gemini` 全部目录
- 统计分析：20 个主会话文件（3 个项目 a/<username>/gemini-cli-main），142 user / 235 gemini（106 带 toolCalls）/ 12 info / 1 error 事件；`$set` 键 4 种；tokens 非 null 仅 1 条
- PTY-Agent 实测 TUI（gem5/gem6/gem7 三次会话，daemon 重启一次）：15 个屏幕快照覆盖全部状态
  - 欢迎页 / 对话空闲 / 思考中 / API 错误 / 正常回复 / ReadFile 工具 / ask_user 对话框 / 权限确认 / 计划模式 / 计划确认 / Shell 权限 / 模型选择 / WriteFile 确认 / Edit diff 确认 / 子代理运行
- 源码确认：`gemini-cli-main/packages/cli/src/ui/key/keyBindings.ts`（SUBMIT=enter、NEWLINE=shift+enter）
- 确认发送方式：`advsend -i "文本{enter}" -e none` 可靠提交
- 快照文件列表（`research_snapshots/`，15 个）：
  `01_welcome.txt` / `02_idle_conversation.txt` / `03_thinking.txt` / `04_api_error.txt` / `05_reply.txt` / `06_tool_readfile.txt` / `07_ask_user.txt` / `08_awaiting_approval.txt` / `09_plan_mode.txt` / `10_plan_confirm.txt` / `11_shell_approval.txt` / `12_model_dialog.txt` / `13_writefile_approval.txt` / `14_edit_diff_approval.txt` / `15_subagent_running.txt`
