cmake_minimum_required(VERSION 3.16)
project(fastscreen VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(DLL_SOURCES
    src/core/api.cpp
    src/core/capture_session.cpp
    src/core/dxgi_capture.cpp
    src/core/wgc_capture.cpp
    src/core/bitblt_capture.cpp
    src/core/image_encoder.cpp
    src/core/enum_helper.cpp
    src/core/frame_pool.cpp
)

add_library(fastscreen SHARED ${DLL_SOURCES})

target_compile_definitions(fastscreen PRIVATE
    FASTSCREEN_EXPORTS
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)

target_include_directories(fastscreen PRIVATE src/core)

if(DEFINED ENV{WindowsSdkDir})
    file(GLOB CPPWINRT_DIRS "$ENV{WindowsSdkDir}Include/*/cppwinrt")
    if(CPPWINRT_DIRS)
        list(SORT CPPWINRT_DIRS ORDER DESCENDING)
        list(GET CPPWINRT_DIRS 0 CPPWINRT_DIR)
        message(STATUS "Using C++/WinRT from: ${CPPWINRT_DIR}")
        target_include_directories(fastscreen PRIVATE ${CPPWINRT_DIR})
    endif()
endif()

target_link_libraries(fastscreen PRIVATE
    dxgi
    d3d11
    user32
    gdi32
    ole32
    oleaut32
    windowscodecs
    shlwapi
    dwmapi
    RuntimeObject
)

set_target_properties(fastscreen PROPERTIES
    OUTPUT_NAME "fastscreen"
    PREFIX ""
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)

if(MSVC)
    # SIMD 优化选项按架构区分：
    #   x86/x64  — /arch:AVX2（RGBA→BGRA 通道重排向量化）
    #   ARM64    — NEON 始终可用，使用默认 armv8 基线，不指定 /arch 指令集
    # /await:strict 用于 C++/WinRT 协程支持（MSVC 16.11+），替换已弃用的 /await。
    if(MSVC_ARCHITECTURE_ID MATCHES "ARM64")
        target_compile_options(fastscreen PRIVATE /O2 /GL /Oi /GS- /fp:fast /utf-8 /await:strict)
    else()
        target_compile_options(fastscreen PRIVATE /O2 /GL /Oi /GS- /arch:AVX2 /fp:fast /utf-8 /await:strict)
    endif()
    target_link_options(fastscreen PRIVATE /LTCG /DEF:${CMAKE_CURRENT_SOURCE_DIR}/fastscreen.def)
endif()
