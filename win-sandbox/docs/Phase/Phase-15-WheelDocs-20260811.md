# Phase 15: wheel 打包 + 全量回归 + 文档更新

**Phase 编号**: 15
**Phase 名称**: wheel 打包、全量回归与文档更新
**创建日期**: 2026-08-11
**预计工期**: 2 个工作日
**负责人**: rikka
**状态**: ✅ 已完成
**上游依赖**: Phase 14（已完成，e2e 全部迁移到 pybind11）
**下游影响**: 无（系列收官）

---

## 1. Phase 目标

### 1.1 总体目标

Phase 10-14 已完成 pybind11 库形态迁移：构建骨架 → 核心绑定 → 删除 IPC → ETW/helpers → e2e 迁移。本 Phase 为系列收官：

1. **wheel 打包**：`pyproject.toml` 配置打包 `win_sandbox_native.pyd`，`pip install` 即可用
2. **全量回归**：e2e 25 套 + ctest 全绿，验证迁移完整性
3. **文档更新**：ARCHITECTURE / API_REFERENCE / USER_GUIDE / DEPLOYMENT / README 全部移除 IPC 描述，改为 pybind11 形态
4. **记忆文档**：记录迁移过程踩坑与决策（`docs/memory/`）
5. **版本号**：0.1.0 → 0.2.0（形态变更，破坏性改动）

### 1.2 非目标

- 不再改 C++ 核心 / bindings / helpers / 测试（Phase 10-14 已完成）
- 不实现新功能

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-15.1 | `pyproject.toml` 配置打包 `.pyd` | P0 | `python -m build python/` 产出 wheel 含 `win_sandbox/_native.pyd` |
| FR-15.2 | wheel 平台标记修正 | P0 | `py3-none-win_amd64`（复用 `fix_wheel_platform.py`） |
| FR-15.3 | `pip install` 验证 | P0 | 干净 venv 安装后 `import win_sandbox` 可用 |
| FR-15.4 | e2e 全量回归 | P0 | 25 套全绿 |
| FR-15.5 | ctest 全量回归 | P0 | 14 项全绿 |
| FR-15.6 | ARCHITECTURE.md 更新 | P0 | 移除 IPC 架构，改为 pybind11 in-process 架构 |
| FR-15.7 | API_REFERENCE.md 更新 | P0 | 全部 API 改为 pybind11 形态 |
| FR-15.8 | USER_GUIDE.md 更新 | P0 | 安装/使用改为 pybind11 |
| FR-15.9 | DEPLOYMENT.md 更新 | P0 | 部署改为 wheel + .pyd |
| FR-15.10 | README.md 更新 | P0 | 概述/快速开始改为 pybind11 |
| FR-15.11 | 记忆文档 | P1 | `docs/memory/2026-08-1x.md` 记录迁移踩坑 |
| FR-15.12 | 版本号 0.2.0 | P0 | `pyproject.toml` + `__init__.py` + CMake `project(VERSION)` |
| FR-15.13 | Lessons-Learned 更新 | P1 | 迁移过程踩坑追加到 `Lessons-Learned.md` |

---

## 3. 技术设计

### 3.1 wheel 打包

#### 3.1.1 `python/pyproject.toml`

```toml
[project]
name = "win-sandbox"
version = "0.2.0"  # 形态变更
# ... 其余不变

[tool.hatch.build.targets.wheel]
packages = ["win_sandbox"]

[tool.hatch.build.targets.wheel.force-include]
# pybind11 扩展（Release 构建）
"../build/bin/Release/win_sandbox_native.pyd" = "win_sandbox/_native.pyd"
```

#### 3.1.2 构建流程

```powershell
# 1. Release 构建 .pyd
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build

# 2. 构建 wheel
pip install build
python -m build python/

# 3. 修正平台标记
python python/scripts/fix_wheel_platform.py
```

产出：`python/dist/win_sandbox-0.2.0-py3-none-win_amd64.whl`

#### 3.1.3 `__init__.py` 加载 .pyd

```python
# python/win_sandbox/__init__.py
import os, sys
from pathlib import Path

# 优先用包内 _native.pyd（wheel 安装后）；回退到 build/bin（开发态）
_native_dir = Path(__file__).parent / "_native"
if _native_dir.is_dir():
    sys.path.insert(0, str(_native_dir))
else:
    _build = Path(__file__).resolve().parents[2] / "build" / "bin"
    if _build.is_dir():
        sys.path.insert(0, str(_build))

from win_sandbox_native import *  # noqa
from .helpers import *  # noqa

__version__ = "0.2.0"
```

### 3.2 文档更新要点

#### 3.2.1 ARCHITECTURE.md

- §2 总体架构：删除 IPC 层，改为「Python 解释器 ← win_sandbox_native.pyd (in-process)」
- 端口清单：删除 `IIpcServer` / `IEventEmitter` / `ICommandHandler` / `IProcessOutputSink`
- §3.5 消息协议：删除，改为「pybind11 直接调用 + 句柄传递 + 回调」
- §4 进程生命周期：删除 IPC 事件发射，改为「Python wait + 回调」
- §5.2 Job 通知：保留 IOCP 机制，事件出口改为 pybind11 回调

#### 3.2.2 API_REFERENCE.md

- 全部 API 改为 pybind11 形态（§2.2 目标 API）
- 删除 IPC 帧/分片/消息类型章节
- 新增 helpers 章节（read_pipe / write_pipe / WallClockTimer / StatsPoller / drain_*）
- 配置 schema 保留（SandboxInstance config）

#### 3.2.3 USER_GUIDE.md

- §3 安装：`pip install win-sandbox`（wheel 含 .pyd）
- §4 快速开始：改为 pybind11 直调示例
- §5 配置：保留（SandboxInstance config）
- §6 隔离能力：保留（语义不变）

#### 3.2.4 DEPLOYMENT.md

- §1 部署形态：删除 sandbox.exe，改为 win_sandbox_native.pyd
- §3 构建：`cmake --build` 产出 .pyd
- §4 分发：wheel 打包
- 删除 IPC 部署相关（命名管道/多客户端等）

#### 3.2.5 README.md

- 标题/概述：改为 pybind11 in-process 库
- 快速开始：改为 pybind11 示例
- 架构图：删除 IPC 层
- 测试：改为 `python tests/e2e/run_all_regression.py`
- 项目结构：更新

### 3.3 记忆文档

`docs/memory/2026-08-1x-pybind11-migration.md`：记录迁移过程关键决策、踩坑、修复。

---

## 4. 任务拆分

| 任务 | 描述 | 产出 |
|------|------|------|
| T15.1 | `pyproject.toml` 配置 .pyd 打包 + 版本 0.2.0 | 改写 |
| T15.2 | `__init__.py` 加载 .pyd 逻辑 | 改写 |
| T15.3 | Release 构建 + wheel 构建 + 平台标记修正 | 验证 |
| T15.4 | 干净 venv `pip install` 验证 | 验证 |
| T15.5 | e2e 全量回归（25 套） | 验证 |
| T15.6 | ctest 全量回归 | 验证 |
| T15.7 | ARCHITECTURE.md 更新 | 改写 |
| T15.8 | API_REFERENCE.md 更新 | 改写 |
| T15.9 | USER_GUIDE.md 更新 | 改写 |
| T15.10 | DEPLOYMENT.md 更新 | 改写 |
| T15.11 | README.md 更新 | 改写 |
| T15.12 | CMake project VERSION 0.2.0 | 改写 |
| T15.13 | 记忆文档 `docs/memory/2026-08-1x-pybind11-migration.md` | 新增 |
| T15.14 | Lessons-Learned 追加迁移踩坑 | 改写 |
| T15.15 | docs/archive 归档旧 IPC 文档 | 移动 |

---

## 5. 验收标准

### 5.1 wheel 验收

```powershell
# 构建
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build
python -m build python/
python python/scripts/fix_wheel_platform.py

# 验证 wheel 内容
python -c "import zipfile; z=zipfile.ZipFile('python/dist/win_sandbox-0.2.0-py3-none-win_amd64.whl'); print(z.namelist())"
# 应含 win_sandbox/_native.pyd

# 干净 venv 安装
python -m venv test_venv
test_venv\Scripts\pip install python/dist/win_sandbox-0.2.0-py3-none-win_amd64.whl
test_venv\Scripts\python -c "import win_sandbox; print(win_sandbox.__version__)"
# 输出 0.2.0
```

### 5.2 全量回归

```powershell
python tests/e2e/run_all_regression.py   # 25/25 PASS
ctest --test-dir build -C Release        # 14/14 PASS
```

### 5.3 文档验收

- 所有文档无 IPC 拗留描述
- API 示例可直接复制运行
- 架构图反映 pybind11 in-process 形态

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | wheel 内 .pyd 路径错误 | `force-include` 映射到 `win_sandbox/_native.pyd`；`__init__.py` 加载逻辑覆盖 wheel + 开发态 |
| 2 | 平台标记未修正导致跨平台安装 | `fix_wheel_platform.py` 改 `py3-none-win_amd64` |
| 3 | 文档更新遗漏 IPC 描述 | `rg "IPC|命名管道|sandbox.exe|SandboxClient|send_start_process" docs/` 确认无残留 |
| 4 | 版本号不一致 | 统一 `pyproject.toml` + `__init__.py` + CMake |
| 5 | 旧文档归档后链接失效 | 更新所有内部链接；归档到 `docs/archive/` |

---

## 7. 测试策略

1. **wheel 安装测试**：干净 venv 安装 + import + 基础调用
2. **全量回归**：e2e 25 套 + ctest 14 项
3. **文档可运行性**：README/USER_GUIDE 示例可直接复制运行

---

## 8. 系列收官

Phase 10-15 完成后，win-sandbox 形态从「sandbox.exe + IPC + Python 客户端」彻底迁移到「win_sandbox_native.pyd (pybind11 in-process)」：

- **C++ 核心**：Job Object + AppContainer + 文件系统隔离 + WFP 网络 + ETW 监控 + Server Silo + 全局配额（隔离语义不变）
- **调用形态**：Python 直接 import + 直调，句柄 in-process 共享，无 IPC 往返
- **输出处理**：Python 自己 ReadFile 原始字节，终端语义/rawio Python 说了算
- **事件通知**：Job IOCP / ETW 通过 pybind11 回调推 Python
- **后台线程**：wall_clock / stats 移到 Python 端（threading.Timer / Thread）
- **测试**：25 套 e2e 全部基于 pybind11 直调
- **打包**：wheel 含 .pyd，`pip install` 即用

**后续可独立推进的方向**（非本系列）：
- ConPTY 终端语义（`docs/design/SandboxConPty-Terminal-Enhancement-20260811.md`）
- 更多隔离能力（minifilter 等）
- 跨平台（仅 Windows，无计划）
