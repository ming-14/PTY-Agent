# PTY-Agent

命令行交互式程序交互代理。通过 subprocess 或伪终端（PTY）与交互式 CLI 程序双向通信，提供 CLI 接口管理会话。

## 快速开始

```powershell
# 启动交互式 Python 会话
python app.py exec py -c "python -u -i" -t ">>>"

# 发送命令并等待提示符
python app.py send py "print(100*100)" -t ">>>"

# 读取输出
python app.py read py --lines 10

# 查看会话事件
python app.py events py --last 5

# 终止会话
python app.py kill py
```

## 安装

无第三方依赖，Python 3.11+ 标准库。

```powershell
git clone <repo-url>
cd pty-agent
```

## 命令概览

| 命令 | 用途 |
|------|------|
| `exec <id> -c "<cmd>"` | 启动会话（执行命令） |
| `send <id> "<input>"` | 发送输入到运行中的会话 |
| `read <id>` | 读取会话输出 |
| `list` | 列出所有会话 |
| `kill <id>` | 终止会话 |
| `events <id>` | 查看会话事件 |
| `start` / `stop` | 手动启停守护进程 |
| `closewin <id> <hwnd>` | 关闭 GUI 窗口 |

## 核心特性

- **触发返回机制**：`--trigger/-t` 指定正则，匹配到特定输出后立即返回，无需固定等待
- **静默超时**：`--idle-timeout` 在程序持续无输出时触发返回
- **进程崩溃检测**：实时感知崩溃事件（Windows Job Object IOCP）
- **GUI 窗口检测**：自动检测子进程弹出的 GUI 窗口
- **编码自动探测**：支持 UTF-8/GBK/GB2312/GB18030/Big5 等编码
- **多 shell 支持**：`--shell cmd/powershell/pwsh/bash`
- **伪终端模式**：`--pty` 启用完整 PTY（适用于需要 TTY 的程序）
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
python app.py send myid "print(1)" -t ">>>"
python app.py send myid "c" --timeout 10                # 无触发条件，等待超时返回
python app.py send myid "import os\nprint(os.name)" -t ">>>" --json-escaping
```

### read — 读取输出

```powershell
python app.py read myid --lines 20          # 最近 20 行
python app.py read myid --grep "ERROR"      # 正则过滤
python app.py read myid --offset 1024       # 增量读取
```

### events — 查看事件

```powershell
python app.py events myid --last 10
python app.py events myid --since "14:30"
python app.py events myid --since "2026-06-22T14:00:00" --until "2026-06-22T15:00:00"
```

## 项目结构

```
pty-agent/
├── app.py                 # 快捷入口
├── src/
│   ├── __main__.py        # CLI 入口（参数解析 + 命令派发）
│   ├── config.py          # 配置常量
│   ├── protocol/          # 通信协议（JSON 行编解码 + ANSI 过滤）
│   ├── client/            # 前端客户端（TCP 连接 + 格式化输出）
│   ├── daemon/            # 守护进程（TCP 服务器 + 请求处理）
│   ├── pty/               # 伪终端后端（Unix/Windows ConPTY/Subprocess）
│   └── session/           # 会话管理（缓冲区 + 触发 + 事件 + 进程监控）
├── test/                  # 单元测试 + 集成测试
├── doc/                   # 设计文档
└── SKILL.md               # AI 技能描述
```

## 架构

```
用户 → CLI → Client (TCP) → 守护进程 → Session → PTY 后端
                                                    ├─ 输出缓冲区
                                                    ├─ 触发匹配
                                                    ├─ 进程监控（IOCP）
                                                    ├─ GUI 窗口检测
                                                    └─ 编码探测
```

详细架构设计见 [`doc/设计架构.md`](doc/设计架构.md)。

## 测试

```powershell
python -m pytest test/ -v
python -m pytest test/unit/ -v
python -m pytest test/integration/ -v
```

## 平台要求

- **Windows**: 10+（ConPTY），推荐 PowerShell
- **Unix**: 支持 `os.openpty()`
- **Python**: 3.11+，纯标准库
