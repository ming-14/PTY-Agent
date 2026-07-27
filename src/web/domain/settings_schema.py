"""Web 设置项 Schema（领域层）。

定义网页端设置项的元数据：
- 有效 key 列表（点号路径，如 'basic.theme'）
- 默认值（来自 config.daemon，即 web.toml 的 [web_settings] / [fastscreen] 节）

该模块是后端默认值的 single source of truth。
前端 settingsSchema.js 负责渲染 UI 元数据（label/desc/options），
默认值以后端 GET /api/settings 返回的值为准。

数据流：
- 默认值：web.toml → config.daemon → GET /api/settings 返回（只读）
- 用户自定义：仅存浏览器 localStorage（不走服务端持久化）
- POST /api/settings 保留端点但当前为空实现（供未来扩展）

注：remote.vncEnabled / remote.fsEnabled 属部署级配置，由 web.toml 的
ENABLE_VNC / ENABLE_FASTSCREEN 提供，守护进程启动时读取，前端不可修改，
故不在此映射中。
"""

import logging
from typing import Any, Dict, Set

from ...config import daemon as _daemon_config

_logger = logging.getLogger("pty-web-settings")

# settings key → config 常量名 的映射
# config 常量在 config.daemon 模块中以扁平大写名导出（来自 web.toml flatten 后的顶层 key）
_KEY_TO_CONFIG_NAME: Dict[str, str] = {
    # ── 基本设置 ──
    "basic.theme": "DEFAULT_THEME",
    # ── 桌宠 ──
    "rikka.enabled": "RIKKA_ENABLED",
    # ── 输入法设置 ──
    "ime.enabled": "IME_ENABLED",
    "ime.candidateCount": "IME_CANDIDATE_COUNT",
    "ime.vertical": "IME_VERTICAL",
    "ime.defaultState": "IME_DEFAULT_STATE",
    # ime.keyboardLayout: 移动端键盘布局（compact=普通键盘 / full=全键盘）
    "ime.keyboardLayout": "IME_KEYBOARD_LAYOUT",
    # ime.toolbarDisplay: 工具栏显示模式（never/desktop_only/always）
    "ime.toolbarDisplay": "IME_TOOLBAR_DISPLAY",
    # ime.tbOpacity: 工具栏透明度（30-100 百分比）
    "ime.tbOpacity": "IME_TB_OPACITY",
    # ime.kbOpacity: 键盘透明度（30-100 百分比，仅移动端）
    "ime.kbOpacity": "IME_KB_OPACITY",
    # ime.tbScale: 工具栏缩放比例（0.8/1.0/1.2/1.5）
    "ime.tbScale": "IME_TB_SCALE",
    # ime.kbScale: 键盘缩放比例（0.8/1.0/1.2/1.5，仅移动端）
    "ime.kbScale": "IME_KB_SCALE",
    # ── 远程桌面连接（仅参数，启用状态由 web.toml 管理） ──
    "remote.fsFps": "FASTSCREEN_DEFAULT_FPS",
    "remote.fsBitrate": "FASTSCREEN_DEFAULT_BITRATE",
    "remote.fsStreamFormat": "FASTSCREEN_DEFAULT_STREAM_FORMAT",
}

# 有效 key 集合（供 /api/settings/schema 端点展示后端有默认值映射的 key）
VALID_KEYS: Set[str] = set(_KEY_TO_CONFIG_NAME.keys())


def get_defaults() -> Dict[str, Any]:
    """从 config.daemon 提取所有设置项的默认值。

    Returns:
        Dict[str, Any]: { settings_key: default_value }
    """
    defaults: Dict[str, Any] = {}
    for settings_key, config_name in _KEY_TO_CONFIG_NAME.items():
        if hasattr(_daemon_config, config_name):
            defaults[settings_key] = getattr(_daemon_config, config_name)
        else:
            _logger.warning(
                "settings_schema: config 常量 %s 未找到 (key=%s)，跳过",
                config_name, settings_key,
            )
    return defaults
