"""插件配置 — 清单默认 + config.yaml + 环境变量覆盖，schema 校验

分层（后层覆盖前层）：
1. plugin.json config.defaults（基准默认值）
2. 插件目录 config.yaml（用户可改，缺失时按默认值自动生成）
3. 环境变量 PTY_PLUGIN_<ID>_<KEY>（扁平键，最高优先，键名含 - 转 _）

config.schema.json（JSON Schema 子集，可选）在合并后校验，失败时插件加载报错
（BROKEN 状态，错误信息可见）。
"""

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple, Union

import yaml

from ..logging import get_logger

_logger = get_logger("pty-plugins")

CONFIG_FILE = "config.yaml"


class ConfigError(Exception):
    """插件配置错误（加载/校验失败）"""


def _coerce_env(value: str, default_type) -> Any:
    """环境变量字符串按默认值类型做类型转换；失败返回原字符串"""
    if default_type is bool:
        return value.lower() in ("true", "1", "yes")
    if default_type is int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if default_type is float:
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    if isinstance(default_type, (list, dict)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


# ── JSON Schema 子集校验 ─────────────────────────────────


def _schema_error(path: str, msg: str) -> str:
    return "%s: %s" % (path, msg) if path else msg


def _validate_schema(value, schema: dict, path: str = "") -> Optional[str]:
    """递归校验值是否符合 schema 子集；合法返回 None，否则返回错误描述"""
    if not isinstance(schema, dict):
        return None

    expected = schema.get("type")

    # 支持联合类型：["string", "null"] 等
    type_list = [expected] if isinstance(expected, str) else expected
    if isinstance(type_list, list):
        ok = False
        for t in type_list:
            if t == "null" and value is None:
                ok = True
                break
            if t == "string" and isinstance(value, str):
                ok = True
                break
            if t == "integer" and isinstance(value, int) and not isinstance(value, bool):
                ok = True
                break
            if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
                ok = True
                break
            if t == "boolean" and isinstance(value, bool):
                ok = True
                break
            if t == "array" and isinstance(value, list):
                ok = True
                break
            if t == "object" and isinstance(value, dict):
                ok = True
                break
        if not ok:
            return _schema_error(path, "应为 %s" % "/".join(type_list))

    # enum 校验
    if "enum" in schema:
        if isinstance(value, (str, int, float, bool)) and value not in schema["enum"]:
            return _schema_error(path, "不在 enum 允许范围")
        return None

    # 递归校验
    if expected == "object" or isinstance(expected, list) and "object" in type_list:
        if not isinstance(value, dict):
            return _schema_error(path, "应为 object")
        props = schema.get("properties", {})
        for key, sub_schema in props.items():
            if key in value:
                err = _validate_schema(value[key], sub_schema, "%s.%s" % (path, key) if path else key)
                if err:
                    return err
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(props)
            if unknown:
                return _schema_error(path, "含未知键 %s" % sorted(unknown))
        for key in schema.get("required", []):
            if key not in value:
                return _schema_error(path, "缺少必填键 %s" % key)
        return None

    if expected == "array" or isinstance(expected, list) and "array" in type_list:
        if not isinstance(value, list):
            return _schema_error(path, "应为 array")
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                err = _validate_schema(item, items, "%s[%d]" % (path, i))
                if err:
                    return err
        return None

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            return _schema_error(path, "长度小于 minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return _schema_error(path, "长度大于 maxLength")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            return _schema_error(path, "不匹配 pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return _schema_error(path, "小于 minimum %s" % schema["minimum"])
        if "maximum" in schema and value > schema["maximum"]:
            return _schema_error(path, "大于 maximum %s" % schema["maximum"])

    return None


# ── PluginConfig ──────────────────────────────────────────


class PluginConfig:
    """插件配置视图（线程安全读取）"""

    def __init__(
        self,
        plugin_id: str,
        plugin_dir: str,
        defaults: dict,
        schema: Optional[dict],
    ):
        self._id = plugin_id
        self._file = os.path.join(plugin_dir, CONFIG_FILE)
        self._defaults = dict(defaults or {})
        self._schema = schema
        self._lock = threading.Lock()
        self._values: dict = {}
        self._env_prefix = (
            "PTY_PLUGIN_" + plugin_id.upper().replace("-", "_")
        )
        self.load()

    def load(self) -> None:
        """重读配置：默认 + yaml + env 合并并校验；缺失 yaml 时自动生成"""
        values = dict(self._defaults)
        if os.path.isfile(self._file):
            try:
                with open(self._file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except (OSError, yaml.YAMLError) as e:
                raise ConfigError("config.yaml 读取失败: %s" % e)
            if not isinstance(data, dict):
                raise ConfigError("config.yaml 顶层必须为映射")
            values.update(data)
        else:
            self._write_defaults()
        # 环境变量覆盖
        for key in list(values):
            env_key = self._env_prefix + "_" + key.upper()
            if env_key in os.environ:
                values[key] = _coerce_env(os.environ[env_key], type(values[key]))
        if self._schema is not None:
            err = _validate_schema(values, self._schema, "")
            if err:
                raise ConfigError("配置校验失败: %s" % err)
        with self._lock:
            self._values = values

    def _write_defaults(self) -> None:
        """按默认值生成 config.yaml（自愈：缺失即生成，静默忽略失败）"""
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    self._defaults, f, allow_unicode=True, sort_keys=False
                )
        except OSError as e:
            _logger.warning("插件 %s 生成 config.yaml 失败: %s", self._id, e)

    def get(self, key: str, default=None):
        with self._lock:
            return self._values.get(key, default)

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._values)

    def set(self, key: str, value) -> None:
        """设置并持久化到 config.yaml（校验通过后生效）"""
        with self._lock:
            values = dict(self._values)
        values[key] = value
        if self._schema is not None:
            err = _validate_schema(values, self._schema, "")
            if err:
                raise ConfigError("配置校验失败: %s" % err)
        try:
            with open(self._file, "w", encoding="utf-8") as f:
                yaml.safe_dump(values, f, allow_unicode=True, sort_keys=False)
        except OSError as e:
            raise ConfigError("配置写入失败: %s" % e)
        with self._lock:
            self._values = values