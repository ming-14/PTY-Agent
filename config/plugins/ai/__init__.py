"""ai 插件 —— CLI 侧 AI 二次分析（自包含）

kind=cli（见同目录 plugin.json）：在客户端进程内执行（daemon 不加载），依赖
同目录 common.py 桥接（run_aichat_capture，自动剥离 ANSI/thinking，失败回退）。
本目录为 aichat 自包含资产：common.py / config_manager.py / talk.py /
_finderror.py / bin/aichat.exe / config/config.yaml（主程序 src/ 无任何 aichat 引用）。

启用：kind=cli，经 exec `--plugin ai` 挂载到会话；挂载后对 exec/send/read/mouse
响应自动回调（宿主按钩子派发，无启用/禁用概念）。钩子：
- before_request：请求发送前；本插件未实现
- transform_response：对 exec/send/read/mouse 响应做 AI 分析，覆盖 outputStream

两种模式（按是否带 -o 自动判定）：
- responseOutput（无 -o）：把 outputStream 拼进 prompt 写临时文件，aichat -f 喂 AI
- fileOutput（有 -o）：从 daemon 渲染结果（svgContent/imageZ）写 -o 文件，
  aichat -f 读该文件（可喂视觉模型看图），并置 resp["aiFileWritten"] 供主程序
  跳过重复写入，保持"-o 文件=原始渲染、stdout=AI 输出"的语义

会话记忆：resp.uid 作为 aichat --session 名，按会话 uid 续聊。
配置：prompt/timeout 经 config/config.yaml 读取（config_manager.py 管理）。
失败处理：aichat 非零/超时/空输出/异常均回退原始 response 并追加 warning。
"""

import importlib.util
import logging
import os
import tempfile

from src.plugins.base import Plugin

_logger = logging.getLogger("pty-client")

# 同目录 common.py 桥接模块（bin/aichat.exe 配套脚本，随插件整体迁入）
_AICHAT_COMMON_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py")
)

_aichat_mod = None


def _load_aichat():
    """动态导入本插件目录 common.py（模块缓存）"""
    global _aichat_mod
    if _aichat_mod is not None:
        return _aichat_mod
    spec = importlib.util.spec_from_file_location("_aichat_common", _AICHAT_COMMON_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _aichat_mod = mod
    return mod


class AiPlugin(Plugin):
    """CLI 侧 AI 二次分析插件（元信息见同目录 plugin.json）"""

    def check_request(self, ctx, msg: dict):
        """请求发送前拦截：aichat.exe 缺失时拒绝 exec（AI 分析是 exec 挂载目的）

        仅拦截 exec：read/send/mouse 是会话读写操作，aichat 缺失时
        transform_response 优雅回退（原始输出 + warning），不阻断。
        """
        if msg.get("type") != "exec":
            return None
        try:
            mod = _load_aichat()
            mod.ensure_aichat()
        except Exception as e:
            return f"AI 分析不可用：{e}"
        return None

    def transform_response(self, ctx, resp: dict):
        # 仅处理命令成功结果且含输出文本
        if resp.get("type") == "error":
            return None
        if "outputStream" not in resp:
            return None

        text = resp.get("outputStream") or ""
        if not text.strip():
            return None

        try:
            mod = _load_aichat()
            settings = mod.load_settings()
        except Exception as e:
            _logger.warning("ai 插件配置读取失败: %s", e)
            settings = None
        if settings is None:
            resp["warning"] = "AI analysis skipped: config unavailable"
            return resp
        prompt = settings.get("prompt")
        timeout = settings.get("timeout")
        if not prompt or not timeout:
            resp["warning"] = "AI analysis skipped: config missing prompt/timeout"
            return resp

        uid = resp.get("uid")
        session_args = ["--session", str(uid), "--save-session"] if uid else []

        # fileOutput：有 -o 时先渲染文件再喂 AI（可喂视觉模型看图）
        output_path = ctx.output_path
        if output_path:
            return self._analyse_file(resp, session_args, prompt, timeout, output_path)
        return self._analyse_stream(resp, session_args, prompt, timeout, text)

    # ── 内部实现 ───────────────────────────────────────────

    def _analyse_stream(self, resp, session_args, prompt, timeout, text):
        """responseOutput：outputStream 拼进 prompt，写临时文件喂 AI"""
        full_prompt = f"{prompt}\n\n=== 待分析内容 ===\n{text}"
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(full_prompt)
            tmp_path = tmp.name
        finally:
            tmp.close()
        try:
            return self._call_aichat(resp, session_args + ["-f", tmp_path, prompt], timeout)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _analyse_file(self, resp, session_args, prompt, timeout, output_path):
        """fileOutput：写 -o 文件后喂 AI，置 aiFileWritten 避免主程序重复写入

        文件内容优先取 daemon 侧渲染结果（svgContent / imageZ，替代旧
        src.client.renderer 本地渲染），否则回退纯文本 outputStream。
        """
        import base64

        err = None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            svg = resp.get("svgContent")
            image_b64 = resp.get("imageZ")
            if svg:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(svg)
            elif image_b64:
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(image_b64))
            else:
                text = resp.get("outputStream") or resp.get("stdout") or ""
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(text)
        except (OSError, ValueError) as e:
            err = str(e)
        if err:
            _logger.warning("ai 插件 fileOutput 渲染失败: %s", err)
            resp["warning"] = f"AI analysis skipped: render failed: {err}"
            return resp
        resp["aiFileWritten"] = True
        return self._call_aichat(resp, session_args + ["-f", output_path, prompt], timeout)

    def _call_aichat(self, resp, args, timeout):
        """调用 aichat 并覆盖 outputStream；失败回退原 resp 追加 warning"""
        try:
            aichat = _load_aichat()
            code, output = aichat.run_aichat_capture(
                args, config=aichat.DEFAULT_CONFIG, timeout=timeout
            )
        except Exception as e:
            _logger.exception("ai 插件调用 aichat 异常")
            resp["warning"] = f"AI analysis failed (exception): {e}"
            return resp

        if code != 0 or not (output or "").strip():
            _logger.warning("ai 插件 aichat 返回 code=%s 输出为空，回退原响应", code)
            resp["warning"] = (
                f"AI analysis failed (aichat exit={code}, "
                "output_empty), fallback to original response"
            )
            return resp

        _logger.info("ai 插件分析完成，outputStream 已覆盖（%d 字符）", len(output))
        resp["outputStream"] = output
        return resp


plugin = AiPlugin
