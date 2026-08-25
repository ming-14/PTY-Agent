# leaf — Windows ConPTY 分屏终端

一个终端内左右分屏同时运行两个子进程（pane1 / pane2），中间 1 列分隔线，末行状态栏。事件驱动渲染：数据到达即重绘，无轮询。

## 用法

```bash
python main.py --pane1 cmd --pane2 cmd
python main.py --pane1 vim --pane2 <path\to\hx.exe> args...
```

- `--pane1` 左窗格程序（默认 `cmd`）
- `--pane2` 右窗格程序（必填，裸程序名自动解析为绝对路径）

### 快捷键与鼠标

| 操作 | 效果 |
|---|---|
| `F9` | 切换焦点窗格 |
| `F10` | 退出 |
| 点击窗格 | 切换焦点到命中的窗格 |
| 拖拽分隔线 | 调整左右分屏比例（松手时子进程才 resize） |
| 滚轮 | 程序未启用鼠标模式时宿主滚动该窗格历史 |

### 排障开关

```bash
LEAF_DUMP_FRAME=1 python main.py ...   # 逐帧 dump 左窗格逻辑行到 leaf.dump（仅本地调试，产物不入库）
```

日志写入 `leaf.log`（文件），控制台仅输出 WARNING（避免混入 TUI 画面）。

## 架构

干净架构四层洋葱模型，依赖方向只允许外层指向内层：

```
domain ← usecases ← adapters ← drivers ← app
```

| 层 | 目录 | 职责 |
|---|---|---|
| 实体层 | `leaf/domain/` | 纯领域规则：领域事件（`events.py`）、ANSI 渲染常量（`ansi.py`）。零 I/O、零框架依赖 |
| 用例层 | `leaf/usecases/` | 应用编排：帧合成与渲染循环（`frame.py`）、输入路由与拖拽状态机（`input.py`）、端口协议（`ports.py`）、程序解析（`launch.py`）。只依赖端口抽象，不接触框架 |
| 接口适配层 | `leaf/adapters/` | 剪贴板（`clipboard.py`）、输出适配（`output.py`：StdoutSink） |
| 框架与驱动层 | `leaf/drivers/` | pywezterm（`pane.py`：Mux 门面）、Win32 控制台输入（`console.py`：ConsoleInput 归一化）。`import pywezterm` 仅出现在此层 |
| 组合根 | `leaf/app.py` + 根 `main.py` | 依赖组装、线程编排、CLI 入口、日志 |

## 关键设计

- **pywezterm 隔离**：用例层通过 `ConsolePort`/`MuxPanelPort`/`ClipboardPort`/`OutputSink` 协议（`usecases/ports.py`）依赖窗格与终端，驱动层实现，可替换/可测试。
- **事件驱动渲染**：Mux 的 reader 线程读到输出即置 `render_event`，渲染线程阻塞在事件上重绘（无主循环轮询）；主线程只处理输入事件。渲染限速 `MIN_FRAME=8ms`，鼠标 move 转发节流 `MOVE_INTERVAL=16ms`。
- **增量重绘**：差分由 pywezterm.Mux 承担（Surface 合成 + 脏行驱动），仅重绘变化行；尺寸/分割位置变化时清屏全量重建（`force_full` 收敛帧）。
- **Win32 输入归一化**：驱动层 `Console.read_inputs()` 直接返回领域事件（KeyEvent/MouseEvent/ResizeEvent），用例层不接触任何 Win32 结构；归一化在绑定层 ConsoleInput 完成。

## 测试

```bash
python -m pytest tests/ -v              # 单元测试 + 真实 ConPTY e2e
python scripts/live_selfcheck.py        # 真实 leaf 进程自检（裂行/重复帧）
```

- `tests/test_console.py` 控制台事件映射
- `tests/test_input_usecases.py` 输入路由与拖拽状态机（假对象）
- `tests/test_e2e.py` 真实 ConPTY 上双窗格 e2e

仅支持 Windows（依赖 ConPTY）。
