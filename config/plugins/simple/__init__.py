"""simple 插件 —— CLI 侧响应精简：只打印输出内容

kind=cli（见同目录 plugin.json）：在客户端进程内执行（daemon 不加载）。
render_response 钩子把 exec/send/read/mouse 等输出类响应渲染为自然文本
（替代默认 JSON 打印），末尾附统计尾巴：

    {输出内容 或 "(no output)"}

    ---
    {triggerReturnReason 值}
    {执行时间}ms

- 错误响应（type=error）不干预，走默认 JSON 打印。
- 无 outputStream 的响应（如 mouse 查询类）不干预，走默认 JSON 打印。
"""

from src.plugins.base import Plugin


class SimplePlugin(Plugin):
    """CLI 响应精简插件（元信息见同目录 plugin.json）"""

    def render_response(self, ctx, resp: dict):
        if resp.get("type") == "error":
            return None
        if "outputStream" not in resp:
            return None
        text = resp["outputStream"] or ""
        # 子进程模式：stderr 附加显示（ERR > 前缀）
        stderr_text = resp.get("stderrOutput") or ""
        if stderr_text:
            stderr_lines = [
                "ERR > " + line if line.strip() else line
                for line in stderr_text.splitlines()
            ]
            if text:
                stderr_block = "\n".join(stderr_lines)
                text = text + ("\n" if not text.endswith("\n") else "") + stderr_block
            else:
                text = "\n".join(stderr_lines)
        if not text:
            text = "(no output)"
        lines = [text]
        reason = resp.get("triggerReturnReason")
        elapsed_ms = resp.get("program", {}).get("debugInformation", {}).get("elapsedMs")
        if reason is not None or elapsed_ms is not None:
            lines.append("")
            lines.append("---")
            if reason is not None:
                lines.append(reason)
            if elapsed_ms is not None:
                lines.append("%.0fms" % elapsed_ms)
        return "\n".join(lines)


plugin = SimplePlugin
