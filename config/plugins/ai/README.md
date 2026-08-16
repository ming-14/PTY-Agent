# ai — CLI 侧 AI 二次分析插件（自包含）

对命令输出（exec/send/read/mouse 响应）做二次 AI 分析，用分析结果覆盖 `outputStream`。
**CLI 级插件**（`kind = "cli"`）：在客户端进程内执行，daemon 不加载。

本目录为 aichat 全部资产的**自包含**位置（自原 `bin/aichat` 整体迁入），主程序 `src/` 无任何 aichat 引用。

## 声明

| 属性 | 值 | 说明 |
|------|-----|------|
| `name` | `ai` | 插件名 |
| `kind` | `cli` | CLI 侧：客户端进程内执行 |
| `commands` | `["exec", "send", "read", "mouse"]` | 生效命令（空=全部；按命令过滤钩子派发） |

钩子：`transform_response`（响应收到后对 exec/send/read/mouse 结果做 AI 分析）。

## 挂载

`exec --plugin ai` 把插件挂载到会话（一次性）；挂载后客户端对 read/send/mouse
自动挂钩回调（宿主按钩子派发，与 daemon 侧挂载同语义，无启用/禁用概念）：

```bash
# responseOutput 模式（默认，不需要 -o）：把 outputStream 拼进 prompt 喂 AI
python app.py exec myid -c "ls -la" --plugin ai

# fileOutput 模式（有 -o 时自动）：AI 读 -o 渲染文件（txt/svg/图，可喂视觉模型）
python app.py exec myid -c "ls -la" -o out.txt --plugin ai

# 后续 read/send/mouse 自动回调分析，无需再指定 --plugin
python app.py read myid -s
```

- `--plugin` 仅在 `exec` 挂载会话时出现；挂载后该会话的 read/send/mouse 自动分析
- 未挂载 `ai` 的会话输出为原始响应（不参与钩子链）

## 分析模式（按是否带 `-o` 自动判定）

| 模式 | 触发 | 行为 |
|------|------|------|
| `responseOutput` | 无 `-o` | 把 outputStream 拼进 prompt 写临时文件，`aichat -f` 喂 AI（避免 Windows 命令行编码问题） |
| `fileOutput` | 有 `-o` | 先经 `src.client.renderer` 渲染 `-o` 文件（txt/svg/图），`aichat -f` 读该文件，并置 `resp["aiFileWritten"]` 让主程序跳过重复写入，保持"-o 文件=原始渲染、stdout=AI 输出"语义 |

## 会话续聊

`resp.uid`（daemon 侧 `Session.uid`）作为 `aichat --session` 名（`--save-session`），
同一 PTY 会话的多次 AI 分析自动上下文延续。

## 配置

### 环境变量（覆盖插件默认）

| 变量 | 默认 | 说明 |
|------|------|------|
| `PTY_AI_PROMPT` | `全面分析该内容，只按内容说话，不给出下一步，不提建议` | 分析提示词 |
| `PTY_AI_TIMEOUT` | `120` | aichat 调用超时（秒） |

### config.yaml（aichat 模型/密钥）

- 位置：`config/config.yaml`（含 api_key，**被 gitignore 忽略，不入库**）
- 模板：`config/config.yaml.example`（入库）
- **自愈**：`common._ensure_config()` 在 config.yaml 缺失但 example 存在时自动复制 example 生成 config.yaml
- 两者都缺失时：aichat 加载配置失败（stderr 报错），AI 分析回退原始响应，不阻断主流程
- 可用 `config_manager.py` 管理：

```bash
python config/plugins/ai/config_manager.py --show-config                 # 查看
python config/plugins/ai/config_manager.py --init                        # 从模板初始化
python config/plugins/ai/config_manager.py --set-config model openai:deepseek-v4-flash-free
```

## 失败处理

aichat 返回非零 / 超时 / 输出为空 / 异常时，**回退原始 response 并追加 `warning` 字段**，不抛异常、不阻断主流程。

## 目录结构

```
config/plugins/ai/
├── __init__.py            # 插件：AiPlugin（kind=cli，transform_response 分析）
├── common.py              # aichat 桥接：run_aichat / run_aichat_capture / strip_think / check_config
├── config_manager.py      # 配置管理工具库（init/show/set-config，被 _finderror 调用）
├── talk.py                # 独立工具：用 aichat 分析内容（-f 文件 / --session 续聊）
├── _finderror.py          # 独立工具：用 aichat 查找用户命令中的错误
├── bin/aichat.exe         # aichat 可执行文件（gitignore；由 BUILD.py step_download_aichat 下载）
└── config/
    ├── config.yaml        # 模型/密钥配置（gitignore，含真实 api_key）
    └── config.yaml.example# 配置模板（入库，首跑自愈重建）
```

## 注册

已注册于 `config/plugins/plugins.json`：

```json
{ "enabled": true, "plugins": ["config/plugins/state_check", "config/plugins/files", "config/plugins/ai", "config/plugins/simple"] }
```

CLI 插件由 `src/client/cli_plugins.py`（CliPluginHost）在客户端进程启动时加载，
与 daemon 插件同一注册体系；daemon 侧 `PluginRegistry` 按 kind 跳过 cli 形态。
