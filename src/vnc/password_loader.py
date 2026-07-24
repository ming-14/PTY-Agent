"""隔离加载 noVNC 模块的 vnc_password.py。

noVNC/src/vnc_password.py 只依赖标准库 + cryptography，无内部依赖，
但作为 noVNC 模块的一部分，它期望在 sys.path 中被 `import` 找到。
为避免污染主项目命名空间，这里用 importlib.util 以独立模块名加载。
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

_LOADED_MODULE: Optional[Any] = None
_LOADED_PATH: Optional[Path] = None


def load_vnc_password_module(novnc_src_dir: Path) -> Any:
    """隔离加载 noVNC 的 vnc_password 模块。

    Args:
        novnc_src_dir: noVNC 模块的 src 目录（包含 vnc_password.py）。

    Returns:
        已加载的模块对象，暴露 encrypt_vnc_password / write_ultravnc_ini 等函数。
    """
    global _LOADED_MODULE, _LOADED_PATH
    target_path = novnc_src_dir / "vnc_password.py"
    if _LOADED_MODULE is not None and _LOADED_PATH == target_path:
        return _LOADED_MODULE

    if not target_path.exists():
        raise FileNotFoundError(f"vnc_password.py not found at {target_path}")

    # 使用独立模块名加载，避免与主项目命名空间冲突
    module_name = "_pty_web_vnc_password"
    spec = importlib.util.spec_from_file_location(module_name, target_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to create spec for {target_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    _LOADED_MODULE = module
    _LOADED_PATH = target_path
    return module
