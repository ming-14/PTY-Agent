# state_check — 通用状态检查插件

纯启发式的终端状态检测插件。**命令返回时触发一次**检查当前终端状态，检测结果作为 `terminalState` 附加到返回信息（exec/send/read/mouse 响应）。插件不轮询、不事件订阅、不干预命令执行。

## 钩子

插件仅提供两个钩子：

1. **返回钩子**（`inspect_state`）：命令返回时（响应构造）触发一次，按优先级表检查屏幕快照、光标位置、备用屏幕、前台进程，返回状态随响应附加：

```json
"terminalState": {"state": "Repl", "reason": "repl prompt", "altScreen": false}
```

2. **命令钩子**（`handle_command`）：外部随时查询当前状态：

```powershell
python app.py plugin cmd my-session state_check status
# → {"state": "Repl", "reason": "repl prompt", "altScreen": false}
```

## 注册

插件系统通过 `config/plugins/plugins.json` 显式指定插件位置：

```json
{
  "enabled": true,
  "plugins": [
    "config/plugins/state_check"
  ]
}
```

修改后需重启守护进程生效。也可用 `PTY_PLUGIN_DIRS` 环境变量追加插件位置（路径分隔符分隔）。

## 使用

```powershell
# 注入到新会话（exec 时指定）
python app.py exec my-session -c "python -u -i" --plugin state_check

# 动态挂载到运行中的会话
python app.py plugin attach my-session state_check

# 卸载
python app.py plugin detach my-session state_check
```

## 检测规则（优先级由高到低，匹配即返回）

| 优先级 | 条件 | 结果状态 | 说明 |
|--------|------|---------|------|
| 2 | 备用屏幕激活（`\x1b[?1049/1047/47/1048` 开关） | `Editor` | vim/htop/less 等 TUI 应用 |
| 3 | 最后一行匹配 REPL 正则（19 种，如 `^>>>\s?$`）且光标不在行首 | `Repl` | Python/IPython/PDB/Ruby/MySQL/Node 等 |
| 4 | 最后一行匹配 Shell 正则（8 种，如 `\$\s?$`）且光标不在行首 | `WaitingForInput` | Shell 提示符 |
| 5 | 前台进程名匹配 bash/zsh/sh/fish/dash/tcsh/csh/pwsh/powershell | `WaitingForInput` | 前台进程是 Shell |
| 7 | 最后一行含 `-- INSERT --` / `-- NORMAL --` / `gnu nano` 等（6 种） | `Editor` | 编辑器模式 |
| 8 | 最后一行含 `(END)` / `manual page` / `--more--` 等（5 种） | `Pager` | 分页器模式 |
| 9 | 全文同时匹配 `("do you want", "yes")` 等（3 种组合） | `Confirm` | Agent 权限提示 |
| 10 | 最后一行含 `password:` / `[sudo]` / `passphrase:` 等（8 种） | `Password` | 密码提示 |
| 11 | 最后一行含 `[y/n]` / `continue?` / `are you sure` 等（12 种） | `Confirm` | 确认提示 |
| 12 | 光标在行首（列 0） | `Running` | 命令执行中 |
| 13 | 最近 3 行含 `error:` / `fatal:` / `traceback` / `panic:` 等（17 种） | `Error` | 错误 |

全部正则与指示词清单见 `__init__.py` 模块级常量。

## 实现要点

- **数据源**：屏幕快照文本（`ctx.session.get_snapshot()`）、光标位置（`ctx.session.cursor_position()`）、备用屏幕（`ctx.session.is_alt_screen()`，终端层跟踪）、会话进程树。
- **备用屏幕跟踪**：下沉到终端层（`TerminalScreen.feed` 对原始 VT 流维护 64 字节尾部窗口拼接检测，跨读取边界可靠；同窗口多序列取最后者），插件无需接触原始字节流。
- **防误匹配**：以 `$ ` / `# ` / `> ` / `% ` 开头的行视为命令输出行（如命令回显、注释），不作为提示符判定（优先级 3/4 的先行条件）；纯 `$ ` 提示符不受影响。
- **前台进程**：经 psutil 解析会话进程树，未安装时该级自动跳过。
- **一次性检查**：返回钩子每次都读取会话实时状态，不维护缓存。

## 声明与约束

- 触发声明：`triggers = []`（无事件/轮询触发，返回钩子与命令钩子实现即生效）
- 未启用级别：进程退出检测（优先级 1）、自定义 shell 正则（优先级 6）
- 检测本身不干预命令的返回时机（返回原因仍由 trigger/超时等决定），仅附加状态信息