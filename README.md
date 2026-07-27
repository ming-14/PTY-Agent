# PTY-Agent

命令行交互式程序交互代理。通过伪终端（PTY）与交互式 CLI 程序双向通信，提供 CLI 接口管理会话。

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

Python 3.8+，依赖如下：

- `cryptography`（Ed25519 公私钥认证必需）
- `tomli`（Python < 3.11 的 TOML 解析；Python 3.11+ 使用内置 `tomllib`）
- Pillow（可选，PNG/JPG/BMP 渲染）
- wcwidth（可选，CJK 字符宽度计算）
- pyte（可选，终端屏幕快照）

```powershell
git clone <repo-url>
cd pty-agent
pip install cryptography tomli
```

## 命令概览

| 命令 | 用途 |
|------|------|
| `exec <id> -c "<cmd>"` | 启动会话（执行命令），支持 `--ai-analyse` |
| `send <id> "<input>"` | 发送输入到运行中的会话，支持 `--ai-analyse` |
| `read <id>` | 读取会话输出，支持 `--ai-analyse` |
| `list` | 列出所有会话 |
| `kill <id>` | 终止会话 |
| `events <id>` | 查看会话事件 |
| `start` / `stop` | 手动启停守护进程 |
| `closewin <id> <hwnd>` | 关闭 GUI 窗口 |
| `mouse <id> <action>` | 发送鼠标动作，支持 `--ai-analyse` |
| `keygen` | 生成 Ed25519 密钥对（公私钥认证用） |

## 核心特性

- **触发返回机制**：`--trigger/-t` 指定正则，匹配到特定输出后立即返回，无需固定等待
- **静默超时**：`--idle-timeout` 在程序持续无输出时触发返回
- **进程崩溃检测**：实时感知崩溃事件（Windows Job Object IOCP）
- **GUI 窗口检测**：自动检测子进程弹出的 GUI 窗口
- **编码自动探测**：支持 UTF-8/GBK/GB2312/GB18030/Big5 等编码
- **配置临时覆盖**：`--default timeout 30` 临时修改默认配置，支持多个 `--default`（如 `--default always-return-snapshot on --default response-format svg`）
- **send 行尾符可配置**：`--default send-eol cr/lf/crlf/none` 控制 send 末尾追加的行尾符（默认 `cr`，模拟终端 Enter 键）
- **子进程环境变量**：`--env KEY=VALUE` 为子进程设置额外环境变量
- **终端屏幕快照**：`read --snapshot` 返回用户真正看到的终端界面文本（基于 pyte 解析 VT 序列）
- **快照模式**：`exec --snapshot-mode` 禁用 trigger/idle-timeout，所有输出返回终端屏幕快照，适合 TUI 程序交互
- **send 快照**：`send --snapshot` 返回终端屏幕快照而非原始 VT 序列（非 snapshot-mode 会话也可用）
- **响应格式选择**：`--response-format <stream|svg>` 选择响应格式（svg 需 PTY 会话的屏幕缓冲区）
- **SVG 压缩等级**：`--svg-compression-level <0|1|2>` SVG 压缩等级（0=不压缩; 1=轻度; 2=深度，默认）
- **快照差异**：`--snapshot-diff/-s` 仅返回屏幕变化的行（需快照模式，stream 格式，格式为 `行号:内容`）
- **文件输出**：`exec/read/send --output/-o <path>` 将输出渲染到文件（.svg 零依赖矢量图；.png/.jpg/.bmp 需 Pillow，Windows 下使用 GDI+BuiltinGlyphs 几何图元渲染消除字符间隙；.txt/其他纯文本在非 snapshot 模式下直接写入 outputStream，不是屏幕快照）
- **配置持久化**：`--default` 设置的值按 session 持久化到守护进程，后续对该 session 的调用自动读取
- **HMAC 签名验证**：守护进程与客户端之间所有通信经 HMAC-SHA256 签名验证，防篡改
- **Ed25519 公私钥认证**：基于 Ed25519 非对称签名，支持跨机部署，服务端 authorized_keys 白名单验签
- **TLS 传输层**：跨机 TLS 连接，自签证书自动生成，TOFU（Trust On First Use）证书验证，SSH 风格 known_hosts 信任存储
- **双端口架构**：明文端口（token 认证，SHM 同机发现）+ TLS 端口（pubkey 认证，跨机访问），两者可同时运行
- **跨机部署**：配置分离（daemon.toml / client.toml），守护进程与客户端可部署在不同机器
- **keygen 密钥生成**：`python -m src keygen` 生成 OpenSSH 兼容的 Ed25519 密钥对
- **screenBuffer 传输优化**：按需返回 + 稀疏表示 + gzip 压缩，典型 94KB 数据压缩至 <1KB
- **AI 分析**：`--ai-analyse <fileOutput|responseOutput>` 将响应输出交给 aichat 做二次分析，用 AI 结果覆盖 outputStream；`--ai-prompt` 自定义分析提示词；`--default ai-analyse`/`--default ai-prompt` 持久化默认值；会话按 uid 续聊

## 认证模式

| 模式 | 配置 | 说明 |
|------|------|------|
| Token + HMAC | `ENABLE_TOKEN_AUTH=true`, `CLIENT_AUTH_METHOD=token` | 默认模式，同机共享密钥，SHM 发现 |
| Ed25519 公私钥 | `ENABLE_PUBKEY_AUTH=true`, `CLIENT_AUTH_METHOD=pubkey` | 跨机 TLS，authorized_keys 白名单 |
| 双端口 | 两者都开 | 明文端口(token) + TLS 端口(pubkey) 同时运行 |
| 无认证 | 两者都关 | 仅本地调试 |

### 密钥生成

```powershell
# 生成 Ed25519 密钥对
python -m src keygen

# 指定密钥目录
python -m src keygen --key-dir ~/.pty-agent/keys

# 覆盖已存在密钥
python -m src keygen --force
```

生成的公钥需追加到服务端 `~/.pty-agent/authorized_keys` 文件。

### 跨机部署配置

守护进程端 (`~/.pty-agent/` 或项目 `src/config/daemon.toml`):
- TLS 服务端配置在 `daemon.toml [auth]` 段

客户端端 (`src/config/client.toml`):
- `DAEMON_REMOTE_HOST` 设为远程 daemon 地址
- `DAEMON_REMOTE_PORT` 设为远程 TLS 端口
- `KNOWN_HOSTS_FILE` 设为 TOFU 信任存储路径
- `TOFU_STRICT` 控制指纹不匹配时的行为（true=拒绝，false=警告）

## 详细用法

### exec — 启动会话

```powershell
python app.py exec myid -c "python -u -i" -t ">>>" --timeout 30
python app.py exec build -c "nmake" --idle-timeout 5
python app.py exec gdb -c "gdb -q test.exe" -t "(gdb)"
python app.py exec vim -c "vim" --env TERM=xterm-256color --env COLORTERM=truecolor
python app.py exec mimo -c "mimo.exe" --snapshot-mode --timeout 5  # TUI 程序快照模式
python app.py exec vim -c "vim" --output screen.svg               # 输出终端快照为 SVG
python app.py exec vim -c "vim" --snapshot-mode --response-format svg --timeout 3  # SVG 格式响应
python app.py read myid --snapshot -o output.png                        # 快照渲染为 PNG（需 Pillow）
python app.py exec myid -c "ls -la" --ai-analyse responseOutput          # AI 分析输出
python app.py exec myid -c "ls -la" -o out.txt --ai-analyse fileOutput   # AI 读 -o 文件分析
```

### send — 发送输入

```powershell
python app.py send myid "print(1)" -t ">>>"
python app.py send myid "c" --timeout 10                # 无触发条件，等待超时返回
python app.py send myid "import os\nprint(os.name)" -t ">>>" -j  # -j 即 --json-escaping
python app.py send myid "{down}" -j -e none              # TUI 方向键，不追加换行
python app.py send myid "{ctrl+c}" -e none               # 发送 Ctrl+C
```

`-j`/`--json-escaping` 同时启用 JSON 转义（`\n`、`\t`、`\uXXXX` 等）与控制字符转义（`{ctrl+a}`、`{enter}`、`{up}`、`{f1}` 等）。字面量 `{`/`}` 用反引号转义：`` `{ ``、` `} ``。

### read — 读取输出

```powershell
python app.py read myid -l 20              # 最近 20 行
python app.py read myid -g "ERROR"         # 正则过滤
python app.py read myid --offset 1024      # 增量读取
python app.py read myid --snapshot         # 终端屏幕快照（用户真正看到的界面）
python app.py read myid --snapshot -o screen.svg  # 快照渲染为 SVG 文件
python app.py read myid                    # snapshot-mode 会话下自动返回快照
```

### events — 查看事件

```powershell
python app.py events myid -l 10
python app.py events myid --since "14:30"
python app.py events myid --since "2026-06-22T14:00:00" --until "2026-06-22T15:00:00"
```

## 项目结构

```
pty-agent/
├── app.py                 # 快捷入口
├── src/
│   ├── __main__.py        # CLI 入口（参数解析 + 命令派发）
│   ├── config/            # 配置中心（TOML 文件：common/daemon/client/web/logging）
│   ├── protocol/          # 通信协议（JSON 行编解码 + ANSI 过滤 + Response）
│   ├── auth/              # 认证层（token/ + pubkey/ + tls/ + 共享基础设施）
│   ├── client/            # 前端客户端（TCP/TLS 连接 + 格式化 + GDI/SVG 渲染 + TOFU + AI 分析）
│   ├── daemon/            # 守护进程（双端口 TCP 服务器 + Listener + 请求处理 + 生命周期）
│   ├── ipc/               # 共享内存 IPC（端口/令牌/HMAC 密钥传递）
│   ├── pty/               # 伪终端后端（Unix/Windows ConPTY）
│   ├── session/           # 会话管理（Session 协调器 + 线程管理）
│   ├── terminal/          # 终端屏幕（pyte VT 解析 → 字符网格 → 快照）
│   ├── encoding/          # 编码探测与解码
│   ├── output/            # 输出缓冲区 + 触发匹配 + 事件历史
│   ├── process/           # 进程监控 + GUI 检测
│   ├── input/             # 输入拦截 + 鼠标处理
│   ├── web/               # Web 界面（干净架构：application/domain/infrastructure）
│   ├── fastscreen/        # FastScreen 屏幕捕获与串流
│   └── vnc/               # VNC 远程桌面集成
├── tests/                 # 单元测试 + 集成测试 + e2e 测试
├── docs/                  # 设计文档
└── SKILL.md               # AI 技能描述
```

## 架构

```
用户 → CLI → Client ──┬── plain TCP → 守护进程 (明文端口, token 认证, SHM 发现)
                      └── TLS TCP  → 守护进程 (TLS 端口, pubkey 认证, TOFU 验证)
                                        ↓
                                     Session → PTY 后端
                                        ├─ 输出缓冲区 / 触发匹配
                                        ├─ 进程监控（IOCP）/ GUI 窗口检测
                                        ├─ 编码探测 / 终端屏幕快照
                                        └─ 渲染器（GDI/SVG/Pillow → PNG/SVG/文本）
```

详细架构设计见 [`docs/设计架构.md`](docs/设计架构.md)。

## 测试

```powershell
python -m pytest tests/ -v
python -m pytest tests/unit/ -v
python -m pytest tests/integration/ -v
python -m pytest tests/e2e/ -v
```

## 平台要求

- **Windows**: 10+（ConPTY），推荐 PowerShell
- **Unix**: 支持 `os.openpty()`
- **Python**: 3.8+，必需依赖 `cryptography`/`tomli`（Pillow 为可选依赖，用于 PNG/JPG/BMP 渲染；wcwidth 为可选依赖，用于 CJK 字符宽度计算，pyte 已间接安装）
