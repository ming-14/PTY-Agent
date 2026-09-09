# PTY-Agent

命令行交互式程序交互代理。通过 subprocess 管道或真实终端（TTY）与交互式 CLI 程序双向通信，提供 CLI 接口管理会话。

> 2026-09 重构：一次性大重构，无兼容残留。通信层统一为共享内存请求门面（`protocol/request.py`）；
> 后端拆分为 **subprocess（纯管道，默认）** 与 **pty（真实终端 + pyte 终端仿真）** 两种互不回退的模式；
> 客户端与守护进程仅共享 `protocol/` 层；呈现与传输解耦（`client/presenters`）。
> `send` 的输入文本统一由 `-i/--input` 选项给出，已删除位置参数写法。

## 快速开始

```powershell
# 启动交互式 Python 会话（默认 subprocess 管道模式）
python app.py exec py -c "python -u -i" -t ">>>"

# 发送命令并等待提示符
python app.py send py -i "print(100*100)" -t ">>>"

# 读取输出（subprocess 模式默认返回完整缓冲）
python app.py read py --full

# 移除会话
python app.py remove py
```

## 安装

Python 3.11+，运行时依赖 `pyte`（真实终端模式的终端仿真解析），测试依赖 `pytest`。

```powershell
pip install -r requirements.txt
```

## 命令概览

| 命令 | 用途 |
|------|------|
| `exec <id> -c "<cmd>"` | 启动会话（`--pty` 启用真实终端，默认 subprocess 管道） |
| `send <id> -i "<input>"` | 发送输入到运行中的会话 |
| `read <id>` | 读取会话输出 |
| `list` | 列出所有会话 |
| `remove <id>` | 移除会话 |
| `start` / `stop` | 手动启停守护进程 |
| `closewin <id> <hwnd>` | 关闭 GUI 窗口 |

## 后端两种模式

| 模式 | 选择 | 后端 | 输出模型 |
|------|------|------|----------|
| **subprocess**（默认） | 不传 `--pty` | `subprocess.Popen` stdin/stdout/stderr 管道 | 原始文本流，原样透传 |
| **pty** | `--pty` | Windows ConPTY / Unix openpty | pyte 终端仿真：滚动历史 + 可见屏幕 |

- **subprocess**：纯管道进程，不涉及终端概念。支持 `--shell`（cmd/powershell/pwsh/bash），字符串命令可经 shell 执行。
- **pty**：命令自动拆为列表执行（无 shell 语法，含操作符时需 `--force-pty-mode`），创建失败**直接报错，不回退 subprocess**。

## read 语义（重构后）

- **pty 模式**：默认返回**可见屏幕**；`--full` = 全量输出（滚动历史 + 可见屏幕）；`--lines N` / `--lines start:end`、`--grep <regex>` 基于全量输出行处理。
- **subprocess 模式**：无可见屏幕概念，默认返回**完整缓冲**（同 `--full`）；`--lines`/`--grep` 基于完整缓冲。
- **`--offset` 增量读取已删除**。触发返回的"增量输出"由内部游标控制（CLI 不暴露 offset）。

## 核心特性

- **触发返回机制**：`--trigger/-t` 指定正则，匹配到特定输出后立即返回（pty 模式在渲染文本上匹配）
- **静默超时**：`--idle-timeout` 在程序持续无输出时触发返回
- **进程崩溃检测**：实时感知崩溃事件（Windows Job Object IOCP）
- **GUI 窗口检测**：WinEvent hook 事件驱动（毫秒级，EnumWindows 兜底扫描），与 `-t` **平级**参与等待竞争，谁先命中谁先返回（仅 Windows）
- **真实终端解析**：pty 模式使用 pyte 终端仿真（光标/清屏/滚动均正确渲染）
- **配置临时覆盖**：`--default timeout 30` 临时修改默认配置

## 详细用法

### exec — 启动会话

```powershell
python app.py exec myid -c "python -u -i" -t ">>>" --timeout 30
python app.py exec build -c "nmake" --idle-timeout 5
python app.py exec gdb -c "gdb -q test.exe" -t "(gdb)" --pty
```

### send — 发送输入

```powershell
python app.py send myid -i "print(1)" -t ">>>"
python app.py send myid -i "c" --timeout 10                  # 无触发条件，等待超时返回
python app.py send myid -i "print(2)" -t ">>>" --send-eol crlf
python app.py send myid -i "" --timeout 3                    # 空输入：只提交一个行尾
```

- 输入文本**必须**由 `-i/--input` 给出（无位置参数写法）：`send myid "print(1)"` 直接报错。
- 内容以 `-` 开头时用 `=` 形式：`--input="--help"` 或 `-i="--help"`。

### read — 读取输出

```powershell
python app.py read myid                    # pty: 可见屏幕 / subprocess: 完整缓冲
python app.py read myid --full             # 全量输出
python app.py read myid --lines 5          # 全量最后 5 行
python app.py read myid --lines 10:20      # 全量第 10~20 行
python app.py read myid --grep "ERROR"     # 全量正则过滤
```

## 项目结构

```
pty-agent/
├── app.py                 # 快捷入口
├── src/
│   ├── __main__.py        # CLI 入口（参数解析 + 呈现派发）
│   ├── config.py          # 配置常量
│   ├── protocol/          # 通信协议层（统一封装，client/daemon 唯一共享层）
│   │   ├── shm.py         # 共享内存信箱/通道/守护进程信息区
│   │   ├── request.py     # roundtrip() 统一共享内存请求门面
│   │   ├── auth.py        # 认证令牌
│   │   ├── daemon_utils.py# 守护进程存活检测
│   │   ├── message.py     # JSON 消息编解码
│   │   └── shm_utils.py   # 跨平台 mmap 原语
│   ├── client/            # 前端客户端层（与 daemon 完全解耦）
│   │   ├── api.py         # PtyClient 门面（构建请求 → roundtrip → 结构化 dict，不呈现）
│   │   ├── controller.py  # 守护进程 start/stop/is_running
│   │   ├── config_manager.py  # --default 临时配置覆盖
│   │   ├── input.py       # 输入文本处理
│   │   └── presenters/    # 呈现层（cli.py 自然语言；MCP 契约位）
│   ├── daemon/            # 守护进程层（信箱服务器 + 请求处理）
│   │   ├── server.py      # 信箱轮询主循环 + 令牌轮换
│   │   ├── handler.py     # RequestHandler（业务协议）
│   │   └── lifecycle.py   # 守护进程入口 + 日志
│   ├── backend/           # 运行后端（subprocess 与 pty 分离、无回退）
│   │   ├── subprocess.py  # SubprocessBackend（纯管道子进程）
│   │   ├── tty.py         # WinTtyBackend / UnixTtyBackend（真实终端）
│   │   ├── factory.py     # create_subprocess / create_tty 显式入口
│   │   ├── base.py        # Backend 抽象 + ProcessEvent
│   │   ├── windows/       # ConPTY + Job Object + GUI 检测
│   │   └── unix/          # openpty + /proc 进程树
│   └── session/           # 会话管理（文本行输出缓冲 + 输出管线）
│       ├── manager.py     # SessionManager
│       ├── session.py     # Session 协调器（模式/管线/游标）
│       ├── wake.py        # WakeSignal 多路唤醒锚（GUI/触发/崩溃/退出同级响应）
│       ├── session_threads.py # 读者/监控线程
│       ├── output/        # buffer(文本行级) / screen(pyte 滚动屏) / pipeline(双管线) / trigger / events
│       ├── encoding/      # UTF-8 解码（subprocess 流）
│       └── process/       # 进程监控 / GUI 检测
├── test/                  # 单元测试 + 集成测试
├── docs/                  # 设计文档
└── SKILL.md               # AI 技能描述
```

## 架构

```
用户 → CLI (__main__) → client.api.PtyClient → protocol.request(共享内存) → 守护进程 → Session → Backend
                                                                              ├─ subprocess：文本流管线
                                                                              └─ pty：pyte 屏幕管线
CLI 呈现：client.presenters.cli（自然语言）；未来 MCP 可复用同一 PtyClient + 独立呈现器
```

通信基于**命名共享内存**（Windows mmap / Unix 文件 mmap），无 socket、无端口、无锁文件。
客户端通过 `protocol/request.py` 的 `roundtrip()` 统一入口与守护进程通信；
单实例检测基于共享内存中的 PID + 心跳时间戳；认证令牌 30 分钟轮换。

详细架构设计见 [`docs/设计架构.md`](docs/设计架构.md)。

## 测试

```powershell
python -m pytest test/unit/ -v
python -m pytest test/integration/ -v
python -m pytest test/ -v
```

## 平台要求

- **Windows**: 10+（ConPTY），推荐 PowerShell
- **Unix**: 支持 `os.openpty()`
- **Python**: 3.11+；运行时依赖 pyte