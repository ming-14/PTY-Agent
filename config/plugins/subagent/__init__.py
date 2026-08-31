"""subagent 插件 — 子代理管理（cli + process 双形态）

- process 侧：<agent>_exec 消息处理 + 装饰 read/send 响应（子代理检测）
- cli 侧：<agent> exec 命令 + before_request 注入 lines
- 新增 agent：agents.py 注册 AgentSpec + plugin.json 声明 cliCommands/messageTypes，
  CLI 命令类由 cli_commands.all_agent_commands() 自动生成，无需手写

清单见同目录 plugin.json（kind=["cli","process"]）。
"""
from .subagent_plugin import SubagentPlugin
from .cli_commands import all_agent_commands

plugin = SubagentPlugin
commands = all_agent_commands()