# config/ — 配置数据目录

TOML 配置数据文件，与 `src/` 平级。加载器代码在 `src/config/`（Python 侧），加载规则与归档见 [src/config/__init__.py](../src/config/__init__.py)。

## 配置域清单

**由 `src/config/_loader.py` 加载（Python 配置体系）：**

| 文件 | 加载模块 | 消费方 | 说明 |
|------|----------|--------|------|
| `common.toml` | `common.py` | 全项目 | 终端默认值 / DAEMON_HOST / 压缩 / 输入限制 / 认证开关 / AI 超时 |
| `daemon.toml` | `daemon.py`（合并） | 守护进程 | 端口 / 缓冲 / 超时 / 命名资源 / SHM / TLS 服务端 |
| `logging.toml` | `daemon.py`（合并） | 守护进程 | 日志级别 / 格式 / 轮转 / logger 分组 |
| `web.toml` | `daemon.py`（合并） | Web（daemon 侧） | 监听 / 密码认证 / VNC 集成开关（`[vnc]` 节）/ fastscreen / 网页端默认值 |
| `client.toml` | `client.py`（合并） | 客户端 | 连接超时 / TLS 客户端 / TOFU |
| `sandbox.toml` | `sandbox.py` | 沙箱（Windows） | 沙箱启用与配额 / 隔离策略（仅 [`sandbox`] 节） |
| `files.toml` | `files.py` | 文件工具 | 读/写/搜索上限、忽略目录、RG_EXE |

**不经过 Python 加载（外部工具配置）：**

| 文件 | 消费方 | 说明 |
|------|--------|------|
| `vnc.toml` | winvnc.exe（部署参考） | VNC 运行时参数（端口 / 密码 / 日志）。Python 侧 VNC 开关在 `web.toml [vnc]` 节 |
| `vnc.example.toml` | 人工 | `vnc.toml` 的示例模板 |

## 规则

- 所有魔数常量统一从 `src/config/*.py` 导入，不散落在业务模块中定义
- `_loader.py` 的 `flatten`/`merge` 对同名 key 冲突直接抛 `ValueError`（防静默覆盖）
- 常量在模块 import 时固化（加载即定值），修改 TOML 后需重启进程生效
- vnc.toml 含加密密码，发布包（BUILD.ps1）不携带；`src/config/` 下的注释/文档描述路径时一律用 `config/`（不带 `src/` 前缀）