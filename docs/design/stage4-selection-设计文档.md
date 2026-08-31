# 阶段 4：文本选择 / 复制 / 粘贴

> 目标：让 `leaf`、`ptyagent` 等应用获得完整的文本选区、复制、粘贴能力——
> 选区（word/line/区域选择 + 取选中纯文本）、应用发起的剪贴板写（OSC 52）、
> 宿主侧粘贴下发（模式感知包裹）。
>
> 约束：**不改任何 vendored wezterm crate**（`wezterm-py/wezterm/*`）。仅新增/
> 修改 pywezterm 绑定层（`wezterm-py/wezterm/pywezterm/src/*`）。选区状态机在
> 绑定层自建（vendored wezterm-term 无 selection 状态），但复用其已暴露的
> `get_semantic_zones`、screen 读取、`Clipboard` 回调接口。

---

## 1. 现状与缺口对照

### 1.1 vendored wezterm-term 已有 vs 未有的能力

| 能力 | wezterm-term 位置 | 状态 |
|---|---|---|
| 语义区（prompt/input/output） | `TerminalState::get_semantic_zones` | ✅ 已绑定（`PyTerminal.get_semantic_zones`） |
| 剪贴板回调注入 | `TerminalState::set_clipboard`（`Clipboard` trait，OSC 52 触发） | ✅ 底层有，❌ 未绑定 |
| 下载/设备控制/通知回调 | `set_download_handler`/`set_device_control_handler`/`set_notification_handler` | ❌ 未绑定（阶段4 顺带暴露） |
| 模式感知粘贴下发 | `TerminalState::send_paste` | ✅ 已绑定（`PyTerminal.send_paste`） |
| 强制全量失效 | `TerminalState::make_all_lines_dirty` | ❌ 未绑定 |
| **选区状态机**（word/line/区域选择、取选中文本） | **vendored 无此能力** | ❌ 需绑定层自建 |
| 逐格/区域取文本（选区的数据源） | `Screen::lines_in_phys_range` + `Line::visible_cells` | ✅ 已绑定（`cells_of_line`/`pane_text` 可复用） |

### 1.2 需求场景对照

| 场景 | 现状（leaf/ptagent） | 阶段4 目标 |
|---|---|---|
| 鼠标拖选文本（区域选择） | ❌ 无 | `Selection.select_region` + `Selection.text` |
| 双击选词 / 三击选行 | ❌ 无 | 复用语义区或按空白/行边界 |
| 拖到屏外自动滚动 | ❌ 无 | 绑定层支持滚动边界（v1 可选） |
| 应用发起的剪贴板写（OSC 52） | ❌ 丢弃（无 handler） | `Terminal.set_clipboard_callback` → Python 回调 |
| 宿主粘贴（Ctrl+V） | `send_paste`（模式感知）已可用 | leaf 事件路由接入 |
| 取选中纯文本 | ❌ 无 | `Selection.text` / `Mux.pane_selection_text` |

---

## 2. 选区状态机设计（绑定层自建）

### 2.1 坐标模型

- 选区坐标用**稳定行索引（stable row）+ 列**表示，跨 scrollback 与可见区，不受
  视图滚动（view_offset）影响——与 `get_semantic_zones` 的坐标基准一致。
- 鼠标事件传入的是**整屏坐标**（leaf/Mux 的鼠标命中坐标），选区入口先把整屏
  坐标换算为 stable 坐标：`stable = phys_to_stable_row_index(start + y)`，
  `col = x`（x 即 pane 内列）。

### 2.2 选区类型与语义

| 类型 | 触发 | 语义 |
|---|---|---|
| `Region`（区域） | 鼠标按下拖拽 | 锚点 (start_stable, start_col) → 当前 (end_stable, end_col)，矩形内全部文本 |
| `Word` | 双击 | 以锚点所在词的边界（空白/标点分隔）为起止 |
| `Line` | 三击 | 以锚点所在物理行的整行（含换行）为起止 |
| `All` | Ctrl+A（可选） | 整个 scrollback + 可见区 |

### 2.3 选区取文本算法

`text()` 的算法（纯函数，可单测）：

1. 锚点/终点按 stable 行号排序 → `(lo_stable, hi_stable)`
2. 对每个 stable 行取物理行：`phys = stable_to_phys_row_index(stable)`
3. 该行取 `cells_of_line` → 拼接 `cell.str()`（跳过续列，宽字符按显示宽度占位）
4. 首行截 `[start_col, ∞)`、末行截 `[0, end_col]`（列按 cell 列号）
5. 中间行整行；行间补 `\n`
6. 末尾行尾空白裁剪（与 `pane_text` 语义一致）

> 关键：**数据源复用 `lines_in_phys_range` + `cells_of_line`**（阶段2 已绑定），
> 选区算法不触碰 vendored crate，纯在绑定层实现。

### 2.4 选区状态（Pane 内持有）

```rust
struct SelectionState {
    anchor: Option<(isize, usize)>,   // (stable_row, col)，按下时的锚点
    kind: SelectionKind,              // Region / Word / Line
    end: (isize, usize),              // 当前端点（拖拽/双击更新）
}
```

- 选区属于**单个 pane**（Mux 的每个 pane 一份）；跨 pane 选择 v1 不支持（与
  常见终端一致：拖选只作用在起点 pane）。
- 选区与 `view_offset` 解耦：选中 scrollback 深处内容时，视图滚动不影响选区边界。

---

## 3. 绑定 API 设计

### 3.1 `PyTerminal`（term.rs）新增

```rust
/// 选区：区域选择（anchor → end，矩形内全部文本）
fn selection_set(&self, anchor_row: isize, anchor_col: usize,
                  end_row: isize, end_col: usize) -> PyResult<()>;

/// 选区：双击选词（以 anchor 所在词的边界）
fn selection_select_word(&self, row: isize, col: usize) -> PyResult<()>;

/// 选区：三击选行（anchor 所在物理行整行）
fn selection_select_line(&self, row: isize, col: usize) -> PyResult<()>;

/// 当前选区纯文本（无选区返回空串）
fn selection_text(&self) -> PyResult<String>;

/// 是否有活动选区
fn selection_active(&self) -> bool;

/// 清除选区
fn selection_clear(&self);

/// 设置剪贴板回调：应用发 OSC 52 时把内容交给 Python（取代默认丢弃）
/// callback: Callable[[str, Optional[str]], None]（selection 名, 内容）
fn set_clipboard_callback(&self, callback: PyObject) -> PyResult<()>;

/// 暴露下载/设备控制/通知回调（顺带补齐，同 Clipboard 链路）
fn set_download_callback(&self, callback: PyObject) -> PyResult<()>;
fn set_device_control_callback(&self, callback: PyObject) -> PyResult<()>;
fn set_notification_callback(&self, callback: PyObject) -> PyResult<()>;

/// 强制全量失效（阶段1 表格列出但未绑定）
fn make_all_lines_dirty(&self);
```

**剪贴板回调实现要点**：`TerminalState::set_clipboard(&Arc<dyn Clipboard>)` 接受
trait object。绑定层实现一个 `PyClipboard`（持有 pyo3 的 `Py<PyAny>` 回调）：

```rust
struct PyClipboard(Py<PyAny>);
impl Clipboard for PyClipboard {
    fn set_contents(&self, sel: ClipboardSelection, data: Option<String>) -> Result<()> {
        // 通过 GIL 调 Python 回调；回调异常吞掉并记录（剪贴板写失败不崩终端）
    }
}
```

> 注意：`set_clipboard` 发生在 `TerminalState` 处理 OSC 52 的上下文中，持 `&self`；
> Python 回调调用需 `Python::with_gil`，且不能重入 `terminal` 锁（回调内若再查
> terminal 会死锁）。文档明确：**回调里只做剪贴板写入，不反查终端状态**。

### 3.2 `PyMux`（mux.rs）新增

Mux 是 leaf 的主入口，选区需要按 pane 路由。新增 pane 级透传（与 `pane_scroll`
等一致）：

```rust
fn pane_selection_set(&self, pane_id, anchor_row, anchor_col, end_row, end_col) -> PyResult<()>;
fn pane_selection_select_word(&self, pane_id, row, col) -> PyResult<()>;
fn pane_selection_select_line(&self, pane_id, row, col) -> PyResult<()>;
fn pane_selection_text(&self, pane_id) -> PyResult<String>;
fn pane_selection_active(&self, pane_id) -> PyResult<bool>;
fn pane_selection_clear(&self, pane_id) -> PyResult<()>;
fn set_focus_selection_callback(&self, callback: PyObject) -> PyResult<()>; // 焦点 pane 剪贴板
```

坐标换算：Mux 入口是**整屏坐标**，先 `hit_test` 命中 pane，再换算为 pane 内
坐标（`x - rect.x`、`y - rect.y`），再转 stable 行——与现有 `mouse()` 路由一致。

### 3.3 渲染层的选区高亮（可选增强）

- 选区渲染（反显/背景色）需要合成时把选中的 cell 标记为 reverse。
- v1 方案：`render()` 返回光标之外，再返回选区矩形列表
  `(start_stable, start_col, end_stable, end_col)`；宿主（leaf）自行把该区域
  合成反显（不改 Surface 合成核心）。
- v2 方案（更内聚）：`compose_pane` 合成时若 cell 在选区矩形内 → 加 reverse
  属性（`CellAttributes::set_reverse(true)`）。选区变化时标记该 pane 脏行全量重写。
- 文档建议 v1 先做「选区文本提取 + 复制」，渲染高亮作为 v1.5 增强，避免
  合成路径复杂度膨胀。

---

## 4. leaf / ptyagent 替换方案

### 4.1 leaf

- **事件路由**（`usecases/input.py`）：
  - 鼠标 press 命中 pane → `mux.pane_selection_set`（拖拽起点）
  - mouse move（按住拖动）→ 更新 `end`（节流同 move 转发）
  - release → 结束；`pane_selection_text` 取文 → 写系统剪贴板（leaf 已可调
    win32 API 或交给宿主）
  - 双击/三击（`mouse()` 编码之外，leaf 判断 count）→ `select_word`/`select_line`
- **粘贴**（`F10` 外的快捷键或 Ctrl+V）→ `mux.send_paste(text)`（模式感知包裹
  已就绪，leaf 只需把剪贴板文本传入）
- **应用 OSC 52**：`set_focus_selection_callback` → 写系统剪贴板
- **删除/停用**：无既有手写选区代码可删（leaf 从未有选区），新增接入点。

### 4.2 ptyagent

- 前端（web_rime）选区：从 session 数据读 pane 文本（已有 `pane_text`），
  前端自行做视觉选区；复制时调 daemon → `pane_selection_text`（或直接前端
  用已读文本）→ 系统剪贴板。
- 应用 OSC 52：daemon 侧 `set_clipboard_callback` 转发到前端。

---

## 5. 验收基准

- **主基准：leaf 无感替换。** 新增选区交互后，leaf 功能不回退，跑通
  `python -m pytest tests/` + `scripts/live_selfcheck.py`。
- **选区专项（自动化）**：
  - `wezterm-py/tests/test_selection.py`：
    - 区域选择跨行取文本（含 scrollback 行）
    - 双击选词边界正确（空白/标点分隔）
    - 三击选行含换行
    - 无选区返回空串
    - 选区清除
    - OSC 52 回调收到内容（`set_clipboard_callback` 注入 Python 捕获）
  - leaf 新增选区路由单测（mock Mux 验证 press/move/release → selection 调用序列）
- **手动验收**：真实 leaf 窗口拖选 → 复制 → 粘贴；vim/tmux 内拖选文本与
  常见终端行为一致；应用（如 `tmux set-clipboard on`）发 OSC 52 → 系统剪贴板收到。

---

## 6. 风险与备选方案

| 风险 | 影响 | 对策 |
|---|---|---|
| vendored wezterm-term 无选区 API，需自建 | 选区算法可能漏边界（宽字符/续列/换行） | 纯函数 + 充分单测；数据源复用 `cells_of_line`（已有宽字符处理） |
| `set_clipboard` 回调持锁调用 Python | 回调内反查终端死锁 | 文档约束 + 回调内 `Python::with_gil` 短调用；测试覆盖回调异常不崩 |
| 选区渲染高亮改合成路径 | 增量渲染回归 | v1 不做渲染高亮，先做取文/复制；高亮 v1.5 单独验收 |
| 拖选跨 pane | 复杂度高 | v1 限定单 pane（与常见终端一致），文档注明 |
| Mux 与 Terminal 两份选区实现重复 | 双份维护 | 选区核心实现放在 term.rs 的**内部模块**（非 pymethods），Mux 的 pane 复用同一结构；只暴露 pyclass 方法做转发 |

---

## 7. 实施顺序

1. **term.rs 内部选区模块**（纯 Rust，不含 pyo3）：`SelectionState` + `text()` 算法
   + `select_word/line/region` 纯函数 → `cargo test` 单测
2. **PyTerminal 选区绑定**：pyo3 方法转发内部模块 → `tests/test_selection.py` 基础项
3. **Clipboard/Download/DeviceControl/Notification 回调绑定**：`PyClipboard` 等
   trait impl + `set_*_callback` → 单测（OSC 52 链路）
4. **PyMux pane 级透传**：整屏坐标命中 → 换算 → 内部选区模块 → 单测
5. **leaf 接入**：input.py 事件路由 + 剪贴板写 + 粘贴下发 → leaf 单测 + live_selfcheck
6. **ptyagent 接入**：daemon 暴露选区/剪贴板接口 → 前端联动
7. **构建**：`BUILD.ps1` → 部署 vendored → 全量回归

> 每一步遵循「先绑定 → 构建 → 单测 → 替换引用 → 验收」。
