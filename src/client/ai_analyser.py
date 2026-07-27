"""AI 分析模块 — 把 PTY response 的 outputStream 交给 aichat 二次分析

在 exec/send/read/mouse 命令产出 response 后，可选地把 outputStream 文本交给
bin/aichat 做二次分析，用分析结果覆盖原 response 的 outputStream。

三种模式：
- none:           不分析，直接返回原 response
- fileOutput:     phase-1 文本已写入 -o 文件，phase-2 用 aichat -f <文件> 喂 AI
- responseOutput: phase-1 文本直接拼进 aichat prompt 喂 AI

失败处理：aichat 返回非零/超时/输出为空时，回退原始 response 并追加 warning 字段，
不阻断主流程。

会话记忆：response.uid（daemon 侧 Session.uid）作为 aichat --session 名，
实现按会话 uid 续聊。
"""

import importlib.util
import logging
import os
import tempfile

_logger = logging.getLogger("pty-client")

# bin/aichat/common.py 路径（基于本文件相对定位：<root>/bin/aichat/common.py）
_AICHAT_COMMON_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "bin", "aichat", "common.py")
)

_aichat_mod = None


def _reset_aichat_cache():
    """重置 aichat 模块缓存（供测试用）"""
    global _aichat_mod
    _aichat_mod = None


def _load_aichat():
    """动态导入 bin/aichat/common.py

    bin/aichat 不在 src 包结构内，用 importlib 按文件路径加载。
    模块缓存到 _aichat_mod 避免重复加载。

    Returns:
        bin/aichat/common.py 模块对象。
    """
    global _aichat_mod
    if _aichat_mod is not None:
        return _aichat_mod
    spec = importlib.util.spec_from_file_location("_aichat_common", _AICHAT_COMMON_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _aichat_mod = mod
    return mod


def _build_session_args(uid):
    """构造 aichat 会话续聊参数

    有 uid 时追加 --session <uid> --save-session，实现按会话 uid 续聊。
    无 uid（理论上不会发生，daemon 总会返回 uid）时不带 session。

    Args:
        uid: response 中的会话 uid，可能为 None。

    Returns:
        aichat 参数列表（可能为空）。
    """
    if uid:
        return ["--session", str(uid), "--save-session"]
    return []


def analyse_response(resp: dict, mode: str, prompt: str, output_file, timeout: int) -> dict:
    """对 PTY response 做 AI 分析，返回替换后的 response

    根据 mode 决定是否调用 aichat 以及如何喂数据：
    - none 或 error response 或空输出 → 直接返回原 resp
    - fileOutput → aichat -f <output_file>
    - responseOutput → 把 outputStream 拼进 prompt

    Args:
        resp:        phase-1 守护进程返回的 response 字典。
        mode:        分析模式（none/fileOutput/responseOutput）。
        prompt:      分析提示词。
        output_file: fileOutput 模式下用户 -o 指定的文件路径（须已写入）。
        timeout:     aichat 调用超时秒数。

    Returns:
        替换后的 response（outputStream 被覆盖为 AI 输出）；
        失败/none 时返回原 resp，失败时额外追加 warning 字段。
    """
    # none 模式直接放行
    if mode == "none":
        return resp

    # error response 不做分析（避免对错误信息做无意义分析）
    if resp.get("type") == "error":
        _logger.debug("ai_analyser: error response, skip analysis")
        return resp

    uid = resp.get("uid")
    session_args = _build_session_args(uid)
    aichat = _load_aichat()

    # 根据模式构造 aichat 调用参数
    tmp_file = None
    if mode == "fileOutput":
        if not output_file:
            _logger.warning("ai_analyser: fileOutput 模式缺少 -o 输出文件，回退原始 response")
            resp["warning"] = "AI analysis skipped: fileOutput requires -o/--output"
            return resp
        if not os.path.exists(output_file):
            _logger.warning("ai_analyser: 输出文件不存在 %s，回退原始 response", output_file)
            resp["warning"] = f"AI analysis skipped: output file not found: {output_file}"
            return resp
        # 用 -f 读输出文件，避免命令行参数编码问题
        aichat_args = session_args + ["-f", output_file, prompt]
        mode_desc = f"fileOutput({output_file})"
    elif mode == "responseOutput":
        text = resp.get("outputStream") or ""
        if not text.strip():
            _logger.warning("ai_analyser: outputStream 为空，跳过分析（uid=%s）", uid)
            resp["warning"] = "AI analysis skipped: outputStream is empty"
            return resp
        # 写入临时文件，避免 Windows 命令行参数 UTF-8 编码问题
        full_prompt = f"{prompt}\n\n=== 待分析内容 ===\n{text}"
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(full_prompt)
        tmp_path = tmp.name
        tmp.close()
        tmp_file = tmp_path
        aichat_args = session_args + ["-f", tmp_path, prompt]
        mode_desc = "responseOutput"
    else:
        _logger.warning("ai_analyser: 未知分析模式 %r，回退原始 response", mode)
        resp["warning"] = f"AI analysis skipped: unknown mode {mode!r}"
        return resp

    _logger.info("ai_analyser: 调用 aichat（mode=%s, uid=%s, timeout=%ss）",
                 mode_desc, uid, timeout)
    try:
        code, output = aichat.run_aichat_capture(
            aichat_args, config=aichat.DEFAULT_CONFIG, timeout=timeout,
        )
    except Exception as e:
        _logger.exception("ai_analyser: aichat 调用异常")
        resp["warning"] = f"AI analysis failed (exception): {e}"
        return resp
    finally:
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass

    if code != 0 or not output.strip():
        _logger.warning("ai_analyser: aichat 返回 code=%s output_len=%d，回退原始 response",
                        code, len(output))
        resp["warning"] = (
            f"AI analysis failed (aichat exit={code}, "
            f"output_empty={not output.strip()}), fallback to original response"
        )
        return resp

    # 成功：覆盖 outputStream
    _logger.info("ai_analyser: 分析完成，outputStream 已覆盖（%d 字符）", len(output))
    resp["outputStream"] = output
    return resp
