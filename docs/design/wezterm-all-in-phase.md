# Phase 文档：wezterm 库化（wezterm-py）+ PTY-Agent all-in 改造

## 1. 文档信息

- 类型：技术设计 + 分阶段实施计划（TDD / Phase Plan）
- 范围：wezterm 库化（独立 Python 库 `wezterm-py`）+ PTY-Agent 全面切换到该库
- 状态：已调研，待分 Phase 实施
- 目标读者：开发团队

## 2. 背景与目标

### 2.1 现状问题

PTY-Agent 当前的内置 pty 层（`src/pty/`）与终端模型层（`src/terminal/`）存在大量兼容性问题：

- `src/pty/windows/conpty.py`：ctypes 直调 `kernel32.CreatePseudoConsole`，并依赖 `AttachConsole + WriteConsoleInputW` 注入鼠标/键盘（tcell 双击、VT_INPUT 模式等），与系统 conhost 行为强耦合，Win10 22H2 下 conhost 会丢弃 CSI/SGR 序列，兼容性差。
- `src/pty/windows/condrv.py`：ConDrv 直连，为项目特有优化。
- `src/terminal/screen.py`：基于 pyte（纯 Python VT102 模拟器），无法正确解析现代 TUI 的 24-bit 色、kitty 协议、复杂光标/alt-screen，导致 grep 定位、scrollback、光标查询错位。

### 2.2 目标

1. 把 wezterm 库化为一个独立的、通用的 Python 库 `wezterm-py`，任意 Python 程序可 `pip install` 后调用（pty 引擎 + 终端模拟器 + 输入编码）。
2. PTY-Agent 作为 `wezterm-py` 的一个使用者，全面切换到该库：
   - 丢弃 ctypes ConPTY / ConDrv / AttachConsole 注入 / VT_INPUT 探测 / pyte。
   - pty 后端、终端模型、输入编码全部由 wezterm 提供。
3. 保留 PTY-Agent 特有能力：Job 进程树追踪、win-sandbox 沙箱（AppContainer 隔离）、GUI 窗口检测。

### 2.3 非目标（Out of Scope）

- 浏览器侧渲染（xterm.js）不变：用户看到的画面仍由 xterm.js 解析渲染，本次不改。
- 不引入 wezterm-mux-server 独立进程方案（不改架构为守护进程托管）。
- 不保留任何 ctypes ConPTY 兼容层（全部删除，不留回退接口）。

## 3. 调研结论

### 3.1 wezterm 能力盘点

| 组件 | 位置 | 能力 | 状态 |
|---|---|---|---|
| `portable_pty`（pty crate） | `wezterm-main/pty/` | `openpty`（CreatePseudoConsole + 双管道）、`spawn_command`（CreateProcessW + HPCON）、MasterPty（read/write/resize/get_size）、Child（wait/kill/pid/句柄） | 可直接绑定 |
| `wezterm-term`（term crate） | `wezterm-main/term/` | 完整终端模拟器：`Terminal::advance_bytes` 喂字节、`screen()`/`visible_lines()` 读网格、`cursor_pos()`、`scrollback_rows()`；**模式感知输入编码**（应用光标模式/kitty/CSI-u/win32） | 可直接绑定 |
| `wezterm-input-types` | `wezterm-main/wezterm-input-types/` | `KeyEvent`/`KeyCode`/`Modifiers`/`MouseEvent` 类型 | 依赖 |
| `wezterm-surface`/`cell`/`escape-parser`/`bidi` | 各 crate | Screen/Line/Cell 数据模型，纯 Rust | 依赖 |

### 3.2 关键技术验证结论

1. **wezterm-term 可独立编译为库**：`term/Cargo.toml` 依赖全为纯 Rust（无 GUI/config/window），可独立编译成 cdylib。
2. **`TerminalConfiguration` 只有 `color_palette()` 是必需方法**，其余全有默认值，绑定成本低。
3. **终端模型 API 齐全**：`Terminal::new(size, config, term_program, term_version, writer)`；`advance_bytes()` 喂字节；`state.screen().visible_lines()` → `Line.visible_cells()` 读每格字符与属性；`state.cursor_pos()` 返回 `(x, y, shape, visibility)`；`screen.scrollback_rows()` + `scrollback_or_visible_range()` 读历史。
4. **输入编码是模式感知的**：`TerminalState::key_down(key, mods)` / `key_up` / `mouse_event(event)` 会根据终端当前状态（`application_cursor_keys`、`newline_mode`、kitty/win32 编码）编码，并把字节写入构造时传入的 `writer`。这是"vim 按键顺"的核心来源。
5. **wezterm-pty 的 openpty 不 spawn 子进程**：spawn 是独立的 `SlavePty::spawn_command`。`PsuedoCon { con: HPCON }` 持有 HPCON（当前私有，需加 getter 暴露）。
6. **wezterm 全仓不使用 Windows Job 对象**：无 `CreateJobObject/AssignProcessToJobObject`，与 PTY-Agent 的 Job 追踪无冲突。
7. **`WinChild` 提供 `process_id()` 与 `as_raw_handle()`**：PTY-Agent 可在 wezterm spawn 后把子进程登记进自己的 Job 对象。
8. **工具链就绪**：cargo/rustc 1.97.0（stable-x86_64-pc-windows-msvc）位于 `~/.cargo/bin`（需激活 PATH）；maturin 1.14.1；Python 3.11.9。
9. **workspace 结构**：`pty`/`term`/`termwiz`/`config` 等通过 path 依赖自动成为 workspace 成员，新增绑定 crate 可直接加入。
10. **wezterm 用 `PSEUDOCONSOLE_WIN32_INPUT_MODE` 创建伪控制台**：是其鼠标/键盘输入路径能工作的基础，绑定后沿用。

### 3.3 影响面评估

| 现有组件 | 影响 | 说明 |
|---|---|---|
| Job 进程树追踪（`src/process/windows/job_tracker.py`） | **基本不受影响** | wezterm 不用 Job；`register_root(pid, hprocess)` 在 wezterm spawn 后照旧可用（`WinChild.as_raw_handle()`）。唯一隐患：wezterm spawn（一次 FFI 往返）到 Python register 之间子进程可能派生孙进程逃出 Job，需在库测试中验证、必要时加缓解 |
| win-sandbox 沙箱（`src/sandbox/`） | **受影响最大** | 现机制是 Python 自建 HPCON 传 win_sandbox；wezterm 不接受外部 HPCON。方案：**wezterm 暴露其 HPCON**，win_sandbox 用 wezterm 的 HPCON 启动沙箱进程（已定方案 A） |
| GUI 窗口检测（`src/process/gui.py`） | **不受影响** | 进程侧枚举，与 pty 无关 |
| 浏览器渲染（web 前端 xterm.js） | **不受影响** | 不改 |
| 输入注入/VT_INPUT 探测（`src/input/interceptor.py`） | **删除** | 由 wezterm 输入编码 + ConPTY 输入管道替代 |
| pyte/GridScreen（`src/terminal/`） | **删除** | 由 wezterm-term 替代 |

## 4. 总体架构

### 4.1 工程结构

```
wezterm-py/                          ← 独立 Python 库项目（任意程序可调用）
├── pyproject.toml                   ← maturin 构建配置（产出 wheel/.pyd）
├── README.md                        ← 库文档（不涉及 PTY-Agent）
├── wezterm-main/                    ← vendored wezterm 源码（含我们 pty/term 的改动）
│   ├── pty/                         ← 改动：暴露 HPCON
│   └── pywezterm/                   ← 新增：pyo3 绑定 crate（workspace 成员，crate-type=cdylib）
│       ├── src/term_bindings.rs     ← wezterm-term 绑定
│       └── src/pty_bindings.rs      ← wezterm-pty 绑定
└── tests/                           ← 库级自测（纯 Python）
```

PTY-Agent 通过 `pyproject.toml` 的 path 依赖或 pip 安装使用 `wezterm-py`，在其 `src/pty/`、`src/terminal/`、`src/input/` 写适配层。

### 4.2 数据流（改造后）

```
子进程（沙箱内或普通，由 wezterm-pty spawn 或 win_sandbox+wezterm HPCON 启动）
   ↓ 输出 VT 字节            ↑ 输入（wezterm-term 编码后的字节 → ConPTY 输入管道）
wezterm-pty（master 读/写）
   ↓                          ↑
PTY-Agent daemon（reader 线程）
   ├─ 原始字节 → 浏览器 xterm.js（渲染）
   └─ 原始字节 → wezterm-term（服务器端模型）
                  └─ snapshot / cursor / scrollback / grep
```

### 4.3 职责边界

| 层 | 归属 | 内容 |
|---|---|---|
| `wezterm-py` 库 | wezterm-py 项目 | Pty API、Terminal API、输入编码，通用、自测、不识别调用方 |
| PTY-Agent 适配层 | PTY-Agent 项目 | PseudoTerminal/TerminalScreen 接口适配、Job 注册、沙箱 HPCON 接线、GUI 检测、daemon/事件/触发器逻辑 |

## 5. wezterm-py 库 API 设计

> 绑定方式：pyo3 + abi3（跨 CPython 小版本）；模块名 `pywezterm`（导入 `pywezterm`）。

### 5.1 Pty 模块

```
class Pty:
    def __init__(self, cols=80, rows=24, pixel_width=0, pixel_height=0)
        # 内部：openpty 创建伪控制台（不 spawn），保存 master/slave

    def spawn(self, argv: list[str], cwd: str|None=None,
              env: dict[str,str]|None=None, cols=None, rows=None) -> Child
        # 内部：CommandBuilder + slave.spawn_command；返回 Child

    def read(self, n=65536) -> bytes          # master 读
    def write(self, data: bytes)              # master 写
    def drain(self, max_bytes=65536) -> bytes # 非阻塞排空
    def resize(self, cols, rows)
    def get_size(self) -> (cols, rows)
    def hpcon(self) -> int|None               # 暴露 HPCON（沙箱/外部 spawn 用）
    def close(self)

class Child:
    def pid(self) -> int|None                 # WinChild.process_id()
    def process_handle(self) -> int|None      # WinChild.as_raw_handle()（Job 注册用）
    def try_wait(self) -> int|None            # None=运行中，否则退出码
    def kill(self)
```

### 5.2 Terminal 模块（终端模型）

```
class Terminal:
    def __init__(self, cols=80, rows=24)
        # 内部：Terminal::new + 捕获式 writer（Vec<u8> 缓冲）

    def feed(self, data: bytes)               # advance_bytes
    def resize(self, cols, rows)
    def cursor(self) -> (row, col, visible)   # cursor_pos()（0-based）
    def snapshot(self) -> list[list[Cell]]    # visible_lines() 每格 Cell
    def scrollback(self) -> list[list[Cell]]  # 历史区（可配置行数上限）
    def close()

class Cell:  # 序列化网格单元
    ch: str          # 字符（含宽字符处理）
    fg/bg: str       # 颜色（"default"/十六进制/SGR 索引）
    bold/italic/underline/reverse: bool
```

### 5.3 输入编码（模式感知）

```
class Input:
    def key_down(self, key: KeyCode, mods: int) -> bytes   # 取 writer 缓冲
    def key_up(self, key: KeyCode, mods: int) -> bytes
    def mouse(self, x, y, button, is_release) -> bytes     # SGR/kitty 编码
    # KeyCode 用字符串描述（如 "Up"/"Left"/"F1"/"a"/"Enter"）+ 修饰位
```

说明：编码结果返回给调用方字节，由调用方决定写入路径（PTY-Agent 直接写 pty，无需 AttachConsole 注入）。

## 6. wezterm 源码改动清单（最小）

| 文件 | 改动 |
|---|---|
| `wezterm-main/pty/src/win/psuedocon.rs` | 新增 `pub fn hpcon(&self) -> HPCON`（暴露私有 `con`） |
| `wezterm-main/pty/src/win/conpty.rs` | `ConPtyMasterPty` 暴露 `hpcon()`（经 `Inner.con` 转发） |
| `wezterm-main/pty/src/lib.rs` | `MasterPty` trait 增加 Windows-only 可选方法 `hpcon()`（默认 None，ConPtyMasterPty 覆写）；若需外部 spawn，增加"带外部 HPCON 的 spawn 入口" |
| `wezterm-main/Cargo.toml` + 新增 `wezterm-main/pywezterm/Cargo.toml` | 加入 pyo3 依赖与 workspace 成员 |

## 7. Phase 划分与实现计划

### Phase 0：wezterm-py 工程搭建

- 目标：库工程可构建、可导入。
- 内容：
  1. 激活 Rust PATH（`~/.cargo/bin`）。
  2. 新增 `wezterm-main/pywezterm/`（pyo3 cdylib crate，空壳 + 一个 `hello` 导出函数），加入 workspace。
  3. `wezterm-py/pyproject.toml`（maturin 配置，module-name=`pywezterm`，abi3）。
  4. 用 maturin 构建，验证 `import pywezterm`。
- 验证：`python -c "import pywezterm; print(pywezterm.__version__)"`。
- 退出条件：构建产物 `.pyd` 生成并可导入。

### Phase 1：绑定 wezterm-term（终端模型 + 输入编码）

- 目标：Python 可创建 Terminal、喂字节、读屏幕/光标/scrollback、做模式感知输入编码。
- 内容：
  1. 实现最小 `TerminalConfiguration`（仅 `color_palette()`，其余默认）。
  2. 实现 `Terminal`/`Cell`/`Input` 的 pyo3 封装（writer=Vec 捕获缓冲，提供 `take_written()`）。
  3. 绑定 `key_down/key_up/mouse_event`。
- 验证：库测试——喂一段 VT 输出 → snapshot/光标/scrollback 断言；编码方向键/功能键字节断言。
- 退出条件：库测试全绿。

### Phase 2：绑定 wezterm-pty + HPCON 暴露

- 目标：Python 可创建 Pty、spawn、读写、resize、拿 child pid/句柄、拿 HPCON。
- 内容：
  1. 按 §6 改动 wezterm-pty（暴露 HPCON）。
  2. 实现 `Pty`/`Child` 的 pyo3 封装。
  3. 处理 read 阻塞语义、EOF、close 幂等。
- 验证：库测试——spawn `cmd /c echo hi` → 读到输出 → resize → kill → 退出码。
- 退出条件：库测试全绿；HPCON 值可取出且非空。

### Phase 3：库级自测收尾

- 目标：wezterm-py 独立可用，库自带测试覆盖。
- 内容：补全边界测试（大输出、UTF-8 中文、CJK 宽字符、spawn 失败、close 幂等、HPCON 沙箱场景冒烟）。
- 验证：`pytest tests/` 全绿（不依赖 PTY-Agent）。
- 退出条件：库可独立交付。

### Phase 4：PTY-Agent 终端模型适配

- 目标：`TerminalScreen` 底层从 pyte 切到 `wezterm-py.Terminal`，接口不变，pyte 作回退。
- 内容：
  1. 新增后端选择：`pywezterm` 可用 → 用库；否则回退 pyte（临时兜底，最终删除）。
  2. 对齐接口：`feed`/`snapshot(keep_ansi, include_cursor)`/`capture_scrollback`/`get_cursor_location`/`line_text`/`resize`/`export_buffer`。
  3. 实现 Cell → SGR/纯文本渲染（对齐原 `_render_with_colors` 语义：每行 CUP 定位、保留中间空行、光标序列）。
- 验证：现有 grep/scrollback/光标相关用例回归。
- 退出条件：web 上 grep 定位、scrollback 恢复、光标查询与 pyte 时代行为一致或更准。

### Phase 5：PTY-Agent 输入编码替换 + 移除注入

- 目标：键盘/鼠标编码走 `wezterm-py.Input`，删除 AttachConsole 注入与 VT_INPUT 探测。
- 内容：
  1. `InputInterceptor`/`mouse.py`：编码逻辑替换为库的模式感知编码。
  2. 删除 `conpty.py` 的 `inject_key_events`/`inject_mouse_events`/`inject_vt_bytes`/`is_vt_input_enabled`/`is_tui_mouse_input_enabled`/`get_console_input_mode`/`_attach_conin` 相关机制。
  3. 键盘/鼠标事件 → 编码字节 → 直接写 pty。
- 验证：真实 TUI（vim/htop/tcell 程序）方向键、功能键、鼠标、双击行为验证；与 wezterm 本体行为对比。
- 退出条件：TUI 兼容性不劣于现状且预期更优。

### Phase 6：PTY-Agent pty 后端替换 + Job 注册

- 目标：`WindowsPseudoTerminal`/`ConDrvPseudoTerminal` → wezterm-py Pty 适配。
- 内容：
  1. 新增 `src/pty/windows/wezterm_pty.py`（PseudoTerminal 子类，包 `wezterm-py` Pty）。
  2. `pty_factory.py`：优先级改为 wezterm → （沙箱）→ 原后端（临时兜底后删）。
  3. spawn 后 `tracker.register_root(pid, process_handle)`（用 `Child.process_handle()`）。
  4. 编码/环境变量处理对齐（ConPTY 恒 UTF-8，`PYTHONIOENCODING` 语义保留）。
- 验证：`cmd`/`powershell`/`python`/`vim` 会话创建、读写、resize、kill、退出码；进程树终止验证。
- 退出条件：会话全生命周期正常；进程树 kill 后无残留进程。

### Phase 7：沙箱 HPCON 接入

- 目标：win_sandbox 用 wezterm-py 的 HPCON 启动沙箱进程。
- 内容：
  1. `SandboxPty`：改用 wezterm-py Pty 的 `hpcon()`，不再自建 `ConPtyHandle`。
  2. 沙箱场景下 wezterm 不调用 `spawn_command`（由 win_sandbox 启动）。
  3. 沙箱 Job 通知（`on_job_process_started/exited`）与 tracker 接线不变。
- 验证：沙箱会话创建、命令执行、资源隔离、进程树 kill。
- 退出条件：沙箱功能与现状等价。

### Phase 8：清理 + 全量回归

- 内容：删除 `conpty.py`/`condrv.py`/`conpty_handle.py`/`vt_input.py` 相关、`pyte` 回退、死代码；更新依赖与文档。
- 验证：全量回归（vim、TUI 鼠标、中文编码、大输出、会话生命周期、沙箱、GUI 检测、web 交互）；守护进程重启后 web 全流程。
- 退出条件：无回归；无历史兼容层残留。

## 8. 风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 终端模型行为差异（wezterm-term vs pyte）导致 grep/scrollback/光标结果变化 | 高 | Phase 4 逐项对齐 + 回归；库测试固化行为 |
| 输入编码替换后与现有 TUI 鼠标/键盘行为的共存问题 | 高 | Phase 5 用真实 TUI 程序逐场景验证，与 wezterm 本体行为对比 |
| Job 注册竞态（wezterm spawn 与 Python register 之间孙进程逃逸） | 中 | Phase 6 验证；必要时 wezterm 侧在 spawn 同步返回前回调 pid/handle |
| 沙箱 HPCON 接线的兼容性 | 中 | Phase 7 专项验证；HPCON 在同一 ConPTY 上多客户端挂载属标准用法 |
| pyo3/abi3 与 Python 版本、`crt-static` 的构建兼容 | 中 | Phase 0 先行验证构建链路 |
| 首次编译量大（wezterm 依赖链） | 低 | 一次性成本；构建产物缓存 |

## 9. 验证标准（总体验收）

- `wezterm-py` 可独立安装、独立测试通过。
- PTY-Agent 全量回归通过（会话生命周期、vim、TUI 鼠标、中文编码、大输出、沙箱、GUI 检测、web 交互）。
- 代码中无 ctypes ConPTY / ConDrv / pyte / AttachConsole 注入残留。

## 10. 开放问题

- 库的包名与模块名：暂定 `wezterm-py` / `pywezterm`（需确认）。
- 沙箱 HPCON 具体接线：wezterm 暴露 HPCON 后，是否还需"外部 spawn 入口"或仅沙箱走 win_sandbox 启动（倾向后者，wezterm 不 spawn）。
- 浏览器侧 xterm.js 未来是否也换 wezterm 渲染：本阶段不做，留待后续评估。
