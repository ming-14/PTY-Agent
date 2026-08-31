# sandbox/src — in-process sandbox core (pybind11 .pyd, the only C++ target).
#
# Requires pybind11 (pip install pybind11 provides the CMake config) and a
# Visual Studio toolchain. Produces win_sandbox_native.pyd loaded directly
# into the Python interpreter process — handles (HPCON included) are shared
# in-process, no IPC.
cmake_minimum_required(VERSION 3.20)
project(win_sandbox_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# pybind11 lives in sandbox/third_party/pybind11.
add_subdirectory(
  ${CMAKE_CURRENT_SOURCE_DIR}/../third_party/pybind11
  ${CMAKE_BINARY_DIR}/pybind11
  EXCLUDE_FROM_ALL
)

set(CORE_SOURCES
  token.cpp
  acl.cpp
  sid.cpp
  job.cpp
  spawn.cpp
  instance.cpp
  module.cpp
)

pybind11_add_module(win_sandbox_native ${CORE_SOURCES})
target_link_libraries(win_sandbox_native PRIVATE advapi32 bcrypt shell32)
target_include_directories(win_sandbox_native PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

if(MSVC)
  target_compile_options(win_sandbox_native PRIVATE /W3 /utf-8 /permissive-)
endif()
