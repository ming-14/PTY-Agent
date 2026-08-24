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
| `numpy>=1.24` | H.264 帧数据处理 |
| `av>=11.0` | PyAV 视频编码/解码 |
| `tomli` | Python < 3.11 的 TOML 解析（3.11+ 用内置 `tomllib`） |

可选依赖（缺失时自动降级，按需安装）：`wcwidth`（终端字符显示宽度计算）、`scour`（SVG 压缩，`--svg-compression-level` 1/2）、`Pillow`（PNG/JPG/BMP 位图渲染）、`psutil`（Web 系统 CPU/内存统计）。

**可选功能模块**（`src/optional.py` 集中管理，缺失即功能禁用、主流程正常）：Web 界面（`web.toml` 缺失即关闭）、VNC 远程桌面（`bin/ultravnc` 缺失即禁用）、Screenshare 屏幕串流（`bin/fastscreencore` 缺失即禁用）、沙箱（`sandbox.toml` 缺失即关闭）、插件系统（`registry.json` 缺失即禁用）。这些模块经惰性导入网关按需加载，文件可安全移除。

```powershell
git clone <repo-url>
cd pty-agent
pip install -r requirements.txt
```

## 命令概览

| 命令 | 用途 |
|------|------|
| `exec <id> -c "<cmd>"` | 启动会话（执行命令） |
| `send <id> -i "<input>"` | 发送输入到运行中的会话（`-i` 必填；原样发送） |
| `advsend <id> -i "<input>"` | 同 send，但恒启用 JSON + 控制字符转义解码（`{enter}`/方向键等） |
| `read <id>` | 读取会话输出 |
| `list` | 列出所有会话 |
| `status` | 查看守护进程运行状态 |
| `kill <id>` | 终止会话 |
| `events <id>` | 查看会话事件 |
| `start` / `stop` | 手动启停守护进程（`stop` 支持 `--force`） |
| `wait [--timeout <seconds>]` | 恒等待指定秒数 |
| `closewin <id> <hwnd>` | 关闭 GUI 窗口 |
| `mouse <id> <action>` | 发送鼠标动作 |
| `workflow <run\|list\|show\|cancel>` | workflow 脚本编排（YAML 定义，DAG 并行 + 条件/变量/重试，后台执行） |
| `set-default <key> <value>` | 覆盖默认配置（守护进程内存记忆，daemon 重启即清空） |
| `plugin <list\|ls\|attach\|detach\|cmd>` | 插件管理 |
| `file <read\|write\|edit\|grep\|glob\|upload\|download>` | 文件工具（读/写/唯一匹配替换/内容搜索/文件名匹配/上传/下载） |
| `keygen` | 生成 Ed25519 密钥对 |

## 连接方式

daemon 支持三种独立监听器（`daemon.toml [listener]` 段），可同开或只开一个；
客户端用 `client.toml [connection]` 的 `CONNECT_MODE` 选择连接位置。

| 监听器 | 连接方式 `CONNECT_MODE` | 认证 | 默认位置 |
|--------|------------------------|------|----------|
| `basic` | `basic` | 共享密码（密码即 HMAC 密钥；空密码=无认证） | `0.0.0.0:10521`（关闭） |
| `token` | `token` | Token + HMAC（同机 SHM） | `127.0.0.1:10520`（开启） |
| `tls` | `tls` | TLS + Ed25519 / authorized_keys | `0.0.0.0:18767`（关闭） |

## 平台要求

- **Windows**: 10+（ConPTY），推荐 PowerShell
- **Unix**: 支持 `os.openpty()`
- **Python**: 3.8+

## 文档

详细文档见 `docs/`。
