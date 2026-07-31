"""认证控制器（展示层）。

提供 Web 密码认证的 REST 端点：
- POST /api/auth/login   — 校验密码，创建会话，Set-Cookie + 返回 token
- POST /api/auth/logout  — 撤销会话，清除 Cookie
- GET  /api/auth/status  — 返回认证状态（enabled + authenticated）
- GET  /login            — 返回登录页 HTML

设计说明：
- 配置文件存储 SHA-256 哈希值（WEB_PASSWORD_HASH），不存明文
- 登录时将提交的密码做 SHA-256 后与存储的哈希比较
- 不使用 HTTP 中间件/重定向，由各受保护端点自行校验
- 前端检测到未授权错误后自行跳转 /login

认证方式（双通道）：
1. Cookie（pty_session）：同源请求自动携带，SameSite=Lax
2. X-Auth-Token 头 / authToken query param：跨域场景，前端存 localStorage
后端同时支持两种方式，优先检查 X-Auth-Token 头，其次检查 Cookie。
"""

import hashlib
import logging
import os

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from ...infrastructure.auth.session_store import SessionStore

_logger = logging.getLogger("pty-web-auth")

_COOKIE_NAME = "pty_session"
_TOKEN_HEADER = "x-auth-token"
_TOKEN_QUERY_PARAM = "authToken"
_COOKIE_MAX_AGE = 86400  # 24h
_LOGIN_HTML_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")


def hash_password(password: str) -> str:
    """对明文密码做 SHA-256 哈希。

    用于：
    1. 登录时将用户提交的密码哈希后与存储的哈希比较
    2. 生成配置文件所需的哈希值（CLI / 脚本调用）

    Args:
        password: 明文密码

    Returns:
        SHA-256 hex 哈希值
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _extract_token_from_request(request: Request) -> str:
    """从请求中提取认证 token。

    优先检查 X-Auth-Token 头（跨域场景），其次检查 Cookie（同源场景）。

    Args:
        request: FastAPI Request

    Returns:
        token 字符串，无 token 时返回空串
    """
    token = request.headers.get(_TOKEN_HEADER, "")
    if token:
        return token
    return request.cookies.get(_COOKIE_NAME, "")


def _extract_token_from_ws(ws: WebSocket) -> str:
    """从 WebSocket 连接中提取认证 token。

    优先检查 authToken query param（跨域场景），其次检查 Cookie（同源场景）。

    Args:
        ws: FastAPI WebSocket

    Returns:
        token 字符串，无 token 时返回空串
    """
    token = ws.query_params.get(_TOKEN_QUERY_PARAM, "")
    if token:
        return token
    return ws.cookies.get(_COOKIE_NAME, "")


def validate_request_auth(request: Request, session_store: SessionStore) -> bool:
    """校验 HTTP 请求的认证 token 是否有效。

    供各受保护端点调用，不抛异常，返回 bool。
    同时支持 X-Auth-Token 头和 Cookie。

    Args:
        request: FastAPI Request
        session_store: SessionStore 实例

    Returns:
        True 表示已认证
    """
    token = _extract_token_from_request(request)
    return session_store.validate(token)


def validate_ws_auth(ws: WebSocket, session_store: SessionStore) -> bool:
    """校验 WebSocket 连接的认证 token 是否有效。

    同时支持 authToken query param 和 Cookie。

    Args:
        ws: FastAPI WebSocket
        session_store: SessionStore 实例

    Returns:
        True 表示已认证
    """
    token = _extract_token_from_ws(ws)
    return session_store.validate(token)


def create_auth_router(session_store: SessionStore, password_hash: str) -> APIRouter:
    """创建认证 REST 路由。

    Args:
        session_store: SessionStore 实例
        password_hash: SHA-256 哈希值（来自配置文件 WEB_PASSWORD_HASH）

    Returns:
        APIRouter: 包含 /api/auth/* 和 /login 端点的路由器
    """
    router = APIRouter(tags=["auth"])

    @router.post("/api/auth/login", response_class=JSONResponse)
    async def login(request: Request) -> JSONResponse:
        """校验密码，创建会话，Set-Cookie + 返回 token。

        Request Body: {"password": "xxx"}
        成功: {"ok": true, "token": "xxx"} + Set-Cookie
        失败: {"error": "unauthorized"} 401

        token 同时返回到 body 中，供跨域场景前端存 localStorage 使用。
        """
        try:
            body = await request.json()
        except Exception:
            _logger.warning("login: invalid body from %s", request.client.host if request.client else "-")
            return JSONResponse(status_code=400, content={"error": "invalid_body"})

        submitted = body.get("password", "")

        if password_hash:
            # 有密码哈希时，校验密码（空密码也放行）
            if submitted:
                submitted_hash = hash_password(submitted)
                if submitted_hash != password_hash:
                    remote = request.client.host if request.client else "-"
                    _logger.warning("login: wrong password from %s", remote)
                    return JSONResponse(status_code=401, content={"error": "unauthorized"})
            # 空密码直接放行
        # 无密码哈希时，任何密码都放行（免密模式）

        token = session_store.create(max_age=_COOKIE_MAX_AGE)
        # 同时设置 Cookie（同源场景）和返回 token（跨域场景）
        response = JSONResponse({"ok": True, "token": token})
        response.set_cookie(
            key=_COOKIE_NAME,
            value=token,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/",
        )
        remote = request.client.host if request.client else "-"
        _logger.info("login: success from %s", remote)
        return response

    @router.post("/api/auth/logout", response_class=JSONResponse)
    async def logout(request: Request) -> JSONResponse:
        """撤销会话，清除 Cookie。"""
        token = _extract_token_from_request(request)
        if token:
            session_store.revoke(token)
        response = JSONResponse({"ok": True})
        response.delete_cookie(key=_COOKIE_NAME, path="/")
        remote = request.client.host if request.client else "-"
        _logger.info("logout: from %s", remote)
        return response

    @router.get("/api/auth/status", response_class=JSONResponse)
    async def auth_status(request: Request) -> JSONResponse:
        """返回认证状态。

        前端启动时调用，判断是否需要跳转登录页。
        enabled=false 表示免密模式，前端跳过登录页直接进入。
        同时支持 X-Auth-Token 头和 Cookie。

        Returns:
            {"enabled": true/false, "authenticated": true/false}
        """
        token = _extract_token_from_request(request)
        authenticated = session_store.validate(token) if token else False
        return JSONResponse({
            "enabled": bool(password_hash),
            "authenticated": authenticated,
        })

    @router.get("/login", response_class=FileResponse)
    async def login_page() -> FileResponse:
        """返回登录页 HTML。"""
        return FileResponse(os.path.join(_LOGIN_HTML_DIR, "login.html"))

    return router
