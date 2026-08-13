# =============================================================================
# tests/ - 测试目标
# ProcessLauncherImpl 运行时验证
# =============================================================================

# ProcessLauncherImpl 验证程序
# 简单 assert + 控制台输出
add_executable(verify_t14
    unit/verify_t14.cpp
)

target_link_libraries(verify_t14 PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
)

# 复用 src/ 的实现源文件（避免重复编译为静态库的复杂度）
target_sources(verify_t14 PRIVATE
    ${CMAKE_SOURCE_DIR}/src/infra/logging/Logger.cpp
    ${CMAKE_SOURCE_DIR}/src/infra/process/ProcessLauncherImpl.cpp
)

target_include_directories(verify_t14 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(verify_t14 PRIVATE
    kernel32
    advapi32
)

# ConfigLoader 验证程序
# 覆盖：合法配置解析 / 缺失字段默认值 / schema 校验失败 / 范围越界 / 环境变量展开 / 文件加载
add_executable(verify_t17
    unit/verify_t17.cpp
)

target_link_libraries(verify_t17 PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
)

target_sources(verify_t17 PRIVATE
    ${CMAKE_SOURCE_DIR}/src/infra/logging/Logger.cpp
    ${CMAKE_SOURCE_DIR}/src/core/entities/SandboxConfig.cpp
    ${CMAKE_SOURCE_DIR}/src/adapters/ConfigLoader.cpp
)

target_include_directories(verify_t17 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(verify_t17 PRIVATE
    kernel32
    advapi32
)

# isolation_policy 解析验证程序
add_executable(verify_t27
    unit/verify_t27.cpp
)

target_link_libraries(verify_t27 PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
)

target_sources(verify_t27 PRIVATE
    ${CMAKE_SOURCE_DIR}/src/infra/logging/Logger.cpp
    ${CMAKE_SOURCE_DIR}/src/core/entities/SandboxConfig.cpp
    ${CMAKE_SOURCE_DIR}/src/adapters/ConfigLoader.cpp
    ${CMAKE_SOURCE_DIR}/src/adapters/StartProcessPayloadParser.cpp
)

target_include_directories(verify_t27 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(verify_t27 PRIVATE
    kernel32
    advapi32
)

# core 层纯头文件编译验证程序
# 覆盖：core 实体与端口头文件必须无 Win32 依赖（故意不包含 windows.h）
# 仅依赖 core 层头文件，编译通过即通过，无需链接库
add_executable(verify_t11
    unit/verify_t11.cpp
)

target_include_directories(verify_t11 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
    ${CMAKE_SOURCE_DIR}/third_party/nlohmann_json/include
)

# TokenIsolatorImpl + WriteAreaImpl 行为验证（probe）
add_executable(probe_t16
    unit/probe_t16.cpp
)

target_link_libraries(probe_t16 PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
)

target_sources(probe_t16 PRIVATE
    ${CMAKE_SOURCE_DIR}/src/infra/logging/Logger.cpp
    ${CMAKE_SOURCE_DIR}/src/infra/token/TokenIsolatorImpl.cpp
    ${CMAKE_SOURCE_DIR}/src/infra/writearea/WriteAreaImpl.cpp
)

target_include_directories(probe_t16 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(probe_t16 PRIVATE
    kernel32
    advapi32
)

add_test(NAME probe_t16 COMMAND probe_t16)
set_tests_properties(probe_t16 PROPERTIES TIMEOUT 60)

# =============================================================================
# CTest 注册：verify_t*.exe 以退出码 0/非0 判定通过/失败
# （这些程序自带 assert + 控制台输出）
# 注：顶层 CMakeLists 已 enable_testing()，此处直接注册
# =============================================================================

# 各验证程序预期运行时长（秒）。CPU/IO 类测试较慢。
add_test(NAME verify_t11 COMMAND verify_t11)
set_tests_properties(verify_t11 PROPERTIES TIMEOUT 30)

add_test(NAME verify_t14 COMMAND verify_t14)
set_tests_properties(verify_t14 PROPERTIES TIMEOUT 60)

add_test(NAME verify_t17 COMMAND verify_t17)
set_tests_properties(verify_t17 PROPERTIES TIMEOUT 30)

add_test(NAME verify_t27 COMMAND verify_t27)
set_tests_properties(verify_t27 PROPERTIES TIMEOUT 30)

# Job 功能增强验证程序
# 覆盖：QueryProcessList / QueryProcessExitCode / QueryProcessPath / SetCrashSilent /
#       crash_silent 崩溃静默 / 正常与指定退出码
# 依赖 crash_dummy.exe 同目录（tests 输出目录都相同，ctest 以 build/tests 为工作目录）
add_executable(verify_t28
    unit/verify_t28.cpp
)

target_link_libraries(verify_t28 PRIVATE
    wil
    nlohmann_json
    spdlog::spdlog
)

target_sources(verify_t28 PRIVATE
    ${CMAKE_SOURCE_DIR}/src/infra/logging/Logger.cpp
    ${CMAKE_SOURCE_DIR}/src/infra/job/JobObjectImpl.cpp
    ${CMAKE_SOURCE_DIR}/src/infra/process/ProcessLauncherImpl.cpp
)

target_include_directories(verify_t28 PRIVATE
    ${CMAKE_SOURCE_DIR}/src
)

target_link_libraries(verify_t28 PRIVATE
    kernel32
    advapi32
)

# 崩溃测试助手：空指针解引用制造 ACCESS_VIOLATION
add_executable(crash_dummy
    unit/crash_dummy.cpp
)
target_link_libraries(crash_dummy PRIVATE
    wil
)

add_test(NAME verify_t28 COMMAND verify_t28)
set_tests_properties(verify_t28 PROPERTIES TIMEOUT 120)
