# pywezterm Mux 复用器设计（leaf / ptyagent 共用）

> 目标：让 leaf、ptyagent 等应用**完全不自己处理终端**——布局、焦点、滚动、多窗格
> 合成、增量渲染全部交给 wezterm 底层模块，经 pywezterm 统一 API 暴露。
>
> 定位：在 **pywezterm 绑定层**自建复用器编排单元（Mux），基于 wezterm 已写好的
> 底层模块（`wezterm-term` pane 模拟 + `wezterm-surface` 合成渲染 + `portable-pty`
> 子进程），而非硬绑 Rust `mux` crate（其依赖 mlua/Lua/async 等 14+ 重 crate，且
> 最终产物仍是 surface/Change，仍需自行接 conhost 渲染）。
>
> 约束：不改 vendored wezterm crate；只新增/修改 pywezterm 绑定层。

---

## 1. 能力来源对照

| 能力 | wezterm 底层模块 | 绑定暴露 | Mux 使用 |
|---|---|---|---|
| 子进程伪终端 | `portable-pty`（PyPty） | ✅ | 每个 pane 一个 Pty |
| 设备无关终端模拟（VT/光标/scrollback/键盘鼠标编码/逻辑行） | `wezterm-term`（PyTerminal） | ✅ (feed/resize/key/mouse/snapshot_lines/logical_lines/scroll/is_mouse...) | 每个 pane 一个 Terminal |
| **增量渲染**（单元格级 diff → Change 流） | `wezterm-surface::Surface.get_changes(seqno)` | ✅ | 合成 pane 到整屏 Surface 后取变化 |
| **Change → 终端字节流**（真实终端 ANSI） | `termwiz::render::terminfo::TerminfoRenderer.render_to` | ✅ | 增量字节返回宿主 |
| 多窗格布局 / 焦点 / 滚动 | wezterm `mux`（不引入） | — | 绑定层自建编排 |

核心结论：**增量渲染与字节输出**是 wezterm 现成成品（`Surface` + renderer），只需绑定；
**多窗格编排**（布局/焦点/滚动/合成）由 Mux 在绑定层组织上述能力。

---

## 2. Mux 复用器 API 设计（Python 视角）

调用方建一个 Mux（对应一个真实终端屏），往里面加 Pane（每 Pane 一个子进程），
设置布局与焦点，每帧调用渲染取增量字节写控制台。

```python
import pywezterm

mux = pywezterm.Mux(cols=120, rows=30)      # 宿主屏默认尺寸
p1 = mux.add_pane(["cmd.exe","/d","/k"])    # 默认填满
p2 = mux.add_pane(["cmd.exe","/d","/k"])
mux.set_sep(True)                           # pane 之间预留分隔线列
mux.set_split_col(60)                       # 左右分屏列位置（拖拽预览）
mux.set_status_rows(1)                      # 底部状态栏行数
mux.set_focus(p2)
mux.resize(120, 30)                         # 宿主终端尺寸变化

# —— 事件路由（键盘/鼠标/滚动 由应用采集原始事件，转交 Mux 编码）——
mux.key_down(key="a", mods=0)               # 编码到焦点 pane，写其 Pty
mux.mouse(x, y, "press", "left", mods=0)    # 命中 pane → 编码 → 写其 Pty
mux.scroll(delta)                           # 焦点 pane 视图滚动

# —— 渲染（增量）——
bytes_, cursor_row, cursor_col, cursor_visible = mux.render()
console.write(bytes_)

# —— 关闭 ——
mux.close()
```

### 2.1 `Mux` 类

| 方法 | 职责 |
|---|---|
| `Mux(cols, rows)` | 构造宿主屏默认尺寸；持有一个 Surface（合成目标）、N 个 Pane、布局树、焦点 |
| `add_pane(argv, cwd, env) -> Pane` | 新建 pane（Pty+Terminal+reader 线程），默认铺满；返回句柄 |
| `set_focus(pane)` | 焦点切换；后续键盘/鼠标走焦点 |
| `focused() -> Pane` | 当前焦点 |
| `set_sep(bool)` | pane 之间是否预留一列分隔线；重算矩形 + 标记各 pane 重写 |
| `set_split_col(Option<usize>)` | 设置左右分屏列位置（None=中点）；**只重算矩形做预览，不实时 resize ConPTY**——宿主在拖拽松手后调 `resize()` 才一次落位（避免拖动中反复窄化 wrap 裂行） |
| `set_status_rows(usize)` | 底部预留状态栏行（0=无），pane 高度相应缩减 |
| `set_status(text)` | 状态栏文本；`render()` 时与上次比较，变化才重画（参与增量） |
| `resize(cols, rows)` | 宿主屏尺寸 + 重切所有 pane 尺寸（含 pty resize + 终端 rewrap + 重置合成基线） |
| `key_down(key, mods)` / `key_up(...)` | 编码到焦点 pane 并写其 Pty（复用 PyTerminal.key_down） |
| `mouse(x, y, kind, button, mods)` | 命中 pane（by 布局矩形）→ 坐标换算 → 编码写 Pty；未命中/状态栏由应用自理 |
| `scroll(delta)` / `scroll_to_bottom()` | 焦点 pane 视图滚动（PyTerminal.scroll） |
| `render() -> (bytes, cursor_row, cursor_col, cursor_visible)` | 合成各 pane 格子到 Surface → get_changes → render_to → 增量字节 + 光标 | 
| `close()` | 关闭所有 pane |

### 2.2 `Pane` 类（pywezterm Mux 内部持有的 pane 句柄）

封装 `Pty + Terminal`，暴露读取/滚动/编码：
`key_down / key_up / mouse / write / resize / scroll / scroll_to_bottom /
logical_lines / snapshot / cursor / is_mouse_grabbed / close / try_wait /
pane_text / pane_scroll`。
（大部分复用现有 PyTerminal 绑定；Mux 内部使用。）

---

## 3. 关键实现点

### 3.1 布局树

用简单嵌套矩形布局（类似 wezterm mux 的 Layout Tree，但仅左右/上下二分，
支持递归）。每个布局叶子 = 一个 pane 的 (x, y, w, h) 矩形。分割时把 target 矩形二分，
新 pane 得其一。布局规则：第 1 个 pane 填满整屏，第 2 个起左右各半（奇数宽左取
w/2、右取余）。Mux 重算所有 pane 的 Terminal 尺寸（`terminal.resize`）。

### 3.2 渲染合成（核心，增量）

1. 对每个 pane：`logical_lines()` 取可见逻辑行（含 scroll 偏移的 first/last stable）。
2. 逐 pane、逐行，把每个 cell 写到 Mux 的 `Surface` 对应矩形坐标：
   `set_cell(px, py, text, fg, bg, bold, ...)`——格式与 snapshot/lines 的 CellTuple 对齐。
3. Mux 保存上一次 `seqno`；`changes = surface.get_changes(seqno)` 得到**仅变化部分**
   （wezterm 内部做 CursorPosition/属性/文本合并）。
4. `TerminfoRenderer::render_to(&changes, &mut buf)` → 增量 ANSI 字节返回给 Python。
5. 光标：取焦点 pane 光标，映射到整屏坐标。

**脏行驱动**：`Line::set_cell` 无条件上推 last_change_seqno，若每帧把所有格子 set_cell
会让 Surface 每帧全量重绘。改为只重写「确实变化」的矩形行（首帧 / 视图平移 → 整
pane 重写；否则用 `changed_stable_rows` 判定脏行），未变化行保留原 Surface → 无变化
帧返回空增量。

**run 合并**：重写行按「连续同列位 + 同风格」合并为一个 Text 段，只在段首发
CUP + 属性，避免逐格 `\x1b[..;..H`（rendered 字节淹没在定位序列中）；行内/行尾
空白补默认空格以清掉过期内容，宽字符按 width 占列（续列不单发）。

**滚动正确性**：滚动只改 pane 视图偏移，不新增终端 seqno → 脏行判不出；故
view_offset 变化即整 pane 重写（等效全量），杜绝滚动错位。Mux 用 Surface 每次合成
后再 diff，视图平移时只对变化的格子重新 set_cell，Surface 的增量自然只输出变化。

### 3.3 命中测试与坐标换算（鼠标）

由布局树矩形判定 `(x,y)` 属于哪个 pane，再把整屏坐标转 pane 内相对坐标
（减去 pane 矩形偏移），调用该 pane 的 `mouse()`。坐标换算含分隔线列（右 pane
起点 = 左宽 + 1 + 1）。

### 3.4 Pane reader 线程与应答回写

每个 Pane 自建 **reader 线程**：阻塞读 pty 输出 → `advance_bytes` 喂终端模型，并把
终端应答（DSR 光标等经 capture writer 收进缓冲的序列）自动回写 pty writer，
避免子进程等应答卡死。

**capture writer 键盘路径**：`key_down/key_up/mouse` 经终端模型模式感知编码 →
取捕获缓冲 → 下发 pty → 返回编码字节。复用 term.rs 的 `ParseKeycode`/
`ParseMouseKind`/`ParseMouseButton`/`CaptureWriter`/`EmbeddedConfig`。

---

## 4. 文件结构（绑定层）

| 文件 | 内容 |
|---|---|
| `wezterm-py/wezterm/pywezterm/src/surface_render.rs` | `PySurface`（转 `wezterm-surface::Surface`）set_cell/dimension/clear/resize；`render_changes_bytes(changes) -> bytes`（TerminfoRenderer 写 Python 缓冲） |
| `wezterm-py/wezterm/pywezterm/src/mux.rs` | `PyMux` / `PyPaneWrap`（布局树、焦点、合成渲染、命中测试、事件路由、reader 线程） |
| `wezterm-py/wezterm/pywezterm/src/lib.rs` | 注册 `PyMux` 与渲染相关类 |
| `wezterm-py/wezterm/pywezterm/Cargo.toml` | `wezterm-surface.workspace`（renderer 用）、`termwiz`（KeyboardEncoding） |
| `leaf/leaf/usecases/frame.py` | 渲染走 `mux.render()`（render 循环 50ms 定期渲染，捕捉后台输出） |
| `leaf/leaf/usecases/input.py` | 事件转调 Mux 路由（整屏坐标命中路由） |
| `leaf/leaf/drivers/pane.py` / `console.py` | 建 mux + pane；console 只负责原始事件采集 + 写字节 |
| `wezterm-py/tests/test_mux*.py` / `test_surface_render.py` | 单测 + e2e |

**不改**任何 vendored wezterm crate（`wezterm-py/wezterm/wezterm-*`）。

---

## 5. leaf / ptyagent 接入点

### leaf（分屏终端 demo）
- `console.py` 保留：读真实控制台原始事件（ReadConsoleInputW）→ 转领域事件。
- 新建 Mux：`split` 两个 pane（cmd）；把领域事件喂给 `mux.key_down/mouse/scroll`；
  渲染调 `mux.render()` 写 stdout。
- 手写增量 diff（`frame.py build_diff`、`ansi.py cell_line`、`prev_view_top`）由
  Surface 合成取代；分割线绘制在合成阶段把分隔 cell 写到 Surface 对应列。

### ptyagent（守护进程侧）
- 仍用 `PyTerminal` 做设备无关快照（`TerminalScreen` 走 snapshot/text）。
- 若后续 ptyagent 需要"分屏/多 pane 会话"，复用同一个 `Mux`：每 pane 一个会话子进程，
  渲染增量可推给 web 端（xterm 序列）或 attend。Mux 不绑定具体输出，字节流可被
  conhost / xterm.js / 任何 VT 消费者复用。

---

## 6. 依赖与构建

- `pywezterm/Cargo.toml`：`wezterm-surface.workspace = true`、`termwiz`（KeyboardEncoding）。
- 构建：`cargo build --release -p pywezterm` → 覆盖 `bin/pywezterm/pywezterm.pyd`。
- 无外部 Lua/async/ssh 依赖，保持轻量单二进制。

---

## 7. 风险与注意

- **Surface 合成语义**：`set_cell` 坐标/属性需按 CellTuple 精确映射，宽字符（CJK/emoji）
  宽度处理要与 `wezterm-term` 一致（用 cell.width()）。
- **滚动正确性**：Mux 每帧重新 `set_cell` + Surface 增量 diff，天然规避手写
  `prev_view_top` 判定；视图平移只起重画受影响细胞，正确且高效。
- **鼠标命中**：坐标换算含分割线列（右 pane 起点 = 左宽 + 1 + 1，与现有 leaf 一致）。
- **不绑定 Rust mux**：规避 mlua/Lua/async 依赖，换取轻量与构建稳定；代价是布局树、
  焦点、命中测试需在绑定层实现。
