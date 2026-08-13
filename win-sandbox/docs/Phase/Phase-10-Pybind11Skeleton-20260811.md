# Phase 10: pybind11 库骨架与构建系统

**Phase 编号**: 10
**Phase 名称**: pybind11 库骨架与构建系统
**创建日期**: 2026-08-11
**预计工期**: 1 个工作日
**负责人**: rikka
**状态**: ✅ 已完成（2026-08-11 实施完毕，pybind11 v3.1.0 + Python 3.11.9，双产物构建 + 23/23 e2e 回归）
**上游依赖**: Phase 9（已完成，进程树 IPC 能力齐备）
**下游影响**: Phase 11（绑定层实现）、Phase 12（删除 IPC）、Phase 15（wheel 打包）

---

## 1. Phase 目标

### 1.1 总体目标

win-sandbox 现有形态为 `sandbox.exe`（C++ 进程）+ 命名管道 IPC + Python 客户端。本系列 Phase（10-15）将核心编译为 pybind11 扩展（`win_sandbox_native.pyd`），Python 直接 in-process 调用，消除 IPC 往返，stdin/stdout 管道句柄直接传给 Python 由其 ReadFile/WriteFile 原始字节。

Phase 10 为系列第一阶段，**仅搭建构建骨架**，不实现任何业务绑定：

1. 引入 pybind11 作为 git submodule（third_party/pybind11）
2. CMake 新增 `win_sandbox_native` 目标（pybind11_add_module），链接现有全部 infra/adapters/core 源文件
3. 新增 `src/bindings/` 目录，含最小空模块 `module.cpp`（仅 `PYBIND11_MODULE(win_sandbox_native, m) { m.doc() = ...; }`）
4. 构建产出 `build/bin/win_sandbox_native.pyd`，Python 可 `import win_sandbox_native`（空模块）
5. `pyproject.toml` 预备打包 `.pyd`（本 Phase 不构建 wheel，仅配置就位）

**本 Phase 不改动任何现有 C++ 源码**（main.cpp / infra/* / core/* / adapters/* 全部不动），仅在现有源码树外新增绑定骨架与构建配置。sandbox.exe 仍可正常构建运行，23 套件 e2e 测试不受影响。

### 1.2 非目标

- 不实现任何业务绑定（SandboxInstance / Process / 配置等绑定留待 Phase 11）
- 不改动现有 C++ 核心（SandboxInstance / StartProcessUseCase 等保持 IPC 形态可用）
- 不删除任何现有代码（IPC 删除在 Phase 12）
- 不迁移 e2e 测试（Phase 14）
- 不构建/发布 wheel（Phase 15）
- 不引入跨平台兼容（仅 Windows x64）

---

## 2. 功能需求

### 2.1 功能需求清单

| 需求编号 | 需求描述 | 优先级 | 验收标准 |
|----------|----------|--------|----------|
| FR-10.1 | 引入 pybind11 git submodule | P0 | `third_party/pybind11/` 存在，`.gitmodules` 含条目，`git submodule update --init` 可拉取 |
| FR-10.2 | CMake 新增 `win_sandbox_native` 目标 | P0 | `cmake --build build` 产出 `build/bin/win_sandbox_native.pyd` |
| FR-10.3 | pybind11 模块可 import | P0 | `python -c "import sys; sys.path.insert(0, r'build/bin'); import win_sandbox_native; print(win_sandbox_native.__doc__)"` 成功输出 |
| FR-10.4 | sandbox.exe 仍可构建 | P0 | `cmake --build build` 同时产出 `sandbox.exe`，现有 e2e 全量通过 |
| FR-10.5 | pyproject.toml 预备 .pyd 打包配置 | P1 | `[tool.hatch.build.targets.wheel.force-include]` 含 `.pyd` 条目（注释或条件化，不破坏现有 wheel 构建） |
| FR-10.6 | 构建脚本更新 | P1 | `build_cmd.bat` 支持 pybind11 构建（需 Python 开发头路径） |

### 2.2 构建产物

| 产物 | 路径 | 说明 |
|------|------|------|
| pybind11 扩展 | `build/bin/win_sandbox_native.pyd` | 本 Phase 为空模块，Phase 11 起填充绑定 |
| sandbox.exe | `build/bin/sandbox.exe` | 保留，Phase 12 删除 |

---

## 3. 技术设计

### 3.1 pybind11 引入方式

采用 git submodule（与现有 WIL/nlohmann_json/spdlog 一致）：

```powershell
# 国内镜像
git -c url.https://v4.gh-proxy.org/.insteadOf=https://github.com/. submodule add https://github.com/pybind/pybind11 third_party/pybind11
```

pybind11 提供 CMake 集成：`add_subdirectory(third_party/pybind11)` 后可用 `pybind11_add_module()`。

### 3.2 CMake 改造

#### 3.2.1 顶层 `CMakeLists.txt`

新增 pybind11 子目录（在 spdlog 之后、`add_subdirectory(src)` 之前）：

```cmake
# --- pybind11 (Python 绑定) ---
# Phase 10：pybind11 扩展，in-process 库形态
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)
set(PYBIND11_FINDPYTHON ON CACHE BOOL "" FORCE)
add_subdirectory(third_party/pybind11)
```

> `find_package(Python3 ... Development)` 确保 Python 开发头（Python.h）路径可用。pybind11 2.11+ 支持 `PYBIND11_FINDPYTHON` 走 CMake FindPython 路径。

#### 3.2.2 `src/CMakeLists.txt`

新增 `win_sandbox_native` 目标，**复用 sandbox.exe 的全部源文件**（Phase 10 不拆分，Phase 11 起按需调整）：

```cmake
# pybind11 扩展（Phase 10：空模块骨架）
pybind11_add_module(win_sandbox_native
    bindings/module.cpp
    # 复用 sandbox 全部源文件（Phase 11 起按需删减 IPC 专属文件）
    infra/logging/Logger.cpp
    infra/ipc/FrameCodec.cpp
    infra/ipc/NamedPipeServerImpl.cpp
    infra/job/JobObjectImpl.cpp
    infra/process/ProcessLauncherImpl.cpp
    infra/process/StreamReader.cpp
    infra/stats/StatsCollectorImpl.cpp
    infra/appcontainer/AppContainerImpl.cpp
    infra/appcontainer/CapabilityMapping.cpp
    infra/appcontainer/PathGrantorImpl.cpp
    infra/filesystem/FileSystemIsolatorImpl.cpp
    infra/wfp/WfpEngineImpl.cpp
    infra/etw/EtwMonitorImpl.cpp
    infra/etw/EventRecordParser.cpp
    infra/StartupCleanup.cpp
    infra/silo/SiloImpl.cpp
    infra/globalquota/GlobalQuotaManagerImpl.cpp
    adapters/ConfigLoader.cpp
    adapters/StartProcessPayloadParser.cpp
    adapters/SandboxInstance.cpp
    adapters/PathRuleEngine.cpp
    adapters/PermissionDetector.cpp
    core/entities/SandboxConfig.cpp
    core/entities/FileSystemConfig.cpp
    core/usecases/EnforcePolicyUseCase.cpp
    core/usecases/StartProcessUseCase.cpp
)

target_link_libraries(win_sandbox_native PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
    kernel32 advapi32 userenv shlwapi tdh psapi ws2_32 iphlpapi
)

target_include_directories(win_sandbox_native PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# pybind11 头文件触发 W4 警告较多，单独降至 W3
if(MSVC)
    target_compile_options(win_sandbox_native PRIVATE /W3)
endif()
```

> **注意**：Phase 10 暂不含 `main.cpp`（pybind11 模块无 main 入口）。`src/CMakeLists.txt` 保留现有 `add_executable(sandbox ...)` 不动。

#### 3.2.3 编译选项兼容

pybind11 头文件与现有 `/W4 /permissive- /Zc:__cplusplus /Zc:preprocessor` 可能有冲突：
- `/W4`：pybind11 触发大量自身警告 → `win_sandbox_native` 单独 `/W3`
- `/permissive-`：pybind11 2.11+ 已兼容，无需调整
- `/utf-8`：保留（pybind11 源文件为 UTF-8）

### 3.3 绑定骨架

`src/bindings/module.cpp`（Phase 10 唯一新增源文件）：

```cpp
// =============================================================================
// win_sandbox_native - pybind11 扩展入口
//
// Phase 10：空模块骨架，仅声明模块 docstring。
// Phase 11 起填充 SandboxInstance / Process / 配置 / 回调绑定。
//
// 形态：pybind11 扩展（.pyd），加载进 Python 解释器进程。
//   - C++ 核心代码与 sandbox.exe 共享（同一源文件树）
//   - in-process：HANDLE 值直接共享，无需 DuplicateHandle 跨进程
//   - Python 端用 ctypes 直接 ReadFile/WriteFile 句柄
// =============================================================================
#include <pybind11/pybind11.h>

namespace py = pybind11;

PYBIND11_MODULE(win_sandbox_native, m) {
    m.doc() = "win-sandbox native extension: in-process Job Object + AppContainer sandbox";
    // Phase 11: SandboxInstance / Process / 配置绑定
}
```

### 3.4 pyproject.toml 预备

`python/pyproject.toml` 新增 `.pyd` 打包条目（注释形式，Phase 15 启用）：

```toml
[tool.hatch.build.targets.wheel.force-include]
# Phase 15 启用：pybind11 扩展
# "../build/bin/win_sandbox_native.pyd" = "win_sandbox/_native.pyd"
# Phase 12 前：sandbox.exe 仍保留（IPC 形态过渡期）
"../build/bin/Release/sandbox.exe" = "win_sandbox/bin/sandbox.exe"
```

### 3.5 build_cmd.bat 更新

现有 `build_cmd.bat` 需确保 Python 开发头路径可用。pybind11 通过 `find_package(Python3 ... Development)` 自动定位，通常无需额外配置（Python 3.10+ 官方安装包含开发头）。若失败，显式指定：

```bat
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DPython3_ROOT_DIR="%PYTHON_HOME%"
```

---

## 4. 任务拆分

| 任务 | 描述 | 产出 | 验证 |
|------|------|------|------|
| T10.1 | 添加 pybind11 git submodule | `third_party/pybind11/`、`.gitmodules` 更新 | `git submodule status` 显示 pybind11 |
| T10.2 | 顶层 CMakeLists.txt 加 pybind11 子目录 | `CMakeLists.txt` 新增 find_package + add_subdirectory | `cmake -B build` 配置成功 |
| T10.3 | src/CMakeLists.txt 新增 win_sandbox_native 目标 | `src/CMakeLists.txt` 新增 pybind11_add_module | `cmake --build build` 产出 .pyd |
| T10.4 | 新增 src/bindings/module.cpp 骨架 | `src/bindings/module.cpp` | 编译通过 |
| T10.5 | pyproject.toml 预备 .pyd 条目 | `python/pyproject.toml` 注释条目 | 不破坏现有 wheel 构建 |
| T10.6 | 构建验证 | `build/bin/win_sandbox_native.pyd` + `sandbox.exe` 均产出 | import 成功 + e2e 全量通过 |

---

## 5. 验收标准

### 5.1 构建验收

```powershell
# 1. 拉取 submodule
git submodule update --init third_party/pybind11

# 2. 配置 + 构建
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build build

# 3. 产物检查
Test-Path build/bin/win_sandbox_native.pyd   # True
Test-Path build/bin/sandbox.exe              # True
```

### 5.2 import 验收

```powershell
python -c "import sys; sys.path.insert(0, r'build/bin'); import win_sandbox_native; print(win_sandbox_native.__doc__)"
# 输出: win-sandbox native extension: in-process Job Object + AppContainer sandbox
```

### 5.3 回归验收

现有 23 套件 e2e + ctest 14 项全量通过（sandbox.exe 未受影响）：

```powershell
python tests/e2e/run_all_regression.py   # 23/23 PASS
ctest --test-dir build -C Debug          # 14/14 PASS
```

---

## 6. 风险与处置

| # | 风险 | 处置 |
|---|---|---|
| 1 | pybind11 submodule 拉取失败（网络） | 使用镜像 `git -c url.https://v4.gh-proxy.org/.insteadOf=https://github.com/. submodule add ...` |
| 2 | `find_package(Python3 ... Development)` 找不到 Python 头 | 确认 Python 3.10+ 官方安装；或显式 `-DPython3_ROOT_DIR` |
| 3 | pybind11 与 MSVC /W4 冲突 | `win_sandbox_native` 单独 `/W3`（见 3.2.3） |
| 4 | pybind11 与现有 /permissive- 冲突 | pybind11 2.11+ 已兼容；若失败升级到最新 release |
| 5 | 同一源文件编译进 sandbox.exe 和 win_sandbox_native.pyd 两个目标 | CMake 允许同一 .cpp 编入多目标；若链接冲突（如 main.cpp 的 wmain），Phase 10 不含 main.cpp，无冲突 |

---

## 7. 测试策略

本 Phase 无新增测试。验证依赖：

1. **构建验证**：`cmake --build build` 成功，双产物产出
2. **import 验证**：Python 能 import 空模块
3. **回归验证**：现有 e2e + ctest 全量通过（证明未破坏现有形态）

---

## 8. 后续衔接

Phase 11 将在此骨架上填充业务绑定：
- 改造 `SandboxInstance`（去 `IEventEmitter` 依赖，改回调注入）
- 改造 `StartProcessUseCase`（拆分：保留隔离+Launch+Assign，删 StreamReader/wait/wall_clock 线程）
- 新增 `src/bindings/SandboxInstanceBinding.cpp` / `ProcessBinding.cpp` / `ConfigBinding.cpp` / `CallbacksBinding.cpp`
- 实现 GIL 管理与回调桥接
