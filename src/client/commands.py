"""客户端会话命令混入 —— cmd_* 命令实现与共享输出/解析工具。

会话命令职责（ClientCommandsMixin）：
- 生命周期：cmd_start / cmd_stop / cmd_status / cmd_list / cmd_kill / cmd_closewin
- 会话交互：cmd_exec / cmd_read / cmd_send / cmd_mouse / cmd_events / cmd_wait
- 插件管理：cmd_plugin
- 实时接管：cmd_attend

模块级共享工具：print_response 经 presenter 模块属性调用（支持测试 monkeypatch）；
_shell 操作符检测 / ISO 时间解析 / 渲染格式协商 / --output 文件写入（daemon 渲染结果）。
"""

import base64
import os
import shlex
import socket
import sys
from typing import Optional

from ..config.common import IS_WINDOWS
from ..config.default_keys import DEFAULT_VALUES as _DEFAULTS_MAP
from ..protocol.response import Response
from ..logging import get_logger
from . import presenter

_logger = get_logger("pty-client")


def _render_format_for(output_path, response_format) -> Optional[str]:
    """计算请求的渲染格式：svg / png / jpg / jpeg / bmp / None

    - --response-format svg → "svg"
    - --output <图片扩展名> → 对应格式（daemon 按格式编码）
    - 其余 → None（不请求服务端渲染，走默认文本输出）
    """
    if response_format == "svg":
        return "svg"
    if output_path:
        _, ext = os.path.splitext(output_path.lower())
        if ext in (".png", ".jpg", ".jpeg", ".bmp"):
            return ext.lstrip(".")
        if ext == ".svg":
            return "svg"
    return None


def _finalize_response(resp, response_format, output_path) -> None:
    """响应收尾：svg / output_path 剥离字段 / 文件写入（exec/read/send/mouse 共用）

    渲染由 daemon 侧 pywezterm 完成（render_svg / render_image），客户端
    直接消费 resp["svgContent"] / resp["imageZ"]，不再本地渲染
    （Python renderer 已删除）。
    """
    if response_format == "svg":
        if resp.get("type") == "error":
            presenter.print_response(resp)
            return
        svg = resp.get("svgContent") or ""
        if not svg:
            # 区分根因：会话已结束（无屏幕缓冲）→ requires a screen buffer；
            # 否则（daemon 渲染不可用/失败）→ requires daemon-side SVG rendering
            if not resp.get("program", {}).get("running", True):
                message = "--response-format svg requires a screen buffer (the session has ended)"
            else:
                message = "--response-format svg requires daemon-side SVG rendering"
            presenter.print_response(Response.error(message))
            return
        # 保留会话上下文（sessionId/program/reason/...）供 SessionResult 渲染：
        # 终端展示带 svg 标签的框 + 状态行，而不是裸 svg 文本
        svg_resp = {
            k: v
            for k, v in resp.items()
            if k
            not in (
                "sessionDefaults",
                "svgContent",
                "imageZ",
                "imageType",
            )
        }
        svg_resp["type"] = "svg"
        svg_resp["data"] = svg
        svg_resp["format"] = "svg"
        presenter.print_response(svg_resp)
    elif output_path:
        display = {
            k: v
            for k, v in resp.items()
            if k
            not in (
                "sessionDefaults",
                "aiFileWritten",
                "svgContent",
                "imageZ",
                "imageType",
            )
        }
        presenter.print_response(display)
    else:
        presenter.print_response(resp)
    # fileOutput 插件已自行写文件，跳过重复写入
    if not resp.get("aiFileWritten"):
        _handle_output(output_path, resp)


_SHELL_OPS = frozenset({"|", "||", "&", "&&", ";", ">", "<", ">>"})


def _has_shell_operators(cmd: str) -> bool:
    try:
        tokens = shlex.split(cmd, posix=not IS_WINDOWS)
    except ValueError:
        return False
    return any(t in _SHELL_OPS for t in tokens)


def _parse_iso_time(s: str) -> float:
    from datetime import datetime

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.timestamp()


def _handle_output(output_path: Optional[str], resp: dict):
    if not output_path:
        return
    if resp.get("type") == "error":
        _logger.warning("请求失败，跳过输出到 %s", output_path)
        return

    # 使用 daemon 侧渲染结果（v7：pywezterm 服务端渲染，替代本地 Python 渲染器）
    svg = resp.get("svgContent")
    image_b64 = resp.get("imageZ")
    if svg:
        # 服务端渲染 SVG
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(svg)
            _logger.info("SVG written to %s (%d bytes)", output_path, len(svg))
            presenter.print_response(Response.info(f"Output written to {output_path}"))
        except OSError as e:
            presenter.print_response(Response.error(f"Failed to write {output_path}: {e}"))
        return
    elif image_b64:
        # 服务端渲染图片（base64 编码，格式按请求：png/jpg/jpeg/bmp）
        try:
            img_bytes = base64.b64decode(image_b64)
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_bytes)
            _logger.info("Image written to %s (%d bytes)", output_path, len(img_bytes))
            presenter.print_response(Response.info(f"Output written to {output_path}"))
        except (OSError, ValueError) as e:
            presenter.print_response(Response.error(f"Failed to write {output_path}: {e}"))
        return

    # 请求了渲染格式（svg/png/jpg/jpeg/bmp）但无渲染结果：拒绝回退写纯文本
    # （用户明确要求图片格式，写纯文本到 .svg/.png 文件会误导）
    if _render_format_for(output_path, None) is not None:
        if not resp.get("program", {}).get("running", True):
            msg = "requires a screen buffer (the session has ended)"
        else:
            msg = "rendering unavailable (daemon-side rendering failed or not available)"
        presenter.print_response(
            Response.error(f"Failed to write {output_path}: {msg}")
        )
        return

    # 回退：无服务端渲染结果时写纯文本输出（原始行为，非图片模式）
    text = resp.get("outputStream") or resp.get("stdout") or ""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        _logger.info("Output written to %s", output_path)
    except OSError as e:
        presenter.print_response(Response.error(f"Failed to write {output_path}: {e}"))


class ClientCommandsMixin:
    """会话命令域（cmd_* 实现）"""

    def cmd_start(self):
        _logger.info("cmd_start")
        from .daemonctl import start_daemon

        started = start_daemon()
        if not started:
            presenter.print_response(Response.error("failed to start daemon"))
            sys.exit(1)
        from .daemonctl import is_running

        if is_running():
            # 守护进程启动：输出进程级插件上下文（<插件名>.md）给用户
            try:
                from ..plugins.context import output_process_contexts
                from ..config.plugins import PLUGIN_DIRS

                output_process_contexts(PLUGIN_DIRS)
            except Exception:
                pass
            resp = self._send_recv({"type": "list"}, autostart=False)
            presenter.print_response(resp)

    def cmd_stop(self, force: bool = False):
        _logger.info("cmd_stop force=%s", force)
        from .daemonctl import stop_daemon

        stop_daemon(force=force)

    def cmd_status(self):
        _logger.info("cmd_status")
        port = self._probe_port()
        if port is None:
            presenter.print_response({"type": "status", "running": False})
            return
        try:
            resp = self._send_recv({"type": "status"}, autostart=False)
            presenter.print_response(resp)
        except SystemExit:
            presenter.print_response({"type": "status", "running": False})
        except Exception as e:
            # 连接失败即视为未运行（tls/basic 模式目标不可达时曾误报 running=yes）；
            # 失败原因输出 stderr，状态展示为 running=no
            _logger.warning("status 探测失败: %s", e)
            presenter.print_response(
                {"type": "error", "message": f"status failed: {e}"}
            )
            presenter.print_response({"type": "status", "running": False})

    def cmd_exec(
        self,
        session_id: str,
        command,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        lines: Optional[object] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        force: bool = False,
        cwd: Optional[str] = None,
        env: Optional[list] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        size: Optional[str] = None,
        plugins: Optional[list] = None,
        mode: str = "pty",
        shell: Optional[str] = None,
        notify: bool = False,
    ):
        _logger.info(
            "cmd_exec: id=%r force=%s env=%s size=%s plugins=%s mode=%s shell=%s",
            session_id,
            force,
            env,
            size,
            plugins,
            mode,
            shell,
        )
        # shell 包装：--shell 显式参数优先于 set-default shell（默认无包装）
        if shell is None:
            shell = self._config.get("shell")
        if shell and shell.lower() in ("none", ""):
            shell = None
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, send_eol = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if mode == "subprocess" and size:
            presenter.print_response(
                Response.error(
                    "子进程模式不支持 --size（无终端）；请使用读 stdin 交互"
                )
            )
            return

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        # 命令拆分：无 shell 时拆为参数列表（子进程模式也拆分，Popen 直接执行）；
        # 有 --shell/set-default shell 时**保持原始字符串**——操作符与复杂引号
        # 交由目标 shell 按语义解析（daemon 端 wrap_command 原样传给 shell 参数），
        # 拆分再重组会丢失操作符/引号语义（如 shlex.join 会把 && 引成字面量）
        if isinstance(command, str):
            if shell:
                _logger.info(
                    "cmd_exec: 使用 shell 包装: %s, command=%r", shell, command[:200]
                )
            else:
                if mode != "subprocess" and _has_shell_operators(command):
                    if not force:
                        presenter.print_response(
                            Response.error(
                                "命令包含 shell 操作符 (| & > < && || ;)，"
                                "这些操作符需要 shell 解析，在 PTY 模式下无法工作。\n"
                                "  → 添加 --force-pty-mode 强制执行（操作符作为字面参数传递）"
                            )
                        )
                        return
                    _logger.warning(
                        "--force-pty-mode: 忽略 shell 操作符检测，原样拆分执行, command=%r",
                        command,
                    )
                command = shlex.split(command, posix=not IS_WINDOWS)
                # PowerShell/CMD 传递含空格路径时 -c 参数可能保留字面量双引号
                command = [s.strip('"') for s in command]
        elif shell:
            # 命令已为 List（web 端等来源），shell 模式原样透传给 daemon 重组
            _logger.info("cmd_exec: shell=%s command_from_list=%r", shell, command)

        msg = {
            "type": "exec",
            "id": session_id,
            "command": command,
            "newline": newline,
            "fresh": fresh,
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
            "mode": mode,
        }
        if notify:
            msg["notify"] = True
        if lines is not None:
            msg["lines"] = lines
        if shell:
            msg["shell"] = shell
        if self._config.get("debug"):
            msg["debug"] = True  # CLI 主动申请返回 debug 信息
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        msg["cwd"] = cwd if cwd is not None else os.getcwd()
        if env is not None:
            env_dict = {}
            for item in env:
                if "=" not in item:
                    presenter.print_response(
                        Response.error(
                            f"Invalid --env format: {item!r} (expected KEY=VALUE)"
                        )
                    )
                    return
                k, v = item.split("=", 1)
                env_dict[k] = v
            msg["env"] = env_dict
        if output_path or response_format == "svg":
            rf = _render_format_for(output_path, response_format)
            if rf:
                msg["render_format"] = rf
                if rf == "svg":
                    msg["svg_compression_level"] = svg_compression_level
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if plugins:
            # --plugin 按插件形态分流：CLI 形态在客户端启用本次调用，
            # 会话/进程形态透传 daemon 按现有逻辑挂载（避免 daemon 对 CLI 插件误报未加载）
            self._route_plugins(msg, plugins)
        if self.plugin_options:
            # 插件自定义选项（cliOptions 声明）随消息下发：daemon 形态注入会话，
            # CLI 形态经激活钩子读取
            msg["pluginOptions"] = self.plugin_options

        # 终端尺寸：--size 优先，否则从 --default terminal-size 读取
        if size:
            from .config_manager import parse_terminal_size

            try:
                c, r = parse_terminal_size(size)
                msg["cols"] = c
                msg["rows"] = r
            except ValueError as e:
                presenter.print_response(Response.error(str(e)))
                return
        else:
            ts = self._config.get("terminal_size")
            if ts and ts != "80x24":
                from .config_manager import parse_terminal_size

                try:
                    c, r = parse_terminal_size(ts)
                    msg["cols"] = c
                    msg["rows"] = r
                except ValueError:
                    pass

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if size is not None:
            # --size 显式指定时优先于 --default terminal-size：
            # 避免新会话创建后被 client_defaults 里的 terminal_size 改回
            client_defaults.pop("terminal_size", None)
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path, autostart=True)
        self._merge_session_defaults(resp)

        if notify:
            # --notify 立即返回（无渲染结果），跳过 svg/文件输出处理
            presenter.print_response(resp)
            return
        _finalize_response(resp, response_format, output_path)

    def cmd_read(
        self,
        session_id: str,
        trigger: Optional[str] = None,
        newline: bool = False,
        timeout: Optional[float] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        lines: Optional[str] = None,
        grep: Optional[str] = None,
        offset: Optional[int] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        column: Optional[int] = None,
        notify: bool = False,
    ):
        """读取会话终端输出，支持触发条件等待"""
        _logger.info(
            "cmd_read: id=%r trigger=%r timeout=%s idle_timeout=%s lines=%s grep=%r offset=%s full=%s",
            session_id,
            trigger,
            timeout,
            idle_timeout,
            lines,
            grep,
            offset,
            full,
        )
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {
            "type": "read",
            "id": session_id,
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
        }
        if self._config.get("debug"):
            msg["debug"] = True  # CLI 主动申请返回 debug 信息
        if trigger is not None:
            msg["trigger"] = trigger
            msg["newline"] = newline
            msg["fresh"] = True
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if lines is not None:
            msg["lines"] = lines
        if grep is not None:
            msg["grep"] = grep
        if offset is not None:
            msg["offset"] = offset
        if encoding is not None:
            msg["encoding"] = encoding
        if output_path or response_format == "svg":
            rf = _render_format_for(output_path, response_format)
            if rf:
                msg["render_format"] = rf
                if rf == "svg":
                    msg["svg_compression_level"] = svg_compression_level
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if column is not None:
            msg["column"] = column
        if notify:
            msg["notify"] = True

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)
        if self.plugin_options:
            msg["pluginOptions"] = self.plugin_options

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        self._merge_session_defaults(resp)

        if notify:
            # --notify 立即返回（无渲染结果），跳过 svg/文件输出处理
            presenter.print_response(resp)
            return
        _finalize_response(resp, response_format, output_path)

    def cmd_send(
        self,
        session_id: str,
        input_text: str,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        full: bool = False,
        keep_ansi: Optional[bool] = None,
        lines: Optional[object] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        json_escaping: bool = False,
        send_eol: Optional[str] = None,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        notify: bool = False,
    ):
        """向会话发送输入文本，支持触发条件等待和屏幕快照

        与 cmd_read 共享响应处理逻辑（trigger/svg/output），
        但额外处理输入文本：json_escaping 转义解码 + send_eol 行尾符追加。

        Args:
            session_id: 会话标识
            input_text: 要发送的原始输入文本
            trigger: 触发条件正则表达式（命中后返回输出）
            newline: 仅在换行后检查触发条件
            fresh: 是否从最新位置开始检测触发（CLI 传入 True）
            timeout: 等待超时秒数
            encoding: 终端编码
            full: 返回全部累积输出
            keep_ansi: 保留 ANSI 颜色/样式码
            idle_timeout: 输出静默超时
            idle_after_first_output: 仅首次输出后检测静默
            json_escaping: 启用 JSON + 控制字符转义解码
            send_eol: 行尾符名称（"cr"/"lf"/"crlf"/"none"），None 时用配置默认值
            output_path: 输出到文件
            response_format: 响应格式（stream/svg）
            svg_compression_level: SVG 压缩等级
            snapshot_diff: 仅返回屏幕变化行
        """
        _logger.info(
            "cmd_send: id=%r trigger=%r timeout=%s json_escaping=%s send_eol=%s",
            session_id,
            trigger,
            timeout,
            json_escaping,
            send_eol,
        )

        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        # 转义展开由守护进程统一完成（按会话模式决定 {enter}/默认行尾符）：
        # CLI 只透传原始 input + 转义开关 + 显式行尾符；默认行尾由 daemon 决定
        #（pty→\r，subprocess→\n）。显式行尾符优先级：--send-eol > set-default send-eol。
        send_eol_name = send_eol  # CLI 显式 --send-eol 名称（"cr"/"lf"/"crlf"/"none"）
        if send_eol_name is None:
            cfg_eol = self._config.get("send_eol")
            if cfg_eol is not None and cfg_eol != _DEFAULTS_MAP.get("send_eol"):
                send_eol_name = cfg_eol

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {
            "type": "send",
            "id": session_id,
            "input": input_text,               # 原始输入，转义展开由守护进程完成
            "json_escaping": json_escaping,    # 是否展开 JSON + 控制字符转义
            "send_eol": send_eol_name,         # 显式行尾符；None=daemon 按会话模式默认
            "full": full,
            "keep_ansi": keep_ansi,
            "timeout": timeout,
            "explicit_timeout": explicit_timeout,
        }
        if self._config.get("debug"):
            msg["debug"] = True  # CLI 主动申请返回 debug 信息
        if trigger is not None:
            msg["trigger"] = trigger
            msg["newline"] = newline
            msg["fresh"] = fresh
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if encoding is not None:
            msg["encoding"] = encoding
        if output_path or response_format == "svg":
            rf = _render_format_for(output_path, response_format)
            if rf:
                msg["render_format"] = rf
                if rf == "svg":
                    msg["svg_compression_level"] = svg_compression_level
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if lines is not None:
            msg["lines"] = lines
        if notify:
            msg["notify"] = True

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)
        if self.plugin_options:
            msg["pluginOptions"] = self.plugin_options

        self._maybe_save_encoding(encoding)
        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        self._merge_session_defaults(resp)

        if notify:
            # --notify 立即返回（无渲染结果），跳过 svg/文件输出处理
            presenter.print_response(resp)
            return
        _finalize_response(resp, response_format, output_path)

    def cmd_list(self):
        """列出所有会话"""
        _logger.info("cmd_list")
        resp = self._send_recv({"type": "list"})
        presenter.print_response(resp)

    def cmd_plugin(
        self,
        action: str,
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        command: Optional[str] = None,
        args: Optional[list] = None,
        path: Optional[str] = None,
        key: Optional[str] = None,
        value: Optional[str] = None,
    ):
        """插件管理命令（list/ls/attach/detach/cmd/install/uninstall/enable/disable/reload/info/status/config）"""
        _logger.info(
            "cmd_plugin: action=%s id=%s name=%s cmd=%s path=%s key=%s",
            action,
            session_id,
            name,
            command,
            path,
            key,
        )
        msg = {"type": "plugin", "action": action}
        if session_id is not None:
            msg["id"] = session_id
        if name is not None:
            msg["name"] = name
        if command is not None:
            msg["command"] = command
        if args is not None:
            msg["args"] = args
        if path is not None:
            msg["path"] = path
        if key is not None:
            msg["key"] = key
        if value is not None:
            msg["value"] = value
        resp = self._send_recv(msg, autostart=False)
        # list 响应合并 CLI 侧插件（daemon 仅返回 daemon 侧插件，kind=cli 的插件
        # 只存在于客户端 CliPluginHost 中）；仅在成功响应时合并
        if (
            action == "list"
            and resp.get("type") != "error"
            and self._cli_plugins is not None
        ):
            cli_plugins = resp.setdefault("plugins", [])
            existing_names = {p.get("name") for p in cli_plugins}
            for pname in self._cli_plugins.names():
                if pname not in existing_names:
                    info = self._cli_plugins.info_for(pname)
                    cli_plugins.append({
                        "name": pname,
                        "version": info["version"],
                        "state": "loaded",
                        "kind": "cli",
                        "cliOptions": info["cliOptions"],
                    })
        presenter.print_response(resp)

    def cmd_set_default(self, key: str, value):
        """set-default 命令：把默认配置写入守护进程内存（不写文件）

        Args:
            key:   内部配置键（下划线形态）。
            value: 归一化后的配置值。

        Returns:
            daemon 响应 dict（含设置后的全部全局默认 defaults）。
        """
        _logger.info("cmd_set_default: key=%s value=%r", key, value)
        msg = {"type": "set_default", "key": key, "value": value}
        return self._send_recv(msg)

    def cmd_wait(self, timeout: Optional[float] = None):
        """恒等待指定秒数（守护进程侧等待）

        Args:
            timeout: 等待秒数。None 时取 ConfigManager 的 timeout 配置（默认 120）。
        """
        if timeout is None:
            timeout = self._config.get("timeout")
        _logger.info("cmd_wait: timeout=%s", timeout)
        resp = self._send_recv({"type": "wait", "timeout": timeout})
        presenter.print_response(resp)

    def cmd_notice(self, nid: str):
        """查看通知的完整内容（--notify 订阅发布的完整命令响应）

        Args:
            nid: 通知标识（wait 命令返回的 notifications[].nid）。
        """
        _logger.info("cmd_notice: nid=%r", nid)
        if not nid or not isinstance(nid, str):
            presenter.print_response(Response.error("invalid notification nid"))
            return
        resp = self._send_recv({"type": "notice", "nid": nid})
        presenter.print_response(resp)

    def cmd_events(
        self,
        session_id: str,
        last: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ):
        """获取会话的所有事件"""
        _logger.info(
            "cmd_events: id=%r last=%s since=%s until=%s",
            session_id,
            last,
            since,
            until,
        )
        msg: dict = {"type": "events", "id": session_id}

        if since:
            msg["since"] = _parse_iso_time(since)
        if until:
            msg["until"] = _parse_iso_time(until)
        if last is not None:
            msg["last"] = last

        resp = self._send_recv(msg)
        presenter.print_response(resp)

    def cmd_kill(self, session_id: str):
        """终止指定会话"""
        _logger.info("cmd_kill: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            presenter.print_response(Response.error("invalid session id"))
            return
        from .daemonctl import is_running

        if not is_running():
            presenter.print_response(
                {"commandType": "kill", "code": -1, "msg": "Daemon not running"}
            )
            return
        try:
            resp = self._send_recv({"type": "kill", "id": session_id})
        except ConnectionError:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon not running, session likely dead",
            }
        except socket.timeout:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon unresponsive, session likely dead",
            }
        except OSError:
            resp = {
                "commandType": "kill",
                "code": 0,
                "msg": "daemon connection failed, session likely dead",
            }
        presenter.print_response(resp)

    def cmd_closewin(self, session_id: str, hwnd: int):
        """关闭指定 GUI 窗口"""
        _logger.info("cmd_closewin: id=%r hwnd=0x%X", session_id, hwnd)
        resp = self._send_recv(
            {
                "type": "closewin",
                "id": session_id,
                "hwnd": hwnd,
            }
        )
        presenter.print_response(resp)

    def cmd_mouse(
        self,
        session_id: str,
        action: dict,
        trigger: Optional[str] = None,
        newline: bool = False,
        fresh: bool = False,
        timeout: Optional[float] = None,
        encoding: Optional[str] = None,
        keep_ansi: Optional[bool] = None,
        lines: Optional[object] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        output_path: Optional[str] = None,
        response_format: Optional[str] = None,
        svg_compression_level: Optional[int] = None,
        snapshot_diff: bool = False,
        notify: bool = False,
    ):
        """发送鼠标动作到会话并等待输出

        Args:
            session_id: 会话标识
            action: 动作描述字典，由 CLI 解析后传入
        """
        _logger.info(
            "cmd_mouse: id=%r action=%s trigger=%r timeout=%s",
            session_id,
            action.get("action"),
            trigger,
            timeout,
        )
        original_timeout = timeout
        timeout, keep_ansi, encoding, newline, _ = self._apply_config_defaults(
            timeout=timeout,
            keep_ansi=keep_ansi,
            encoding=encoding,
            newline=newline,
        )
        # 仅命令行显式传 --timeout 才进入等待模式；
        # set-default/--default 配置的 timeout 只作为等待时长的取值，
        # 不应把无触发参数的 read/exec/send 变成等待模式
        explicit_timeout = original_timeout is not None

        if response_format is None:
            response_format = self._config.get("response_format") or "stream"
        if svg_compression_level is None:
            svg_compression_level = self._config.get("svg_compression_level")
            if svg_compression_level is None:
                # 配置未设置时默认等级 1；注意 0 是合法值（不压缩），不能用 or 兜底
                svg_compression_level = 1

        msg = {"type": "mouse", "id": session_id, **action}
        msg["newline"] = newline
        msg["fresh"] = fresh
        msg["keep_ansi"] = keep_ansi
        msg["timeout"] = timeout
        msg["explicit_timeout"] = explicit_timeout
        if trigger is not None:
            msg["trigger"] = trigger
        if encoding is not None:
            msg["encoding"] = encoding
        if idle_timeout is not None:
            msg["idle_timeout"] = idle_timeout
            msg["idle_after_first_output"] = idle_after_first_output
        if output_path or response_format == "svg":
            rf = _render_format_for(output_path, response_format)
            if rf:
                msg["render_format"] = rf
                if rf == "svg":
                    msg["svg_compression_level"] = svg_compression_level
        if snapshot_diff:
            msg["snapshot_diff"] = True
        if lines is not None:
            msg["lines"] = lines
        if notify:
            msg["notify"] = True

        # CLI 插件按会话挂载自动挂钩（exec --plugin 挂载到会话后，此处自动回调）
        self._activate_session_cli(session_id)
        if self.plugin_options:
            msg["pluginOptions"] = self.plugin_options

        client_defaults = self._get_client_defaults()
        if client_defaults:
            msg["client_defaults"] = client_defaults

        resp = self._send_recv(msg, output_path=output_path)
        self._merge_session_defaults(resp)

        if notify:
            # --notify 立即返回（无渲染结果），跳过 svg/文件输出处理
            presenter.print_response(resp)
            return
        _finalize_response(resp, response_format, output_path)

    def cmd_attend(self, session_id: str) -> int:
        """接管会话为完整实时终端（镜像 + 输入/鼠标/resize，不影响 web 端）

        长连接交互：进入后把 daemon 透传的原始输出字节流写入本机终端，
        控制台输入事件映射为帧发给 daemon。Ctrl+\\ 分离，Ctrl+C 透传会话。

        Returns:
            进程退出码（0=正常结束/分离，1=失败）。
        """
        _logger.info("cmd_attend: id=%r", session_id)
        if not session_id or not isinstance(session_id, str):
            presenter.print_response(Response.error("invalid session id"))
            return 1
        from .attend import run_attend

        return run_attend(self, session_id)