"""呈现层 — 前端展示契约

呈现层把守护进程返回的结构化响应 dict 转为前端可展示的形态，
与传输/业务完全解耦：

- cli.CliPresenter / print_response —— 自然语言终端输出（CLI 使用）
- 未来 MCP 可在此注入结构化呈现器（tools/content 格式）而不触及
  client.api 与守护进程协议。

契约：任何呈现器只需实现 ``present(resp) -> None``（副作用输出）。
"""

from typing import Protocol, runtime_checkable

from .cli import print_response, CliPresenter


@runtime_checkable
class Presenter(Protocol):
    """呈现器协议 — 接收结构化响应并输出"""

    def present(self, resp: dict) -> None:
        """呈现单个响应 dict"""
        ...


__all__ = ["Presenter", "print_response", "CliPresenter"]
