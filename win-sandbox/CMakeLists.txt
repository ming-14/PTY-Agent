# =============================================================================
# win-sandbox 顶层 CMake 配置
# 项目骨架 + 第三方库集成 + 编译链路验证
# =============================================================================
cmake_minimum_required(VERSION 3.20)

# ===== 依赖扫描策略（CMP0155）=====
# 启用 CMake 3.28+ 的独立依赖扫描（CXX_SCAN + depfile/dyndep）。
# 背景（2026-08-12）：本项目 C++20，默认 deps=msvc 模式依赖编译时
# /showIncludes 输出解析，偶发解析失败会导致头文件变更漏编（旧 .obj 与
# 新代码链接，ABI 不匹配 → 运行时崩溃，实测 verify_t27 SegFault）。
# 扫描器生成的 depfile 不依赖编译输出，可根治静默漏编。
if(POLICY CMP0155)
    cmake_policy(SET CMP0155 NEW)
endif()

project(win_sandbox
    VERSION 0.2.0
    DESCRIPTION "Windows Sandbox (Job Object + Low IL token) callable from Python"
    LANGUAGES CXX
)

# ===== 全局 C++ 标准 =====
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# ===== 输出目录（统一到 build/bin、build/lib）=====
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

# 默认构建类型
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Debug CACHE STRING "Build type" FORCE)
endif()

# ===== MSVC 编译选项 =====
# /utf-8           源码与执行字符集均为 UTF-8
# /W4              高警告级别
# /permissive-     严格标准符合
# /Zc:__cplusplus  正确报告 __cplusplus 宏
# /Zc:preprocessor 标准预处理器
if(MSVC)
    add_compile_options(/utf-8 /W4 /permissive- /Zc:__cplusplus /Zc:preprocessor)
    add_compile_definitions(
        _CRT_SECURE_NO_WARNINGS
        NOMINMAX
        WIN32_LEAN_AND_MEAN
        UNICODE
        _UNICODE
    )
endif()

# ===== 第三方库 =====

# --- WIL (Windows Implementation Libraries, header-only) ---
add_library(wil INTERFACE)
target_include_directories(wil INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/wil/include
)

# --- nlohmann/json (header-only) ---
add_library(nlohmann_json INTERFACE)
target_include_directories(nlohmann_json INTERFACE
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party/nlohmann_json/include
)

# --- spdlog (静态库) ---
# 关闭 spdlog 自带示例/测试/bench，使用内置 fmt
set(SPDLOG_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_EXAMPLE OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_BENCH OFF CACHE BOOL "" FORCE)
set(SPDLOG_BUILD_TESTS_HO OFF CACHE BOOL "" FORCE)
set(SPDLOG_ENABLE_PCH OFF CACHE BOOL "" FORCE)
add_subdirectory(third_party/spdlog)

# --- pybind11 (Python 绑定) ---
# in-process 库形态：win_sandbox_native.pyd 加载进 Python 解释器进程
# pybind11 3.0+ 走 CMake FindPython 路径（PYBIND11_FINDPYTHON=ON）
find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)
set(PYBIND11_FINDPYTHON ON CACHE BOOL "" FORCE)
add_subdirectory(third_party/pybind11)

# ===== 项目目标 =====
add_subdirectory(src)

# ===== 测试 =====
enable_testing()
add_subdirectory(tests)
