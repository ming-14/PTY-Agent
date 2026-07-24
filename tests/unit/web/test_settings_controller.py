"""web/presentation/controllers/settings_controller.py 单元测试

验证 REST 端点（localStorage-only 模式）：
- GET  /api/settings         — 仅返回 web.toml 默认值（只读兜底，不合并用户文件）
- POST /api/settings         — 空实现（noop），始终返回 {ok: True}，不读写任何数据
- GET  /api/settings/schema  — 返回 {valid_keys, defaults}（无 restart_required_keys）

数据流背景：
- 用户自定义设置仅存浏览器 localStorage，不走服务端持久化
- 服务端 GET 仅提供 web.toml 默认值作为前端兜底
- POST 端点保留供未来扩展，当前为空实现
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.presentation.controllers import settings_controller


@pytest.fixture
def app():
    """创建挂载 settings 路由的 FastAPI 应用（控制器无状态，无需临时 store）"""
    app = FastAPI()
    app.include_router(settings_controller.create_settings_router())
    return app


@pytest.fixture
def client(app):
    """TestClient fixture"""
    return TestClient(app)


class TestGetSettings:
    """GET /api/settings 验证：仅返回 web.toml 默认值"""

    def test_get_returns_200(self, client):
        """GET 应返回 200"""
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_get_returns_json_object(self, client):
        """响应应为 JSON object"""
        resp = client.get("/api/settings")
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert isinstance(data, dict)

    def test_get_returns_defaults_only(self, client):
        """GET 仅返回 web.toml 默认值（与 settings_schema.get_defaults() 一致）

        不再合并用户文件（用户自定义仅存浏览器 localStorage）。
        """
        from src.web.domain import settings_schema
        expected = settings_schema.get_defaults()
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json() == expected

    def test_get_does_not_contain_deploy_keys(self, client):
        """GET 不应返回部署级配置 key（vncEnabled / fsEnabled 由 web.toml 管理）"""
        data = client.get("/api/settings").json()
        assert "remote.vncEnabled" not in data
        assert "remote.fsEnabled" not in data

    def test_get_idempotent(self, client):
        """多次 GET 返回一致结果（无状态、不读用户文件）"""
        first = client.get("/api/settings").json()
        second = client.get("/api/settings").json()
        assert first == second


class TestPostSettings:
    """POST /api/settings 验证：空实现（noop）"""

    def test_post_returns_ok(self, client):
        """POST 任意有效 JSON object 始终返回 {ok: True}"""
        resp = client.post("/api/settings", json={
            "basic.theme": "dark",
            "ime.enabled": True,
            "remote.fsFps": 15,
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_post_empty_body_returns_ok(self, client):
        """空 object 请求体也返回 {ok: True}"""
        resp = client.post("/api/settings", json={})
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_post_does_not_persist(self, client):
        """POST 不会持久化任何数据：POST 后 GET 仍返回原始默认值

        用户自定义设置仅存浏览器 localStorage，服务端不存储。
        """
        from src.web.domain import settings_schema
        before = client.get("/api/settings").json()

        client.post("/api/settings", json={
            "basic.theme": "light",
            "ime.candidateCount": 9,
        })

        after = client.get("/api/settings").json()
        assert after == before
        assert after == settings_schema.get_defaults()

    def test_post_does_not_return_restart_required(self, client):
        """POST 响应不包含 restart_required 字段（部署级配置已移除）"""
        resp = client.post("/api/settings", json={"basic.theme": "light"})
        data = resp.json()
        assert "restart_required" not in data

    def test_post_ignores_deploy_keys(self, client):
        """POST 包含 vncEnabled/fsEnabled 时仍返回 {ok: True}（noop，不处理）"""
        resp = client.post("/api/settings", json={
            "remote.vncEnabled": False,
            "remote.fsEnabled": False,
        })
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestGetSchema:
    """GET /api/settings/schema 验证"""

    def test_schema_returns_200(self, client):
        resp = client.get("/api/settings/schema")
        assert resp.status_code == 200

    def test_schema_returns_valid_keys(self, client):
        """schema 端点返回 valid_keys 列表"""
        data = client.get("/api/settings/schema").json()
        assert "valid_keys" in data
        assert isinstance(data["valid_keys"], list)
        assert len(data["valid_keys"]) > 0
        # 应包含核心 key
        assert "basic.theme" in data["valid_keys"]
        assert "ime.enabled" in data["valid_keys"]

    def test_schema_returns_defaults(self, client):
        """schema 端点返回 defaults（与 GET /api/settings 一致）"""
        data = client.get("/api/settings/schema").json()
        assert "defaults" in data
        assert isinstance(data["defaults"], dict)
        # 与直接 GET 一致
        assert data["defaults"] == client.get("/api/settings").json()

    def test_schema_valid_keys_sorted(self, client):
        """valid_keys 应为排序列表（便于前端展示）"""
        data = client.get("/api/settings/schema").json()
        assert data["valid_keys"] == sorted(data["valid_keys"])

    def test_schema_does_not_return_restart_required_keys(self, client):
        """schema 端点不返回 restart_required_keys（部署级配置已移除）"""
        data = client.get("/api/settings/schema").json()
        assert "restart_required_keys" not in data

    def test_schema_valid_keys_match_domain(self, client):
        """valid_keys 应与 settings_schema.VALID_KEYS 一致"""
        from src.web.domain import settings_schema
        data = client.get("/api/settings/schema").json()
        assert set(data["valid_keys"]) == settings_schema.VALID_KEYS

    def test_schema_does_not_contain_deploy_keys(self, client):
        """valid_keys 不应包含部署级配置 key"""
        data = client.get("/api/settings/schema").json()
        assert "remote.vncEnabled" not in data["valid_keys"]
        assert "remote.fsEnabled" not in data["valid_keys"]
