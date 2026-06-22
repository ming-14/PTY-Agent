# pty-agent 开发与使用指南

> 开发指南，面向**开发者**。用户命令参考见 [`命令行交互.md`](命令行交互.md)，架构设计见 [`设计架构.md`](设计架构.md)。

---

## 1. 项目概述

**pty-agent**：通过 PTY 启动交互式程序，对外提供 CLI 接口管理会话（启动/发送/读取/触发）。

```
pty-agent/
├── doc/          # 设计文档（架构/规范/命令参考）
├── src/          # 主包（模块化架构：protocol/ client/ daemon/ session/ pty/）
│   └── session/  # 已拆分为 encoding/ output/ process/ 三个子包
├── test/         # 测试套件
│   ├── conftest.py                   # pytest 配置
│   ├── unit/                         # 单元测试（隔离测试单一模块）
│   ├── integration/                  # 集成测试（多模块协作）
│   └── test_gdb_full/                # GDB 完整端到端集成测试
├── app.py        # 快捷入口脚本
└── logs/         # 运行时日志目录（daemon.log / client.log）
```

## 2. 开发环境

| 组件 | 要求 |
|------|------|
| Python | 3.11+，纯标准库，无第三方依赖 |
| Windows | 10+（ConPTY）|
| Unix | 支持 `os.openpty()` |

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
         → Client (client/transport) — TCP 请求 → 守护进程 (daemon/server + handler)
                                                        → Session 协调器 (session/session)
                                                            ├─ output/buffer       输出缓冲
                                                            ├─ output/trigger      触发匹配
                                                            ├─ output/events       事件管理
                                                            ├─ process/monitor     进程监控
                                                            ├─ encoding/detector   编码探测
                                                            ├─ process/gui         GUI 检测
                                                            ├─ session_threads     后台线程
                                                            └─ PTY 后端 (pty/factory: create_pty)
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
python -m pytest test/ -v

# 仅单元测试
python -m pytest test/unit/ -v

# 仅集成测试
python -m pytest test/integration/ -v

# GDB 完整端到端集成测试（需系统 PATH 中 gdb.exe + test_debug.exe）
python test/test_gdb_full/test_gdb_full.py
```

测试结构详见 [`测试规范.md`](测试规范.md)。
