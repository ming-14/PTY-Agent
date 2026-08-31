"""插件上下文输出 — <插件目录>/<插件名>.md 的读取与 CLI 输出

插件目录下存在 <插件名>.md 时，其内容作为插件上下文信息**输出给用户**（CLI）：

- 进程级插件：守护进程启动时（`app.py start`）由 CLI 输出
- 会话级/CLI 级插件：exec --plugin 启用时由 CLI 输出（stderr 信息区）

**只发一次**：每个 daemon 周期内，每插件文档只输出一次（daemon 启动时重置
状态）；文档内容变化（sha256 不同）时同周期内重新输出。

输出格式（统一）：

    [plugin <id> context]
    <文件内容>
    [plugin <id> context end]

约束：上限 64KB 截断；文件缺失/读取失败仅跳过，不影响插件加载。
"""

import hashlib
import json
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

from ..config.common import DATA_DIR
from ..logging import get_logger

_logger = get_logger("pty-plugins")

# 上下文文件上限（超出截断并追加提示）
CONTEXT_MAX_SIZE = 64 * 1024

# 插件文档发送状态文件（每 daemon 周期重置；gethelp 标记也在此，内存态）
STATE_FILE = os.path.join(DATA_DIR, "plugin-context-state.json")


# ── 目录扫描 ──────────────────────────────────────────────


def scan_plugin_dirs(plugin_dirs: List[str]) -> List[Tuple[str, List[str], str]]:
    """扫描插件目录清单，返回 [(id, kinds, path)]（读取 plugin.json 的 id/kind，kind 归一为列表）"""
    result = []
    for plugin_dir in plugin_dirs:
        manifest_file = os.path.join(plugin_dir, "plugin.json")
        if not os.path.isfile(manifest_file):
            continue
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("id"):
                kind_raw = data.get("kind", "")
                kinds = [kind_raw] if isinstance(kind_raw, str) else list(kind_raw)
                result.append((data["id"], kinds, plugin_dir))
        except (OSError, ValueError):
            continue
    return result


def find_plugin_dir(plugin_dirs: List[str], name: str) -> Optional[str]:
    """在插件目录列表中按清单 id 查找插件目录；未找到返回 None"""
    for plugin_id, _kind, path in scan_plugin_dirs(plugin_dirs):
        if plugin_id == name:
            return path
    return None


# ── 上下文文本读取 ────────────────────────────────────────


def context_text(plugin_name: str, plugin_dir: str) -> Optional[str]:
    """读取并格式化插件上下文文本（含标记）；无文件/读取失败返回 None"""
    context_file = os.path.join(plugin_dir, plugin_name + ".md")
    if not os.path.isfile(context_file):
        return None
    try:
        with open(context_file, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        _logger.warning("插件 %s 上下文文件读取失败，跳过输出: %s", plugin_name, e)
        return None
    if len(content) > CONTEXT_MAX_SIZE:
        content = content[:CONTEXT_MAX_SIZE] + "\n[context truncated]\n"
    return "[plugin %s context]\n%s\n[plugin %s context end]\n" % (
        plugin_name,
        content.rstrip("\n"),
        plugin_name,
    )


# ── 发送状态（只发一次） ─────────────────────────────────


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_context_state(state_file: str = None) -> dict:
    """读取文档发送状态；状态文件缺失/损坏返回 {}"""
    path = state_file or STATE_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_context_state(state: dict, state_file: str = None) -> None:
    """原子写入文档发送状态（临时文件 + rename）"""
    path = state_file or STATE_FILE
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def reset_context_state(state_file: str = None) -> None:
    """重置文档发送状态（daemon 启动时调用：新周期重新发送）

    gethelp 标记与自动注入状态同文件（内存态），一起重置。
    """
    path = state_file or STATE_FILE
    try:
        os.remove(path)
    except OSError:
        pass


# ── 输出上下文（含状态检查与标记） ────────────────────────


def output_context(
    stream, plugin_name: str, plugin_dir: str, state_file: str = None
) -> bool:
    """输出插件上下文文本到流（CLI stderr 等）

    每个 daemon 周期只发一次：已发送且内容未变化（sha256 相同）则跳过；
    发送成功后标记状态。文档缺失/读取失败/损坏返回 False 且不标记。
    """
    text = context_text(plugin_name, plugin_dir)
    if text is None:
        return False
    digest = _content_hash(text)
    state = load_context_state(state_file)
    entry = state.get(plugin_name)
    if isinstance(entry, dict) and entry.get("sent") and entry.get("contentHash") == digest:
        return False
    try:
        stream.write(text)
        stream.flush()
    except OSError:
        return False
    state[plugin_name] = {"sent": True, "sentAt": time.time(), "contentHash": digest}
    save_context_state(state, state_file)
    return True


# ── 批量输出辅助 ──────────────────────────────────────────


def disabled_plugin_names() -> set:
    """返回 registry.json 中显式禁用的插件名集合

    未记录的插件按默认启用处理（不在此列）；读取失败返回空集。
    """
    try:
        from ..config.plugins import PLUGIN_STATES
        return {pid for pid, enabled in PLUGIN_STATES.items() if not enabled}
    except Exception:
        return set()


def output_process_contexts(
    plugin_dirs: List[str], stream=None, disabled=None, state_file: str = None
) -> int:
    """输出已启用进程级插件的上下文（守护进程启动时调用）；返回输出数量

    disabled: 显式禁用的插件名集合（缺省从 registry.json 读取）。
    contextHidden 声明的插件跳过自动输出（plugin gethelp 按需查看）。
    """
    stream = stream or sys.stderr
    if disabled is None:
        disabled = disabled_plugin_names()
    count = 0
    for plugin_id, kind, path in scan_plugin_dirs(plugin_dirs):
        if plugin_id in disabled:
            continue
        if "process" not in kind:
            continue  # 仅进程级插件（kind 含 process）守护进程启动时输出
        if _is_context_hidden(path):
            continue
        if output_context(stream, plugin_id, path, state_file=state_file):
            count += 1
    return count


def _is_context_hidden(plugin_dir: str) -> bool:
    """读取插件清单 contextHidden 声明（读取失败视为未隐藏）"""
    try:
        manifest_file = os.path.join(plugin_dir, "plugin.json")
        with open(manifest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("contextHidden", False))
    except (OSError, ValueError):
        return False