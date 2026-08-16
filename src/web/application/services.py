"""应用层跨用例服务。"""

import codecs
from typing import Any

from .ports import ConnectionContext, ThreadExecutor
from ...logging import get_logger

_logger = get_logger("pty-web")


class MessageEncoderService:
    """管理连接上的增量解码器，负责字节到文本的转换。"""

    def __init__(self, context: ConnectionContext):
        self._context = context

    def decode_output(self, session_id: str, session_encoding: str, data: bytes) -> str:
        """使用会话对应的增量解码器将字节转换为文本。

        当编码发生变化或解码失败时，会自动重建解码器。
        """
        dec_info = self._context.get_decoder(session_id)
        if not dec_info or dec_info["encoding"] != session_encoding:
            if dec_info:
                try:
                    dec_info["decoder"].decode(b"", final=True)
                except Exception:
                    pass
            dec_info = {
                "encoding": session_encoding,
                "decoder": codecs.getincrementaldecoder(session_encoding)(),
            }
            self._context.set_decoder(session_id, dec_info)

        decoder = dec_info["decoder"]
        try:
            return decoder.decode(data)
        except Exception:
            _logger.warning(
                "decoder reset for session %s encoding %s due to decode error",
                session_id,
                session_encoding,
            )
            text = data.decode(session_encoding, errors="replace")
            self._context.set_decoder(
                session_id,
                {
                    "encoding": session_encoding,
                    "decoder": codecs.getincrementaldecoder(session_encoding)(),
                },
            )
            return text

    def reset_decoder(self, session_id: str, encoding: str) -> Any:
        """重置/创建会话的增量解码器，并返回解码器信息。"""
        decoder = codecs.getincrementaldecoder(encoding)()
        dec_info = {"encoding": encoding, "decoder": decoder}
        self._context.set_decoder(session_id, dec_info)
        return dec_info


class SubscriptionService:
    """订阅相关的通用逻辑。"""

    def __init__(
        self,
        context: ConnectionContext,
        encoder: MessageEncoderService,
        executor: ThreadExecutor,
    ):
        self._context = context
        self._encoder = encoder
        self._executor = executor

    async def prepare_subscription(self, session_id: str, session: Any) -> dict:
        """准备订阅：返回 scrollback + visible snapshot（pty）或增量输出（subprocess）。

        - pty 模式：返回终端模型 snapshot（带 VT 颜色序列 + 每行前 CSI row+1;1H），
          与 ConPTY repaint 同源，显示正确
          （原始输出缓冲区含 ConPTY 增量光标序列，在 term.clear() 后重放会错位，不作为 replay）
        - 子进程模式：无终端，replay 为累积的 stdout 文本（增量输出，无 ANSI 语义）
        - 后续实时输出通过 publisher 持续推送

        - 额外返回 scrollback_ansi（wezterm 终端模型维护的历史区，仅 pty 模式）
        - 前端写入 xterm.js 推入 scrollback 区，F5 刷新/重开浏览器后历史不丢
        - 与 tmux 共享 grid 不同：daemon 与 browser 是 C/S 架构，必须通过 WS 传输

        编码探测仍然需要（后续实时输出解码用）。

        Returns:
            dict: {
                "replay": str,       # pty=visible snapshot；subprocess=stdout 全文
                "scrollback": str,   # scrollback ANSI 字符串（仅 pty；无历史时为 ""）
            }
        """
        # 仍需读取输出缓冲区用于编码探测（仅尾部样本，避免全量 100MB 拷贝）
        out_data = self._read_output_buffer(session)
        session.detect_encoding(out_data)
        enc = session.encoding or "utf-8"
        self._encoder.reset_decoder(session_id, enc)

        if getattr(session, "mode", "pty") == "subprocess":
            # 子进程模式：replay = stdout 全文（无终端快照）
            replay_text = session.get_output(encoding=enc)
            try:
                scrollback_ansi = session.capture_scrollback()
            except Exception:
                scrollback_ansi = ""
            return {"replay": replay_text, "scrollback": scrollback_ansi}

        # pty 模式：用终端模型 snapshot 作为 replay（而非原始输出缓冲区）
        # 终端模型已解析所有 VT 序列，snapshot 是当前屏幕的"真相"
        # snapshot 格式由 backends.py 的 render_ansi 控制（每行前 CSI row+1;1H）
        replay_text = session.get_snapshot(keep_ansi=True)

        # 捕获 scrollback 历史区（wezterm 终端模型维护，带 SGR 颜色）
        # 前端写入 xterm.js 时推入 scrollback 区，实现刷新后历史不丢
        try:
            scrollback_ansi = session.capture_scrollback()
        except Exception as e:
            _logger.warning(
                "prepare_subscription: capture_scrollback failed sid=%s: %s",
                session_id,
                e,
            )
            scrollback_ansi = ""

        return {"replay": replay_text, "scrollback": scrollback_ansi}

    @staticmethod
    def _read_output_buffer(session: Any) -> bytes:
        """读取会话输出缓冲区尾部样本（编码探测仅需尾部，与 output.py 一致）"""
        buf = session.output_buffer
        return buf.get_slice(max(0, buf.length - 4096))
