"""消息签名抽象（协议域）——消息完整性签名接口

消息签名属于通信协议领域：Message 层在 send 时调用 sign() 追加签名，
recv 时调用 verify_and_strip() 验签。抽象定义在协议层（被依赖方），
具体实现（HMAC-SHA256 / Ed25519）在 auth 包实现，依赖方向 auth → protocol。
"""

import json
from abc import ABC, abstractmethod
from typing import Optional


class MessageSigner(ABC):
    """消息签名器 — 签名和验证消息完整性

    同时用于守护进程和客户端。发送时调用 sign() 附加签名，
    接收时调用 verify_and_strip() 验证并移除签名字段。
    """

    @abstractmethod
    def sign(self, obj: dict) -> dict:
        """签名消息，返回带签名的消息副本

        Args:
            obj: 待签名的消息字典。

        Returns:
            带签名字段的消息副本（不修改原 dict）。
        """
        ...

    def sign_bytes(self, obj: dict) -> bytes:
        """签名并直接产出完整 wire 字节（含签名字段的 JSON 行 + \\n）

        默认实现：sign() 后按与 Message.encode 一致的格式编码。
        子类覆写为单次序列化（签名器内部已产出规范 JSON，
        在规范字节上拼接签名字段，避免 send 侧二次 dumps——MB 级消息成本翻倍）。

        Args:
            obj: 待签名的消息字典。

        Returns:
            可直接 sendall 的完整 wire 字节。
        """
        signed = self.sign(obj)
        return (json.dumps(signed, ensure_ascii=False) + "\n").encode("utf-8")

    @abstractmethod
    def verify(self, obj: dict, signature: str) -> bool:
        """验证消息签名

        Args:
            obj: 消息字典（不含签名字段）。
            signature: 待验证的签名字符串。

        Returns:
            True 表示签名验证通过。
        """
        ...

    @abstractmethod
    def verify_and_strip(self, msg: dict) -> Optional[dict]:
        """验证消息签名并移除签名字段

        Args:
            msg: 接收到的消息字典（包含签名字段）。

        Returns:
            验证通过时返回移除签名字段后的消息副本，失败返回 None。
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """签名器名称（用于日志）"""
        ...

    @property
    @abstractmethod
    def signature_fields(self) -> tuple:
        """该签名器写入消息的签名字段名元组

        用于接收端判断消息是否携带签名（区分"有签名需验证"与"无签名"）。
        例如 HmacMessageSigner 返回 ("_sig",)，Ed25519MessageSigner 返回 ("_sig_ed25519",)。
        """
        ...
