"""插件测试辅助 — 构造清单与临时插件目录

测试统一经真实清单/加载路径构造插件（manifest → 目录 → load_plugin_dir），
避免直接注入类绕过声明校验；轻量断言场景可直接构造 PluginManifest 挂到实例。
"""

import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.plugins.manifest import PluginManifest  # noqa: E402


def make_manifest(
    plugin_id: str,
    kind: str = "session",
    triggers=None,
    hooks=None,
    message_types=None,
    needs_io: bool = False,
    commands=None,
    auto_load=None,
    poll_interval=None,
    events=None,
    permissions=None,
    config_defaults=None,
    config_schema=None,
    path: str = "/virtual",
) -> PluginManifest:
    """构造清单对象（host 单测用，无需落盘）"""
    return PluginManifest(
        id=plugin_id,
        version="1.0",
        kind=kind,
        path=path,
        triggers=list(triggers or []),
        hooks={k: dict(v) for k, v in (hooks or {}).items()},
        message_types=list(message_types or []),
        needs_io=needs_io,
        commands=list(commands or []),
        auto_load=auto_load,
        poll_interval=poll_interval,
        events=list(events or []),
        permissions=list(permissions or []),
        config_defaults=dict(config_defaults or {}),
        config_schema=config_schema,
    )


def attach_manifest(plugin, manifest: PluginManifest) -> PluginManifest:
    """把清单挂到插件实例（宿主按实例 manifest 注册钩子）"""
    plugin.manifest = manifest
    return manifest


def write_plugin_dir(tmp_path, plugin_id: str, kind: str, src: str,
                     manifest_extra=None) -> str:
    """在 tmp_path 下创建插件目录（plugin.json + __init__.py），返回目录路径"""
    pdir = tmp_path / plugin_id
    pdir.mkdir()
    manifest = {
        "id": plugin_id,
        "version": "1.0",
        "kind": kind,
        "description": "test plugin",
    }
    if manifest_extra:
        manifest.update(manifest_extra)
    (pdir / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pdir / "__init__.py").write_text(src, encoding="utf-8")
    return str(pdir)
