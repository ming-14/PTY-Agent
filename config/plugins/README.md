# config/plugins/ — 插件目录（daemon 侧 + CLI 侧）

插件实现目录。每个插件为**自包含目录**：`plugin.json` 清单声明元数据/声明/配置
默认值，代码与资产随目录携带。目录发现与状态管理见 `registry.json`。

## 目录结构

```
config/plugins/
├── registry.json        # 插件系统总开关 + 各插件启用状态（enable/disable 持久化）
├── policy.json          # 权限策略（可选）：按插件 id 追加授予/拒绝权限
├── __init__.py          # 包标记（空）
├── state_check/         # 状态检查插件（process+cli 双形态：装饰 list 响应 + CLI 显示 HEUR 标记）
│   ├── plugin.json
│   ├── __init__.py
│   └── config.yaml
├── subagent/            # 子代理管理（kind=["cli","process"]：多 agent 子代理）
│   ├── plugin.json      # 清单：messageTypes=["codebuddy_exec","devin_exec","opencode_exec","claude_exec","smartagent_exec"]、cliCommands=["codebuddy","devin","opencode","claude","smartagent"]
│   ├── subagent_plugin.py  # SubagentPlugin（通用多 agent，数据驱动）
│   ├── agents.py         # AgentSpec 注册表（声明 agent 差异）
│   ├── cli_commands.py   # CLI 命令自动生成（all_agent_commands）
│   ├── turn_monitor.py   # 通用回合监控（按 agent 选 screen parser）
│   ├── parser_loader.py  # 多 parser 命名空间包加载器
│   ├── smartagent/       # Smart Agent（真人聊天窗口）：smartagent.py 服务端 + smartagent_tui.py 人类窗口
│   ├── parser/           # parser 包（workbuddyparser、devinparser、opencodeparser、claudeparser、smartparser 等）
│   │   ├── workbuddyparser/  # CodeBuddy (cbc) 会话解析器
│   │   ├── devinparser/      # Devin CLI 会话解析器
│   │   ├── opencodeparser/   # OpenCode (sst) 会话解析器
│   │   └── claudeparser/     # Claude Code 会话解析器
│   └── subagent.md       # 插件帮助文档
└── ai/                  # CLI 侧 AI 二次分析插件（kind=cli，自包含 aichat 资产）
    ├── plugin.json      # 清单：commands/权限（无 config：prompt/timeout 在 config.yaml）
    ├── __init__.py      # AiPlugin（transform_response 分析，覆盖 outputStream）
    ├── common.py        # aichat 桥接（run_aichat_capture / load_settings 等）
    ├── talk.py / _finderror.py / config_manager.py   # aichat 独立工具
    ├── bin/aichat.exe   # aichat 可执行文件（BUILD.py 下载，gitignore）
    ├── config/config.yaml(.example)  # aichat 模型/密钥 + 插件 prompt/timeout 配置（自愈重建）
    └── README.md
```

## 注册与发现

插件目录发现：扫描 `config/plugins/` 下含 `plugin.json` 的子目录（每目录一插件），
由 `src/config/plugins.py` 读取；环境变量 `PTY_PLUGIN_DIRS`（`os.pathsep` 分隔）
可追加插件目录。修改插件目录后需重启守护进程，或执行 `plugin reload <id>` 热重载。

`registry.json` 记录总开关与各插件启用状态（enable/disable 自动持久化）：

```json
{
  "enabled": true,
  "plugins": {
    "state_check": {"enabled": true},
    "ai": {"enabled": true},
    "subagent": {"enabled": true}
  }
}
```

`registry.json` 缺失时插件系统整体禁用（主流程正常）。

## 清单（plugin.json）

插件元数据的**单一事实来源**（由 `src/plugins/manifest.py` 解析校验）：

| 字段 | 说明 |
|------|------|
| `id` | 插件名（小写字母/数字/下划线/连字符） |
| `version` | 版本号 |
| `kind` | 形态：`process`（进程级）/ `session`（会话级）/ `cli`（CLI 侧）；**支持数组组合**（如 `["cli","process"]`） |
| `description` | 功能描述 |
| `entry` | 入口文件名（默认 `__init__.py`，须导出 `plugin` 属性） |
| `triggers` | 触发声明（仅 session）：`event` / `poll`（声明须实现对应钩子） |
| `pollInterval` | poll 触发间隔（秒，声明 poll 时必填） |
| `autoLoad` | 自动加载条件（仅 session）：`command`（正则/关键词）、`cwd`（前缀/正则）、`env`（变量→正则） |
| `messageTypes` | 接管的消息类型（仅 process，须实现 handle_message） |
| `needsIO` | 是否需要 I/O 通道（多帧传输协议用） |
| `commands` | 生效命令白名单（CLI 形态；空=全部命令） |
| `cliCommands` | 注册的新 CLI 命令名（CLI 形态；须在入口模块 `commands` 导出对应 Command 类） |
| `decorateTypes` | 装饰的内置命令响应类型（仅 process；须实现 decorate_response，如 `["list"]`） |
| `autoMount` | 命令自动参与 CLI 钩子链（仅 cli；无需 `--plugin` 显式激活，如 `["list"]`） |
| `contextHidden` | 隐藏上下文（bool，默认 false）：daemon 启动时不自动输出 `<插件名>.md`，改为 `plugin --gethelp <name>` 按需查看 |
| `hooks` | 钩子优先级声明：`{"on_input": {"priority": 120}}` |
| `permissions.required` | 必需能力列表（见下方权限） |
| `config.defaults` | 配置默认值（+ 可选 `config.schema.json` 校验） |
| `events.subscribe` | 订阅的 daemon 事件总线主题模式（`*` 单段 / `>` 多段） |
| `dependencies` | 依赖声明：`plugins`（插件依赖）、`python`（Python 包依赖） |
| `cliOptions` | 自定义 CLI 选项声明（见「插件自定义参数」；仅 cli/session 形态） |

**声明即契约**：清单声明的触发方式/钩子必须在入口模块的插件类中实现，
校验失败仅跳过该插件，不影响其他插件与主流程。

## 插件形态（kind）

| kind | 执行位置 | 生命周期 |
|------|----------|----------|
| `process` | daemon 进程 | 注册表 enable 时构造单例（on_init → on_enable）；disable → on_disable；`messageTypes` 接管消息路由 |
| `session` | daemon 进程 | 规范实例随 enable 创建（on_init → on_enable，收总线事件）；每次会话挂载构造独立实例（on_init → on_attach），卸载 → on_detach |
| `cli` | 客户端进程 | 每次命令进程启动时加载（on_init）；处理请求/响应三阶段钩子（before_request / transform_response / render_response），经 `exec --plugin` 或会话挂载列表 activate 后自动派发 |

**多形态组合**：`kind` 声明为数组时，插件在多个侧同时生效（各侧按对应形态生命周期
独立运行）。例如 `["cli","process"]` 的插件：CLI 侧注册新命令（`cliCommands` +
`commands` 导出）、daemon 侧接管 `messageTypes` 并装饰内置响应（`decorateTypes`）。
daemon 侧仅跳过**纯 cli** 形态的插件登记（`plugin list` 仍显示 cli 形态插件）。

## 配置

- 默认值：`plugin.json` `config.defaults`（基准）
- 覆盖：`plugin config <id> set <key> <value>`（**仅内存**，守护进程重启即恢复默认，
  与 `set-default` 语义一致，不读写任何文件）
- 校验：插件目录 `config.schema.json`（JSON Schema 子集：type/enum/minimum/maximum/
  minLength/maxLength/pattern/items/properties/required/additionalProperties），
  合并后校验，失败时插件进入 BROKEN 状态（错误可见）

## 插件自定义参数（cliOptions）

插件可在 `plugin.json` 声明自己的 CLI 选项，调用方经命令行直接传参，
无需约定统一的 `--pluginargs` 选项名。仅 `cli` / `session` 形态可用
（process 形态的消息源命令不在会话 IO 命令集内，无注入路径）。

### 声明示例

```json
{
  "id": "mydemo",
  "kind": "session",
  "cliOptions": [
    {"name": "pa", "short": "p", "type": "str", "default": null,
     "help": "要注入插件的参数", "commands": ["exec", "send"]},
    {"name": "num", "type": "int", "default": 3, "help": "数值"},
    {"name": "any", "type": "flag", "help": "任意开关"},
    {"name": "mode", "type": "choice", "choices": ["a", "b"], "help": "枚举"}
  ]
}
```

| 字段 | 必填 | 规则 |
|------|------|------|
| `name` | 是 | `^[a-z0-9][a-z0-9-]*$`；生成长选项 `--<name>` |
| `short` | 否 | 单字符；生成短选项 `-<short>` |
| `type` | 否 | `str`（默认）/ `int` / `float` / `flag` / `choice` |
| `choices` | choice 时必填 | 非空字符串列表 |
| `default` | 否 | 类型必须与 `type` 匹配（`flag` 未声明 default 时视为 False） |
| `help` | 否 | `--help` 展示文本 |
| `commands` | 否 | 生效命令白名单；可取值 `exec`/`send`/`advsend`/`read`/`mouse`；空=全部 |

选项注册到对应命令的 argparse（`pty-agent exec --help` 可见），
值类型/枚举由 argparse 校验，非法取值解析期即报错。

### 冲突检测（检测到即不加载插件）

加载期对全部插件清单做交叉冲突检测（daemon 与客户端一致执行），
任一冲突 → 插件不加载（daemon 侧 `plugin list` 可见 `broken` 与错误原因；
客户端侧跳过加载、选项不注册）：

- 与内置参数冲突：选项串命中该命令已注册的内置选项
  （如声明 `{"name": "timeout"}` 与 `--timeout` 冲突）
- 插件间冲突：两插件在同一命令上声明了相同长选项或相同短选项 → 双方都不加载
- 按命令域判定：仅在双方都注册的命令上同串才算冲突
  （A 在 exec 声明 `--pa`、B 在 send 声明 `--pa` → 无冲突）

### 读取方式

- **CLI 插件**：三阶段钩子（before_request/transform_response/render_response）
  经 `ctx.options` 读取本次调用显式提供的选项（`{选项名: 值}`）
- **session 插件**：`ctx.options` 在**会话生命周期内所有钩子**可读
  （on_init/on_attach/on_input/on_output/on_snapshot/on_event/on_poll/
  handle_command/inspect_state 等）。选项在 exec 创建会话时注入；
  后续 `send/read/mouse` 携带的选项**合并更新**到会话；动态 `plugin attach`
  时沿用会话已存选项。未提供任何选项时为空 `dict`
- 仅显式提供的选项值随消息下发（默认值不传输，插件经清单自取）

### 命令示例

```bash
python app.py exec mysession -c "cmd" --plugin mydemo --pa "插件读取" --num 7
python app.py send mysession -i "hi" --pa "更新后的值" --timeout 5
python app.py read mysession --pa "x" -t "done"
```

daemon 侧对 `msg.pluginOptions` 统一校验（对象/标量类型/总大小上限），
非法形状的请求直接返回错误。

## 事件总线

daemon 级 pub/sub 事件总线（`src/plugins/events.py`），主题按 `.` 分段：
`*` 匹配单段，`>` 匹配剩余任意段。标准主题：

```
daemon.started / daemon.stopping
plugin.enabled / plugin.disabled / plugin.installed / plugin.uninstalled
session.created / session.ended / session.event.<事件类型>
```

插件经清单 `events.subscribe` 声明订阅（enable 时自动挂钩，回调 `on_bus_event`），
或运行期 `ctx.events.subscribe(pattern, cb)` 编程订阅。

## 权限

清单声明 `permissions.required`（能力列表）；`policy.json` 由管理员维护，
按插件 id 追加授予/拒绝：

```json
{
  "plugins": {
    "state_check": {"grant": ["network.connect"], "deny": []}
  }
}
```

有效权限 = required ∪ grant − deny（deny 覆盖一切）。插件经
`ctx.permission.require/check` 自行检查；拒绝事件写入日志（审计轨迹）。

## 存储

插件数据目录 `<DATA_DIR>/plugins/<id>/`（DATA_DIR = `~/.pty-agent`），
提供 kv（JSON 文件）/ 文件 / sqlite 三种视图，按插件命名空间隔离；
uninstall 时整目录清除，disable 时保留。

## 插件管理命令

```bash
python app.py plugin list                        # 列出已加载插件（含状态/形态）
python app.py plugin ls <id>                     # 列出会话挂载的插件
python app.py plugin --gethelp <name>            # 显示插件帮助文档（<插件名>.md，按需查看）
python app.py plugin attach <id> <name>          # 动态挂载到运行中会话
python app.py plugin detach <id> <name>          # 从会话卸载
python app.py plugin cmd <id> <name> <cmd> [...] # 调用插件自定义命令
python app.py plugin install <path>              # 从目录安装（须含 plugin.json）
python app.py plugin uninstall <name>            # 卸载（须先 disable）
python app.py plugin enable <name>               # 启用
python app.py plugin disable <name>              # 停用
python app.py plugin reload <name>               # 热重载（重新加载代码与清单）
python app.py plugin info <name>                 # 详情（清单/状态/权限/事件）
python app.py plugin status <name>               # 运行状态
python app.py plugin config <name> [key value]   # 查看/修改配置
```

## 上下文输出（<插件名>.md）

插件目录下若存在 `<插件名>.md`（如 `subagent.md`），其内容会
**输出到 CLI 给用户看**（stderr 信息区，不进入会话输出/终端画面）；
清单声明 `contextHidden: true` 的插件不自动输出（用 `plugin --gethelp <name>` 按需查看）：

```
[plugin subagent context]
<subagent.md 内容>
[plugin subagent context end]
```

**输出时机（按形态）：**

| 形态 | 输出时机 | 说明 |
|------|----------|------|
| `process` | 守护进程启动时 | `app.py start` 后 CLI 输出进程级插件上下文 |
| `session` | exec 启用时 | `exec --plugin <name>` 时 CLI 输出该插件上下文 |
| `cli` | exec 启用时 | `exec --plugin <name>` 时 CLI 输出该插件上下文 |

约定：
- 输出到 CLI（stderr 信息区），**不注入会话输出流、不渲染进终端画面**
- **只发一次**：每个 daemon 周期内每插件文档只输出一次，重启 daemon 后重新发送（新周期）
- **内容变化重发**：同周期内插件 .md 文件内容更新（sha256 变化）自动重新发送
- 上限 64KB，超出截断并追加 `[context truncated]` 提示
- 文件缺失/读取失败仅跳过，不影响插件加载与挂载
- 重置：删除 `~/.pty-agent/plugin-context-state.json` 或重启 daemon

## 插件一览

| 插件 | 形态 | 功能 |
|------|------|------|
| `state_check` | 多形态（`kind = ["process","cli"]`） | 装饰 list 响应并在 CLI 显示 HEUR 状态标记（纯启发式） |
| `ai` | CLI 侧（`kind = "cli"`） | 对 exec/send/read/mouse 响应做 AI 二次分析，`exec --plugin ai` 挂载后自动回调 |
| `subagent` | 多形态（`kind = ["cli","process"]`） | 子代理管理：codebuddy / devin / opencode / claude exec 命令 + smartagent（真人 Smart Chat 聊天窗口），装饰 read/send/list 响应（子代理检测），AgentSpec 注册表扩展，回合状态监控 + 通知 |

### kind 多形态组合（v2.0+）

`kind` 从单值字符串升级为字符串或数组，插件可同时参与多侧：
- `["cli", "process"]`：CLI 侧注册命令 + daemon 侧处理消息（如 subagent 插件）
- `["cli", "session"]`：CLI 侧钩子 + daemon 会话级挂载
- 单值 `"process"` / `"session"` / `"cli"` 保持兼容

多形态时各侧校验独立进行：CLI 侧检查 CLI 钩子、process 侧检查 messageTypes 等。`plugin list` 中 kind 显示为 `cli/process` 用 `/` 连接。

详细说明见各插件子目录 README。
