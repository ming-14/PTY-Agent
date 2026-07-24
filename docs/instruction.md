# pty-agent 开发与使用指南

> 开发指南，面向**开发者**。用户命令参考见 [`命令行交互.md`](命令行交互.md)，架构设计见 [`设计架构.md`](设计架构.md)。

---

## 1. 项目概述

**pty-agent**：通过 PTY 启动交互式程序，对外提供 CLI 接口管理会话（启动/发送/读取/触发）。

**JSON 输出纯度**：JSON 模式（默认）下，stdout 仅输出 JSON，不含任何非 JSON 内容。守护进程启停信息、配置查询、帮助文本、警告等均以 JSON 格式输出（`type` 为 `info`/`config`/`help`/`warning`）。守护进程日志不会泄漏到 stderr。

```
pty-agent/
├── docs/          # 设计文档（架构/规范/命令参考）
├── src/           # 主包（模块化架构：protocol/ client/ daemon/ session/ pty/ auth/ web/ fastscreen/ vnc/）
│   └── session/   # 已拆分为 encoding/ output/ process/ 三个独立子包
├── tests/         # 测试套件
│   ├── conftest.py                   # pytest 配置
│   ├── unit/                         # 单元测试（隔离测试单一模块）
│   ├── integration/                  # 集成测试（多模块协作）
│   ├── e2e/                          # 端到端测试（VNC/TLS/pubkey/resize 等）
│   ├── web/                          # Web 界面测试
│   └── live-environment/             # 实环境测试
├── bin/           # 辅助工具与二进制资源（aichat/cursorlocator/fastscreencore/ultravnc）
├── app.py         # 快捷入口脚本
└── logs/          # 运行时日志目录（daemon.log / client.log）
```

## 2. 开发环境

| 组件 | 要求 |
|------|------|
| Python | 3.8+ |
| Windows | 10+（ConPTY）|
| Unix | 支持 `os.openpty()` |

### 核心依赖

| 包 | 用途 |
|---|------|
| `cryptography>=41.0` | Ed25519 公私钥认证 |
| `fastapi>=0.111` | Web 服务器 |
| `uvicorn[standard]>=0.30` | ASGI 服务器 |
| `starlette>=0.37` | FastAPI 依赖 |
| `websockets>=12.0` | WebSocket 支持 |
| `aiohttp>=3.9` | FastScreen HTTP/WS 服务 |
| `numpy>=1.24` | H.264 帧数据处理 |
| `av>=11.0` | PyAV 视频编码/解码 |

### 可选依赖

| 包 | 用途 |
|---|------|
| `PySide6>=6.0` | 本地 GUI 窗口 |
| `pyte>=0.8` | 终端模拟/屏幕快照 |
| `wcwidth>=0.2` | CJK 字符宽度计算 |
| `scour` | SVG 压缩 |
| `Pillow>=10.0` | PNG/JPG/BMP 渲染 |
| `psutil>=5.9` | 系统 CPU/内存统计 |

```powershell
# 直接运行
python app.py start
python app.py exec myid -c "python -i -u" -t ">>>"
python app.py stop

# 或通过模块方式
python -m src start
python -m src exec myid -c "python -i -u" -t ">>>"
python -m src stop
```

## 3. 架构简述

**不重复设计架构.md**。核心脉络：

```
用户 → CLI (src/__main__.py)
         → Client (client/transport) ─┬── plain TCP → 守护进程 (明文端口, token 认证, SHM 发现)
                                       └── TLS TCP  → 守护进程 (TLS 端口, pubkey 认证, TOFU 验证)
                                            ↓
                                         Session → PTY 后端
                                            ├─ 输出缓冲区 / 触发匹配
                                            ├─ 进程监控（IOCP）/ GUI 窗口检测
                                            ├─ 编码探测 / 终端屏幕快照
                                            └─ 渲染器（GDI/SVG/Pillow → PNG/SVG/文本）
```

详细的分层图、模块职责表、数据流、线程模型、通信协议等全部见 [`设计架构.md`](设计架构.md)。

## 4. 构建与测试

### 4.1 快速验证

```powershell
# 手动集成测试（当前无需构建，纯 Python）
python app.py start
python app.py exec test -c "python -u -i" -t ">>>" --timeout 5
python app.py send test "print(100*100)" -t ">>>"
# 预期: >>> 10000\n>>>
python app.py send test "for i in range(3):\n    print(i)" -t ">>>"
# 预期: >>> 0\n1\n2\n>>>
python app.py kill test
python app.py stop
```

### 4.2 运行测试套件

```powershell
# 全部单元 + 集成测试（pytest）
python -m pytest tests/ -v

# 仅单元测试
python -m pytest tests/unit/ -v

# 仅集成测试
python -m pytest tests/integration/ -v

# 端到端测试
python -m pytest tests/e2e/ -v
```

测试结构详见 [`测试规范.md`](测试规范.md)。
