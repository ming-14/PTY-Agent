# PTY-Agent 命令行帮助文档

> PTY-Agent 是一个命令行交互式程序交互代理。通过伪终端 (PTY) 与交互式 CLI 程序双向通信，由后台守护进程 (daemon) 统一管理会话。支持触发返回、静默超时、进程崩溃检测、GUI 窗口检测、编码自动探测、终端屏幕快照、AI 二次分析、Ed25519 公私钥认证、TLS 跨机部署、VNC 远程桌面、Screenshare 屏幕串流等能力。

| 项目       | 值                                    |
| ---------- | ------------------------------------- |
| Python     | >= 3.8                                |
| 平台       | Windows 10+ (ConPTY) / Unix (os.openpty) |
| token 端口 | 10520                                |
| basic 端口 | 10521                                |
| web 端口   | 18766                                 |
| TLS 端口   | 18767                                 |

## 目录

1. [入口与脚本](#1-入口与脚本)
2. [全局选项](#2-全局选项)
3. [子命令一览](#3-子命令一览)
4. [子命令详细说明](#4-子命令详细说明)
   - 4.0 [workflow](#40-workflow---workflow-脚本编排) · 4.1 [start](#41-start---启动后台守护进程) · 4.2 [stop](#42-stop---停止后台守护进程) · 4.3 [status](#43-status---查看守护进程运行状态) · 4.4 [list](#44-list---列出所有活跃会话)
   - 4.5 [exec](#45-exec---启动或附加到会话) · 4.6 [send](#46-send---向运行中的会话发送输入) · 4.7 [read](#47-read---读取会话终端输出) · 4.8 [kill](#48-kill---终止指定会话)
   - 4.9 [events](#49-events---查看会话事件) · 4.10 [closewin](#410-closewin---关闭指定-gui-窗口) · 4.11 [mouse](#411-mouse---发送鼠标动作到-pty-会话) · 4.12 [wait](#412-wait---恒等待指定秒数)
   - 4.13 [keygen](#413-keygen---生成-ed25519-公私钥对) · 4.14 [set-default](#414-set-default---覆盖默认配置-会话级) · 4.15 [file](#415-file---文件工具) · 4.16 [plugin](#416-plugin---插件管理)
5. [公共选项参考](#5-公共选项参考)
6. [配置系统](#6-配置系统)
7. [认证模式](#7-认证模式)
8. [构建脚本 BUILD.py](#8-构建脚本-buildpy)
9. [启动/停止/重启脚本](#9-启动停止重启脚本)
10. [辅助工具 (bin/)](#10-辅助工具-bin)
11. [环境变量](#11-环境变量)
12. [典型工作流](#12-典型工作流)
13. [退出码与错误](#13-退出码与错误)
14. [另见](#14-另见)

---

## 1. 入口与脚本

**Python 入口：**

```bash
python app.py <args>          # 快捷入口，等同于 python -m src
python -m src <args>          # 包执行入口
python -m src.daemon          # 守护进程入口 (转调 lifecycle.main())
```

**脚本入口：**

| 脚本                          | 作用                                   |
| ----------------------------- | -------------------------------------- |
| `stop.ps1` / `stop.sh`        | 停止守护进程                           |
| `restart.ps1` / `restart.sh`  | 强制停止 → 等待 1 秒 → 启动            |
| `BUILD.py`                    | 发布构建（重建 `pty-agent/` 发布目录） |

**通用形式：**

```bash
python app.py <子命令> [位置参数] [选项]
python app.py <子命令> -h     # 查看该子命令的帮助
```

> **说明：**
> - `exec` 命令在守护进程未运行时会自动启动守护进程，一般无需手动 `start`。
> - 所有命令均以 JSON 响应输出到 stdout。

---

## 2. 全局选项

`--show-config` / `--default` 可置于子命令之前；`--default`、`--encoding`、`--debug-output` 也可置于子命令之后（见第 [5](#5-公共选项参考) 节）。

| 选项                    | 说明                                                         |
| ----------------------- | ------------------------------------------------------------ |
| `--show-config [KEY]`   | 查看配置值（不指定 KEY 则显示全部）                         |
| `--default KEY VALUE`   | 设置默认配置（可多次指定，可用键见 4.14 / 第 5 节）         |

**示例：**

```bash
python app.py --show-config
python app.py --show-config timeout
```

---

## 3. 子命令一览

| 子命令                 | 用途                              |
| ---------------------- | --------------------------------- |
| `start`                | 启动后台守护进程                  |
| `stop`                 | 停止后台守护进程                  |
| `status`               | 查看守护进程运行状态              |
| `list`                 | 列出所有活跃会话                  |
| `exec <id>`            | 启动或附加到会话（pty 默认；`--subprocess` 子进程模式） |
| `send <id> -i <input>` | 向运行中的会话发送输入（`-i` 必填） |
| `read <id>`            | 读取会话终端输出                  |
| `kill <id>`            | 终止指定会话                      |
| `events <id>`          | 查看会话事件                      |
| `closewin <id> <hwnd>` | 关闭指定 GUI 窗口                 |
| `mouse <id> <action>`  | 发送鼠标动作到 PTY 会话           |
| `wait`                 | 恒等待指定秒数（守护进程侧等待）  |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排（YAML 定义 + DAG 并行 + 条件/变量/重试） |
| `plugin <list\|ls\|attach\|detach\|cmd>` | 插件管理（列出/挂载/卸载/调用插件命令） |
| `file <read\|write\|edit\|grep\|glob\|upload\|download>` | 文件工具（读/写/唯一匹配替换/内容搜索/文件名匹配/上传/下载） |
| `keygen`               | 生成 Ed25519 公私钥对             |
| `set-default <key> <val>` | 覆盖默认配置（会话级）         |

---

## 4. 子命令详细说明

所有子命令均支持第 [5](#5-公共选项参考) 节列出的“公共选项”。

### 4.0  workflow - workflow 脚本编排

**用法：**

```bash
python app.py workflow run <file> [--vars KEY=VALUE ...] [--parallel N] [公共选项]
python app.py workflow list [公共选项]
python app.py workflow show <run-id> [公共选项]
python app.py workflow cancel <run-id> [公共选项]
```

**说明：** 用 YAML 定义文件声明一系列步骤（exec/send/read/kill/wait），由 daemon 后台调度执行：
支持依赖图并行、步骤间变量传递、条件判定、失败重试与错误策略。定义文件由 CLI 本机读取后
发送给 daemon 解析（跨机 tls 模式下语义不变）。**详细文档见 [WORKFLOW.md](WORKFLOW.md)**。

**定义文件结构（YAML）：**

```yaml
name: 构建流水线            # 可选，工作流名称
vars:                       # 可选，全局变量（可被 --vars 覆盖）
  repo: myrepo
max_parallel: 4             # 可选，最大并行步骤数（默认 4，可被 --parallel 覆盖）
steps:
  - id: clone               # 必填，步骤唯一标识（也是表达式引用名）
    type: exec              # 步骤类型: exec/send/read/kill/wait
    session: clone          # 会话标识
    command: "git clone https://example.com/{{vars.repo}}"
    trigger: "Cloning into|error"
    timeout: 300
  - id: build
    type: exec
    session: build
    command: "cd {{vars.repo}} && make"
    trigger: "error|^$"
    depends_on: [clone]     # 显式依赖；未声明则隐式依赖前一个步骤
    if: "clone.reason == 'trigger_matched'"   # 条件判定（安全表达式），为假跳过
    on_error: continue      # 失败策略: fail(默认)/continue/ignore
    retry: 2                # 失败重试次数（默认 0）
    retry_interval: 1.0     # 重试间隔秒数（默认 1.0）
  - id: test
    type: send
    session: build
    input: "./test.sh"
    trigger: "PASS|FAIL"
    depends_on: [build]
```

**步骤类型与必填字段：**

| type | 必填字段 | 主要可选字段 |
|------|---------|-------------|
| `exec` | session/command | trigger/timeout/idle_timeout/cwd/env/size/cols/rows/mode(subprocess)/full/keep_ansi |
| `send` | session/input | trigger/timeout/idle_timeout/send_eol/keep_ansi |
| `read` | session | trigger/timeout/lines/grep/full/keep_ansi |
| `kill` | session | - |
| `wait` | seconds | - |

**变量与表达式：**

- 插值 `{{...}}`：步骤字段支持插值全局变量（`{{vars.xxx}}`）与已有步骤结果
- 步骤结果核心字段（可被后续步骤 `if` 条件与插值引用）：
  `{{<step-id>.output}}`（outputStream）、`{{<step-id>.reason}}`（triggerReturnReason）、
  `{{<step-id>.exit_code}}`、`{{<step-id>.error}}`
- `if` 条件：安全表达式求值器（AST 白名单，拒绝函数调用），支持比较（`==`/`in`/`<` 等）、
  布尔（`and`/`or`/`not`）、算术、字符串成员判断，如 `'error' in build.output`

**并行与依赖：**

- 步骤未声明 `depends_on` 时隐式依赖前一个步骤（串行）；声明 `depends_on: [a, b]` 按显式依赖
- `depends_on: []` 表示无依赖，可与其前序步骤并行
- 依赖失败的步骤自动跳过（skipped）；`on_error=fail` 时整个 workflow 终止（其余步骤跳过），
  `on_error=continue` 仅标记本步骤失败，`on_error=ignore` 将失败视为成功（依赖可继续执行）
- 依赖环在解析阶段拒绝

**错误策略与重试：**

- `on_error`（fail/continue/ignore）控制步骤失败后的行为；默认 fail 终止整个 workflow
- `retry: N` 对失败步骤重试 N 次（间隔 `retry_interval`），重试耗尽后再按 on_error 处理

**运行时管理：** workflow 在 daemon 后台执行，`list`/`show` 可查看运行与步骤状态，
`cancel` 请求中断（置位取消事件，等待中的步骤最快 0.1s 内响应）。

**示例：**

```bash
# 启动定义文件
python app.py workflow run build.yaml

# 覆盖变量 + 限制并行度
python app.py workflow run deploy.yaml --vars env=prod region=cn-east --parallel 2

# 查看运行列表 / 单次运行状态
python app.py workflow list
python app.py workflow show wf-1786777600000-1

# 取消运行
python app.py workflow cancel wf-1786777600000-1
```

---

### 4.1  start - 启动后台守护进程

**用法：**

```bash
python app.py start [公共选项]
```

**说明：** 自动检测守护进程是否已运行。已运行则返回会话列表，未运行则自动启动子进程并写入共享内存（SHM）中的认证令牌/HMAC 密钥（监听位置由 daemon.toml `[listener]` 配置，不写入 SHM）。

**示例：**

```powershell
python app.py start
.\restart.ps1                 # 等价于 stop --force + start
```

---

### 4.2  stop - 停止后台守护进程

**用法：**

```bash
python app.py stop [--force | -f] [公共选项]
```

**选项：**

| 选项        | 说明                                                   |
| ----------- | ------------------------------------------------------ |
| `--force`, `-f` | 强制清理。端口丢失时通过互斥锁定位并终止守护进程。 |

**示例：**

```powershell
python app.py stop
python app.py stop --force
.\stop.ps1 -Force
```

---

### 4.3  status - 查看守护进程运行状态

**用法：**

```bash
python app.py status [公共选项]
```

**输出：** `running` / `pid` / `port` 等信息。

---

### 4.4  list - 列出所有活跃会话

**用法：**

```bash
python app.py list [公共选项]
```

**输出：** 所有活跃会话的 `id` / `command` / `running` 状态。

---

### 4.5  exec - 启动或附加到会话

**用法：**

```bash
python app.py exec <id> -c <命令> [选项] [公共选项]
```

**位置参数：**

| 参数 | 说明                  |
| ---- | --------------------- |
| `id` | 会话标识（最长 128 字符） |

**必填选项：**

| 选项            | 说明                                    |
| --------------- | --------------------------------------- |
| `--command CMD`, `-c CMD` | 要执行的命令字符串（自动拆分为参数列表），最长 65536 字符 |

**运行模式：**

| 选项                | 说明                                                         |
| ------------------- | ------------------------------------------------------------ |
| `--subprocess`      | 子进程模式：用 `subprocess.Popen` 直接捕获 stdout/stderr（非 PTY）。无终端回显、无快照、无 resize；增量输出 + stderr 分离（`stderrOutput`），支持写 stdin |

> **pty 模式** 输出始终为终端屏幕快照（`read`/`send` 亦返回快照），保留 TUI 交互（键盘/鼠标）与 trigger 触发返回。
> **子进程模式** 输出为增量文本，`read` 支持 `--offset` 增量读取；`stderr` 独立返回（`stderrOutput` 字段，CLI simple 插件显示为 `ERR > xxx`）。不支持 `--size`/`mouse`/`closewin`。

**会话控制选项：**

| 选项                | 说明                                                         |
| ------------------- | ------------------------------------------------------------ |
| `--force-pty-mode`  | 强制模式：忽略 shell 操作符检测，原样拆分执行                 |
| `--cwd DIR`         | 子进程工作目录（默认为守护进程当前目录）                     |
| `--env KEY=VALUE...`| 子进程环境变量（可多次指定），例：`--env TERM=xterm-256color COLORTERM=truecolor` |
| `--size WxH`        | 终端尺寸（如 `120x40`，默认 `80x24`）                        |

**触发与超时选项：**

| 选项                        | 说明                                                |
| --------------------------- | --------------------------------------------------- |
| `--trigger PATTERN`, `-t PATTERN` | 触发条件（正则表达式），命中后返回输出，最长 4096 字符 |
| `--newline`                 | 仅在换行后才检查触发条件（默认取配置值）            |
| `--timeout SECS`            | 等待超时秒数（float，默认 120）                     |
| `--idle-timeout SECS`       | 输出静默超时（秒）。程序持续 N 秒无新输出时触发返回  |
| `--idle-after-first-output` | 仅在程序首次输出后才开始检测静默超时                |

**输出选项：**

| 选项                          | 说明                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| `--full`                      | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |
| `--keep-ansi`                 | 保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留） |
| `--snapshot-diff`, `-s`       | 仅返回屏幕变化的行（需快照模式，stream 格式）               |
| `--output PATH`, `-o PATH`    | 输出到文件：`.txt/.log`=纯文本；`.svg`=矢量图；`.png/.jpg/.bmp`=位图（需 Pillow） |
| `--response-format {stream,svg}` | 响应格式（默认 stream；svg 需快照模式）                   |
| `--svg-compression-level {0,1,2}` | SVG 压缩等级（0=不压缩；1=轻度；2=深度，默认）           |

**示例：**

```bash
# 启动 Python REPL，等待提示符
python app.py exec py -c "python -u -i" -t ">>>"

# 启动长时运行服务器
python app.py exec srv -c "python server.py" --timeout 10

# 下载大文件，等待 100% 或错误
python app.py exec dl -c "curl -O https://example.com/big.zip" \
    --trigger "100%|error" --timeout 600

# TUI 程序 (快照模式 + SVG)
python app.py exec ui -c "mimo.exe" \
    --response-format svg --timeout 5

# 自定义工作目录与环境变量
python app.py exec w -c "make build" --cwd D:\proj \
    --env MAKEFLAGS="-j4"

# 自定义终端尺寸
python app.py exec wide -c "htop" --size 120x40

# 静默超时 (编译任务)
python app.py exec build -c "make all" --idle-timeout 10 \
    --idle-after-first-output
```

---

### 4.6  send - 向运行中的会话发送输入

**用法：**

```bash
python app.py send <id> -i <input> [选项] [公共选项]
```

**参数：**

| 参数    | 说明                                                    |
| ------- | ------------------------------------------------------- |
| `id`    | 会话标识（位置参数）                                    |
| `-i/--input` | 要发送的输入文本（最长 65536 字符），**必填选项** |

**输入控制选项：**

| 选项                              | 说明                                                                 |
| --------------------------------- | -------------------------------------------------------------------- |
| `--json-escaping`, `-j`           | 启用 JSON + 控制字符转义解码（见下）                                 |
| `--send-eol {lf,crlf,cr,none}`, `-e ...` | 末尾追加的行尾符（默认 `cr=\r`，模拟终端 Enter）              |

`-j` JSON 转义支持的序列：

```
\n          -> 换行
{enter}     -> 回车
{ctrl+a}    -> Ctrl+A
{up}/{down}/{left}/{right}
{f1}..{f12}
{home}/{end}/{pageup}/{pagedown}
{tab}/{esc}/{backspace}
```

**触发与超时选项：**（同 exec）`--trigger / -t`、`--newline`、`--timeout`、`--idle-timeout`、`--idle-after-first-output`

**输出选项：**

| 选项                | 说明                                    |
| ------------------- | --------------------------------------- |
| `--full`            | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |
| `--keep-ansi`       | 保留 ANSI 颜色/样式码                   |
| `--snapshot-diff`, `-s` | 仅返回屏幕变化的行                  |
| `--output / -o`     | 输出到文件                              |
| `--response-format` | 响应格式                                |
| `--svg-compression-level` | SVG 压缩等级                     |

**示例：**

```bash
# 发送 Python 代码
python app.py send py -i "print(100*100)" -t ">>>"

# 多行代码 (JSON 转义)
python app.py send py -i "for i in range(3):\n    print(i)" -t ">>>" -j

# 发送方向键 (TUI)
python app.py send ui -i "{down}" -j -e none

# 发送 Ctrl+C
python app.py send job1 -i "{ctrl+c}" -e none

# 发送并取屏幕快照
python app.py send ui -i "j" --timeout 5
```

---

### 4.7  read - 读取会话终端输出

**用法：**

```bash
python app.py read <id> [选项] [公共选项]
```

**位置参数：**

| 参数 | 说明     |
| ---- | -------- |
| `id` | 会话标识 |

> **说明：** `read` 默认立即返回当前输出；带 `--trigger` / `--idle-timeout` / `--timeout` 时进入等待模式，条件命中或超时后返回（与 send/exec 一致）。

**过滤选项：**

| 选项                    | 说明                                            |
| ----------------------- | ----------------------------------------------- |
| `--lines RANGE`, `-l RANGE` | 行数过滤：PTY/子进程对全量（含 scrollback 历史）或增量输出取**最后 N 行**（`start:end`=范围） |
| `--grep PATTERN`, `-g PATTERN` | 正则匹配过滤行                             |
| `--offset N`            | 增量读取：从指定字节偏移开始（int）             |
| `--column N`            | 输出第 N 列（1-based，仅 PTY 快照模式）         |
| `--full`                | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |

**触发选项：**（用于等待新输出）`--trigger / -t`、`--newline`、`--idle-timeout`、`--idle-after-first-output`

**输出选项：**

| 选项                | 说明                                        |
| ------------------- | ------------------------------------------- |
| `--keep-ansi`       | 保留 ANSI 颜色/样式码                       |
| `--snapshot-diff`, `-s` | 仅返回屏幕变化的行                      |
| `--output / -o`     | 输出到文件                                  |
| `--response-format` | 响应格式                                    |
| `--svg-compression-level` | SVG 压缩等级                           |

**示例：**

```bash
# 读取最近 20 行
python app.py read srv -l 20

# 增量读取 (从上次 offset 继续)
python app.py read srv

# 只看错误行
python app.py read srv -g "ERROR"

# 取屏幕快照
python app.py read ui

# 取快照差异 (仅变化行)
python app.py read ui -s

# 输出到 SVG 文件
python app.py read ui --response-format svg -o screen.svg
```

---

### 4.8  kill - 终止指定会话

**用法：**

```bash
python app.py kill <id> [公共选项]
```

**位置参数：**

| 参数 | 说明     |
| ---- | -------- |
| `id` | 会话标识 |

**说明：** 终止整个进程树（通过 Job Object 或进程组信号）。

**示例：**

```bash
python app.py kill py
```

---

### 4.9  events - 查看会话事件

**用法：**

```bash
python app.py events <id> [选项] [公共选项]
```

**位置参数：**

| 参数 | 说明     |
| ---- | -------- |
| `id` | 会话标识 |

**选项：**

| 选项            | 说明                                  |
| --------------- | ------------------------------------- |
| `--last N`, `-l N` | 仅返回最近 N 条事件（int）         |
| `--since TIME`  | 仅返回此时间之后的事件（ISO 8601 或 `HH:MM`） |
| `--until TIME`  | 仅返回此时间之前的事件                |

**时间解析规则：**

- 含 `T` 或前 5 字符含 `-` → ISO 8601（自动补本地时区偏移）
- 否则 → `HH:MM`（补当天日期）

**示例：**

```bash
python app.py events py --last 5
python app.py events py --since 14:30
python app.py events py --since 2026-08-10T14:30:00 --until 14:45
```

---

### 4.10  closewin - 关闭指定 GUI 窗口

**用法：**

```bash
python app.py closewin <id> <hwnd> [公共选项]
```

**位置参数：**

| 参数   | 说明                                |
| ------ | ----------------------------------- |
| `id`   | 会话标识                            |
| `hwnd` | 窗口句柄（支持十进制或 `0x` 十六进制） |

**示例：**

```bash
python app.py closewin job1 0x12345
python app.py closewin job1 74565
```

---

### 4.11  mouse - 发送鼠标动作到 PTY 会话

**用法：**

```bash
python app.py mouse <id> <action> [args...] [选项] [公共选项]
```

**位置参数：**

| 参数     | 说明                                                                 |
| -------- | -------------------------------------------------------------------- |
| `id`     | 会话标识                                                             |
| `action` | 鼠标动作类型：`click` / `drag` / `scroll` / `hover` / `press` / `grep` / `_get_cursor_location` |
| `args`   | 动作位置参数（依 action 而定，见下）                                 |

**动作参数要求：**

| action                | 参数                                   | 说明                       |
| --------------------- | -------------------------------------- | -------------------------- |
| `click`               | `<coordinates>` 如 `10,5`              | 或用 `--grep`              |
| `drag`                | `<from> <to>` 如 `10,5 30,5`           | 或用 `--grep`              |
| `scroll`              | `<coordinates> <direction> <times>`    | direction ∈ {up, down}，times >= 1 整数；或用 `--grep` |
| `hover`               | `<coordinates>`                        | 或用 `--grep`              |
| `press`               | `<coordinates> <seconds>`              | seconds > 0 浮点；或用 `--grep` |
| `grep`                | `<pattern>`                            | 纯查询，返回所有匹配的首/尾坐标 |
| `_get_cursor_location`| 无参数                                 | 返回光标位置 col,row 及所在行完整内容 |

**坐标系：** 1-based `(col, row)`。col 从 1 开始（左到右），row 从 1 开始（上到下）。与 SGR-1006 鼠标协议一致。

**选项：**

| 选项                        | 说明                                             |
| --------------------------- | ------------------------------------------------ |
| `--button {left,right,middle}` | 鼠标按钮（默认 left）                         |
| `--count {1,2,3}`            | 点击次数（默认 1，仅 click 有效）               |
| `--ctrl`                    | 按住 Ctrl                                        |
| `--shift`                   | 按住 Shift                                       |
| `--alt`                     | 按住 Alt                                         |
| `--grep PATTERN`            | 用正则匹配终端屏幕内容获取坐标。多匹配时不执行动作，返回所有坐标 |

**触发/输出选项：**（同 send）`--trigger / -t`、`--newline`、`--timeout`、`--idle-timeout`、`--idle-after-first-output`、`--keep-ansi`、`--snapshot-diff / -s`、`--output / -o`、`--response-format`、`--svg-compression-level`

**`--grep` 行为：**

- 单匹配 → 自动用匹配首坐标执行动作
- 多匹配 → 不执行动作，返回所有 `{"start":{"col":x,"row":y},"end":...}`
- 无匹配 → 返回错误

**示例：**

```bash
# 单击
python app.py mouse ui click 10,5

# 右键双击 + 修饰键
python app.py mouse ui click 10,5 --button right --count 2 --ctrl --shift

# 拖拽
python app.py mouse ui drag 10,5 30,5 --button left

# 滚动
python app.py mouse ui scroll 10,5 down 3

# 悬停 / 按住
python app.py mouse ui hover 10,5
python app.py mouse ui press 10,5 2.0 --button middle

# 纯查询匹配坐标
python app.py mouse ui grep "Error"

# 获取光标位置
python app.py mouse ui _get_cursor_location

# grep 自动定位单击
python app.py mouse ui click --grep "OK"

# 带快照与触发
python app.py mouse ui click 10,5 --timeout 5
python app.py mouse ui click 10,5 -t ">>>" --timeout 10
```

---

### 4.12  wait - 恒等待指定秒数

**用法：**

```bash
python app.py wait [--timeout SECS] [公共选项]
```

**选项：**

| 选项            | 说明                          |
| --------------- | ----------------------------- |
| `--timeout SECS` | 等待秒数（float，默认 120）  |

**说明：** 在守护进程侧等待指定秒数，用于脚本中的固定延时。

**示例：**

```bash
python app.py wait --timeout 5
```

---

### 4.13  keygen - 生成 Ed25519 公私钥对

**用法：**

```bash
python app.py keygen [--force | -f] [--key-dir DIR] [--comment TEXT | -C TEXT]
```

**选项：**

| 选项                  | 说明                              |
| --------------------- | --------------------------------- |
| `--force`, `-f`       | 覆盖已存在的密钥文件              |
| `--key-dir DIR`       | 密钥目录（默认 `~/.pty-agent/keys`） |
| `--comment TEXT`, `-C TEXT` | 公钥注释（默认 `用户名@主机名`） |

**输出：**

- OpenSSH 兼容的 `id_ed25519`（私钥，Unix 0600 / Windows 跳过权限位）
- `id_ed25519.pub`（公钥，Unix 0644）
- 打印指纹
- 提示追加到服务端 `~/.pty-agent/authorized_keys`

**示例：**

```bash
python app.py keygen
python app.py keygen -f -C "user@laptop"
python app.py keygen --key-dir D:\keys
```

---

### 4.14  set-default - 覆盖默认配置（会话级）

**用法：**

```bash
python app.py set-default <key> <value> [公共选项]
```

**位置参数：**

| 参数    | 说明     |
| ------- | -------- |
| `key`   | 配置键（见下表） |
| `value` | 配置值   |

**可用键：**

| 键                     | 说明                                            |
| ---------------------- | ----------------------------------------------- |
| `timeout`              | 触发超时秒数（float）                          |
| `newline`              | 仅在换行后检查触发条件（bool）                 |
| `keep-ansi`            | 保留 ANSI 颜色/样式码（bool）                   |
| `encoding`             | 终端编码（如 utf-8, gbk）                       |
| `debug`                | 启用 debug 信息输出（bool）                     |
| `send-eol`             | 发送行尾符（lf/crlf/cr/none）                   |
| `response-format`      | 响应格式（stream/svg）                         |
| `svg-compression-level`| SVG 压缩等级（0/1/2）                          |
| `terminal-size`        | 终端尺寸（如 120x40）                          |

**示例：**

```bash
python app.py set-default timeout 60
python app.py set-default terminal-size 120x40
python app.py set-default response-format svg
```

---

### 4.15  file - 文件工具

文件读写/搜索/传输工具集（read-before-write 状态机、rg 双引擎，机制详见 [docs/design/files-tools.md](design/files-tools.md) 与 [docs/design/files-transfer.md](design/files-transfer.md)）。

**用法：**

```bash
python app.py file read     <path> [-s SESSION_ID] [--offset N] [--limit N] [公共选项]
python app.py file write    <path> [-s SESSION_ID] --content TEXT | --content-file FILE [公共选项]
python app.py file edit     <path> [-s SESSION_ID] [--old TEXT | --old-file FILE] [--new TEXT | --new-file FILE] [公共选项]
python app.py file grep     <pattern> [path] [-s SESSION_ID] [--include GLOB] [--literal-text] [公共选项]
python app.py file glob     <pattern> [path] [-s SESSION_ID] [公共选项]
python app.py file upload   <local-path> <remote-path> [-s SESSION_ID] [--force] [--timeout N] [公共选项]
python app.py file download <remote-path> <local-path> [-s SESSION_ID] [--force] [--timeout N] [公共选项]
```

| 子命令 | 用途 | 关键点 |
| ------ | ---- | ------ |
| `file read`  | 读取文件内容（带行号输出，默认 2000 行） | 超过 250KB / 图片文件拒绝；文件不存在时提示相似文件名；`--offset` 0-based |
| `file write` | 覆盖写/新建文件（自动建父目录） | `--content` 与 `--content-file` 二选一（大文件用后者）；已存在文件必须先 `file read`，被外部修改后写入被拒；内容相同拒绝 |
| `file edit`  | 唯一匹配替换 | `--old`/`--old-file`、`--new`/`--new-file` 各自二选一；`--old` 为空=新建（文件必须不存在）；`--new` 为空=删除；`--old` 必须唯一匹配（未找到/重复均拒绝） |
| `file grep`  | 内容搜索（rg 引擎优先，缺失自动降级纯 Python） | `path` 缺省=会话 cwd；`--include` 文件名过滤；`--literal-text` 按字面量匹配 |
| `file glob`  | 文件名匹配（rg --files 引擎优先，缺失自动降级） | `path` 缺省=会话 cwd；pattern 支持 `**` 任意层级 |
| `file upload`  | 上传本地文件/目录到会话侧（scp -r 语义） | `local-path` 为 CLI 本机路径（文件或目录），`remote-path` 由 daemon 按会话 cwd 解析（支持 `~`）；目标已存在且相同→跳过，不同→拒绝并提示 `--force`；`--timeout` 为整个传输总时限（默认 120s），超时中止并清理临时文件 |
| `file download`  | 下载会话侧文件/目录到本地（scp -r 语义） | 与 upload 反向；`remote-path` 由 daemon 按会话 cwd 解析，可为文件或目录；覆盖策略与 `--timeout` 同 upload |

**`-s/--cwd-session`（必填）：** 指定某个会话，取它的 cwd 作为路径解析基准（不操作该会话）。相对路径基于该 cwd 拼接、`~` 按 daemon 用户展开、绝对路径原样使用；grep/glob 的 `path` 缺省即为该 cwd。跨机场景（CLI 与 daemon 不同机器）下语义依然正确——路径在 daemon 所在机器上解析。注意 cwd 是会话创建时的值，shell 内 `cd` 后不更新。会话不存在或已结束时报错。

**示例：**

```bash
python app.py file read src/main.py -s myapp --limit 50
python app.py file write cfg.ini -s myapp --content "[app]`nname=pty"
python app.py file write out.txt -s myapp --content-file big.txt      # 大文件内容从文件读取
python app.py file edit src/main.py -s myapp --old "TODO" --new "DONE"
python app.py file edit src/main.py -s myapp --old-file old.txt --new-file new.txt
python app.py file grep "def " src -s myapp --include *.py
python app.py file glob "src/**/*.py" -s myapp
python app.py file upload ./local.txt remote_dir/ -s myapp          # 上传本地文件到会话侧
python app.py file download remote_dir/local.txt ./local.txt -s myapp --force
```

**注意：**

- `file write` / `file edit`（replace/delete）受 read-before-write 状态机保护：必须先 `file read` 且期间未被外部修改，否则拒绝并提示
- `--content-file` / `--old-file` / `--new-file` 在 CLI 侧按 UTF-8 读取（非法编码报错），CRLF 规范化为 LF（与 `file read` 视图一致），内容再走同一传输链路，不受命令行长度限制
- 每次写操作在 ~/.pty-agent/history.db 的 `files_history` 表落版本链（initial → v1 → v2），便于后续回溯；当前不提供查询命令
- `file grep` / `file glob` 结果按文件修改时间最新优先，上限 100 条（超出截断并标记）
- `file upload` / `file download` 走二进制帧传输，不受 JSON 消息长度（MAX_MESSAGE_LENGTH）限制；upload 落盘后写 history 版本链 + 状态机双刷（与 write 一致），download 不落 history；传输中断/超时会清理临时文件

---

### 4.16  plugin - 插件管理

**用法：**

```bash
python app.py plugin list                # 列出所有已加载插件
python app.py plugin ls <id>             # 列出指定会话挂载的插件
python app.py plugin attach <id> <name>  # 动态挂载插件到运行中的会话
python app.py plugin detach <id> <name>  # 从会话卸载插件
python app.py plugin cmd <id> <name> <command> [args...]  # 调用插件自定义命令
```

**子命令：**

| 子命令 | 用法 | 说明 |
| ------ | ---- | ---- |
| `list` | `plugin list` | 列出已加载插件 |
| `ls`   | `plugin ls <id>` | 列出会话挂载的插件 |
| `attach` | `plugin attach <id> <name>` | 动态挂载插件到运行中的会话 |
| `detach` | `plugin detach <id> <name>` | 从会话卸载插件 |
| `cmd`  | `plugin cmd <id> <name> <command> [args...]` | 调用插件自定义命令（参数可选） |

> 插件在类声明处用 `kind` 声明自己支持哪侧钩子：`cli`=客户端进程内（before_request / transform_response / render_response），`session`/`process`=daemon 侧挂载。`--plugin <name>` 仅在 `exec` 出现：一次性把插件挂载到会话，按 kind 自动分流——CLI 形态记录到会话，后续 `read/send/mouse` 客户端自动挂钩回调（无需再传 `--plugin`）；会话/进程形态在 daemon 挂载。未指定时按插件 `auto_load` 条件自动注入 daemon 插件。

**示例：**

```bash
python app.py plugin list
python app.py plugin ls myapp
python app.py plugin attach myapp files
python app.py plugin cmd myapp files <command>
```

---

## 5. 公共选项参考

以下选项对绝大多数子命令有效（`exec`/`send`/`read`/`mouse`/`events` 等均支持；`keygen`/`set-default` 除外）：

| 选项                | 说明                                            |
| ------------------- | ----------------------------------------------- |
| `--encoding ENC`    | 终端编码（如 utf-8, gbk），本次调用记忆         |
| `--default KEY VALUE` | 设置默认配置（可多次指定）。可用键同 `set-default` |
| `--debug-output`    | 响应中输出 `debugInformation`（进程树/GUI 窗口/事件），默认关闭 |

**示例：**

```bash
python app.py exec s1 -c "bash" --encoding gbk
python app.py exec s1 -c "bash" --default timeout 60 --default keep-ansi true
python app.py read s1 --debug-output
```

**配置优先级：**

```
命令行显式参数  >  --default 覆盖值  >  代码内置默认值
```

---

## 6. 配置系统

**配置目录：** `config/`

**加载机制：** 从 TOML 文件展平为模块级常量

```
common.py    = common.toml + 运行时计算（IS_WINDOWS / DATA_DIR / PROJECT_ROOT）
daemon.py    = common.toml + daemon.toml + shared.toml + logging.toml + web.toml（可选）
client.py    = common.toml + shared.toml + client.toml
transfer.py  = transfer.toml
sandbox.py   = daemon/sandbox.toml（可选，缺失即沙箱关闭）
plugins.py   = config/plugins/plugins.json（可选，缺失即插件系统禁用）
optional.py  = 可选模块惰性导入网关（web/vnc/screenshare/cursorlocator/sandbox/plugins 可用性探测 + 惰性导入）

# 文件工具插件业务参数（读/写/搜索限制、忽略目录、RG_EXE）在插件自包含配置
# config/plugins/files/files.toml（config.py 加载），不进核心配置目录
```

**可选配置缺失行为：** `web.toml` 缺失时视为 web 未启用（`ENABLE_WEB=False`，连带 VNC/FastScreen 禁用，守护进程正常启动）；`plugins.json` 缺失时插件系统禁用；`sandbox.toml` 缺失时沙箱关闭。`vnc.toml`/`vnc.example.toml` 为 winvnc.exe 运行时配置，Python 不加载。

**数据目录：** `~/.pty-agent/`

| 路径               | 说明                       |
| ------------------ | -------------------------- |
| `keys/`            | Ed25519 密钥               |
| `authorized_keys`  | 公钥白名单（服务端）       |
| `known_hosts`      | TOFU 信任存储（客户端）    |
| `certs/`           | TLS 自签证书               |
| `logs/`            | 日志文件                   |

### 6.1  common.toml - 共有配置

```toml
[terminal]
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

[compression]
GZIP_COMPRESS_LEVEL = 6

[input_limit]
MAX_SESSION_ID_LEN = 128
MAX_COMMAND_LEN    = 65536
MAX_INPUT_LEN      = 65536
MAX_PATTERN_LEN    = 4096
```

### 6.2  daemon.toml - 守护进程配置

> 协议/IPC/daemon 控制等跨侧共享常量在 `shared.toml`；本文件管控监听器与认证参数。

```toml
[listener]
# 三监听器各自的启用/监听位置，可同开或只开一个
BASIC_ENABLED  = false
BASIC_HOST     = "0.0.0.0"     # 明文监听地址（对外暴露需谨慎）
BASIC_PORT     = 10521         # 明文监听端口
BASIC_PASSWORD = ""            # 明文监听器共享密码（空=无认证；非空时同时作为 HMAC 密钥）

TOKEN_ENABLED = true
TOKEN_HOST    = "127.0.0.1"   # 本机 token 监听地址（固定回环，仅本机可达）
TOKEN_PORT    = 10520

TLS_ENABLED   = false
TLS_HOST      = "0.0.0.0"     # TLS 监听地址（跨机访问需 0.0.0.0）
TLS_PORT      = 18767

[buffer]
MAX_OUTPUT_BUFFER = 104_857_600    # 100 MB
MAX_TRIGGER_SCAN  = 1_048_576      # 1 MB

[timeout]
DEFAULT_TRIGGER_TIMEOUT = 120.0

[misc]
SOCKET_LISTEN_BACKLOG  = 5
PTY_READ_SIZE          = 65536

[named_resource]
JOB_OBJECT_NAME_PREFIX = "Local\\PTYJob_"

[input_limit]
MAX_SESSIONS = 50

[auth]
# Token 认证（[listener] TOKEN_ENABLED=true 时生效）
AUTH_TOKEN_ROTATE_INTERVAL = 1800
AUTH_TOKEN_GRACE_PERIOD    = 120

# 公私钥认证（[listener] TLS_ENABLED=true 时生效）
PUBKEY_ALGORITHM       = "ed25519"
PUBKEY_AUTHORIZED_KEYS = "~/.pty-agent/authorized_keys"
PUBKEY_KEY_DIR         = "~/.pty-agent/keys"

# TLS 服务端（[listener] TLS_ENABLED=true 时生效）
TLS_CERT_DIR           = "~/.pty-agent/certs"
TLS_CERT_FILE          = "~/.pty-agent/certs/daemon.crt"
TLS_KEY_FILE           = "~/.pty-agent/certs/daemon.key"
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
```

### 6.3  client.toml - 客户端配置

```toml
[connection]
# 客户端连接方式，三选一，必须与 daemon 侧 [listener] 对应监听器 enabled 匹配
CONNECT_MODE = "token"        # basic / token / tls

# basic 模式连接位置（CONNECT_MODE=basic 时生效）
BASIC_HOST     = "127.0.0.1"    # 明文监听器地址
BASIC_PORT     = 10521          # 明文监听器端口
BASIC_PASSWORD = ""             # 明文监听器共享密码（空=无认证；须与 daemon 侧一致）

# token 模式连接位置（CONNECT_MODE=token 时生效，本机）
TOKEN_HOST = "127.0.0.1"      # 本机 token 监听器地址
TOKEN_PORT = 10520

# tls 模式连接位置（CONNECT_MODE=tls 时生效）
TLS_HOST = ""                 # 远程 daemon TLS 监听器地址
TLS_PORT = 18767

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
# 公私钥认证（CONNECT_MODE=tls 时生效）
PUBKEY_PRIVATE_KEY_PATH = "~/.pty-agent/keys/id_ed25519"  # 客户端私钥
KNOWN_HOSTS_FILE        = "~/.pty-agent/known_hosts"      # TOFU 信任存储文件
TOFU_STRICT             = true                            # true=指纹不匹配拒绝，false=仅警告
```

### 6.4  日志配置

日志系统位于 `src/logging/` 子包，基于异步队列（`QueueHandler` + 后台单线程 `pty-log-writer`），
业务线程零阻塞。日志文件写入 `<用户目录>/.pty-agent/logs/`，按模块分组拆分（时间戳命名），
后台线程自动将前一日（本地 0 点前）的 `*.log` gzip 归档为 `.log.gz`。

配置文件组织：
- `config/logging.toml` — 跨侧共享（格式 / 归档间隔 / 异步队列容量）
- `config/daemon/logging.toml` — daemon 侧专属（级别 / logger 分组）
- `config/client/logging.toml` — client 侧专属（级别 / logger 分组）

```toml
# config/logging.toml — 跨侧共享
[format]
LOG_FORMAT      = "%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s:%(threadName)s] %(filename)s:%(lineno)d - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

[archive]
LOG_ARCHIVE_INTERVAL = 600        # 前一日日志 gzip 归档检查间隔（秒）

[async]
LOG_QUEUE_SIZE = 8192             # 异步队列容量，满时 drop_oldest
```

```toml
# config/daemon/logging.toml — daemon 侧
[level]
DAEMON_LOG_LEVEL = "INFO"         # daemon/session/pty/protocol/auth/sandbox 级别
WEB_LOG_LEVEL    = "INFO"         # web/screenshare 级别

[loggers]
DAEMON_LOGGERS    = ["pty-daemon"]
SESSION_LOGGERS   = ["pty-session", "pty-grid", "pty-grid-screen", "pty-ipc",
                     "pty-plugins",
                     "process-job-tracker", "process-gui-monitor", "process-win32-error",
                     "process-base", "process-pgid-tracker"]
PTY_LOGGERS       = ["pty-factory", "pty-subprocess", "pty-wezterm",
                     "pty-windows", "pty-condrv", "pty-unix", "pty-unix-process"]
PROTOCOL_LOGGERS  = ["pty-protocol"]
AUTH_LOGGERS      = ["pty-auth", "pty-auth-tls"]
SANDBOX_LOGGERS   = ["sandbox-tracker", "sandbox-pty", "sandbox-manager"]
WEB_LOGGERS       = ["pty-web", "pty-web-settings", "pty-web-auth", "pty-vnc"]
SCREENSHARE_LOGGERS = ["pty-web-screenshare", "pty-screenshareservice", "pty-screenshareservice-encoder",
                       "screenshare", "screenshare.manager", "screenshare.h264_mse",
                       "screenshare.fmp4", "screenshare.h264_webcodecs"]
```

```toml
# config/client/logging.toml — client 侧
[level]
CLIENT_LOG_LEVEL = "DEBUG"

[loggers]
CLIENT_LOGGERS = ["pty-client", "pty-daemonctl"]
```

### 6.5  web.toml - Web 服务器配置（可选）

> 本文件可选：缺失时视为 web 未启用（`ENABLE_WEB=False`，连带 `ENABLE_VNC`/`ENABLE_FASTSCREEN` 禁用），守护进程正常启动，不触发 vnc/screenshare 加载。

```toml
[server]
ENABLE_WEB          = true
WEB_HOST = "127.0.0.1"
WEB_PORT = 18766
WEB_PASSWORD_HASH = ""              # SHA-256 hex，空=无认证

[vnc]
ENABLE_VNC         = true
VNC_WINVNC_PATH    = ""
VNC_MODULE_DIR = ""

[fastscreen]
ENABLE_FASTSCREEN           = true
FASTSCREEN_PACKAGE_DIR      = ""
FASTSCREEN_DEFAULT_FPS      = 30
FASTSCREEN_DEFAULT_QUALITY  = 0.8
FASTSCREEN_DEFAULT_BITRATE  = 2_000_000
FASTSCREEN_DEFAULT_GOP_SIZE = 30
FASTSCREEN_DEFAULT_METHOD   = "auto"
FASTSCREEN_DEFAULT_STREAM_FORMAT = "mse"   # auto/mjpeg/mse/webcodecs

[web_settings]
RIKKA_ENABLED         = true
DEFAULT_THEME         = "dark"      # light/dark/system
IME_ENABLED           = true
IME_CANDIDATE_COUNT   = 5
IME_VERTICAL          = false
IME_DEFAULT_STATE     = "chinese"   # chinese/english/last
IME_KEYBOARD_LAYOUT   = "compact"   # compact/full
IME_TOOLBAR_DISPLAY   = "always"    # never/desktop_only/always
IME_TB_OPACITY        = 100
IME_KB_OPACITY        = 100
IME_TB_SCALE          = 1.0
IME_KB_SCALE          = 1.0
```

### 6.6  客户端 ConfigManager 默认值

| 配置项                    | 默认值    | 说明                       |
| ------------------------- | --------- | -------------------------- |
| `timeout`                 | 120.0     | 触发超时秒数               |
| `newline`                 | False     | 仅在换行后检查触发条件     |
| `encoding`                | None      | 终端编码                   |
| `keep_ansi`               | False     | 保留 ANSI 颜色/样式码      |
| `debug`                   | True      | 启用 debug 信息输出        |
| `send_eol`                | `\r` (cr) | 发送行尾符                 |
| `response_format`         | stream    | 响应格式（stream/svg）     |
| `svg_compression_level`   | 1         | SVG 压缩等级               |
| `terminal_size`           | 80x24     | 终端尺寸                   |

> AI 分析不再作为配置项：已移出主程序为 CLI 插件（`config/plugins/ai`），
> 经 `exec --plugin ai` 挂载到会话后自动回调，无 `ai_analyse` / `ai_prompt` 配置。

---

## 7. 认证与监听方式

支持三种连接方式，通过 `client.toml [connection]` 的 `CONNECT_MODE` 选择连接哪个 daemon 监听器，
各自须与 `daemon.toml [listener]` 对应监听器的 enabled 状态匹配：

| `CONNECT_MODE` | 监听器 | 认证方式 | 适用场景 |
|----------------|--------|----------|----------|
| `token`（默认） | `[listener] TOKEN_ENABLED` | Token + HMAC（本机，SHM 分发） | 本机 IPC |
| `basic` | `BASIC_ENABLED` | 共享密码（密码即 HMAC 密钥，空密码=无认证） | 可信局域网，明文直连 |
| `tls` | `TLS_ENABLED` | TLS + Ed25519（跨机，TOFU 信任） | 跨机安全访问 |

### 7.1  token - 本机 Token + HMAC 认证（默认）

- `daemon.toml [listener] TOKEN_ENABLED = true`，监听 `TOKEN_HOST`:`TOKEN_PORT`（默认 127.0.0.1:10520）
- `client.toml [connection] CONNECT_MODE = "token"`，目标 `TOKEN_HOST`:`TOKEN_PORT`
- 守护进程启动时生成随机 Token 与 HMAC 密钥，写入共享内存
- 客户端从 SHM 读取，每次消息附加 HMAC-SHA256 签名
- Token 定期轮换（`AUTH_TOKEN_ROTATE_INTERVAL=1800s`），有宽限期（`AUTH_TOKEN_GRACE_PERIOD`）
- 仅限本机使用（SHM 隔离）

### 7.2  basic - 共享密码认证（跨机/局域网直连）

- `daemon.toml [listener] BASIC_ENABLED = true`，监听 `BASIC_HOST`:`BASIC_PORT`（默认 0.0.0.0:10521）
- `client.toml [connection] CONNECT_MODE = "basic"`，目标 `BASIC_HOST`:`BASIC_PORT`
- 明文传输，无加密；密码认证依赖配置的共享密码 `BASIC_PASSWORD`（两侧须一致）：
  - 非空：密码即 HMAC 密钥，双向签名 + 密码身份校验，防伪造防篡改
  - 空：退化为无认证，仅用于完全可信环境
- 密码以明文形式出现在请求消息中，可被局域网嗅探，对外暴露需谨慎

### 7.3  tls - 跨机 TLS + Ed25519 公私钥认证

- `daemon.toml [listener] TLS_ENABLED = true`，监听 `TLS_HOST`:`TLS_PORT`（默认 0.0.0.0:18767）
- `client.toml [connection] CONNECT_MODE = "tls"`，目标 `TLS_HOST`:`TLS_PORT`（即远程 daemon 地址）
- 客户端用私钥（`PUBKEY_PRIVATE_KEY_PATH`）签名，服务端用 `authorized_keys` 白名单验签
- 传输层使用 TLS（自签证书，类似 SSH host key）
- 客户端 TOFU 信任首次连接的证书指纹（`KNOWN_HOSTS_FILE` / `TOFU_STRICT`）

**工作流：**

```bash
# 1. 客户端生成密钥对
python app.py keygen -C "user@host"
# 2. 将 ~/.pty-agent/keys/id_ed25519.pub 追加到服务端
#    ~/.pty-agent/authorized_keys
# 3. 服务端启用 TLS 监听器并启动
#    daemon.toml: [listener] TLS_ENABLED = true
python app.py start
# 4. 客户端配置 client.toml [connection] CONNECT_MODE = "tls"、
#    TLS_HOST = <server>，跨机执行
python app.py exec s1 -c "bash"
```

---

## 8. 构建脚本 BUILD.py

**用法：**

```bash
python BUILD.py [选项]
```

发布构建脚本（Python，需 3.8+）。重建发布目录 `pty-agent/`，构建 rime-plugin / fastscreen / win-sandbox / wezterm-py，并下载 aichat / ripgrep / UltraVNC / terminal_injector。构建/下载产物统一先落入源目录基础包（`bin/`、`src/web/static/vendor/rime/`），最后整体复制进发布目录。

**选项：**

| 选项                              | 说明                                          |
| --------------------------------- | --------------------------------------------- |
| `-NoAichat`                       | 跳过 aichat 下载                              |
| `-NoFastscreen`                   | 跳过 fastscreen.dll 编译                      |
| `-NoWinsandbox`                   | 跳过 win_sandbox_native.pyd 编译              |
| `-NoWeztermPy`                    | 跳过 wezterm-py 编译                          |
| `-NoUltravnc`                     | 跳过 UltraVNC 下载                            |
| `-NoTerminalInjector`             | 跳过 terminal_injector 下载                   |
| `-NoRime`                         | 跳过 rime-plugin 构建                         |
| `-NoRg`                           | 跳过 ripgrep 下载                             |
| `-Mirror <url>` / `-m <url>`      | GitHub 下载镜像（亦可用 `$env:GITHUB_MIRROR`）|
| `-ApiMirror <url>` / `-am <url>`  | GitHub API 镜像（默认 `https://api.github.com`） |

**环境变量：** 命令行 `-NoX` 优先于对应环境变量。

| 变量                       | 默认                      | 说明                    |
| -------------------------- | ------------------------- | ----------------------- |
| `GITHUB_MIRROR`            | `""`                      | GitHub 下载镜像前缀     |
| `GITHUB_API_MIRROR`        | `https://api.github.com`  | GitHub API 镜像         |
| `DOWNLOAD_AICHAT`          | `true`                    | 是否下载 aichat         |
| `BUILD_FASTSCREEN`         | `true`                    | 是否编译 fastscreen.dll |
| `BUILD_WINSANDBOX`         | `true`                    | 是否编译 win_sandbox_native.pyd |
| `BUILD_WEZTERMPY`          | `true`                    | 是否编译 wezterm-py     |
| `DOWNLOAD_ULTRAVNC`        | `true`                    | 是否下载 UltraVNC       |
| `DOWNLOAD_TERMINALINJECTOR`| `true`                    | 是否下载 terminal_injector |
| `BUILD_RIME`               | `true`                    | 是否构建 rime-plugin    |
| `DOWNLOAD_RG`              | `true`                    | 是否下载 ripgrep        |

**构建流程：**

1. 清空并重建发布目录 `pty-agent/`
2. 构建 rime-plugin（`web_rime/plugin`，npm run build，产物落入 `src/web/static/vendor/rime/`）
3. 用 cmake + `Visual Studio 18 2026` x64 编译 `fastscreen.dll`（指定生成器失败时回退默认），产物落入 `bin/fastscreencore/`
4. 用 cmake + Ninja + vcvars64 编译 `win_sandbox_native.pyd`（pybind11），产物落入 `bin/win_sandbox/_native/`
5. 用 maturin + vcvars64 + cargo 编译 wezterm-py，解包 wheel 落入 `bin/pywezterm/`
6. 从 `sigoden/aichat` releases 下载 `aichat.exe` 到 ai 插件目录 `config/plugins/ai/bin/`
7. 从 `BurntSushi/ripgrep` releases 下载 `rg.exe`（按系统架构 x86_64/aarch64）到 `bin/rg/`
8. 下载 `UltraVNC_1824.zip`（按 x64/x86 架构）到 `bin/ultravnc/`
9. 下载 `terminal_injector_x64_v1.0.zip` 到 `bin/terminal_injector/`
10. 复制基础包（`src/`、`bin/`、`config/plugins/`、`app.py`、`SKILL.md`）到 `pty-agent/`
11. 清理 `__pycache__` 与 `.gitkeep`
12. 删除发布目录中的冗余文件（rime-plugin 的 ESM 变体与 source map、ai 插件 config.yaml、vnc.toml/vnc.example.toml、ultravnc 的 `.log`/`.ini`）

**构建产物：** `./pty-agent/`（被 `.gitignore` 忽略）

**示例：**

```powershell
python BUILD.py
python BUILD.py -NoAichat -NoUltravnc
$env:GITHUB_MIRROR="https://ghproxy.com/"; python BUILD.py
python BUILD.py -Mirror "https://v4.gh-proxy.org/" -ApiMirror "https://api.github.com"
python BUILD.py -NoUltravnc -NoTerminalInjector -Mirror "https://v4.gh-proxy.org/"
```

日志：控制台 UTF-8 输出 + `%TEMP%/pty-agent-build.log`。

---

## 9. 启动/停止/重启脚本

### 9.1  Windows（PowerShell）

```powershell
.\stop.ps1 [-Force]           # 停止守护进程
.\restart.ps1                 # 强制停止 -> 等待 1 秒 -> 启动
```

`restart.ps1` 流程：

```powershell
python -m src stop --force
Start-Sleep -Seconds 1
python -m src start
```

### 9.2  Unix（Bash）

```bash
./stop.sh [--force]           # 停止守护进程
./restart.sh                  # 同 restart.ps1
```

### 9.3  直接命令

```bash
python app.py start
python app.py stop [--force]
python app.py status
# 重启无独立命令:
python app.py stop --force; python app.py start
```

---

## 10. 辅助工具 (bin/)

### 10.1  aichat - AI 聊天工具

- **路径：** `config/plugins/ai/bin/aichat.exe`（由 `BUILD.py` 下载）
- **用途：** CLI 级 `ai` 插件（`config/plugins/ai`）对内调用，对响应做二次分析

### 10.2  cursorlocator - 光标定位器

- **路径：** `bin/cursorlocator/`
- **用途：** Win32 API + 渲染（`ring_worker.py`、`win32_api.py`、`pixel_color.py`、`rendering.py`、`config.py`）
- **可选：** 仅 web 启用时经 `src/optional` 惰性加载；缺失时 web 禁用光标定位功能（`is_available()` 返回 False）

### 10.3  fastscreencore - 屏幕捕获核心

- **路径：** `bin/fastscreencore/`
- **用途：** DXGI/WGC/BitBlt 屏幕捕获（`capture.py`、`_core.py`、`fastscreen.dll`）
- **可选：** 仅 web + screenshare 启用时经 `src/optional` 惰性加载；整体缺失时 web 禁用 FastScreen 功能

### 10.4  pproxy - 加密 SOCKS5 代理

- **路径：** `bin/pproxy/`
- **用途：** Shadowsocks `ss://` 协议代理服务器

**用法：**

```bash
python start.py                          # 使用 config.json
python start.py -p mypassword            # 覆盖密码
python start.py --port 8388 \
    --cipher aes-256-gcm -p mypassword
python start.py -h                       # 所有选项
```

### 10.5  terminal_injector - 终端注入工具

- **路径：** `bin/terminal_injector/`（由 `BUILD.py` 下载 `terminal_injector_x64_v1.0.zip` 到该目录）
- **用途：** 强制劫持已运行的控制台程序供会话使用

**用法：**

```bash
terminal_injector.exe --list-targets --json
terminal_injector.exe --mediator --target-pid $pid

# 通过 CLI 调用:
app.py exec sid -c "terminal_injector.exe --mediator --target-pid $pid" \
    --default response-format svg --timeout 15
```

### 10.6  win_sandbox - Windows 沙盒

- **路径：** `bin/win_sandbox/`（`_native/win_sandbox_native*.pyd` 由 `BUILD.py` 编译）
- **内容：** vendored python 包（`win_sandbox`）+ pybind11 原生扩展
- **用途：** 沙箱会话后端（Job Object + Low IL 隔离，进程内直调，无 IPC 管道）
- **可选：** 仅 `sandbox.toml` 启用沙箱时加载；缺失时 `sandbox/manager.py` 惰性导入返回不可用，`start()` 抛 `SandboxError`，不中断 daemon 启动

### 10.7  ultravnc - UltraVNC 远程桌面

- **路径：** `bin/ultravnc/`（由 `BUILD.py` 下载 `UltraVNC_1824.zip` 到该目录）
- **用途：** VNC 远程桌面
- **可选：** 仅 web + VNC 启用时经 `src/optional` 惰性加载；`winvnc.exe` 缺失时 `is_available()` 返回 False，web 前端隐藏 VNC 入口

---

## 11. 环境变量

### 11.1  连接与认证相关（client.toml 配置键）

> 以下项是 `client.toml` 的配置键（非进程环境变量），由 `src/config/client.py` 加载。

| 变量                   | 说明                                       |
| ---------------------- | ------------------------------------------ |
| `CONNECT_MODE`         | 客户端连接方式: basic/token/tls             |
| `BASIC_HOST`/`BASIC_PORT`/`BASIC_PASSWORD` | 明文监听器地址/端口/共享密码（空=无认证） |
| `TOKEN_HOST`/`TOKEN_PORT`   | 本机 token 监听器地址/端口           |
| `TLS_HOST`/`TLS_PORT`       | 远程 TLS 监听器地址/端口            |
| `PUBKEY_PRIVATE_KEY_PATH`  | 客户端 Ed25519 私钥路径           |
| `KNOWN_HOSTS_FILE`     | TOFU 信任存储路径                          |
| `TOFU_STRICT`          | TOFU 严格模式                              |

### 11.2  BUILD.py 环境变量

| 变量                       | 说明                                          |
| -------------------------- | --------------------------------------------- |
| `GITHUB_MIRROR`            | GitHub 下载镜像                               |
| `GITHUB_API_MIRROR`        | GitHub API 镜像（默认 `https://api.github.com`） |
| `DOWNLOAD_AICHAT`          | 是否下载 aichat（默认 true）                  |
| `BUILD_FASTSCREEN`         | 是否编译 fastscreen.dll（默认 true）          |
| `BUILD_WINSANDBOX`         | 是否编译 win_sandbox_native.pyd（默认 true）  |
| `BUILD_WEZTERMPY`          | 是否编译 wezterm-py（默认 true）              |
| `DOWNLOAD_ULTRAVNC`        | 是否下载 UltraVNC（默认 true）                |
| `DOWNLOAD_TERMINALINJECTOR`| 是否下载 terminal_injector（默认 true）       |
| `BUILD_RIME`               | 是否构建 rime-plugin（默认 true）             |
| `DOWNLOAD_RG`              | 是否下载 ripgrep（默认 true）                 |

### 11.3  子进程环境变量

通过 `exec --env KEY=VALUE`（可多个）为子进程设置额外环境变量，合并到继承的环境中。典型用于设置：

```
TERM=xterm-256color
COLORTERM=truecolor
LANG/LC_ALL
```

---

## 12. 典型工作流

### 12.1  交互式 Python REPL

```bash
python app.py exec py -c "python -u -i" -t ">>>"
python app.py send py -i "print(100*100)" -t ">>>"
# 预期: >>> 10000\n>>>
python app.py send py -i "for i in range(3):\n    print(i)" -t ">>>" -j
# 预期: >>> 0\n1\n2\n>>>
python app.py read py --lines 10
python app.py events py --last 5
python app.py kill py
```

### 12.2  长时运行服务器

```bash
python app.py exec srv -c "python server.py" --timeout 10
python app.py read srv -l 20        # 中途查看最近 20 行
python app.py read srv              # 增量读取 (从上次 offset 继续)
python app.py read srv -g "ERROR"   # 只看错误行
python app.py kill srv
```

### 12.3  下载大文件 / 编译

```bash
python app.py exec dl -c "curl -O https://example.com/largefile.zip" \
    --trigger "100%"
python app.py exec dl -c "curl -O https://example.com/largefile.zip" \
    --trigger "100%" --timeout 600 --idle-timeout 30
python app.py read dl
python app.py read dl --trigger "100%|error|warning" --timeout 600
python app.py kill dl
```

### 12.4  容易崩溃的程序

```bash
python app.py exec job1 -c "python worker.py" --idle-timeout 5
# 进程崩溃时 triggerReturnReason="program_crashed"
# program.exitCode 非零
python app.py events job1 -l 10      # 查看崩溃事件详情
```

### 12.5  TUI 程序交互

```bash
python app.py exec ui -c "mimo.exe" \
    --default response-format svg --timeout 5
python app.py send ui -i "j" --send-eol cr --timeout 5 -s
python app.py read ui -s
python app.py send ui -i "帮我写一个贪吃蛇游戏" -s
python app.py kill ui
```

### 12.6  自定义终端尺寸

```bash
python app.py exec wide -c "htop" --size 120x40 --timeout 5
python app.py exec s1 -c "cmd" --default terminal-size 100x30
python app.py --show-config terminal-size
```

### 12.7  调试器交互（cdb/gdb）

```bash
python app.py exec dbg -c \
    '"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe" myapp.exe' \
    -t ">"
python app.py send dbg -i "g" -t "0:000"      # 继续执行
python app.py send dbg -i "k" -t "0:000"      # 查看调用栈
python app.py send dbg -i "db esp L100" -t "0:000"  # 查看内存
python app.py send dbg -i "q" -t ">"          # 退出
python app.py kill dbg
```

### 12.8  鼠标操作

```bash
python app.py mouse ui click 10,5
python app.py mouse ui click 10,5 --button right --count 2 --ctrl --shift
python app.py mouse ui drag 10,5 30,5 --button left
python app.py mouse ui scroll 10,5 down 3
python app.py mouse ui hover 10,5
python app.py mouse ui press 10,5 2.0 --button middle
python app.py mouse ui grep "Error"                  # 纯查询返回坐标
python app.py mouse ui _get_cursor_location          # 获取光标位置
python app.py mouse ui click --grep "OK"             # grep 自动定位
python app.py mouse ui click 10,5 --timeout 5
python app.py mouse ui click 10,5 -t ">>>" --timeout 10
```

### 12.9  控制字符发送（-j JSON 转义模式）

```bash
python app.py send myid -i "import os\nprint(os.name)" -t ">>>" -j
python app.py send myid -i "{down}" -j -e none          # TUI 方向键
python app.py send myid -i "{ctrl+c}" -e none           # 发送 Ctrl+C
python app.py send myid -i "{enter}" -j                 # 回车
```

### 12.10  跨机 TLS 部署

```bash
# 服务端: daemon.toml [listener] TLS_ENABLED=true（监听 TLS_HOST:TLS_PORT，跨机需 0.0.0.0）
#   python app.py start
# 客户端:
python app.py keygen -C "user@client"
# 将 id_ed25519.pub 追加到服务端 ~/.pty-agent/authorized_keys
# client.toml: [connection] CONNECT_MODE="tls", TLS_HOST="<server-ip>", TLS_PORT=18767
python app.py exec s1 -c "bash"
```

### 12.11  Web 界面

```bash
# 启动守护进程 (含 Web 服务器，默认 127.0.0.1:18766)
python app.py start
# 浏览器访问 http://127.0.0.1:18766

# 内网穿透 (远程访问 Web):
#   Cloudflare Tunnel:
./cloudflared tunnel --url http://127.0.0.1:18766
#   cpolar (需 tcp 转发 + TLS 包装，http 不支持 websocket):
./cpolar tcp 18766
```

### 12.12  快速验证

```bash
python app.py start
python app.py exec test -c "python -u -i" -t ">>>" --timeout 5
python app.py send test -i "print(100*100)" -t ">>>"
# 预期: >>> 10000\n>>>
python app.py send test -i "for i in range(3):\n    print(i)" -t ">>>"
# 预期: >>> 0\n1\n2\n>>>
python app.py kill test
python app.py stop
```

---

## 13. 退出码与错误

**CLI 进程退出码：**

| 退出码 | 含义                                             |
| ------ | ------------------------------------------------ |
| `0`    | 成功（响应正常返回）                             |
| `1`    | 业务错误（守护进程返回 error 响应，如会话不存在） |
| `2`    | 用法错误（argparse 参数缺失/不识别）             |
| `130`  | 用户中断（Ctrl+C）                               |

**响应 JSON 中的关键字段：**

| 字段                 | 说明                                                                 |
| -------------------- | -------------------------------------------------------------------- |
| `status`             | `"success"` / `"error"`                                              |
| `error.message`      | 错误信息                                                             |
| `triggerReturnReason`| 触发返回原因：`trigger_matched` / `trigger_timeout` / `idle_timeout` / `program_ended` / `program_crashed` / `gui_detected` / `ok` |
| `program.exitCode`   | 子进程退出码（非零表示异常）                                         |
| `debugInformation`   | debug 信息（进程树/GUI 窗口/事件，默认关闭，可用 `--debug-output` 开启）           |

**常见错误：**

- **守护进程未运行：** `exec` 会自动启动，无需手动 `start`
- **端口丢失：** 使用 `stop --force` 通过互斥锁定位并终止
- **编码乱码：** 使用 `--encoding` 指定正确编码（如 gbk）
- **触发条件不命中：** 检查正则表达式，或使用 `--idle-timeout` 兜底
- **TUI 程序无输出：** pty 模式恒返回屏幕快照，确认会话处于 pty 模式

---

## 14. 另见

| 文档                              | 说明                                   |
| --------------------------------- | -------------------------------------- |
| `README.md`                       | 项目说明、命令概览、认证模式           |
| `AGENTS.md`                       | AI Agent 工作规范                      |
| `SKILL.md`                        | AI 技能描述（最详尽的命令参考）        |
| `docs/ARCHITECTURE.md`           | 架构设计（模块化分层、调用链、线程模型） |
| `docs/CODEING-STANDARD.md`        | Python 编码规范                        |
| `docs/filestree/src.md`           | `src/` 文件树                          |
| `docs/filestree/web-static.md`    | `src/web/static/` 前端文件树           |
| `docs/filestree/document-standard.md` | 文件树编写规范                    |
| `docs/net-traveral/cloudflare-tunnel.md` | Cloudflare Tunnel 内网穿透       |
| `docs/net-traveral/cpolar.md`     | cpolar 内网穿透                        |
| `tests/docs/FILESTREE.md`         | `tests/` 测试套件文件树                |
| `fastscreen/README.md`     | `fastscreen/` C++ 屏幕捕获库说明   |
| `web_rime/docs/FILESTREE.md`      | `web_rime/` Rime 输入法文件树          |

**测试：**

```bash
python -m pytest tests/ -v              # 全部测试
python -m pytest tests/unit/ -v         # 单元测试
python -m pytest tests/integration/ -v  # 集成测试
python -m pytest tests/e2e/ -v          # 端到端 (VNC/TLS/pubkey/resize)
python -m pytest tests/web/ -v          # Web (MSE/H264/WebSocket)
```
