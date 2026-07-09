---
name: pty-agent
description: Interactive CLI program proxy via subprocess or pseudo-terminal (PTY). Use when: (1) must maintain ongoing dialog with interactive programs (REPLs, debuggers, Servers) — send input and wait for specific prompts; (2) process may block, crash, or pop up GUI windows — need real-time state detection; (3) simulating user interaction tests. DO NOT use for: non-interactive scripts, web/HTTP API calls, GUI interfaces. If a plain script suffices, do not use this tool.
allowed-tools: Bash, Read, Write, Edit
---

# PTY-Agent

PTY-Agent 是一个**命令行交互式程序交互代理**，通过subprocess或伪终端（PTY）与交互式 CLI 程序双向通信

原理：程序后台有运行一个守护进程，由守护进程接受用户命令，对应CLI进行操作

程序位于`app.py`，运行方法：`python app.py ...`

## 何时使用

- **迫不得已**必须与交互式程序持续对话，需要发送输入并等待特定输出（提示符）后再继续，如GDB、服务器Server、大程序的编译操作。但是**能写脚本执行就不用PTY-Agent**
- **进程可能阻塞或执行时间过长** — 需要定期检测状态
- **子进程可能崩溃** — 需要实时感知崩溃事件
- **被要求进行模拟用户操作的测试**

## 何时不使用

- 非交互式任务
- 完全能写脚本执行、调试
- GUI界面，浏览器，复杂的TUI程序

## 命令速查

| 命令 | 用途 | 典型选项 | 示例 |
|------|------|----------|------|
|`start/stop`| 手动启动/停止守护进程；启动守护进程`exec`可实现，一般无需手动 | | |
| `exec <new-session-id> <options>` | 执行命令以启动会话 | `-c "<command>"`(req), `-t "<regex>"`, `--timeout <seconds>`, `--cwd <path>` | `exec id_py -c "python -i" -t ">>>"` |
| `send <session-id> "<content>" [options]` | 发送输入到运行中的会话 | `-t "<regex>"`, `--timeout <seconds>` | `send id_py "print(1)" -t ">>>"` |
| `read <session-id> [options]` | 读取会话输出 | `--lines`, `--grep` | `read myid --lines 10` |
| `list` | 列出所有会话 | | |
| `kill <session-id>` | 终止会话 | | |
| `events <session-id> [options]` | 查看会话事件 | `--last <N>`, `--since <iso-datetime\|HH:MM>` | `events myid --last 10` |
| `closewin <session-id> <window-handle>` | 关闭 GUI 窗口；`<window-handle>`支持十进制或 0x十六进制| |

### 命令 send 的返回条件

| 模式 | 条件 | 返回信息 |
|------|------|----------|
| 带trigger | 匹配到正则 | 增量输出 |
| 带idle-timeout | 静默超时 | 增量输出 |
| 带timeout | 达到超时 | 增量输出 |
| 都不带 | 达到默认超时 | 增量输出 |
| 未关闭GUI检测 | 检测到GUI窗口（subprocess不一定可用） | GUI窗口信息+增量输出 |
| | 有进程崩溃 | 相关事件+增量输出 |
| | 程序退出 | 增量输出 |

注：**高效利用本程序的条件返回功能，及时根据对应程序的输出结果更新条件（特别是`-t`），灵活使用不同的返回条件**。不建议终端执行Sleep，不建议反复send后又read

## exec 用法

`python app.py exec <session-id> <options>`

选项基本与 send 一致
特殊选项：
- `-c "<command>"`(req) 执行的命令，必填
- `--shell <shell>` 支持`cmd/powershell/pwsh/bash`（默认PowerShell，环境不支持自动回退CMD）；只有在`exec`启动时才能配置终端
- `--pty` 启用完整伪终端（不支持 `|`、`&&` 等shell语法）
    - `--force-pty-mode` 忽略`--pty`下的shell操作符检测
- `--cwd <path>` 子进程工作目录，不填则默认为调用者（客户端）的工作目录；如果与期望工作目录不一致，建议指定

## send 用法

`python app.py send <session-id> "<content>" [options]`

选项
- `-t/--trigger "<regex>"` 匹配正则
    - `--newline` — 换行后才检查正则触发条件, 与`-t`搭配
- `--timeout <seconds>` 等待超时（默认120s）
    - `--idle-after-first-output` 首次输出后才开始检测静默，与`--idle-timeout`搭配
    - 注：**高效利用本程序的条件返回功能，及时根据对应程序的输出结果更新条件（特别是`-t`），灵活使用不同的返回条件**。不建议终端执行Sleep，不建议反复send后又read
- `--idle-timeout <seconds>` 输出静默超时，程序在指定时间内不输出时触发条件
- `--full` 返回终端全部数据（数据大，尽量用`--lines N`）
- `--json-escaping` **JSON 转义模式**，将`<content>`进行JSON转义（`\\ \" \n \t \r \uXXX ...`），**建议在发送多行内容时使用**

`<content>`末尾自动追加换行
没有`--input`参数

### 引号处理规则（命令行层）

- cmd 写 `\"` 嵌套： `-c "python -c \"print(1)\""`
- PowerShell/Pwsh 外层单引号，内层双引号： `-c 'python -c "print(1)"'`

## read 用法

`python app.py read <session-id> [options]`

选项：
- `--lines <N>` 最后 N 行
- `--lines start:end` 范围行
- `--grep "<regex>"` 正则过滤
- `--offset <bytes>` 增量读取
- `--full` 返回终端全部数据（数据大，尽量用`--lines N`）

## events 完整用法
`python app.py events <session-id> [options]`

选项：
- `--last N`
- `--since <iso-datetime\|HH:MM>`
- `--until <iso-datetime\|HH:MM>`

## 全局/通用选项

- `--keep-ansi` 保留控制码
- `--color` - 启用终端颜色输出
- `--output-by-natural-language` 自然语言输出
- `--encoding <encoding>` 终端编码，乱码时设置`utf-8/gbk/gb2312/gb18030/big5`
- `--no-debug` 禁用响应中的 debug 输出（进程树/GUI 窗口/事件）
- `--show-config [KEY]` 查看当前调用配置
- `--default <KEY> <VALUE>` 临时覆盖默认配置（可用键：`output-by-natural-language`/`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`，`<VALUE>`是配置值或者`on`/`off`）

## 输出格式

1. JSON 模式（默认）— stdout 仅输出 JSON，每行一个 JSON 对象，。所有非命令响应（守护进程启停信息、配置查询、帮助文本、警告等）也以 JSON 格式输出：
   - 命令响应：`{"type": "result", ...}` / `{"type": "ok", ...}` / `{"type": "error", ...}`
   - 守护进程信息：`{"type": "info", "message": "[pty-agent] 守护进程已启动 (端口 12345)"}`
   - 配置查询：`{"type": "config", "content": "当前调用配置:\n  ..."}`
   - 帮助文本：`{"type": "help", "content": "usage: pty-agent ..."}`
   - 警告信息：`{"type": "warning", "message": "..."}`
2. 自然语言模式 — 使用 `--output-by-natural-language` 切换

## 示例场景

### 长时运行进程监控 + 中途读取 + 终止

```bash
app.py exec srv -c "python server.py" --idle-timeout 3 # 启动，idle-timeout 使首次输出后快速返回
app.py read srv --lines 20 # 中途查看最近20行输出
app.py read srv --offset 1024 # 增量读取（从上次 offset 继续）
app.py read srv --grep "ERROR" # 只看错误行
app.py kill srv # 不再需要时终止
```

### 进程崩溃检测

```bash
app.py exec job -c "python worker.py" --idle-timeout 5 # 启动，idle-timeout 等待输出
# 若进程崩溃，返回中 trigger.reason="ended"，program.exit_code 非零，error_message 含崩溃信息
app.py events job --last 10 # 查看崩溃事件详情（process_crash 类型）
```
