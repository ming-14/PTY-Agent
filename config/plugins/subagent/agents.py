"""Agent 规格注册表 — 声明每个子代理的 spawn 参数与 parser 类型

基础设施是公共的，解析器是独有的。每个 agent 声明自己的命令、参数、
会话发现策略和对应的 parser agent 名，插件按此注册表派发。
新增 agent 只需在此注册一条 AgentSpec，无需改动通用组件。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class AgentSpec:
    """单个子代理的规格

    build_command 生成完整 spawn 命令；各 agent 的差异（会话 ID 参数、
    一次性/交互 prompt 位置、权限模式、消息解析适配器）都在此集中声明。
    """
    agent_id: str                        # 标识（如 codebuddy / devin / opencode）
    display_name: str                    # 通知/渲染时显示名
    command: List[str]                   # 基础命令（如 ["cbc"] / ["devin.exe"] / ["opencode.exe"]）
    parser_agent: str                    # parser_loader 的 agent 名
    message_type: str                    # 消息类型（如 codebuddy_exec / devin_exec）
    permission_args: List[str]           # 权限绕过参数
    supports_oneshot: bool = True        # 是否支持 --oneshot（smartagent 常驻 TUI 不支持）
    command_path_env: str = ""            # 命令行路径的环境变量（如 "OPENCODE_PATH"）；空=仅 PATH 查找
    has_program_path: bool = True         # 是否支持 --program-path（smartagent 用 python 脚本，不适用）
    session_id_arg: Optional[str] = None # 显式 session-id 参数名（如 codebuddy "--session-id"）
    session_id_arg_oneshot_only: bool = False  # session_id_arg 仅 oneshot 模式生效（如 opencode --title）
    export_arg: str = ""                  # 导出参数名（如 devin "--export"），uid 为导出文件路径
    uid_is_path: bool = False             # uid 是文件路径（而非会话名），跳过 locator 定位
    oneshot_flags: List[str] = field(default_factory=list)  # 一次性模式附加参数
    interactive_prompt_sep: List[str] = field(default_factory=list)  # 交互 prompt 前的分隔（如 ["--"]）
    trust_dialog: bool = False           # 是否需自动确认信任对话框
    trust_dialog_check: str = "Do you trust the files in this folder?"  # 信任对话框检测文本
    trust_dialog_keys: str = "\r"        # 确认按键模板（如 claude 需 "{down}{enter}{enter}"）
    trust_dialog_timeout: float = 5.0    # 检测超时秒数
    wrap_shell: str = ""                  # 需经 shell 包装的命令（如 "cmd"）；空=不包装
    startup_error_hints: List[str] = field(default_factory=list)  # 启动错误检测关键词
    welcome_marks: List[str] = field(default_factory=list)       # 欢迎页检测关键词
    # 消息解析适配器：parser 内模块名 + 定位/加载函数名（按 parser 独有实现声明）
    messages_adapter: str = "messages_jsonl"      # 如 messages_transcript
    locator_fn: str = "find_session_file"         # 如 find_transcript_file
    msg_loader_fn: str = "load_jsonl"             # 如 load_transcript
    loader_meta_first: bool = False               # 加载函数返回 (meta, entities) 顺序
    # LiveState 特有字段（parser LiveState 可能带，插件读取时动态附加到结果）
    live_state_fields: tuple = ("context_percent", "permission_mode")
    # interactive 模式会话发现函数名（session_locator 中）；空=无需发现
    discover_fn: str = ""
    # 数据目录隔离：spawn 时设置该环境变量为独立目录（如 "XDG_DATA_HOME"），
    # 使 agent 的日志/数据库写入独立路径，实现并发安全、无污染发现。
    # 设置后 discover 从独立日志文件提取 session.id，消息读取传独立 data_dir。
    data_dir_env: str = ""
    # 独立数据目录根下 agent 数据子目录（如 opencode 数据在 $XDG_DATA_HOME/opencode/），
    # 消息读取传给 parser 的 data_dir = 根 + 子目录
    data_dir_subdir: str = ""
    # 独立数据目录下日志文件的相对路径（如 "opencode/log/opencode.log"），
    # discover 从该文件提取会话 ID（正则见 discover_log_regex）
    discover_log_relpath: str = ""
    discover_log_regex: str = r"session\.id=(ses_[A-Za-z0-9]+)"

    def build_command(self, prompt: str, model: Optional[str] = None,
                      uid: Optional[str] = None, oneshot: bool = False) -> List[str]:
        """生成完整 spawn 命令列表

        Args:
            prompt: 任务提示词
            model: 可选模型名
            uid: 显式会话 ID（session_id_arg）或导出文件路径（export_arg）
            oneshot: 一次性模式（阻塞，输出即返回）
        """
        args = []
        if model:
            args += ["--model", model]
        args += self.permission_args
        # session_id_arg：仅 oneshot 生效（opencode --title 不支持 interactive）
        if self.session_id_arg and uid and (not self.session_id_arg_oneshot_only or oneshot):
            args += [self.session_id_arg, uid]
        if self.export_arg and uid:
            args += [self.export_arg, uid]
        if oneshot:
            # 一次性：flags 后跟 prompt
            args += self.oneshot_flags
            args.append(prompt)
        else:
            # 交互：prompt 前加分隔符（devin 需要 "--"；codebuddy 直接追加）
            args += self.interactive_prompt_sep
            args.append(prompt)
        return self.command + args


AGENTS: dict = {
    "smartagent": AgentSpec(
        agent_id="smartagent",
        display_name="Smart Agent",
        command=["python", "-u",
                 os.path.join(os.path.dirname(__file__), "smartagent", "smartagent.py")],
        parser_agent="smartagent",
        message_type="smartagent_exec",
        permission_args=[],
        has_program_path=False,
        session_id_arg="--sid",
        interactive_prompt_sep=["--prompt"],
        oneshot_flags=["--oneshot", "--prompt"],
        trust_dialog=False,
        startup_error_hints=[],
        welcome_marks=[],
        messages_adapter="messages_jsonl",
        locator_fn="find_session_file",
        msg_loader_fn="load_jsonl_with_meta",
        loader_meta_first=True,
        live_state_fields=(),
    ),
    "claude": AgentSpec(
        agent_id="claude",
        display_name="Claude Code",
        command=["claude"],
        parser_agent="claude",
        message_type="claude_exec",
        permission_args=["--dangerously-skip-permissions"],
        session_id_arg="--session-id",
        oneshot_flags=["-p", "--output-format", "text"],
        interactive_prompt_sep=[],
        wrap_shell="cmd",  # claude.cmd 为 npm shim，与 cbc 一致
        trust_dialog=True,
        trust_dialog_check="Quick safety check",
        trust_dialog_keys="{down}{enter}{enter}",  # 模板形式：展开为 ↓+回车+回车，分段写入停顿
        trust_dialog_timeout=8.0,
        startup_error_hints=["does not exist", "Invalid model"],
        welcome_marks=["Claude Code", "╭"],
        messages_adapter="messages_jsonl",
        locator_fn="find_session_file",
        msg_loader_fn="load_jsonl_with_meta",
        loader_meta_first=True,
        live_state_fields=("permission_mode", "effort", "mode"),
    ),
    "codebuddy": AgentSpec(
        agent_id="codebuddy",
        display_name="Codebuddy",
        command=["cbc"],
        parser_agent="codebuddy",
        message_type="codebuddy_exec",
        permission_args=["--permission-mode", "bypassPermissions"],
        session_id_arg="--session-id",
        oneshot_flags=["-p", "--output-format", "text"],
        trust_dialog=True,
        wrap_shell="cmd",  # cbc 为 npm .cmd shim，Windows 下需 cmd /c 包装
        startup_error_hints=["Please use --model", "specify a valid model"],
        welcome_marks=["CodeBuddy Code", "╭"],
        messages_adapter="messages_jsonl",
        locator_fn="find_session_file",
        msg_loader_fn="load_jsonl",
    ),
    "devin": AgentSpec(
        agent_id="devin",
        display_name="Devin",
        command=["devin.exe"],
        parser_agent="devin",
        message_type="devin_exec",
        permission_args=["--permission-mode", "dangerous",
                         "--respect-workspace-trust", "false"],
        oneshot_flags=["-p"],
        interactive_prompt_sep=["--"],
        trust_dialog=False,
        startup_error_hints=[],
        welcome_marks=["Devin CLI", "v3000."],
        # --export 指定确定性路径，devin 每回合后导出 ATIF 格式
        export_arg="--export",
        uid_is_path=True,
        messages_adapter="messages_transcript",
        locator_fn="find_transcript_file",
        msg_loader_fn="load_transcript",
        loader_meta_first=True,
    ),
    "opencode": AgentSpec(
        agent_id="opencode",
        display_name="OpenCode",
        command=["opencode.exe"],
        command_path_env="OPENCODE_PATH",
        parser_agent="opencode",
        message_type="opencode_exec",
        permission_args=["--auto"],
        session_id_arg="--title",
        session_id_arg_oneshot_only=True,
        oneshot_flags=["run"],
        trust_dialog=False,
        startup_error_hints=[],
        welcome_marks=["opencode"],
        messages_adapter="messages_db",
        locator_fn="find_session_by_title",
        msg_loader_fn="load_session_messages_by_id",
        loader_meta_first=True,
        # 数据目录隔离：每个 spawn 独立 XDG_DATA_HOME（opencode 的日志+db 写入
        # $XDG_DATA_HOME/opencode/），会话日志/数据库天然隔离，并发安全；
        # discover 从独立日志文件提取 session.id，屏幕无输出污染
        data_dir_env="XDG_DATA_HOME",
        data_dir_subdir="opencode",
        discover_log_relpath="opencode/log/opencode.log",
        interactive_prompt_sep=["--prompt"],
    ),
}


# 消息类型 → agent_id 反向映射
MESSAGE_TYPE_TO_AGENT = {spec.message_type: aid for aid, spec in AGENTS.items()}