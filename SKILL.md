---
name: pty-agent
description: "Interactive CLI program proxy via pseudo-terminal (PTY). Use when: (1) must maintain ongoing dialog with interactive programs (REPLs, debuggers, Servers) — send input and wait for specific prompts; (2) process may block, crash, or pop up GUI windows — need real-time state detection; (3) simulating user interaction tests; (4) download large files. DO NOT use for: non-interactive scripts, web/HTTP API calls, GUI interfaces. If a plain script suffices, do not use this tool."
---

# PTY-Agent

PTY-Agent 是一个**命令行交互式程序交互代理**，通过伪终端（PTY）与交互式 CLI 程序双向通信

原理：程序后台有运行一个守护进程，执行命令时需要再次调用程序，程序会call守护进程对对应CLI进行操作

程序位于`app.py`，运行方法：`python app.py <args>`，或者包执行等其他方法

## 环境要求

最低 Python3.8。如果用户没安装 Python 或者版本或者版本太低，请从 https://winpython.github.io/ 拉取0dot（Windows优先选winpy） 或从 https://github.com/astral-sh/python-build-standalone/releases 拉取兼容版本，未经用户允许不要私自修改系统Path

如果 SKILL 目录只有可执行文件，没有 Python 文件，可能说明 Rikka（项目作者）已经把 PTY-Agent 打包好了，此时你直接用可执行文件就好。注意 Shell 执行可执行文件通常要用`.\`：`.\pty-agent.exe <args>`

## 命令速查

| 命令 | 用途 | 典型选项 | 示例 |
|------|------|----------|------|
| `start/stop [options]` | 手动启动/停止守护进程；启动守护进程`exec`可直接启动，一般无需手动 | `stop --force` | |
| `status` | 查看守护进程状态 | | |
| `exec <new-session-id> <options>` | 执行命令以启动会话 | `-c "<command>"`(-c req), `-t "<regex>"`, `--timeout <seconds>`, `--cwd <path>`, `--env KEY=VALUE`, `--snapshot-mode`, `--size WxH`, `-o <path>` | `exec id_py -c "python -i" -t ">>>"` |
| `send <session-id> "<content>" [options]` | 发送输入到运行中的会话 | `-t "<regex>"`, `-j`, `-e <lf|crlf|cr|none>`, `--timeout <seconds>`, `--snapshot` | `send id_py "print(1)" -t ">>>"`；`send id_py "{ctrl+c}" -e none` |
| `read <session-id> [options]` | 读取会话输出 | `-l <N>`, `-g "<regex>"`, `--snapshot`, `-o <path>` | `read myid -l 10` |
| `list` | 列出所有会话 | | |
| `kill <session-id>` | 终止会话 | | |
| `events <session-id> [options]` | 查看会话运行程序生命周期事件 | `-l <N>`, `--since <iso-datetime\|HH:MM>` | `events myid -l 10` |
| `closewin <session-id> <window-handle>` | 关闭 GUI 窗口；`<window-handle>`支持十进制或 0x十六进制| | |
| `mouse <session-id> <action>` | 发送鼠标动作到 PTY 会话 | `--button`, `--count`, `--ctrl`, `--shift`, `--alt`, `--grep` | `mouse myid click 10,5 --button right` / `mouse myid _get_cursor_location` |
| `set-default <KEY> <VALUE>` | 覆盖默认配置（sid会话级）；全局set-default一次只能配置一个 | | `set-default timeout 30` |

### 两种输出状态

#### In 屏幕快照模式

强烈建议在TUI程序启用，改模式只返回终端屏幕快照（`send`/`read`/`mouse`），**等效于实际用户使用时真正看到的部分**

- 进入方式：在 exec 使用`--snapshot-mode`，等效之后执行`--default always-return-snapshot on`
在非屏幕快照模式下，你也可以在`send`/`read`/`mouse`显式指定`--snapshot`临时进入屏幕快照模式

#### In 增量模式

适合普通的不对终端有特殊处理的程序

- 进入方式：默认

## exec 用法

`python app.py exec <session-id> <options>`

选项基本与 send 一致，见上文命令速查典型选项
特殊选项：
- `-c "<command>"`(req) 执行的命令，必填
- `--force-pty-mode` 忽略命令中的 shell 操作符（`|`、`&&`、`>` 等）检测，强制执行
- `--cwd <path>` 子进程工作目录，不填则默认为调用者（客户端）的工作目录；如果与期望工作目录不一致，建议指定
- `--env KEY=VALUE` 子进程额外环境变量，可指定多个，合并到继承的环境中；适用于设置 `TERM`、`COLORTERM` 等终端能力变量
- `--snapshot-mode` 屏幕快照模式，启用后始终等待固定时间后返回终端屏幕快照
- `--size <WxH>` 终端尺寸（如 `120x40`，默认 `80x24`）
- `-o/--output <path>` 输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，需 Pillow）

`-c "<command>"`必填

**运行TUI程序强烈建议使用屏幕快照模式**（exec指定`--snapshot-mode`）,否则读数据的时候终端炸了后果自负
如果是简单的字符流程序没必要启用屏幕快照模式，只读增量输出就很方便

#### 命中条件

与普通增量输出模式基本一致，要注意 `--trigger` 和 `--idle-timeout` 检测的是屏幕而不是增量输出流

注意所有的返回屏幕快照都不会消费outputOffset

使用`--keep-ansi`返回屏幕原始VT序列。不建议，需要获取终端颜色请用`--response-format svg`代替

## send 用法

`python app.py send <session-id> "<content>" [options]`

send 没有`-i`/`--input`参数，直接加`"<content>"`

选项：
- `-t/--trigger "<regex>"` 匹配正则
    - `--newline` — 程序输出换行后开始才检查正则触发条件, 与`-t`搭配
- `--timeout <seconds>` 等待超时（默认120s）
    - `--idle-after-first-output` 首次输出后才开始检测静默，与`--idle-timeout`搭配
- `--idle-timeout <seconds>` 输出静默超时，程序在指定时间内不输出时触发条件
- `--full` 返回终端全部数据（数据大，尽量用`-l N`）
- `-j/--json-escaping` **JSON + 控制字符转义模式**，将`<content>`先进行 JSON 反转移，再展开 `{...}` 控制字符语法
- `-e/--send-eol <lf|crlf|cr|none>` 末尾追加的行尾符（默认`cr`=`\r`，模拟终端 Enter 键；`lf`=`\n`；`crlf`=`\r\n`；`none`=不追加）
- `--snapshot` 本次返回终端屏幕快照而非原始 VT 序列输出（注意`--snapshot`不会消费outputOffset；非 snapshot-mode 会话也可使用）
- `-s/--snapshot-diff` 仅返回屏幕变化的行
- `-o/--output <path>` 输出到文件
- `--response-format <stream|svg>` 响应格式选择
- `--svg-compression-level <0|1|2>` SVG 压缩等级

默认情况下，"<content>"是不转义输入，如果需要输入控制字符，必须`-j`进入转义模式。发送多行、使用控制字符必须进入转义模式

### 程序返回条件

增量输出模式：

| 模式 | 条件 | 返回信息 |
|------|------|----------|
| 都不带 | **1s后返回** | 增量输出 |
| 带trigger | 增量输出流匹配到正则 | 增量输出 |
| 带idle-timeout | 屏幕静默超时（在一段时间内无变化） | 增量输出 |
| 带timeout | 达到超时 | 增量输出 |
| 有指定返回条件 | 但是达到了默认超时 | 增量输出 |
| 未关闭GUI检测 | 检测到GUI窗口 | GUI窗口信息+增量输出 |
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
| 未关闭GUI检测 | 检测到GUI窗口 | GUI窗口信息+屏幕快照 |
| | 有进程崩溃 | 相关事件+屏幕快照 |
| | 程序退出 | 最后的屏幕快照 |

注：**高效利用本程序的条件返回功能，及时根据对应程序的输出结果更新条件（特别是`-t`），灵活使用不同的返回条件**。不建议反复`send`后又`read`，如果可以的话尽量一次性设置最强的返回条件

### -j JSON + 控制字符转义模式

将`<content>`先进行 JSON 反转移（`\\ \" \n \t \r \uXXX ...`），再展开 `{...}` 控制字符语法。

控制字符语法：

| 写法 | 输出 | 说明 |
|------|------|------|
| `{ctrl+a}` | `\x01` | Ctrl+字母 → ASCII 控制字符 |
| `{ctrl+alt+s}` | `\x1b\x13` | 修饰键用 `+` 连接；`alt` 前缀 ESC |
| `{enter}` | `\r` | 回车；精细控制输出下，请把行尾设为`none`，`\r`默认行尾添加不检测 `{enter}`，会和默认行尾冲突 |
| `{esc}` | `\x1b` | 退出 |
| `{tab}` | `\t` | 制表符 |
| `{backspace}` | `\x7f` | 退格 |
| `{backtab}` | `\x1b[Z` | Shift+Tab |
| `{space}` | `\x20` | 空格；直接输入字符串` `就好了 |
| `{up}`/`{down}`/`{left}`/`{right}` | `\e[A`/`\e[B`/`\e[D`/`\e[C` | 方向键 |
| `{home}`/`{end}` | `\e[1~`/`\e[4~` | |
| `{pageup}`/`{pagedown}` | `\e[5~`/`\e[6~` | |
| `{insert}`/`{delete}` | `\e[2~`/`\e[3~` | |
| `{f1}`~`{f12}` | `\eOP`~`\e[24~` | 功能键 |

字面量 `{` 或 `}` 使用反引号转义：`` `{ ``、`` `} ``。名称大小写不敏感。

在发送多行、使用控制字符时使用；发送控制字符时，强烈建议精细控制行尾：使用`-e none`

注意：终端换行是`\r`而不是`\n`，Python换行是`\n`或者`\r\n`若不是单独的`\r`

### -o 文件输出

`exec`、`read`、`send` 和 `mouse` 支持 `--output/-o <path>` 将**返回的**瞬间输出渲染到文件：

| 后缀 | 格式 | 依赖 |
|------|------|------|
| `.svg` | 矢量图 |  |
| `.png` / `.jpg` / `.bmp` | 位图 | Pillow |
| `.txt` / `.log` / 其他 | 纯文本 |  |

注意，**输出的内容就是命令返回的outputStream，如果处于增量输出模式，那么输出到txt文件就只有增量部分**
增量输出模式获取完整输出请使用`--full`，想拿屏幕快照的Stream请使用`--snapshot`

```powershell
app.py exec vim -c "vim" --snapshot-mode --timeout 3 -o screen.svg
app.py send myid "dir" -t ">" -o output.txt
app.py read myid --snapshot -o output.png
```

> 注：图片（svg、png等）输出需要屏幕快照模式

### --response-format 响应格式

| 格式 | 说明 | 依赖 |
|------|------|------|
| stream | 默认，JSON 格式输出 | |
| svg | 返回 SVG 矢量图（JSON 包装） | |

对有 ConPTY 的 session，尽管再非屏幕快照模式下，`--response-format svg` 会强制自动请求屏幕缓冲区数据

### --snapshot 本次进入屏幕快照模式

屏幕快照模式下，输出将变为屏幕状态而不是增量

### --svg-compression-level SVG 压缩等级

| 等级 | 说明 | 依赖 |
|------|------|------|
| 0 | 不压缩，仅移除空标签 | |
| 1 | 轻度压缩（默认） | Python库：scour |
| 2 | 深度压缩 | Python库：scour |

影响所有svg输出的地方

### -s 仅返回屏幕变化的行

需屏幕快照模式，返回为 stream 格式

首次调用返回完整快照，后续只返回变化行，格式为 `行号:内容`

### `<content>` 追加字符

`<content>`末尾自动追加行尾符（默认`\r`），可通过`--default send-eol`配置，`--send-eol <lf|crlf|cr|none>`覆盖本次：
- `cr`（默认）— 追加 `\r`（终端 Enter 键，PTY 模式下 TUI 程序的提交键）
- `lf` — 追加 `\n`
- `crlf` — 追加 `\r\n`
- `none` — 不追加（适用于TUI程序发送方向键等纯按键序列）

当输入已以 `\n` 或 `\r` 结尾时不重复追加

提示：操作ssh、gdb等 REPL 由于本身就要在命令末尾敲换行，所以不建议更改或显式指定追加字符，因为默认就是`\r`（终端换行）
需要精细控制输入时，可以显式指定或更改默认值

### 引号处理规则（你的Shell命令行层）

- CMD（你的Shell，不是PTY-Agent的exec shell） 写 `\"` 嵌套： `-c "python -c \"print(1)\""`
- PowerShell/Pwsh **外层单引号**，内层双引号： `-c 'python -c "print(1)"'`

## read 用法

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
- `-l/--lines <N>` 最后 N 行
- `-l/--lines start:end` 范围行
- `-g/--grep "<regex>"` 正则过滤
- `--offset <bytes>` 增量：从指定字节开始读取
- `--full` 返回终端全部的输出数据（数据大，尽量用`--lines N`，禁止在TUI等高刷程序使用改选项）
- `--snapshot` 本次返回终端屏幕快照而非原始 VT 序列输出（注意`--snapshot`不会消费outputOffset；非 snapshot-mode 会话也可使用）
- `-s/--snapshot-diff` 仅返回屏幕变化的行
- `--column <N>` 输出第 N 列（必须用全名`--column`；仅read有此参数）
- `-o/--output <path>`：输出到文件
- `--response-format <stream|svg>`：响应格式选择

## mouse 用法

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
python app.py mouse myid click 10,5 --snapshot --timeout 5

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

## events 用法

`python app.py events <session-id> [options]`

选项：
- `-l/--last N`
- `--since <iso-datetime\|HH:MM>`
- `--until <iso-datetime\|HH:MM>`

## 全局/通用选项

- `--keep-ansi` 通用子命令：保留完整VT序列（默认过滤掉终端颜色/样式码，只保留清屏/光标等控制序列，开启后保留全部）
- `--encoding <encoding>` 通用子命令：终端编码，乱码时设置`utf-8/gbk/gb2312/gb18030/big5`，指定一次后会自动记忆
- `--no-debug` 通用子命令：禁用响应中的 debugInformation 输出（进程树/GUI 窗口/事件）
- `--show-config [KEY]` 查看当前调用配置
- `--default <KEY> <VALUE>` 通用子命令：覆盖默认配置（可用键：`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`/`send-eol`/`always-return-snapshot`/`response-format`/`svg-compression-level`/`terminal-size`，`<VALUE>`是配置值或者`on`/`off`；支持多个 `--default`；按 session 持久化到守护进程）

`terminal-size`即刻生效，运行中更改终端尺寸就用`--default terminal-size NxN`

## 示例场景

### 长时运行**服务器**

```bash
app.py exec srv -c "python server.py" --timeout 10
app.py read srv -l 20 # 中途查看最近20行输出
app.py read srv # 默认就是增量读取（从上次 offset 继续）
app.py read srv -g "ERROR" # 只看错误行
app.py kill srv # 不再需要时终止
```

### **下载大文件**

```bash
app.py exec download1 --command "curl -O https://example.com/largefile.zip" --trigger "100%"
app.py exec download1 --command "curl -O https://example.com/largefile.zip" --trigger "100%" --timeout 600 --idle-timeout 30 # 如果下载很慢，用 --timeout 给足时间
app.py read download1 # 查看进度
app.py read download1 --trigger "100%|error|warning" --timeout 600 # 等待完成
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
app.py exec mimo -c "mimo.exe --trust" --default response-format svg --snapshot-mode --timeout 5  # 启动 TUI 程序，5秒后返回屏幕快照
app.py send mimo --send-eol cr --timeout 5 "j" -s # 发送按键，等5秒后返回快照
app.py read mimo -s
app.py send mimo "帮我写一个贪吃蛇游戏" -s
app.py kill mimo
```

### **自定义终端尺寸**

```bash
# 宽屏终端（120列40行）
app.py exec wide -c "mimo.exe" --snapshot-mode --size 120x40 --timeout 5

# 用 --default 设置默认终端尺寸（即时生效）
app.py exec s1 -c "cmd" --default terminal-size 100x30

# 查看当前终端尺寸配置
app.py --show-config terminal-size
```

### **调试器（cdb/gdb）**交互

```bash
app.py exec dbg1 -c '"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe" myapp.exe' -t ">"
app.py send dbg1 "g" -t "0:000" # 继续执行
app.py send dbg1 "k" -t "0:000" # 查看调用栈
app.py send dbg1 "db esp L100" -t "0:000" # 查看内存
app.py send dbg1 "q" -t ">" # 退出调试器
app.py send kill dbg1
```

### TUI程序**测试***

写文件执行，作为e2e测试：
```

```
