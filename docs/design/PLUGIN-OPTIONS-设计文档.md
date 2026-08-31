# 插件自定义参数注册(cliOptions)设计文档

## 背景与目标

当前插件系统(`src/plugins/`,见 `config/plugins/README.md`)中,插件**无法接收调用方通过命令行传入的自定义参数**:

- `exec --plugin <name>` 全程只传递插件名,无参数概念
- `send/read/mouse` 没有 `--plugin` 选项,仅按会话挂载的 CLI 插件自动挂钩
- 现有传参通道仅三条:会话插件 `plugin cmd <sid> <name> <command> [args...]`(位置参数)、
  插件配置(`plugin config`，内存态：清单默认 + 内存覆盖，重启清空)

目标(用户决策):

1. **参数由插件自己声明注册**:插件在 `plugin.json` 清单中声明自己的 CLI 选项
   (如 `--pa`、`--any`),CLI 解析器动态注册,选项名不写死
2. **冲突检测,检测到就不加载插件**:
   - 插件选项与内置 CLI 选项冲突(如声明 `--timeout`)→ 插件不加载
   - 多个插件之间选项冲突(同一命令上同名长/短选项)→ 双方都不加载
   - 检测在 daemon 与客户端两侧一致执行
3. **会话生命周期内每次钩子都能读**:session 插件挂载后,参数在会话生命周期内
   的所有钩子调用(含后续 `send/read/mouse` 更新)中经 `ctx.options` 可读

原则:不留旧接口、残留接口与死代码;实现干净,必要时重构;不采用降级/兼容方案。

## 设计总览

```
plugin.json 声明 cliOptions
        │
        ▼
┌─ 冲突检测(check_cli_option_conflicts, 共享模块) ─┐
│  内置保留选项表 RESERVED_OPTIONS + 全部插件清单      │
│  冲突 → daemon: BROKEN 不加载 / 客户端: 跳过        │
└────────────────────┬──────────────────────────────┘
                     ▼
┌─ CLI 侧(客户端进程)──────────────────────────────────┐
│ main: 先加载插件清单 → build_parser 动态注册选项       │
│ parse_args → 收集 plugin_<id>_<name> → Client        │
│ cmd_exec/send/read/mouse: msg["pluginOptions"] 注入  │
│ CLI 插件: CliContext.options 钩子内可读               │
└────────────────────┬──────────────────────────────┘
                     ▼
┌─ daemon 侧 ────────────────────────────────────────┐
│ exec_handler → create_session(plugin_options)      │
│ send/read/mouse handler → session 更新选项(合并)    │
│ PluginHost 持有 per-plugin 选项                      │
│ PluginContext.options → 会话生命周期内所有钩子可读    │
└───────────────────────────────────────────────────┘
```

## 一、清单扩展(cliOptions)

### 字段定义

```json
{
  "id": "mydemo",
  "kind": "session",
  "cliOptions": [
    {
      "name": "pa",
      "short": "p",
      "type": "str",
      "default": null,
      "help": "要注入插件的参数",
      "commands": ["exec", "send"]
    },
    {
      "name": "any",
      "type": "flag",
      "help": "任意开关"
    }
  ]
}
```

| 字段 | 必填 | 规则 |
|------|------|------|
| `name` | 是 | `^[a-z0-9][a-z0-9-]*$`;生成长选项 `--<name>` |
| `short` | 否 | 单字符(`[a-zA-Z0-9]`);生成短选项 `-<short>` |
| `type` | 否 | `str`(默认)/ `int` / `float` / `flag` / `choice` |
| `choices` | choice 时必填 | 非空字符串列表 |
| `default` | 否 | 类型必须与 `type` 匹配(`flag` 未声明时视为 False) |
| `help` | 否 | `--help` 展示文本 |
| `commands` | 否 | 生效命令白名单;可取值 `exec`/`send`/`advsend`/`read`/`mouse`;空或缺省 = 全部 |

### 校验规则(manifest 结构校验,失败即插件不加载)

- `cliOptions` 必须为数组;每项为对象
- 同一插件内 `name` 唯一、`short` 唯一
- `type` 非法、`choices` 缺失/非字符串列表、`default` 类型不匹配 → 校验失败
- `commands` 含未知命令名 → 校验失败(声明即契约,防拼写错误静默失效)
- **仅 `kind=cli` / `kind=session` 可用**;`process` 形态声明 → 校验失败
  (process 插件经 `messageTypes` 接管 `file_*` 等消息,其消息源命令不在
  会话 IO 命令集内,选项无注入路径,禁止声明避免半残功能)

## 二、冲突检测(共享模块 `src/plugins/cli_options.py`)

### 保留选项表 `RESERVED_OPTIONS`

`Dict[command, frozenset[str]]`:命令 → 该命令 argparse 已注册的全部选项串
(长+短,如 `{"exec": frozenset({"--command", "-c", "--timeout", ...})}`)。
含 argparse 自动生成的 `-h/--help`(插件声明 `--help` 同样视为冲突)。

- 覆盖命令:`exec` / `send` / `advsend` / `read` / `mouse`
  (命令集常量 `CLI_OPTION_COMMANDS` 定义于 `manifest.py`,cli_options 同源复用)
- 来源:与 `src/cli/commands/*` + `src/cli/common_args.py` 的 argparse 定义
  保持一致;**由不变量单元测试保证同步**(测试构建真实解析器并比对选项串集合),
  防止新增内置参数后表漂移

### 冲突判定 `check_cli_option_conflicts(manifests) -> Dict[str, str]`

输入:全部插件清单(含 cli/session/process,与加载顺序无关,结果对称)。
输出:冲突插件 id → 错误描述。

对每个命令逐一判定:

1. **插件 vs 内置**:插件选项串(长/短)命中 `RESERVED_OPTIONS[cmd]` → 插件冲突
2. **插件 vs 插件**:两插件在同一命令上声明了相同长选项或相同短选项 → 双方都冲突

规则:

- 冲突按命令域计算:仅在双方都注册的命令上冲突才成立
  (A 在 exec 声明 `--pa`、B 在 send 声明 `--pa` → 无冲突)
- 短选项与长选项分别检测,任一命中即冲突
- 任一命令上存在冲突 → 该插件整体不加载(全部选项不注册)

### 不加载语义(两侧一致)

- **daemon 侧**:`PluginRegistry` 对全部已加载清单(含 cli 形态,参与交叉检测)
  计算冲突;冲突插件登记为 `BROKEN`,`error` 可见(`plugin list/info`),
  不参与 enable/instantiate
- **客户端侧**:`CliPluginHost` 对同一清单集计算冲突;冲突的 cli 插件跳过加载,
  其选项不注册;daemon 形态插件冲突时客户端同样不注册其选项
- 冲突在 `reload` / `load_dir` 后重新计算(每次登记变化即刷新)

## 三、CLI 侧:解析顺序调整与动态注册

### 现状约束

`src/cli/main.py` 当前顺序:`build_parser()` → `parse_args()` → `setup_cli_plugins()`
——解析时插件宿主尚不存在,插件选项无法注册。

### 调整后顺序

```
1. setup_cli_plugins()           # 提前:加载全部清单 + 冲突检测 + 实例化 cli 插件
2. registry.build_parser(plugin_registrations=...)   # 动态注册插件选项
3. args = parser.parse_args()
4. collect_option_values(args, registrations)        # → plugin_options
5. CommandContext(plugin_options=...) → Client(plugin_options=...)
6. 命令分发(不变)
```

要点:

- `setup_cli_plugins` 对所有调用执行(含本地命令;插件加载本身无副作用,
  `CliPluginHost` 已按插件隔离异常)
- `CommandRegistry.build_parser` 新增 `plugin_registrations` 参数:
  对每个非冲突插件的每个选项,按 `commands` 白名单注册到对应子命令 parser:

  ```python
  parser.add_argument("--pa", "-p", dest="plugin_mydemo_pa",
                      type=..., choices=..., default=argparse.SUPPRESS, help=...)
  ```

- `dest` 用 `plugin_<id>_<name>` 命名空间隔离,无跨插件/跨命令冲突
- `default=argparse.SUPPRESS`:仅用户显式提供时属性才存在,
  值收集只含本次调用实际传入的参数(默认值不随消息下发,插件经清单自取)
- `flag` → `action="store_true"`;`int/float/choice` → argparse 原生类型/选项校验
- 注册期选项串冲突已由冲突检测前置排除,argparse 不会因重复选项崩溃

### 值收集与注入

`collect_option_values(args, registrations) -> Dict[plugin_id, Dict[name, value]]`:

- 遍历注册映射,`getattr(args, "plugin_<id>_<name>", None)`(SUPPRESS 保证
  未提供时为缺省)→ 仅收集实际提供的选项
- 结果经 `CommandContext.plugin_options` → `Client(plugin_options=...)` 构造参数
- `cmd_exec` / `cmd_send` / `cmd_read` / `cmd_mouse`:
  `if self.plugin_options: msg["pluginOptions"] = self.plugin_options`
- CLI 插件挂钩:`CliPluginHost.activate(names, options)` 增加选项参数,
  `CliContext.options` 新字段,三阶段钩子(before_request/transform_response/
  render_response)内可读本插件选项切片

## 四、daemon 侧:会话生命周期选项

### 接收与存储

- `exec_handler`:读取 `msg["pluginOptions"]`(经 `validate_plugin_options` 校验)
  → `create_session(plugin_options=...)` → `_attach_plugins` 时
  `plugin_host.attach(inst, options=plugin_options.get(name))`
- `send/read/mouse` handler:读取并校验 `msg["pluginOptions"]`
  → `session.plugin_host.update_options(mapping)` 合并进会话选项
  (对已挂载/未挂载插件均生效;后续动态 attach 时沿用)

### 宿主存储与上下文

`PluginHost` 新增按插件名持有的选项表 `_options: Dict[str, dict]`:

- `attach(plugin, options=None)`:显式 options 覆盖;`None` 时沿用已存选项
  (动态 `plugin attach` 不传选项 → 沿用 exec 时设置的会话选项)
- `update_options(mapping)`:逐插件合并(dict update)
- `detach` 保留选项(会话生命周期语义,重挂载沿用)

`PluginContext` 新增 `options` 字段(默认 `{}`),由 `PluginHost._ctx()` 注入
当前插件选项(快照拷贝) → **会话生命周期内所有钩子可读**:
`on_init/on_attach/on_detach/on_input/on_output/on_snapshot/on_event/on_poll/handle_command/inspect_state`。
`snapshot_info()`(plugin ls / debugInformation.plugins)含每插件 `options` 字段。

process 形态不参与 cliOptions:清单校验禁止声明,`msg.pluginOptions` 亦不进入
其消息路由(消息源命令不在会话 IO 命令集内)。

### 消息校验 `validate_plugin_options(value)`

daemon 侧对 `msg["pluginOptions"]` 统一校验(防伪造/超长):

- 必须为对象;键(插件 id)为非空字符串,长度 ≤ 64
- 值必须为对象;键(选项名)非空字符串,长度 ≤ 64
- 标量值仅允许 str/int/float/bool
- 总 JSON 序列化长度上限(64KB)
- 非法 → 请求返回 error

## 五、清理项(不留残留)

- `PluginHost.attach(self, plugin, session=None)` 的 `session` 参数
  在全部调用点均未使用,属残留接口 —— 移除,签名改为 `attach(plugin, options=None)`
- `PluginHost.attach_many` 唯一调用点(会话管理器)改为按插件逐个 attach
  (需携带 options),该方法移除
- 排查实现过程中触及的其它残留(以代码走查为准)

## 六、文件改动清单

| 文件 | 改动 |
|------|------|
| `src/plugins/manifest.py` | `PluginCliOption` dataclass;`PluginManifest.cli_options`;`CLI_OPTION_COMMANDS` 常量;`_validate` cliOptions 结构校验(含长度/重复项) |
| `src/plugins/cli_options.py` | **新增**:`RESERVED_OPTIONS`、`option_strings`、`check_cli_option_conflicts`、`validate_plugin_options`、`collect_option_values`、`build_option_registrations` |
| `src/plugins/registry.py` | 登记后刷新冲突 → BROKEN;`info/list_all` 输出 cliOptions |
| `src/plugins/base.py` | `PluginContext.options` 字段 |
| `src/plugins/host.py` | `_options` 表;`attach(options)`;`update_options`;`_ctx` 注入 options;移除残留 session 参数 |
| `src/plugins/__init__.py` | 导出新符号 |
| `src/client/cli_plugins.py` | 冲突跳过;`activate(names, options)`;`CliContext.options`;`option_registrations()` 供 parser 注册 |
| `src/client/plugin_route.py` | `_route_plugins`/`_activate_session_cli` 携带选项 |
| `src/client/transport.py` | `Client(plugin_options=...)` |
| `src/client/commands.py` | `cmd_exec/cmd_send/cmd_read/cmd_mouse` 注入 `msg["pluginOptions"]` |
| `src/cli/main.py` | 解析顺序调整;值收集;`CommandContext.plugin_options` |
| `src/cli/registry.py` | `build_parser(plugin_registrations=...)` |
| `src/cli/base.py` | `CommandContext.plugin_options` |
| `src/daemon/handlers/exec_handler.py` | pluginOptions 校验 + 透传 create_session |
| `src/daemon/handlers/send_handler.py` / `read_handler.py` / `mouse_handler.py` | pluginOptions 校验 + 更新会话选项 |
| `src/session/manager.py` | `create_session(plugin_options=...)` → `_attach_plugins` |
| `config/plugins/README.md` | cliOptions 文档(字段/冲突规则/语义/示例) |

## 七、测试计划

### 单元测试 `tests/unit/plugins/`

- `test_cli_options.py`(新增,42 项):
  - 清单校验:合法 cliOptions 解析;非法 name/type/choices/default/commands 拒绝;
    process 形态声明拒绝;同插件 name/short 重复拒绝;commands 重复拒绝;
    插件 id/选项名长度上限(≤64 字符)
  - 冲突检测:内置冲突(`--timeout`)、插件间长/短选项冲突、命令域隔离
    (不同命令同名不冲突)、对称性
  - `RESERVED_OPTIONS` 不变量:构建真实 CLI 解析器比对选项串集合
  - `collect_option_values`:SUPPRESS 语义(仅显式提供收集)、短选项收集
  - `validate_plugin_options`:形状/长度/类型边界
  - 注册表冲突:冲突插件 → BROKEN + error 可见、不 enable;
    reload 后冲突刷新(修复/保持)、冲突解除(对方卸载后恢复)
  - CLI 选项冲突恢复:一方卸载后另一方从 BROKEN 恢复
- `test_plugin_host.py`(扩展,6 项):attach 带 options、update_options 合并、
  动态 attach 沿用、detach 保留、snapshot_info 含 options、attach/reset
- `test_registry.py`(扩展):冲突 BROKEN
- `test_manifest.py`(扩展):cliOptions 解析

### 单元测试 `tests/unit/client/`

- `test_cli_plugins.py`(扩展,4 项):CLI 插件 activate 携 options → 三阶段钩子
  ctx.options 切片正确;未激活不运行;info_for 返回版本+cliOptions
- `test_transport.py`(扩展,4 项):cmd_exec/cmd_send/cmd_read/cmd_mouse 注入
  msg.pluginOptions;空选项时不注入

### E2E `tests/e2e/`(真实 PTY 会话,5 项)

- `test_plugins_options_e2e.py`(新增):
  - exec 携带 pluginOptions → 会话插件 on_attach/钩子经 ctx.options 读取
  - send 更新选项 → 合并;后续钩子看到新值
  - read 更新选项 → 合并
  - 已有会话 exec 附加带选项 → 选项合并更新
  - 非法/超长 pluginOptions → 请求被拒绝
  - 冲突插件 → BROKEN 不可挂载

## 八、文档更新计划

- `config/plugins/README.md`:清单字段表新增 `cliOptions`;新增「插件自定义参数」
  小节(声明示例、冲突规则、读取方式、命令示例)
- 本文档随实现校正(实现与计划不一致处以后者为准并回填)

## 九、风险与对策

| 风险 | 对策 |
|------|------|
| 解析顺序调整影响无插件场景 | `setup_cli_plugins` 全量 try/except,失败返回 None 等价现状;注册映射为空时 parser 行为与现状完全一致 |
| 保留选项表与 argparse 漂移 | 不变量单测构建真实解析器比对,新增内置参数即被测试拦截 |
| 插件选项与内置参数行为冲突(如 help) | 冲突前置检测拒绝加载;help 自动展示插件选项文本 |
| daemon 侧伪造 pluginOptions | 统一 `validate_plugin_options` 校验形状/长度/大小 |
| 选项值注入影响未挂载插件 | 选项按插件名存储,未挂载插件的选项仅保留不生效,挂载后即读 |
