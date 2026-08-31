# config/ — 配置数据目录

TOML 配置数据文件，与 `src/` 平级。加载器代码在 `src/config/`（Python 侧），加载规则与归档见 [src/config/__init__.py](../src/config/__init__.py)。

配置按侧分离：**共享配置**（两端都读）留在本目录根，**daemon 专属**在 `daemon/` 子目录，**client 专属**在 `client/` 子目录。

## 三监听器架构

daemon 支持三种独立监听器，各自配置监听位置（host/port），可同开或只开一个：

| 监听器 | 传输 | 认证 | 位置（默认） | 典型场景 |
|--------|------|------|--------------|----------|
| `basic` | 明文 | 共享密码（密码即 HMAC 密钥，空密码=无认证） | `0.0.0.0:10521`（默认关闭） | 内网/受信网络直连 |
| `token` | 明文 | Token + HMAC（SHM 同机分发） | `127.0.0.1:10520`（默认开启） | 本机同机访问 |
| `tls` | TLS | Ed25519 公私钥 + TOFU | `0.0.0.0:18767`（默认关闭） | 跨机安全访问 |

- daemon 侧 `daemon.toml [listener]` 段逐段配置 `ENABLED` / `HOST` / `PORT`
- client 侧 `client.toml [connection]` 段用 `CONNECT_MODE` 选择连接哪个监听器
- 客户端连接方式必须与 daemon 已启用的监听器匹配；token 监听器启用时经 SHM 发布凭据并周期轮换
- `SINGLE_INSTANCE`（daemon.toml 顶层，默认 true）：单实例互斥锁开关；false 时仅 basic/tls 监听器场景生效（token 监听器启用时 CLI 依赖互斥锁做发现，强制保留锁），允许多实例并存

## 目录结构

```
config/
├── common.toml          # 共享：终端 / 压缩 / 输入限制 / 数据目录（DATA_DIR）
├── shared.toml          # 共享：协议 / IPC 命名 / daemon 控制 / 日志格式
├── transfer.toml        # 共享：传输协议帧参数
├── daemon/
│   ├── daemon.toml      # 守护进程：三监听器（[listener]）/ 缓冲 / 超时 / 认证参数
│   ├── logging.toml     # 守护进程：日志级别 / logger 分组 / 归档间隔
│   ├── web.toml         # 守护进程：Web / VNC 开关 / fastscreen / 网页端默认值（可选，缺失即 web 关闭）
│   ├── sandbox.toml     # 守护进程：沙箱启用 / 配额 / 隔离策略（可选，缺失即沙箱关闭）
│   ├── vnc.toml         # winvnc.exe 外部配置（Python 不加载）
│   └── vnc.example.toml # vnc.toml 示例模板
├── client/
│   ├── client.toml      # 客户端：连接方式（[connection]）/ 连接超时 / 客户端认证参数 / 日志
│   └── (各模式监听位置见 [connection] 段)
├── plugins/             # 插件实现目录（含 plugin.json 清单 + registry.json 状态）
│   └── registry.json     # 插件系统总开关 + 各插件启用状态（可选，缺失即插件系统禁用）
└── README.md
```

## 配置域清单

**由 `src/config/_loader.py` 加载（Python 配置体系）：**

| 文件 | 加载模块 | 消费方 | 说明 |
|------|----------|--------|------|
| `common.toml` | `common.py` | 全项目 | 终端默认值 / 压缩 / 输入限制 / 数据目录（`DATA_DIR`，日志/凭据/插件/历史库的根目录） |
| `shared.toml` | `shared.py`（合并） | 全项目 | 协议缓冲 / IPC 命名 / daemon 控制超时 / 日志格式 |
| `daemon/daemon.toml` | `daemon.py`（合并） | 守护进程 | 三监听器（`[listener]`：basic/token/tls，含 `BASIC_PASSWORD`）/ 缓冲 / 触发超时 / backlog / 命名资源 / 认证参数（轮换周期、授权公钥、TLS 证书） |
| `daemon/logging.toml` | `daemon.py`（合并） | 守护进程 | 日志级别 / logger 分组 / 归档间隔 |
| `daemon/web.toml` | `daemon.py`（合并） | Web（daemon 侧） | 监听 / 密码认证 / VNC 集成开关（`[vnc]` 节）/ fastscreen / 网页端默认值。**可选**：缺失时视为 web 关闭（`ENABLE_WEB=False`，连带 VNC/FastScreen 禁用） |
| `daemon/sandbox.toml` | `sandbox.py` | 沙箱（Windows） | 沙箱启用与配额 / 隔离策略（仅 [`sandbox`] 节）。**可选**：缺失即沙箱关闭 |
| `client/client.toml` | `client.py`（合并） | 客户端 | 连接方式（`[connection]`：CONNECT_MODE + 各模式监听位置，含 `BASIC_PASSWORD`）/ 连接超时 / 客户端认证参数（私钥/TOFU）/ 客户端日志 |
| `transfer.toml` | `transfer.py` | 传输协议（daemon/CLI 两端） | 数据帧大小 / 控制帧上限 / 条目上限 / 超时 / tmp 后缀 |

文件工具为内置功能（`src/files/`），运行参数由 `src/files/settings.py` 默认值提供，
不进核心配置目录。

**不经过 Python 加载（外部工具配置）：**

| 文件 | 消费方 | 说明 |
|------|--------|------|
| `daemon/vnc.toml` | winvnc.exe（部署参考） | VNC 运行时参数（端口 / 密码 / 日志）。Python 侧 VNC 开关在 `daemon/web.toml [vnc]` 节 |
| `daemon/vnc.example.toml` | 人工 | `vnc.toml` 的示例模板 |

## 环境变量覆写

所有 TOML 配置 key 均可用环境变量覆写，**优先级：环境变量 > 文件**。

- 环境变量名 = `PTY_AGENT_` + 配置 key（大写），如：

  | TOML key | 环境变量 | 类型 |
  |----------|---------|------|
  | `DATA_DIR` | `PTY_AGENT_DATA_DIR` | str（~/%VAR% 展开） |
  | `ENABLE_WEB` | `PTY_AGENT_ENABLE_WEB` | bool |
  | `AUTH_TOKEN_ROTATE_INTERVAL` | `PTY_AGENT_AUTH_TOKEN_ROTATE_INTERVAL` | int |
  | `CONNECT_MODE` | `PTY_AGENT_CONNECT_MODE` | str |
  | `TRANSFER_CHUNK_SIZE` | `PTY_AGENT_TRANSFER_CHUNK_SIZE` | int |

- 取值按文件原值类型转换：bool 接受 `true/false/1/0/yes/no/on/off`（大小写不敏感）；
  int/float 直接转换；list/dict（如 `ISOLATION_NET_ALLOWLIST`）按 JSON 解析；
  str 原样。转换失败时警告并保留文件值，不阻断启动。
- 仅对配置中已存在的 key 生效；环境变量设置了但配置中无对应 key 时被忽略。
- 运行时常量（IS_WINDOWS/PROJECT_ROOT/LOG_DIR 等非 TOML 配置）不参与覆写。
- sandbox 配置按 `<节名>_<键名>` 展平后覆写（如 `PTY_AGENT_SANDBOX_ENABLED`、
  `PTY_AGENT_QUOTA_MEMORY_MB`、`PTY_AGENT_ISOLATION_NET_POLICY`）。
- `PTY_AGENT_CONFIG_DIR` 为加载器专属变量（测试隔离重定向配置目录），非配置 key。

## 规则

- 所有魔数常量统一从 `src/config/*.py` 导入，不散落在业务模块中定义
- `_loader.py` 的 `flatten`/`merge` 对同名 key 冲突直接抛 `ValueError`（防静默覆盖）
- 常量在模块 import 时固化（加载即定值），修改 TOML 后需重启进程生效
- vnc.toml 含加密密码，发布包（BUILD.ps1）不携带；`src/config/` 下的注释/文档描述路径时一律用 `config/`（不带 `src/` 前缀）
