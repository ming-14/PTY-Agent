---
name: pty-agent
description: "Interactive CLI program proxy via pseudo-terminal (PTY). Use when: (1) must maintain ongoing dialog with interactive programs (REPLs, debuggers, Servers) — send input and wait for specific prompts; (2) process may block, crash, or pop up GUI windows — need real-time state detection; (3) simulating user interaction tests; (4) download large files. DO NOT use for: non-interactive scripts, web/HTTP API calls, GUI interfaces. If a plain script suffices, do not use this tool."
---

[TOC]

# PTY-Agent

PTY-Agent 是一个**命令行交互式程序交互代理**，通过伪终端（PTY）与交互式 CLI 程序双向通信

原理：程序后台有运行一个守护进程，执行命令时需要再次调用程序，程序会call守护进程对对应CLI进行操作：你 <-> PTY-Agent <-> 守护进程 <-> PTY

程序位于`app.py`，运行方法：`python app.py <args>`，或者包执行等其他方法

## 环境要求

1. 最低 Python3.8。如果用户没安装 Python 或者版本或者版本太低，请从 https://winpython.github.io/ 拉取0dot（Windows优先选winpy） 或从 https://github.com/astral-sh/python-build-standalone/releases 拉取兼容版本，未经用户允许不要私自修改系统Path

2. 还要判断环境，因为wezterm-py的构建产物需要区分平台，请检查wezterm-py产物架构是否属于你的平台

如果只有发布目录而没有源码，说明项目作者已经把 PTY-Agent 打包好了。发布构建脚本 `python BUILD.py` 产出自包含目录 `pty-agent/`（含 `src/`、`bin/`、`app.py`、`SKILL.md`），直接在该目录内用 `python app.py <args>` 运行；某些部署形态可能额外提供打包好的可执行文件，若存在则按提示直接调用即可（Windows 下通常需 `.\` 前缀）

## 两种运行模式

### pty 模式（默认，终端）

基于ConPTY，有TTY，**输出始终为终端屏幕快照**，等效于实际用户使用时真正看到的部分

- 适用：TUI 程序（vim/htop）、需要回显/行编辑的交互式程序
- 快照返回条件：trigger 匹配快照文本 / idle-timeout / timeout / 进程结束 / GUI 检测

注意：pty 模式默认没有Shell包装，直接执行`echo xxx`必定失败。若要使用Shell，请先使用`exec`新建pwsh/bash等，之后使用`send`发送命令

### 子进程模式（--subprocess）

用 `subprocess.Popen` 直接捕获子进程的 **stdout 与 stderr**（无伪终端），
**增量输出**，无终端回显、无快照、无 resize。

- 适用：普通命令行程序（编译、脚本、下载），只需追踪输出流
- `exec`/`send`/`read` 返回增量文本；`read` 支持 `--offset` 增量读取
- 输入通过写 stdin

注意：不要使用子进程模式运行需要TTY的会话！

## 程序返回条件

命令执行后，程序满足条件会携带消息返回，之后你可继续操作该会话

子进程模式：

| 模式 | 条件 | 返回信息 |
|------|------|----------|
| 都不带 | **1s后返回** | 增量输出 |
| 带trigger | 增量输出流匹配到正则 | 增量输出 |
| 带idle-timeout | 屏幕静默超时（在一段时间内无变化） | 增量输出 |
| 带timeout | 达到超时 | 增量输出 |
| 有指定返回条件 | 但是达到了默认超时 | 增量输出 |
| GUI检测（仅无trigger等待） | 检测到GUI窗口 | GUI窗口信息+增量输出 |
| | 有进程崩溃 | 相关事件+增量输出 |
| | 程序退出 | 残余增量输出 |

屏幕快照模式：

| 模式 | 条件 | 返回信息 |
|------|------|----------|
| 都不带 | **1s后返回** | 屏幕快照 |
| 带trigger | 屏幕变化行匹配到正则 | 屏幕快照 |
| 带idle-timeout | 屏幕静默超时（在一段时间内无变化） | 屏幕快照 |
| 带timeout | 达到超时 | 屏幕快照 |
| 有指定返回条件 | 但是达到了默认超时 | 屏幕快照 |
| GUI检测（等待模式下） | 检测到GUI窗口 | GUI窗口信息+屏幕快照 |
| | 有进程崩溃 | 相关事件+屏幕快照 |
| | 程序退出 | 最后的屏幕快照 |

注：**高效利用本程序的条件返回功能，及时根据对应程序的输出结果更新条件（特别是`-t`），灵活使用不同的返回条件**。不建议反复`send`后又`read`，如果可以的话尽量一次性设置最强的返回条件

注：GUI 窗口检测仅在等待流程中生效；子进程模式带 `-t` 的 trigger 等待不检测 GUI。命中时 `triggerReturnReason="gui_detected"`

## 命令速查

| 命令 | 用途 | 典型选项 | 示例 |
|------|------|----------|------|
| `start/stop [options]` | 手动启动/停止守护进程；启动守护进程`exec`可直接启动，一般无需手动 | `stop --force` | |
| `status` | 查看守护进程状态 | | |
| `exec <new-session-id> <options>` | 执行命令以启动会话 | `-c "<command>"`(-c req), `-t "<regex>"`, `--timeout <seconds>`, `--cwd <path>`, `--env KEY=VALUE`, `--subprocess`, `--size WxH`, `-o <path>`, `--plugin <name>` | `exec id_py -c "python -i" -t ">>>"` |
| `send <session-id> <options>` | 发送输入到运行中的会话 | `-i "<content>"`(-i req), `-t "<regex>"`, `-j`, `-e <lf|crlf|cr|none>`, `--timeout <seconds>` | `send id_py -i "print(1)" -t ">>>"`；`send id_py -i "{ctrl+c}" -e none` |
| `read <session-id> [options]` | 读取会话输出 | `-l <N>`, `-g "<regex>"`, `-o <path>` | `read myid -l 10` |
| `list` | 列出所有会话 | | |
| `kill <session-id>` | 终止会话 | | |
| `events <session-id> [options]` | 查看会话运行程序生命周期事件 | `-l <N>`, `--since <iso-datetime\|HH:MM>` | `events myid -l 10` |
| `closewin <session-id> <window-handle>` | 关闭 GUI 窗口；`<window-handle>`支持十进制或 0x十六进制| | |
| `mouse <session-id> <action>` | 发送鼠标动作到 PTY 会话 | `--button`, `--count`, `--ctrl`, `--shift`, `--alt`, `--grep` | `mouse myid click 10,5 --button right` / `mouse myid _get_cursor_location` |
| `wait [--timeout <seconds>]` | 恒等待指定秒数（守护进程侧等待） | `--timeout <seconds>` | `wait --timeout 5` |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排：YAML 定义多步骤（exec/send/read/kill/wait），daemon 后台执行，支持依赖图并行/变量传递/条件判定/重试；执行状态可查询、可取消 | `run <file>`（`--vars K=V`, `--parallel N`） / `list` / `show <run-id>` / `cancel <run-id>` | `workflow run build.yaml`；`workflow show wf-1786777600000-1` |
| `keygen [-f] [--key-dir <dir>] [-C <comment>]` | 生成 Ed25519 公私钥对（TLS 跨机认证用） | `-f`, `--key-dir <dir>`, `-C "<comment>"` | `keygen -C "user@host"` |
| `plugin <list\|ls\|attach\|detach\|cmd>` | 插件管理 | `plugin list` / `plugin ls <id>` / `plugin attach <id> <name>` / `plugin detach <id> <name>` / `plugin cmd <id> <name> <command> [args...]` | `plugin list` |
| `set-default <KEY> <VALUE>` | 覆盖默认配置（sid会话级）；全局set-default一次只能配置一个 | | `set-default timeout 30` |

## 命令详解

### exec 用法

`python app.py exec <session-id> <options>`

选项基本与 send 一致，见上文命令速查典型选项
特殊选项：
- `-c "<command>"`(req) 执行的命令，必填
- `--force-pty-mode` 忽略命令中的 shell 操作符（`|`、`&&`、`>` 等）检测，强制执行
- `--cwd <path>` 子进程工作目录（默认取调用方 CLI 的当前目录）；如果与期望工作目录不一致，建议指定
- `--env KEY=VALUE` 子进程额外环境变量，可指定多个，合并到继承的环境中；适用于设置 `TERM`、`COLORTERM` 等终端能力变量
- `--subprocess` 子进程模式：Popen 捕获 stdout/stderr（非 PTY），增量输出 + stderr 分离，支持写 stdin，无 resize/快照
- `--size <WxH>` 终端尺寸（如 `120x40`，默认 `80x24`；仅 pty 模式，**仅会话创建时生效**；运行中调整请用 `--default terminal-size NxN`）
- `-o/--output <path>` 输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）

`-c "<command>"`必填

**运行TUI程序建议使用 pty 模式**（PTY 恒返回屏幕快照），否则读数据的时候终端内容可能损坏
如果是简单的字符流程序，使用 `--subprocess` 子进程模式，只读增量输出就很方便

#### 命中条件

与普通增量输出模式基本一致，要注意 `--trigger` 和 `--idle-timeout` 检测的是屏幕而不是增量输出流

注意所有的返回屏幕快照都不会消费outputOffset

使用`--keep-ansi`返回屏幕原始VT序列。不建议，需要获取终端颜色请用`--response-format svg`代替

### send 用法

`python app.py send <session-id> -i "<content>" [options]`

send 通过 `-i`/`--input` 参数（必填）指定要发送的输入文本，例如 `send id_py -i "print(1)" -t ">>>"`

选项：
- `-t/--trigger "<regex>"` 匹配正则
    - `--newline` — 程序输出换行后开始才检查正则触发条件, 与`-t`搭配
- `--timeout <seconds>` 等待超时（默认120s）
    - `--idle-after-first-output` 首次输出后才开始检测静默，与`--idle-timeout`搭配
- `--idle-timeout <seconds>` 输出静默超时，程序在指定时间内不输出时触发条件
- `--full` 返回全部内容（PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出；数据大，尽量用`-l N`）
- `-j/--json-escaping` **JSON + 控制字符转义模式**，将`<content>`先进行 JSON 反转移，再展开 `{...}` 控制字符语法
- `-e/--send-eol <lf|crlf|cr|none>` 末尾追加的行尾符（默认`cr`=`\r`，模拟终端 Enter 键；`lf`=`\n`；`crlf`=`\r\n`；`none`=不追加）
- `-s/--snapshot-diff` 仅返回屏幕变化的行，推荐启用
- `-o/--output <path>` 输出到文件
- `--response-format <stream|svg>` 响应格式选择
- `--svg-compression-level <0|1|2>` SVG 压缩等级
默认情况下，"<content>"是不转义输入，如果需要输入控制字符，必须`-j`进入转义模式。发送多行、使用控制字符必须进入转义模式

`-i "<content>" `必填

#### -j JSON + 控制字符转义模式

将`<content>`先进行 JSON 反转移（`\\ \" \n \t \r \uXXX ...`），再展开 `{...}` 控制字符语法。

控制字符语法：

| 写法 | 说明 |
|------|------|------|
| `{ctrl+a}` | Ctrl+字母 → ASCII 控制字符 |
| `{ctrl+alt+s}` | 修饰键用 `+` 连接；`alt` 前缀 ESC |
| `{enter}` | `\r`回车；精细控制输出下，请把行尾设为`none`，`\r`默认行尾添加不检测 `{enter}`，会和默认行尾冲突 |
| `{esc}` | 退出 |
| `{tab}` | 制表符 |
| `{backspace}` | 退格 |
| `{backtab}` | Shift+Tab |
| `{space}` | 空格；直接输入字符串` `就好了 |
| `{up}`/`{down}`/`{left}`/`{right}` | 方向键 |
| `{home}`/`{end}` | |
| `{pageup}`/`{pagedown}` | |
| `{insert}`/`{delete}` | |
| `{f1}`~`{f12}` |  功能键 |

字面量 `{` 或 `}` 使用反引号转义：`` `{ ``、`` `} ``。名称大小写不敏感。

在发送多行、使用控制字符时使用；发送控制字符时，强烈建议精细控制行尾：使用`-e none`

注意：终端换行是`\r`而不是`\n`，Python换行是`\n`或者`\r\n`若不是单独的`\r`

#### -o 文件输出

`exec`、`read`、`send` 和 `mouse` 支持 `--output/-o <path>` 将**返回的**瞬间输出渲染到文件：

| 后缀 | 格式 | 依赖 |
|------|------|------|
| `.svg` | 矢量图 |  |
| `.png` / `.jpg` / `.bmp` | 位图 | Pillow |
| `.txt` / `.log` / 其他 | 纯文本 |  |

注意，**输出的内容就是命令返回的outputStream，如果处于增量输出模式，那么输出到txt文件就只有增量部分**
增量输出模式获取完整输出请使用`--full`

```powershell
app.py exec vim -c "vim" --timeout 3 -o screen.svg
app.py send myid -i "dir" -t ">" -o output.txt
app.py read myid -o output.png
```

> 注：图片（svg、png等）输出需要 PTY 模式，否则只能使用txt

#### --response-format 响应格式

仅 PTY

| 格式 | 说明 | 依赖 |
|------|------|------|
| stream | 默认，JSON 格式输出 | |
| svg | 返回 SVG 矢量图（JSON 包装） | |

对有 ConPTY 的 session，尽管再非屏幕快照模式下，`--response-format svg` 会强制自动请求屏幕缓冲区数据

#### --svg-compression-level SVG 压缩等级

仅 PTY

| 等级 | 说明 | 依赖 |
|------|------|------|
| 0 | 不压缩，仅移除空标签 | |
| 1 | 轻度压缩（默认） | Python库：scour |
| 2 | 深度压缩 | Python库：scour |

影响所有svg输出的地方

#### -s 仅返回屏幕变化的行

仅 PTY

需屏幕快照模式，返回为 stream 格式

首次调用返回完整快照，后续只返回变化行，格式为 `行号:内容`

#### `<content>` 追加字符

`<content>`末尾自动追加行尾符（默认`\r`），可通过`--default send-eol`配置，`--send-eol <lf|crlf|cr|none>`覆盖本次：
- `cr`（默认）— 追加 `\r`（终端 Enter 键，PTY 模式下 TUI 程序的提交键）
- `lf` — 追加 `\n`
- `crlf` — 追加 `\r\n`
- `none` — 不追加（适用于TUI程序发送方向键等纯按键序列）

当输入已以 `\n` 或 `\r` 结尾时不重复追加

提示：操作ssh、gdb等 REPL 由于**本身就要在命令末尾敲换行**，所以不建议更改或显式指定追加字符，因为默认就是`\r`（终端换行）
需要精细控制输入时，可以显式指定或更改默认值

#### 引号处理规则（你的Shell命令行层）

- CMD（你的Shell，不是PTY-Agent的exec shell） 写 `\"` 嵌套： `-c "python -c \"print(1)\""`
- PowerShell/Pwsh **外层单引号**，内层双引号： `-c 'python -c "print(1)"'`

### read 用法

`python app.py read <session-id> [options]`

选项：
命中条件返回：
- `-t/--trigger "<regex>"` 匹配正则
    - `--newline` — 程序输出换行后开始才检查正则触发条件, 与`-t`搭配
- `--timeout <seconds>` 等待超时（默认120s）
    - `--idle-after-first-output` 首次输出后才开始检测静默，与`--idle-timeout`搭配
- `--idle-timeout <seconds>` 输出静默超时，程序在指定时间内不输出时触发条件
输出格式：
- 不使用参数 增量模式下返回增量（会推进offset），屏幕快照模式下返回屏幕快照
- `-l/--lines <N>` PTY 取最后 N 行（作用于含 scrollback 历史的全量）；子进程取最后 N 行
- `-l/--lines start:end` 范围行（PTY 作用于含 scrollback 历史的全量）
- `-g/--grep "<regex>"` 正则过滤
- `--offset <bytes>` 增量读取（仅子进程模式）：从指定字节开始读取
- `--full` 返回全部内容（PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出；数据大，尽量用`--lines N`，禁止在TUI等高刷程序使用该选项）
- `-s/--snapshot-diff` 仅返回屏幕变化的行
- `--column <N>` 输出第 N 列（必须用全名`--column`；仅read有此参数）
- `-o/--output <path>`：输出到文件
- `--response-format <stream|svg>`：响应格式选择

### mouse 用法

仅 PTY

`python app.py mouse <session-id> <action> [args] [options]`

坐标采用 **1-based `(col,row)`**，即 `col` 从 1 开始（左到右），`row` 从 1 开始（上到下）

支持的action：

| 动作 | 位置参数 | 说明 |
| ------ | ---------- | ------ |
| `click` | `--grep "<regex>"`/`<c,r>` | 单/双/三击；`--count`（1/2/3，默认 1） |
| `drag` | `<from> <to>` | 从起点拖动到终点，格式均为 `col,row` |
| `scroll` | `--grep "<regex>"`/`<c,r>` | 在指定坐标滚轮滚动 N 次 |
| `hover` | `--grep "<regex>"`/`<c,r>` | 鼠标悬停（移动无按钮） |
| `press` | `<c,r> <seconds>` | 长按指定秒数 |
| `grep` | `<pattern>` | 纯查询：返回屏幕快照中所有匹配的首/尾坐标 |
| `_get_cursor_location` | 无 | 查询：返回光标位置（col,row）及所在行完整内容 |

**位置参数可以用`--grep`代替，也非常推荐使用`--grep`代替坐标**
比如：`python app.py mouse myid click --grep "open"`，很方便

通用选项：

- `--button <left|right|middle>`：指定按钮（click/drag/press 有效），`--button` 默认 left
- `--count <1|2|3>`：单/双/三击（仅 click）
- `--ctrl` / `--shift` / `--alt`：修饰键，可组合
- `--grep <pattern>`：用正则匹配终端屏幕内容代替指定坐标

输出控制选项：

- `-t/--trigger <regex>`：命中正则后返回
- `--timeout <seconds>`：等待超时（默认 120）
- `--idle-timeout <seconds>`：输出静默超时
- `-s/--snapshot-diff`：仅返回屏幕变化的行
- `-o/--output <path>`：输出到文件
- `--response-format <stream|svg>`：响应格式选择

示例：

```powershell
# 单击 / 双击 / 三击
python app.py mouse myid click 10,5
python app.py mouse myid click 10,5 --button right --count 2 --ctrl --shift

# 拖拽
python app.py mouse myid drag 10,5 30,5 --button left

# 滚轮上下滚动 N 次
python app.py mouse myid scroll 10,5 down 3

# 悬停
python app.py mouse myid hover 10,5

# 长按 N 秒
python app.py mouse myid press 10,5 2.0 --button middle

# 纯 grep：返回所有匹配的首/尾坐标
python app.py mouse myid grep "Error"

# 获取光标位置及所在行内容
python app.py mouse myid _get_cursor_location

# 用 grep 自动定位并执行动作（单匹配时执行；多匹配时返回坐标不执行）
python app.py mouse myid click --grep "OK"

# 点击后等待输出并返回屏幕快照
python app.py mouse myid click 10,5 --timeout 5

# 点击后等待指定提示符
python app.py mouse myid click 10,5 -t ">>>" --timeout 10

# 非常推荐使用grep自动定位
```

不要把动作的`grep`和选项的`--grep <pattern>`弄混

`--grep` 行为：
- 单匹配：自动用匹配首坐标执行动作。
- 多匹配：不执行动作，返回所有 `{"start":{"col":x,"row":y},"end":...}` 坐标。
- 无匹配：返回错误。
而`grep` 动作本身为纯查询模式，始终返回坐标列表。

### events 用法

`python app.py events <session-id> [options]`

选项：
- `-l/--last N`
- `--since <iso-datetime\|HH:MM>`
- `--until <iso-datetime\|HH:MM>`

### workflow 用法

多步骤脚本编排：YAML 定义文件声明一系列步骤，daemon 后台调度执行。
适用：需要按序/并行编排多个 exec/send/read 操作的自动化流程
（构建流水线、TUI 冒烟、多会话协作）。详细文档见 `docs/WORKFLOW.md`。

```bash
python app.py workflow run <file>              # 启动（后台执行，立即返回 runId）
python app.py workflow run <file> --vars env=prod --parallel 2   # 覆盖变量/并行度
python app.py workflow list                     # 所有运行（含已结束）
python app.py workflow show <run-id>            # 运行状态：步骤状态+输出+日志
python app.py workflow cancel <run-id>          # 取消（等待中的步骤最快 0.1s 响应）
```

基本结构（YAML）：

```yaml
name: build
vars:                  # 全局变量，可被 --vars 覆盖
  repo: myrepo
max_parallel: 4        # 最大并行步骤数（默认 4）
steps:
  - id: clone          # 步骤唯一 id（也是表达式引用名）
    type: exec         # 步骤类型: exec/send/read/kill/wait
    session: clone
    command: "git clone ...{{vars.repo}}"
    trigger: "Cloning into|error"
  - id: build
    type: exec
    session: build
    command: "make"
    depends_on: [clone]    # 显式依赖；不写则隐式依赖前一个步骤（串行）
    if: "clone.reason == 'trigger_matched'"   # 条件判定，为假跳过
    on_error: continue      # 失败策略: fail(默认)/continue/ignore
    retry: 2                # 失败重试次数
```

要点：

- **步骤类型**：`exec`（session/command 必填；已运行会话直接附加）/ `send`（session/input
  必填；input 默认自动追加 `\r` 模拟 Enter，可用可选字段 `eol: lf|crlf|cr|none` 覆盖、
  `json: true` 启用 `{enter}`/`{ctrl+a}` 等转义展开，与 CLI send 语义一致）/ `read`（session 必填）/ `kill` / `wait`（seconds）
- **并行**：`depends_on: [a, b]` 显式依赖；`depends_on: []` 无依赖可与前序并行；
  依赖失败的步骤自动跳过（skipped）；依赖环解析期拒绝
- **变量/表达式**：字段支持 `{{vars.x}}` / `{{<step-id>.output}}` 插值；步骤结果
  暴露 `output`/`reason`/`exit_code`/`error` 四字段；`if` 为安全表达式
  （支持 `==`/`in`/`and`/`or`/比较，**不允许函数调用**），如 `'error' in build.output`
- **失败**：trigger 超时不算失败；`on_error=fail` 失败即终止整个 workflow，
  `continue` 继续调度，`ignore` 视为成功
- **会话生命周期**：workflow 创建的会话结束后保留，可用 kill 步骤或外部命令清理
- **限制**：运行记录上限 50（超限自动淘汰最旧终态）；定义文件上限 1 MB；
  运行状态仅存内存（daemon 重启即清空）

示例：先启动 REPL 等待提示符（`>>>`），发送代码等输出，再读结果：

```yaml
steps:
  - id: py
    type: exec
    session: py-repl
    command: "python -u -i"
    trigger: ">>>"
  - id: run
    type: send
    session: py-repl
    input: "print('hello workflow')"
    trigger: "hello workflow"
  - id: done
    type: read
    session: py-repl
    lines: 5
```

send 步骤需要发送快捷键/转义时用 `json: true`（如 `input: "{ctrl+c}"`），
需要控制行尾时用 `eol`（如 `input: "make -j8"` 后触发 `eol: none` 的按键步骤）。

### 全局/通用选项

- `--keep-ansi` 通用子命令：保留完整VT序列（默认过滤掉终端颜色/样式码，只保留清屏/光标等控制序列，开启后保留全部）
- `--encoding <encoding>` 通用子命令：终端编码，乱码时设置`utf-8/gbk/gb2312/gb18030/big5`，指定一次后会自动记忆
- `--debug-output` 通用子命令：响应中输出 debugInformation（进程树/GUI 窗口/事件），默认关闭
- `--show-config [KEY]` 查看当前调用配置
- `--default <KEY> <VALUE>` 通用子命令：覆盖默认配置（可用键：`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`/`send-eol`/`response-format`/`svg-compression-level`/`terminal-size`，`<VALUE>`是配置值或者`on`/`off`；支持多个 `--default`；按 session 持久化到守护进程）

`--default terminal-size NxN` 对**运行中的会话即刻生效**：exec/send/read/mouse 任一命令携带该配置时，daemon 检测到尺寸变化即对会话执行 resize（默认 `80x24`）；`--size WxH` 只在**会话创建时**生效（新会话初始尺寸）

### 总结

熟练运用各种返回条件来做到准确返回

禁止将`--timeout`设置成很大的值，必须合理设置

| 选项 | exec-pty | send-pty | read-pty | mouse-pty | exec-subprocess | send-subprocess | read-subprocess | events |
|------|----------|----------|----------|-----------|-----------------|-----------------|-----------------|--------|
| `-l/--lines` | — | — | 对含 scrollback 历史的全量内容过滤：`N`=末N行，`start:end`=范围 | — | — | — | 对增量输出行过滤：`N`=末N行，`start:end`=范围 | — |
| `--snapshot-diff/-s` | 返回启动后屏幕变化行 | 返回发送输入后的变化行 | 返回本次较上次快照的变化行 | 返回鼠标动作后的变化行 | 不支持 | 不支持 | 不支持 | — |
| `--idle-timeout` | 屏幕持续 N 秒无变化即返回快照 | 屏幕持续 N 秒无变化即返回 | 同左 | 同左 | 无新输出 N 秒即返回增量 | 同左 | 同左 | — |
| `--full` | 返回全部内容 = scrollback 历史 + 当前可见区 | 同左 | 同左 | — | 返回全部累积 stdout        | 同左 | 同左 | — |
| `--offset` | — | — | — | — | — | — | 从指定字节偏移增量读取输出（不可与等待模式同用） | — |
| `--trigger/-t` | 基于快照文本命中正则即返回 | 同左 | 同左 | 同左 | 基于增量输出命中正则即返回 | 同左 | 同左 | — |
| `--size` | 创建会话时设置终端尺寸 WxH（运行中调整用 `--default terminal-size`） | — | — | — | — | — | — | — |
| `--newline` | 仅换行后才检查触发条件 | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 | — |
| `--timeout` | 等待触发/首次输出的超时秒数 | 等待触发超时 | 同左 | 同左 | 等待触发超时 | 同左 | 同左 | — |
| `--idle-after-first-output` | 首次输出后才开始检测静默超时 | 同左 | 同左 | 同左 | 同左 | 同左 | 同左 | — |
| `--keep-ansi` | 快照保留 ANSI 颜色/样式码 | 同左 | 同左 | 同左 | 输出保留 ANSI 码 | 同左 | 同左 | — |
| `--grep/-g` | — | — | 正则过滤快照行 | 正则匹配屏幕内容以定位坐标（多匹配不动作） | — | — | 正则过滤增量输出行 | — |
| `-j/--json-escaping` | — | 对输入做 JSON 转义解码后写入 | — | — | — | 同左 | — | — |
| `-e/--send-eol` | — | 追加行尾符 lf/crlf/cr/none | — | — | — | 同左 | — | — |
| `-o/--output` | 写文件：文本(.txt/.log)/svg/图片 | 同左 | 同左 | 同左 | 仅文本文件 | 同左 | 同左 | — |
| `--response-format` | stream/svg（svg 需屏幕快照） | 同左 | 同左 | 同左 | 仅 stream | 同左 | 同左 | — |
| `--svg-compression-level` | svg 压缩等级 0/1/2 | 同左 | 同左 | 同左 | 无效 | 同左 | 同左 | — |
| `--column` | — | — | 对快照行取第 N 列（1-based） | — | — | — | 对输出行取第 N 列（1-based） | — |
| `--last/-l` | — | — | — | — | — | — | — | 仅返回最近 N 条事件 |
| `--since` | — | — | — | — | — | — | — | 仅返回此时间之后的事件（ISO/HH:MM） |
| `--until` | — | — | — | — | — | — | — | 仅返回此时间之前的事件（ISO/HH:MM） |
| `--plugin` | 挂载插件 | 不支持 | 同左 | 同左 | 挂载插件 | 不支持 | 同左 | 同左 |

## 插件

使用前清先加载

### files

```bash
python app.py file <read|write|edit|grep|glob|upload|download> ... -s <session-id>
```

见`config\plugins\files\USAGE.md`

### simple

极简模式输出，省Token

直接挂载即可

### state_check

提供基本状态查询，见`config\plugins\state_check\README.md`

### ai

对命令输出做二次 AI 分析

见`config\plugins\ai\README.md`

## 示例场景

### 长时运行**服务器**

```bash
app.py exec srv -c "python server.py" --timeout 10
app.py read srv -l 20 # 中途查看最近20行输出
app.py read srv # PTY 模式返回屏幕快照；子进程模式则从上次 offset 增量读取
app.py read srv -g "ERROR" # 只看错误行
app.py kill srv # 不再需要时终止
```

### **下载大文件**

```bash
app.py exec download1 -c "curl -O https://example.com/largefile.zip" --trigger "100%" --subprocess
app.py exec download1 -c "curl -O https://example.com/largefile.zip" --trigger "100%" --timeout 600 --idle-timeout 30 --subprocess # 如果下载很慢，用 --timeout 给足时间
app.py read download1 # 查看进度
app.py read download1 -t "100%|error|warning" --timeout 600 # 等待完成
app.py kill download1 # 下载完成后清理
```

编译也是同理

### **容易崩溃的程序**

```bash
app.py exec job1 -c "python worker.py" --idle-timeout 5 # 启动，idle-timeout 等待输出
# 若进程崩溃，返回中 triggerReturnReason="program_crashed"，program.exitCode 非零，errorMessage 含崩溃信息
app.py events job1 -l 10 # 查看崩溃事件详情（process_crash 类型）
```

### **TUI 程序**交互

```bash
app.py exec mimo -c "mimo.exe --trust" --default response-format svg --timeout 5  # 启动 TUI 程序，5秒后返回屏幕快照
app.py send mimo --send-eol cr --timeout 5 -i "j" -s # 发送按键，等5秒后返回快照
app.py read mimo -s
app.py send mimo -i "帮我写一个贪吃蛇游戏" -s
app.py kill mimo
```

### **调试器（cdb/gdb）**交互

```bash
app.py exec dbg1 -c '"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe" myapp.exe' -t ">" --subprocess
app.py send dbg1 -i "g" -t "0:000" # 继续执行
app.py send dbg1 -i "k" -t "0:000" # 查看调用栈
app.py send dbg1 -i "db esp L100" -t "0:000" # 查看内存
app.py send dbg1 -i "q" -t ">" # 退出调试器
app.py kill dbg1
```

## 小工具

### Terminal-Injector

强制性劫持已经运行的控制台程序供PTY-Agent使用。`bin/terminal_injector/terminal_injector.exe` 由发布构建（`python BUILD.py`）下载到该目录，源码仓库默认不携带

用法
```bash
terminal_injector.exe --list-targets --json # 劫持可劫持的窗口
terminal_injector.exe --mediator --target-pid $pid # 劫持

# 接入PTY-Agent
app.py exec sid -c "terminal_injector.exe --mediator --target-pid $pid" --default response-format svg --timeout 15
```