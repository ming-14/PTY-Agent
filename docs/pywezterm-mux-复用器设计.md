# pywezterm Mux 复用器设计（leaf / ptyagent 共用）

> 目标：让 leaf、ptyagent 等应用**完全不自己处理终端**——布局、焦点、滚动、多窗格
> 合成、增量渲染全部交给 wezterm 底层模块，经 pywezterm 统一 API 暴露。
>
> 定位：在 **pywezterm 绑定层**自建复用器编排单元（Mux），基于 wezterm 已写好的
> 底层模块（`wezterm-term` pane 模拟 + `wezterm-surface` 合成渲染 + `portable-pty`
> 子进程），而非硬绑 Rust `mux` crate（其依赖 mlua/Lua/async 等 14+ 重 crate，且
> 最终产物仍是 surface/Change，仍需自行接 conhost 渲染，成本与收益不匹配）。
>
> 约束：不改 vendored wezterm crate；只新增/修改 pywezterm 绑定层。

---

## 1. 能力来源对照

| 能力 | wezterm 底层模块 | 绑定暴露现状 | Mux 需要 |
|---|---|---|---|
| 子进程伪终端 | `portable-pty`（PyPty） | ✅ 已暴露 | 每个 pane 一个 Pty |
| 设备无关终端模拟（VT/光标/scrollback/键盘鼠标编码/逻辑行） | `wezterm-term`（PyTerminal） | ✅ 已暴露 (feed/resize/key/mouse/snapshot_lines/logical_lines/scroll/is_mouse...) | 每个 pane 一个 Terminal |
| **增量渲染**（单元格级 diff → Change 流） | `wezterm-surface::Surface.get_changes(seqno)` | ❌ 未绑定 | **新增绑定** |
| **Change → 终端字节流**（真实终端 ANSI） | `termwiz::render::terminfo::TerminfoRenderer.render_to`(`RenderTty: Write`) | ❌ 未绑定 | **新增绑定** |
| 多窗格布局 / 焦点 / 滚动 | wezterm `mux`（不引入） | — | **绑定层自建编排** |

核心结论：**增量渲染与字节输出**是 wezterm 现成成品（`Surface` + renderer），只需绑定；
**多窗格编排**（布局/焦点/滚动/合成）由 Mux 在绑定层组织上述能力。

---

## 2. Mux 复用器 API 设计（Python 视角）

目标形态：调用方建一个 Mux（对应一个真实终端屏），往里面加 Pane（每 Pane 一个子进程），
设置布局与焦点，每帧调用渲染取增量字节写控制台。

```python
import pywezterm

mux = pywezterm.Mux(port_rule="proxy")          # 可选：子进程创建方式（本地/预留）
p1 = mux.add_pane(["cmd.exe","/d","/k"])        # 默认填满
p2 = mux.add_pane(["cmd.exe","/d","/k"])
mux.split(p1, "right", p2)                      # p2 放 p1 右侧；p1 保留焦点
mux.set_focus(p2)
mux.resize(120, 30)                             # 宿主终端尺寸变化

# —— 事件路由（键盘/鼠标/滚动 由应用采集原始事件，转交 Mux 编码）——
mux.key_down(focus=None, key="a", mods=0)       # 编码到焦点 pane，写其 Pty
mux.mouse(x, y, "press", "left", mods=0)        # 命中 pane → 编码 → 写其 Pty
mux.scroll(focus, delta)                        # 焦点 pane 视图滚动

# —— 渲染（增量）——
data, cursor = mux.render()                     # data: ANSI 字节（仅变化部分），cursor: (row,col,visible)
console.write(data); console.restore_cursor(*cursor)

# —— 关闭 ——
mux.close()
```

### 2.1 `Mux` 类

| 方法 | 签名 | 职责 |
|---|---|---|
| `Mux(cols, rows)` | 构造宿主屏默认尺寸 | 持有一个 Surface（后半填充），N 个 Pane，布局树，焦点 |
| `add_pane(argv, cwd, env) -> Pane` | 新建 pane（Pty+Terminal），默认铺满 | 返回句柄 |
| `split(target, dir, pane)` | dir∈left/right/up/down 把 pane 并排到 target | 更新布局树 |
| `set_focus(pane)` | 焦点切换 | 后续键盘/鼠标走焦点 |
| `focused()` -> Pane | 当前焦点 | |
| `resize(cols, rows)` | 宿主屏尺寸 + 重切所有 pane 尺寸 | 触发表面 resize → 下次全量 |
| `key_down(key, mods)` / `key_up(...)` | 编码到焦点 pane 并写其 Pty | 复用 PyTerminal.key_down |
| `mouse(x,y,kind,button,mods)` | 命中 pane（by 布局矩形）→ 坐标换算 → 编码写 Pty | 未命中/状态栏由应用自理 |
| `scroll(delta)` | 焦点 pane 视图滚动（PyTerminal.scroll） | |
| `render() -> (bytes, cursor)` | 合成各 pane 格子到 Surface → get_changes → render_to → 增量字节 + 光标 | 核心 |
| `close()` | 关闭所有 pane | |

### 2.2 `Pane` 类（pywezterm Mux 内部持有的 pane 句柄）

封装 `Pty + Terminal`，暴露读取/滚动/编码：
`key_down / key_up / mouse / write / resize / scroll / scroll_to_bottom /
logical_lines / snapshot / cursor / is_mouse_grabbed / close`。
（大部分复用现有 PyTerminal 绑定；Mux 内部使用。）

---

## 3. 关键实现点

### 3.1 布局树

用简单嵌套矩形布局（类似 wezterm mux 的 Layout Tree，但仅左右/上下二分，
支持递归）。每个布局叶子 = 一个 pane 的 (x, y, w, h) 矩形。分割时把 target 矩形二分，
新 pane 得其一。Mux 重算所有 pane 的 Terminal 尺寸（`terminal.resize`）。

### 3.2 渲染合成（核心，增量）

1. 对每个 pane：`terminal.logical_lines()` 取可见逻辑行（含 scroll 偏移的 first/last stable）。
2. 逐 pane、逐行，把每个 cell 写到 Mux 的 `Surface` 对应矩形坐标：
   `surface.set_cell(px, py, text, fg, bg, bold, ...)`——格式与现有 snapshot/lines 的
   CellTuple 对齐。
3. Mux 保存上一次 `seqno`；`changes = surface.get_changes(seqno)` 得到**仅变化部分**
   （wezterm 内部做 CursorPosition/属性/文本合并，替换手写 build_diff）。
4. `TerminfoRenderer::render_to(&changes, &mut myBuf)` → 增量 ANSI 字节返回给 Python
   （`RenderTty for Buffer` 只需 `Write` + 尺寸）。
5. 光标：取焦点 pane 光标，映射到整屏坐标。

滚动错位的根由正是在这层：Mux 用 Surface 每次合成后再 diff，视图平移时只对变化的
格子重新 set_cell，Surface 的增量自然只输出变化——不依赖我们手写 `prev_view_top` 类
判定，天然正确。

### 3.3 命中测试与坐标换算（鼠标）

由布局树矩形判定 `(x,y)` 属于哪个 pane，再把整屏坐标转 pane 内相对坐标
（减去 pane 矩形偏移），调用该 pane 的 `mouse()`。

---

## 4. 新增/修改文件（只动绑定层）

| 文件 | 动作 | 内容 |
|---|---|---|
| `wezterm-py/wezterm/pywezterm/src/surface_render.rs` | 新增 | `PySurface`（转 `wezterm-surface::Surface`）set_cell/dimension/clear/resize；`render_changes_bytes(changes) -> bytes`（TerminfoRenderer 写 Python 缓冲） |
| `wezterm-py/wezterm/pywezterm/src/mux.rs` | 新增 | `PyMux` / `PyPaneWrap`（布局树、焦点、合成渲染、命中测试、事件路由） |
| `wezterm-py/wezterm/pywezterm/src/lib.rs` | 修改 | 注册 `PyMux` 与渲染相关类 |
| `wezterm-py/wezterm/pywezterm/Cargo.toml` | 修改 | 加 `wezterm-surface.workspace`（renderer 用）、`termwiz`（已加） |
| `leaf/leaf/usecases/frame.py` | 修改 | 删除手写 build_diff/cell_line；改调 `mux.render()` |
| `leaf/leaf/usecases/input.py` | 修改 | 事件转调 Mux 路由 |
| `leaf/leaf/drivers/pane.py` / `console.py` | 修改 | 建 mux + pane；console 只负责原始事件采集 + 写字节 |
| 新建 `leaf/tests/test_mux*.py` / `wezterm-py/tests/test_surface_render.py` | 新增 | 单测 + e2e |

**不改**任何 vendored wezterm crate（`wezterm-py/wezterm/wezterm-*`）。

---

## 5. leaf / ptyagent 接入点

### leaf（分屏终端 demo）
- `console.py` 保留：读真实控制台原始事件（ReadConsoleInputW）→ 转领域事件（保留）。
- 新建 Mux：`split` 两个 pane（cmd）；把领域事件喂给 `mux.key_down/mouse/scroll`；
  渲染调 `mux.render()` 写 stdout。
- 删掉：`frame.py build_diff`、`domain/ansi.py cell_line`（合成交给 Surface）、
  `layout.logical_lines`（已删）、手写增量 diff。保留分割线绘制（在合成阶段把分隔
  cell 写到 Surface 对应列）。

### ptyagent（守护进程侧）
- 仍用现 `PyTerminal` 做设备无关快照（`TerminalScreen` 走 snapshot/text）不变。
- 若后续 ptyagent 需要"分屏/多 pane 会话"，复用同一个 `Mux`：每 pane 一个会话子进程，
  渲染增量可推给 web 端（xterm 序列）或 attend。Mux 不绑定具体输出，字节流可被
  conhost / xterm.js / 任何 VT 消费者复用。

---

## 6. 依赖与构建

- `pywezterm/Cargo.toml` 增：`wezterm-surface.workspace = true`（已有依赖项可扩展）。
  `termwiz` 已在阶段1加入（KeyboardEncoding），renderer 复用同一依赖。
- 构建：`cargo build --release -p pywezterm` → 覆盖 `bin/pywezterm/pywezterm.pyd`。
- 无外部 Lua/async/ssh 依赖，保持轻量单二进制。

---

## 7. 分阶段实施

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M1** | 新增 `surface_render.rs`：PySurface(set_cell/dim/clear/resize)、render_changes_bytes；单测 | `wezterm-py/tests/test_surface_render.py`：写入变化→增量字节正确；无变化→空 |
| **M2** | 新增 `mux.rs`：PyMux(add_pane/split/set_focus/resize/render)、PyPaneWrap；单测布局矩形 + 合成渲染 | `test_mux_lowlevel.py`：两 pane 增量渲染、滚动后正确 |
| **M3** | leaf 接入：frame/input/pane 改用 Mux；删手写渲染 | leaf 41+ 测试提升、真实渲染/滚动无误 |
| **M4** | ptyagent 侧验证 Mux 可复用（可选分屏会话 demo / 输出字节给 xterm.js） | ptyagent 相关 32+ 通过 |

---

## 8. 风险与注意

- **Surface 合成语义**：`set_cell` 坐标/属性需按 CellTuple 精确映射，宽字符（CJK/emoji）
  宽度处理要与 `wezterm-term` 一致（用 cell.width()）。
- **滚动正确性**：Mux 每帧重新 `set_cell` + Surface 增量 diff，天然规避手写
  `prev_view_top` 判定；视图平移只起重画受影响细胞，正确且高效。
- **鼠标命中**：坐标换算含分割线列（右 pane 起点 = 左宽 + 1 + 1，与现有 leaf 一致）。
- **不绑定 Rust mux**：规避 mlua/Lua/async 依赖，换取轻量与构建稳定；代价是布局树、
  焦点、命中测试需在绑定层实现（M2 覆盖）。

---

## 实施记录

### M1（已完成）：增量渲染绑定
- 新增 `pywezterm/src/surface_render.rs`：`PySurface`（set_cell/clear/resize/
  get_changes_bytes/repaint_bytes），复用 `wezterm-surface::Surface.get_changes`
  增量 diff + `termwiz::render::terminfo::TerminfoRenderer` 转 ANSI 字节。
- 注册进 `lib.rs`；`Cargo.toml` 依赖 `wezterm-surface`/`termwiz`（均已在 workspace）。
- 单测 `wezterm-py/tests/test_surface_render.py`（5 项）：首帧全量、仅变化增量、
  未变化空字节、定位/内容断言。验证：改 1 格只输出该格变化字节。
- wezterm-py 现有测试（test_term 等）不回归。

### M2a（已完成）：Mux 骨架 + 布局矩形
- 新增 `pywezterm/src/mux.rs`：`PyMux`（构造/尺寸/pane_count/焦点/add_pane_placeholder/
  _debug_rects）+ 布局树（`Layout` Leaf/Split、`SplitDir` LR/UD）+ `compute_rects` 递归。
- 布局规则：第 1 个 pane 填满整屏，第 2 个起左右各半（奇数宽左取 w/2、右取余）。
- 单测 `wezterm-py/tests/test_mux_layout.py`（4 项）：满屏、LR 二分、奇数宽。
- 验证：`Mux(120,30)` + 2 pane → rects `[(0,0,60,30),(60,0,60,30)]`，focus=1。

### M3（已完成）：leaf 接入 Mux
把 leaf 的实际渲染/输入管线切到 Mux，删除手写 `build_diff` / `ansi.cell_line` /
`prev_view_top` / 逐 pane 的 Pty+Terminal+reader：
- `leaf/drivers/pane.py`：改为**一个共享 Mux 的门面** `MuxPanel`（`set_sep(True)` +
  `set_status_rows(1)`，`render` 统一合成 pane+分隔线+状态栏；`key_down/up/mouse(pane_at
  命中)/scroll_pane/set_focus/set_split_col(拖动预览)/resize/set_status/all_eof`）。
  保留 `Pane` 薄封装（单 pane Mux）供 test_e2e/独立场景。
- `leaf/usecases/frame.py`：删 `build_diff`；`render_loop` 走 `mux.render()`
  + `mux.set_status` + 光标写回；**render 循环加 50ms 定期渲染**（pane reader 线程
  已沉入 Mux，无输入时也要捕捉后台输出）。
- `leaf/usecases/input.py`：`handle_key/mouse/events` 改走 `mux.key_down/key_up/mouse/
  scroll_pane/set_split_col/resize`（整屏坐标命中路由，不再逐 pane）。状态机
  （拖拽预览松手落位 / F9 / F10 / 尺寸 force_full）保留。
- `leaf/app.py`：建 `MuxPanel`；`leaf/adapters/output.py` `StdoutSink` 支持 bytes。
- 删除 `leaf/domain/layout.py`（split_layout 死代码）、`leaf/domain/ansi.cell_line/
  color_seq/cursor_col`（合成下沉 Mux）、`tests/test_frame.py|test_ansi.py|
  test_layout.py`（测已删逻辑）。
- 测试：`test_e2e.py`（Pane 独立/resize/滚动 + MuxPanel 合成两 pane+分隔线+增量空）
  、`test_input_usecases.py`（假门面路由/拖拽/滚轮）。新增 Mux 侧 `pane_at`。
- 验证：leaf 29 通过（含真实 ConPTY e2e）；wezterm-py 49 通过。
- 注：`scripts/live_selfcheck.py`（嵌套宿主 ConPTY 自检）在无真实交互控制台的自动
  化环境里受 `console=Console()` 修改宿主控制台模式的影响，须在真实交互终端中运行
  （用户偏好：assistant 启动 cmd/wt，用户粘贴注入命令）。

### 待办（M4）
- M4：ptyagent 侧复用验证（可选：分屏会话 / 输出字节给 xterm.js）。
- 建议：leaf 运行时（真实交互）配合 live_selfcheck 做最终人工验收；如需在自动化里
  校验，可考虑把 `console=Console()` 的宿主模式修改与 Mux pane 输出捕获隔离。

### M3 Mux 前置能力（已完成，供 leaf 接手直接使用）
为承载 leaf 的拖拽分割线与底部状态栏，Mux 增加布局/合成控制（与 pane 内容并入
同一 Surface 增量输出）：
- `set_sep(bool)`：pane 之间是否预留一列分隔线（leaf 形态）；重算矩形 + 标记各
  pane 重写；默认 false（紧贴，保持既有布局测试不变）。
- `set_split_col(Option<usize>)`：设置左右分屏的列位置（None=中点）。**只重算矩形
  并标志 pane 重写做预览，不实时 resize ConPTY**——宿主在拖拽松手后调 `resize()`
  才一次落位（避免拖动中反复窄化 wrap 裂行）。
- `set_status_rows(usize)`：底部预留状态栏行（0=无），pane 高度相应缩减。
- `set_status(text)`：状态栏文本；`render()` 时与上次比较，变化才重画（参与增量）。
- `set_focus / key_down / key_up / mouse(整屏坐标命中路由) / scroll / scroll_to_bottom`
  / `resize(cols,rows)`（重算矩形 + resize 各 pane pty+终端 + 重置合成基线）。
- `render()` 返回 `(bytes, cursor_row, cursor_col, cursor_visible)`：合成所有 pane
  + 分隔线（`│`）+ 状态栏为增量字节。
- 测试 `wezterm-py/tests/test_mux_lowlevel.py::test_mux_sep_status_layout_controls`
  （分隔线/指定分割列/状态栏行/状态文本的布局与增量合成）。

### M2c（已完成）：渲染合成 + 命中路由 + 滚动
- `pywezterm/src/mux.rs` 新增整屏合成渲染：
  - `render()`：把各 pane 可见内容合成进 Mux 内部 `Surface` → `get_changes(seqno)`
    取增量 → `render_changes_bytes`（terminfo→ANSI）→ 返回 `(bytes, cursor_row,
    cursor_col, cursor_visible)`（光标映射到整屏坐标）。
  - **关键修复**：`Line::set_cell` 无条件上推 last_change_seqno，若每帧把所有格子
    set_cell 会让 Surface 每帧全量重绘。改为**脏行驱动**：只重写「确实变化」的
    矩形行（首帧 / 视图平移 → 整 pane 重写；否则用 `changed_stable_rows` 判定
    脏行），未变化行保留原 Surface → 无变化帧返回空增量。
  - **run 合并**：重写行按「连续同列位 + 同风格」合并为一个 Text 段，只在段首发
    CUP + 属性，避免逐格 `\x1b[..;..H`（rendered 字节淹没在定位序列中）；行内/
    行尾空白补默认空格以清掉过期内容，宽字符按 width 占列（续列不单发）。
  - 滚动正确性：滚动只改 pane 视图偏移，不新增终端 seqno → 脏行判不出；故
    **view_offset 变化即整 pane 重写**（等效 leaf 的 prev_view_top 强制全量），
    杜绝滚动错位。
- 路由/滚动/布局：`set_focus` / `key_down` / `key_up` / `mouse`（命中 pane→坐标
  换算→编码下发）/ `scroll` / `scroll_to_bottom` / `resize`（重算矩形 + resize
  各 pane pty+终端 + 重置合成基线）。`pane_scroll` / `pane_text`（计入视图偏移）。
- surface_render.rs 抽取 `render_changes_bytes`（PySurface 与 Mux 共用）。
- 测试 `wezterm-py/tests/test_mux_lowlevel.py`（5 项）：首帧全量/二帧空增量、
  增量只含新增内容、滚动后 render 反映视图平移并可回落复原、鼠标命中路由（未命中
  报错）、set_focus/resize。
- 验证：wezterm-py 全量 48 通过。

### M2b（已完成）：Mux 真实 Pty + Terminal pane
- `pywezterm/src/mux.rs`：PyMux 由「placeholder」升级为持有真实 Pane。每个 Pane =
  portable-pty（openpty + spawn 子进程）+ 一个 `wezterm-term` 终端模型。
- 每 Pane 自建 **reader 线程**：阻塞读 pty 输出 → `advance_bytes` 喂终端模型，并把
  终端应答（DSR 光标等经 capture writer 收进缓冲的序列）自动回写 pty writer，
  避免子进程等应答卡死（把原先 test_pty.py `_run` 的手动闭环下沉进引擎）。
- **capture writer 键盘路径**：`pane_key_down/key_up/mouse` 经终端模型模式感知编码
  → 取捕获缓冲 → 下发 pty → 返回编码字节。复用 term.rs 的 `ParseKeycode`/
  `ParseMouseKind`/`ParseMouseButton`/`CaptureWriter`/`EmbeddedConfig`（改 pub(crate)）。
- `add_pane(argv, cwd, env)` 替换 `add_pane_placeholder`（真实 spawn；布局规则沿用 M2a：
  第 1 个满屏、第 2 个起左右各半，pane 终端/pty 尺寸按布局矩形初始化）。
- 暴露 pane 级查询：`pane_text / pane_cursor / pane_is_mouse_grabbed / pane_try_wait /
  pane_resize / pane_write / close_pane / close`。
- pty.rs 抽取 `ensure_conpty_dir(py)`（Mux 建 pane 与 Pty 复用侧载 conpty/OpenConsole）。
- 测试：`test_mux_layout.py` 改用真实 `add_pane`+`close`（4 项）；新增
  `test_mux_pane.py`（4 项）：spawn+reader 喂终端、多 pane 互不串扰、键盘编码下发
  （交互 cmd 回显键入内容）、close 幂等。
- 验证：wezterm-py 全量 43 通过；`add_pane` 默认可省 `cwd/env`（`#[pyo3(signature=...)]`）。