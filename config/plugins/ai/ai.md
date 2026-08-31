# ai 插件说明

本插件对命令输出做 AI 二次分析

exec/send/read/mouse 响应经 aichat 调用大模型分析后，用分析结果覆盖 `outputStream`，让 AI 替你阅读长上下文，**终端图片**等

## 挂载（必须）

插件需挂载到会话后才生效。两种方式任选：

```bash
python app.py exec <session-id> -c "<命令>" --plugin ai
python app.py plugin attach <session-id> ai
```

只挂载一次就好，后续无需

## 分析模式（按是否带 `-o` 自动判定）

| 模式 | 触发 | 行为 |
|------|------|------|
| responseOutput | 无 `-o` | 分析文本覆盖输出 |
| fileOutput | 有 `-o` | 喂 AI 读该文件分析（可喂视觉模型看图），文件保持原始渲染、stdout 为 AI 输出。**适合看终端图片** |

```bash
python app.py exec myid -c "ls -la" --plugin ai # responseOutput（默认）：直接分析输出文本
python app.py exec myid -c "TUIgame.exe" -o out.jpg --plugin ai # fileOutput：带 -o 时 AI 分析渲染后的终端图片

# 挂载后 read/send/mouse 自动回调，无需再带 --plugin
python app.py read myid -s
python app.py send myid -i "more commands"
```

## 会话记忆

AI 记得同一会话的历史对话

---

## 配置

配置存于插件目录 `config/plugins/ai/config/config.yaml`

| 键 | 默认 | 说明 |
|------|------|------|
| `model` | | aichat 模型（`provider:model` 格式） |
| `prompt` | `全面分析该内容，只按内容说话，不给出下一步，不提建议` | 分析提示词 |
| `timeout` | `120` | aichat 调用超时（秒） |
| `clients[].type/name/api_base/api_key` | — | aichat 客户端（API 端点/密钥） |

```bash
python config_manager.py --show-config                        # 查看
python config_manager.py --set-config prompt "新的提示词"      # 改分析提示词
python config_manager.py --set-config timeout 60              # 改超时
python config_manager.py --set-config model modelxxx   # 改模型
```

## 常见问题

- **分析结果不理想？** 调整 `prompt` 提示词（`config_manager.py --set-config prompt "..."`），或带 `-o` 用 fileOutput 模式喂视觉模型
- **想完全关闭分析？** 不挂载 ai 插件即可
