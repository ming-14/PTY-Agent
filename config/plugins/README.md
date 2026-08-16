# config/plugins/ — 插件目录（daemon 侧 + CLI 侧）

插件实现目录，各插件业务参数自包含于自身子目录。插件位置由 `plugins.json` 显式注册（相对项目根的路径），由 `src/plugins/loader.py` 加载，注册配置由 `src/config/plugins.py` 读取。
插件用 `kind` 声明运行侧：`session`/`process` 由 daemon 加载，`cli` 由客户端进程加载（见 [插件形态](#插件形态kind)）。

## 注册

`plugins.json` 声明总开关与插件位置：

```json
{
  "enabled": true,
  "plugins": [
    "config/plugins/state_check",
    "config/plugins/files"
  ]
}
```

- `enabled`：插件系统总开关
- `plugins`：插件位置列表（相对项目根，指向含 `__init__.py` 的目录或单文件 `*.py`）
- 加载约定：模块必须导出 `plugin` 属性（Plugin 实例或 Plugin 子类），声明校验失败仅跳过该插件
- 修改后需重启守护进程生效；也可用 `PTY_PLUGIN_DIRS` 环境变量追加插件位置（路径分隔符分隔）

## 目录结构

```
config/plugins/
├── plugins.json       # 插件注册（相对项目根路径）
├── __init__.py        # 包标记（空）
├── files/             # 文件工具插件（进程级：file_read/write/edit/grep/glob/upload/download）
│   ├── files_plugin.py
│   ├── files.toml / config.py   # 插件配置（业务参数自包含）
│   ├── state.py history.py diff.py permission.py paths.py errors.py  # 公共模块
│   ├── read/ write/ search/ transfer/   # 各用例实现
│   └── README.md
├── state_check/       # 终端状态检查插件（返回钩子 + 命令钩子）
│   ├── __init__.py
│   └── README.md
├── simple/            # CLI 侧响应精简插件（kind=cli：客户端进程内 render 钩子）
│   └── __init__.py
└── ai/                # CLI 侧 AI 二次分析插件（kind=cli，自包含 aichat 资产）
    ├── __init__.py    # AiPlugin（transform_response 分析，覆盖 outputStream）
    ├── common.py      # aichat 桥接（run_aichat_capture 等）
    ├── talk.py / _finderror.py / config_manager.py   # aichat 独立工具
    ├── bin/aichat.exe  # aichat 可执行文件（BUILD.py 下载，gitignore）
    ├── config/config.yaml(.example)  # 模型/密钥配置 + 模板（自愈重建）
    └── README.md
```

## 插件一览

| 插件 | 类型 | 功能 |
|------|------|------|
| `files` | 进程级（`message_types` 非空，`needs_io`） | 接管 `file_*` 文件工具消息，响应形状与原内置 handler 逐字段一致 |
| `state_check` | 钩子式（`triggers = []`） | 命令返回时检测终端状态，以 `terminalState` 附加到返回信息 |
| `simple` | CLI 侧（`kind = "cli"`） | 客户端进程内把输出类响应渲染为自然文本（输出 + triggerReturnReason/执行时间尾巴），daemon 不加载 |
| `ai` | CLI 侧（`kind = "cli"`） | 对 exec/send/read/mouse 响应做 AI 二次分析，`exec --plugin ai` 挂载到会话后自动回调（responseOutput/fileOutput 两模式、按会话 uid 续聊），daemon 不加载 |

## 插件形态（kind）

插件基类（`src/plugins/base.py`）支持三种形态，按 `kind` 类属性区分，
未显式声明时按 `message_types` 推断（非空 → `"process"`，空 → `"session"`）：

| kind | 执行位置 | 说明 |
|------|----------|------|
| `process` | daemon 进程 | 进程级：启动单例实例化，`message_types` 接管消息路由（`handle_message`） |
| `session` | daemon 进程 | 会话级：exec 注入 / attach / auto_load，挂载到会话后由 PluginHost 调度钩子链 |
| `cli` | 客户端进程 | CLI 侧：每次命令进程启动时加载，处理请求/响应三阶段钩子（before_request / transform_response / render_response）；daemon 注册表跳过，不实例化 |

详细说明见各插件子目录 README。
