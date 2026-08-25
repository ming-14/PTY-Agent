# 插件上下文 CLI 输出（"插件文档只发一次"）— 全面黑盒测试报告

- 测试对象：PTY-Agent 插件系统的"插件文档只发一次"功能（`<插件名>.md` → CLI stderr 信息区）
- 测试方式：**纯黑盒**，仅通过外部 CLI（`python app.py ...`）验证，未 import / 直接调用 `src/` 内部 Python API
- 规格依据：`config/plugins/README.md`「上下文输出（<插件名>.md）」章节
- 测试日期：2026-08-25（本报告覆盖旧报告）

---

## 1. 环境摘要

| 项目 | 值 |
|------|----|
| 工作目录 | `C:\Users\rikka\Desktop\warp\PTY-Agent` |
| Python | 3.11（`python`，`C:\Program Files\Python311\python.exe`） |
| 操作系统 | Windows（PowerShell 环境） |
| daemon 启停 | `python app.py stop` / `python app.py start 2>&1`（stderr 合并查看） |
| daemon 数据目录 | `%USERPROFILE%\.pty-agent`（日志、历史、web 等） |
| 会话清理 | `python app.py kill <id>`（每次 exec 后立即执行） |
| exec 防卡住 | `--timeout 1/5`（`exec --help` 确认存在 `--timeout` 参数） |
| 状态文件 | `%USERPROFILE%\.pty-agent\plugin-context-state.json`（daemon 启动自动重置） |
| 临时插件注入 | 环境变量 `PTY_PLUGIN_DIRS`（Windows `;` 分隔，每项直接指向含 `plugin.json` 的目录）；daemon 启动时读取，改动后重启 daemon 生效；CLI 每次调用同样需带该变量才能发现临时插件的 `.md` |
| 内置插件 | files（process，有 files.md）、state_check（session，有 state_check.md）、ai（cli，有 ai.md）、simple（cli，有 simple.md） |
| 临时插件 | `_bbtest_plugins\ctxdemo`（session，有 ctxdemo.md）、`nomd`（session，无 .md）、`big`（session，72KB 超大 .md）；测试后已删除 |
| registry.json | 测试前/后均恢复为 4 插件（files/state_check/simple/ai）全部 enabled |

测试要点说明：
- `python app.py status` 在 daemon 未运行时会**自动拉起 daemon**（属设计行为），因此周期敏感用例中避免使用 status 探测。
- `python app.py start` 对已在运行的 daemon 打印 `Daemon already running` 后仍执行进程级上下文输出逻辑，但状态文件命中"已发送"即跳过（这正是 A2 的判定依据）。

---

## 2. 结果总览

| 编号 | 场景 | 结论 |
|------|------|------|
| A.1 | stop → start：输出 `[plugin files context]` + files.md 内容 + `[plugin files context end]` | ✅ 通过 |
| A.2 | 再次 start（daemon 已在跑）：同周期不再输出 files 上下文 | ✅ 通过 |
| A.3 | stop → start（新周期）：重新输出 files 上下文 | ✅ 通过 |
| A.4 | start 只输出进程级 files 上下文，不含 state_check/ai/simple | ✅ 通过 |
| B.5 | `exec --plugin state_check`：输出 `[plugin state_check context]` | ✅ 通过 |
| B.6 | 新会话再 `exec --plugin state_check`（同周期）：不输出 | ✅ 通过 |
| B.7 | `exec --plugin ai` 输出一次；同周期再次 exec 不输出 | ✅ 通过 |
| B.8 | state_check 已发送后 `exec --plugin simple` 仍正常输出（互不影响） | ✅ 通过 |
| C.9 | 临时插件（PTY_PLUGIN_DIRS，.md="v1"）→ 重启 daemon → exec 输出 v1 | ✅ 通过 |
| C.10 | 同周期改 .md 为 "v2" → 再 exec → **重新输出且内容为 v2** | ✅ 通过 |
| C.11 | 内容未变再 exec → 不输出 | ✅ 通过 |
| C.12 | 重启 daemon（新周期）→ exec → 重新输出 | ✅ 通过 |
| D.13 | 状态文件写为非法 JSON → exec state_check → 正常输出（容错视为未发送） | ✅ 通过 |
| D.14 | 删除状态文件 → exec state_check → 重新输出 | ✅ 通过 |
| D.15 | `disable state_check` → exec 不输出；`enable` 后恢复 | ✅ 通过 |
| E.16 | 挂载插件的会话 `read` 输出不含任何 `[plugin ... context]` 标记 | ✅ 通过 |
| E.17 | 无 .md 的临时插件 → exec 无上下文输出、命令正常 | ✅ 通过 |
| E.18 | `plugin list` / `plugin info files` 正常 | ✅ 通过 |
| S.1 | 补充：>64KB .md 截断并追加 `[context truncated]` | ✅ 通过 |

**合计：规格场景 18 项 + 补充 1 项 = 19 项，通过 19 项，失败 0 项。**

---

## 3. 场景 A — start 只发一次（进程级）

### A.1 stop → start 输出 files 上下文 ✅

```powershell
python app.py stop 2>&1
python app.py start 2>&1
```

关键输出摘录（stderr 合并）：

```
(PTY-Agent message: Daemon started)
...
[plugin files context]
# files 插件说明
本插件提供文件工具消息（file_read / file_write / ...）
...
[plugin files context end]
(PTY-Agent message: No active session.)
```

格式与 files.md 内容完全一致，含首尾标记。

### A.2 再次 start（daemon 已在跑）不输出 ✅

```powershell
python app.py start 2>&1
```

关键输出摘录：

```
(PTY-Agent message: Daemon already running (token 端口 10520))
(PTY-Agent message: No active session.)
```

无任何 `[plugin files context]` 标记（同周期不发第二次）。状态文件佐证：

```json
{ "files": { "sent": true, "sentAt": 1787625957.23, "contentHash": "46dd9a80..." } }
```

### A.3 stop → start（新周期）重新输出 ✅

```powershell
python app.py stop 2>&1
python app.py start 2>&1
```

再次出现完整 `[plugin files context] ... [plugin files context end]` 块；状态文件 `sentAt` 更新为新时间戳（1787626022.40），证明新周期重置并重发。

### A.4 start 只输出进程级 files ✅

A.1/A.3 的 start 输出中仅含 files 上下文，未出现 state_check/ai/simple 的上下文标记（与规格"process 形态 daemon 启动时输出"一致）。

---

## 4. 场景 B — exec --plugin 只发一次

### B.5 `exec --plugin state_check` 输出 ✅

```powershell
python app.py exec s1 -c "python -u -i" --plugin state_check --timeout 1 2>&1
python app.py kill s1
```

关键输出摘录：

```
[plugin state_check context]
# state_check — 通用状态检查插件
...
[plugin state_check context end]
─────────────────────────────────── snapshot ───────────────────────────────────
Python 3.11.9 ...
>>> 
[exec · timeout · 1.12s]  s1  running  pty
state: Repl
```

上下文输出到 CLI 信息区（stderr），会话命令正常执行。

### B.6 新会话同周期不再输出 ✅

```powershell
python app.py exec s2 -c "python -u -i" --plugin state_check --timeout 1 2>&1
python app.py kill s2
```

输出仅含 snapshot 与 exec 结果，**无** `[plugin state_check context]`（同周期第二次不发）。

### B.7 `--plugin ai` 输出一次，同周期再次不输出 ✅

```powershell
python app.py exec s3 -c "echo hello" --plugin ai --timeout 5 2>&1   # 第一次：输出 ai 上下文
python app.py exec s4 -c "echo hello" --shell cmd --plugin ai --timeout 5 2>&1   # 第二次：不输出
```

第一次输出：

```
[plugin ai context]
# ai 插件说明
...
[plugin ai context end]
```

状态文件确认 `ai: {sent: true, contentHash: 7c961b34...}`；第二次 exec 无 ai 上下文（只发一次生效）。

> **环境观察（非缺陷）**：ai 插件上下文功能正常，但其"AI 二次分析"能力依赖 `config/plugins/ai/bin/aichat.exe`，当前环境该文件不存在（gitignore，需 BUILD 下载）。当 exec 命令产生输出文本时，ai 插件的 transform_response 会调用 aichat → `sys.exit("aichat.exe not found ...")` 导致 CLI 以退出码 1 结束。此为环境资产缺失，与本功能（上下文只发一次）无关；上下文发送在请求前完成，不受影响。

### B.8 插件状态互不影响 ✅

```powershell
python app.py exec s6 -c "echo hello" --shell cmd --plugin simple --timeout 5 2>&1
python app.py kill s6
```

state_check 已发送后，exec --plugin simple 仍正常输出其上下文：

```
[plugin simple context]
# simple 插件说明
...
[plugin simple context end]
hello
```

---

## 5. 场景 C — 内容变化重发（临时插件，PTY_PLUGIN_DIRS）

临时插件：`_bbtest_plugins\ctxdemo\`（plugin.json kind=session + `__init__.py` 导出 `plugin` + `ctxdemo.md`）。

### C.9 重启 daemon → exec 输出 v1 ✅

```powershell
$env:PTY_PLUGIN_DIRS = "C:\...\_bbtest_plugins\ctxdemo;C:\...\_bbtest_plugins\nomd"
python app.py stop 2>&1
python app.py start 2>&1
python app.py exec c9 -c "echo ctxdemo-v1" --shell cmd --plugin ctxdemo --timeout 5 2>&1
```

关键输出摘录：

```
[plugin ctxdemo context]
# ctxdemo 插件说明
context version v1
[plugin ctxdemo context end]
─────────────────────────────────── snapshot ───────────────────────────────────
ctxdemo-v1
```

### C.10 同周期改 .md 为 v2 → 重新输出 ✅

```powershell
# 修改 _bbtest_plugins\ctxdemo\ctxdemo.md 内容为 "context version v2"（不重启 daemon）
python app.py exec c10 -c "echo ctxdemo-v2" --shell cmd --plugin ctxdemo --timeout 5 2>&1
```

关键输出摘录：

```
[plugin ctxdemo context]
# ctxdemo 插件说明
context version v2
[plugin ctxdemo context end]
─────────────────────────────────── snapshot ───────────────────────────────────
ctxdemo-v2
```

内容变化（sha256 变化）自动重新发送，且内容为新版本 v2。

### C.11 内容未变再 exec → 不输出 ✅

```powershell
python app.py exec c11 -c "echo ctxdemo-v2-again" --shell cmd --plugin ctxdemo --timeout 5 2>&1
```

输出仅 snapshot（`ctxdemo-v2-again`）与 exec 结果，无上下文标记。

### C.12 重启 daemon（新周期）→ 重新输出 ✅

```powershell
python app.py stop 2>&1
$env:PTY_PLUGIN_DIRS = "..."; python app.py start 2>&1
$env:PTY_PLUGIN_DIRS = "..."; python app.py exec c12 -c "echo new-cycle" --shell cmd --plugin ctxdemo --timeout 5 2>&1
```

重新输出 `[plugin ctxdemo context]`（内容仍为 v2，文件未再改动），证明新周期重置。

---

## 6. 场景 D — 边界条件

### D.13 状态文件损坏容错 ✅

```powershell
Set-Content -Path "$HOME\.pty-agent\plugin-context-state.json" -Value "{corrupt" -Encoding utf8 -NoNewline
python app.py exec d13 -c "echo corrupt-test" --shell cmd --plugin state_check --timeout 5 2>&1
```

损坏 JSON 被容错视为"未发送"，正常输出 `[plugin state_check context] ... [plugin state_check context end]`，且会话命令正常执行；执行后状态文件被重写为合法 JSON（含 state_check 已发送记录）。

### D.14 删除状态文件 → 重新输出 ✅

```powershell
Remove-Item "$HOME\.pty-agent\plugin-context-state.json" -Force
python app.py exec d14 -c "echo delete-test" --shell cmd --plugin state_check --timeout 5 2>&1
```

输出 `[plugin state_check context] ... [plugin state_check context end]`（状态重置后重新发送）。

### D.15 禁用插件不输出；enable 恢复 ✅

```powershell
python app.py plugin disable state_check 2>&1     # (PTY-Agent message: 已禁用: state_check)
python app.py exec d15a -c "echo disabled-test" --shell cmd --plugin state_check --timeout 5 2>&1
python app.py plugin enable state_check 2>&1      # (PTY-Agent message: 已启用: state_check)
python app.py exec d15b -c "echo reenabled-test" --shell cmd --plugin state_check --timeout 5 2>&1
```

- disable 后 exec：输出仅 snapshot（`disabled-test`），**无** state_check 上下文（registry.json 显式禁用 → 跳过）；命令本身正常执行。
- enable 后：插件恢复可用（exec 正常、挂载不受影响）。
- registry.json 已确认 `state_check: {"enabled": false}` → `{"enabled": true}` 持久化。

> **行为说明（符合规格）**：同周期内 enable 后上下文不会立刻重发——状态文件仍记录"已发送"，只发一次语义依旧生效；上下文重新发送发生在下一个 daemon 周期（重启）。这与规格"每个 daemon 周期内每插件文档只输出一次"一致，非缺陷。

---

## 7. 场景 E — 回归

### E.16 read 输出不含上下文标记 ✅

```powershell
python app.py exec e16 -c "python -u -i" --plugin state_check --timeout 1 2>&1   # 会话挂载 state_check
python app.py read e16 --timeout 1 2>&1
python app.py kill e16
```

read 输出仅含终端快照（Python 提示符）与 `[read · timeout ...]` 结果，**无任何** `[plugin ... context]` 标记——上下文绝不进入会话输出流/终端画面。

### E.17 无 .md 插件静默跳过 ✅

```powershell
$env:PTY_PLUGIN_DIRS = "...;_bbtest_plugins\nomd"
python app.py exec e17 -c "echo nomd-test" --shell cmd --plugin nomd --timeout 5 2>&1
```

输出仅 snapshot（`nomd-test`）与 exec 结果，无上下文标记（缺失 .md 静默跳过），命令正常执行。

### E.18 插件管理命令正常 ✅

```powershell
python app.py plugin list 2>&1
python app.py plugin info files 2>&1
```

`plugin list` 列出全部插件（含临时注入的 ctxdemo/nomd，已加载）：files(process)/state_check(session)/ctxdemo/nomd 均 enabled，ai/simple 为 cli loaded；`plugin info files` 正常显示清单/形态/状态/权限。

---

## 8. 补充测试 — 64KB 截断（S.1）

```powershell
# 构造 72028 字节 big.md（超 64KB），插件 big（session）
$env:PTY_PLUGIN_DIRS = "...;_bbtest_plugins\big"
python app.py stop / start（重启 daemon 加载新插件）
python app.py exec big1 -c "echo big-test" --shell cmd --plugin big --timeout 5 2>&1
```

关键输出摘录（末尾）：

```
...0123456789abcdef0123456789abcdef...
[context truncated]
[plugin big context end]
```

> 注：输出为截断后的内容，末尾追加 `[context truncated]`，随后正常闭合 `[plugin big context end]`，且截断不破坏标记结构。✓

---

## 9. 失败分析

**失败 0 项，无失败分析。**

### 观察记录（非缺陷，供后续参考）

1. **ai 插件依赖 aichat.exe 缺失**：`config/plugins/ai/bin/aichat.exe` 不存在（BUILD 未下载）。当 exec --plugin ai 的命令产生输出文本时，ai 插件 transform_response 阶段调用 aichat 失败并以 `sys.exit` 结束 CLI（退出码 1）。上下文输出功能本身正常（每次周期一次）。若要完整验证 ai 插件分析能力，需先执行 BUILD 下载 aichat.exe。
2. **enable 后同周期不重发**：D.15 中 enable state_check 后，同周期内 exec 不重新输出上下文（状态文件"已发送"仍生效），重启 daemon 后恢复——符合"每 daemon 周期只发一次"语义。
3. **start 与 reset 的时序**：daemon 先获取单实例锁、后初始化插件注册表并重置上下文状态文件；CLI 的 `start` 在探测到 daemon 存活后输出进程级上下文。理论上存在 CLI 在 daemon 重置状态前读取旧状态文件的极小竞态窗口，实测多次 stop/start 均表现正确（daemon 启动初始化开销远大于 CLI 后续路径），未观察到异常。
4. **`status` 命令自动拉起 daemon**：周期敏感用例中需避免使用（会影响"只发一次"判定）。

---

## 10. 环境清理（已完成）

| 项 | 结果 |
|----|------|
| `python app.py stop` | ✅ daemon 已停止 |
| kill 全部测试会话（s1/s2/s5/s6/e16 等） | ✅ 无残留会话 |
| 删除临时插件目录 `_bbtest_plugins`（ctxdemo/nomd/big） | ✅ 已删除 |
| 删除 `~/.pty-agent/plugin-context-state.json` | ✅ 已删除（避免残留状态影响用户） |
| registry.json 恢复 4 插件全部 enabled | ✅ 已恢复（files/state_check/simple/ai） |
