# PTY-Agent 命令行帮助文档

> PTY-Agent 是一个命令行交互式程序交互代理。通过伪终端 (PTY) 与交互式 CLI 程序双向通信，由后台守护进程 (daemon) 统一管理会话。支持触发返回、静默超时、进程崩溃检测、GUI 窗口检测、编码自动探测、终端屏幕快照、AI 二次分析（插件）、Ed25519 公私钥认证、TLS 跨机部署、VNC 远程桌面、Screenshare 屏幕串流、子代理（Smart Agent / Claude Code / CodeBuddy / Devin / OpenCode）管理等能力。

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
   - 4.0 [workflow](#40--workflow---workflow-脚本编排) · 4.1 [start](#41--start---启动后台守护进程) · 4.2 [stop](#42--stop---停止后台守护进程) · 4.3 [status](#43--status---查看守护进程运行状态) · 4.4 [list](#44--list---列出所有活跃会话)
   - 4.5 [exec](#45--exec---启动或附加到会话) · 4.6 [send / advsend](#46--send--advsend---向运行中的会话发送输入) · 4.7 [read](#47--read---读取会话终端输出) · 4.8 [kill](#48--kill---终止指定会话)
   - 4.9 [events](#49--events---查看会话事件) · 4.10 [closewin](#410--closewin---关闭指定-gui-窗口) · 4.11 [mouse](#411--mouse---发送鼠标动作到-pty-会话) · 4.12 [wait](#412--wait---等待通知或指定秒数)
   - 4.13 [notice](#413--notice---查看通知的完整内容) · 4.14 [keygen](#414--keygen---生成-ed25519-公私钥对) · 4.15 [set-default](#415--set-default---覆盖全局默认配置) · 4.16 [file](#416--file---文件工具)
   - 4.17 [plugin](#417--plugin---插件管理) · 4.18 [attend](#418--attend---接管会话为完整实时终端) · 4.19 [子代理命令](#419--子代理命令smartagent--claude--codebuddy--devin--opencode)
5. [公共选项参考](#5-公共选项参考)
6. [返回条件与结果处理](#6-返回条件与结果处理)
7. [通知机制（--notify / wait / notice）](#7-通知机制)
8. [配置系统](#8-配置系统)
9. [认证与监听方式](#9-认证与监听方式)
10. [构建脚本 BUILD.py](#10-构建脚本-buildpy)
11. [启动/停止/重启脚本](#11-启动停止重启脚本)
12. [辅助工具 (bin/)](#12-辅助工具-bin)
13. [环境变量](#13-环境变量)
14. [典型工作流](#14-典型工作流)
15. [退出码与错误](#15-退出码与错误)
16. [另见](#16-另见)

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
| `stop.sh`                     | 停止守护进程（强制）                   |
| `restart.sh`                  | 强制停止 → 等待 1 秒 → 启动            |
| `BUILD.py`                    | 发布构建（重建 `pty-agent/` 发布目录） |

**通用形式：**

```bash
python app.py <子命令> [位置参数] [选项]
python app.py <子命令> -h     # 查看该子命令的帮助
```

> **说明：**
> - `exec` 命令在守护进程未运行时会自动启动守护进程，一般无需手动 `start`。
> - 输出为**渲染后的文本**（内容 → stdout；状态行/消息/错误 → stderr），详见第 [15](#15-退出码与错误) 节。**不再是原始 JSON dump**。

---

## 2. 全局选项

`--show-config` 可置于子命令之前（顶层）；`--default`、`--encoding`、`--debug-output` 为各子命令的公共参数（置于子命令之后，见第 [5](#5-公共选项参考) 节）。

| 选项                    | 说明                                                         |
| ----------------------- | ------------------------------------------------------------ |
| `--show-config [KEY]`   | 查看当前调用配置值（不指定 KEY 则显示全部；只读，不连接 daemon） |

**示例：**

```bash
python app.py --show-config
python app.py --show-config timeout
```

输出示例：

```
当前调用配置:
  debug = off
  encoding = (未设置)
  keep_ansi = off
  newline = off
  response_format = stream
  send_eol = cr (\r)
  shell = (未设置)
  svg_compression_level = 1
  terminal_size = 80x24
  timeout = 120.0
```

---

## 3. 子命令一览

| 子命令                 | 用途                              |
| ---------------------- | --------------------------------- |
| `start`                | 启动后台守护进程（`--foreground` 前台 / `--survive` 生存模式） |
| `stop`                 | 停止后台守护进程（`--force` 强制） |
| `status`               | 查看守护进程运行状态（pid/port/uptime/sessions/web） |
| `list`                 | 列出所有活跃会话（含已结束）      |
| `exec <id> -c <cmd>`   | 启动或附加到会话（pty 默认；`--subprocess` 子进程模式） |
| `send <id> -i <input>` | 原样发送输入（`-i` 必填）          |
| `advsend <id> -i <input>` | 发送输入（恒启用 JSON + 控制字符转义解码） |
| `read <id>`            | 读取会话终端输出                  |
| `kill <id>`            | 终止指定会话                      |
| `events <id>`          | 查看会话事件                      |
| `closewin <id> <hwnd>` | 关闭指定 GUI 窗口（仅 Windows）   |
| `attend <id>`          | 接管会话为完整实时终端（镜像 + 输入/鼠标/resize，不影响 web 端） |
| `mouse <id> <action>`  | 发送鼠标动作到 PTY 会话           |
| `wait [--timeout N]`   | 有待消费通知立即返回摘要，否则等待指定秒数（通知到达即唤醒） |
| `notice <nid>`         | 查看通知的完整内容                |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排（YAML 定义 + DAG 并行 + 条件/变量/重试） |
| `plugin <子命令>`      | 插件管理（list/ls/attach/detach/cmd/install/uninstall/enable/disable/reload/info/status/config/gethelp） |
| `file <read\|write\|edit\|grep\|glob\|upload\|download>` | 文件工具（读/写/唯一匹配替换/内容搜索/文件名匹配/上传/下载） |
| `keygen`               | 生成 Ed25519 公私钥对（本地命令，无需 daemon） |
| `set-default <key> <val>` | 覆盖全局默认配置（守护进程内存，daemon 重启即清空） |
| `smartagent` / `claude` / `codebuddy` / `devin` / `opencode` | 子代理管理（spawn 子代理进程，插件 `subagent` 注册；详见 4.19） |

---
## 4. 子命令详细说明

所有子命令均支持第 [5](#5-公共选项参考) 节列出的"公共选项"（`keygen`/`set-default` 除外）。

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
vars:                       # 可选，全局变量（值限 str/int/float/bool，可被 --vars 覆盖）
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

**解析期校验**（run 时即报错，不产生运行）：id 非空唯一、type 合法且必填字段齐全、
`depends_on` 引用存在且无环、`on_error`/`retry`/`max_parallel` 取值合法、定义文件上限 20 MB。
（`trigger` 正则为**运行时编译**，解析期不校验。）

**步骤类型与必填字段：**

| type | 必填字段 | 主要可选字段 |
|------|---------|-------------|
| `exec` | session/command | trigger/timeout/idle_timeout/idle_after_first_output/cwd/env(映射)/encoding/size 或 cols+rows/mode(subprocess)/full/keep_ansi |
| `send` | session/input | trigger/timeout/idle_timeout/eol(lf/crlf/cr/none)/json(true 转义)/keep_ansi |
| `read` | session | trigger/timeout/idle_timeout/lines/grep/full/keep_ansi |
| `kill` | session | - |
| `wait` | seconds | - |

**变量、插值与条件：**

- 插值 `{{...}}`：步骤字段支持插值全局变量（`{{vars.xxx}}`）与已有步骤结果
- 步骤结果核心字段（可被后续步骤 `if` 条件与插值引用）：
  `{{<step-id>.output}}`（outputStream）、`{{<step-id>.reason}}`（triggerReturnReason）、
  `{{<step-id>.exit_code}}`、`{{<step-id>.error}}`
- `if` 条件：安全表达式求值器（AST 白名单，拒绝任何函数调用/属性方法），支持比较（`==`/`in`/`<` 等）、
  布尔（`and`/`or`/`not`）、算术、字符串成员判断，如 `'error' in build.output`；
  `true`/`false`/`null` 等价 True/False/None

**并行与依赖：**

- 步骤未声明 `depends_on` 时隐式依赖前一个步骤（串行）；声明 `depends_on: [a, b]` 按显式依赖
- `depends_on: []` 表示无依赖，可与其前序步骤并行
- 依赖失败的步骤自动跳过（skipped）；`on_error=fail` 时整个 workflow 终止（其余步骤跳过），
  `on_error=continue` 仅标记本步骤失败，`on_error=ignore` 将失败视为成功（依赖可继续执行）
- 依赖环在解析阶段拒绝（返回循环路径）
- **同一 `session` 的步骤强制串行派发**（防止并发写输入互相踩踏）

**失败、重试与错误策略：**

- 步骤失败 = 执行异常（会话创建失败、会话不存在、写入失败等）或错误响应；
  **trigger 超时不算失败**（正常返回 `reason=trigger_timeout`）
- `retry: N` 对失败步骤重试 N 次（最多尝试 N+1 次，间隔 `retry_interval` 秒，默认 1.0）
- `on_error`（fail/continue/ignore）控制步骤失败后的行为；默认 fail 终止整个 workflow

**运行时管理：** workflow 在 daemon 后台执行，`list`/`show` 可查看运行与步骤状态，
`cancel` 请求中断（置位取消事件，等待中的步骤最快 0.1s 内响应）。
运行记录上限 50（超限淘汰最旧终态）；步骤输出保存上限 4096 字符；运行状态仅存内存
（daemon 重启即清空）。

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
python app.py start [公共选项] [--foreground] [--survive]
```

**说明：** 自动检测守护进程是否已运行。已运行则返回会话列表，未运行则自动启动子进程并写入共享内存（SHM）中的认证令牌/HMAC 密钥（监听位置由 daemon.toml `[listener]` 配置）。启动成功后会输出可用的 shell 列表与进程级插件上下文。

**选项：**

| 选项 | 说明 |
| --- | --- |
| `--foreground` | 前台运行：当前进程 exec 替换为 daemon（不双 fork），供 s6/systemd 等服务监督器以 `exec python app.py start --foreground` 直接持有（exec 链保持同一 PID），日志输出到 stderr |
| `--survive` | 生存模式：运行期间拦截忽略所有结束进程的信号（SIGTERM/SIGHUP/SIGINT/SIGQUIT）与 stop 协议消息，仅 SIGKILL 可终止（`stop --force` 仍可用）；经环境变量 `PTY_AGENT_SURVIVE` 传递给 daemon，可与 `--foreground` 组合 |

**示例：**

```powershell
python app.py start
# s6 容器服务（run 脚本）：
#   exec python3 /path/app.py start --foreground
# 不可终止的守护进程：
#   python app.py start --survive
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

**说明：** 按连接模式路由：token 模式经 SHM/TCP stop（失败后可用互斥锁定位 PID）；basic 模式直接明文连接目标端口；tls 模式经 TLS 连接远程 daemon（失败回退本地强制终止）。survive 模式下 daemon 拒绝 stop 协议消息，仅 `stop --force`（SIGKILL）可终止。

**示例：**

```powershell
python app.py stop
python app.py stop --force
```

---

### 4.3  status - 查看守护进程运行状态

**用法：**

```bash
python app.py status [公共选项]
```

**输出：** key/value 表格：`running` / `pid` / `port` / `uptime` / `sessions`（active/ended）/ `web`（URL，web 启用时）。daemon 未运行时 `running=no`。

---

### 4.4  list - 列出所有活跃会话

**用法：**

```bash
python app.py list [公共选项]
```

**输出：** 表格 `ID / COMMAND / TIME / STATE`（含已结束会话，`STATE` 为 `running`/`ended`）。无会话时提示 "No active session."。子代理会话的 STATE 列显示 `subagent_<ai_status>`（插件接管渲染）。

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
> **子进程模式** 输出为增量文本，`read` 支持 `--offset` 增量读取；`stderr` 独立返回（`stderrOutput` 字段）。不支持 `--size`/`mouse`/`closewin`/`--snapshot-diff`。

**会话控制选项：**

| 选项                | 说明                                                         |
| ------------------- | ------------------------------------------------------------ |
| `--force-pty-mode`  | 强制模式：忽略 shell 操作符检测，原样拆分执行                 |
| `--shell SHELL`     | 用指定 shell 包装执行命令（如 `bash`/`cmd`/`pwsh`）。命令内的 shell 操作符由该 shell 解析（无需 `--force-pty-mode`）；复杂引号按目标 shell 规则重组保真。不指定时用 `set-default shell`（默认 `none`=无包装）；找不到/不支持的 shell 报错 |
| `--cwd DIR`         | 子进程工作目录（默认取调用方 CLI 的当前目录）                 |
| `--env KEY=VALUE...`| 子进程环境变量（可多次指定，也支持单次多个），例：`--env TERM=xterm-256color COLORTERM=truecolor` |
| `--size WxH`        | 终端尺寸（如 `120x40`，默认 `80x24`；仅新会话创建时生效；运行中调整请用 `--default terminal-size NxN`；子进程模式不支持） |
| `--plugin NAME`     | 挂载插件到会话（可多次指定；按插件形态自动分流：daemon 形态在 daemon 挂载，CLI 形态记录到会话，后续 read/send/mouse 自动回调） |

**触发与超时选项：**

| 选项                        | 说明                                                |
| --------------------------- | --------------------------------------------------- |
| `--trigger PATTERN`, `-t PATTERN` | 触发条件（正则表达式），命中后返回输出，最长 4096 字符 |
| `--newline`                 | 仅在换行后才检查触发条件（默认取配置值）            |
| `--timeout SECS`            | 等待超时秒数（float，默认 120）                     |
| `--idle-timeout SECS`       | 输出静默超时（秒）。程序持续 N 秒无新输出时触发返回  |
| `--idle-after-first-output` | 仅在程序首次输出后才开始检测静默超时                |
| `--notify`                  | 注册通知订阅：命令立即返回（reason=notify_waiting），后台线程继续等待条件，满足后发布通知（用 `wait` 查看摘要，`notice <nid>` 查看完整内容） |

**输出选项：**

| 选项                          | 说明                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| `--full`                      | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |
| `--lines N / start:end`, `-l` | 行数过滤（N=最后 N 行；`start:end`=范围；对 exec/send 默认作用于增量交付块 / 可见屏幕快照，需再加 `--full` 才取全量） |
| `--keep-ansi`                 | 保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）。仅作用于文本快照；SVG/PNG 等图像输出的颜色由各单元格样式决定，与该标志正交 |
| `--snapshot-diff`, `-s`       | 仅返回屏幕变化的行（需快照模式，stream 格式；子进程模式不支持） |
| `--output PATH`, `-o PATH`    | 输出到文件：`.txt/.log`=纯文本；`.svg`=矢量图；`.png/.jpg/.bmp`=位图（daemon 侧 pywezterm 渲染，客户端无需 Pillow） |
| `--response-format {stream,svg}` | 响应格式（默认 stream；svg 需快照模式与运行中会话）        |
| `--svg-compression-level {0,1,2}` | SVG 压缩等级（0=不压缩；1=轻度，默认；2=深度）           |

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

# 异步：--notify 立即返回，后台等待
python app.py exec srv -c "python server.py" --trigger "ready" --notify
```

---
### 4.6  send / advsend - 向运行中的会话发送输入

**用法：**

```bash
python app.py send <id> -i <input> [选项] [公共选项]
python app.py advsend <id> -i <input> [选项] [公共选项]
```

`send` 原样发送输入，不做任何转义；`advsend` 与其参数完全一致，但**恒启用
JSON + 控制字符转义解码**（等价于旧 `send -j` 模式）。

**参数：**

| 参数    | 说明                                                    |
| ------- | ------------------------------------------------------- |
| `id`    | 会话标识（位置参数）                                    |
| `-i/--input` | 要发送的输入文本（最长 65536 字符），**必填选项** |

**输入控制选项：**

| 选项                              | 说明                                                                 |
| --------------------------------- | -------------------------------------------------------------------- |
| `--send-eol {lf,crlf,cr,none}`, `-e ...` | 末尾追加的行尾符（默认按会话模式：pty=`\r` 模拟终端 Enter；subprocess=`\n`）。`cr`=`\r`；`lf`=`\n`；`crlf`=`\r\n`；`none`=不追加。输入已以 `\n`/`\r` 结尾时不重复追加 |

`advsend` 的 JSON 转义支持的序列（名称大小写不敏感）：

```
\n          -> 换行（JSON 反转移，还有 \t \r \uXXXX \" \\ 等标准序列）
{ctrl+a}    -> Ctrl+字母 → ASCII 控制字符（修饰键用 + 连接，如 {ctrl+alt+s}）
{enter}     -> 回车（展开值按会话模式：pty=`\r`、subprocess=`\n`）
{up}/{down}/{left}/{right}
{home}/{end}/{pageup}/{pagedown}
{insert}/{delete}
{f1}..{f12}
{tab}/{esc}/{backspace}    {backtab}（Shift+Tab）  {space}
```

> 字面量 `{` 或 `}` 必须使用反引号转义（`` `{ ``、`` `} ``），单独的 `{`/`}` 会报错。
> 子进程模式使用控制字符会原样把翻译后的字节注入 stdin，是否响应仅看对方程序。
> 若已用 `{enter}` 明确收尾，无需再配 `-e none`（默认行尾不会重复追加）。

**触发与超时选项：**（同 exec）`--trigger / -t`、`--newline`、`--timeout`、`--idle-timeout`、`--idle-after-first-output`、`--notify`

**输出选项：**

| 选项                | 说明                                    |
| ------------------- | --------------------------------------- |
| `--full`            | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |
| `--lines / -l`      | 行数过滤（N=最后 N 行；`start:end`=范围；对 send 默认作用于增量交付块 / 可见屏幕快照，需再加 `--full` 才取全量） |
| `--keep-ansi`       | 保留 ANSI 颜色/样式码                   |
| `--snapshot-diff`, `-s` | 仅返回屏幕变化的行                  |
| `--output / -o`     | 输出到文件                              |
| `--response-format` | 响应格式（stream/svg）                  |
| `--svg-compression-level` | SVG 压缩等级                     |

**示例：**

```bash
# 发送 Python 代码
python app.py send py -i "print(100*100)" -t ">>>"

# 多行代码 (advsend JSON 转义)
python app.py advsend py -i "for i in range(3):\n    print(i)" -t ">>>"

# 发送方向键 (TUI)
python app.py advsend ui -i "{down}" -e none

# 发送 Ctrl+C（advsend 转义；send 原样时 write_ctrl_c 也适用）
python app.py advsend job1 -i "{ctrl+c}" -e none

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

> **说明：** `read` 默认立即返回当前输出（无参数时先等 1s 收集输出）；带 `--trigger` / `--idle-timeout` / 显式 `--timeout` 时进入等待模式，条件命中或超时后返回。

**过滤选项：**

| 选项                    | 说明                                            |
| ----------------------- | ----------------------------------------------- |
| `--lines RANGE`, `-l RANGE` | 行数过滤：PTY/子进程对全量（含 scrollback 历史）或增量输出取**最后 N 行**（`start:end`=范围） |
| `--grep PATTERN`, `-g PATTERN` | 正则匹配过滤行（**仅终端模式**；子进程模式拒绝）。命中行输出格式 `行号:内容`（0-based） |
| `--offset N`            | 增量读取：从指定字节偏移开始（**仅子进程模式**；终端模式拒绝）。与 `lines`/`full`/`snapshot-diff`/等待模式互斥 |
| `--column N`            | 输出第 N 列（1-based 字符位；PTY 快照行与子进程输出行均适用，短行取空） |
| `--full`                | 返回全部内容：PTY = scrollback 历史 + 当前可见区；子进程 = 全部累积输出 |
| `--rf {snapshot,message}` | 响应格式（**subagent 插件注册的选项**）：`snapshot`=屏幕快照（默认）；`message`=最近 N 条结构化消息（需 `-l`，仅子代理会话有意义） |

**触发选项：**（用于等待新输出）`--trigger / -t`、`--newline`、`--idle-timeout`、`--idle-after-first-output`、`--notify`

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

# 增量读取 (从上次 offset 继续，子进程模式)
python app.py read srv

# 只看错误行（终端模式）
python app.py read srv -g "ERROR"

# 取屏幕快照
python app.py read ui

# 取快照差异 (仅变化行)
python app.py read ui -s

# 输出到 SVG 文件
python app.py read ui --response-format svg -o screen.svg

# 子代理会话读取最近 N 条结构化消息（subagent 插件）
python app.py read dev --rf message -l 10
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

**说明：** 终止整个进程树（通过 Job Object 或进程组信号）。会话结束后会变成 `ended` 状态，使用 `kill` 彻底移除并移出会话列表。已结束的归档会话直接删除。

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
- `--since` 与 `--until` 可同时使用

**事件类型：** 记录进程（包括子进程）生命周期：`process_spawn`（启动）、`process_exit`（停止）、`process_crash`（崩溃）、`gui_window`（GUI 窗口出现）。

> **注意：** 不传任何选项时只返回**未消费**的事件；查看完整历史请加 `-l`（如 `-l 10`）。
> 已结束会话从历史存储回放（同样应用过滤）。

**示例：**

```bash
python app.py events py --last 5
python app.py events py --since 14:30
python app.py events py --until 14:45
python app.py events py --since 14:30 --until 14:45   # 同时指定起止
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
| `hwnd` | 窗口句柄（支持十进制或 `0x` 十六进制），必须是会话已跟踪的 GUI 窗口 |

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
| `scroll`              | `<coordinates>` 如 `10,5`             | direction ∈ {up, down}（默认 down），times >= 1 整数（默认 1）；或用 `--grep` |
| `hover`               | `<coordinates>`                        | 或用 `--grep`              |
| `press`               | `<coordinates> <seconds>`              | seconds > 0 浮点；或用 `--grep` |
| `grep`                | `<pattern>`                            | 纯查询，返回所有匹配的首/尾坐标 |
| `_get_cursor_location`| 无参数                                 | 返回光标位置 col,row 及所在行完整内容 |

**坐标系：** 1-based `(col, row)`。col 从 1 开始（左到右），row 从 1 开始（上到下）。与 SGR-1006 鼠标协议一致。

**选项：**

| 选项                        | 说明                                             |
| --------------------------- | ------------------------------------------------ |
| `--button {left,right,middle}` | 鼠标按钮（默认 left；click/drag/press 有效）   |
| `--count {1,2,3}`            | 点击次数（默认 1，仅 click 有效）               |
| `--ctrl`                    | 按住 Ctrl                                        |
| `--shift`                   | 按住 Shift                                       |
| `--alt`                     | 按住 Alt                                         |
| `--grep PATTERN`            | 用正则匹配终端屏幕内容获取坐标。多匹配时不执行动作，返回所有坐标 |
| `--direction {up,down}`     | 滚动方向（默认 down，仅 scroll 有效）                  |
| `--times N`                 | 滚动次数（默认 1，仅 scroll 有效）                    |

**触发/输出选项：**（同 send）`--trigger / -t`、`--newline`、`--timeout`、`--idle-timeout`、`--idle-after-first-output`、`--notify`、`--keep-ansi`、`--snapshot-diff / -s`、`--lines / -l`、`--output / -o`、`--response-format`、`--svg-compression-level`

**`--grep` 行为：**

- 单匹配 → 自动用匹配首坐标执行动作
- 多匹配 → 不执行动作，返回所有 `{"start":{"col":x,"row":y},"end":...}`
- 无匹配 → 返回错误（grep 动作纯查询时返回 "No match found."）

**示例：**

```bash
# 单击
python app.py mouse ui click 10,5

# 右键双击 + 修饰键
python app.py mouse ui click 10,5 --button right --count 2 --ctrl --shift

# 拖拽
python app.py mouse ui drag 10,5 30,5 --button left

# 滚动（方向/次数为选项，坐标可用 --grep 定位）
python app.py mouse ui scroll 10,5 --direction down --times 3
python app.py mouse ui scroll --grep "OK" --direction up

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

### 4.12  wait - 等待通知或指定秒数

**用法：**

```bash
python app.py wait [--timeout SECS] [公共选项]
```

**选项：**

| 选项            | 说明                          |
| --------------- | ----------------------------- |
| `--timeout SECS` | 等待秒数（float，默认 120）  |

**说明：** 守护进程侧等待：
- **有待消费通知**（`--notify` 订阅的）→ 立即返回通知摘要列表（无需等待）
- 无通知 → 等待指定秒数，**通知到达立即唤醒**返回
- 超时 → 返回（无通知）

**输出：** 有待消费通知时渲染 `NID / SESSION / DETAIL / REASON / TIME` 摘要表格（完整内容走 `notice <nid>`）；无通知时提示"无通知"，随后输出状态行 `[wait · ok · <耗时>] waited`。

**示例：**

```bash
python app.py wait --timeout 5
python app.py wait --timeout 300   # 等子代理回合完成通知
```

---

### 4.13  notice - 查看通知的完整内容

**用法：**

```bash
python app.py notice <nid> [公共选项]
```

**位置参数：**

| 参数  | 说明                                             |
| ----- | ------------------------------------------------ |
| `nid` | 通知标识（32 位十六进制串，来自 `wait` 返回的 `notifications[].nid`） |

**说明：** 查看 `--notify` 订阅发布的某条通知的**完整响应内容**（与普通命令回复同结构：commandType/sessionId/outputStream/triggerReturnReason/program 等）。只读不消费：多次查看同一条通知返回同一内容；已消费（被 wait 取走）的通知仍可查询。

**示例：**

```bash
python app.py notice 3f9c2a8b...
```

---
### 4.14  keygen - 生成 Ed25519 公私钥对

**用法：**

```bash
python app.py keygen [--force | -f] [--key-dir DIR] [--comment TEXT | -C TEXT]
```

**选项：**

| 选项                  | 说明                              |
| --------------------- | --------------------------------- |
| `--force`, `-f`       | 覆盖已存在的密钥文件（默认拒绝）  |
| `--key-dir DIR`       | 密钥目录（默认 `<DATA_DIR>/keys`，即 `~/.pty-agent/keys`，支持 `~`/`%VAR%` 展开） |
| `--comment TEXT`, `-C TEXT` | 公钥注释（默认 `用户名@主机名`） |

**输出：**

- OpenSSH 兼容的 `id_ed25519`（私钥，Unix 0600；Windows 下自动收紧 ACL——仅当前用户 + SYSTEM + Administrators）
- `id_ed25519.pub`（公钥，Unix 0644）
- 打印指纹
- 提示追加到服务端 `~/.pty-agent/authorized_keys`

**说明：** 本地命令，无需 daemon 运行。用于 `CONNECT_MODE=tls` 的跨机非对称认证。

**示例：**

```bash
python app.py keygen
python app.py keygen -f -C "user@laptop"
python app.py keygen --key-dir D:\keys
```

---

### 4.15  set-default - 覆盖全局默认配置

默认配置存于**守护进程内存**（不写任何文件，daemon 重启即清空），
影响**之后**的所有会话请求（包括已存在会话的后续请求；`shell` 键真正只对新建会话生效，
`terminal-size` 对运行中会话即刻生效）。命令返回时列出当前全部默认值。

**用法：**

```bash
python app.py set-default <key> <value> [公共选项]
```

**位置参数：**

| 参数    | 说明     |
| ------- | -------- |
| `key`   | 配置键（见下表） |
| `value` | 配置值（布尔用 `on`/`off`/`true`/`false`） |

**可用键：**

| 键                     | 说明                                            |
| ---------------------- | ----------------------------------------------- |
| `timeout`              | 触发超时秒数（float）                          |
| `newline`              | 仅在换行后检查触发条件（bool）                 |
| `keep-ansi`            | 保留 ANSI 颜色/样式码（bool）                   |
| `encoding`             | 终端编码（如 utf-8, gbk）                       |
| `debug`                | 启用 debug 信息输出（bool）                     |
| `send-eol`             | 发送行尾符（lf/crlf/cr/none；未设时按会话模式默认：pty=cr、subprocess=lf）                   |
| `response-format`      | 响应格式（stream/svg）                         |
| `svg-compression-level`| SVG 压缩等级（0/1/2）                          |
| `terminal-size`        | 终端尺寸（如 120x40，须 20-500×5-200）。对运行中的会话：下次 exec/send/read/mouse 携带时即刻 resize |
| `shell`                | 默认 shell 包装（如 `cmd`/`bash`/`pwsh`；`none`=无包装）。仅新会话创建时生效 |

**示例：**

```bash
python app.py set-default timeout 60
python app.py set-default terminal-size 120x40
python app.py set-default response-format svg
```

---

### 4.16  file - 文件工具

文件读写/搜索/传输工具集（read-before-write 状态机、rg 双引擎），内置顶层命令（不依赖插件系统），始终可用。

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

- `file write` / `file edit`（replace/delete）受 read-before-write 状态机保护：必须先 `file read` 且期间未被外部修改（mtime 检查），否则拒绝并提示；内容与现有相同也拒绝。状态在守护进程进程内保存，重启即失效
- `--content-file` / `--old-file` / `--new-file` 在 CLI 侧按 UTF-8 读取（非法编码报错），CRLF 规范化为 LF（与 `file read` 视图一致），内容再走同一传输链路，不受命令行长度限制
- 多行/含特殊字符内容：**必须**先用本地 write 工具写中转文件，再用 `--content-file`/`--old-file`/`--new-file` 传入（避免 Shell 复杂转义与命令行长度上限）
- 每次写操作在 `<DATA_DIR>/history.db` 的 `files_history` 表落版本链（initial → v1 → v2），便于后续回溯；当前不提供查询命令
- `file grep` / `file glob` 结果按文件修改时间最新优先，上限 100 条（超出截断并标记）
- `file upload` / `file download` 走二进制帧传输，不受 JSON 消息长度（MAX_MESSAGE_LENGTH）限制；upload 落盘后写 history 版本链 + 状态机双刷（与 write 一致），download 不落 history；传输中断/超时会清理临时文件

---

### 4.17  plugin - 插件管理

**用法：**

```bash
python app.py plugin list                        # 列出所有已加载插件（含状态/形态）
python app.py plugin ls <id>                     # 列出指定会话挂载的插件
python app.py plugin attach <id> <name>          # 动态挂载插件到运行中的会话
python app.py plugin detach <id> <name>          # 从会话卸载插件
python app.py plugin cmd <id> <name> <command> [args...]  # 调用插件自定义命令
python app.py plugin install <path>              # 从目录安装插件（须含 plugin.json）
python app.py plugin uninstall <name>            # 卸载插件（须先 disable）
python app.py plugin enable <name>               # 启用插件
python app.py plugin disable <name>              # 停用插件
python app.py plugin reload <name>               # 热重载插件（重新加载代码与清单）
python app.py plugin info <name>                 # 插件详情（清单/状态/权限/事件）
python app.py plugin status <name>               # 插件运行状态
python app.py plugin config <name> [key value]   # 查看/修改插件配置
python app.py plugin --gethelp <name>            # 显示插件帮助文档（<插件名>.md）
```

**子命令：**

| 子命令 | 用法 | 说明 |
| ------ | ---- | ---- |
| `list` | `plugin list` | 列出已加载插件（含状态/形态；daemon 侧 + CLI 侧） |
| `ls`   | `plugin ls <id>` | 列出会话挂载的插件 |
| `attach` | `plugin attach <id> <name>` | 动态挂载插件到运行中的会话 |
| `detach` | `plugin detach <id> <name>` | 从会话卸载插件 |
| `cmd`  | `plugin cmd <id> <name> <command> [args...]` | 调用插件自定义命令（参数可选） |
| `install` | `plugin install <path>` | 从目录安装插件（校验清单后复制进 config/plugins，不自动启用） |
| `uninstall` | `plugin uninstall <name>` | 卸载插件（须先 disable；清除代码/数据/状态记录） |
| `enable` | `plugin enable <name>` | 启用插件（on_init → on_enable，订阅事件） |
| `disable` | `plugin disable <name>` | 停用插件（on_disable，释放实例） |
| `reload` | `plugin reload <name>` | 热重载插件（重新导入代码与清单，保持原启用状态；纯 cli 形态在客户端进程内本地重载） |
| `info` | `plugin info <name>` | 插件详情（清单/状态/路径/权限/事件） |
| `status` | `plugin status <name>` | 插件运行状态（info 的子集渲染） |
| `config` | `plugin config <name> [key value]` | 查看配置；`key value` 形式为设置（value 支持 JSON 类型）。**仅内存**：不写任何文件，daemon 重启即恢复 plugin.json 默认值 |
| `--gethelp` | `plugin --gethelp <name>` | 读取并显示插件帮助文档（`<插件名>.md`，按需查看，不自动输出） |

> 插件形态（kind）在 `plugin.json` 清单声明：`cli`=客户端进程内（before_request / transform_response / render_response），`session`/`process`=daemon 侧。`--plugin <name>` 仅在 `exec` 出现：一次性把插件挂载到会话，按 kind 自动分流——CLI 形态记录到会话，后续 `read/send/mouse` 客户端自动挂钩回调（无需再传 `--plugin`）；会话/进程形态在 daemon 挂载。未指定时按插件 `auto_load` 条件自动注入 daemon 插件。

**示例：**

```bash
python app.py plugin list
python app.py plugin ls myapp
python app.py plugin attach myapp files
python app.py plugin cmd myapp files <command>
python app.py plugin info ai
python app.py plugin config files
python app.py plugin config files max_grep_matches 200
python app.py plugin reload files
python app.py plugin --gethelp subagent
```

---

### 4.18  attend - 接管会话为完整实时终端

把当前终端接管为会话的完整实时终端：镜像显示（daemon 原始字节流透传，由本机终端原生渲染，与直接运行一致）+ 输入/鼠标/resize 接管。**不影响 web 端**与其他 CLI 读（走 publisher 推送，不消费共享输出游标）。

```bash
python app.py attend <id> [公共选项]
```

**交互行为：**

- 进入即把会话 PTY resize 到当前终端尺寸，并持续跟随窗口尺寸变化
- `Ctrl+\` 分离（回到原 shell，会话继续运行）
- `Ctrl+C` 透传给会话（与直接运行一致）
- 鼠标：应用启用鼠标追踪时交还应用（点击/滚轮/拖拽）；未启用时可文本选择
- 会话自然结束/被 kill 时自动退出并恢复终端

**说明：**

- 输出为原始字节透传，滚动历史从 attach 起由本机终端累积
- 输入可打印字符（含中文/IME）与特殊键走 daemon 模式感知编码（DECCKM/kitty/CSI-u 全处理）
- subprocess 模式会话：退化为基础文本流（stdout/stderr 双流透传 + 写 stdin），无终端语义

---

### 4.19  子代理命令（smartagent / claude / codebuddy / devin / opencode）

由 `subagent` 插件（`config/plugins/subagent`）注册的 CLI 命令，用于 spawn 编码子代理进程并统一管理其会话。每个命令支持 `exec` 子命令。

**用法：**

```bash
python app.py <agent> exec <id> -p <prompt> [--cwd DIR] [--model MODEL] [--oneshot | --interactive]
# claude / codebuddy / devin / opencode 额外支持：
python app.py <agent> exec <id> -p <prompt> [--program-path PATH] [--oneshot | --interactive]
```

其中 `<agent>` ∈ `smartagent`（Smart Agent） / `claude`（Claude Code） / `codebuddy`（CodeBuddy, cbc） / `devin`（Devin） / `opencode`（OpenCode）。

**选项：**

| 选项 | 说明 |
| ---- | ---- |
| `id` | 会话标识（位置参数） |
| `-p/--prompt PROMPT` | 任务提示词（必填） |
| `--cwd DIR` | 工作目录 |
| `--model MODEL` | 模型名（如 hy3） |
| `--program-path PATH` | 子代理程序路径（不设置时按环境变量（如 `OPENCODE_PATH`）→ PATH 查找，找不到报错）。**仅 claude / codebuddy / devin / opencode 支持**（smartagent 为内置 Python 脚本，无此选项） |
| `--oneshot` | 一次性模式（阻塞，一直等待子代理工作完成，完成后返回完整输出） |
| `--interactive` | 交互模式（不阻塞，返回会话标识；**默认**） |

**交互（interactive）模式：**

- spawn 后返回会话标识，子代理在后台工作；**不支持**返回条件参数与结果处理参数（不要用 `--timeout` 等）
- 读取输出/状态：`app.py read <sid> --rf snapshot`（屏幕快照）或 `app.py read <sid> --rf message -l N`（最近 N 条结构化消息，仅已完成的输出）
- 发消息：`app.py send <sid> -i "消息"`
- 回合完成（或卡权限 / AI 提问）时插件发布通知：`app.py wait` 阻塞等通知，`app.py notice <nid>` 查看内容
- `app.py list` 中该会话 STATE 显示 `subagent_<ai_status>`
- **注意：** `exec`/`read`/`send` 会清除该会话的所有通知，禁止操作后再 `wait`

**示例：**

```bash
# 一次性执行
python app.py codebuddy exec fix -p "仔细探索该仓库" --cwd C:\repo --oneshot

# 交互式多轮（CodeBuddy）
python app.py codebuddy exec dev -p "先看看代码结构，然后把XXXbug修了" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read dev --rf message -l 10              # 看结果
python app.py send dev -i "你的工作还没有完成，给我继续"   # 继续聊天

# 交互式多轮（OpenCode）
python app.py opencode exec octask -p "分析这个仓库的结构" --cwd C:\repo
python app.py wait --timeout 300
python app.py read octask --rf message -l 10           # 看结果（opencode.db 消息）
```

---
## 5. 公共选项参考

以下选项对绝大多数子命令有效（`exec`/`send`/`read`/`mouse`/`events` 等均支持；`keygen`/`set-default` 除外）：

### 5.1  通用选项

| 选项                | 说明                                            |
| ------------------- | ----------------------------------------------- |
| `--encoding ENC`    | 终端编码（如 utf-8, gbk），本次调用记忆。非法编码名（如 zzzz）在解析期报错（退出码 2） |
| `--default KEY VALUE` | 设置默认配置（可多次指定）。可用键同 `set-default`（**不含 `shell`**；`shell` 仅 `set-default` 可设）。`--default terminal-size NxN` 对运行中会话即刻 resize |
| `--debug-output`    | 响应中输出 `debugInformation`（进程树/GUI 窗口/事件/耗时），默认关闭 |

### 5.2  会话 IO 参数组（exec / send / read / mouse 共用）

| 选项                | 说明                                            |
| ------------------- | ----------------------------------------------- |
| `--trigger PATTERN`, `-t` | 触发条件（正则表达式），命中后返回输出       |
| `--newline`         | 仅在换行后才检查触发条件（终端有回显时建议使用，防止输入字符被正则误匹配） |
| `--timeout SECS`    | 等待超时秒数（默认 120，可通过 `--default timeout` 修改） |
| `--idle-timeout SECS` | 输出静默超时（秒）。程序持续 N 秒无新输出时触发返回 |
| `--idle-after-first-output` | 仅在程序首次输出后才开始检测静默超时（初始不检测） |
| `--keep-ansi`       | 保留终端颜色/样式码（默认过滤；清屏/光标等控制序列始终保留）。仅终端模式 |
| `--snapshot-diff`, `-s` | 仅返回屏幕变化的行（需快照模式，stream 格式；子进程模式不支持） |
| `--notify`          | 注册通知订阅：命令立即返回（reason=notify_waiting），后台线程继续等待条件，满足后发布通知。用 `wait` 查看摘要，`notice <nid>` 查看完整内容 |

### 5.3  输出参数组（exec / send / read / mouse 共用）

| 选项                | 说明                                            |
| ------------------- | ----------------------------------------------- |
| `--full`            | 返回全部累积输出而非仅新输出                    |
| `--lines / -l`      | 行数过滤: N=最后N行, start:end=范围              |
| `--output PATH`, `-o` | 输出到文件（.txt/.log=纯文本；.svg=矢量图；.png/.jpg/.bmp=位图，daemon 侧渲染，客户端无需 Pillow） |
| `--response-format {stream,svg}` | 响应格式（默认 stream；svg 需快照模式与运行中会话） |
| `--svg-compression-level {0,1,2}` | SVG 压缩等级（0=不压缩；1=轻度，默认；2=深度） |

### 5.4  配置优先级

```
命令行显式参数  >  --default 覆盖值  >  set-default 全局默认（守护进程内存）  >  代码内置默认值
```

**注意：** `--default terminal-size NxN` 对运行中的会话即刻生效（不是配置，是实时调整终端尺寸）；`--default` 不支持 `shell`（请用 `--shell` 或 `set-default shell`）。

**示例：**

```bash
python app.py exec s1 -c "bash" --encoding gbk
python app.py exec s1 -c "bash" --default timeout 60 --default keep-ansi true
python app.py read s1 --debug-output
```

---

## 6. 返回条件与结果处理

### 6.1  返回条件组合语义

| 条件 | 参数 | 终端模式 | 子进程模式 |
|------|------|----------|-----------|
| 都不带 | | **1s 后返回**（快照） | **1s 后返回**（增量） |
| 只带 trigger | `-t "<regex>"` | 屏幕变化行匹配正则，兜底默认超时 | 增量输出流匹配正则，兜底默认超时 |
| trigger + newline | `-t "<regex>" --newline` | 换行后检查屏幕变化行（输入回显行先被剔除），兜底默认超时 | 换行后检查增量输出流，兜底默认超时 |
| 只带 idle-timeout | `--idle-timeout <S>` | 屏幕 N 秒无变化，兜底默认超时 | 输出流 N 秒无新输出，兜底默认超时 |
| idle + 仅首次输出后 | `--idle-timeout <S> --idle-after-first-output` | 仅在首次输出后检测静默 | 同左 |
| 只带 timeout | `--timeout <S>` | S 秒后返回 | S 秒后返回 |
| GUI 检测 | 检测到 GUI 窗口 | 检测到 GUI 窗口（+ trigger 时不触发 GUI 返回） | 检测到 GUI 窗口 |
| 进程崩溃 | | 退出码非零（`program_crashed`） | 退出码非零（`program_crashed`） |
| 程序退出 | | 退出码为零（`program_ended`） | 退出码为零（`program_ended`） |

> **注意：** `--idle-after-first-output` 需配合 `--idle-timeout` 使用，单独设置无效。
> idle-timeout 从最后输出到达时开始计时；若 stdout 有块缓冲（如 `python -c` 未加 `-u`），输出可能延迟到达，idle 在缓冲 flush 前触发 → 可能返回空输出（数据未丢，可后续 read）。
> 建议：如果要带其他条件，把 timeout 也带上并设合理值，避免默认 120s 卡死。
> 建议：高效利用条件返回，及时根据输出更新条件（特别是 `-t`），灵活使用；不建议反复 `send` 后又 `read`，尽量一次性设置最强返回条件。

### 6.2  结果处理参数

条件命中后对结果的处理：

| 选项 | 子进程模式 | 终端模式 |
|------|-----------|----------|
| `-l N` | 取累积输出的最后 N 行 | 取全量输出的最后 N 行 |
| `-l start:end` | 取累积输出中指定范围的行 | 取全量输出中指定范围的行 |
| `-g "<regex>"` | 不支持 | 用可见屏幕的每一行匹配（格式 `行号:内容`，0-based） |
| `--offset <bytes>` | 从指定字节偏移开始增量读取 | 不支持 |
| `--full` | 返回全部累积输出（慎用，数据量大） | 返回全量输出（scrollback + 可见区） |
| `-s/--snapshot-diff` | 不支持 | 仅返回与上次可见屏幕相比变化的行（格式 `行号:内容`） |
| `--column N` | 按字符位取第 N 列（短行取空） | 取可见屏幕的第 N 列 |
| `-o/--output <path>` | 写入文件 | 写入文件 |
| `--response-format stream\|svg` | 仅 stream | 支持 stream 与 svg |
| `--svg-compression-level 0/1/2` | 不支持 | 压缩等级（0 仅移除空标签；1 轻度；2 深度，需 scour） |

> 注：`-l` 的"累积/全量"语义仅对 `read` 成立；`exec`/`send` 的 `-l` 默认作用于增量交付块/可见屏幕快照，需再加 `--full` 才取全量。

### 6.3  输出格式与渲染

PTY-Agent 的 CLI 输出**不再以原始 JSON 呈现**，而是由 Presenter 渲染为可读文本：

- **内容**（程序输出/表格主体/配置/原始文本）→ **stdout**
- **元信息**（状态行/原因/hint）→ **stdout 底部**
- **消息/警告/错误**（`(PTY-Agent message: ...)`）→ **stderr**
- **错误** → **stderr + 非零退出码**

**分隔线内嵌标签：**

```
─────────────────────────────────── snapshot ───────────────────────────────────
Python 3.11.9 (tags/v3.11.9:de54cf5, ...)
>>> 
────────────────────────────────────────────────────────────────────────────────
[exec · matched · 1.23s]  py  running  pty
```

标签由响应格式生成：`tail:N`（`-l N`）、`lines:A:B`（`-l start:end`）、`snapshot`、`diff`、`full`、`col:N`、`match:pattern`。

**状态行格式：** `[cmd · reason · elapsed]  session  state  mode`

- `reason` 短标签：`ok` / `matched` / `timeout` / `idle` / `ended` / `crashed`(exit_code: N) / `gui` / `cancelled` / `notify`
- `state` ∈ `running` / `ended`
- `mode` ∈ `pty` / `subprocess`

**mouse grep/cursor 输出：**

```
[grep "Error"] row=5 col=10..15
[cursor] col=5 row=6 line='...'
```

**debug 输出（`--debug-output`）：**

```
[debug]
elapsed: 1234 ms
process: [1234] C:\path\to\program.exe
gui: MyApp Window
  14:30:00.12  process_spawn  pid=1234
```

---

## 7. 通知机制（--notify / wait / notice）

PTY-Agent 支持异步通知机制：`exec`/`send`/`read`/`mouse` 均可带 `--notify` 标志，使命令立即返回（reason=notify_waiting），后台线程继续等待返回条件，条件满足时发布通知。

```bash
# 1. 启动异步等待
python app.py exec srv -c "python server.py" --trigger "ready" --notify

# 2. 查看通知摘要
python app.py wait --timeout 300
# 输出：
# NID                               SESSION  DETAIL    REASON   TIME
# --------------------------------  -------  --------  -------  --------------------
# 3f9c2a8b...                       srv      srv已完成  matched  14:30:00
# [wait · ok · 1.23s] waited

# 3. 查看通知完整内容
python app.py notice 3f9c2a8b...
# 返回完整命令响应（commandType/sessionId/outputStream/triggerReturnReason/program 等）
```

**关键设计：**

- 通知存于守护进程内存（不落盘，daemon 重启即清空），每会话最多 50 条，超限淘汰最旧
- 摘要列表（`wait` 消费）移入归档（最多 200 条），不删除——`notice` 仍可查看已消费通知
- 操作型命令（exec/send/read/mouse/kill）请求到达时自动消费该会话的所有通知
- `pendingNotifCount` 出现在每次命令响应中（告知还有多少待消费通知）
- 通知 nid 用 uuid4().hex 生成

---
## 8. 配置系统

**配置目录：** `config/`

**加载机制：** 从 TOML 文件展平为模块级常量，所有配置 key 均可用环境变量 `PTY_AGENT_<KEY>` 覆写（**优先级：环境变量 > 文件**）。

```
common.py    = common.toml + 运行时计算（IS_WINDOWS / DATA_DIR / PROJECT_ROOT）
shared.py    = shared.toml（协议缓冲 / IPC 命名 / daemon 控制超时）
daemon.py    = daemon.toml + logging.toml + web.toml（可选）+ shared.toml
client.py    = client.toml + logging.toml + shared.toml
transfer.py  = transfer.toml
sandbox.py   = daemon/sandbox.toml（可选，缺失即沙箱关闭）
plugins.py   = config/plugins/ 目录发现（plugin.json）+ registry.json（可选，缺失即插件系统禁用）
```

**可选配置缺失行为：** `web.toml` 缺失时视为 web 未启用（`ENABLE_WEB=False`，连带 VNC/FastScreen 禁用，守护进程正常启动）；`registry.json` 缺失时插件系统禁用；`sandbox.toml` 缺失时沙箱关闭。`vnc.toml`/`vnc.example.toml` 为 winvnc.exe 运行时配置，Python 不加载。

**数据目录：** `<DATA_DIR>/`（由 `common.toml [paths] DATA_DIR` 配置，默认 `~/.pty-agent`，支持 `~` 与 `%VAR%/$VAR` 展开）

| 路径               | 说明                       |
| ------------------ | -------------------------- |
| `keys/`            | Ed25519 密钥               |
| `authorized_keys`  | 公钥白名单（服务端）       |
| `known_hosts`      | TOFU 信任存储（客户端）    |
| `certs/`           | TLS 自签证书               |
| `logs/`            | 日志文件（`LOG_DIR` 由此派生） |

### 8.1  common.toml - 共有配置

```toml
[paths]
DATA_DIR = "~/.pty-agent"       # 运行时数据根目录（日志/单实例锁/SHM 回退/插件存储/history.db）

[terminal]
DEFAULT_COLS = 80
DEFAULT_ROWS = 24

[compression]
GZIP_COMPRESS_LEVEL = 2

[input_limit]
MAX_SESSION_ID_LEN = 128
MAX_COMMAND_LEN    = 65536
MAX_INPUT_LEN      = 65536
MAX_PATTERN_LEN    = 4096
```

### 8.2  shared.toml - 共享配置（协议 / IPC / daemon 控制）

```toml
[protocol]
SOCKET_RECV_BUFSIZE = 65536
MAX_MESSAGE_LENGTH  = 50331648     # 48 MB

[ipc]
SINGLE_INSTANCE_MUTEX_NAME = "Local\\PTYAgentSingleInstance"
AUTH_TOKEN_NAME = "Local\\PTYAgentAuth"
AUTH_TOKEN_SIZE = 64
HMAC_KEY_NAME   = "Local\\PTYAgentHmac"
HMAC_KEY_SIZE   = 64

[daemonctl]
DAEMON_START_TIMEOUT       = 3.0
DAEMON_START_POLL_INTERVAL = 0.3
PING_TIMEOUT               = 1.0
STOP_TIMEOUT               = 3.0
PROCESS_EXIT_WAIT_RETRIES  = 10
PROCESS_EXIT_WAIT_INTERVAL = 0.1
```

### 8.3  daemon.toml - 守护进程配置

```toml
SINGLE_INSTANCE = true            # 单实例互斥锁开关（false 时仅 basic/tls 场景允许多实例并存）

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
SOCKET_LISTEN_BACKLOG   = 128
PTY_READ_SIZE           = 65536
MAX_CONNECTIONS         = 100     # 全局并发连接数上限（Slowloris 防护）
CONNECTION_READ_TIMEOUT = 30.0    # 连接读请求超时（秒）

[input_limit]
MAX_SESSIONS = 50

[workflow]
WORKFLOW_MAX_RUNS          = 50
WORKFLOW_DEFAULT_PARALLEL  = 4
WORKFLOW_STEP_OUTPUT_LIMIT = 4096
WORKFLOW_MAX_FILE_SIZE     = 20971520

[auth]
# Token 认证（[listener] TOKEN_ENABLED=true 时生效）
AUTH_TOKEN_ROTATE_INTERVAL = 1800
AUTH_TOKEN_GRACE_PERIOD    = 120

# 公私钥认证（[listener] TLS_ENABLED=true 时生效）
# 路径字段为空 = 默认位于 <DATA_DIR> 下，支持 ~ 与 %VAR%/$VAR 展开
PUBKEY_ALGORITHM       = "ed25519"
PUBKEY_AUTHORIZED_KEYS = ""             # 默认 <DATA_DIR>/authorized_keys
PUBKEY_KEY_DIR         = ""             # 默认 <DATA_DIR>/keys

# TLS 服务端（[listener] TLS_ENABLED=true 时生效）
TLS_CERT_DIR           = ""             # 默认 <DATA_DIR>/certs
TLS_CERT_FILE          = ""             # 默认 <DATA_DIR>/certs/daemon.crt
TLS_KEY_FILE           = ""             # 默认 <DATA_DIR>/certs/daemon.key
TLS_CERT_VALIDITY_DAYS = 365
TLS_CERT_SUBJECT_CN    = "pty-agent-daemon"
```

### 8.4  client.toml - 客户端配置

```toml
[connection]
# 客户端连接方式，三选一，必须与 daemon 侧 [listener] 对应监听器 enabled 匹配
CONNECT_MODE = "token"        # basic / token / tls

# basic 模式连接位置（CONNECT_MODE=basic 时生效）
BASIC_HOST     = "127.0.0.1"
BASIC_PORT     = 10521
BASIC_PASSWORD = ""             # 须与 daemon 侧一致

# token 模式连接位置（CONNECT_MODE=token 时生效，本机）
TOKEN_HOST = "127.0.0.1"
TOKEN_PORT = 10520

# tls 模式连接位置（CONNECT_MODE=tls 时生效）
TLS_HOST = ""                 # 远程 daemon TLS 监听器地址
TLS_PORT = 18767

[timeout]
CONNECT_TIMEOUT         = 30.0
DEFAULT_TRIGGER_TIMEOUT = 120.0

[auth]
# 公私钥认证（CONNECT_MODE=tls 时生效）
PUBKEY_PRIVATE_KEY_PATH = ""          # 默认 <DATA_DIR>/keys/id_ed25519
KNOWN_HOSTS_FILE        = ""          # 默认 <DATA_DIR>/known_hosts
TOFU_STRICT             = true        # true=指纹不匹配拒绝，false=仅警告
```

### 8.5  日志配置

日志系统位于 `src/logging/` 子包，基于异步队列（`QueueHandler` + 后台单线程 `pty-log-writer`），业务线程零阻塞。日志文件写入 `<DATA_DIR>/logs/`，按模块分组拆分（时间戳命名），后台线程自动将前一日 `*.log` gzip 归档为 `.log.gz`。

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

### 8.6  web.toml - Web 服务器配置（可选）

> 本文件可选：缺失时视为 web 未启用（`ENABLE_WEB=False`，连带 `ENABLE_VNC`/`ENABLE_FASTSCREEN` 禁用）。

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
FASTSCREEN_DEFAULT_FPS      = 30
FASTSCREEN_DEFAULT_BITRATE  = 2_000_000
FASTSCREEN_DEFAULT_STREAM_FORMAT = "mse"   # auto/mjpeg/mse/webcodecs

[web_settings]
RIKKA_ENABLED         = true       # 桌宠
DEFAULT_THEME         = "dark"     # light/dark/system
IME_ENABLED           = true       # 启用 Web RIME
IME_CANDIDATE_COUNT   = 5
IME_VERTICAL          = false
IME_DEFAULT_STATE     = "chinese"  # chinese/english/last
IME_KEYBOARD_LAYOUT   = "compact"  # compact/full
IME_TOOLBAR_DISPLAY   = "always"   # never/desktop_only/always
```

### 8.7  sandbox.toml - 沙箱配置（可选，Windows）

> 缺失即沙箱关闭。沙箱会话 = 原生 win_sandbox_native（pybind11 in-process）启动进程（WRITE_RESTRICTED 受限令牌 + Job Object 隔离）。

```toml
[sandbox]
enabled = false                     # 沙箱会话开关；false = 会话走原生 PTY 后端
log_level = "info"                  # trace|debug|info|warn|error

[quota]                             # 资源配额（0 = 不限制）
memory_mb = 0                       # 进程内存上限（MB）
cpu_ms = 0                          # CPU 时间上限（ms，硬限制）
cpu_rate_percent = 0                # CPU 速率限制（%）
max_processes = 0                   # Job 内最大进程数
wall_clock_timeout_ms = 0           # 墙钟超时（ms）
crash_silent = true                 # 崩溃静默：不弹窗不触发 WER

[isolation]
net_policy = "unrestricted"         # unrestricted / allowlist（WFP+SOCKS5）
net_allowlist = []
clipboard_isolate = false           # 剪贴板隔离
```

### 8.8  transfer.toml - 文件传输协议配置

```toml
[transfer]
TRANSFER_CHUNK_SIZE = 262144        # 传输数据帧大小（256KB）
TRANSFER_MAX_FILES = 100000         # 单次传输条目数上限
TRANSFER_MAX_CONTROL = 16777216     # 控制帧 payload 上限（16MB）
TRANSFER_MAX_SIZE = 0               # 单文件大小上限；0 = 无限制
TRANSFER_TMP_SUFFIX = ".pty-tmp"    # 传输临时文件后缀
TRANSFER_TIMEOUT = 120              # file upload/download 默认总时限（秒）
```

### 8.9  客户端 ConfigManager 默认值

| 配置项                    | 默认值    | 说明                       |
| ------------------------- | --------- | -------------------------- |
| `timeout`                 | 120.0     | 触发超时秒数               |
| `newline`                 | False     | 仅在换行后检查触发条件     |
| `encoding`                | None      | 终端编码（None=自动探测）  |
| `keep_ansi`               | False     | 保留 ANSI 颜色/样式码      |
| `debug`                   | False     | 启用 debug 信息输出        |
| `send_eol`                | 按模式 | 发送行尾符（未设时按会话模式默认：pty=`\r`、subprocess=`\n`） |
| `response_format`         | stream    | 响应格式（stream/svg）     |
| `svg_compression_level`   | 1         | SVG 压缩等级               |
| `terminal_size`           | 80x24     | 终端尺寸                   |
| `shell`                   | None      | 默认 shell 包装（仅 set-default 可设） |

> AI 分析不再作为配置项：已移出主程序为 CLI 插件（`config/plugins/ai`），
> 经 `exec --plugin ai` 挂载到会话后自动回调，无 `ai_analyse` / `ai_prompt` 配置。

---

## 9. 认证与监听方式

支持三种连接方式，通过 `client.toml [connection]` 的 `CONNECT_MODE` 选择连接哪个 daemon 监听器，
各自须与 `daemon.toml [listener]` 对应监听器的 enabled 状态匹配：

| `CONNECT_MODE` | 监听器 | 认证方式 | 适用场景 |
|----------------|--------|----------|----------|
| `token`（默认） | `[listener] TOKEN_ENABLED` | Token + HMAC（本机，SHM 分发） | 本机 IPC |
| `basic` | `BASIC_ENABLED` | 共享密码（密码即 HMAC 密钥，空密码=无认证） | 可信局域网，明文直连 |
| `tls` | `TLS_ENABLED` | TLS + Ed25519（跨机，TOFU 信任） | 跨机安全访问 |

### 9.1  token - 本机 Token + HMAC 认证（默认）

- `daemon.toml [listener] TOKEN_ENABLED = true`，监听 `TOKEN_HOST`:`TOKEN_PORT`（默认 127.0.0.1:10520）
- `client.toml [connection] CONNECT_MODE = "token"`，目标 `TOKEN_HOST`:`TOKEN_PORT`
- 守护进程启动时生成随机 Token 与 HMAC 密钥，写入共享内存
- 客户端从 SHM 读取，每次消息附加 HMAC-SHA256 签名
- Token 定期轮换（`AUTH_TOKEN_ROTATE_INTERVAL=1800s`），有宽限期（`AUTH_TOKEN_GRACE_PERIOD`）
- 仅限本机使用（SHM 隔离）

### 9.2  basic - 共享密码认证（跨机/局域网直连）

- `daemon.toml [listener] BASIC_ENABLED = true`，监听 `BASIC_HOST`:`BASIC_PORT`（默认 0.0.0.0:10521）
- `client.toml [connection] CONNECT_MODE = "basic"`，目标 `BASIC_HOST`:`BASIC_PORT`
- 明文传输，无加密；密码认证依赖配置的共享密码 `BASIC_PASSWORD`（两侧须一致）：
  - 非空：密码即 HMAC 密钥，双向签名 + 密码身份校验，防伪造防篡改
  - 空：退化为无认证，仅用于完全可信环境
- 密码以明文形式出现在请求消息中，可被局域网嗅探，对外暴露需谨慎

### 9.3  tls - 跨机 TLS + Ed25519 公私钥认证

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
## 10. 构建脚本 BUILD.py

**用法：**

```bash
python BUILD.py [选项]
```

发布构建脚本（Python，需 3.8+）。重建发布目录 `pty-agent/`，下载 fastscreen / sandbox（win_sandbox_native.pyd）/ wezterm-py，并下载 aichat / ripgrep / UltraVNC / terminal_injector / MapleMono 字体。构建/下载产物统一先落入源目录基础包（`bin/`、`src/web/static/vendor/rime/`、`src/assets/fonts/`），最后整体复制进发布目录并打包 zip。

**选项：**

| 选项                              | 说明                                          |
| --------------------------------- | --------------------------------------------- |
| `-NoAichat`                       | 跳过 aichat 下载                              |
| `-NoFastscreen`                   | 跳过 fastscreen.dll 下载（Windows 专属）      |
| `-NoWinsandbox`                   | 跳过 win_sandbox_native.pyd 编译（Windows 专属） |
| `-NoWeztermPy`                    | 跳过 pywezterm 下载（PTY 后端）              |
| `-NoUltravnc`                     | 跳过 UltraVNC 下载（Windows 专属）            |
| `-NoTerminalInjector`             | 跳过 terminal_injector 下载（Windows 专属）   |
| `-NoRime`                         | 跳过 rime-plugin 构建                         |
| `-NoRg`                           | 跳过 ripgrep 下载                             |
| `-NoFonts`                        | 跳过 MapleMono 字体下载                       |
| `-Mirror <url>` / `-m <url>`      | GitHub 下载镜像（亦可用 `$env:GITHUB_MIRROR`）|
| `-ApiMirror <url>` / `-am <url>`  | GitHub API 镜像（默认 `https://api.github.com`） |

**环境变量：** 命令行 `-NoX` 优先于对应环境变量。

| 变量                       | 默认                      | 说明                    |
| -------------------------- | ------------------------- | ----------------------- |
| `GITHUB_MIRROR`            | `""`                      | GitHub 下载镜像前缀     |
| `GITHUB_API_MIRROR`        | `https://api.github.com`  | GitHub API 镜像         |
| `DOWNLOAD_AICHAT`          | `true`                    | 是否下载 aichat         |
| `BUILD_FASTSCREEN`         | `true`                    | 是否下载 fastscreen.dll |
| `BUILD_WINSANDBOX`         | `true`                    | 是否编译 win_sandbox_native.pyd |
| `BUILD_WEZTERMPY`          | `true`                    | 是否下载 pywezterm（PTY 后端） |
| `DOWNLOAD_ULTRAVNC`        | `true`                    | 是否下载 UltraVNC       |
| `DOWNLOAD_TERMINALINJECTOR`| `true`                    | 是否下载 terminal_injector |
| `BUILD_RIME`               | `true`                    | 是否构建 rime-plugin    |
| `DOWNLOAD_RG`              | `true`                    | 是否下载 ripgrep        |
| `DOWNLOAD_FONTS`           | `true`                    | 是否下载 MapleMono 字体 |

**构建流程：**

1. 清空并重建发布目录 `pty-agent/`
2. 构建 rime-plugin（`web_rime/plugin`，npm run build，产物落入 `src/web/static/vendor/rime/`）
3. 从 GitHub Releases 下载 `fastscreen.dll`（按当前平台架构 x86_64/x86/arm64，走镜像），产物落入 `bin/fastscreencore/`
4. 用 cmake + Ninja + vcvars64 编译 `win_sandbox_native.pyd`（`sandbox/src`，pybind11），产物落入 `bin/win_sandbox/_native/`
5. 从 GitHub Releases 下载 pywezterm wheel（按当前平台架构，走镜像），解包 wheel 落入 `bin/pywezterm/`
6. 从 `sigoden/aichat` releases 下载 `aichat.exe` 到 ai 插件目录 `config/plugins/ai/bin/`
7. 从 `BurntSushi/ripgrep` releases 下载 `rg`（按系统架构 x86_64/aarch64）到 `bin/rg/`
8. 下载 `UltraVNC_1824.zip`（按 x64/x86 架构）到 `bin/ultravnc/`
9. 下载 `terminal_injector_x64_v1.0.zip` 到 `bin/terminal_injector/`
10. 下载 MapleMono NF CN 字体到 `src/assets/fonts/`
11. 复制基础包（`src/`、`bin/`、`config/plugins/`、`app.py`、`SKILL.md`）到 `pty-agent/`
12. 清理 `__pycache__`、`.gitkeep`、日志文件，删除发布目录冗余文件（rime-plugin 的 ESM 变体与 source map、ai 插件 config.yaml、vnc.toml/vnc.example.toml、ultravnc 的 `.log`/`.ini`）
13. 打包发布 zip

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

## 11. 启动/停止/重启脚本

### 11.1  Unix（Bash）

```bash
./stop.sh [--force]           # 停止守护进程（强制）
./restart.sh                  # 强制停止 -> 等待 1 秒 -> 启动
```

`restart.sh` 流程：

```bash
python -m src stop --force
sleep 1
python -m src start
```

### 11.2  Windows（PowerShell）

仓库不提供 `.ps1` 脚本，使用直接命令：

```powershell
python app.py start
python app.py stop [--force]
python app.py status
# 重启无独立命令:
python app.py stop --force; python app.py start
```

---

## 12. 辅助工具 (bin/)

### 12.1  aichat - AI 聊天工具

- **路径：** `config/plugins/ai/bin/aichat.exe`（由 `BUILD.py` 下载）
- **用途：** CLI 级 `ai` 插件（`config/plugins/ai`）对内调用，对响应做二次分析

### 12.2  cursorlocator - 光标定位器

- **路径：** `bin/cursorlocator/`
- **用途：** Win32 API + 渲染（`ring_worker.py`、`win32_api.py`、`pixel_color.py`、`rendering.py`、`config.py`）
- **可选：** 仅 web 启用时经 `src/optional` 惰性加载；缺失时 web 禁用光标定位功能（`is_available()` 返回 False）

### 12.3  fastscreencore - 屏幕捕获核心

- **路径：** `bin/fastscreencore/`
- **用途：** DXGI/WGC/BitBlt 屏幕捕获（`capture.py`、`_core.py`、`fastscreen.dll`）
- **可选：** 仅 web + screenshare 启用时经 `src/optional` 惰性加载；整体缺失时 web 禁用 FastScreen 功能

### 12.4  pproxy - 加密 SOCKS5 代理

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

### 12.5  pywezterm - wezterm 终端引擎绑定

- **路径：** `bin/pywezterm/`（由 `BUILD.py` 编译 `wezterm-py` 解包 wheel）
- **内容：** `pywezterm.pyd`（Rust pybind11 扩展）+ `conpty.dll` + `OpenConsole.exe`
- **用途：** PTY 后端（ConPTY）、终端模型（VT 解析）、SVG/位图渲染（daemon 侧）

### 12.6  rg - ripgrep 搜索工具

- **路径：** `bin/rg/`（由 `BUILD.py` 下载）
- **用途：** `file grep` / `file glob` 的搜索引擎（缺失自动降级纯 Python 实现）

### 12.7  terminal_injector - 终端注入工具

- **路径：** `bin/terminal_injector/`（由 `BUILD.py` 下载 `terminal_injector_x64_v1.0.zip`）
- **用途：** 强制劫持已运行的控制台程序供会话使用

**用法：**

```bash
terminal_injector.exe --list-targets --json        # 查看可劫持的窗口
terminal_injector.exe --mediator --target-pid $pid # 劫持桥接

# 通过 CLI 调用:
app.py exec sid -c "terminal_injector.exe --mediator --target-pid $pid" \
    --timeout 15
```

> 不建议单独执行 `--inject <pid>`，直接用 PTY-Agent 执行 `--mediator` 即可。

### 12.8  win_sandbox - Windows 沙盒

- **路径：** `bin/win_sandbox/_native/win_sandbox_native*.pyd`（由 `BUILD.py` 编译 `sandbox/src` 复制到 `bin/win_sandbox/_native/`）
- **内容：** pybind11 原生扩展（C++ 核心，`sandbox/` 工程）
- **用途：** 沙箱会话后端（WRITE_RESTRICTED 受限令牌 + Job Object 隔离，进程内直调，无 IPC 管道）
- **加载：** 经 vendored 包 `bin/win_sandbox`（`__init__.py` 把 `_native/` 加入 sys.path）
- **可选：** 仅 `sandbox.toml` 启用沙箱时加载；缺失时 `src/sandbox/manager.py` 惰性导入返回不可用，`start()` 抛 `SandboxError`，不中断 daemon 启动

### 12.9  ultravnc - UltraVNC 远程桌面

- **路径：** `bin/ultravnc/`（由 `BUILD.py` 下载 `UltraVNC_1824.zip` 到该目录）
- **用途：** VNC 远程桌面
- **可选：** 仅 web + VNC 启用时经 `src/optional` 惰性加载；`winvnc.exe` 缺失时 `is_available()` 返回 False，web 前端隐藏 VNC 入口

---

## 13. 环境变量

### 13.1  配置覆写（PTY_AGENT_<KEY>）

所有 TOML 配置 key 均可用环境变量覆写，**优先级：环境变量 > 文件**：

- 变量名 = `PTY_AGENT_` + 配置 key（大写），如 `DATA_DIR` → `PTY_AGENT_DATA_DIR`、`ENABLE_WEB` → `PTY_AGENT_ENABLE_WEB`、`CONNECT_MODE` → `PTY_AGENT_CONNECT_MODE`
- 取值按文件原值类型转换：bool 接受 `true/false/1/0/yes/no/on/off`；int/float 直接转换；list/dict 按 JSON 解析；str 原样。转换失败时警告并保留文件值
- 仅对配置中已存在的 key 生效；运行时常量（IS_WINDOWS/PROJECT_ROOT/LOG_DIR 等）不参与覆写
- sandbox 配置按 `<节名>_<键名>` 展平覆写（如 `PTY_AGENT_SANDBOX_ENABLED`、`PTY_AGENT_QUOTA_MEMORY_MB`）
- `PTY_AGENT_CONFIG_DIR` 为加载器专属变量（测试隔离重定向配置目录），非配置 key

### 13.2  连接与认证相关（client.toml 配置键）

| 变量                   | 说明                                       |
| ---------------------- | ------------------------------------------ |
| `CONNECT_MODE`         | 客户端连接方式: basic/token/tls             |
| `BASIC_HOST`/`BASIC_PORT`/`BASIC_PASSWORD` | 明文监听器地址/端口/共享密码（空=无认证） |
| `TOKEN_HOST`/`TOKEN_PORT`   | 本机 token 监听器地址/端口           |
| `TLS_HOST`/`TLS_PORT`       | 远程 TLS 监听器地址/端口            |
| `PUBKEY_PRIVATE_KEY_PATH`  | 客户端 Ed25519 私钥路径           |
| `KNOWN_HOSTS_FILE`     | TOFU 信任存储路径                          |
| `TOFU_STRICT`          | TOFU 严格模式                              |

### 13.3  运行时环境变量

| 变量                   | 说明                                       |
| ---------------------- | ------------------------------------------ |
| `PTY_AGENT_SURVIVE`    | start --survive 生存模式标记（daemon 忽略结束信号与 stop） |
| `PTY_AGENT_CONFIG_DIR` | 重定向配置目录（测试/部署用）              |
| `PTY_PLUGIN_DIRS`      | 追加插件发现目录（os.pathsep 分隔）        |

### 13.4  BUILD.py 环境变量

| 变量                       | 说明                                          |
| -------------------------- | --------------------------------------------- |
| `GITHUB_MIRROR`            | GitHub 下载镜像                               |
| `GITHUB_API_MIRROR`        | GitHub API 镜像（默认 `https://api.github.com`） |
| `DOWNLOAD_AICHAT`          | 是否下载 aichat（默认 true）                  |
| `BUILD_FASTSCREEN`         | 是否下载 fastscreen.dll（默认 true）          |
| `BUILD_WINSANDBOX`         | 是否编译 win_sandbox_native.pyd（默认 true）  |
| `BUILD_WEZTERMPY`          | 是否下载 pywezterm（PTY 后端，默认 true）     |
| `DOWNLOAD_ULTRAVNC`        | 是否下载 UltraVNC（默认 true）                |
| `DOWNLOAD_TERMINALINJECTOR`| 是否下载 terminal_injector（默认 true）       |
| `BUILD_RIME`               | 是否构建 rime-plugin（默认 true）             |
| `DOWNLOAD_RG`              | 是否下载 ripgrep（默认 true）                 |
| `DOWNLOAD_FONTS`           | 是否下载 MapleMono 字体（默认 true）          |

### 13.5  子进程环境变量

通过 `exec --env KEY=VALUE`（可多个）为子进程设置额外环境变量，合并到继承的环境中。典型用于设置：

```
TERM=xterm-256color
COLORTERM=truecolor
LANG/LC_ALL
```

---
## 14. 典型工作流

### 14.1  交互式 Python REPL

```bash
python app.py exec py -c "python -u -i" -t ">>>"
python app.py send py -i "print(100*100)" -t ">>>"
# 预期: >>> 10000\n>>>
python app.py advsend py -i "for i in range(3):\n    print(i)" -t ">>>"
# 预期: >>> 0\n1\n2\n>>>
python app.py read py --lines 10
python app.py events py --last 5
python app.py kill py
```

### 14.2  长时运行服务器

```bash
python app.py exec srv -c "python server.py" --timeout 10
python app.py read srv -l 20        # 中途查看最近 20 行
python app.py read srv              # PTY 模式返回屏幕快照；子进程模式则从上次 offset 增量读取
python app.py read srv -g "ERROR"   # 只看错误行（终端模式）
python app.py kill srv
```

### 14.3  下载大文件 / 编译

```bash
python app.py exec dl -c "curl -O https://example.com/largefile.zip" \
    --trigger "100%" --timeout 600 --idle-timeout 30
python app.py read dl
python app.py read dl --trigger "100%|error|warning" --timeout 600
python app.py kill dl
```

> 如果下载很慢，用 `--timeout` 给足时间，但必须设置 `--idle-timeout` 防止卡死。编译同理。

### 14.4  容易崩溃的程序

```bash
python app.py exec job1 -c "python worker.py" --idle-timeout 5
# 进程崩溃时 triggerReturnReason="program_crashed"
# program.exitCode 非零
python app.py events job1 -l 10      # 查看崩溃事件详情（process_crash 类型）
```

### 14.5  TUI 程序交互

```bash
python app.py exec ui -c "mimo.exe" \
    --response-format svg --timeout 5
python app.py send ui -i "j" --send-eol cr --timeout 5 -s
python app.py read ui -s
python app.py send ui -i "帮我写一个贪吃蛇游戏" -s
python app.py kill ui
```

### 14.6  自定义终端尺寸

```bash
python app.py exec wide -c "htop" --size 120x40 --timeout 5
python app.py exec s1 -c "cmd" --default terminal-size 100x30
python app.py --show-config terminal-size
```

### 14.7  调试器交互（cdb/gdb）

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

### 14.8  鼠标操作

```bash
python app.py mouse ui click 10,5
python app.py mouse ui click 10,5 --button right --count 2 --ctrl --shift
python app.py mouse ui drag 10,5 30,5 --button left
python app.py mouse ui scroll 10,5 --direction down --times 3
python app.py mouse ui hover 10,5
python app.py mouse ui press 10,5 2.0 --button middle
python app.py mouse ui grep "Error"                  # 纯查询返回坐标
python app.py mouse ui _get_cursor_location          # 获取光标位置
python app.py mouse ui click --grep "OK"             # grep 自动定位
python app.py mouse ui click 10,5 --timeout 5
python app.py mouse ui click 10,5 -t ">>>" --timeout 10
```

### 14.9  控制字符发送（advsend JSON 转义模式）

```bash
python app.py advsend myid -i "import os\nprint(os.name)" -t ">>>"
python app.py advsend myid -i "{down}" -e none               # TUI 方向键
python app.py advsend myid -i "{ctrl+c}" -e none             # 发送 Ctrl+C
python app.py advsend myid -i "{enter}"                      # 回车（pty→\r，subprocess→\n）
```

### 14.10  异步通知（--notify / wait / notice）

```bash
# 启动异步等待
python app.py exec srv -c "python server.py" --trigger "ready" --notify
# 稍后取回
python app.py wait --timeout 300
python app.py notice <nid>
```

### 14.11  子代理（交互式多轮）

```bash
python app.py codebuddy exec dev -p "先看看代码结构，然后把XXXbug修了" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read dev --rf message -l 10              # 看结果
python app.py send dev -i "你的工作还没有完成，给我继续"   # 继续聊天
```

### 14.12  跨机 TLS 部署

```bash
# 服务端: daemon.toml [listener] TLS_ENABLED=true（监听 TLS_HOST:TLS_PORT，跨机需 0.0.0.0）
#   python app.py start
# 客户端:
python app.py keygen -C "user@client"
# 将 id_ed25519.pub 追加到服务端 ~/.pty-agent/authorized_keys
# client.toml: [connection] CONNECT_MODE="tls", TLS_HOST="<server-ip>", TLS_PORT=18767
python app.py exec s1 -c "bash"
```

### 14.13  Web 界面

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

### 14.14  快速验证

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

## 15. 退出码与错误

**CLI 进程退出码：**

| 退出码 | 含义                                             |
| ------ | ------------------------------------------------ |
| `0`    | 成功（响应正常返回）                             |
| `1`    | 业务错误（守护进程返回 error 响应，如会话不存在） |
| `2`    | 用法错误（argparse 参数缺失/不识别/非法编码）     |
| `130`  | 用户中断（Ctrl+C）                               |

**响应 JSON 中的关键字段：**

| 字段                 | 说明                                                                 |
| -------------------- | -------------------------------------------------------------------- |
| `status`             | `"success"` / `"error"`                                              |
| `error.message`      | 错误信息                                                             |
| `triggerReturnReason`| 触发返回原因：`ok` / `trigger_matched` / `trigger_timeout` / `idle_timeout` / `program_ended` / `program_crashed` / `gui_detected` / `cancelled` / `notify_waiting` |
| `program.exitCode`   | 子进程退出码（非零表示异常）                                         |
| `program.running`    | 子进程是否仍在运行                                                   |
| `program.mode`       | 运行模式：`pty` / `subprocess`                                       |
| `debugInformation`   | debug 信息（进程树/GUI 窗口/事件/耗时，默认关闭，可用 `--debug-output` 开启）           |
| `sessionDefaults`    | 会话默认配置回填                                                     |
| `pendingNotifCount`  | 待消费通知数（--notify 相关）                                        |
| `stderrOutput`       | 子进程模式下的 stderr 输出                                           |

**常见错误：**

- **守护进程未运行：** `exec` 会自动启动，无需手动 `start`
- **端口丢失：** 使用 `stop --force` 通过互斥锁定位并终止
- **编码乱码：** 使用 `--encoding` 指定正确编码（如 gbk）
- **触发条件不命中：** 检查正则表达式，或使用 `--idle-timeout` 兜底
- **TUI 程序无输出：** pty 模式恒返回屏幕快照，确认会话处于 pty 模式
- **shell 操作符报错：** 命令含 `| & > < && || ;` 且为 pty 模式时默认拒绝，改用 `--shell` 或 `--force-pty-mode`
- **`--response-format svg` 报错：** 需运行中会话（已结束会话无屏幕缓冲）

---

## 16. 另见

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



