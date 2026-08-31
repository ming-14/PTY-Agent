"""JSON 消息编解码

Message 类提供消息的编码与解码功能（JSON + UTF-8），
不依赖任何传输层（socket / 共享内存），供共享内存协议与上层复用。
所有方法为 @staticmethod，无状态设计。
"""

import json
import logging
from typing import Optional

_logger = logging.getLogger("pty-protocol")


class Message:
    """JSON 换行分隔消息

    每条消息为单行 JSON，以 ``\\n`` 结尾，UTF-8 编码。
    仅负责编解码，传输由共享内存协议（protocol/shm.py）承担。
    """

    @staticmethod
    def encode(obj: dict) -> bytes:
        """将 dict 编码为 JSON 行 + \\n + UTF-8 字节"""
        encoded = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        _logger.debug("Message.encode: type=%s len=%d", obj.get("type", "?"), len(encoded))
        return encoded

    @staticmethod
    def decode(data: bytes) -> Optional[dict]:
        """从 bytes 解码为 dict

        Args:
            data: JSON 字节。

        Returns:
            解码后的 dict，解析失败返回 None（不再抛出）。
        """
        try:
            decoded = json.loads(data.decode("utf-8"))
            _logger.debug("Message.decode: type=%s len=%d", decoded.get("type", "?"), len(data))
            return decoded
        except Exception as e:
            _logger.warning("Message.decode 失败: %s, data=%r", e, data[:200])
            return None
