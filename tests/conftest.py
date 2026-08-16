"""test 配置：添加项目根目录到 sys.path 以便导入 src 包"""
import logging
import sys
import os

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_bin_dir = os.path.join(_project_root, "bin")
if _bin_dir not in sys.path:
    sys.path.insert(0, _bin_dir)


@pytest.fixture(autouse=True)
def _isolate_logging():
    """autouse: 给 root logger 挂 NullHandler，防止测试日志输出到 stderr 干扰

    业务模块的 logger propagate=False 时不传播到 root，WARNING+ 日志会走
    logging.lastResort 输出到 stderr。此 fixture 不禁用日志（保留 caplog 能力），
    仅在 root 挂 NullHandler 兜底。
    """
    root = logging.getLogger()
    if not any(isinstance(h, logging.NullHandler) for h in root.handlers):
        root.addHandler(logging.NullHandler())
    yield
