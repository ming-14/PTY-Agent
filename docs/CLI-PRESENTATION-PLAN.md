# CLI 呈现层重构计划：类型化 Result + Presenter

> 目标：把 CLI 从"daemon 响应的 JSON dump"升级为独立"表示层"——
> `transport` 只负责返回**类型化 Result**，`presenter` 负责按命令渲染
> （内容→stdout、元信息→stderr）。彻底解决"CLI 原样输出 daemon 响应"。
> 与 [PROTOCOL-REDESIGN-PLAN.md](PROTOCOL-REDESIGN-PLAN.md)（线协议分组）配套：
> 本计划消费的响应，Phase-3 落地后即切换到分组 `data/state/meta`。

## 0. 实施状态 — 已完成

- **P1 模型层**：`client/result.py`（`Result` 类型 + `from_response` 工厂）+ `test_presenter.py` 覆盖。
- **P2 渲染层**：`client/presenter.py`（内容→stdout / 元信息→stderr / 错误+退出码 / 插件钩子 / `emit`/`emit_error`）+ 测试。
- **P3 接线**：`transport.print_response` → presenter（保留函数名兼容测试 monkeypatch）；
  `main`/`pipeline` 走 presenter 的 debug/渲染钩子/退出码；本地消息（set-default/keygen/start/--show-config/警告）全部转 presenter，零残留 JSON dump。
- **P4 收尾**：移除死代码 `client/formatter.py` 并迁移其测试；修正预先存在的 `--default`/`map_reason` 断言 bug。
- **状态**：实现对线协议分组、认证零影响；全量单测 1651 passed（2 个 session 崩溃检测为环境既存失败，与本次无关）。

## 1. 背景与目标

- 现状：CLI 是"显示器"——`transport.cmd_*` 内部直接 `print_response(resp)` 原样 JSON dump。
- 目标：
  1. `transport.cmd_*` 返回 `Result` 模型，**不再打印**。
  2. `Command.run` 拿到 Result 交给 **Presenter** 渲染。
  3. 内容与元信息**分流**：程序输出/表格主体 → stdout；状态/原因/hint/debug → stderr。
  4. 错误 → 人类可读 + 退出码；`--debug-output`/`--keep-ansi`/`-o` 等开关全部保留语义。
  5. 保留 CLI 插件变换链（`transform_response` / `render_response`）。

## 2. 现状调研结论

### 2.1 呈现链路
```
cli/main.py: 构建 registry → 解析 args → 配置管线 → setup_cli_plugins(set_render_hook)
             → dispatch → Registry.dispatch(args,ctx) → Command.run(args,ctx)
Command.run:  → client.cmd_*()          （纯转发，返回 None）
client/transport.cmd_*: 组 msg → _send_recv → 后处理(screen解压/merge defaults/svg/-o)
             → print_response(resp)     （约 25 个打印点内联在 25 个 cmd_* 里）
client/formatter.print_response:
             → 剥离 debugInformation（除非 --debug-output）
             → 插件 render_hook 返回 str 则打印它，否则 json.dumps(resp) → stdout
```

### 2.2 打印点与职责耦合
- `client/transport.py`：**I/O + 呈现混层**，25 个 `cmd_*` 结尾都在打印（`print_response`）；错误路径 `print_response(Response.error(...))` 后 `sys.exit`/`return`——退出码耦合在传输层。
- 本地命令直接打印 JSON：`cli/commands/set_default.py`、`cli/pipeline.py:apply_config_ops`（`--default`/`--show-config`）、`keygen`——均 `safe_print(json.dumps(Response.*))`。
- `client/formatter.py`：全局 `_error_printed` → `main` 据此 `exit 1`；`KeyboardInterrupt` → 130。
- `client/input.py: safe_print`：唯一 stdout 写入器（编码自适应），可 `file=` 指定 stderr。

### 2.3 输出控制选项（呈现层需全部保留）
- 公共：`--encoding`、`--default KEY VALUE`、`--debug-output`。
- 会话 IO（exec/send/read/mouse）：`--trigger/-t`、`--newline`、`--timeout`、`--idle-timeout`、`--idle-after-first-output`、`--keep-ansi`、`--snapshot-diff/-s`。
- 输出（exec/send/read/mouse）：`--output/-o`、`--response-format {stream,svg}`、`--svg-compression-level {0,1,2}`。
- 专属：read `--lines/-l、--grep、--offset、--column、--full`；events `--last/-l、--since、--until`；file grep `--include、--literal-text`。

### 2.4 响应形态（Result 的输入）
- 会话结果：`commandType / sessionId / uid / outputStream / outputOffset / triggerReturnReason / program{running,ptyType,exitCode,...} / debugInformation / hint / terminalState / sessionDefaults / screenBufferZ+Meta / stderrOutput+Offset`。
- 通用：`type = error|warning|info|config|pong|status`。
- 列表/事件：`list`(sessions)、`events`(pendingEvents+count)、`plugin`(plugins)、`file_*`(matches/result)、`workflow`(runs/steps)。

### 2.5 测试依赖（改动影响面）
- `tests/unit/client/test_formatter.py`：`print_response` JSON dump、strip debug、`_SHOW_DEBUG`。
- `test_exit_code_flag.py`：`error_was_printed`。
- `test_cli_optimization.py`：`print_response`/`set_debug_mode`。
- `test_cli_plugins.py`：`host.render_hook` 返回文本覆盖输出。
- `test_transport.py` / `test_file_cli.py`：`monkeypatch` `transport.print_response` 断言调用。
- `test_renderer.py`、`test_main.py`；e2e 断言 JSON 形态。

### 2.6 痛点
1. 呈现职责全在传输层，Command 层 pass-through 无返回值。
2. 内容与元信息混同一 stream，`> out.txt` 拿到 JSON，无法管道。
3. `-o`/svg 时 stdout 打的是"剔除 screenBuffer 的 dict"，仍不可读。
4. 退出码、错误、呈现全部耦合在一起。

## 3. 目标架构

```
daemon resp(dict，transport 已解压/merge)
  → client/result.py: Result.from_response(resp) → 类型化模型
  → Command.run: result = client.cmd_*(); ctx.render(result)
  → client/presenter.py: 按 result 类型渲染
       内容 → sys.stdout (safe_print)
       元信息 → sys.stderr
       错误 → 人类可读 + 退出码
```

### 3.1 分层职责
| 层 | 职责 | 改动 |
|----|------|------|
| `transport` | 组消息、发收、后处理，**返回 Result** | 25 个 `cmd_*` 去打印 |
| `result.py` | 从响应 dict 构造类型化模型，暴露 `.ok/.kind/字段` | 新增 |
| `presenter.py` | 按类型渲染，内容/元信息分流，错误与退出码 | 新增 |
| `Command.run` | 取 Result → `ctx.render(result)`；不再直接打印 | 改 |
| `formatter.py` | 收敛为 debug 剥离 + 序列化底层（供插件/内部），移除 JSON dump | 改 |
| `cli_plugins` | `transform_response` 仍在 Result 构造前；`render_response` 平移进 presenter | 接线 |

### 3.2 Result 模型矩阵（`client/result.py`）
| 模型 | 来源 commandType/type | 承载 |
|------|------------------------|------|
| `ErrorResult` | error/通用错误 | code/message/params；`.ok=False` |
| `MessageResult` | info/warning/config/pong | message/content/text |
| `SessionResult` | exec/send/read/mouse | `.data.output/.stderr`、`.state{offset,reason,program,uid}`、`.meta{hint,terminalState,debug,elapsed}` |
| `SvgResult` | response-format svg | svg 文本 → stdout 或文件 |
| `ListResult` | list | sessions(columns+rows) |
| `StatusResult` | status | running/pid/port/uptime/sessions/webUrl |
| `EventsResult` | events | events 列表、count |
| `KillResult`/`StopResult`/`CloseWinResult` | 对应 | code/msg/closed/hwnd |
| `PluginResult` | plugin | action/plugins |
| `FileReadResult`/`FileWriteResult`/`FileEditResult` | file_* | 内容/result/建议 |
| `FileGrepResult`/`FileGlobResult` | file_* | 匹配项、路径 |
| `FileTransferResult` | upload/download | 传输结果 |
| `WorkflowResult` | workflow | runs/steps/状态 |

### 3.3 Presenter 渲染通道
| 命令类别 | stdout（内容） | stderr（元信息） |
|---------|---------------|------------------|
| exec/send/read/mouse | `outputStream`（含 `--keep-ansi`） | `[cmd·reason] id running pty`、`hint`、`terminalState`、`offset`、`--debug-output` 的进程/事件/GUI/elapsed |
| list / status / plugin / workflow | 表格 `columns+rows` | hint、状态说明 |
| events | 事件行 | count/hint |
| file_* | 内容/匹配项 | 路径/建议 |
| kill/stop/closewin/etc | 单行结果 | — |
| `-o`/svg | 程序内容（写文件为副作用） | "已写入 <path>" |
| 错误 | — | `error: message` + 退出码 |

统一表格坑：`present` 内部 `_table(columns,rows)`、`_keyval(...)`、`_events(...)`、`_prog(...)` 助手，各命令零特判；`data.columns+rows` 对齐线协议分组。

### 3.4 错误与退出码
- `Result.ok=False`（`ErrorResult`）→ presenter 写 stderr、`main` `exit 1`。
- `KeyboardInterrupt` → 130（保留）。
- 退出码不再由 `formatter._error_printed` 全局判断，改由 Result 类型驱动。

### 3.5 插件钩子接线
- `CliPluginHost.transform_response` 仍在 `_send_recv` 后、Result 构造前改 dict（保留）。
- `render_response`：presenter 先试插件渲染钩子，返回非 None 文本则整体输出；否则走类型化渲染（语义不变，位置平移）。

## 4. 兼容与迁移

- 呈现层消费现扁平响应，**不依赖线协议是否已分组**；Phase-3 落地分组后仅改 `result.from_response` 字段来源。
- `--debug-output` 仍控制元信息详略；`formatter` 保留为底层，不破坏插件。
- e2e 断言从"JSON 形态"改为"stdout 内容 + stderr 元信息"。
- 单条命令退出码语义保持不变（error→1，Ctrl-C→130）。

## 5. 分步实施

### P1 — Result 模型层
- 新增 `client/result.py`：`Result` 基类 + `from_response(resp)` 工厂 + 各类型模型；`.ok/.kind/字段`。
- 提取 debug 剥离逻辑到 result 构造处（承接 formatter 的 `_strip_debug_info`）。
- 单测：`tests/unit/client/test_result.py`（各命令响应 → 模型字段、`ok`、debug 剥离）。

### P2 — Presenter 渲染层
- 新增 `client/presenter.py`：`present(result, out=stdout, err=stderr)`；内容/元信息分流；`_prog/_table/_keyval/_events/_error` 助手；`--debug-output` 控制详略。
- 插件 `render_response` 钩子接入 presenter。
- 单测：`test_presenter.py`（capture stdout/stderr、cwd 分流、各命令渲染）。

### P3 — 传输/命令接线
- `transport.cmd_*`（25 处）去掉 `print_response`，改为返回 `Result`；错误路径返回 `ErrorResult`。
- `CommandContext` 增 `render(result)`；各 `Command.run` 改为 `ctx.render(client.cmd_*())`。
- 本地命令（set-default/pipeline/keygen）接入 `MessageResult`/`ErrorResult` 渲染。
- `formatter.py` 收敛：仅留 `set_debug_mode` + 插件序列化底层；移除 `error_was_printed` json 路径（退出码由 Result 驱动）。
- 更新 `test_transport/test_file_cli/test_cli_optimization/test_exit_code_flag/test_formatter`。

### P4 — 链路验证与收尾
- 全命令呈现走查：四终端命令 × `-o/svg/--keep-ansi/-s/--full/--lines/--grep/--column`；list/status/events/kill/plugin/file/workflow。
- 三认证 e2e 回归；`> out.txt` 管道语义验证。
- 文档同步（README 输出示例、ARCHITECTURE 附录）；按清洁度移除本计划文档。

## 6. 测试策略

- 单元：`result`（模型构造/ok/debug 剥离）、`presenter`（stdout/stderr 分流、表格/事件/错误、插件钩子）、`formatter`（降级为底层）、`cli`（退出码/help）。
- 集成：loopback TCP 全链路，断言 cmd_* 返回 Result 而非打印。
- e2e：三认证 × 命令往返，断言内容→stdout、元信息→stderr、退出码正确。

## 7. 风险与依赖

| 风险 | 说明 | 缓解 |
|------|------|------|
| 影响面大 | 25 个 cmd_* + 全部命令 + tests | 分 P1–P4，每步可测 |
| 插件渲染 | ai 等经 transform_response/render_hook 覆盖输出 | P2 平移钩子、先单测 |
| stderr 分流改动管道语义 | 依赖现有 `> out.txt` 的脚本 | P4 专门 e2e 验证管道 |
| 与线协议分组耦合 | Result 字段来源随协议变化 | 抽象 `from_response`，字段来源后期切换 |