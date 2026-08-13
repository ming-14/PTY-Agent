# 沙箱增强终端方案：ConPTY 落地方案分析

> 日期：2026-08-11
> 状态：待评审（评审通过后按「实现任务拆分」委托实现）
> 背景：沙箱 cmd 会话与普通 ConPTY 会话体验不一致（无回显/方向键失效/命令回声），根因是沙箱子进程 stdin/stdout 为匿名管道，cmd 退化为批处理模式，无终端语义。

## 1. 背景与问题

### 1.1 现状

- 普通 pty 会话：`src/pty/windows/conpty.py`，子进程（cmd.exe）挂在 ConPTY 上，具备完整终端语义（行编辑、回显、方向键历史）。
- 沙箱会话：`src/sandbox/pty.py`（SandboxPty）+ win-sandbox `ProcessLauncherImpl`，子进程 stdio 为 `CreatePipe` 匿名管道，**无终端语义**：
  - 打字无回显（cmd 只对 console 输入回显；本地管道实验证实管道输入不回显）
  - 方向键失效（`\x1b[A` 原样进入管道，cmd 无行编辑）
  - cmd 把读到的命令行再次输出（沙箱环境下实测 `write_stdin("s\n")` 后输出流出现 `s\n`），与前端 lineMode 本地回显叠加成"双重回显"
- 前端为补偿以上缺陷启用了 lineMode（仅 `ptyType === 'win-sandbox'`），已修方向键剥离、粘贴并入输入行，但本质是"前端模拟行编辑"，漏洞与返工风险持续存在（焦点序列、粘贴拼接、双回显等均已先后踩坑）。

### 1.2 根本原因

沙箱子进程缺一个**伪终端**。ConPTY（Windows 10 1809+ 原生伪终端）正是给"非交互启动的 console 程序"提供终端语义的官方机制，普通 pty 后端已验证成熟。**给沙箱子进程套上 ConPTY，即与普通 pty 体验对齐，前端 lineMode 补丁可整体撤销。**

## 2. 目标与非目标

### 2.1 目标

1. 沙箱 cmd 以及所有 console 程序获得完整终端语义：输入回显、方向键/历史、粘贴、Ctrl+C、resize。
2. 无 GUI 窗口弹出（顺带根治此前 interactive 模式弹窗问题，SW_HIDE 补偿可移除）。
3. 数据流/协议尽量复用现有管道管线（输出仍走 ProcessOutput 事件流、输入仍走 WriteStdin），daemon 与前端改动最小。
4. 仅 interactive（REPL/长跑）会话启用 ConPTY；一次性 stdin 场景（stdin_data）保留原管道路径。

### 2.2 非目标

- 不在沙箱内重实现终端模拟（如自绘行编辑/屏幕缓冲），那等于再造一个 ConPTY。
- 不改变沙箱隔离语义（Job Object / AppContainer / 文件过滤器照旧）。

## 3. 技术路线对比

| 方案 | 说明 | 结论 |
|---|---|---|
| **A. 沙箱内创建 ConPTY** | 子进程以 ConPTY 为控制台启动；沙箱读写 ConPTY 的输入/输出管道，经现有 IPC 转发 | **采用**。官方机制、与普通 pty 同款语义、无窗口、输出仍为管道句柄可复用现有 StreamReader |
| B. CreateConsoleScreenBuffer 自绘 | 用 console 屏幕缓冲 + 手动解析 VT/重绘，自建 mini-ConPTY | 不采用。工作量大、语义难对齐（输入法/编码/全屏） |
| C. 保持管道 + 前端增强 | 继续在 web 端补丁（本次已修方向键/粘贴/回显抑制） | 不采用。打补丁不可穷尽（焦点、IME、同步问题），且与普通 pty 的两套行为长期并存 |

## 4. 方案设计（ConPTY）

### 4.1 总体数据流

```
web(xterm) ←ws→ daemon ←IPC(命名管道)← sandbox.exe ←→ ConPTY ←→ cmd.exe
                               │                            │
                    现有 ProcessOutput 事件流          ConPTY 输出管道（读端→沙箱）
                    现有 WriteStdin 命令              ConPTY 输入管道（写端→沙箱）
                    新增 Resize 命令                  ResizePseudoConsole
```

### 4.2 win-sandbox 侧改动

#### 4.2.1 Starter 进程（infra 层）

新增 ConPTY 启动分支（建议新组件 `ConPtyLauncher`，或扩展 `ProcessLauncherImpl`）：

1. 创建两对匿名管道：
   - 输入管道：沙箱持有写端（供 WriteStdin）；读端传给 ConPTY
   - 输出管道：ConPTY 持有写端；读端由沙箱持有（供现有 StreamReader 读取）
2. `CreatePseudoConsole(size, inputRead, outputWrite, 0, &hpc)`：
   - 输入：`COORD{cols, rows}`（初值 80x24，随后由 resize 命令调整）
   - 继承：ConPTY 句柄须可继承（子进程经 `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` 获取）
3. 子进程启动：
   - `CreateProcessW/AsUserW` + `EXTENDED_STARTUPINFO_PRESENT` + `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`（首属性）+ 现有 Job/AppContainer 属性链（`PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES`）追加
   - **不设 STARTF_USESTDHANDLES**（子进程 stdio 由 ConPTY 提供）
   - 保留 `CREATE_NEW_PROCESS_GROUP`（CTRL_BREAK 定向投递机制不变）
4. 关闭：`ClosePseudoConsole(hpc)`（进程退出/会话关闭时，与现有 wait 线程联动）
5. `resize(cols, rows)` → `ResizePseudoConsole(hpc, COORD{cols, rows})`

#### 4.2.2 UseCase（core 层）

- `StartProcessUseCase`：`req.interactive == true` 时走 ConPTY 分支；`stdin_data` 一次性输入场景仍走管道分支（或 ConPTY 分支下 WriteStdin 仍在进程启动后写，无需区分——交实现时以最小差异为准，建议统一 ConPTY，stdin_data 写入 WriteStdin 即可）。
- 新增 `ResizeUseCase`：`resize(process_id, cols, rows)`，查找 usecase 后调用 launcher 的 resize。

#### 4.2.3 IPC 协议

- `StartProcess` payload 新增可选字段：`terminal_size: {cols, rows}`（缺省 80x24）。
- 新命令 `resize`：`{process_id, cols, rows}`。
- 输出/输入复用现有 `ProcessOutput` / `WriteStdin`（句柄来源变成 ConPTY 管道，消息格式不变）。

#### 4.2.4 信号

- Ctrl+C：ConPTY 无控制台窗口，`GenerateConsoleCtrlEvent(CTRL_C_EVENT)` 行为需验证；首选注入方案：WriteInput 写入 `\x03`（ConPTY 会当作 Ctrl+C 递给子进程组）。验证失败则仍按现有 CtrlBreak 语义（沙箱已声明只支持 CtrlBreak + Kill）。
- CtrlBreak：现有 `GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, procGroupPid)` 机制不变（T3.4）。

#### 4.2.5 输出编码

ConPTY 输出为 **UTF-8**（不带 VT 模拟时按字节透传）。daemon decode 用 utf-8（与原生 ConPTY 一致），不再用 gbk。

### 4.3 PTY-Agent daemon 侧改动

1. `src/sandbox/manager.py`：`start_process` 支持传 `terminal_size`；新增 `resize_process(pid, cols, rows)`（发 resize 命令）。
2. `src/sandbox/pty.py`（SandboxPty）：
   - `spawn` 时把 `cols/rows` 透传（现已有，只是 win-sandbox 没用上）
   - `resize()` 从 no-op 改为调用 manager 的 resize 命令
   - `decode`：会话 encoding 逻辑对齐 ConPTY（utf-8）
3. `src/session/*`：沙箱会话的 ptyType 标记（见 4.4）。

### 4.4 前端改动

1. **移除沙箱 lineMode**：`setLineMode` 不再为 win-sandbox 启用 lineMode（或按新 ptyType 判定）。
2. 清理 lineMode 遗留补丁：`handleLineModeInput` 方向键剥离、`doPaste` 的 lineMode 分支（回到原生 bracketed paste 透传——ConPTY cmd 支持粘贴，与普通 pty 一致）。
3. **会话标记**：沙箱会话 ptyType 建议改为 `win-sandbox-conpty`（或新增 `hasTerminal` 标志）以区分"有真终端"与"管道沙箱"，避免前端仍按 win-sandbox 走旧逻辑；引用点需更新（`setLineMode`、mouseMode/1004 解析等通用逻辑不受影响）。
4. 键盘/粘贴/焦点/鼠标全部回到与普通 pty 一致的透传路径；resize 走现有 onResize → wsSend 流程（无需前端改动，daemon 转发即可）。

### 4.5 回退与兼容

- `interactive=false` / `stdin_data` 一次性管道模式保持原样（不影响现有 e2e/ctest）。
- ConPTY 创建失败时：记录 error 并走原管道分支（**仅限 ConPTY API 不可用场景**，如低于 1809 系统；不把管道分支作为常规降级路径）。

## 5. 关键风险与验证点

| # | 风险 | 处置 |
|---|---|---|
| 1 | **ConPTY 在沙箱进程（standard_user / AppContainer）中的可用性**——CreatePseudoConsole 与子进程继承、Job/AppContainer 属性链共存 | 首个 PoC 任务先行验证：在当前 sandbox.exe 模式下创建 ConPTY + 启动 cmd + 读写各 5 次；失败则检查 token/句柄继承细节（ConPTY 句柄需在所有进程创建属性中正确传递） |
| 2 | CTRL_C / CTRL_BREAK 在 ConPTY 下的投递语义 | T2 单独验证；结论写入类型文档；不可用时维持 CtrlBreak + Kill 语义 |
| 3 | resize 期间输出竞态（ConPTY 重绘输出） | daemon 现有 resize 缓冲机制（方案 G）若适用则直接复用；不适用时在沙箱 ConPTY 分支增加过滤（尺寸突变时丢弃旧尺寸 partial repaint） |
| 4 | 进程退出与 ConPTY 关闭时序（输出管道 EOF 关联） | 沙箱经 ProcessExited 判定退出不变；ClosePseudoConsole 在 usecase 析构/退出后调用 |
| 5 | 输出编码：ConPTY UTF-8 与 GBK 程序（cmd + chcp 936） | ConPTY 统一输出 UTF-8（系统保证），daemon 按 utf-8 decode；与普通 pty 完全一致，不存在 GBK 场景 |

## 6. 实现任务拆分（委托实现）

按依赖顺序（每项含测试）：

- **T1（PoC）**：win-sandbox 最小验证 —— 新 `ConPtyLauncher`：CreatePseudoConsole + 启动 `cmd`（属性链含 PSEUDOCONSOLE）+ 读输出/写输入 + ClosePseudoConsole；确认 standard_user/AppContainer 下可用。**输出：PoC 结论记录到 docs/memory/**。
- **T2（win-sandbox 正式实现）**：StartProcessUseCase 接入 ConPTY 分支（interactive=true）+ `terminal_size` 解析 + resize 命令 + ResizeUseCase + 信号验证（T2 附）；ctest 新增用例（启动/输入回显/输出/关闭/resize）。
- **T3（PTY-Agent daemon）**：manager/pty 透传尺寸 + resize 落地 + decode utf-8；`SandboxPty.resize` 生效；session 层 ptyType 标记更新。
- **T4（前端）**：移除沙箱 lineMode（setLineMode / handleLineModeInput / doPaste lineMode 分支按新类型清理）；新 ptyType 引用点更新。
- **T5（回归）**：win-sandbox ctest 全量 + PTY-Agent 单测/e2e（sandbox 组）+ web 手动清单（见下）。

## 7. 验收标准（web 手动清单）

在 web 端新建沙箱会话并按序验证：

1. 打字可见回显，无双重回显
2. 方向键 ↑/↓ 历史、←/→ 行内移动、Backspace 删除
3. 粘贴（Ctrl+V / 右键 / touch 端）：单行无拼接，中文无乱码；多行逐行执行
4. `Ctrl+C` 中断（如 `ping` 长命令）；`CtrlBreak` 语义按 T2 结论
5. 窗口 resize 后布局正确、无输出错位
6. 全屏程序（如 `more`、`clip`）行为正常
7. 无 GUI 窗口弹出（含 interactive 模式）
8. 普通 pty 会话行为不变（回归）

## 8. 受影响文件清单（预期）

**win-sandbox**
- `src/infra/process/ProcessLauncherImpl.cpp`（或新增 `ConPtyLauncher.*`）
- `src/core/usecases/StartProcessUseCase.cpp`、`StartProcessUseCase.h`（请求字段）
- 新增 `src/core/usecases/ResizeUseCase.*`
- `src/main.cpp`（resize 命令分发）、`src/adapters/StartProcessPayloadParser.cpp`（terminal_size）
- `src/core/process/` 相关接口（StartProcessRequest / launcher 抽象）
- 测试：`tests/`（启动/resize/关闭用例）

**PTY-Agent**
- `src/sandbox/manager.py`（terminal_size / resize 命令）
- `src/sandbox/pty.py`（resize 落地、decode）
- `src/web/static/js/infrastructure/terminal/input.js`（setLineMode / lineMode 分支清理）
- `src/web/static/js/*`（新 ptyType / hasTerminal 引用点）
- 测试：`tests/unit/sandbox/`、`tests/e2e/`