"""前端 i18n node 测试 hook（pytest 集成）

调用 node 运行 tests/web/test_i18n_i18n.mjs，验证前端 i18n 字典与 settingsSchema 生成文案。
依赖本机 node（支持 ESM import）。
"""

import shutil
import subprocess

import pytest


def test_frontend_i18n_node_script():
    if shutil.which("node") is None:
        pytest.skip("Node.js 不存在，跳过前端 i18n 测试")
    script = "tests/web/test_i18n_i18n.mjs"
    result = subprocess.run(
        ["node", script],
        cwd=".",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout