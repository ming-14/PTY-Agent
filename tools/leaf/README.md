# leaf — Windows ConPTY 分屏终端

一个终端内左右分屏同时运行两个子进程（pane1 / pane2），中间 1 列分隔线，末行状态栏。事件驱动渲染：数据到达即重绘，无轮询。

内置 asciinema 录制/回放/转换/拼接/直播功能。

## 用法

```bash
python main.py --pane1 cmd --pane2 cmd
python main.py --pane1 vim --pane2 <path\to\hx.exe> args...
```

- `--pane1` 左窗格程序（默认 `cmd`）
- `--pane2` 右窗格程序（可选，默认单窗格；裸程序名自动解析为绝对路径）

### 快捷键与鼠标

| 操作 | 效果 |
|---|---|
| `F8` | 开始/结束录制（保存到 `工作目录/窗口标题.cast`） |
| `F9` | 切换焦点窗格 |
| `F10` | 退出 |
| `F11` | 录制时加标记（marker） |
| `F12` | 录制时暂停/恢复 |
| 点击窗格 | 切换焦点到命中的窗格 |
| 拖拽分隔线 | 调整左右分屏比例（松手时子进程才 resize） |
| 滚轮 | 程序未启用鼠标模式时宿主滚动该窗格历史 |

### 排障开关

```bash
LEAF_DUMP_FRAME=1 python main.py ...   # 逐帧 dump 左窗格逻辑行到 leaf.dump（仅本地调试，产物不入库）
```

日志写入 `leaf.log`（文件），控制台仅输出 WARNING（避免混入 TUI 画面）。

## asciinema 功能

### rec — 录制

```bash
python main.py rec demo.cast [--pane1 cmd] [--pane2 hx] [选项]
# 等价于：
python main.py --pane1 cmd --pane2 hx --record demo.cast
```

选项：
- `--capture-input` 录制键盘输入（敏感输入也会被记录）
- `--title T` 录制标题
- `--idle-time-limit S` 空闲时间限制（秒）
- `--append` 追加到现有文件
- `--overwrite` 覆盖现有文件
- `--headless` 无头模式（不显示界面，适合自动化/脚本录制）

录制内容为**整屏合成输出**（两个窗格 + 分隔线 + 状态栏，即所见即所得），时间戳按渲染帧记录。`F11` 加标记、`F12` 暂停/恢复。

### play — 回放

```bash
python main.py play demo.cast [--speed 2.0] [--loop] [--idle-time-limit 2] [--pause-on-markers] [--resize]
```

- 支持本地文件与 http(s) URL；自动识别 zstd 压缩
- `space` 暂停/恢复，`.` 单步（暂停时），`]` 跳到下一标记（暂停时），`ctrl+c` 退出

### cat — 拼接

```bash
python main.py cat a.cast b.cast c.cast -o combined.cast
```

按顺序拼接多个录制，时间轴连续，尺寸变化处自动插入 resize 事件。

### convert — 格式转换

```bash
python main.py convert in.cast out.cast [-f {v3,raw,txt,mp4}] [--overwrite]
```

- `v3`：asciicast v3 格式
- `raw`：原始终端输出（`\x1b[8;rows;colst` 头 + 输出数据）
- `txt`：纯文本（经终端模拟去除 ANSI 与颜色）
- `mp4`：视频导出（需 ffmpeg；`--cell-size` 每格像素宽，`--tail` 末帧保持秒数，`--padding` 四周边框像素，`--border-color` 边框颜色 R,G,B 或 RRGGBB；可变帧率，仅终端画面变化时出帧）
- 输入支持 v3/zstd/URL；输出 `-` 表示 stdout（mp4 除外）

## 架构

干净架构四层洋葱模型，依赖方向只允许外层指向内层：

```
domain ← usecases ← adapters ← drivers ← app
```

| 层 | 目录 | 职责 |
|---|---|---|
| 实体层 | `leaf/domain/` | 纯领域规则：领域事件（`events.py`）、ANSI 常量（`ansi.py`）、asciicast v3 格式（`asciicast.py`：v3/raw/txt 编解码、空闲限制、加速）。零 I/O、零框架依赖 |
| 用例层 | `leaf/usecases/` | 应用编排：帧合成与渲染循环（`frame.py`）、输入路由与拖拽状态机（`input.py`）、录制（`recorder.py`）、回放（`player.py`）、cast 操作（`cast_ops.py`：cat/convert）、端口协议（`ports.py`）、程序解析（`launch.py`）。只依赖端口抽象，不接触框架 |
| 接口适配层 | `leaf/adapters/` | 剪贴板（`clipboard.py`）、输出适配（`output.py`：StdoutSink/NullSink）、cast 文件读写（`castfile.py`：zstd/URL/append）、mp4 导出（`mp4_export.py`） |
| 框架与驱动层 | `leaf/drivers/` | pywezterm（`pane.py`：Mux 门面，含 `pane_take_output` 录制缓冲）、Win32 控制台输入（`console.py`）。`import pywezterm` 仅出现在此层 |
| 组合根 | `leaf/app.py` + 根 `main.py` | 依赖组装、线程编排、CLI 入口（子命令分发）、日志 |

## 关键设计

- **pywezterm 隔离**：用例层通过 `ConsolePort`/`MuxPanelPort`/`ClipboardPort`/`OutputSink`/`RecorderPort` 协议（`usecases/ports.py`）依赖窗格与终端，驱动层实现，可替换/可测试。
- **事件驱动渲染**：Mux 的 reader 线程读到输出即置 `render_event`，渲染线程阻塞在事件上重绘（无主循环轮询）；主线程只处理输入事件。渲染限速 `MIN_FRAME=8ms`，鼠标 move 转发节流 `MOVE_INTERVAL=16ms`。
- **增量重绘**：差分由 pywezterm.Mux 承担（Surface 合成 + 脏行驱动），仅重绘变化行；尺寸/分割位置变化时清屏全量重建（`force_full` 收敛帧）。
- **整屏录制**：渲染线程把合成输出（两窗格 + 分隔线 + 状态栏）逐帧 tee 给 Recorder，时间戳按渲染帧记录；恢复录制（F12）时 `force_repaint` 强制全量帧，保证回放连续。
- **Win32 输入归一化**：驱动层 `Console.read_inputs()` 直接返回领域事件（KeyEvent/MouseEvent/ResizeEvent），用例层不接触任何 Win32 结构。
- **录制缓冲**：pywezterm Mux 每个 pane 内置原始输出缓冲（`pane_take_output`），支持按需 drain。

## 测试

```bash
python -m pytest tests/ -v              # 单元测试 + 真实 ConPTY e2e
python scripts/live_selfcheck.py        # 真实 leaf 进程自检（裂行/重复帧）
python scripts/asciinema_selfcheck.py   # asciinema 黑盒自检（headless 录制/回放/转换/拼接）
python scripts/interactive_selfcheck.py # 交互录制黑盒自检（输入→录制→回放）
python scripts/play_selfcheck.py        # play/convert/cat 命令黑盒自检
python scripts/blackbox_full.py         # 全面黑盒测试（所有子命令端到端）
```

- `tests/test_console.py` 控制台事件映射
- `tests/test_input_usecases.py` 输入路由与拖拽状态机（假对象）
- `tests/test_e2e.py` 真实 ConPTY 上双窗格 e2e
- `tests/test_asciicast.py` asciicast v3 格式白盒（编解码往返、cat/convert）
- `tests/test_recording.py` 录制/回放 e2e（真实 ConPTY 输出捕获、输入记录、暂停）

仅支持 Windows（依赖 ConPTY）。
