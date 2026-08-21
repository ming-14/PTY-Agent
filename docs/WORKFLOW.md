# Workflow 脚本编排 — 使用文档

> 用 YAML 定义文件声明一系列步骤（exec/send/read/kill/wait），由守护进程后台调度执行。
> 支持依赖图并行、步骤间变量传递、条件判定、失败重试与错误策略。

---

## 1. 快速开始

```powershell
# 1. 写一个定义文件 build.yaml
# 2. 启动（后台执行，立即返回 runId）
python app.py workflow run build.yaml

# 3. 查看运行列表与状态
python app.py workflow list
python app.py workflow show wf-1786777600000-1

# 4. 取消运行（等待中的步骤最快 0.1s 内响应）
python app.py workflow cancel wf-1786777600000-1
```

最小示例：

```yaml
name: hello
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

执行过程：启动 Python REPL 等待提示符 → 发送代码等输出 → 读取结果。三个步骤
未声明依赖时按顺序串行执行（后一步隐式依赖前一步）。

## 2. 定义文件结构（YAML）

```yaml
name: str          # 可选，工作流名称（show/list 显示用）
vars: {k: v}       # 可选，全局变量（值限 str/int/float/bool）
max_parallel: int  # 可选，最大并行步骤数（默认 4，可被 --parallel 覆盖）
steps:
  - id: str        # 必填，步骤唯一标识（唯一；也是表达式引用名）
    type: ...      # 必填，步骤类型（exec/send/read/kill/wait）
    ...            # 步骤字段（见第 3 节）
    if: expr       # 可选，条件表达式，为假时跳过（见第 5 节）
    depends_on: [id...]   # 可选，显式依赖（见第 4 节）
    on_error: fail|continue|ignore   # 可选，失败策略（默认 fail，见第 6 节）
    retry: int            # 可选，失败重试次数（默认 0）
    retry_interval: float # 可选，重试间隔秒数（默认 1.0）
```

解析期校验（`run` 时即报错，不产生运行）：

- 步骤必须是映射；`id` 非空、唯一，仅允许字母/数字/下划线/连字符
- `type` 必须属于五类之一；每类必填字段缺失时报错
- `depends_on` 引用不存在的 id、依赖自身、构成依赖环均拒绝
- `on_error` / `retry` / `retry_interval` / `max_parallel` 取值范围校验
- 定义文件大小上限 1 MB（`WORKFLOW_MAX_FILE_SIZE`）

定义文件由 CLI 本机读取（UTF-8）后发送 daemon 解析，跨机 tls 部署语义不变。

## 3. 步骤类型

### 3.1 exec — 启动或附加会话

| 字段 | 必填 | 说明 |
|------|------|------|
| `session` | ✓ | 会话标识 |
| `command` | ✓ | 命令字符串（自动拆分为参数列表） |
| `trigger` | | 触发条件（正则），命中后步骤返回 |
| `timeout` | | 等待超时秒数（默认 120） |
| `idle_timeout` | | 输出静默超时（秒） |
| `cwd` / `env` | | 工作目录 / 环境变量（KEY=VALUE 列表） |
| `size` / `cols` / `rows` | | 终端尺寸（`size: "120x40"` 或直接 cols/rows） |
| `mode` | | `pty`（默认，屏幕快照）/ `subprocess`（增量输出 + stderr 分离） |
| `full` / `keep_ansi` | | 返回全部内容 / 保留 ANSI 颜色码 |
| `snapshot_diff` | | 仅返回屏幕变化的行 |

**语义**：与会话同名且仍在运行 → 直接附加（不重复创建）；会话已结束 → 步骤失败。
exec 步骤的 `output` 是返回时的终端快照；`reason` 为返回原因
（`trigger_matched` / `trigger_timeout` / `idle_timeout` / `program_ended` /
`program_crashed` / `gui_detected` / `ok`）。

### 3.2 send — 向会话发送输入

| 字段 | 必填 | 说明 |
|------|------|------|
| `session` | ✓ | 会话标识（须已存在且运行中） |
| `input` | ✓ | 输入文本 |
| `trigger` / `timeout` / `idle_timeout` | | 等待返回（同 exec） |
| `send_eol` | | 末尾行尾符（lf/crlf/cr/none；默认按会话模式：pty=cr、subprocess=lf） |
| `keep_ansi` / `full` | | 输出处理 |

### 3.3 read — 读取会话输出

| 字段 | 必填 | 说明 |
|------|------|------|
| `session` | ✓ | 会话标识（不存在 → 步骤失败） |
| `trigger` / `timeout` / `idle_timeout` | | 等待模式（有 trigger/idle-timeout 时进入等待） |
| `lines` / `grep` | | 行数过滤（N 或 start:end）/ 正则过滤行 |
| `full` / `keep_ansi` / `snapshot_diff` | | 输出处理 |

无等待条件时立即返回当前快照。

### 3.4 kill — 终止会话

| 字段 | 必填 | 说明 |
|------|------|------|
| `session` | ✓ | 会话标识（不存在 → 步骤失败） |

终止整个进程树（Job Object / 进程组信号）。

### 3.5 wait — 固定等待

| 字段 | 必填 | 说明 |
|------|------|------|
| `seconds` | ✓ | 等待秒数（float） |

等待期间响应取消（分段睡眠，0.1s 粒度）。

> **会话生命周期**：workflow 创建的会话在执行结束后保留（可继续 read/send/kill），
> 显式 `kill` 步骤或外部命令终止。

## 4. 依赖与并行

调度依据**依赖图**（DAG）：依赖全部终态的步骤立即进入就绪集合，由线程池并行执行。

| 声明方式 | 语义 |
|----------|------|
| 不写 `depends_on` | 隐式依赖前一个步骤（**串行**，后一步必须等前一步结束） |
| `depends_on: [a, b]` | 显式依赖 a、b 两者都完成 |
| `depends_on: []` | **无依赖**，与其前序步骤可并行执行 |

```yaml
steps:
  - id: clone      # 不写 depends_on → 串行基准
    type: exec
    session: clone
    command: "git clone ..."
  - id: build
    type: exec
    session: build
    command: "make"
    depends_on: [clone]
  - id: unit
    type: exec
    session: unit
    command: "make test"
    depends_on: [clone]    # unit 与 build 均只依赖 clone → 两者并行
  - id: report
    type: wait
    seconds: 1
    depends_on: [build, unit]   # 等 build 与 unit 都完成
```

- 并行度上限：`max_parallel`（定义）/ `--parallel`（CLI 覆盖，优先级更高），
  默认 `WORKFLOW_DEFAULT_PARALLEL`（4）
- 依赖失败的步骤自动跳过（`skipped`），依赖取消的步骤同样跳过
- 依赖环解析期拒绝（报循环路径）

## 5. 变量、插值与条件判定

### 变量作用域

- **全局变量**：`vars` 定义；启动时 `--vars KEY=VALUE` 覆盖（优先级高于定义）。
  表达式以 `vars.<name>` 引用。
- **步骤结果**：已完成的步骤以 id 直接引用，暴露四个核心字段：

| 字段 | 来源 |
|------|------|
| `output` | outputStream（终端快照/增量输出） |
| `reason` | triggerReturnReason（见 3.1） |
| `exit_code` | program.exitCode（程序退出码） |
| `error` | 步骤失败时的错误描述 |

### 插值 `{{...}}`

步骤的任何字符串字段支持 `{{表达式}}` 插值（执行前渲染，可引用已完成的步骤）：

```yaml
command: "git clone https://example.com/{{vars.repo}}"
input: "print('{{build.output}}')"
```

### 条件判定 `if`

`if` 字段为**表达式**（非插值），求值为假时步骤跳过（`skipped`，note 标注原因）：

```yaml
if: "clone.reason == 'trigger_matched'"      # 比较
if: "'error' in build.output"                 # 字符串成员判断
if: "build.exit_code == 0 and not vars.skip"  # 逻辑运算
if: "len(build.output) > 10"                  # 不支持函数调用 → 拒绝
```

表达式由**安全求值器**执行（AST 白名单）：仅支持字面量、名称/属性/下标访问、
比较（`==` `!=` `<` `>` `in` `not in`）、布尔（`and` `or` `not`）、算术、容器。
小写字面量 `true`/`false`/`null`/`none`（YAML/JSON 习惯）等价于 Python 的
`True`/`False`/`None`。
**任何函数调用、属性方法执行都会被拒绝**（不可信定义文件无副作用风险）；
变量名缺失、语法非法均使步骤失败（可按 `on_error` 策略处理）。

## 6. 失败、重试与错误策略

### 失败定义

步骤失败 = 执行异常（会话创建失败、会话不存在、写入失败等）或返回错误响应。
trigger 超时**不算失败**（正常返回 `reason=trigger_timeout`）。

### 重试

```yaml
- id: pull
  type: exec
  session: pull
  command: "git pull"
  trigger: "error|done"
  retry: 2            # 失败重试 2 次（最多尝试 3 次）
  retry_interval: 2.0 # 重试间隔 2 秒
```

重试耗尽后按 `on_error` 处理。

### on_error 策略

| 值 | 本步骤状态 | 后续行为 |
|----|-----------|----------|
| `fail`（默认） | failed | 终止整个 workflow（其余未开始步骤 skipped，run 状态 failed） |
| `continue` | failed | workflow 继续调度其他步骤；依赖本步骤的步骤 skipped |
| `ignore` | done（note 记录忽略的错误） | 视为成功，依赖本步骤的步骤正常执行 |

### 失败传播

依赖失败的步骤自动 `skipped`（note 标注依赖 id 与状态），且传播到整条依赖链；
`on_error=ignore` 的步骤不传播失败。

## 7. CLI 命令

```bash
python app.py workflow run <file> [--vars KEY=VALUE ...] [--parallel N] [公共选项]
python app.py workflow list [公共选项]
python app.py workflow show <run-id> [公共选项]
python app.py workflow cancel <run-id> [公共选项]
```

| 命令 | 说明 |
|------|------|
| `run` | 启动（后台执行）。定义文件从本机读取（UTF-8，1 MB 上限）；`--vars` 可多个；`--parallel` 覆盖定义 max_parallel |
| `list` | 所有运行（含已结束）：runId/name/status/startedAt/finishedAt/stepCount |
| `show` | 单次运行完整状态：run 级状态 + 每步骤状态（含 attempts 尝试次数，output 截断至 4096 字符）+ 事件日志 |
| `cancel` | 请求取消：置位取消事件，执行中的步骤最快 0.1s 内响应；已终态运行幂等 |

`run` 立即返回 `runId`（如 `wf-1786777600000-1`），执行结果需 `show` 轮询。

## 8. 状态模型与限制

### 运行状态（run）

`running` → `done` / `failed` / `cancelled`

- `failed`：某步骤 `on_error=fail` 失败，或引擎异常
- `cancelled`：收到取消请求（已开始未完成的步骤标记 cancelled，未开始的 skipped）

### 步骤状态（step）

`pending` → `running` → `done` / `failed` / `skipped` / `cancelled`

- `skipped`：if 条件为假 / 依赖失败或取消 / workflow 因 on_error=fail 终止
- `cancelled`：运行取消时正在执行

### 限制

| 项 | 值 | 配置 |
|----|----|------|
| 运行记录上限 | 50 | `WORKFLOW_MAX_RUNS`（超限自动淘汰最旧终态；全部运行中则拒绝新 run） |
| 默认并行度 | 4 | `WORKFLOW_DEFAULT_PARALLEL` |
| 步骤输出保存上限 | 4096 字符 | `WORKFLOW_STEP_OUTPUT_LIMIT`（仅 show 日志，不影响真实输出） |
| 定义文件上限 | 1 MB | `WORKFLOW_MAX_FILE_SIZE` |

运行记录仅保存在内存（daemon 重启即清空）；daemon 停止时未完成运行随守护进程终止。

## 9. 完整示例

### 构建流水线（并行 + 条件 + 重试）

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
    timeout: 300
  - id: deps
    type: exec
    session: deps
    command: "cd {{vars.repo}} && pip install -r requirements.txt"
    trigger: "Successfully installed|error"
    timeout: 600
    retry: 1
    depends_on: [clone]
  - id: build
    type: exec
    session: build
    command: "cd {{vars.repo}} && make -j8"
    trigger: "error|^make:"
    idle_timeout: 60
    timeout: 900
    depends_on: [clone]
  - id: test
    type: send
    session: test
    input: "cd {{vars.repo}} && make test\n"
    trigger: "PASS|FAIL|error"
    timeout: 600
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

### TUI 冒烟测试（鼠标 + 快照）

```yaml
name: tui-smoke
steps:
  - id: launch
    type: exec
    session: tui
    command: "my_tui_app.exe"
    timeout: 10
  - id: interact
    type: send
    session: tui
    input: "j"
    timeout: 3
    depends_on: [launch]
  - id: verify
    type: read
    session: tui
    grep: "menu"
    if: "interact.reason == 'trigger_timeout'"   # 无触发时按快照验证
    depends_on: [interact]
  - id: quit
    type: send
    session: tui
    input: "{ctrl+c}"
    depends_on: [verify]
```

## 10. 常见问题

- **步骤频繁 failed（error 为空）？** `if` 条件表达式语法/名称错误也会使步骤失败，
  `show` 的 note/error 会写明原因；`on_error: ignore` 可让非关键校验不阻断流程。
- **并行没生效？** 检查 `depends_on`：省略 `depends_on` 的步骤隐式依赖前一个步骤
  （串行）；要并行必须显式声明依赖组或 `depends_on: []`。
- **依赖环报错？** daemon 解析期即拒绝并返回循环路径，`run` 直接返回错误。
- **workflow run 报"运行数已达上限"？** 全部运行仍在执行（50 上限）；
  等待完成或取消部分运行。
- **会话被外部误杀？** exec 步骤附加已结束会话会失败；kill 步骤对不存在会话也失败，
  可用 `on_error: ignore` 容忍竞态。