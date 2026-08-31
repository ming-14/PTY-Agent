---
name: pty-agent
description: "Interactive CLI program proxy via pseudo-terminal (PTY). Use when: (1) must maintain ongoing dialog with interactive programs (REPLs, debuggers, Servers) — send input and wait for specific prompts; (2) process may block, crash, or pop up GUI windows — need real-time state detection; (3) simulating user interaction tests; (4) download large files."
---

[TOC]

# PTY-Agent

PTY-Agent 是一个**命令行交互式程序交互代理**，通过伪终端（PTY）与交互式 CLI 程序双向通信

原理：程序后台有运行一个守护进程，执行命令时需要再次调用程序，程序会call守护进程对对应CLI进行操作：你 <-> PTY-AgentCLI <-> PTY-Agent守护进程 <-> PTY

程序位于`app.py`，运行方法：`python app.py <args>`，或者包执行等其他方法

## 环境要求

Python>=3.8 requirements.txt

守护进程会向`~/pty-agent/`写日志和配置，若你在沙箱运行，请允许读写`~/pty-agent/`

出现守护进程报认证失败或其他启动错误，可以在用户允许的前提下，执行`app.py stop force`

## 两种运行模式

### 终端模式

基于伪终端，有TTY，**输出始终为终端屏幕快照**，等效于实际用户使用时真正看到的部分

- 适用：TUI 程序（vim/htop）、需要回显/行编辑的交互式程序
- 快照返回条件：trigger 匹配快照文本 / idle-timeout / timeout / 进程结束 / GUI 检测

注意：pty 模式默认没有Shell包装，若要使用Shell，请使用`--shell <shell>`

### 子进程模式（需显式--subprocess）

用 `subprocess.Popen` 直接捕获子进程的 **stdout 与 stderr**（无伪终端），
**增量输出**，无终端回显、无快照、无 resize。

- 适用：普通命令行程序（编译、脚本、下载），只需追踪输出流
- `exec`/`send`/`read` 返回增量文本；`read` 支持 `--offset` 增量读取
- **增量语义**：stdout/stderr 各有一个会话内消费游标（镜像关系）。`exec`/`send`
  与默认 `read` 从"上次交付末尾"续读，两次调用之间写入的输出不会丢失；
  `-l/--column/--full` 是累积查询（从保留起点读，**不**推进游标）。
  缓冲超限会从头裁剪最旧字节（游标前数据可能被丢弃，响应仍单调推进）
- 输入通过写 stdin

注意：不要使用子进程模式运行需要TTY的会话！

## 命令速查

| 命令 | 用途 | 典型选项 | 示例 |
|------|------|----------|------|
| `start/stop [options]` | 手动启动/停止守护进程；启动守护进程`exec`可直接启动，一般无需手动。未经用户运行，不要随便结束守护进程 | `stop --force`；`start --foreground`（前台/s6 监督）；`start --survive`（忽略结束信号与 stop，仅 SIGKILL 可终止） | |
| `status` | 查看守护进程状态 | | |
| `exec <new-session-id> <options>` | 执行命令以启动会话 | `-c "<command>"`(-c req), `-t "<regex>"`, `--cwd <path>`, `--env KEY=VALUE`, `--subprocess`, `--shell <shell>` | `exec id_py -c "python -i" -t ">>>"` |
| `send <session-id> <options>` | 发送输入到运行中的会话（原样，不转义） | `-i "<content>"`(-i req), `-e <lf|crlf|cr|none>`, `-t "<regex>"` | `send id_py -i "print(1)" -t ">>>"` |
| `advsend <session-id> <options>` | 发送输入到运行中的会话（JSON + 控制字符转义解码） | 同 `send` | `advsend server -i "{ctrl+c}" -e none` |
| `read <session-id> [options]` | 读取会话输出 | `-l <N>`, `-g "<regex>"`, `-o <path>` | `read myid -l 10` |
| `list` | 列出所有会话 | | |
| `kill <session-id>` | 终止会话；会话结束后会变成`ended`状态，使用`kill`命令彻底去除且移出会话列表 | | |
| `events <session-id> [options]` | 查看会话运行程序生命周期事件 | `-l <N>`, `--since <iso-datetime\|HH:MM>` | `events myid -l 10` |
| `closewin <session-id> <window-handle>` | 关闭 GUI 窗口（**仅 Windows**）；`<window-handle>`支持十进制或 0x十六进制| | |
| `mouse <session-id> <action>` | 发送鼠标动作到 PTY 会话 | `--button`, `--count`, `--ctrl`, `--shift`, `--alt`, `--grep` | `mouse myid click 10,5 --button right` / `mouse myid _get_cursor_location` |
| `wait [--timeout <seconds>]` | 等待：有待消费通知立即返回摘要，否则等待指定秒数（通知到达即唤醒）。`--timeout` 可选（默认 120） | `--timeout <seconds>` | `wait --timeout 5` |
| `notice <nid>` | 查看通知的完整内容（`nid` 为 32 位十六进制串，来自 `wait` 返回的摘要） | | `notice 3f9c2a8b...` |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排 | `run <file>`（`--vars K=V`, `--parallel N`） / `list` / `show <run-id>` / `cancel <run-id>` | `workflow run build.yaml`；`workflow show wf-1786777600000-1` |
| `attend <sid>` | 附加到某个会话（注意：这是给用户使用的不是给你用的）| | |
| `keygen [-f] [--key-dir <dir>] [-C <comment>]` | 生成 Ed25519 公私钥对（TLS 跨机认证用） | `-f`, `--key-dir <dir>`, `-C "<comment>"` | `keygen -C "user@host"` |
| `plugin <list\|ls\|attach\|detach\|cmd>` | 插件管理 | `plugin list` / `plugin ls <id>` / `plugin attach <id> <name>` / `plugin detach <id> <name>` / `plugin cmd <id> <name> <command> [args...]` | `plugin list` |
| `set-default <KEY> <VALUE>` | 覆盖全局默认配置 | | `set-default timeout 30` |
| `file <read\|write\|edit\|grep\|glob\|upload\|download> ... -s <session-id>` | 文件工具（读/写/编辑/搜索/上传/下载；`-s/--cwd-session` 必填，取该会话 cwd 作路径基准，不操作该会话） | `-s <session-id>`（req） | `file read src/main.py -s myapp` |

## *返回条件参数

命令执行后，程序满足设定的返回条件参数会携带消息返回，之后你可继续操作该会话

| 条件 | 参数 | 子进程模式 | 终端模式 | 提示 | 
|------|-----|----------| ------ | ----- |
| 都不带 | | **1s后返回** | **1s后返回** | |
| 只带 trigger | `-t/--trigger "<regex>"` | 增量输出流匹配到正则，兜底默认超时 | 屏幕变化行匹配到正则，兜底默认超时 | |
| trigger + newline | `-t "<regex>" --newline` | 换行后开始检查增量输出流匹配正则，兜底默认超时 | 换行后开始检查屏幕变化行匹配正则（输入回显行会先被剔除），兜底默认超时 | 终端有回显，如果你输入的字符会被正则匹配，建议使用`--newline`开启换行后检查 |
| 只带 idle-timeout | `--idle-timeout <seconds>` | 输出静默超时（在一段时间内无新输出），兜底默认超时 | 屏幕静默超时（在一段时间内无变化），兜底默认超时 | idle-timeout 从**最后输出到达**时开始计时；若程序 stdout 有块缓冲（如 `python -c` 未加 `-u`），输出可能延迟到达，idle 在缓冲 flush 前触发 → 返回空输出（数据未丢，可后续 read） |
| idle + 仅首次输出后检测 | `--idle-timeout <seconds> --idle-after-first-output` | 仅在程序首次输出后才开始检测静默 | 同左 | `--idle-after-first-output` 需配合 `--idle-timeout` 使用 |
| 只带 timeout | `--timeout <seconds>` | 指定时间后返回 | 指定时间后返回 | |
| 带 timeout + 其他条件 | 比如`-t "<regex>" --idle-timeout <seconds> --timeout <seconds>` |  命中其他条件，兜底超时 | 命中其他条件，兜底超时 | 注意！1.请不要将timeout设置为很大的值，否则若其他条件无法匹配就会卡死 2.建议如果要带其他条件，那就把timeout也带上并且设定合理的值，因为默认超时是120s |
| GUI 检测 | | 检测到 GUI 窗口 | 检测到 GUI 窗口 | GUI窗口通常阻塞程序运行，需要处理。**仅 Windows 原生模式生效**（Unix 不支持）；子进程模式 + trigger 时不触发 GUI 返回 |
| 进程崩溃（退出码非零） | 进程崩溃 | 进程崩溃 | |
| 程序退出（退出码0） | 程序退出 | 程序退出 | |

注：**高效利用本程序的条件返回功能，及时根据对应程序的输出结果更新条件（特别是`-t`），灵活使用不同的返回条件**。不建议反复`send`后又`read`，如果可以的话尽量一次性设置最强的返回条件

关于timeout：对于时间的预估应该基于执行的命令性质，不可以基于实际条件匹配状态。简单的命令预估执行快，但是可能因为其他原因导致条件未命中而拉长时间。此时绝对不可以设置大timeout或者执行长sleep，而是应该找未匹配的原因，及时取消执行

## *返回结果处理参数

条件命中后，程序会对结果进行处理再返回

| 选项 / 参数 | 子进程模式 | 终端模式 |
|-------------|------------|---------------------|
| `-l/--lines <N>` | 取累积输出的最后 N 行 | 取全量输出的最后 N 行 |
| `-l/--lines start:end` | 取累积输出中指定范围的行 | 取全量输出中指定范围的行 |
| `-g/--grep "<regex>"` | 不支持 | 用可见屏幕的每一行 |
| `--offset <bytes>` | 从指定字节偏移开始增量读取 | 不支持 |
| `--full` | 返回全部累积输出（数据量大，慎用） | 返回全量输出（数据量大，慎用） |
| `-s/--snapshot-diff` | 不支持 | 仅返回与上一次可见屏幕相比发生变化的行 |
| `--column <N>` | 支持（按字符位取第 N 列，短行取空） | 取可见屏幕的第 N 列 |
| `-o/--output <path>` | 将输出结果写入指定文件 | 同左 |
| `--response-format <stream\|svg>` | 只支持stream | 选择响应格式 |
| `--svg-compression-level <0/1/2>` | 不支持 | 本机 SVG 压缩等级 |

> 注：`-l` 的"累积/全量"语义仅对 `read` 成立；`exec`/`send` 的 `-l` 默认作用于增量交付块 / 可见屏幕快照，需再加 `--full` 才取全量输出。
>
> 注：`-g`（终端模式）与 `-s` 的输出均为 `行号:内容` 格式（0-based 行号）。

终端模式的全量输出：指包含 scrollback 历史的全量输出

### -o 文件输出

`--output/-o <path>` 将**返回的**瞬间输出渲染到文件：

支持的后缀： `.svg`、`.png` / `.jpg` / `.bmp`、`.txt` / 其他（其他后缀名等价txt纯文本）

注意，**输出的内容就是命令返回的outputStream，如果处于增量输出模式，那么输出到txt文件就只有增量部分**
增量输出模式获取完整输出请使用`--full`

> 注：图片（svg、png等）输出需要终端模式，否则只能使用txt

### --response-format 响应格式

仅终端模式支持调节

| 格式 | 说明 | 依赖 |
|------|------|------|
| stream | 默认 | |
| svg | SVG | |

### --svg-compression-level SVG 压缩等级

仅终端模式支持调节

| 等级 | 说明 | 依赖 |
|------|------|------|
| 0 | 不压缩，仅移除空标签 | |
| 1 | 轻度压缩（默认） | Python库：scour |
| 2 | 深度压缩 | Python库：scour |

影响所有svg输出的地方，包括结果输出，文件输出

### -s 仅返回屏幕变化的行

首次调用返回完整快照，后续只返回变化行，格式为 `行号:内容`

## 返回结果示例

```
                                   (分隔线内嵌过滤条件标签)
────────────────────────────────── lines:0:2 ───────────────────────────────────
Python 3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (
AMD64)] on win32
(输出结果)
────────────────────────────────────────────────────────────────────────────────
[read · ok · 0.00s]  py1  running  pty
[(命令类型) · (原因短标签) · (执行时间)]  (sid)  (当前程序状态)  (运行模式)

(PTY-Agent message: 系统消息)

(hit: 系统提示)
```

- 分隔线内嵌标签由响应格式生成：`-l N` 显示 `tail:N`（如 `tail:10`）；`-l start:end` 显示 `lines:A:B`；另见 `snapshot`/`diff`/`full`/`col:N`/`match:pattern` 等。
- 状态行中的原因显示为**短标签**：`ok`/`matched`/`timeout`/`idle`/`ended`/`crashed`/`gui`/`cancelled`/`notify`（`trigger_matched` 显示为 `matched`；崩溃时附加 `(exit_code: N)`）。
- `(PTY-Agent message: ...)` 为系统消息（info/warning/error，写入 stderr）；`(hit: ...)` 为提示信息（追加在输出末尾）。

## 引号处理规则（**你的**Shell命令行层）

- CMD（你的Shell，不是PTY-Agent的exec shell） 写 `\"` 嵌套： `-c "python -c \"print(1)\""`
- PowerShell/Pwsh **外层单引号**，请记住powershell外层用**单引号**，内层双引号： `app.py exec sid -c 'python -c "print(1)"'`

## 命令详解

### exec 用法

`python app.py exec <session-id> <options>`

选项基本与 send 一致，见上文命令速查典型选项
特殊选项：
- `-c "<command>"`(req) 执行的命令，必填
- `--force-pty-mode` 非 subprocess 模式且未指定 `--shell` 时，默认会检查命令中的 shell 操作符并拒绝执行（提示改用 `--shell`）；该选项忽略该检测，强制执行（操作符作为字面参数传递）
- `--cwd <path>` 子进程工作目录（默认取调用方 CLI 的当前目录）；如果与期望工作目录不一致，建议指定
- `--env KEY=VALUE` 子进程额外环境变量，可指定多个，合并到继承的环境中；适用于设置 `TERM`、`COLORTERM` 等终端能力变量
- `--subprocess` 子进程模式：Popen 捕获 stdout/stderr（非 PTY），增量输出 + stderr 分离，支持写 stdin，无 resize/快照
- `--size <WxH>` 终端尺寸（如 `120x40`，默认 `80x24`；仅 pty 模式，**仅会话创建时生效**；运行中调整请用 `--default terminal-size NxN`）
- `--shell <shell>` shell 模式，用指定 shell 包装执行命令（如 `bash`/`cmd`/`pwsh`），显式指定`--shell`优先级大于默认值
- `-o/--output <path>` 输出到文件（.txt/.log=纯文本; .svg=矢量图; .png/.jpg/.bmp=位图，daemon 侧渲染，客户端无需 Pillow）
- `--plugin <name>` 将插件挂载到该会话（可多次指定；按插件形态自动分流：CLI 形态记录到会话、会话/进程形态在 daemon 挂载）

**运行TUI程序建议使用终端模式**
如果是简单的字符流程序，使用 `--subprocess` 子进程模式，只读取增量输出

**--shell 示例**：
```
python app.py exec sid -c "echo a && echo b" --shell pwsh
```

### send / advsend 用法

`python app.py send <session-id> -i "<content>" [options]`
`python app.py advsend <session-id> -i "<content>" [options]`

* `send` 原样发送输入，简单输入，不做任何转义；
* `advsend` 参数与 `send` 完全一致，但**恒启用JSON + 控制字符转义解码**（`\n`、`\t`、`\uXXXX` 等 JSON 反转移 + `{ctrl+a}`、`{enter}`、方向键、`{f1}~{f12}` 等控制字符语法）。需要发送多行、控制字符或按键序列时，使用`advsend`；普通文本用 `send` 即可。

send 通过 `-i`/`--input` 参数（必填）指定要发送的输入文本，例如 `send id_py -i "print(1)" -t ">>>"`

选项：
- 支持*返回条件参数
- *返回结果处理参数
- `-e/--send-eol <lf|crlf|cr|none>` 末尾追加的行尾符（可选 `cr`=`\r`；`lf`=`\n`；`crlf`=`\r\n`；`none`=不追加）
- `--notify` 注册通知订阅：命令立即返回（reason=notify_waiting），后台等待条件满足时发布通知；之后用 `wait` 查看摘要、`notice <nid>` 查看完整内容

默认情况下，`send` 发送的"<content>"是不转义输入；如果需要输入控制字符、多行（`\r`），必须改用 `advsend`（恒转义）

#### advsend JSON + 控制字符转义模式

控制字符语法：

| 写法 | 说明 |
|------|------|------|
| `{ctrl+a}` | Ctrl+字母 → ASCII 控制字符 |
| `{ctrl+alt+s}` | 修饰键用 `+` 连接；`alt` 前缀 ESC |
| `{enter}` | 回车，终端模式代表`\r`、子进程模式代表`\n` |
| `{backtab}` | Shift+Tab |
| `{space}` | 空格；直接输入字符串` `就好了 |
| `{up}`/`{down}`/`{left}`/`{right}`、`{home}`/`{end}`、`{pageup}`/`{pagedown}`、`{insert}`/`{delete}`、`{f1}`~`{f12}`、`{esc}`、`{tab}`、`{backspace}` | |

字面量 `{` 或 `}` 必须使用反引号转义，单独的`{`/`}`会报错：`` `{ ``、`` `} ``。名称大小写不敏感。
子进程模式使用控制字符会原样把翻译后的字节注入stdin，是否响应仅看对方程序处理方法

在发送多行、使用控制字符时使用 `advsend`；若已用 `{enter}` 明确收尾，无需再配 `-e none`（默认行尾不会重复追加）

#### `<content>` 追加字符

`<content>`末尾自动追加行尾符（终端模式`\r`，子进程模式`\n`），可通过`--default send-eol`配置，`--send-eol <lf|crlf|cr|none>`覆盖本次：
- `cr` — 追加 `\r`（终端 Enter 键，pty 默认）
- `lf` — 追加 `\n`（subprocess 默认）
- `crlf` — 追加 `\r\n`
- `none` — 不追加，适合精细控制输入

当输入已以 `\n` 或 `\r` 结尾时不重复追加

注意：
1. 终端换行、多行请使用`\r`
2. 大部分语言的行结尾是`\n`，用`\r`不会触发 readline 断行（`\r`作为普通字符保留）
3. PTY 模式下内核 tty 会自动处理行尾（`\r`→`\n`转换）；子进程模式无此处理，需自行配置行尾符

**需要精细控制输入时，请显式指定或更改默认值**

### read 用法

`python app.py read <session-id> [options]`

选项：
- 支持*返回条件参数
- 支持*返回结果处理参数

### 通知（--notify / wait / notice）

`exec`/`send`/`advsend`/`read`/`mouse` 均可带 `--notify`：命令立即返回（reason=`notify_waiting`），后台线程继续等待返回条件，条件满足时发布一条通知。之后：

- `python app.py wait [--timeout <秒>]` 有待消费通知时**立即返回摘要列表**（无需等待）；无通知时才等待指定秒数（默认 120s，通知到达即唤醒）
- `python app.py notice <nid>` 查看某条通知的完整响应内容（nid 来自 `wait` 返回的 `notifications[].nid`）

通知存于守护进程内存（不落盘，daemon 重启即清空）。

### mouse 用法

仅终端模式

`python app.py mouse <session-id> <action> [args] [options]`

坐标采用 **1-based `(col,row)`**，即 `col` 从 1 开始（左到右），`row` 从 1 开始（上到下）

支持的action：

| 动作 | 位置参数 | 说明 |
| ------ | ---------- | ------ |
| `click` | `--grep "<regex>"`/`<c,r>` | 单/双/三击；`--count`（1/2/3，默认 1） |
| `drag` | `<from> <to>` | 从起点拖动到终点，格式均为 `col,row` |
| `scroll` | `--grep "<regex>"`/`<c,r>` | 滚轮滚动：`--direction <up\|down>`（默认 down）、`--times <N>`（默认 1，N>=1）；滚动为按下不抬起 |
| `hover` | `--grep "<regex>"`/`<c,r>` | 鼠标悬停（移动无按钮） |
| `press` | `<c,r> <seconds>` | 长按指定秒数 |
| `grep` | `<pattern>` | 纯查询：返回屏幕快照中所有匹配的首/尾坐标 |
| `_get_cursor_location` | 无 | 查询：返回光标位置（col,row）及所在行完整内容 |

**位置参数`<c,r>`可以用`--grep`代替，也非常推荐使用`--grep`代替坐标，防止手动定位错误**

选项：

- `--button <left|right|middle>`：指定按钮（click/drag/press 有效），`--button` 默认 left
- `--count <1|2|3>`：单/双/三击（仅 click）
- `--direction <up|down>`：滚动方向（默认 down，仅 scroll）
- `--times <N>`：滚动次数（默认 1，仅 scroll）
- `--ctrl` / `--shift` / `--alt`：修饰键，可组合
- `--grep <pattern>`：用正则匹配终端屏幕内容代替指定坐标

输出控制选项：
- 支持*返回条件参数
- 支持*返回结果处理参数

不要把动作的`grep`和选项的`--grep <pattern>`弄混

- `--grep` 行为：
  - 单匹配：自动用匹配首坐标执行动作。
  - 多匹配：不执行动作，返回所有对应坐标。
  - 无匹配：返回错误。
- 而`grep` 动作本身为纯查询模式，始终返回坐标列表。

### events 用法

事件记录进程（包括子进程）生命周期：启动（`process_spawn`）、停止（`process_exit`）、崩溃（`process_crash`）、GUI 窗口出现（`gui_window`）

`python app.py events <session-id> [options]`

选项：
- `-l/--last N` 最后 N 个事件
- `--since <iso-datetime\|HH:MM>` 只查看指定时间之后的事件
- `--until <iso-datetime\|HH:MM>` 只查看指定时间之前的事件

`--since`和`--until`可以一起用

注意：不传任何选项时只返回**未消费**的事件；查看完整历史请加 `-l`（如 `-l 10`）。

### workflow 用法

多步骤脚本编排：YAML 定义文件声明一系列步骤，daemon 后台调度执行。
适用：需要按序/并行编排多个 exec/send/read 操作的自动化流程
（构建流水线、TUI 冒烟、多会话协作）

见附录

### keygen 用法

生成 Ed25519 公私钥对，用于 CONNECT_MODE=tls 的跨机非对称认证。
密钥写入 `~/.pty-agent/keys/`（私钥 `id_ed25519` + 公钥 `id_ed25519.pub`），
生成后需把公钥追加到服务端 `~/.pty-agent/authorized_keys`。

```bash
python app.py keygen                          # 生成到 ~/.pty-agent/keys/
python app.py keygen -f                       # 覆盖已存在的密钥文件
python app.py keygen --key-dir ./mykeys       # 指定密钥目录
python app.py keygen -C "user@host"           # 公钥注释（默认 用户名@主机名）
```

选项：
- `-f/--force` 覆盖已存在的密钥文件（默认拒绝覆盖）
- `--key-dir <dir>` 密钥目录（默认 `~/.pty-agent/keys`，支持 `~`/`%TEMP%` 展开）
- `-C/--comment "<comment>"` 公钥注释（默认 `用户名@主机名`）

keygen 为本地命令，无需 daemon；Windows 下私钥自动收紧 ACL（仅当前用户 + SYSTEM + Administrators）。

### plugin 用法

插件管理。插件注册在 `config/plugins/registry.json`（`enabled` 总开关 + 各插件启用状态；registry.json 缺失则插件系统禁用），
插件目录发现 = 扫描 `config/plugins/` 下含 `plugin.json` 的目录，可用 `PTY_PLUGIN_DIRS` 环境变量追加位置。
目录级改动后需重启 daemon（或 `plugin reload <name>` 热重载）。

```bash
python app.py plugin list                          # 列出已加载插件（daemon 侧 + CLI 侧）
python app.py plugin ls <session-id>               # 列出会话挂载的插件
python app.py plugin attach <session-id> <name>    # 动态挂载插件到运行中的会话
python app.py plugin detach <session-id> <name>    # 从会话卸载插件
python app.py plugin cmd <session-id> <name> <command> [args...]   # 调用插件命令钩子
python app.py plugin install <path>                # 从目录安装插件（须含 plugin.json，不自动启用）
python app.py plugin uninstall <name>              # 卸载插件（须先 disable）
python app.py plugin enable <name>                 # 启用插件
python app.py plugin disable <name>                # 停用插件
python app.py plugin reload <name>                 # 热重载插件（重新加载代码与清单，保持启用状态）
python app.py plugin info <name>                   # 插件详情（清单/状态/路径/权限/事件）
python app.py plugin status <name>                 # 插件运行状态
python app.py plugin config <name> [key value]     # 查看/修改插件配置（仅内存，重启清空）
```

### file 用法

`file` 提供文件工具：读、写、唯一匹配替换、内容搜索、文件名匹配、上传、下载

```bash
python app.py file <read|write|edit|grep|glob|upload|download> ... -s <session-id>
```

`-s/--cwd-session` **必填**：指定某个会话，取它的 cwd 作为路径解析基准（不操作该会话）

| 子命令 | 用法 | 要点 |
| ------ | ---- | ---- |
| `file read <path> [--offset N] [--limit N]` | 读文件（带行号，默认 2000 行） | 超过 250KB / 图片拒绝；不存在时提示相似文件名；`--offset` 0-based |
| `file write <path> --content TEXT \| --content-file FILE` | 覆盖写/新建（自动建父目录） | **已存在文件必须先 `file read`**；外部修改后拒绝；内容相同拒绝；大文件用 `--content-file`（与 `--content` 互斥） |
| `file edit <path> --old TEXT \| --old-file FILE [--new TEXT \| --new-file FILE]` | 唯一匹配替换 | `--old` 空=新建（文件须不存在）；`--new` 空=删除；`--old` 须唯一匹配（未找到/重复均拒绝） |
| `file grep <pattern> [path] [--include GLOB] [--literal-text]` | 内容搜索 | rg 引擎优先，缺失自动降级纯 Python；`path` 缺省=会话 cwd |
| `file glob <pattern> [path]` | 文件名匹配 | rg 引擎优先，缺失自动降级纯 Python；支持 `**` 任意层级；`path` 缺省=会话 cwd |
| `file upload <local-path> <remote-path> [--force] [--timeout N]` | 上传本地文件/目录到会话侧（scp -r 语义） | `local-path` 为 CLI 本机路径，`remote-path` 由 daemon 按会话 cwd 解析（支持 `~`）；目标已存在且相同→跳过，不同→拒绝并提示 `--force`；`--timeout` 为整个传输总时限（默认 120s），超时中止并清理临时文件 |
| `file download <remote-path> <local-path> [--force] [--timeout N]` | 下载会话侧文件/目录到本地（scp -r 语义） | 与 upload 反向；`remote-path` 可为文件或目录；覆盖策略与 `--timeout` 同 upload |

**路径规则：** 相对路径基于 `-s` 会话的 cwd 拼接；`~` 按 daemon 用户展开；绝对路径原样使用；cwd 是会话创建时的值，shell 内 `cd` 后不更新；跨机场景（CLI 与 daemon 异机）语义依然正确——路径在 daemon 所在机器上解析。

**写保护状态机（read-before-write）：** `file write` / `file edit` 受读前写保护——文件已存在时必须先 `file read`（成功后记录读时刻）；写/编辑时若文件 mtime 晚于读时刻（期间被外部修改）→ 拒绝并提示；内容与现有内容相同 → 拒绝。状态在守护进程进程内保存，重启守护进程即失效。每次写操作在 `<DATA_DIR>/history.db` 的 `files_history` 表落版本链（initial → v1 → v2）。

**多行/含特殊字符内容：** 输入带换行、`\`、`"`、`'` 等字符的内容时，**必须**先调用本地 write 工具写中转文件，再使用 `--content-file` / `--old-file` / `--new-file` 传入（一次调用两个工具：write + file），避免 Shell 复杂转义与命令行长度上限。

**使用示例：**

```bash
# 先拉起一个会话作为 cwd 基准
python app.py exec sid_cwd -c "cmd" --cwd <path>

# 读 / 搜索 / 匹配
python app.py file read src/main.py -s sid_cwd --limit 50
python app.py file grep "def " src -s sid_cwd --include *.py
python app.py file glob "src/**/*.py" -s sid_cwd

# write 两步法（先本地 write 写中转文件，再 --content-file 传入）
python app.py file write out.txt -s sid_cwd --content-file tempfiles/_write_temp1.txt

# edit 三步法（本地 write 分别写 old/new 中转文件）
python app.py file edit src/main.py -s sid_cwd --old-file tempfiles/_editold_temp1.txt --new-file tempfiles/_editnew_temp1.txt

# 上传 / 下载
python app.py file upload ./local.txt remote_dir/ -s sid_cwd
python app.py file download remote_dir/local.txt ./local.txt --force -s sid_cwd
```

### set-default 用法

覆盖全局默认配置（影响**后续所有会话请求**的默认值，包括已存在会话的后续请求；仅 `shell` 键真正只对新建会话生效，`terminal-size` 对运行中会话即刻生效）。
默认配置存于**守护进程内存**（不写任何文件），daemon 重启即清空；命令返回时会列出当前全部默认值。

- `app.py set-default <KEY> <VALUE>` 通用子命令：覆盖默认配置（需 daemon 运行；daemon 未运行时报错，请先 `exec` 启动）
  - `<KEY>`可用键：`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`/`send-eol`/`response-format`/`svg-compression-level`/`terminal-size`/`shell`，`<VALUE>`是配置值或者`on`/`off`

### 全局/通用选项

- `--keep-ansi` （仅终端模式，exec/send/read/mouse 可用）通用选项：保留完整VT序列（默认过滤掉终端颜色/样式码及 OSC 序列，只保留清屏/光标等控制序列，开启后保留全部）
- `--encoding <encoding>` 通用选项：终端编码，乱码时设置`utf-8/gbk/gb2312/gb18030/big5`；本次调用进程内会记忆（不写盘、不跨调用持久化），如需跨调用默认请用 `set-default encoding` / `--default encoding`
- `--debug-output` 通用选项：启用后响应中输出 debugInformation（进程树/GUI 窗口/事件）
- `--show-config [KEY]` 查看当前调用配置
- 以上选项（`--keep-ansi`/`--encoding`/`--debug-output`/`--show-config`/`--default`）仅影响本次调用；如需影响后续所有会话的默认值，请使用 `set-default` 命令：
- `--default <KEY> <VALUE>` 通用选项：调整该会话的配置值
  - `<KEY>`可用键：`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`/`send-eol`/`response-format`/`svg-compression-level`/`terminal-size`，`<VALUE>`是配置值或者`on`/`off`
  - 支持多个 `--default`
  - 默认配置按 session 记在守护进程内存（不写盘，daemon 重启/session 结束即清空）
  - `--default terminal-size NxN` 不是配置，是实时调整终端尺寸，对**运行中的会话即刻生效**
  - `--default`不支持`shell`

## 插件

使用前请先挂载（state_check/ai 用 `exec --plugin <name>` 或 `plugin attach <session-id> <name>` 挂载到会话）。

### ai

对命令输出做二次 AI 分析（exec/send/read/mouse 响应），见`config/plugins/ai/README.md`

（另注：`config/plugins/` 下还有 `2048`（游戏）、`subagent`（子代理管理，注册 codebuddy/devin/opencode/claude/smartagent 命令等插件）

## 其他

Windows注意事项：
- 少用cmd
- 输入带空格的文文件时，建议使用 8.3 短路径

跨机使用注意事项：
exec时请**显式设定环境变量**，避免不同环境的冲突

## 示例场景

### 长时运行**服务器**

```bash
app.py exec srv -c "python server.py" --timeout 10
app.py read srv -l 20 # 中途查看最近20行输出
app.py read srv # PTY 模式返回屏幕快照；子进程模式则从上次 offset 增量读取（无返回条件时等待 1s）
app.py read srv -g "ERROR" # 只看错误行
app.py kill srv # 不再需要时终止
```

### **下载大文件**

```bash
app.py exec download1 -c "curl -O https://example.com/largefile.zip" --trigger "100%" --timeout 600 --idle-timeout 10 --subprocess # 如果下载很慢，用 --timeout 给足时间，但是必须设定--idle-timeout防止卡死
app.py read download1 # 查看进度
app.py read download1 -t "100%|error|warning" --timeout 600 --idle-timeout 10 # 等待完成
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
app.py exec mimo -c "mimo.exe --trust" --timeout 10 # 启动 TUI 程序，10秒后返回屏幕快照
app.py send mimo --send-eol cr -i "j" -s # 发送按键，1秒后返回快照
app.py read mimo -s
app.py send mimo -i "帮我写一个贪吃蛇游戏" -s --timeout 30 --idle-timeout 5
app.py kill mimo
```

### **调试器（cdb/gdb）**交互

```bash
app.py exec dbg1 -c '"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe" myapp.exe' -t ">" --subprocess --timeout 5
app.py send dbg1 -i "g" -t "0:000" --timeout 5 # 继续执行；timeout看情况设定
app.py send dbg1 -i "k" -t "0:000" --timeout 5 # 查看调用栈
app.py send dbg1 -i "db esp L100" --timeout 5 -t "0:000" # 查看内存
app.py send dbg1 -i "q" -t ">" --timeout 5 # 退出调试器
app.py kill dbg1
```

## 小工具

### Terminal-Injector

强制性劫持已经运行的控制台程序供PTY-Agent使用：`bin/terminal_injector/terminal_injector.exe`

用法：

```bash
terminal_injector.exe --list-targets --json # 查看可劫持的窗口
terminal_injector.exe --inject <pid>        # 注入 DLL
terminal_injector.exe --mediator --target-pid <pid> # 劫持桥接

# 接入 PTY-Agent
# 不建议执行`terminal_injector.exe --inject <pid>` ，而是直接使用 PTY-Agent 执行`app.py exec sid -c "terminal_injector.exe --mediator --target-pid <pid>" --timeout 10`
app.py exec sid -c "terminal_injector.exe --mediator --target-pid <pid>" --timeout 10
```

---

## @附录：workflow 用法

```bash
python app.py workflow run <file>              # 启动（后台执行，立即返回 runId）
python app.py workflow run <file> --vars env=prod --parallel 2   # 覆盖变量/并行度
python app.py workflow list                     # 所有运行（含已结束）
python app.py workflow show <run-id>            # 运行状态：步骤状态+输出+日志
python app.py workflow cancel <run-id>          # 取消（等待中的步骤最快 0.1s 响应）
```

定义文件结构（YAML）：

```yaml
name: build          # 可选，workflow 名称（show/list 显示用）
vars:                # 可选，全局变量（值限 str/int/float/bool），可被 --vars 覆盖
  repo: myrepo
max_parallel: 4      # 可选，最大并行步骤数（默认 4，可被 --parallel 覆盖）
steps:
  - id: clone        # 必填，步骤唯一 id（也是表达式引用名）
    type: exec       # 必填，步骤类型: exec/send/read/kill/wait
    session: clone
    command: "git clone ...{{vars.repo}}"
    trigger: "Cloning into|error"
  - id: build
    type: exec
    session: build
    command: "make"
    depends_on: [clone]    # 可选，显式依赖；不写则隐式依赖前一个步骤（串行）
    if: "clone.reason == 'trigger_matched'"   # 可选，条件表达式，为假跳过
    on_error: continue     # 可选，失败策略: fail(默认)/continue/ignore
    retry: 2               # 可选，失败重试次数（retry_interval 默认 1.0s）
```

解析期校验（run 时即报错，不产生运行）：id 非空唯一、type 合法且必填字段齐全、
`depends_on` 引用存在且无环、`on_error`/`retry`/`max_parallel` 取值合法、定义文件上限 20 MB。
（注：`trigger` 正则为**运行时编译**，解析期不校验，非法正则在执行时使步骤失败。）

**步骤类型**：

- `exec` — 启动/附加会话（`session`+`command` 必填）
  - 返回条件：`trigger`（正则，命中返回）/ `timeout`（默认 120）/ `idle_timeout`（输出静默）/ `idle_after_first_output`（仅首次输出后才检测静默）
  - 环境：`cwd`、`env`（**映射**，如 `KEY: VALUE`）、`encoding`、`size`（"120x40"）/`cols`/`rows`（同时给定时 `size` 优先）
  - `mode`：`pty`（默认，屏幕快照）/ `subprocess`（增量输出 + stderr 分离）
  - 输出：`full` / `keep_ansi`（`snapshot_diff` 仅 `read` 步骤支持）
  - 语义：同名会话仍在运行 → 直接附加；已结束 → 步骤失败
  - 结果：`output` 为返回时终端快照；`reason` 为返回原因（trigger_matched /
    trigger_timeout / idle_timeout / program_ended / program_crashed / gui_detected / ok /
    cancelled / notify_waiting）
- `send` — 向会话发送输入（`session`+`input` 必填，会话须已运行）
  - `trigger`/`timeout`/`idle_timeout` 等待返回（同 exec）
  - `eol: lf|crlf|cr|none` 行尾（默认按会话模式：pty=cr、subprocess=lf）；`json: true` 启用 `{enter}`/`{ctrl+a}` 转义展开（与 CLI advsend 语义一致，`{enter}` 按模式展开）
- `read` — 读取会话输出（`session` 必填，不存在 → 步骤失败）
  - `lines`（N 或 start:end）/ `grep`（正则）过滤；有 trigger/idle_timeout 时进入等待，否则立即返回当前快照
- `kill` — 终止会话（`session` 必填，终止整个进程树：Job Object / 进程组信号）
- `wait` — 固定等待（`seconds` 必填，float；分段睡眠 0.1s 粒度，可响应取消）

**依赖与并行**：

- 不写 `depends_on` → 隐式依赖前一个步骤（**串行**）
- `depends_on: [a, b]` → 等 a、b 都完成；`depends_on: []` → 无依赖可与前序并行
- 依赖失败/取消的步骤自动 skipped；依赖环解析期拒绝（返回循环路径）
- 并行度上限 `max_parallel`（定义，默认 4）或 `--parallel N`（CLI 覆盖，优先级更高）
- **同一 `session` 的步骤强制串行派发**（防止并发写输入/篡改触发条件互相踩踏）

**变量、插值与条件**：

- 全局变量：`vars` 定义，`--vars KEY=VALUE` 启动时覆盖（优先级更高，覆盖的值一律为字符串），以 `vars.<name>` 引用
- 步骤结果：已完成步骤以 id 引用，暴露 `output`/`reason`/`exit_code`/`error` 四字段
- 插值：任何字符串字段支持 `{{表达式}}`，执行前渲染（可引用已完成的步骤），如
  `"git clone {{vars.repo}}"`、`"print('{{build.output}}')"`
- `if` 为**表达式**（非插值），为假时步骤 skipped（note 标注原因）：
  - `"clone.reason == 'trigger_matched'"`、`"'error' in build.output"`、
    `"build.exit_code == 0 and not vars.skip"`
  - 安全求值器（AST 白名单）：仅字面量/名称/属性/下标、比较（`==` `!=` `<` `>` `in` `not in`）、
    布尔（`and`/`or`/`not`）、算术、容器；`true`/`false`/`null` 等价 True/False/None；
    **任何函数调用、属性方法执行一律拒绝**；变量缺失/语法非法使步骤失败

**失败、重试与错误策略**：

- 步骤失败 = 执行异常（会话创建失败、会话不存在、写入失败等）或错误响应；
  **trigger 超时不算失败**（正常返回 `reason=trigger_timeout`）
- `retry: N` 失败重试 N 次（最多尝试 N+1 次），`retry_interval` 间隔秒数（默认 1.0）
- `on_error`：
  - `fail`（默认）本步骤 failed → 终止整个 workflow（未开始步骤 skipped，run 状态 failed）
  - `continue` 本步骤 failed → workflow 继续调度；依赖本步骤的步骤 skipped
  - `ignore` 视为成功（note 记录被忽略的错误），依赖本步骤的步骤正常执行
- 失败沿依赖链传播；`on_error=ignore` 的步骤不传播失败

**状态模型与限制**：

- run：`running` → `done` / `failed` / `cancelled`
- step：`pending` → `running` → `done` / `failed` / `skipped` / `cancelled`
  （skipped：if 为假 / 依赖失败或取消 / workflow 终止；cancelled：运行时收到取消）
- `cancel` 置位取消事件，执行中的步骤最快 0.1s 内响应；已终态运行幂等
- 运行记录上限 50（超限自动淘汰最旧终态）；步骤输出保存上限 4096 字符（仅 show 日志）
- 运行状态仅存内存（daemon 重启即清空）；workflow 创建的会话结束后保留，可 kill 步骤或外部命令清理

示例 1 — 启动 REPL → 发代码 → 读结果（默认串行）：

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

示例 2 — 构建流水线（并行 + 条件 + 重试）：

```yaml
name: build-pipeline
vars:
  repo: myrepo
  tag: nightly
steps:
  - id: clone
    type: exec
    session: clone
    command: "git clone -b {{vars.tag}} https://example.com/{{vars.repo}}"
    trigger: "Cloning into|error|fatal"
  - id: deps
    type: exec
    session: deps
    command: "cd {{vars.repo}} && pip install -r requirements.txt"
    trigger: "Successfully installed|error"
    retry: 1
    depends_on: [clone]
  - id: build
    type: exec
    session: build
    command: "cd {{vars.repo}} && make -j8"
    trigger: "error|^make:"
    idle_timeout: 60
    depends_on: [clone]        # deps 与 build 均只依赖 clone → 并行
  - id: test
    type: send
    session: build          # 复用已创建的 build 会话执行 make test（send 步骤不能引用未创建的会话）
    input: "cd {{vars.repo}} && make test\n"
    trigger: "PASS|FAIL|error"
    depends_on: [build]
  - id: report
    type: read
    session: build          # 读取 make test 的输出
    grep: "FAIL"
    if: "test.reason == 'trigger_matched' and 'FAIL' in test.output"
    depends_on: [test]
  - id: cleanup
    type: kill
    session: deps
    on_error: ignore
    depends_on: [test]
```

send 步骤需要发送快捷键/转义时用 `json: true`（如 `input: "{ctrl+c}"`），
需要控制行尾时用 `eol`（如 `input: "make -j8"` 后触发 `eol: none` 的按键步骤）。

## @附录：文档

文档很大，用户明确要求再查看

配置文件使用方法请查看`docs\CONFIG.md`
插件开发请查看`‪docs\PLUGINS_API.md`
了解项目架构请查看`docs\ARCHITECTURE.md`

## @附录：沙箱

配置沙箱请查看`docs\CONFIG.md`

## @附录：远程执行命令，跨机开发

跨机开发请查看`docs\CONFIG.md`
