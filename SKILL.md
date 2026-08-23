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

一次性检查命令（Python版本+依赖）：
```bash
python -c "import sys,importlib.util as u; print('[FAIL] Python >= 3.8 required, current:',sys.version.split()[0]) or sys.exit(1) if sys.version_info<(3,8) else print('[OK] Python',sys.version.split()[0]); deps=['cryptography','fastapi','uvicorn','starlette','websockets','yaml','numpy','av','psutil','wcwidth']+(['tomli'] if sys.version_info<(3,11) else []); missing=[d for d in deps if u.find_spec(d) is None]; print('[FAIL] Missing:',', '.join(missing)) or sys.exit(1) if missing else print('[OK] All dependencies installed')"
```

1. 最低 Python3.8。如果用户没安装 Python 或者版本或者版本太低，请从 https://winpython.github.io/ 拉取0dot（Windows优先选winpy） 或从 https://github.com/astral-sh/python-build-standalone/releases 拉取兼容版本，未经用户允许不要私自修改系统Path

2. 还要判断环境（Linux or Windows），因为wezterm-py的构建产物需要区分平台

3. 依赖安装了吗，没有就执行`pip install -r requirements.txt`或者创建venv

如果只有发布目录而没有源码，说明项目作者已经把 PTY-Agent 打包好了。发布构建脚本 `python BUILD.py` 产出自包含目录 `pty-agent/`（含 `src/`、`bin/`、`app.py`、`SKILL.md`），直接在该目录内用 `python app.py <args>` 运行；某些部署形态可能额外提供打包好的可执行文件，若存在则按提示直接调用即可（通常需 `.\` 前缀）

## 两种运行模式

### 终端模式

基于ConPTY，有TTY，**输出始终为终端屏幕快照**，等效于实际用户使用时真正看到的部分

- 适用：TUI 程序（vim/htop）、需要回显/行编辑的交互式程序
- 快照返回条件：trigger 匹配快照文本 / idle-timeout / timeout / 进程结束 / GUI 检测

注意：pty 模式默认没有Shell包装，直接执行`echo xxx`必定失败。若要使用Shell，请先使用`exec`新建pwsh/bash等，之后使用`send`发送命令

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
| `start/stop [options]` | 手动启动/停止守护进程；启动守护进程`exec`可直接启动，一般无需手动。未经用户运行，不要随便结束守护进程 | `stop --force` | |
| `status` | 查看守护进程状态 | | |
| `exec <new-session-id> <options>` | 执行命令以启动会话 | `-c "<command>"`(-c req), `-t "<regex>"`, `--cwd <path>`, `--env KEY=VALUE`, `--subprocess`, `--plugin <name>` | `exec id_py -c "python -i" -t ">>>"` |
| `send <session-id> <options>` | 发送输入到运行中的会话（原样，不转义） | `-i "<content>"`(-i req), `-e <lf|crlf|cr|none>`, `-t "<regex>"` | `send id_py -i "print(1)" -t ">>>"` |
| `advsend <session-id> <options>` | 发送输入到运行中的会话（JSON + 控制字符转义解码） | 同 `send` | `advsend server -i "{ctrl+c}" -e none` |
| `read <session-id> [options]` | 读取会话输出 | `-l <N>`, `-g "<regex>"`, `-o <path>` | `read myid -l 10` |
| `list` | 列出所有会话 | | |
| `kill <session-id>` | 终止会话 | | |
| `events <session-id> [options]` | 查看会话运行程序生命周期事件 | `-l <N>`, `--since <iso-datetime\|HH:MM>` | `events myid -l 10` |
| `closewin <session-id> <window-handle>` | 关闭 GUI 窗口；`<window-handle>`支持十进制或 0x十六进制| | |
| `mouse <session-id> <action>` | 发送鼠标动作到 PTY 会话 | `--button`, `--count`, `--ctrl`, `--shift`, `--alt`, `--grep` | `mouse myid click 10,5 --button right` / `mouse myid _get_cursor_location` |
| `wait [--timeout <seconds>]` | 恒等待指定秒数（守护进程侧等待） | `--timeout <seconds>` | `wait --timeout 5` |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排 | `run <file>`（`--vars K=V`, `--parallel N`） / `list` / `show <run-id>` / `cancel <run-id>` | `workflow run build.yaml`；`workflow show wf-1786777600000-1` |
| `attend <sid>` | 附加到某个会话（注意：这是给用户使用的不是给你用的）| | |
| `keygen [-f] [--key-dir <dir>] [-C <comment>]` | 生成 Ed25519 公私钥对（TLS 跨机认证用） | `-f`, `--key-dir <dir>`, `-C "<comment>"` | `keygen -C "user@host"` |
| `plugin <list\|ls\|attach\|detach\|cmd>` | 插件管理 | `plugin list` / `plugin ls <id>` / `plugin attach <id> <name>` / `plugin detach <id> <name>` / `plugin cmd <id> <name> <command> [args...]` | `plugin list` |
| `set-default <KEY> <VALUE>` | 覆盖全局默认配置（即只影响之后新建的会话的默认值） | | `set-default timeout 30` |

## *返回条件参数

命令执行后，程序满足设定的返回条件参数会携带消息返回，之后你可继续操作该会话

| 条件 | 参数 | 子进程模式 | 终端模式 | 提示 | 
|------|-----|----------| ------ | ----- |
| 都不带 | | **1s后返回** | **1s后返回** | |
| 只带 trigger | `-t/--trigger "<regex>"` | 增量输出流匹配到正则，兜底默认超时 | 屏幕变化行匹配到正则，兜底默认超时 | |
| trigger + newline | `-t "<regex>" --newline` | 换行后开始检查增量输出流匹配正则，兜底默认超时 | 换行后开始检查屏幕变化行匹配正则（输入回显行会先被剔除），兜底默认超时 | 终端有回显，如果你输入的字符会被正则匹配，建议使用`--newline`开启换行后检查 |
| 只带 idle-timeout | `--idle-timeout <seconds>` | 屏幕静默超时（在一段时间内无变化），兜底默认超时 | 屏幕静默超时（在一段时间内无变化），兜底默认超时 | |
| 只带 timeout | `--timeout <seconds>` | 指定时间后返回 | 指定时间后返回 | |
| 带 timeout + 其他条件 | 比如`-t "<regex>" --idle-timeout <seconds> --timeout <seconds>` |  命中其他条件，兜底超时 | 命中其他条件，兜底超时 | 注意！1.请不要将timeout设置为很大的值，否则若其他条件无法匹配就会卡死 2.建议如果要带其他条件，那就把timeout也带上并且设定合理的值，因为默认超时是120s |
| GUI 检测 | 检测到 GUI 窗口 | 检测到 GUI 窗口 | GUI窗口通常阻塞程序运行，需要处理 |
| 进程崩溃（退出码非） | 进程崩溃 | 进程崩溃 | |
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
| `--column <N>` | 不建议 | 取可见屏幕的第 N 列 |
| `-o/--output <path>` | 将输出结果写入指定文件 | 同左 |
| `--response-format <stream\|svg>` | 只支持stream | 选择响应格式 |
| `--svg-compression-level <0|1|2>` | | 本 SVG 压缩等级 |

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
                                   (过滤条件)
────────────────────────────────── lines:0:2 ───────────────────────────────────
Python 3.11.9 (tags/v3.11.9:de54cf5, Apr  2 2024, 10:12:12) [MSC v.1938 64 bit (
AMD64)] on win32
(输出结果)
────────────────────────────────────────────────────────────────────────────────
[read · ok · 0.00s]  py1  running  pty
[(命令类型) · (返回原因) · (执行时间)]  (sid)  (当前程序状态)  (运行模式)

(PTY-Agent message: 系统消息)

(hit: 系统提示)
```

## 引号处理规则（**你的**Shell命令行层）

- CMD（你的Shell，不是PTY-Agent的exec shell） 写 `\"` 嵌套： `-c "python -c \"print(1)\""`
- PowerShell/Pwsh **外层单引号**，请记住powershell外层用**单引号**，内层双引号： `app.py exec sid -c 'python -c "print(1)"'`

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

**运行TUI程序建议使用终端模式**
如果是简单的字符流程序，使用 `--subprocess` 子进程模式，只读取增量输出

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
2. 大部分语言的行结尾是`\n`，用`\r`就等者被readline吃掉吧！readline嗷呜嗷呜
0. 你是娇生惯养长大的，因为内核tty在后面帮你把行尾全部处理好了。现在你长大了，需要承担起配置行尾的责任，不能依靠内核tty了！

**需要精细控制输入时，请显式指定或更改默认值**

### read 用法

`python app.py read <session-id> [options]`

选项：
- 支持*返回条件参数
- 支持*返回结果处理参数

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

事件只记录进程（包括子进程）启动，进程停止，进程崩溃

`python app.py events <session-id> [options]`

选项：
- `-l/--last N` 最后 N 个事件
- `--since <iso-datetime\|HH:MM>` 只查看指定时间之后的事件
- `--until <iso-datetime\|HH:MM>` 只查看指定时间之后的事件

`--since`和`--until`可以一起用

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

插件管理：`list`/`ls`/`attach`/`detach`/`cmd`。插件注册在
`config/plugins/plugins.json`（`enabled` 总开关 + `plugins` 位置列表），
修改后需重启 daemon；也可用 `PTY_PLUGIN_DIRS` 环境变量追加插件位置。

```bash
python app.py plugin list                          # 列出已加载插件（daemon 侧 + CLI 侧）
python app.py plugin ls <session-id>               # 列出会话挂载的插件
python app.py plugin attach <session-id> <name>    # 动态挂载插件到运行中的会话
python app.py plugin detach <session-id> <name>    # 从会话卸载插件
python app.py plugin cmd <session-id> <name> <command> [args...]   # 调用插件命令钩子
```

### 全局/通用选项

- `--keep-ansi` （仅终端模式）通用子命令：保留完整VT序列（默认过滤掉终端颜色/样式码，只保留清屏/光标等控制序列，开启后保留全部）
- `--encoding <encoding>` 通用子命令：终端编码，乱码时设置`utf-8/gbk/gb2312/gb18030/big5`，指定一次后会自动记忆
- `--debug-output` 通用子命令：启用后响应中输出 debugInformation（进程树/GUI 窗口/事件）
- `--show-config [KEY]` 查看当前调用配置
- 以上命令只在本次调用中生效，如果需要之后不显式设定也可以缩小，需要指定默认值：
- `--default <KEY> <VALUE>` 通用子命令：覆盖默认配置
  - 可用键：`timeout`/`newline`/`keep-ansi`/`encoding`/`debug`/`send-eol`/`response-format`/`svg-compression-level`/`terminal-size`，`<VALUE>`是配置值或者`on`/`off`
  - 支持多个 `--default`
  - 默认配置按 session 持久化
  - `--default terminal-size NxN` 对**运行中的会话即刻生效**

## 插件

使用前清先加载

### files

```bash
python app.py file <read|write|edit|grep|glob|upload|download> ... -s <session-id>
```

见`config\plugins\files\USAGE.md`

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
app.py exec mimo -c "mimo.exe --trust" --timeout 10 # 启动 TUI 程序，5秒后返回屏幕快照
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

用法
```bash
terminal_injector.exe --list-targets --json # 查看可劫持的窗口
terminal_injector.exe --mediator --target-pid $pid # 劫持

# 接入PTY-Agent
app.py exec sid -c "terminal_injector.exe --mediator --target-pid $pid" --timeout 10
```

---

@附录：workflow 用法

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
`depends_on` 引用存在且无环、`on_error`/`retry`/`max_parallel` 取值合法、定义文件上限 1 MB。

**步骤类型**：

- `exec` — 启动/附加会话（`session`+`command` 必填）
  - 返回条件：`trigger`（正则，命中返回）/ `timeout`（默认 120）/ `idle_timeout`（输出静默）
  - 环境：`cwd`、`env`（KEY=VALUE 列表）、`size`（"120x40"）/`cols`/`rows`
  - `mode`：`pty`（默认，屏幕快照）/ `subprocess`（增量输出 + stderr 分离）
  - 输出：`full` / `keep_ansi` / `snapshot_diff`
  - 语义：同名会话仍在运行 → 直接附加；已结束 → 步骤失败
  - 结果：`output` 为返回时终端快照；`reason` 为返回原因（trigger_matched /
    trigger_timeout / idle_timeout / program_ended / program_crashed / gui_detected / ok）
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

**变量、插值与条件**：

- 全局变量：`vars` 定义，`--vars KEY=VALUE` 启动时覆盖（优先级更高），以 `vars.<name>` 引用
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
    session: test
    input: "cd {{vars.repo}} && make test\n"
    trigger: "PASS|FAIL|error"
    depends_on: [build]
  - id: report
    type: read
    session: test
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
