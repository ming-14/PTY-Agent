# fastscreen/ C++ 屏幕捕获

> C++ 屏幕捕获 DLL 源码 + Python 封装 + GUI 测试。Python 流服务层在 `src/fastscreen/`（见 [src.md](src.md)）。

```
fastscreen/                  # C++ 屏幕捕获 DLL 源码 + Python 封装 + GUI 测试
├── CMakeLists.txt           # CMake 构建
├── build.py                 # 构建脚本
├── fastscreen.def           # DLL 导出定义
├── pyproject.toml           # Python 包配置
├── README.md                # 说明
├── .gitattributes
├── .gitignore
├── gui/                     # Python GUI 测试工具
│   ├── main.py
│   ├── main_window.py
│   └── requirements.txt
├── src/                     # C++ 源码（DXGI/WGC/BitBlt 捕获 + 编码）
│   └── core/
│       ├── api.cpp
│       ├── bitblt_capture.cpp / .h
│       ├── capture_session.cpp / .h
│       ├── common.h
│       ├── dxgi_capture.cpp / .h
│       ├── enum_helper.cpp / .h
│       ├── frame_buffer.h
│       ├── frame_pool.cpp
│       ├── image_encoder.cpp / .h
│       └── wgc_capture.cpp / .h
└── tests/                   # C++ 测试
    ├── conftest.py
    ├── perf_verify.py
    ├── test_benchmark.py
    ├── test_capture.py
    ├── test_capture_types.py
    ├── test_core.py
    └── test_workflow.py
```
