# subagent 插件 — CodeBuddy (cbc) / Devin / OpenCode / Claude Code / Smart Agent 子代理管理

使用 CodeBuddy / Devin / OpenCode / Claude Code / Smart Agent 编码子代理

**请注意，子代理是代理，拥有完整的权限，请尊重子代理**

子代理和你的子代理一模一样，你在安排任务的时候要给他完整的上下文，清晰的任务安排，明确的指令

## 命令

```
codebuddy exec <sid> -p <prompt> [--cwd <dir>] [--model <model>] [--program-path <path>] [--oneshot | --interactive]
devin exec <sid> -p <prompt> [--cwd <dir>] [--model <model>] [--program-path <path>] [--oneshot | --interactive]
opencode exec <sid> -p <prompt> [--cwd <dir>] [--model <model>] [--program-path <path>] [--oneshot | --interactive]
claude exec <sid> -p <prompt> [--cwd <dir>] [--model <model>] [--program-path <path>] [--oneshot | --interactive]
smartagent exec <sid> -p <prompt> [--cwd <dir>] [--model <model>] [--oneshot | --interactive]
```

- `--program-path <path>`：指定子代理程序路径。不指定时按环境变量（如 `OPENCODE_PATH`）→ PATH 顺序查找，找不到报错

```
app.py read <sid> [--rf snapshot|message] [-l N]    # --rf = --response-format
app.py send <sid> -i <input>                        # 发送给自代理
app.py wait [--timeout <seconds>]                   # 等通知
app.py notice <nid>                                 # 查看通知完整内容
```

### exec — spawn 子代理

- `--oneshot`：一次性模式（阻塞），一直等待子代理工作返回，完成后返回完整输出
- `--interactive`：交互模式（默认），后续可用 read / send / advsend / mouse 交互
- 不支持*返回条件参数 *返回结果处理参数，**请不要使用`--timeout`等条件返回参数**

### read — 读取输出与状态（插件接管子代理会话）

直接当作普通会话操作`app.py read dev --rf message -l 2`，**请不要使用`--timeout`等条件返回参数**

- `--rf snapshot`：输出屏幕快照（不支持`-l`）
- `--rf message -l N`：最近 N 条结构化消息，仅记录已完成的输出，实时状态请用 snapshot

1. 欲获取实时状态，遇到异常请使用`--rf snapshot`获取屏幕快照
2. 已知子代理完成工作时，请使用`--rf message -l N`，例如验收结果请使用`--rf message -l 2`

### send — 发送输入

`app.py send <sid> -i "消息"`给子代理发消息，**请不要使用`--timeout`等条件返回参数**

### wait / notice — 回合完成通知

回合完成（或卡权限 / AI 提问）时，插件发布到 NotificationManager：
- `app.py wait`：阻塞等待通知
- `app.py notice <nid>`：查看该通知的内容

使用`wait`前，请确保子代理处于工作状态，防止长时间`wait`阻塞

！请注意：使用`exec`/`read`/`send`等会清除该会话的所有通知，禁止使用`exec`/`read`/`send`后再`wait`会话

## 示例

```bash
# 一次性执行
python app.py codebuddy exec fix -p "仔细探索该仓库" --cwd C:\repo --oneshot

# 交互式多轮（CodeBuddy）
python app.py codebuddy exec dev -p "先看看代码结构，然后把XXXbug修了" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read dev --rf message -l 10              # 看结果
python app.py send dev -i "你的工作还没有完成，给我继续"   # 继续聊天
python app.py wait --timeout 300                       # 等下一轮
# 你也可以不wait继续工作，你下次使用PTY-Agent的时候会发通知给你

# 交互式多轮（Devin）
python app.py devin exec devtask -p "分析这个仓库的结构" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read devtask --rf message -l 10          # 看结果（transcript JSON）

# 交互式多轮（OpenCode）
python app.py opencode exec octask -p "分析这个仓库的结构" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read octask --rf message -l 10           # 看结果（opencode.db 消息）

# 一次性模式（OpenCode）
python app.py opencode exec octask -p "分析这个仓库" --cwd C:\repo --oneshot

# 交互式多轮（Claude Code）
python app.py claude exec cltask -p "分析这个仓库的结构" --cwd C:\repo
python app.py wait --timeout 300                       # 等回合完成通知
python app.py read cltask --rf message -l 10           # 看结果（~/.claude jsonl 消息）

# 一次性模式（Claude Code）
python app.py claude exec cltask -p "分析这个仓库" --cwd C:\repo --oneshot

# 状态查看
python app.py read dev                                  # 屏幕快照 + 实时状态
python app.py list                                      # STATE 显示 subagent_<ai_status>

# 查看帮助文档
python app.py plugin --gethelp subagent
```
