# fastscreen/ C++ 屏幕捕获

> C++ 屏幕捕获 DLL 源码 + Python 封装 + GUI 测试。Python 流服务层在 `src/fastscreen/`（见 [src.md](../../docs/filestree/src.md)）。

```
fastscreen/                  # C++ 屏幕捕获 DLL 源码 + Python 封装 + GUI 测试
├── CMakeLists.txt           # CMake 构建配置
├── build.py                 # 构建脚本
├── fastscreen.def           # DLL 导出定义
├── pyproject.toml           # Python 包配置
├── README.md                # 项目说明
├── .gitignore               # Git 忽略规则
├── docs/                    # 文档
│   └── FILESTREE.md         # 本文档（目录结构说明）
├── gui/                     # ═══════ Python GUI 测试工具 ═══════
│   ├── main.py              # GUI 入口
│   ├── main_window.py       # 主窗口实现
│   └── requirements.txt     # GUI 依赖
├── src/                     # ═══════ C++ 源码（DXGI/WGC/BitBlt 捕获 + 编码） ═══════
│   └── core/                # 核心捕获与编码
│       ├── api.cpp                    # DLL 导出 API 实现
│       ├── bitblt_capture.cpp / .h    # BitBlt GDI 捕获
│       ├── capture_session.cpp / .h   # 捕获会话管理
│       ├── common.h                   # 公共定义
│       ├── dxgi_capture.cpp / .h      # DXGI 桌面捕获
│       ├── enum_helper.cpp / .h       # 显示器枚举辅助
│       ├── frame_buffer.h             # 帧缓冲区
│       ├── frame_pool.cpp             # 帧池
│       ├── image_encoder.cpp / .h     # 图像编码（PNG/JPG/BMP）
│       └── wgc_capture.cpp / .h       # WGC（Windows.Graphics.Capture）捕获
└── tests/                   # ═══════ Python 测试 ═══════
    ├── conftest.py          # pytest 全局配置
    ├── perf_verify.py       # 性能验证脚本
    ├── test_benchmark.py    # 基准测试
    ├── test_capture.py      # 捕获测试
    ├── test_capture_types.py # 捕获类型测试
    ├── test_core.py         # 核心接口测试
    └── test_workflow.py     # 工作流测试
```
