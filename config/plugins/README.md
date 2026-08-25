# config/plugins/ — 插件目录（daemon 侧 + CLI 侧）

插件实现目录。每个插件为**自包含目录**：`plugin.json` 清单声明元数据/声明/配置
默认值，代码与资产随目录携带。目录发现与状态管理见 `registry.json`。

## 目录结构

```
config/plugins/
├── registry.json        # 插件系统总开关 + 各插件启用状态（enable/disable 持久化）
├── policy.json          # 权限策略（可选）：按插件 id 追加授予/拒绝权限
├── __init__.py          # 包标记（空）
├── files/               # 文件工具插件（进程级：file_read/write/edit/grep/glob/upload/download）
│   ├── plugin.json      # 清单：messageTypes/needsIO/权限/配置默认值
│   ├── config.schema.json  # 配置 JSON Schema（合并后校验）
│   ├── files_plugin.py  # FilesPlugin（on_init 注入配置 + handle_message 分发）
│   ├── settings.py      # 运行设置持有器（默认值来自 plugin.json）
│   ├── state.py history.py diff.py permission.py paths.py errors.py  # 公共模块
│   ├── read/ write/ search/ transfer/   # 各用例实现
│   └── README.md
├── state_check/         # 终端状态检查插件（session：inspect_state + handle_command）
│   ├── plugin.json
│   ├── __init__.py
│   └── README.md
├── simple/              # CLI 侧响应精简插件（kind=cli：客户端进程内 render 钩子）
│   ├── plugin.json
│   └── __init__.py
└── ai/                  # CLI 侧 AI 二次分析插件（kind=cli，自包含 aichat 资产）
    ├── plugin.json      # 清单：commands/权限/配置默认值（prompt/timeout）
    ├── __init__.py      # AiPlugin（transform_response 分析，覆盖 outputStream）
    ├── common.py        # aichat 桥接（run_aichat_capture 等）
    ├── talk.py / _finderror.py / config_manager.py   # aichat 独立工具
    ├── bin/aichat.exe   # aichat 可执行文件（BUILD.py 下载，gitignore）
    ├── config/config.yaml(.example)  # aichat 模型/密钥配置 + 模板（自愈重建）
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
    "files": {"enabled": true},
    "state_check": {"enabled": true},
    "simple": {"enabled": true},
    "ai": {"enabled": true}
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
| `kind` | 形态：`process`（进程级）/ `session`（会话级）/ `cli`（CLI 侧） |
| `description` | 功能描述 |
| `entry` | 入口文件名（默认 `__init__.py`，须导出 `plugin` 属性） |
| `triggers` | 触发声明（仅 session）：`event` / `poll`（声明须实现对应钩子） |
| `pollInterval` | poll 触发间隔（秒，声明 poll 时必填） |
| `autoLoad` | 自动加载条件（仅 session）：`command`（正则/关键词）、`cwd`（前缀/正则）、`env`（变量→正则） |
| `messageTypes` | 接管的消息类型（仅 process，须实现 handle_message） |
| `needsIO` | 是否需要 I/O 通道（多帧传输协议用） |
| `commands` | 生效命令白名单（CLI 形态；空=全部命令） |
| `hooks` | 钩子优先级声明：`{"on_input": {"priority": 120}}` |
| `permissions.required` | 必需能力列表（见下方权限） |
| `config.defaults` | 配置默认值（+ 可选 `config.schema.json` 校验） |
| `events.subscribe` | 订阅的 daemon 事件总线主题模式（`*` 单段 / `>` 多段） |
| `dependencies` | 依赖声明：`plugins`（插件依赖）、`python`（Python 包依赖） |

**声明即契约**：清单声明的触发方式/钩子必须在入口模块的插件类中实现，
校验失败仅跳过该插件，不影响其他插件与主流程。

## 插件形态（kind）

| kind | 执行位置 | 生命周期 |
|------|----------|----------|
| `process` | daemon 进程 | 注册表 enable 时构造单例（on_init → on_enable）；disable → on_disable；`messageTypes` 接管消息路由 |
| `session` | daemon 进程 | 规范实例随 enable 创建（on_init → on_enable，收总线事件）；每次会话挂载构造独立实例（on_init → on_attach），卸载 → on_detach |
| `cli` | 客户端进程 | 每次命令进程启动时加载（on_init）；处理请求/响应三阶段钩子（before_request / transform_response / render_response），经 `exec --plugin` 或会话挂载列表 activate 后自动派发 |

## 配置

- 默认值：`plugin.json` `config.defaults`（基准）
- 用户配置：插件目录 `config.yaml`（缺失时按默认值自动生成；修改后 `plugin config <id> set` 或重启生效）
- 环境变量覆盖：`PTY_PLUGIN_<ID>_<KEY>`（扁平键，最高优先）
- 校验：插件目录 `config.schema.json`（JSON Schema 子集：type/enum/minimum/maximum/
  minLength/maxLength/pattern/items/properties/required/additionalProperties），
  合并后校验，失败时插件进入 BROKEN 状态（错误可见）

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
    "files": {"grant": ["network.connect"], "deny": []}
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

插件目录下若存在 `<插件名>.md`（如 `state_check.md`、`files.md`），其内容会
**输出到 CLI 给用户看**（stderr 信息区，不进入会话输出/终端画面）：

```
[plugin state_check context]
<state_check.md 内容>
[plugin state_check context end]
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
| `files` | 进程级（`messageTypes` 非空，`needsIO`） | 接管 `file_*` 文件工具消息，响应形状与原内置 handler 逐字段一致 |
| `state_check` | 会话级（`triggers = []`） | 命令返回时检测终端状态，以 `terminalState` 附加到返回信息 |
| `simple` | CLI 侧（`kind = "cli"`） | 客户端进程内把输出类响应渲染为自然文本，daemon 不加载 |
| `ai` | CLI 侧（`kind = "cli"`） | 对 exec/send/read/mouse 响应做 AI 二次分析，`exec --plugin ai` 挂载后自动回调 |

详细说明见各插件子目录 README。
