# 插件开发指南（Plugin API）

> 面向插件开发者的完整参考：如何编写、声明、注册、调试一个插件。
> 配套代码：`src/plugins/`（核心实现）、`src/client/cli_plugins.py`（CLI 宿主）、
> `config/plugins/`（内置插件目录）、`tests/`（单元与 e2e 测试）。

---

## 目录

1. [插件系统概览](#1-插件系统概览)
2. [插件目录与发现](#2-插件目录与发现)
3. [清单 plugin.json](#3-清单-pluginjson)
4. [三种形态（kind）](#4-三种形态kind)
5. [Plugin 基类与全部钩子](#5-plugin-基类与全部钩子)
6. [运行时上下文](#6-运行时上下文)
7. [钩子链与调度语义](#7-钩子链与调度语义)
8. [事件总线](#8-事件总线)
9. [配置系统](#9-配置系统)
10. [存储系统](#10-存储系统)
11. [权限系统](#11-权限系统)
12. [自定义 CLI 选项（cliOptions）](#12-自定义-cli-选项clioptions)
13. [插件上下文输出（帮助文档）](#13-插件上下文输出帮助文档)
14. [插件管理命令](#14-插件管理命令)
15. [注册新 CLI 命令（cliCommands）](#15-注册新-cli-命令clicommands)
16. [编写插件：完整示例](#16-编写插件完整示例)
17. [调试与测试](#17-调试与测试)
18. [规范与最佳实践](#18-规范与最佳实践)

---

## 1. 插件系统概览

插件系统是清单（`plugin.json`）驱动的可扩展机制：插件以**自包含目录**存在，
清单声明元数据、形态、钩子、触发方式、权限与配置；加载器按清单导入入口模块并
校验"声明即契约"；运行时按形态把插件接入 daemon（进程级/会话级）或客户端（CLI）。

架构分层：

```
┌─ 客户端进程（每次调用）─────────────────────────────┐
│  CliPluginHost（src/client/cli_plugins.py）         │
│  kind=cli 插件：check_request/before_request/       │
│  transform_response/render_response 三阶段钩子      │
└──────────────────────┬──────────────────────────────┘
                       │ TCP/TLS · NDJSON
┌─ 守护进程 daemon ────┴──────────────────────────────┐
│  PluginRegistry（src/plugins/registry.py）          │
│  ├─ kind=process：单例实例，messageTypes 接管消息、  │
│  │                 decorateTypes 装饰内置响应、     │
│  │                 on_session_created 会话回调      │
│  ├─ kind=session：规范实例 + 会话挂载实例，          │
│  │                 PluginHost 钩子链驱动变换        │
│  └─ 环境 PluginEnvironment：事件总线/配置/存储/权限  │
└──────────────────────────────────────────────────────┘
```

核心模块（`src/plugins/`）：

| 模块 | 职责 |
|------|------|
| `base.py` | Plugin 基类、PluginContext、ProcessPluginContext、HANDLED 哨兵 |
| `manifest.py` | plugin.json 清单解析与校验（PluginManifest / PluginCliOption） |
| `loader.py` | 清单驱动加载器（目录 → 清单 → 模块导入 → 声明校验） |
| `registry.py` | 进程级注册表（加载 + enable/disable/reload/install/remove + auto_load） |
| `host.py` | 会话级插件宿主（挂载链 + 钩子调度 + 返回控制 + 自我卸载） |
| `hooks.py` | 钩子链引擎（优先级排序 + modify/observe/provide 三类调度语义） |
| `events.py` | daemon 事件总线（pub/sub + 主题通配 `*`/`>`） |
| `config.py` | 插件配置（清单默认 + 内存覆盖 + JSON Schema 子集校验） |
| `storage.py` | 插件存储（kv / 文件 / sqlite 三种视图，按插件隔离） |
| `permissions.py` | 能力检查 + 审计（PermissionChecker / PermissionDenied） |
| `environment.py` | 运行环境（daemon 全局共享能力集合） |
| `context.py` | 插件上下文输出（`<插件目录>/<插件名>.md` 输出给用户） |
| `cli_options.py` | 插件自定义 CLI 选项（声明/冲突检测/值收集/消息校验） |
| `io.py` | 进程级插件 I/O 端口（多帧传输协议通道） |
| `decorate.py` | 内置响应装饰（按 decorateTypes 匹配 commandType） |

---

## 2. 插件目录与发现

### 目录约定

每个插件是 `config/plugins/<id>/` 下的一个自包含目录（`<id>` 即清单 `id`）：

```
config/plugins/<id>/
├── plugin.json          # 清单（必填，单一事实来源）
├── __init__.py          # 入口模块（默认；须导出 plugin 属性）
├── config.schema.json   # 可选：配置 JSON Schema 子集
├── <id>.md              # 可选：插件帮助文档（上下文输出给用户）
└── ...                  # 其余代码与资产随目录携带
```

### 发现规则

- 默认扫描 `config/plugins/` 下**含 `plugin.json`** 的子目录（每目录一插件）。
- 环境变量 `PTY_PLUGIN_DIRS`（`os.pathsep` 分隔）可追加额外插件目录。
- 目录级改动后需重启 daemon，或 `plugin reload <id>` 热重载。

### 启用状态（registry.json）

`config/plugins/registry.json` 记录**总开关**与各插件启用状态：

```json
{
  "enabled": true,
  "plugins": {
    "state_check": {"enabled": true},
    "ai": {"enabled": true}
  }
}
```

- `registry.json` **缺失** → 插件系统整体禁用（主流程不受影响）。
- `enable` / `disable` 命令会自动持久化到该文件。
- 未在 `plugins` 中记录的插件默认**启用**。

### 权限策略（policy.json，可选）

`config/plugins/policy.json` 由管理员维护，按插件 id 追加授予/拒绝能力：

```json
{
  "plugins": {
    "state_check": {"grant": ["network.connect"], "deny": []}
  }
}
```

---

## 3. 清单 plugin.json

清单是插件元数据的**单一事实来源**，由 `src/plugins/manifest.py` 解析校验。
校验失败仅跳过该插件（记 error），不影响其他插件与主流程。

### 完整字段参考

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | ✅ | 插件名，`^[a-z0-9][a-z0-9_-]*$`，长度 ≤ 64 |
| `version` | string | ✅ | 非空版本号 |
| `kind` | string \| string[] | ✅ | `process` / `session` / `cli` 之一或组合（详见第 4 节） |
| `description` | string | — | 功能描述 |
| `entry` | string | — | 入口文件名（默认 `__init__.py`，须导出 `plugin` 属性） |
| `triggers` | string[] | — | 触发声明（仅 session）：`event` / `poll` |
| `pollInterval` | number | 声明 poll 时必填 | `on_poll` 触发间隔（秒，>0） |
| `autoLoad` | object | — | 自动加载条件（仅 session）：`command` / `cwd` / `env` |
| `messageTypes` | string[] | — | 接管的消息类型（仅 process，须实现 `handle_message`） |
| `needsIO` | boolean | — | 是否需要 I/O 通道（多帧传输协议用） |
| `commands` | string[] | — | 生效命令白名单（CLI 形态；空 = 全部命令） |
| `cliCommands` | string[] | — | 注册的新 CLI 命令名（须在入口模块 `commands` 导出对应 Command 类） |
| `decorateTypes` | string[] | — | 装饰的内置命令响应类型（仅 process，须实现 `decorate_response`） |
| `autoMount` | string[] | — | 命令自动参与 CLI 钩子链（无需 `--plugin` 显式激活） |
| `contextHidden` | boolean | — | 隐藏帮助文档：daemon 启动时不自动输出 `<id>.md`，用 `plugin --gethelp` 查看 |
| `hooks` | object | — | 钩子优先级声明：`{"on_input": {"priority": 120}}` |
| `permissions.required` | string[] | — | 必需能力列表 |
| `config.defaults` | object | — | 配置默认值 |
| `events.subscribe` | string[] | — | 订阅的事件总线主题模式（`*` 单段 / `>` 多段） |
| `dependencies` | object | — | 依赖声明：`plugins`（插件依赖）、`python`（Python 包依赖） |
| `cliOptions` | object[] | — | 自定义 CLI 选项（仅 cli/session 形态，详见第 12 节） |

### 最小清单示例

```json
{
  "id": "myplugin",
  "version": "1.0.0",
  "kind": "session",
  "description": "我的第一个会话级插件"
}
```

**声明即契约**：清单声明的触发方式/钩子必须在入口模块的插件类中实现；
校验失败仅跳过该插件，不影响其他插件与主流程。

---

## 4. 三种形态（kind）

`kind` 决定插件的**执行位置**与**生命周期**，可声明为单值或数组组合。

| kind | 执行位置 | 生命周期 | 典型用途 |
|------|----------|----------|----------|
| `process` | daemon 进程 | 注册表 enable 时构造**单例**（`on_init` → `on_enable`）；disable → `on_disable` | 接管消息类型（`messageTypes`）、装饰内置响应（`decorateTypes`）、会话创建回调（`on_session_created`） |
| `session` | daemon 进程 | 注册表 enable 时构造**规范实例**（`on_init` → `on_enable`，收总线事件）；每次会话挂载构造**独立实例**（`on_init` → `on_attach`）；卸载 → `on_detach` | 会话级变换链（`on_input`/`on_output`/`on_snapshot`）、触发（`on_event`/`on_poll`）、自定义命令（`handle_command`） |
| `cli` | 客户端进程 | 每次客户端进程启动时加载（`on_init`；`on_enable`/`on_disable` 不参与） | 请求/响应三阶段钩子（`check_request`/`before_request`/`transform_response`/`render_response`）、注册新 CLI 命令（`cliCommands`）、自定义 CLI 选项 |

**多形态组合**：`kind` 声明为数组时，插件在多个侧同时生效（各侧按对应形态生命周期独立运行）。
例如 `["cli", "process"]`：CLI 侧注册新命令 + daemon 侧接管消息并装饰响应（如内置 `subagent` 插件）。

**生命周期约定汇总**（来自 `base.py` 文档）：

- `process`：注册表 enable 时构造单例 → `on_init` → `on_enable`；disable → `on_disable`
- `session`：注册表 enable 时构造规范实例（`on_init` → `on_enable`，收总线事件）；每次会话挂载构造独立实例（`on_init` → `on_attach`）；卸载 → `on_detach`
- `cli`：客户端进程加载时构造 → `on_init`（`on_enable`/`on_disable` 不参与）

---

## 5. Plugin 基类与全部钩子

插件类继承 `src.plugins.base.Plugin`，**只实现钩子**；元信息（id/version/kind 等）
由加载器从清单注入类属性（`name`/`version`/`description`/`kind`/`manifest`）。

```python
from src.plugins.base import Plugin

class MyPlugin(Plugin):
    def on_attach(self, ctx):
        print("attached to", ctx.session.id)

plugin = MyPlugin          # 导出插件类（或实例）
```

入口模块必须导出 `plugin`（Plugin 实例或子类）。若声明了 `cliCommands`，还需
导出 `commands`（Command 子类列表，见第 15 节）。

### 全部钩子（VALID_HOOKS）

| 钩子 | 调度语义 | 适用形态 | 签名与说明 |
|------|----------|----------|------------|
| `on_init` | 生命周期 | 全部 | `(ctx)` 实例构造后初始化（配置/存储/日志准备） |
| `on_enable` | 生命周期 | process/session | `(ctx)` 全局启用（注册表 enable 时由规范实例回调） |
| `on_disable` | 生命周期 | process/session | `(ctx)` 全局停用 |
| `on_attach` | 生命周期 | session | `(ctx)` 挂载到会话时（exec 注入或动态 attach；会话可能尚未启动） |
| `on_detach` | 生命周期 | session | `(ctx, exit_code)` 从会话卸载（用户 detach、自我卸载或会话结束） |
| `on_session_created` | 生命周期 | process | `(ctx, session, msg)` 会话创建成功后回调（ExecHandler 通用流程内） |
| `on_input` | modify | session | `(ctx, data)` PTY 写入前输入变换；**返回 None 拦截丢弃** |
| `on_output` | modify | session | `(ctx, data: bytes) -> bytes` PTY 原始输出变换（reader 线程） |
| `on_snapshot` | modify | session | `(ctx, text: str) -> str` 终端快照文本变换 |
| `on_event` | observe | session | `(ctx, event: dict)` 会话事件订阅（需清单 `triggers` 含 `"event"`） |
| `on_poll` | observe | session | `(ctx)` 定时触发（需清单 `triggers` 含 `"poll"` + `pollInterval`） |
| `on_bus_event` | observe | process | `(ctx, event)` daemon 事件总线事件（按清单 `events.subscribe` 订阅） |
| `handle_command` | 按名路由 | session | `(ctx, msg)` 自定义命令（`plugin cmd <sid> <name> <cmd>` 触发，路由到指定插件）；未处理返回 None |
| `handle_message` | — | process | `(ctx, msg)` 进程级命令处理（需 `messageTypes`）；返回 dict 原样发送 / `HANDLED` 已自行多帧响应 / None 未处理 |
| `inspect_state` | provide | session | `(ctx)` 命令返回时一次性状态检查；返回 dict 附加为 `terminalState` |
| `decorate_response` | — | process | `(ctx, resp)` 装饰内置命令响应（按 `decorateTypes` 匹配 commandType）；返回修改后的 resp 或 None |
| `check_request` | provide | cli | `(ctx, msg)` 请求发送前拦截；返回 None 放行，返回 str 拒绝（作为错误消息） |
| `before_request` | modify | cli | `(ctx, msg)` 请求发送前；返回 dict 替换 msg，None 放行 |
| `transform_response` | modify | cli | `(ctx, resp)` 响应收到后；返回 dict 替换 resp，None 不变 |
| `render_response` | provide | cli | `(ctx, resp)` 响应打印前；返回 str 直接打印，None 走默认渲染 |

---

## 6. 运行时上下文

钩子调用时宿主构造上下文对象，插件通过 `ctx` 访问会话/环境能力。

### 6.1 PluginContext（会话级）

`src.plugins.base.PluginContext`，每个钩子调用由宿主构造：

| 属性 | 说明 |
|------|------|
| `session` | 当前会话对象（`Session` 实例） |
| `plugin` | 插件实例自身 |
| `options` | 本插件的会话选项（`cliOptions` 声明，exec 注入、send/read/mouse 更新）；未设置时为空 dict（**只读**） |
| `events` | daemon 事件总线（EventBus） |
| `notify_manager` | 通知管理器（`--notify`/`wait` 通知发布用） |
| `config` | 插件配置视图（PluginConfig） |
| `storage` | 插件存储入口（PluginStorage） |
| `permission` | 能力检查器（PermissionChecker） |
| `logger` | 插件共享日志器 |

| 方法 | 说明 |
|------|------|
| `request_return(reason: str) -> bool` | 请求当前等待命令（exec/send 的 trigger/snapshot 等待）立即返回；原因透传给调用方（`triggerReturnReason`）。无等待时返回 False |
| `self_unload() -> bool` | 请求从当前会话卸载自身（当前钩子链结束后生效，触发 `on_detach`） |

### 6.2 ProcessPluginContext（进程级）

`src.plugins.base.ProcessPluginContext`，每个消息处理由调度器构造：

| 属性 | 说明 |
|------|------|
| `manager` | SessionManager |
| `plugin` | 插件实例 |
| `io` | PluginIO（仅 `needsIO=True` 时注入；否则 None） |
| `events` / `config` / `storage` / `permission` / `logger` | 同 PluginContext |

### 6.3 CliContext（CLI 侧）

`src.client.cli_plugins.CliContext`：

| 属性 | 说明 |
|------|------|
| `command` | 当前命令名（如 `"exec"`、`"send"`） |
| `client` | Client 实例引用 |
| `plugin` | 插件实例 |
| `output_path` | 本次调用 `-o` 输出路径（无则 None；fileOutput 类插件读取） |
| `config` | 插件配置视图（清单默认 + 内存覆盖） |
| `options` | 本次调用显式提供的插件选项（cliOptions 声明；未提供为空 dict） |

---

## 7. 钩子链与调度语义

`HookEngine`（`src/plugins/hooks.py`）在插件挂载/启用时把已实现的钩子编译为链，
按**优先级**（默认 100，高者先）与注册顺序排序；调用时按钩子类型选择语义：

| 语义 | 行为 | 涉及钩子 |
|------|------|----------|
| **modify** | 链式变换：前一输出为后一输入；`on_input` 任一返回 None 即**拦截**（整体返回 None），`on_output`/`on_snapshot` 返回 None 视为"不修改"沿用上一值 | `on_input` / `on_output` / `on_snapshot`；CLI `before_request` / `transform_response` |
| **observe** | 只通知，返回值忽略 | `on_event` / `on_poll` / `on_bus_event` / 生命周期 |
| **provide** | 提供者：按优先级**升序**，首个非 None 生效 | `inspect_state` / CLI `check_request` / `render_response` |

**异常隔离**：单个钩子抛异常只记日志，不影响链上其余钩子与主流程。
链为空时所有调用零开销短路。

**优先级声明**（`plugin.json`）：

```json
{
  "hooks": {
    "on_input": {"priority": 120},
    "on_snapshot": {"priority": 80}
  }
}
```

modify/observe 链按优先级降序（高者先），provide 链按优先级升序（低者先，首个非 None 生效）。

**触发门控**：`on_event` 仅当清单 `triggers` 含 `"event"` 时注册；`on_poll` 仅当
`triggers` 含 `"poll"` 且提供 `pollInterval` 时注册。`handle_message` 不经会话钩子链
（dispatcher 直调）。

---

## 8. 事件总线

`EventBus`（`src/plugins/events.py`）是 daemon 级 pub/sub 总线，主题按 `.` 分段：
`*` 匹配单段，`>` 匹配剩余任意段（MQTT 风格）。

**标准主题**：

```
daemon.started / daemon.stopping
plugin.enabled / plugin.disabled / plugin.installed / plugin.uninstalled
session.created / session.ended / session.event.<事件类型>
```

**事件对象**（`Event`）：`topic` / `source` / `timestamp` / `payload`，不可变，
`to_dict()` 序列化。

**订阅方式**：

1. 清单声明（enable 时自动挂钩，回调 `on_bus_event`）：

```json
{
  "events": {
    "subscribe": ["session.ended", "session.event.>"]
  }
}
```

2. 运行期编程订阅：`ctx.events.subscribe(pattern, cb)`（回调签名 `callback(event)`）。

订阅回调异常隔离：只记日志不中断发布。

---

## 9. 配置系统

插件配置分层（后层覆盖前层）：

1. `plugin.json` `config.defaults`（基准默认值）
2. `plugin config set` 的内存覆盖（守护进程内存记忆，**重启即恢复默认**，不写任何文件）

与 daemon `set-default` 的"内存记忆"语义一致。

**配置校验**：插件目录下 `config.schema.json`（JSON Schema 子集：
`type`/`enum`/`minimum`/`maximum`/`minLength`/`maxLength`/`pattern`/`items`/
`properties`/`required`/`additionalProperties`）在合并后校验，失败时插件进入
BROKEN 状态（错误可见）。

**访问方式**：`ctx.config.get(key, default)` / `ctx.config.as_dict()` /
`ctx.config.set(key, value)` / `ctx.config.reset()`。

**清单示例**：

```json
{
  "config": {
    "defaults": {
      "timeout": 10,
      "trigger": "quit"
    }
  }
}
```

`config.schema.json` 示例：

```json
{
  "type": "object",
  "properties": {
    "timeout": {"type": "integer", "minimum": 1, "maximum": 3600},
    "trigger": {"type": "string", "minLength": 1}
  },
  "additionalProperties": false
}
```

---

## 10. 存储系统

插件数据目录 `<DATA_DIR>/plugins/<id>/`（`DATA_DIR` = `~/.pty-agent`），
按插件命名空间隔离，插件只能访问自己的根目录。`uninstall` 整目录清除，
`disable` 保留。经 `ctx.storage` 访问：

| 视图 | 方法 | 说明 |
|------|------|------|
| kv | `ctx.storage.kv(name="state")` | JSON 文件键值存储（线程安全，单文件，适合小状态）：`get`/`set`/`delete`/`keys`/`as_dict` |
| 文件 | `ctx.storage.files(name="files")` | 文件视图（路径限定在存储根内，防越界）：`write`/`read`/`delete`/`list_files` |
| sqlite | `ctx.storage.sqlite(name="db")` | 返回 sqlite 数据库文件路径（插件自行用标准库 `sqlite3` 打开） |

```python
# kv
ctx.storage.kv().set("last_state", "running")
val = ctx.storage.kv().get("last_state")

# 文件
ctx.storage.files().write("cache/data.bin", b"...")
data = ctx.storage.files().read("cache/data.bin")

# sqlite
import sqlite3
db_path = ctx.storage.sqlite()
conn = sqlite3.connect(db_path)
```

---

## 11. 权限系统

能力检查 + 审计（`src/plugins/permissions.py`）：

- **有效权限** = 清单 `permissions.required` ∪ `policy.json` 的 `grant` − `deny`
  （`deny` 覆盖一切，须显式括号；`-` 优先级高于 `|`）。
- 插件经 `ctx.permission.require/check` 自行检查；`require` 不满足抛 `PermissionDenied`，
  拒绝事件写入日志（审计轨迹）。

```python
def on_output(self, ctx, data: bytes) -> bytes:
    if ctx.permission.check("network.connect"):
        return data
    return data
```

**权限命名**：自定义能力名，需在文档中声明语义。内置插件使用的示例：
`filesystem.write`、`filesystem.execute`、`network.connect`、`session.read`、`session.write`。

---

## 12. 自定义 CLI 选项（cliOptions）

插件可在清单声明自己的 CLI 选项，调用方经命令行直接传参（无需约定统一的
`--pluginargs` 选项名）。仅 `cli` / `session` 形态可用（process 形态的消息源
命令不在会话 IO 命令集内，无注入路径）。

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
| `name` | ✅ | `^[a-z0-9][a-z0-9-]*$`；生成长选项 `--<name>` |
| `short` | — | 单字符；生成短选项 `-<short>` |
| `type` | — | `str`（默认）/ `int` / `float` / `flag` / `choice` |
| `choices` | choice 时必填 | 非空字符串列表 |
| `default` | — | 类型必须与 `type` 匹配（`flag` 未声明 default 时视为 False） |
| `help` | — | `--help` 展示文本 |
| `commands` | — | 生效命令白名单；可取值 `exec`/`send`/`advsend`/`read`/`mouse`；空 = 全部 |

选项注册到对应命令的 argparse（`python app.py exec --help` 可见），值类型/枚举由
argparse 校验，非法取值解析期即报错。

### 冲突检测（检测到即不加载插件）

加载期对全部插件清单做交叉冲突检测（daemon 与客户端一致执行），任一冲突 →
插件不加载（daemon 侧 `plugin list` 可见 `broken` 与错误原因；客户端侧跳过加载、
选项不注册）：

- 与内置参数冲突：选项串命中该命令已注册的内置选项（如声明 `{"name": "timeout"}` 与 `--timeout` 冲突）
- 插件间冲突：两插件在同一命令声明相同长选项或相同短选项 → 双方都不加载
- 按命令域判定：仅在双方都注册的命令上同串才算冲突（A 在 exec 声明 `--pa`、B 在 send 声明 `--pa` → 无冲突）

### 读取方式

- **CLI 插件**：三阶段钩子经 `ctx.options` 读取本次调用显式提供的选项（`{选项名: 值}`）。
- **session 插件**：`ctx.options` 在会话生命周期内所有钩子可读（on_init/on_attach/
  on_input/on_output/on_snapshot/on_event/on_poll/handle_command/inspect_state 等）。
  选项在 exec 创建会话时注入；后续 `send/read/mouse` 携带的选项**合并更新**到会话；
  动态 `plugin attach` 时沿用会话已存选项。未提供任何选项时为空 `dict`。
- 仅显式提供的选项值随消息下发（默认值不传输，插件经清单自取）。

### 使用示例

```bash
python app.py exec mysession -c "cmd" --plugin mydemo --pa "插件读取" --num 7
python app.py send mysession -i "hi" --pa "更新后的值" --timeout 5
python app.py read mysession --pa "x" -t "done"
```

daemon 侧对 `msg.pluginOptions` 统一校验（对象/标量类型/总大小上限），非法形状的请求直接返回错误。

---

## 13. 插件上下文输出（帮助文档）

插件目录下若存在 `<插件名>.md`，其内容会**输出到 CLI 给用户看**（stderr 信息区，
不进入会话输出/终端画面）：

```
[plugin subagent context]
<subagent.md 内容>
[plugin subagent context end]
```

**输出时机（按形态）：**

| 形态 | 输出时机 |
|------|----------|
| `process` | 守护进程启动时（`app.py start` 后 CLI 输出） |
| `session` / `cli` | `exec --plugin <name>` 时 CLI 输出 |

**约定：**

- 输出到 CLI（stderr 信息区），不注入会话输出流、不渲染进终端画面
- **只发一次**：每个 daemon 周期内每插件文档只输出一次，重启 daemon 后重新发送
- **内容变化重发**：同周期内插件 .md 文件内容更新（sha256 变化）自动重新发送
- 上限 64KB，超出截断并追加 `[context truncated]` 提示
- 清单声明 `contextHidden: true` 的插件不自动输出（用 `plugin --gethelp <name>` 按需查看）
- 文件缺失/读取失败仅跳过，不影响插件加载与挂载
- 重置：删除 `~/.pty-agent/plugin-context-state.json` 或重启 daemon

---

## 14. 插件管理命令

```bash
python app.py plugin list                          # 列出已加载插件（含状态/形态）
python app.py plugin ls <sid>                      # 列出会话挂载的插件
python app.py plugin --gethelp <name>              # 显示插件帮助文档（<插件名>.md）
python app.py plugin attach <sid> <name>           # 动态挂载到运行中会话
python app.py plugin detach <sid> <name>           # 从会话卸载
python app.py plugin cmd <sid> <name> <cmd> [...]  # 调用插件自定义命令
python app.py plugin install <path>                # 从目录安装（须含 plugin.json）
python app.py plugin uninstall <name>              # 卸载（须先 disable）
python app.py plugin enable <name>                 # 启用
python app.py plugin disable <name>                # 停用
python app.py plugin reload <name>                 # 热重载（重新加载代码与清单）
python app.py plugin info <name>                   # 详情（清单/状态/权限/事件）
python app.py plugin status <name>                 # 运行状态
python app.py plugin config <name> [key value]     # 查看/修改配置（内存态）
```

### 状态机

插件状态：`loaded`（已加载未启用）→ `enabled`（on_init+on_enable 完成）→
`disabled`/`loaded`；加载或初始化失败 → `broken`（error 可见，不参与运行）。
`plugin list` / `plugin info` 展示状态与错误原因。

### 挂载方式汇总

| 方式 | 命令 | 说明 |
|------|------|------|
| exec 注入 | `exec <sid> -c "..." --plugin <name>` | daemon 形态挂载到会话；CLI 形态客户端挂钩 + 记录到会话（后续 read/send/mouse 自动回调） |
| 自动加载 | 清单 `autoLoad` 条件命中 | 按 command（正则/关键词）、cwd（前缀/正则）、env（变量→正则）自动注入会话 |
| 动态挂载 | `plugin attach <sid> <name>` | 挂载到运行中的会话 |
| autoMount | 清单 `autoMount`（CLI 形态） | 命令自动参与 CLI 钩子链，无需 `--plugin` 显式激活 |

---

## 15. 注册新 CLI 命令（cliCommands）

CLI 形态插件可以注册全新的顶层命令（如内置 `subagent` 插件的 `codebuddy` / `devin` /
`opencode` / `claude` / `smartagent`）。

### 步骤

1. 清单声明 `cliCommands`：

```json
{
  "id": "subagent",
  "kind": "cli",
  "cliCommands": ["codebuddy", "devin", "opencode", "claude", "smartagent"]
}
```

2. 入口模块导出 `commands`（Command 子类列表，含 `name` 与 `run` 方法）：

```python
from src.cli.base import Command

class CodeBuddyCommand(Command):
    name = "codebuddy"
    help = "spawn CodeBuddy 子代理"
    use_common_args = False

    def add_arguments(self, parser):
        ...

    def run(self, args, ctx):
        ...

commands = [CodeBuddyCommand, DevinCommand, ...]
```

校验：`cliCommands` 声明集合必须与导出的命令名集合一致，否则加载失败。

---

## 16. 编写插件：完整示例

### 示例 1：会话级插件（输入变换 + 快照标记）

目录 `config/plugins/mytag/`：

`plugin.json`：

```json
{
  "id": "mytag",
  "version": "1.0.0",
  "kind": "session",
  "description": "在快照末尾追加标记，并把以 T: 开头的输入去掉前缀",
  "hooks": {
    "on_input": {"priority": 120},
    "on_snapshot": {}
  }
}
```

`__init__.py`：

```python
from src.plugins.base import Plugin


class MyTagPlugin(Plugin):
    """会话级示例插件"""

    def on_attach(self, ctx):
        ctx.logger.info("mytag attached to %s", ctx.session.id)

    def on_input(self, ctx, data):
        # 返回 None 表示拦截；这里只做前缀剥离
        if isinstance(data, str):
            return data[2:] if data.startswith("T:") else data
        return data[2:] if data.startswith(b"T:") else data

    def on_snapshot(self, ctx, text: str) -> str:
        return text + "\n[tagged by mytag]"


plugin = MyTagPlugin
```

使用：

```bash
python app.py exec myid -c "python -i" --plugin mytag -t ">>>"
python app.py send myid -i "T:print(1)" -t ">>>"
python app.py read myid -s
```

### 示例 2：进程级插件（接管消息类型）

目录 `config/plugins/myeval/`：

`plugin.json`：

```json
{
  "id": "myeval",
  "version": "1.0.0",
  "kind": "process",
  "description": "接管 myeval_exec 消息类型",
  "messageTypes": ["myeval_exec"]
}
```

`__init__.py`：

```python
from src.plugins.base import Plugin, HANDLED


class MyEvalPlugin(Plugin):
    """进程级示例插件：处理自定义消息类型"""

    def handle_message(self, ctx, msg: dict):
        if msg.get("type") != "myeval_exec":
            return None
        # 返回 dict 原样作为响应发送
        return {
            "commandType": "myeval",
            "sessionId": msg.get("id", ""),
            "result": "evaluated",
        }


plugin = MyEvalPlugin
```

客户端发送 `{"type": "myeval_exec", "id": "x"}` 即可路由到该插件
（消息类型与内置 handler 冲突时内置优先；插件间同类型按插件名序先者胜）。

### 示例 3：CLI 侧插件（transform_response 分析输出）

目录 `config/plugins/noter/`：

`plugin.json`：

```json
{
  "id": "noter",
  "version": "1.0.0",
  "kind": "cli",
  "description": "在响应输出前追加一段说明",
  "commands": ["exec", "send", "read", "mouse"],
  "hooks": {
    "transform_response": {},
    "render_response": {}
  }
}
```

`__init__.py`：

```python
from src.plugins.base import Plugin


class NoterPlugin(Plugin):
    """CLI 侧示例插件"""

    def transform_response(self, ctx, resp: dict):
        if resp.get("type") == "error":
            return None
        if "outputStream" in resp:
            resp["outputStream"] = (resp["outputStream"] or "") + "\n[note: by noter]"
            return resp
        return None


plugin = NoterPlugin
```

使用：

```bash
python app.py exec myid -c "dir" --plugin noter
```

---

## 17. 调试与测试

- **日志**：插件日志归入 `pty-plugins` / `pty-client` 分组（按模块分组独立日志文件）。
  插件内可用 `ctx.logger` 或 `logging.getLogger("pty-client")`。
- **查看状态**：`plugin list`（状态/错误）、`plugin info <name>`（清单/状态/权限/事件）、
  `plugin status <name>`（运行状态）。
- **热重载**：`plugin reload <name>` 重新加载代码与清单（保持启用状态）；
  CLI 形态插件重载在客户端进程内完成（`plugin reload` 按 kind 分发双侧重载）。
- **单测**：`tests/helpers.py` 提供 `make_manifest` / `attach_manifest` /
  `write_plugin_dir`（构造临时插件目录走真实清单/加载路径）。
- **e2e**：`tests/e2e/test_plugins_e2e.py` 覆盖 exec 注入、变换链、动态挂载、
  auto_load、自定义命令、request_return/self_unload、事件总线发布。
- **单元测试**：`tests/unit/plugins/` 覆盖 loader / registry / host / manifest /
  process_plugin / cli_options / context / state_check / subagent 等。

---

## 18. 规范与最佳实践

1. **声明即契约**：清单声明的钩子/触发必须在类中实现；未实现会导致加载校验失败（跳过插件）。
2. **异常隔离**：钩子抛异常只记日志，不影响链上其余钩子与主流程；插件应自行 try/except 处理可预期错误。
3. **线程安全**：会话级插件的 `on_output` 在 reader 线程、`poll_tick` 在监控线程、
   其余在 handler 线程被调用，宿主不做额外加锁，插件实现需保证自身线程安全。
4. **性能**：`on_input`/`on_output`/`on_snapshot` 是热路径（每块输出/每次输入），
   避免重计算；空链时宿主零开销短路，插件不应注册空实现钩子。
5. **插件选项只读**：`ctx.options` 为只读（误写会抛 TypeError），更新选项走
   `send/read/mouse` 携带的 `cliOptions` 合并。
6. **配置内存态**：`plugin config set` 仅内存，重启恢复默认；需要持久状态用 `ctx.storage`。
7. **多形态复用**：基础设施（exec 流程、装饰、命令注册）写一次，agent/形态差异用
   数据驱动声明（参考内置 `subagent` 插件：差异全部在 AgentSpec 注册表，插件本体无硬编码）。
8. **不污染主流程**：插件无法为其他插件打补丁；升级插件系统通过添加通用钩子、
   扩展系统功能、适配新插件需求实现（见 `config/plugins/AGENTS.md`）。
9. **接口同步**：插件系统接口更新时，`config/plugins/` 下所有插件须同步更新，
   不留兼容接口、旧接口、残留接口，保证代码逻辑干净。
10. **自定义命令的返回约定**：`handle_message` 返回 dict 原样发送、返回 `HANDLED`
    表示已自行完成多帧响应（调度器不再发送）、返回 None 表示未处理（调度器回错误）。
