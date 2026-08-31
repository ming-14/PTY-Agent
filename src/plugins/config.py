"""插件配置 — 清单默认 + 内存覆盖，schema 校验

分层（后层覆盖前层）：
1. plugin.json config.defaults（基准默认值）
2. plugin config set 的内存覆盖（守护进程内存记忆，重启即恢复默认）

与 daemon set-default 的"内存记忆"语义一致：不读写 config.yaml、不做任何持久化。
config.schema.json（JSON Schema 子集，可选）在合并后校验，失败时插件加载报错
（BROKEN 状态，错误信息可见）。
"""

import re
import threading
from typing import Optional


class ConfigError(Exception):
    """插件配置错误（加载/校验失败）"""


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
    """插件配置视图（内存态，线程安全读取）

    默认值来自 plugin.json config.defaults；plugin config set 仅覆盖内存，
    守护进程重启即恢复默认（与 daemon set-default 语义一致）。
    """

    def __init__(self, defaults: dict, schema: Optional[dict]):
        self._defaults = dict(defaults or {})
        self._schema = schema
        self._lock = threading.Lock()
        self._values: dict = {}
        self.reset()

    def reset(self) -> None:
        """重置为默认值（初始/恢复默认）"""
        values = dict(self._defaults)
        if self._schema is not None:
            err = _validate_schema(values, self._schema, "")
            if err:
                raise ConfigError("配置校验失败: %s" % err)
        with self._lock:
            self._values = values

    def get(self, key: str, default=None):
        with self._lock:
            return self._values.get(key, default)

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._values)

    def set(self, key: str, value) -> None:
        """设置配置值（仅内存，校验通过后生效，重启清空）"""
        with self._lock:
            values = dict(self._values)
        values[key] = value
        if self._schema is not None:
            err = _validate_schema(values, self._schema, "")
            if err:
                raise ConfigError("配置校验失败: %s" % err)
        with self._lock:
            self._values = values
