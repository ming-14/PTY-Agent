"""插件存储 — 插件数据目录下的 kv / 文件 / sqlite 三种视图

数据目录：<DATA_DIR>/plugins/<plugin_id>/（DATA_DIR 来自 src.config.common），
与插件生命周期绑定：disable 保留（状态持久化），uninstall 整目录清除。
存储按插件命名空间隔离，插件只能访问自己的根目录。
"""

import json
import os
import shutil
import threading
from typing import Dict, List

from ..logging import get_logger

_logger = get_logger("pty-plugins")


class KvStore:
    """JSON 文件键值存储（线程安全，单文件，适合小状态）"""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._data = data
            except (OSError, ValueError):
                _logger.warning("kv 存储读取失败，重置: %s", self._path)

    def _flush(self) -> None:
        # 首次写入时创建父目录（存储根目录惰性创建）
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        # 紧凑分隔符 + 原子替换：tmp 写完后 os.replace 落盘，
        # 避免半写文件被读到（并发读/崩溃场景）
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, self._path)

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._flush()

    def delete(self, key: str) -> bool:
        with self._lock:
            if key not in self._data:
                return False
            del self._data[key]
            self._flush()
            return True

    def keys(self, prefix: str = "") -> List[str]:
        with self._lock:
            return [k for k in self._data if k.startswith(prefix)]

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._data)


class FileStore:
    """文件存储视图（路径限定在存储根目录内，防越界；根目录惰性创建）"""

    def __init__(self, root: str):
        self._root = os.path.abspath(root)

    def _resolve(self, rel: str) -> str:
        target = os.path.normpath(os.path.join(self._root, rel))
        if target != self._root and not target.startswith(self._root + os.sep):
            raise ValueError("存储路径越界: %s" % rel)
        return target

    def write(self, rel: str, data: bytes) -> str:
        target = self._resolve(rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(data)
        return target

    def read(self, rel: str) -> bytes:
        with open(self._resolve(rel), "rb") as f:
            return f.read()

    def delete(self, rel: str) -> bool:
        try:
            os.remove(self._resolve(rel))
            return True
        except FileNotFoundError:
            return False

    def list_files(self, prefix: str = "") -> List[str]:
        result = []
        for dirpath, _, filenames in os.walk(self._root):
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, self._root)
                if rel.startswith(prefix):
                    result.append(rel)
        return sorted(result)


class PluginStorage:
    """插件存储入口 — 三种视图共享同一根目录（根目录惰性创建）"""

    def __init__(self, root: str):
        self._root = os.path.abspath(root)
        self._kv: Dict[str, KvStore] = {}
        self._files: Dict[str, FileStore] = {}
        self._lock = threading.Lock()

    @property
    def root(self) -> str:
        return self._root

    def _ensure_root(self) -> None:
        os.makedirs(self._root, exist_ok=True)

    def kv(self, name: str = "state") -> KvStore:
        with self._lock:
            store = self._kv.get(name)
            if store is None:
                self._ensure_root()
                store = KvStore(os.path.join(self._root, name + ".json"))
                self._kv[name] = store
            return store

    def files(self, name: str = "files") -> FileStore:
        with self._lock:
            store = self._files.get(name)
            if store is None:
                self._ensure_root()
                store = FileStore(os.path.join(self._root, name))
                self._files[name] = store
            return store

    def sqlite(self, name: str = "db") -> str:
        """返回 sqlite 数据库文件路径（插件自行用标准库 sqlite3 打开）"""
        self._ensure_root()
        return os.path.join(self._root, name + ".db")

    def clear(self) -> None:
        """清除全部存储（uninstall 时调用）"""
        with self._lock:
            self._kv.clear()
            self._files.clear()
        shutil.rmtree(self._root, ignore_errors=True)