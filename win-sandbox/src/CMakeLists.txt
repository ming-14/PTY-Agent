# =============================================================================
# src/ - 沙箱核心（pybind11 in-process 库形态）
#
# win_sandbox_native.pyd 是唯一目标
# =============================================================================

# -----------------------------------------------------------------------------
# 核心源文件（win_sandbox_native.pyd）
# -----------------------------------------------------------------------------
set(WIN_SANDBOX_CORE_SOURCES
    # infra 层
    infra/logging/Logger.cpp
    infra/job/JobObjectImpl.cpp
    infra/process/ProcessLauncherImpl.cpp
    infra/token/TokenIsolatorImpl.cpp        # Low IL 隔离 token 派生
    infra/writearea/WriteAreaImpl.cpp        # Low IL 可写区
    infra/wfp/WfpEngineImpl.cpp                 # WFP 网络白名单
    infra/etw/EtwMonitorImpl.cpp               # ETW 行为监控
    infra/etw/EventRecordParser.cpp            # ETW 事件解析
    infra/StartupCleanup.cpp                   # 残留清理
    infra/silo/SiloImpl.cpp                    # 候选：Server Silo 更强隔离
    infra/globalquota/GlobalQuotaManagerImpl.cpp # 候选：多沙箱全局资源配额
    # adapters 层
    adapters/ConfigLoader.cpp
    adapters/StartProcessPayloadParser.cpp  # dict→StartProcessRequest 解析
    adapters/PermissionDetector.cpp          # 权限检测
    adapters/NativeSandboxInstance.cpp          # pybind11 专用多进程管理器
    # core 层（实体实现）
    core/entities/SandboxConfig.cpp
    # core 层（用例实现）
    core/usecases/NativeSandboxedProcess.cpp   # 去 IPC 的进程用例
    # bindings 层
    bindings/module.cpp
    bindings/SandboxInstanceBinding.cpp         # PySandboxInstance 包装
    bindings/ProcessBinding.cpp                 # PyProcess 包装 + 回调桥接
    bindings/CallbacksBinding.cpp               # contains_access_denied_keyword 工具函数
)

# 公共链接库（第三方 + Windows 系统库）
set(WIN_SANDBOX_COMMON_LIBS
    wil
    nlohmann_json
    spdlog::spdlog
    kernel32
    advapi32
    userenv
    shlwapi
    shell32
    tdh
    psapi
    ws2_32
    iphlpapi
)

# =============================================================================
# win_sandbox_native.pyd（pybind11 in-process 库，唯一 C++ 目标）
#
# 形态：pybind11 扩展，加载进 Python 解释器进程
#   - in-process：HANDLE 值直接共享，无需 DuplicateHandle 跨进程
#   - Python 端用 ctypes 直接 ReadFile/WriteFile 句柄
# =============================================================================
pybind11_add_module(win_sandbox_native
    ${WIN_SANDBOX_CORE_SOURCES}
)

target_link_libraries(win_sandbox_native PRIVATE ${WIN_SANDBOX_COMMON_LIBS})

target_include_directories(win_sandbox_native PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
)

# pybind11 头文件触发 W4 警告较多，单独降至 W3（其余选项继承顶层 /utf-8 /permissive- 等）
if(MSVC)
    target_compile_options(win_sandbox_native PRIVATE /W3)
endif()
