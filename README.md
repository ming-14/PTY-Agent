# PTY-Agent

命令行交互式程序交互代理。通过伪终端（PTY）与交互式 CLI 程序双向通信，提供 CLI 接口管理会话。

## 快速开始

```powershell
# 启动交互式 Python 会话
python app.py exec py -c "python -u -i" -t ">>>"

# 发送命令并等待提示符
python app.py send py -i "print(100*100)" -t ">>>"

# 读取输出
python app.py read py --lines 10

# 终止会话
python app.py kill py
```

也可通过模块方式运行：`python -m src exec myid -c "python -i -u" -t ">>>"`

## 安装

Python 3.8+，核心依赖：

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
| `tomli` | Python < 3.11 的 TOML 解析（3.11+ 用内置 `tomllib`） |

可选依赖：`PySide6`（GUI）、`pyte`（终端快照）、`wcwidth`（CJK 宽度）、`scour`（SVG 压缩）、`Pillow`（PNG/JPG/BMP）、`psutil`（系统统计）。

```powershell
git clone <repo-url>
cd pty-agent
pip install cryptography tomli
```

## 命令概览

| 命令 | 用途 |
|------|------|
| `exec <id> -c "<cmd>"` | 启动会话（执行命令） |
| `send <id> -i "<input>"` | 发送输入到运行中的会话（`-i` 必填） |
| `read <id>` | 读取会话输出 |
| `list` | 列出所有会话 |
| `kill <id>` | 终止会话 |
| `events <id>` | 查看会话事件 |
| `start` / `stop` | 手动启停守护进程 |
| `closewin <id> <hwnd>` | 关闭 GUI 窗口 |
| `mouse <id> <action>` | 发送鼠标动作 |
| `file <read\|write\|edit\|grep\|glob>` | 文件工具（读/写/唯一匹配替换/内容搜索/文件名匹配） |
| `keygen` | 生成 Ed25519 密钥对 |

## 连接方式

daemon 支持三种独立监听器（`daemon.toml [listener]` 段），可同开或只开一个；
客户端用 `client.toml [connection]` 的 `CONNECT_MODE` 选择连接位置。

| 监听器 | 连接方式 `CONNECT_MODE` | 认证 | 默认位置 |
|--------|------------------------|------|----------|
| `plain` | `plain` | 无认证 | `0.0.0.0:10521`（关闭） |
| `token` | `token` | Token + HMAC（同机 SHM） | `127.0.0.1:10520`（开启） |
| `tls` | `tls` | TLS + Ed25519 / authorized_keys | `0.0.0.0:18767`（关闭） |

## 平台要求

- **Windows**: 10+（ConPTY），推荐 PowerShell
- **Unix**: 支持 `os.openpty()`
- **Python**: 3.8+

## 文档

详细文档见 `docs/`。
