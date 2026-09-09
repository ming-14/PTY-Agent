# AtomCode 解析器调研报告（atomcodeparser）

## 0. 核心结论

**AtomCode（AtomGit atomcode v5.0.8）在本地存储完整结构化对话历史（JSONL 追加日志 + 完整快照）**，解析器采用**混合方案**：
- **JSONL 消息历史**（主源）：`~/.atomcode/sessions/<cwd-hash>/<sessionId>.jsonl`
  — 每行一个完整回合，含 user/assistant/tools/usage
- **会话元数据**（辅助）：`~/.atomcode/sessions/<cwd-hash>/<sessionId>.meta`
  — 会话元数据（working_dir/turn_stats/tokens/ctx_window）
- **完整消息快照**（补充）：`~/.atomcode/sessions/<cwd-hash>/<sessionId>.snapshot`
  — 完整消息列表（含 System/User/Assistant/Tool roles + 工具调用详情 + 元数据）
- **屏幕快照**（补充）：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` → 实时状态
  （AI 状态/输入框/上下文百分比/模型名）

本地存储位置（`$ATOMCODE_HOME`，默认 `~/.atomcode`，通过 `ATOMCODE.ps1` 设为 `~/.atomcode`/`__atomcode`）：
- `sessions/<cwd-hash>/<sessionId>.jsonl` — 消息历史（追加式，每回合一行）
- `sessions/<cwd-hash>/<sessionId>.meta` — 会话元数据 JSON
- `sessions/<cwd-hash>/<sessionId>.snapshot` — 完整消息列表 JSON
- `sessions/<cwd-hash>/<sessionId>.rewind.json` — 回退点
- `sessions/<cwd-hash>/<sessionId>.ui.json` — UI 状态
- `sessions/<cwd-hash>/<sessionId>.lease` — 租约锁（空文件）
- `sessions/<cwd-hash>/<sessionId>.meta.lock` — 元数据锁（空文件）
- `history-v2/<cwd-hash>/entries.jsonl` — 输入历史
- `config.toml` — 设置（provider/model/api_key/base_url/context_window/max_tokens）
- `logs/atomcode.log` — 运行日志
- `plugins/` — 插件市场
- `telemetry/` — 遥测数据
- `device_id` — 设备 UUID
- `recent_dirs.txt` — 最近工作目录
- `stderr.log` — 安装日志

cwd 编码规则：16 位十六进制哈希（64 位），算法为 Rust 平台特定实现（非标准库常见哈希）

## 1. 存储布局

```
~/.atomcode/sessions/
├── <cwd-hash-1>/                      # 第一个工作目录
│   ├── <sessionId-1>.jsonl            # 消息历史
│   ├── <sessionId-1>.meta             # 会话元数据
│   ├── <sessionId-1>.snapshot         # 完整消息快照
│   ├── <sessionId-1>.rewind.json      # 回退点
│   ├── <sessionId-1>.ui.json          # UI 状态
│   ├── <sessionId-1>.lease            # 租约锁
│   ├── <sessionId-1>.meta.lock        # 元数据锁
│   ├── <sessionId-2>.jsonl
│   └── ...
├── <cwd-hash-2>/                      # 第二个工作目录
│   └── ...
└── ...
```

cwd-hash 示例：
| 工作目录 | 哈希 |
|----------|------|
| `C:\Users\<username>` | `e3e2660b9823f796` |
| `C:\Users\<username>\Desktop\atomcodeparser` | `208fb10d41f370a1` |
| `\\?\C:\Users\<username>\Desktop\atomcodeparser` | `7a61b6421611fc7d` |

注意：`-C`/`--cwd` 参数会导致路径存储带 `\\?\` 前缀，产生不同哈希。

## 2. JSONL 消息历史结构（`<sessionId>.jsonl`）

每行一个 JSON 事件，一个 turn 一行。实测样本（2 会话 / 4 回合 / 2 轮带工具调用）：

```json
{
  "v": 1,
  "started_at": 1787465209009,       // 回合开始时间戳（毫秒）
  "ts": 1787465215555,                // 事件时间戳（毫秒）
  "iso": "2026-08-23T06:06:55.555+00:00",  // ISO 8601 时间戳
  "session_id": "86f15020-...",       // 会话 UUID
  "turn_id": 1,                       // 回合序号
  "undone": false,                    // 是否撤销
  "user": "hi",                       // 用户输入文本
  "assistant": "\n\nHi! How can I help you today?",  // 助手回复文本
  "tools": [                          // 工具调用列表
    {
      "name": "list_directory",       // 工具名
      "args": "{\"path\": \"C:/Users/...\", \"depth\": 2}",  // 参数字符串
      "result": "hash_crack.py\n...", // 工具结果文本
      "is_error": false               // 是否错误（可选）
    }
  ],
  "usage": {                          // Token 用量
    "prompt": 17396,                  // 输入 token 数
    "completion": 311,                // 输出 token 数
    "cached": 16384                   // 缓存命中 token 数
  }
}
```

### 2.1 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `v` | int | ✓ | 格式版本（当前为 1） |
| `started_at` | int | ✓ | 回合开始时间（毫秒 Unix 时间戳） |
| `ts` | int | ✓ | 事件时间戳 |
| `iso` | str | ✓ | ISO 8601 时间戳 |
| `session_id` | str | ✓ | 会话 UUID |
| `turn_id` | int | ✓ | 回合序号（从 1 递增） |
| `undone` | bool | ✓ | 是否已撤销（如回退） |
| `user` | str | ✓ | 用户输入文本 |
| `assistant` | str | ✓ | 助手回复文本（可能为空，此时工具调用在 tools 中） |
| `tools` | array | ✓ | 工具调用列表，空数组表示无工具调用 |
| `usage` | object | ✓ | Token 用量 |

### 2.2 tools 元素结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | str | ✓ | 工具名（如 `list_directory`、`read_file`、`bash` 等） |
| `args` | str | ✓ | JSON 字符串形式的工具参数 |
| `result` | str | ✓ | 工具执行结果文本 |
| `is_error` | bool | | 工具执行是否出错（可选，缺失视为 false） |

### 2.3 已发现工具

| 工具名 | 说明 | 参数形态 |
|--------|------|----------|
| `list_directory` | 列出目录内容 | `{"path": str, "depth": int?}` |
| `read_file` | 读取文件 | 推理（未实测） |
| `edit_file` | 编辑文件 | 推理（未实测） |
| `bash` | 执行 shell 命令 | 推理（未实测） |
| `write_file` | 写入文件 | 推理（未实测） |
| `grep` | 搜索文件内容 | 推理（未实测） |
| `glob` | 文件匹配 | 推理（未实测） |

注意：解析器对工具名通用处理（不硬编码工具列表）。

## 3. 会话元数据（`<sessionId>.meta`）

完整 JSON 格式：

```json
{
  "v": 1,
  "id": "86f15020-b057-4954-8c5f-93e9e2074c38",
  "name": "Initial Greeting",              // AI 生成的会话标题
  "user_renamed": false,                   // 用户是否重命名
  "ai_named": true,                        // AI 是否自动命名
  "owner": "native",                       // 所有者类型
  "import_info": null,                     // 导入信息
  "fork_info": null,                       // 分支信息
  "working_dir": "C:\\Users\\<username>\\Desktop\\atomcodeparser",  // 工作目录
  "created_at": 1787465104827,             // 创建时间（毫秒）
  "updated_at": 1787465390043,             // 最后更新时间
  "turn_count": 2,                         // 回合数
  "message_count": 9,                      // 消息数（含 System 消息）
  "turn_stats": [                          // 每回合统计
    {
      "after_message": 5,                  // 回合结束后的消息数
      "position_valid": true,
      "turn_id": 1,
      "round_count": 1,                    // 轮次数（工具调用轮数）
      "tool_call_count": 0,               // 工具调用次数
      "duration_ms": 6537,                 // 持续毫秒
      "total_tokens": 17243,               // 总 token 数
      "errored": false,                    // 是否出错
      "used_tokens": 17215,               // 使用 token 数
      "ctx_window": 262144,                // 上下文窗口大小
      "model_usage": [                     // 模型用量（可多个）
        {
          "provider_id": "SenseNova",
          "model_id": "sensenova-6.8-flash-lite",
          "tokens": {
            "input": 831,                  // 输入 token
            "output": 28,                  // 输出 token
            "cached_input": 16384          // 缓存命中 token
          }
        }
      ]
    }
  ],
  "origin": "manual"                       // 来源（manual / continue / import）
}
```

### 3.1 关键字段说明

- `working_dir`：工作目录，TUI 交互模式为常规路径（`C:\...`），头戴模式（`-p`）为 `\\?\C:\...` 扩展路径
- `turn_stats`：每回合的详细统计，含模型用量与 token 明细
- `turn_count`：回合数（与 jsonl 行数一致）
- `message_count`：消息数（含 System 角色消息，通常 = turn_count * 4 + 3）
- `origin`：`manual`（手动创建）/ `continue`（继续会话）/ `import`（导入）

## 4. 完整消息快照（`<sessionId>.snapshot`）

包含完整系统提示 + 对话消息 + 工具调用详情，`messages` 数组含 9 条消息（2 回合对话）：

### 4.1 顶层结构

```json
{
  "version": 1,
  "messages": [ <message>, ... ],
  "cache_epoch": 0,
  "turn_counter": 2,
  "request_counter": 3
}
```

### 4.2 消息角色与结构

| 角色 | 数量 | 说明 | 关键字段 |
|------|------|------|----------|
| `System` | 3 | 系统提示（系统角色+会话上下文+可用技能） | `text`, `tool_calls:[]`, `synthetic:false` |
| `User` | 2 | 用户输入 | `text`, `tool_calls:[]`, `is_error:false` |
| `Assistant` | 3 | 助手回复/工具调用 | `text`, `tool_calls: [{name, arguments, id}]`, `meta:{tokens, elapsed_ms, ...}` |
| `Tool` | 1 | 工具结果返回 | `text`(结果), `tool_call_id`, `is_error:false` |

### 4.3 Assistant 消息 meta 字段

```json
{
  "tokens": {
    "prompt": 17215,
    "completion": 28,
    "cached": 16384
  },
  "elapsed_ms": 6528,
  "reasoning_elapsed_ms": 0,
  "ctx_window": 262144,
  "used_tokens": 17215,
  "utilization": 0.06567001,
  "round": 1,
  "turn_id": 1,
  "request_id": 1,
  "provider_response_id": "fbcd9771-...",
  "provider_model": "sensenova-6.8-flash-lite",
  "session_id": "86f15020-...",
  "finish_reason": "stop"              // stop / tool_calls / error
}
```

### 4.4 Tool 消息结构

```json
{
  "role": "Tool",
  "text": "hash_crack.py\nhash_crack2.py\n...",
  "tool_calls": [],
  "tool_call_id": "call_1d012f88d5e246dea778f7bb",
  "is_error": false,
  "meta": null,
  "synthetic": false,
  "reasoning": null,
  "images": [],
  "reasoning_blocks": []
}
```

## 5. 其他文件结构

### 5.1 rewind.json

```json
{
  "version": 2,
  "points": [
    {
      "turn_id": 1,
      "prompt_number": 1,
      "prompt_preview": "hi",
      "files": []
    }
  ]
}
```

### 5.2 ui.json

```json
{
  "v": 1,
  "entries": []
}
```

### 5.3 history-v2/entries.jsonl

每行一个 JSON：`{"text":"hi"}`

### 5.4 config.toml

```toml
default_provider = "SenseNova"

[providers.SenseNova]
type = "openai"
api_key = "sk-..."
model = "sensenova-6.8-flash-lite"
base_url = "https://your-api-endpoint.com/v1"
context_window = 262144
max_tokens = 65536
```

## 6. 屏幕快照格式（混合方案补充实时状态用）

### 6.1 AtomCode TUI 布局（v5.0.8，实测）

**欢迎页（main）**：
```
  * AtomCode                            v5.0.8  ·  MIT
  * ~/Desktop/atomcodeparser
  * sensenova-6.8-flash-lite
  Tips for getting started
  /login  claim a free token quota
  /mcp    connect MCP tools
  /setup  one-shot recommended setup
  /loop   run a prompt on a recurring loop

    i Multi-line input: end the line with `\` then press Enter.
      ...

────────────────────────────────────
>
────────────────────────────────────
  sensenova-6.8-flash-lite · ~/Desktop/atomcodeparser · 0/262k tok (0%)
```

**对话中（conversation）**：
```
  * AtomCode                            v5.0.8  ·  MIT
  * ~/Desktop/atomcodeparser
  * sensenova-6.8-flash-lite

> hi

  Hi! How can I help you today?

  --- Done · 1 rounds · 0 tools · 6.5s · 859 tokens · 95% cached ---

> 看看当前目录

* ListDirectory(C:/Users/<username>/Desktop/atomcodeparser)
  ` hash_crack.py (13 lines)

  看起来是一个密码哈希相关的 Python 项目...

  --- Nailed it · 2 rounds · 1 tools · 6.9s · 18.56K tokens · 47% cached ---

────────────────────────────────────
>
────────────────────────────────────
  sensenova-6.8-flash-lite · ~/Desktop/atomcodeparser · 17.4k/262k tok (7%)
```

**思考/工作状态（working）**：
```
> 看看当前目录

/ Noodling... (0s)            ← 或 `- Pondering... (4s)`
```

**输入待提交（input_pending）**：
```
> 看看当前目录                  ← 输入框有文字，输入 `>` 后
```

### 6.2 实时状态字段（JSONL 缺失/不即时，需从屏幕解析）

| 字段 | 屏幕位置 | 解析方式 |
|------|----------|----------|
| AI 状态 | 消息区底部 | `/ Noodling...` / `- Pondering...` = 思考中；`* ListDirectory(...)` 行 = 工具执行中；无以上且输入框空 = 空闲 |
| 输入框文字 | 分隔线间 `>` 行 | `>` 后的文本；`>` 单独一行无文本 = 输入框为空 |
| 上下文百分比 | 状态栏 | `N/262k tok (N%)` 提取百分比 |
| 模型名 | 状态栏行首 / 欢迎页 | `sensenova-6.8-flash-lite · ...` 提取 |
| 工作目录 | 状态栏 / 欢迎页 `*` 行 | `* ~/Desktop/...` 或 `· ~/Desktop/...` 提取 |
| 界面类型 | 有无 `* AtomCode` header | 有 `* AtomCode` + `v5.0.8` = main（欢迎页）；有对话标记（消息/分隔线/状态栏 token 数） = conversation |
| 版本号 | 欢迎页 header | `* AtomCode ... v5.0.8` 提取 |
| Token 用量 | 状态栏 | `N/262k tok (N%)` 提取已用与总量 |
| 回合完成状态 | 分隔线 | `--- Done / Nailed it · N rounds · N tools · Ns · N tokens · N% cached ---` |

### 6.3 消息类型视觉特征（屏幕解析参考）

| 前缀/特征 | 类型 |
|-----------|------|
| `> <text>` | 用户输入回显 |
| 无前缀文本（在 `>` 回显下方） | AI 回复正文 |
| `* <ToolName>(...args...)` | 工具调用（完成） |
| `` ` ``（缩进，反引号前缀） | 工具输出（单行摘要） |
| `/ Noodling...` / `- Pondering...` | 思考/等待中 |
| `--- Done / Nailed it · ...` | 回合完成标记 |
| `────────────────────` | 输入框分隔线（全 `─` 行） |
| `  sensenova-6.8-flash-lite · ...` | 状态栏 |

### 6.4 多尺寸注意事项

| 尺寸 | 现象 |
|------|------|
| 40x10 | 仅显示尾部消息，欢迎页 header 滚出屏幕；状态栏截断 |
| 60x15 | 部分内容可见，欢迎页 header 滚出 |
| 80x24 | 可显示完整消息区域，欢迎页 header 可能滚出 |
| 120x40 | 完整显示，同默认 |
| 200x50 | 完整显示，部分行换行显示 |

## 7. 会话定位

### 7.1 存储布局

```
~/.atomcode/sessions/
├── e3e2660b9823f796/                 # C:\Users\<username> 的哈希
│   ├── b2942751-....jsonl            # 交互会话（2 回合，含工具调用）
│   ├── b2942751-....meta
│   ├── b2942751-....snapshot
│   ├── f89b7640-....meta             # 空会话（turn_count=0）
│   └── ...
├── 208fb10d41f370a1/                 # C:\Users\<username>\Desktop\atomcodeparser
│   └── 86f15020-....jsonl            # 交互会话（2 回合，含 list_directory 工具）
└── ...
```

### 7.2 定位策略

1. 按 session_id 在 `sessions/` 下递归搜索：遍历所有子目录，查找 `<sessionId>.jsonl` 文件
2. 从 `sessions/` 子目录名获取 cwd-hash，但无法直接反向解码为路径
3. 从 `.meta` 文件的 `working_dir` 字段获取实际工作目录
4. 列出全部会话：遍历 `sessions/` 下全部子目录，读取每个子目录中的 `.meta` 文件
5. 运行中会话：无专用进程锁文件（`.lease` 为空文件，不包含 PID 信息）；可通过 `turn_stats` 判断（有 `position_valid` 字段）或检查进程是否存在

### 7.3 cwd 哈希编码

cwd 哈希为 16 位十六进制（64 位），由 AtomCode 的 Rust 运行时计算。算法未公开，无法直接从路径计算哈希。解析器采用**扫描方式**：
- 遍历 `sessions/` 下所有子目录
- 读取 `.meta` 获取 `working_dir`
- 按需建立路径→哈希的运行时缓存

## 8. 已收集样本

### 8.1 会话数据

| 会话 | 文件 | 回合数 | 说明 |
|------|------|--------|------|
| `b2942751-...` | `b2942751-....jsonl` | 2 | 真实交互（hi + 查看桌面），含 list_directory 工具调用 |
| `b2942751-...` | `b2942751-....snapshot` | 2 | 35329 字节，完整消息列表含 System/User/Assistant/Tool |
| `86f15020-...` | `86f15020-....jsonl` | 2 | 真实交互（hi + 看看当前目录），含 list_directory 工具调用 |
| `86f15020-...` | `86f15020-....snapshot` | 2 | 31952 字节，完整消息列表，含 finish_reason 和 utilization |
| `66a2b782-...` | `66a2b782-....jsonl` | 1 | 头戴模式（`-p "say hello"`），无工具调用 |
| `66a2b782-...` | `66a2b782-....meta` | 1 | 含 `\\?\` 前缀的 working_dir |
| `f89b7640-...` | `f89b7640-....meta` | 0 | 空会话（turn_count=0） |
| `9cbc554b-...` | `9cbc554b-....meta` | 0 | 空会话 |
| 旧版 3 会话 | `.atomcode/sessions/` | 0 | 7/24 创建的空会话 |

### 8.2 屏幕快照样本

| 文件 | 场景 | 尺寸 |
|------|------|------|
| `sample_idle.txt` | 欢迎页（main，含 `* AtomCode` header + 提示 + 状态栏） | 120x40 |
| `sample_input_pending.txt` | 输入待提交（输入框有文字，AI 空闲） | 120x40 |
| `sample_conversation_idle.txt` | 对话空闲态（含回复 + 回合完成标记 + 状态栏） | 120x40 |
| `sample_conversation_idle2.txt` | 对话空闲态（含工具调用输出 + ListDirectory） | 120x40 |
| `sample_working.txt` | 思考中（`/ Noodling...` 行） | 120x40 |
| `sz_40x10.txt` | 窄屏（40x10，对话空闲态） | 40x10 |
| `sz_60x15.txt` | 小屏（60x15，对话空闲态） | 60x15 |
| `sz_80x24.txt` | 中屏（80x24，对话空闲态） | 80x24 |
| `sz_120x40.txt` | 宽屏（120x40，对话空闲态） | 120x40 |
| `sz_200x50.txt` | 超宽屏（200x50，对话空闲态） | 200x50 |

屏幕快照获取方式：PTY-Agent 终端模式 `exec <sid> -c "atomcode.exe" --env ATOMCODE_HOME=... --size WxH` → `send/read <sid> --keep-ansi -o <file>`（快照模式，AtomCode TUI 是增量刷新，`--full` 可能导致 pyte 渲染重叠）。

### 8.3 多尺寸解析注意事项

| 现象 | 处理 |
|------|------|
| 窄屏（40x10/60x15）欢迎页 header 滚出屏幕 | `screen_type` 用输入框 `>` 判定为 conversation |
| 窄屏状态栏截断 | 状态栏文本只取可见部分，model_display/cwd_display 尽力解析 |
| 宽屏（200x50）多行内容换行 | 换行行在 pyte 中为独立行，不影响正则匹配 |
| 分隔线 `─` 行 | 正则 `^\s*─{10,}\s*$` 匹配（整行纯 ─） |
| 思考状态两种形态 | `/ Noodling...` 和 `- Pondering...` 均为思考中，统一为 `thinking` |
| 回合完成标记 | `Done`（成功）/ `Nailed it`（成功）/ 可能其他变体 |

## 9. 解析器返回结构建议

```json
{
  "session": {
    "id": "86f15020-b057-4954-8c5f-93e9e2074c38",
    "name": "Initial Greeting",
    "cwd": "C:\\Users\\<username>\\Desktop\\atomcodeparser",
    "started_at": 1787465209009,
    "status": "completed",
    "model": "sensenova-6.8-flash-lite",
    "model_provider": "SenseNova",
    "cli_version": "5.0.8",
    "git_commit": "2d510a3",
    "turn_count": 2,
    "message_count": 9,
    "ctx_window": 262144,
    "usage": {
      "input_tokens": 19080,
      "output_tokens": 339,
      "cached_input_tokens": 32768
    }
  },
  "live_state": {
    "ai_status": "idle|thinking|tool_running",
    "input_text": "",
    "context_percent": 7.0,
    "context_tokens": 17200,
    "context_window": 262144,
    "screen_type": "main|conversation",
    "model_display": "sensenova-6.8-flash-lite",
    "cwd_display": "~/Desktop/atomcodeparser",
    "version_display": "5.0.8"
  },
  "messages": [
    {
      "id": "86f15020-...-u1",
      "role": "user",
      "ts": 1787465209009,
      "ts_iso": "2026-08-23T06:06:55.555+00:00",
      "items": [
        { "type": "text", "text": "hi" }
      ]
    },
    {
      "id": "86f15020-...-a1",
      "role": "assistant",
      "ts": 1787465215555,
      "ts_iso": "2026-08-23T06:06:55.555+00:00",
      "model": "sensenova-6.8-flash-lite",
      "usage": {
        "input_tokens": 17215,
        "output_tokens": 28,
        "cached_input_tokens": 16384
      },
      "finish_reason": "stop",
      "items": [
        { "type": "text", "text": "\n\nHi! How can I help you today?" }
      ]
    },
    {
      "id": "86f15020-...-a2",
      "role": "assistant",
      "ts": 1787465390052,
      "ts_iso": "2026-08-23T06:09:50.052+00:00",
      "model": "sensenova-6.8-flash-lite",
      "usage": {
        "input_tokens": 17396,
        "output_tokens": 311,
        "cached_input_tokens": 16384
      },
      "finish_reason": "tool_calls",
      "items": [
        { "type": "text", "text": "\n\n" },
        {
          "type": "tool_use",
          "tool_call_id": "call_1d012f88d5e246dea778f7bb",
          "name": "list_directory",
          "input": { "path": "C:/Users/<username>/Desktop/atomcodeparser", "depth": 2 }
        },
        {
          "type": "tool_result",
          "tool_call_id": "call_1d012f88d5e246dea778f7bb",
          "name": "list_directory",
          "success": true,
          "is_denied": false,
          "is_error": false,
          "output": "hash_crack.py\nhash_crack2.py\n..."
        }
      ]
    }
  ]
}
```

## 10. 与 claudeparser / clineparser / codexparser / devinparser / workbuddyparser / opencodeparser 的差异

| 维度 | 其他解析器 | **atomcodeparser** |
|------|-----------|-------------------|
| 存储 | JSONL / JSON / ATIF / SQLite | **JSONL（每回合一行）+ .meta 元数据 + .snapshot 完整快照** |
| 会话 ID | UUID / 数字+随机 / 形容词-名词 | **UUID**（如 `86f15020-b057-4954-8c5f-93e9e2074c38`） |
| 会话定位 | 文件系统扫描 / JSON 索引 / SQLite 查询 | **遍历 sessions/ 子目录 + .meta 文件的 working_dir** |
| 时间戳 | 毫秒 int / ISO 字符串 | **毫秒 int + ISO 字符串并存**（jsonl 中两者都有） |
| 用户输入 | 字符串 / content 数组 / input_text 标签 | **`user` 字段直接字符串** |
| tool_use | content 项 / function_call 独立事件 / tool_calls 数组 | **`tools` 数组（name + args JSON 字符串 + result）** |
| tool_result | content 项 / function_call_output 独立事件 / observation | **result 字符串 + 可选的 `is_error` 布尔** |
| 思考 | thinking content 项 / reasoning 独立事件 | **无显式思考字段**（jsonl 中无 thinking，snapshot 中 assistant 消息也无 reasoning 字段） |
| 消息聚合 | 每事件 / 按回合 / 按 step / 按 message | **按回合聚合**（jsonl 每行 = 1 回合；snapshot 按消息） |
| Token 用量 | message.usage / metrics 字段 | **jsonl.usage + snapshot.meta.tokens** |
| 工具调用与结果 | 分离事件或同体 | **同体**（一个 tools 元素同时含 args 和 result） |
| 会话元数据 | model/workspace/usage 等 | **turn_stats 含详细统计（模型用量、ctx_window、duration）** |
| 运行中会话 | sessions/<pid>.json / session_locks | **无专用索引**（.lease 为空文件，需通过 tasklist 检查进程） |
| 会话标题 | ai-title 事件 / title 字段 | **.meta.name 字段**（AI 生成） |
| 子代理 | Agent 工具 / parent_id 链 | 未实测（可能支持） |
| 屏幕状态 | 各有特色 | **`>` 输入框、`/ Noodling...` 思考态、`--- Done ...` 完成标记、`* ToolName` 工具行** |

## 11. 已知注意事项

- **消息 ID**：JSONL 中无消息 ID，只有回合 ID（`turn_id`）。解析器生成序号 ID（`<sessionId>-uN` / `-aN`）。
- **思考字段**：JSONL 中无 thinking 字段，snapshot 中 assistant 消息也无 `reasoning` 字段。AtomCode 的 sensenova 模型未返回 reasoning。
- **工具调用与结果同体**：一个 tools 元素同时承载调用参数（`args` JSON 字符串）和结果（`result` 字符串），解析时同时输出 tool_use 和 tool_result 两个 item。
- **模型信息**：来自 `.meta.turn_stats[].model_usage[]` 的 `provider_id` 和 `model_id`。
- **Token 用量**：JSONL 中 `usage.prompt` 为输入，`usage.completion` 为输出，`usage.cached` 为缓存命中。snapshot 中 `meta.tokens` 更详细。
- **会话定位**：cwd 哈希算法未公开，无法反向计算。解析器通过遍历 `sessions/` 子目录 + 读取 `.meta.working_dir` 定位。
- **运行中会话**：`.lease` 文件为空，不含 PID。需通过 `tasklist` 检查 AtomCode 进程是否存在。snapshot 的 `turn_counter` 和 `request_counter` 可判断会话是否活跃（非零 = 有活动）。
- **空会话**：`turn_count=0` 的会话（`.meta` 中 `turn_stats:[]`）无消息数据，应过滤。
- **屏幕快照**：PTY-Agent 终端模式 `read <sid> --keep-ansi -o <file>` 抓取，注意 AtomCode TUI 是增量刷新，`--full` 可能导致 pyte 渲染重叠。
- **多尺寸**：窄屏（40x10/60x15）欢迎页 header 滚出屏幕，`screen_type` 判定用 `* AtomCode` 行 + `v5.0.8` 版本号。宽屏（200x50）内容换行不影响解析。
- **placeholder 输入**：输入框为空时，`>` 单独一行，无文本。
- **思考状态**：两种形态 `/ Noodling...` 和 `- Pondering...`，均表示 AI 正在思考/工作，统一为 `thinking` 状态。
- **回合完成标记**：`--- Done · ...`（首次成功）/ `--- Nailed it · ...`（后续成功），解析时统一处理。