# wezterm-py 全量终端能力暴露与落地计划

> 目标：让 `leaf`、`ptyagent` 等应用借助 wezterm 已经写好的终端引擎「开箱即用」实现完整终端功能，避免各自重复实现增量渲染、逻辑行 reflow、鼠标模式检测、控制台输入采集、文本选择/剪贴板。
>
> 约束：**不改任何 vendored wezterm crate**（`wezterm-py/wezterm/*`）。本计划只新增/修改 pywezterm 的绑定层（`wezterm-py/wezterm/pywezterm/src/*`），把 wezterm-term / portable-pty / window 已有的能力暴露到 Python。除非某能力真缺且必须，才评估新增 crate（届时单独对齐）。

---

## 1. 现状与缺口对照

### 1.1 wezterm-term 能力 vs 暴露面

`pywezterm.Terminal` 现仅暴露约 17 个方法（feed/resize/cursor/snapshot/scrollback/scrollback_count/clear_scrollback/reset/text/key_down/key_up/mouse/drain_written/...）。

底层 `TerminalState` 已就绪但**未暴露**的能力：

| 能力 | TerminalState 位置 | 用处 |
|---|---|---|
| `is_mouse_grabbed()` | `terminalstate/mod.rs` | 结束「正则猜 DECSET 鼠标模式」 |
| `get_keyboard_encoding()` | 同上 | 当前键盘协议（Ctrl+U/kitty/win32/...） |
| `is_alt_screen_active()` | 同上 | 备用屏/滚轮代滚判定 |
| `bracketed_paste_enabled()` | 同上 | 判断粘贴是否需要包裹 |
| `focus_changed(bool)` | 同上 | 焦点报告（DECSET 1004） |
| `get_title()` / `get_progress()` | 同上 | 标题/进度 |
| `get_current_dir()` | 同上 | OSC 7 工作目录追踪 |
| `get_semantic_zones()` | 同上 | 超链接/词/行语义区 |
| `set_clipboard/set_download/set_device_control/set_notification` | 同上 | 剪贴板/下载/设备控制回调 |
| `send_paste(text)` | 同上 | 模式感知粘贴下发 |
| `make_all_lines_dirty()` | 同上 | 强制刷新 |

### 1.2 screen crate 能力 vs 暴露面

`Screen` 完全未绑定。已就绪的关键原语：

| 能力 | screen.rs | 用处 |
|---|---|---|
| `get_changed_stable_rows(seqno)` | 增量脏行 | 替代 leaf/ptagent 手写 diff（build_diff） |
| `for_each_logical_line_*` | reflow 后逻辑行 | 替代 leaf 手写 logical_lines |
| `scrollback_or_visible_range()` | 混合历史区读 | 快照/回滚输出 |
| `lines_in_phys_range()` | 读某段物理行 | 当前 snapshot 来源 |

### 1.3 leaf 手写相对照

| leaf 手写场景 | 文件 | 可下沉能力 |
|---|---|---|
| 增量渲染 build_diff + 行签名对比 | `usecases/frame.py` | `Screen.get_changed_stable_rows` |
| 格网→ANSI 拼接 cell_line | `domain/ansi.py` | 绑定侧输出缓冲（仍可为纯 Python，但数据源换脏行） |
| 逻辑行重组 logical_lines | `domain/layout.py` | `Screen.for_each_logical_line_*` |
| 读 Windows 控制台输入 console.py + win32_input.py | `drivers/console.py` `adapters/win32_input.py` | `window` crate 输入采集绑定 |
| 滚轮宿主代滚 / 备用屏判定 | `usecases/input.py` | `is_alt_screen_active`、`is_mouse_grabbed` |

### 1.4 ptyagent 前端对照

`mouseMode.js` 用正则 `/\x1b\[\?(\d+...)[hl]/g` 解析输出流猜鼠标模式（脆弱、滞后）。可改为从 daemon/session 直读 `Terminal.is_mouse_grabbed()` + `get_keyboard_encoding()` + `bracketed_paste_enabled()`。

---

## 2. 验收基准

- **主基准：leaf 无感替换。** 每个阶段完成后，leaf 换用新绑定替换对应手写模块，功能不回退，跑通 `leaf` 的 e2e 测试（`python -m pytest tests/`）+ `scripts/live_selfcheck.py`。
- 附属基准：ptyagent 前端从「猜测模式」切到「直读状态」，鼠标/键盘行为与替换前一致。

---

## 3. 分阶段实施

每个阶段：**先加绑定 → 构建 → 单测 → 替换 leaf/ptagent 引用 → 验收**。

### 阶段 1 — 终端状态查询下沉（先解鼠标靠猜）

- **绑定改动**（`wezterm-py/wezterm/pywezterm/src/term.rs`）：
  - 新增 `PyTerminal.is_mouse_grabbed() -> bool`
  - `get_keyboard_encoding() -> str`
  - `is_alt_screen_active() -> bool`
  - `bracketed_paste_enabled() -> bool`
  - `focus_changed(focused: bool)`
  - `get_title() -> str`
  - `get_current_dir() -> Option<str>`
  - `get_progress() -> Optional[tuple]`
  - `send_paste(text: str)`
  - `get_semantic_zones() -> List[dict]`（name / row range / data）
- **leaf 替换**：`input.py` 滚轮代滚判定改用 `is_mouse_grabbed` / `is_alt_screen_active`。
- **ptyagent 替换**：`mouseMode.js` 增加从 session 数据读模式，`setAppMouseMode` 数据源改为直读。
- **验收**：leaf e2e 通过；前端鼠标模式与正则解析一致。

> ✅ 已实施（见「实施记录：阶段1 + 统一 vendored」）。注意阶段1实际还前置补齐了
> `scroll / scroll_to_bottom / snapshot_lines`（用于统一两套 pywezterm），并把
> `snapshot()/text()` 改为计入视图滚动偏移；`get_semantic_zones` 返回值采用
> `(start_y, start_x, end_y, end_x, semantic_type)` 元组而非 dict（见实施记录）。

### 阶段 2 — 渲染原语下沉（leaf 最痛，增量差分 + 逻辑行）

- **绑定改动**（原计划独立 `Screen` 类；实际直接加在 `PyTerminal`，方法名贴合消费）：
  - `current_seqno() -> int`：当前序列号（增量差分基线）
  - `changed_stable_rows(since_seqno) -> List[int]`：自基线以来脏的稳定行
  - `logical_lines() -> List[(first_stable, last_stable, cells)]`：可见区逻辑行
    （`for_each_logical_line_in_stable_range` 重组，带超长逻辑行防护）
- **leaf 替换**：`frame.py` build_diff 改用引擎逻辑行 + `changed_stable_rows` 脏行差分，
  替代逐行签名对比；删除 `domain/layout.logical_lines` 手写 wrap 拼接（死代码）。
- **验收**：leaf e2e + selfcheck + 手动运行；增量渲染行为与替换前逐帧一致。

> ✅ 已实施（见「实施记录：阶段2」）。

### 阶段 3 — Windows 控制台输入采集下沉

- **绑定改动**（新增 `wezterm-py/wezterm/pywezterm/src/console_input.rs`）：封装 `window` 的 Windows 控制台输入与模式设置，向 Python 返回归一化事件（Key/Mouse/Resize），并暴露 console 模式设置/恢复。
- **leaf 替换**：删除 `drivers/console.py` + `adapters/win32_input.py`，改调绑定。
- **验收**：leaf 键盘/鼠标/尺寸事件与替换前一致。

### 阶段 4 — 文本选择 / 复制 / 粘贴

- **绑定改动**：基于 `get_semantic_zones` + 光标实现选区（word/line 区选、取选中纯文本）；暴露 `send_paste`、`set_clipboard` 回调链路。
- **leaf/ptagent 替换**：文本选择与快捷复制。
- **验收**：选择、复制、粘贴与既有行为一致。

---

## 4. 工程注意事项

- **构建**：改完绑定后在 `wezterm-py/` 下执行 `.\\BUILD.ps1`（maturin 构建 + 复制 wheel 到 `bin/pywezterm`）。src 侧经 `sys.path` 加载 vendored 产物，无需 pip 安装。
- **EABI / Python3.8**：绑定用 pyo3/abi3，新增方法无动态类型，保持 Python 3.8 兼容。
- **不需要改上游**：阶段 1–4 全部能力在现有 vendored crate 中已存在；若阶段 4（selection）发现 term crate 确缺选区 API，才新增对自有算法的实现，仍不改 vendored。
- **测试**：每个绑定方法配 `wezterm-py/tests/` 单测；leaf/ptagent 替换后跑各自 e2e。

---

## 实施记录

### 阶段1 + 统一 vendored（已完成）

**背景**：替换产物时发现 `leaf` 依赖的 pywezterm（`%APPDATA%...site-packages`）与
PTY-Agent 的 vendored（`bin/pywezterm`）是**两套分叉实现**——snapshot 结构、scroll
能力、鼠标模式能力均不同。经与用户确认：**删除 site-packages，以 vendored 为唯一真源**，
leaf 与 ptyagent 共源。

**改动清单**：

1. `pywezterm/src/term.rs`：
   - 新增状态查询：`is_mouse_grabbed` / `get_keyboard_encoding` /
     `is_alt_screen_active` / `bracketed_paste_enabled` / `focus_changed` /
     `get_title` / `get_current_dir` / `get_progress` / `get_semantic_zones` /
     `send_paste`。
   - 新增视图滚动原语（统一 vendored 前置补齐）：`scroll(delta)` / `scroll_to_bottom()` /
     `snapshot_lines()`（返回 `(wrapped, cells)`）；`snapshot()/text()` 改为计入视图
     `view_offset`，供 leaf 滚动查看历史。
   - `pywezterm/Cargo.toml` 增加 `termwiz` 依赖（`KeyboardEncoding`）。
2. `leaf/leaf/drivers/pane.py`：`import pywezterm` 前注入 vendored 路径
   （优先 `PYWEZTERM_DIR` 环境变量，否则向上遍历找 `PTY-Agent/bin/pywezterm`）；
   `snapshot()` 改用 `snapshot_lines()`。
3. `leaf/leaf/usecases/input.py`：滚轮路由改用 `pane.is_mouse_grabbed()`
   （应用接管则转发，否则宿主代滚）。
4. `leaf/leaf/usecases/ports.py` 与测试 `_FakePane`：补充
   `is_mouse_grabbed` / `is_alt_screen_active` / `scroll` 协议与存根，新增滚轮路由单测。
5. 删除 `%APPDATA%...\site-packages\pywezterm`；`wezterm-py/tests/conftest.py` 统一注入
   vendored bin 路径。
6. 新增 `wezterm-py/tests/test_stage1_state.py`（12 项状态查询 + 视图滚动）。

**验收**：
- leaf 全量测试 41 通过（真实 ConPTY e2e 在内）。
- ptyagent `tests/unit/session|input` 相关 32 通过；wezterm-py 测试 22 通过。
- daemon（`python -m src.daemon`）基于最新 vendored pyd 重启。

**注意事项**：
- `get_semantic_zones` 返回 `(start_y, start_x, end_y, end_x, semantic_type)` 元组，
  semantic_type ∈ prompt/input/output（计划中写的 dict 形式未采纳，Python 侧消费更直接）。
- `snapshot()`（纯 cells 结构，供 ptyagent）保持向后兼容；leaf 用 `snapshot_lines()`。
- 本轮为消除两套分叉，把原属阶段2的 `scroll/snapshot_lines` 前置补齐。

### 阶段2（已完成）

**绑定改动**（直接加在 `PyTerminal`，未用独立 Screen 类）：
- `current_seqno()` / `changed_stable_rows(since_seqno)` / `logical_lines()`。
- `logical_lines` 经 `for_each_logical_line_in_stable_range` 重组，带超长逻辑行防护。

**leaf 改动**：
- `frame.py build_diff` 改用 `pane.logical_lines()`（引擎逻辑行）+ `changed_stable_rows`
  脏行差分，替代逐行签名对比；Pane 增 `current_seqno/changed_stable_rows/logical_lines`
  透传 + `last_seqno` 基线。
- 删除 `domain/layout.logical_lines` 手写 wrap 拼接（死代码）。
- `live_selfcheck.py` 改为自动加载 vendored 引擎（不依赖 site-packages）。

**验收**：
- leaf 全量 41 通过；live_selfcheck 全部 PASS（含窄化/调大无裂行、无三连重复）。
- wezterm-py 27 通过（含新 `test_stage2_render.py` 5 项）；ptyagent 终端相关 32 通过。
- 已启动真实 leaf 窗口供手动验收渲染/滚动。

### 阶段3（已完成）：Windows 控制台输入采集下沉

**背景**：leaf 原来用纯 ctypes 实现控制台输入（`drivers/console.py` + `adapters/win32_input.py`），
与 Mux/绑定层并存两套 Win32 代码。

**改动清单**：

1. 新增 `pywezterm/src/console_input.rs`（`#[cfg(windows)]`，类名 `ConsoleInput`）：
   - 构造时保存控制台原始模式并设置（输出 VT 处理/禁换行自动回车；输入原始按键事件
     + 忽略快速编辑），`restore()`/Drop 恢复；
   - `wait_input(ms)`（WaitForSingleObject）/ `read_inputs()`（GetNumberOfConsoleInputEvents
     + ReadConsoleInputW 批量读 → 归一化 tuple）/ `size()`（GetConsoleScreenBufferInfo）；
   - 归一化语义与 leaf 既有 ctypes 实现逐条对齐：键名（Backspace/Tab/.../F1-F24）、
     Unicode 字符、Ctrl+字母控制码回映射、修饰键自身忽略、keyup 保留 down=False；
     鼠标 press/move/release + wheel_up/down、双击简化为 press、**抬键 last_pressed
     补全**（Windows 抬键事件不带按钮号，wezterm 编码 release 必须带按钮）；
   - 事件返回平铺 tuple：`("key", key, mods, down)` / `("mouse", x, y, kind, button, mods)`
     / `("resize",)`（曾误写成嵌套单元素 tuple，leaf 侧全部误判为 resize）；
   - 归一化核心是纯函数 + Rust 单测 14 项（`cargo test -p pywezterm`）；
   - `Cargo.toml` winapi features 增加 consoleapi/wincon/synchapi/winuser（无新 crate）；
   - 未引入 window crate（vendored 无此 crate），用 winapi 直接实现，参考 termwiz
     `terminal/windows.rs` 的成熟做法。
2. leaf 侧：
   - 删除 `adapters/win32_input.py` + `tests/test_win32_input.py`（归一化下沉绑定）；
   - `drivers/console.py` 重写为 `pywezterm.ConsoleInput` 门面（`_to_domain` 做
     归一化 tuple → 领域事件薄映射）；
   - 引擎路径注入抽到 `drivers/_engine.py`（pane.py 与 console.py 共用，消除重复）。
3. 测试：
   - Rust：`console_input.rs` 14 项单测（键/鼠标归一化各分支）；
   - Python：`leaf/tests/test_console.py`（5 项 tuple→领域事件映射）；
   - `wezterm-py/tests/test_console_input.py`（4 项，无交互控制台时自动 skip）。

**连带修复（Mux 状态栏宽度 bug，调试中发现的既有缺陷）**：
- 现象：leaf 首帧渲染后 pane 内容区空白、仅状态栏可见；渲染线程持续输出
  （每帧光标序列）但 mux.render() 增量恒为空。
- 根因：`draw_status_text` 按**字符数**截断/补白，状态栏含 CJK 双宽字符时
  总宽度超列数（如 155 字符宽 166）；`Surface::print_text` 超宽时按终端语义
  触发 `scroll_screen_up`，把整屏格子顶上消失 → pane 内容被清。
- 修复：按**显示宽度**（WcLookupTable，wezterm-char-props）截断/补白，
  `pywezterm/Cargo.toml` 增加 wezterm-char-props.workspace 依赖。

**验收**：
- cargo test -p pywezterm 14 通过；wezterm-py 49 通过（4 skip = ConsoleInput 环境跳过）；
- leaf 21 通过（含真实 ConPTY e2e）；
- live_selfcheck：渲染类检查（启动画面/稳定帧/窄化/调大）PASS；
  `dir` 输入类检查在自动化 ConPTY 宿主环境 FAIL——**OpenConsole 的已知限制**：
  渲染线程持续写输出（每帧光标序列）时，conhost 的输入管道处理被饿死，
  WaitForSingleObject 不信号、ReadConsoleInputW 读不到写入的字节。与输入采集
  实现无关（ctypes 版同样失败），leaf 的交互输入须在真实终端运行（同 M3 记录）。

**连带修复（阶段3 验收中发现的渲染坐标 bug）**：
- 现象：增量渲染的 CUP 定位行列颠倒——`\x1b[1;4H`（第 1 行第 4 列）实为本意
  `\x1b[4;1H`（第 4 行第 1 列）；多行内容全部堆到第 1 行，行首残留旧内容碎片
  （如 "CHE"）；首帧后 pane 内容错位。
- 根因：wezterm-surface 的 `Change::CursorPosition` 语义是 (x=列, y=行)，但
  termwiz `TerminfoRenderer` 渲染绝对定位时把 x 当行、y 当列（
  `move_cursor_absolute` 的 line=x/col=y），两者相反；`Relative`/`EndRelative`
  分支语义正确（独立的行/列相对移动）。
- 修复：`surface_render.rs::render_changes_bytes` 在渲染前把 CursorPosition 的
  **Absolute** x/y 互换（Relative/EndRelative 不参与，避免破坏相对移动语义）。
- 同时 `ConsoleInput` 构造时把宿主控制台输出代码页切到 UTF-8（保存原代码页，
  restore 恢复），使渲染字节（UTF-8）在宿主 ConPTY 中正确解析。

**待办（阶段4）**：文本选择/复制/粘贴（get_semantic_zones + 光标选区、send_paste/set_clipboard）。

### 阶段2 修订：滚动错位修复（手动验收发现）

**现象**：真实使用 leaf 时，滚动视图后画面错位/内容重复残留；拖动分隔线强制刷新后即正常，再滚动又错位。

**根因**：滚动只修改视图偏移（view_offset），不产生新 seqno，也不改变逻辑行内容。若某逻辑行滚动后其 stable 区间与上帧该 y 恰好一致/部分匹配，增量 diff 判定"未变"而跳过重绘，导致旧位置残留 + 新滚动内容叠加 → 错位/重复。

**修复**（`leaf/usecases/frame.py`）：
- `build_diff` 新增 `prev_view_top` 参数与 `view_top` 返回值（视口首逻辑行的
  first_stable 作为锚点），并在调用间传递（`render_loop` 维护 `last_view_top`）。
- 当本次 `view_top` 与上帧不同（视图滚动/平移）→ `prev_rows = None` 强制全量重绘，
  与"拖动分隔线强制刷新"同样作用，杜绝滚动后错位。

**验收**：
- leaf 42 通过（新增 `test_build_diff_view_scroll_forces_full_repaint` 回归测试）。
- live_selfcheck 全部 PASS。
- 滚动复现脚本验证：滚动 5 行后整屏正确重绘（L18-L36），无残留重复。