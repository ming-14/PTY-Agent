"""设置控制器（展示层）。

提供用户设置的 REST 端点：
- GET  /api/settings         — 读取 web.toml 默认值（只读，作为前端兜底默认值来源）
- POST /api/settings         — 空实现（用户自定义设置仅存浏览器 localStorage，不走服务端持久化）
- GET  /api/settings/schema  — 返回设置项元数据（有效 key 列表 + 默认值）

数据流：
- 默认值：web.toml → config.daemon → GET /api/settings 返回
- 用户自定义：仅存浏览器 localStorage（前端 settingsStore 管理）
- POST 端点保留供未来扩展（如需服务端持久化的字段），当前为空实现

设计说明：
remote.vncEnabled / remote.fsEnabled 属部署级配置，由 web.toml 的
ENABLE_VNC / ENABLE_FASTSCREEN 提供，守护进程启动时读取，前端不可修改，
故不在 VALID_KEYS 中，GET 不返回。
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...domain.settings_schema import (
    VALID_KEYS,
    get_defaults,
)

_logger = logging.getLogger("pty-web-settings")


def create_settings_router() -> APIRouter:
    """创建设置 REST 路由。

    Returns:
        APIRouter: 包含 /api/settings* 端点的路由器
    """
    router = APIRouter(prefix="/api/settings", tags=["settings"])

    @router.get("", response_class=JSONResponse)
    async def get_settings(request: Request) -> JSONResponse:
        """读取 web.toml 默认值（只读）。

        前端启动时调用，作为 localStorage 缓存未命中时的默认值来源。
        不再读取 web_user_choice.json（用户自定义仅存浏览器 localStorage）。

        Returns:
            JSONResponse: 扁平 key→value 的默认值对象
        """
        client = request.client.host if request.client else "-"
        try:
            defaults = get_defaults()
            _logger.info(
                "GET /api/settings from %s: %d keys (web.toml defaults)",
                client, len(defaults),
            )
            return JSONResponse(defaults)
        except Exception:
            _logger.exception("GET /api/settings failed")
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "message": "Failed to load settings"},
            )

    @router.post("", response_class=JSONResponse)
    async def save_settings(request: Request) -> JSONResponse:
        """空实现：用户自定义设置仅存浏览器 localStorage，不走服务端持久化。

        保留端点供未来扩展（如需服务端持久化的字段）。当前不读取/写入任何数据。

        Returns:
            JSONResponse: { ok: True }
        """
        client = request.client.host if request.client else "-"
        _logger.info("POST /api/settings from %s: noop (localStorage-only mode)", client)
        return JSONResponse({"ok": True})

    @router.get("/schema", response_class=JSONResponse)
    async def get_schema(request: Request) -> JSONResponse:
        """返回设置项元数据（有效 key 列表 + 默认值）。

        前端可用于校验 key 合法性。

        Returns:
            JSONResponse: { valid_keys, defaults }
        """
        client = request.client.host if request.client else "-"
        _logger.info("GET /api/settings/schema from %s", client)
        return JSONResponse({
            "valid_keys": sorted(VALID_KEYS),
            "defaults": get_defaults(),
        })

    return router
