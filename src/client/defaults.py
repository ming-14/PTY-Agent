"""客户端默认配置混入 —— 调用级默认值应用与会话默认值回填。

配置默认值职责（ClientDefaultsMixin）：
- _apply_config_defaults：timeout/keep_ansi/encoding/newline/send_eol 默认值合并
- _get_client_defaults：收集与内置默认不同的客户端默认字段（随请求发送）
- _merge_session_defaults：daemon 返回的会话默认值回填到本地配置
- _maybe_save_encoding：编码记忆（探测结果落盘）
"""

from typing import Optional

from ..config.client import DEFAULT_TRIGGER_TIMEOUT
from .config_manager import _DEFAULTS as _DEFAULTS_MAP


class ClientDefaultsMixin:
    """调用级默认配置应用与 daemon 会话默认值回填"""

    def _apply_config_defaults(
        self,
        *,
        timeout: Optional[float] = None,
        keep_ansi: Optional[bool] = None,
        encoding: Optional[str] = None,
        newline: Optional[bool] = None,
        send_eol: Optional[str] = None,
    ) -> tuple:
        cfg = self._config.get_all()
        if timeout is None:
            timeout = cfg.get("timeout", DEFAULT_TRIGGER_TIMEOUT)
        if keep_ansi is None:
            keep_ansi = cfg.get("keep_ansi", False)
        if encoding is None:
            encoding = cfg.get("encoding")
        if newline is None:
            newline = cfg.get("newline", False)
        if send_eol is None:
            send_eol = cfg.get("send_eol", "\r")
        return timeout, keep_ansi, encoding, newline, send_eol

    def _get_client_defaults(self) -> dict:
        cfg = self._config.get_all()
        defaults = {}
        for key in (
            "timeout",
            "newline",
            "keep_ansi",
            "encoding",
            "debug",
            "send_eol",
            "response_format",
            "svg_compression_level",
            "terminal_size",
        ):
            val = cfg.get(key)
            if val is not None and val != _DEFAULTS_MAP.get(key):
                defaults[key] = val
        return defaults

    def _merge_session_defaults(self, resp: dict):
        session_defaults = resp.get("sessionDefaults")
        if not session_defaults or not isinstance(session_defaults, dict):
            return
        for key, val in session_defaults.items():
            if self._config.get(key) is None or self._config.get(
                key
            ) == _DEFAULTS_MAP.get(key):
                try:
                    self._config.set(key, val)
                except (ValueError, KeyError):
                    pass

    def _maybe_save_encoding(self, encoding: Optional[str]):
        if encoding is not None and self._config.get("encoding") != encoding:
            self._config.set("encoding", encoding)