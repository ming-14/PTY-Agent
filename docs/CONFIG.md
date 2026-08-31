# PTY-Agent 配置说明

> 本文档描述 PTY-Agent 的完整配置体系：TOML 配置文件分层、环境变量覆写、运行时默认配置键。
>
> 配套代码：`src/config/`（加载器）、`config/`（TOML 数据文件）。

---

## 配置体系总览

配置采用 **TOML 文件 + 环境变量覆写** 双层机制，按侧分离：

- **共享配置**（`config/` 根目录）— daemon 与 client 两端均需使用
- **daemon 专属**（`config/daemon/`）— 仅守护进程加载
- **client 专属**（`config/client/`）— 仅客户端加载

**优先级：环境变量 > TOML 文件 > 代码默认值**

所有 TOML 配置 key 均可用环境变量覆写，格式为 `PTY_AGENT_<KEY>`（如 `DATA_DIR` → `PTY_AGENT_DATA_DIR`）。
环境变量取值按原值类型转换（bool/int/float/str，list/dict 按 JSON），转换失败时告警并保留文件值，不阻断启动。

运行时常量（`IS_WINDOWS`、`DATA_DIR`、`PROJECT_ROOT`、`LOG_DIR`）由代码计算，不参与环境变量覆写。

---

## 目录结构

```
config/
├── common.toml              # 共享：终端默认尺寸 / 压缩 / 输入限制 / 数据目录
├── shared.toml              # 共享：协议缓冲 / IPC 命名 / daemon 控制超时
├── logging.toml             # 共享：日志格式 / 归档间隔 / 异步队列
├── transfer.toml            # 共享：文件传输帧参数
├── daemon/
│   ├── daemon.toml          # 守护进程：三监听器 / 缓冲 / 超时 / 认证参数
│   ├── logging.toml         # 守护进程：日志级别 / logger 分组
│   ├── web.toml             # Web 服务器 / VNC / FastScreen / 网页端默认值（可选）
│   ├── sandbox.toml         # 沙箱启用 / 配额 / 隔离策略（可选，Windows 专属）
│   ├── vnc.toml             # winvnc.exe 外部配置（Python 不加载）
│   └── vnc.example.toml     # vnc.toml 示例模板
├── client/
│   ├── client.toml          # 客户端：连接方式 / 超时 / 认证参数
│   └── logging.toml         # 客户端日志级别 / logger 注册
├── apikey.env               # aichat 插件密钥环境文件（不入发布包，非本配置体系）
└── plugins/
    ├── registry.json        # 插件系统总开关 + 各插件启用状态（可选）
    └── policy.json          # 插件权限策略（可选）
```

---

## 配置文件逐项说明

### 1. `config/common.toml` — 共有配置

消费方：全项目（daemon 与 client 均加载）

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `DATA_DIR` | `~/.pty-agent` | str | 运行时数据根目录（日志、单实例锁、SHM 回退、插件存储、history.db 等）。支持 `~` 与 `%VAR%`/`$VAR` 展开；空值回落到 `~/.pty-agent` |
| `DEFAULT_COLS` | `80` | int | 终端默认列数 |
| `DEFAULT_ROWS` | `24` | int | 终端默认行数 |
| `GZIP_COMPRESS_LEVEL` | `2` | int | SVG 输出 GZIP 压缩等级 |
| `MAX_SESSION_ID_LEN` | `128` | int | 会话 ID 最大长度（字符） |
| `MAX_COMMAND_LEN` | `65536` | int | exec 命令最大长度（字节） |
| `MAX_INPUT_LEN` | `65536` | int | send/advsend 输入最大长度（字节） |
| `MAX_PATTERN_LEN` | `4096` | int | 正则 trigger 模式最大长度（字符） |

### 2. `config/shared.toml` — 共享配置

消费方：全项目（协议域 / IPC 域 / daemon 控制）

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `SOCKET_RECV_BUFSIZE` | `65536` | int | socket 接收缓冲区大小（字节） |
| `MAX_MESSAGE_LENGTH` | `50331648` | int | 消息总大小上限（48 MB），需大于 workflow 定义文件上限（20MB）经序列化后的体积 |
| `SINGLE_INSTANCE_MUTEX_NAME` | `Local\PTYAgentSingleInstance` | str | 单实例互斥锁命名 |
| `AUTH_TOKEN_NAME` | `Local\PTYAgentAuth` | str | 认证 Token 共享内存命名 |
| `AUTH_TOKEN_SIZE` | `64` | int | 认证 Token 字节长度 |
| `HMAC_KEY_NAME` | `Local\PTYAgentHmac` | str | HMAC 密钥共享内存命名 |
| `HMAC_KEY_SIZE` | `64` | int | HMAC 密钥字节长度 |
| `DAEMON_START_TIMEOUT` | `3.0` | float | daemon 启动等待超时（秒） |
| `DAEMON_START_POLL_INTERVAL` | `0.3` | float | daemon 启动轮询间隔（秒） |
| `PING_TIMEOUT` | `1.0` | float | ping 探测超时（秒） |
| `STOP_TIMEOUT` | `3.0` | float | daemon 停止等待超时（秒） |
| `PROCESS_EXIT_WAIT_RETRIES` | `10` | int | 进程退出等待重试次数 |
| `PROCESS_EXIT_WAIT_INTERVAL` | `0.1` | float | 进程退出等待重试间隔（秒） |

### 3. `config/logging.toml` — 日志共享配置

消费方：全项目（跨侧共享）

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `LOG_FORMAT` | `%(asctime)s.%(msecs)03d [%(levelname)-8s] [%(name)s:%(threadName)s] %(filename)s:%(lineno)d - %(message)s` | str | 日志输出格式 |
| `LOG_DATE_FORMAT` | `%Y-%m-%d %H:%M:%S` | str | 日志时间戳格式 |
| `LOG_ARCHIVE_INTERVAL` | `600` | int | 前一日日志自动 gzip 归档的后台检查间隔（秒） |
| `LOG_QUEUE_SIZE` | `8192` | int | 异步日志队列容量，满时丢弃最旧记录 |

### 4. `config/transfer.toml` — 文件传输配置

消费方：daemon 与 CLI 两端共享

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `TRANSFER_CHUNK_SIZE` | `262144` | int | 传输数据帧大小（256 KB） |
| `TRANSFER_MAX_FILES` | `100000` | int | 单次传输条目数上限（目录树遍历防御） |
| `TRANSFER_MAX_CONTROL` | `16777216` | int | 控制帧 payload 上限（16 MB，清单/计划） |
| `TRANSFER_MAX_SIZE` | `0` | int | 单文件大小上限（0 = 无限制） |
| `TRANSFER_TMP_SUFFIX` | `.pty-tmp` | str | 传输临时文件后缀 |
| `TRANSFER_PROGRESS_INTERVAL` | `60` | int | 非 TTY 强制进度打印间隔（秒） |
| `TRANSFER_TIMEOUT` | `120` | int | `file upload/download` 默认总时限（秒） |

### 5. `config/daemon/daemon.toml` — 守护进程配置

消费方：仅守护进程

#### 顶层

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `SINGLE_INSTANCE` | `true` | bool | 单实例互斥锁开关；false 时仅 basic/tls 监听器场景生效，允许多实例并存 |

#### `[listener]` — 三监听器

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `BASIC_ENABLED` | `false` | bool | 明文监听器启用 |
| `BASIC_HOST` | `0.0.0.0` | str | 明文监听器绑定地址 |
| `BASIC_PORT` | `10521` | int | 明文监听器端口 |
| `BASIC_PASSWORD` | `""` | str | 明文监听器共享密码；空=无认证，非空时同时作为 HMAC 密钥 |
| `TOKEN_ENABLED` | `true` | bool | Token 监听器启用（本机访问） |
| `TOKEN_HOST` | `127.0.0.1` | str | Token 监听器绑定地址 |
| `TOKEN_PORT` | `10520` | int | Token 监听器端口 |
| `TLS_ENABLED` | `false` | bool | TLS 监听器启用（跨机安全访问） |
| `TLS_HOST` | `0.0.0.0` | str | TLS 监听器绑定地址 |
| `TLS_PORT` | `18767` | int | TLS 监听器端口 |

#### `[buffer]` — 缓冲

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `MAX_OUTPUT_BUFFER` | `104857600` | int | 会话输出缓冲上限（100 MB） |
| `MAX_TRIGGER_SCAN` | `1048576` | int | trigger 正则扫描范围（1 MB） |

#### `[timeout]` — 超时

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `DEFAULT_TRIGGER_TIMEOUT` | `120.0` | float | 默认 trigger 等待超时（秒） |

#### `[misc]` — 杂项

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `SOCKET_LISTEN_BACKLOG` | `128` | int | 监听 socket backlog |
| `PTY_READ_SIZE` | `65536` | int | PTY 读取缓冲区大小（字节） |
| `MAX_CONNECTIONS` | `100` | int | 全局并发连接数上限（Slowloris 防护） |
| `CONNECTION_READ_TIMEOUT` | `30.0` | float | 连接读请求超时（秒） |

#### `[named_resource]` — 命名资源

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `JOB_OBJECT_NAME_PREFIX` | `""` | str | Job Object 命名前缀（Windows）；空值使用未命名 Job Object |

#### `[input_limit]` — 输入限制

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `MAX_SESSIONS` | `50` | int | 守护进程最大并发会话数 |

#### `[workflow]` — 工作流编排

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `WORKFLOW_MAX_RUNS` | `50` | int | 运行记录上限，超限自动淘汰最旧终态 |
| `WORKFLOW_DEFAULT_PARALLEL` | `4` | int | 默认最大并行步骤数 |
| `WORKFLOW_STEP_OUTPUT_LIMIT` | `4096` | int | 步骤输出保存上限（字符，仅 show 日志） |
| `WORKFLOW_MAX_FILE_SIZE` | `20971520` | int | 定义文件大小上限（20 MB） |

#### `[auth]` — 认证

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `AUTH_TOKEN_ROTATE_INTERVAL` | `1800` | int | Token 轮换周期（秒） |
| `AUTH_TOKEN_GRACE_PERIOD` | `120` | int | Token 轮换宽限期（秒，旧 token 仍有效） |
| `PUBKEY_ALGORITHM` | `ed25519` | str | 公钥算法 |
| `PUBKEY_AUTHORIZED_KEYS` | `""` | str | 授权公钥文件路径；空=默认 `<DATA_DIR>/authorized_keys` |
| `PUBKEY_KEY_DIR` | `""` | str | 密钥目录；空=默认 `<DATA_DIR>/keys` |
| `TLS_CERT_DIR` | `""` | str | TLS 证书目录；空=默认 `<DATA_DIR>/certs` |
| `TLS_CERT_FILE` | `""` | str | TLS 证书文件路径；空=默认 `<DATA_DIR>/certs/daemon.crt` |
| `TLS_KEY_FILE` | `""` | str | TLS 密钥文件路径；空=默认 `<DATA_DIR>/certs/daemon.key` |
| `TLS_CERT_VALIDITY_DAYS` | `365` | int | 自签证书有效期（天） |
| `TLS_CERT_SUBJECT_CN` | `pty-agent-daemon` | str | 自签证书 Common Name |

### 6. `config/daemon/sandbox.toml` — 沙箱配置（可选）

消费方：仅守护进程（Windows 专属）。**文件缺失时沙箱功能关闭**，不触发 `src/sandbox` 导入。

#### `[sandbox]` — 全局开关

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `enabled` | `false` | bool | 沙箱会话开关；false 时会话走原生 PTY 后端 |
| `log_level` | `info` | str | 沙箱日志级别（trace / debug / info / warn / error） |

#### `[quota]` — 资源配额（0 = 不限制）

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `memory_mb` | `0` | int | 进程内存上限（MB） |
| `cpu_ms` | `0` | int | CPU 时间上限（ms，硬限制） |
| `cpu_rate_percent` | `0` | int | CPU 速率限制（%） |
| `max_processes` | `0` | int | Job 内最大进程数 |
| `wall_clock_timeout_ms` | `0` | int | 墙钟超时（ms） |
| `crash_silent` | `true` | bool | 崩溃静默：不弹窗不触发 WER |

#### `[isolation]` — 隔离策略

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `net_policy` | `unrestricted` | str | 网络策略：`unrestricted`（不限制）/ `allowlist`（仅白名单放行） |
| `net_allowlist` | `[]` | list | 网络白名单规则：`[{ ip, port, protocol }]` |
| `clipboard_isolate` | `false` | bool | 剪贴板隔离（Job UI 限制） |

### 7. `config/daemon/web.toml` — Web 服务器配置（可选）

消费方：仅守护进程 Web 模块。**文件缺失时视为 Web 未启用**（`ENABLE_WEB=False`，连带 VNC/FastScreen 禁用）。

#### `[server]` — Web 服务器

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `ENABLE_WEB` | `true` | bool | Web 服务器启用开关；关闭时自动禁用 VNC 和 FastScreen |
| `WEB_HOST` | `127.0.0.1` | str | Web 服务器监听地址 |
| `WEB_PORT` | `18766` | int | Web 服务器端口 |
| `WEB_PASSWORD_HASH` | `""` | str | Web 密码认证（SHA-256 hex）；空=无认证 |

#### `[vnc]` — VNC 远程桌面

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `ENABLE_VNC` | `true` | bool | VNC 集成开关 |
| `VNC_WINVNC_PATH` | `""` | str | winvnc.exe 路径，空=适配器自行推导 |
| `VNC_MODULE_DIR` | `""` | str | 预留，当前未消费 |

#### `[fastscreen]` — FastScreen 屏幕流

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `ENABLE_FASTSCREEN` | `true` | bool | FastScreen 屏幕流开关 |
| `FASTSCREEN_PACKAGE_DIR` | `""` | str | 预留，当前未消费（适配器自行推导路径） |
| `FASTSCREEN_DEFAULT_FPS` | `30` | int | 默认帧率 |
| `FASTSCREEN_DEFAULT_QUALITY` | `0.8` | float | 预留，当前未消费 |
| `FASTSCREEN_DEFAULT_BITRATE` | `2000000` | int | 默认比特率（bps） |
| `FASTSCREEN_DEFAULT_GOP_SIZE` | `30` | int | 预留，当前未消费 |
| `FASTSCREEN_DEFAULT_METHOD` | `auto` | str | 预留，当前未消费 |
| `FASTSCREEN_DEFAULT_STREAM_FORMAT` | `mse` | str | 传输协议默认值（auto / mjpeg / mse / webcodecs） |

#### `[web_settings]` — 网页端默认值

守护进程启动时读取，作为 `/api/settings` 返回的默认值；用户覆盖项存储在 `~/.pty-agent/web_user_choice.json`，优先级高于此节。

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `RIKKA_ENABLED` | `true` | bool | 获取一只 rikka 桌宠 |
| `DEFAULT_THEME` | `dark` | str | 主题：`light` / `dark` / `system` |
| `IME_ENABLED` | `true` | bool | 启用 Web RIME 输入法 |
| `IME_CANDIDATE_COUNT` | `5` | int | 候选词数量 |
| `IME_VERTICAL` | `false` | bool | 竖排候选 |
| `IME_DEFAULT_STATE` | `chinese` | str | 默认输入状态：`chinese` / `english` / `last` |
| `IME_KEYBOARD_LAYOUT` | `compact` | str | 移动端键盘布局：`compact`（普通）/ `full`（全键） |
| `IME_TOOLBAR_DISPLAY` | `always` | str | 工具栏显示：`never` / `desktop_only` / `always` |
| `IME_TB_OPACITY` | `100` | int | 工具栏透明度（30–100，百分比） |
| `IME_KB_OPACITY` | `100` | int | 键盘透明度（30–100，百分比，仅移动端） |
| `IME_TB_SCALE` | `1.0` | float | 工具栏缩放（0.8 / 1.0 / 1.2 / 1.5） |
| `IME_KB_SCALE` | `1.0` | float | 键盘缩放（0.8 / 1.0 / 1.2 / 1.5，仅移动端） |

### 8. `config/client/client.toml` — 客户端配置

消费方：仅 CLI 客户端

#### `[connection]` — 连接方式

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `CONNECT_MODE` | `token` | str | 客户端连接方式：`basic` / `token` / `tls`，需与 daemon 侧对应监听器匹配 |
| `BASIC_HOST` | `127.0.0.1` | str | 明文监听器地址（`CONNECT_MODE=basic` 时生效） |
| `BASIC_PORT` | `10521` | int | 明文监听器端口 |
| `BASIC_PASSWORD` | `""` | str | 明文监听器共享密码（须与 daemon 侧一致） |
| `TOKEN_HOST` | `127.0.0.1` | str | Token 监听器地址（`CONNECT_MODE=token` 时生效） |
| `TOKEN_PORT` | `10520` | int | Token 监听器端口 |
| `TLS_HOST` | `""` | str | TLS 监听器地址（`CONNECT_MODE=tls` 时生效，远程 daemon） |
| `TLS_PORT` | `18767` | int | TLS 监听器端口 |

#### `[timeout]` — 超时

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `CONNECT_TIMEOUT` | `30.0` | float | 客户端连接 daemon 超时（秒） |
| `DEFAULT_TRIGGER_TIMEOUT` | `120.0` | float | 默认 trigger 等待超时（秒） |

#### `[auth]` — 客户端认证

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `PUBKEY_PRIVATE_KEY_PATH` | `""` | str | 私钥路径；空=默认 `<DATA_DIR>/keys/id_ed25519` |
| `KNOWN_HOSTS_FILE` | `""` | str | known_hosts 文件路径；空=默认 `<DATA_DIR>/known_hosts` |
| `TOFU_STRICT` | `true` | bool | TOFU 严格模式：true=指纹不匹配拒绝，false=仅警告 |

### 9. 日志级别配置

#### `config/daemon/logging.toml`

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `DAEMON_LOG_LEVEL` | `INFO` | str | 守护进程核心日志级别 |
| `WEB_LOG_LEVEL` | `INFO` | str | Web 模块日志级别 |

**Logger 分组：**

| 分组 | 包含的 logger 名 |
|------|-----------------|
| `DAEMON_LOGGERS` | `pty-daemon` |
| `SESSION_LOGGERS` | `pty-session`, `pty-grid`, `pty-grid-screen`, `pty-ipc`, `pty-plugins`, `process-job-tracker`, `process-gui-monitor`, `process-win32-error`, `process-base`, `process-pgid-tracker` |
| `PTY_LOGGERS` | `pty-factory`, `pty-subprocess`, `pty-wezterm`, `pty-windows`, `pty-condrv`, `pty-unix`, `pty-unix-process` |
| `PROTOCOL_LOGGERS` | `pty-protocol` |
| `AUTH_LOGGERS` | `pty-auth`, `pty-auth-tls` |
| `SANDBOX_LOGGERS` | `sandbox-tracker`, `sandbox-pty`, `sandbox-manager` |
| `WEB_LOGGERS` | `pty-web`, `pty-web-settings`, `pty-web-auth`, `pty-vnc` |
| `SCREENSHARE_LOGGERS` | `pty-web-screenshare`, `pty-screenshareservice`, `pty-screenshareservice-encoder`, `screenshare`, `screenshare.manager`, `screenshare.h264_mse`, `screenshare.fmp4`, `screenshare.h264_webcodecs` |

#### `config/client/logging.toml`

| 键 | 默认值 | 类型 | 说明 |
|---|--------|------|------|
| `CLIENT_LOG_LEVEL` | `DEBUG` | str | 客户端日志级别 |

**Logger 注册：** `CLIENT_LOGGERS` = `["pty-client", "pty-daemonctl", "pty-auth", "pty-ipc", "pty-auth-tls", "pty-plugins"]`

---

## 运行时默认配置键（`set-default` / `--default`）

运行时可通过 `set-default` 命令或 `--default` 选项覆盖的键，存于守护进程内存（daemon 重启即清空）：

| 键 | 默认值 | 类型 | CLI 格式 | 说明 |
|---|--------|------|---------|------|
| `timeout` | `120.0` | float | `--timeout` | 默认 trigger 等待超时（秒） |
| `newline` | `false` | bool | `--newline` | 换行后开始检查 trigger |
| `keep_ansi` | `false` | bool | `--keep-ansi` | 保留完整 VT 序列 |
| `encoding` | `null` | str/None | `--encoding` | 终端编码（auto / utf-8 / gbk 等） |
| `debug` | `false` | bool | `--debug-output` | 启用调试输出 |
| `send_eol` | `\r` | str | `--send-eol` | 行尾符（`lf` / `crlf` / `cr` / `none`） |
| `response_format` | `stream` | str | `--response-format` | 响应格式（`stream` / `svg`） |
| `svg_compression_level` | `1` | int | `--svg-compression-level` | SVG 压缩等级（0 / 1 / 2） |
| `terminal_size` | `80x24` | str | `--default terminal-size` | 终端尺寸 WxH（20–500 × 5–200） |
| `shell` | `null` | str/None | `--shell` | Shell 包装（仅 `set-default` 可设，`--default` 不支持） |

---

## 特殊环境变量

| 环境变量 | 用途 |
|---------|------|
| `PTY_AGENT_CONFIG_DIR` | 测试隔离：重定向配置目录（e2e 测试用临时目录），不污染生产配置 |
| `PTY_PLUGIN_DIRS` | 插件目录追加（`os.pathsep` 分隔），扫描含 `plugin.json` 的目录 |

---

## 三监听器架构

daemon 支持三种独立监听器，各自配置监听位置，可同开或只开一个：

| 监听器 | 传输 | 认证 | 默认位置 | 典型场景 |
|--------|------|------|----------|----------|
| `basic` | 明文 | 共享密码（密码即 HMAC 密钥，空=无认证） | `0.0.0.0:10521`（默认关闭） | 内网/受信网络直连 |
| `token` | 明文 | Token + HMAC（SHM 同机分发） | `127.0.0.1:10520`（默认开启） | 本机同机访问 |
| `tls` | TLS | Ed25519 公私钥 + TOFU | `0.0.0.0:18767`（默认关闭） | 跨机安全访问 |

- daemon 侧 `daemon.toml [listener]` 逐段配置 `ENABLED` / `HOST` / `PORT`
- client 侧 `client.toml [connection]` 用 `CONNECT_MODE` 选择连接哪个监听器
- 客户端连接方式必须与 daemon 已启用的监听器匹配

---

## 配置加载规则

1. **加载顺序**：`build_config` 装配 common → shared → logging → 侧专属额外配置 → 环境变量覆写
2. **同名冲突**：`flatten` 与 `merge` 对同名 key 直接抛出 `ValueError`，防止静默覆盖
3. **路径展开**：`~` / `%VAR%` / `$VAR` 统一展开，`normpath` 归一化分隔符
4. **可选文件**：`web.toml` 和 `sandbox.toml` 缺失时不影响主流程启动，对应功能自动关闭
5. **加载即定值**：常量在模块 import 时固化，修改 TOML 后需重启进程生效