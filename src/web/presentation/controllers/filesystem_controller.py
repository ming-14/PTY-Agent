"""文件系统浏览端点（展示层）。

提供目录列表 API：
- GET /api/listdir?path=... — 返回指定路径下的子目录名列表

认证与 settings 端点同源（auth_validator 可选）。
"""

import os
from typing import Callable, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ....logging import get_logger

_logger = get_logger("pty-web-fs")


def create_filesystem_router(
    auth_validator: Optional[Callable] = None,
) -> APIRouter:
    """创建文件系统浏览路由。

    Args:
        auth_validator: 认证校验函数，接收 Request 返回 bool；None 时跳过认证

    Returns:
        APIRouter: 包含 /api/listdir 端点的路由器
    """
    router = APIRouter(prefix="/api", tags=["filesystem"])

    @router.get("/listdir", response_class=JSONResponse)
    async def listdir(
        request: Request,
        path: str = Query(default="", description="要列出的目录路径"),
    ) -> JSONResponse:
        """列出指定路径下的子目录。

        Args:
            path: 目录路径（空 = 返回空列表）。

        Returns:
            JSONResponse: {"path": "...", "directories": ["dir1", "dir2", ...]}
                路径不存在或不可读时返回 {"error": "..."}，status 400。
        """
        if auth_validator is not None and not auth_validator(request):
            return JSONResponse(status_code=401, content={"error": "unauthorized"})
        if not path:
            return JSONResponse({"path": "", "directories": []})

        # 统一分隔符：后端用 os.path，前端传 / 即可
        norm_path = path.replace("/", os.sep).replace("\\", os.sep)
        if not os.path.isdir(norm_path):
            _logger.debug("listdir: path not found or not a directory: %r", norm_path)
            return JSONResponse(
                status_code=400,
                content={"error": f"directory not found: {path}"},
            )

        try:
            entries = sorted(
                e
                for e in os.listdir(norm_path)
                if os.path.isdir(os.path.join(norm_path, e))
            )
            # 路径统一用 / 返回（前端跨平台兼容）
            return JSONResponse({"path": path, "directories": entries})
        except PermissionError:
            _logger.warning("listdir: permission denied: %r", norm_path)
            return JSONResponse(
                status_code=403,
                content={"error": f"permission denied: {path}"},
            )
        except Exception as e:
            _logger.exception("listdir error: path=%r", norm_path)
            return JSONResponse(
                status_code=500,
                content={"error": str(e)},
            )

    return router