# FastScreen

高性能 Windows 截屏库，C++ 核心 + Python 绑定，支持 DXGI / WGC / BitBlt 三级捕获策略。

![CI](https://github.com/ming-14/fastscreen/actions/workflows/ci.yml/badge.svg)

## 特性

- **三级捕获策略**：DXGI Desktop Duplication → Windows Graphics Capture → BitBlt，自动选择最优方法
- **DXGI Desktop Duplication**：GPU 直读桌面，全屏捕获最高性能
- **Windows Graphics Capture**：支持窗口捕获（含被遮挡窗口），persistent session 连续捕获高帧率
- **BitBlt**：通用回退方案，兼容性最佳
- **C++ 核心引擎**：零拷贝 GPU 纹理读取、WIC 硬件加速图像编码
- **Python API**：ctypes 绑定（fastscreencore），简洁易用
- **PySide6 GUI**：实时预览、连续捕获、FPS 显示
- **自动回退**：Auto 模式下捕获失败自动降级，显式模式不回退

## 系统要求

- Windows 10 1903+（WGC 需要）
- MSVC 2019+ / Visual Studio Build Tools
- CMake 3.16+

## 架构支持

| 架构 | VS 平台 | SIMD | 说明 |
|------|---------|------|------|
| **AMD64 (x64)** | `x64` | AVX2 | 默认架构，`build.py` 默认构建 |
| **x86 (32位)** | `Win32` | AVX2 | `--arch x86` |
| **ARM64** | `ARM64` | NEON | `--arch arm64`，构建/运行均原生支持 |

DLL 按架构安装到 `fastscreencore/<arch>/`，Python 绑定层按运行平台自动加载对应架构。

## 构建

```bash
python build.py               # 编译当前平台架构的 Release DLL
python build.py --arch x86    # 指定目标架构（x64 / x86 / arm64）
python build.py clean         # 清理构建产物
python -m pytest tests/ -v    # 运行测试
python gui/main.py            # 启动 PySide6 GUI
```

## CI 与 Release

GitHub Actions（`.github/workflows/ci.yml`）：
- **构建 + 测试**：push / PR 时并行构建 x64 / x86 / arm64
  - x64、x86 运行对应架构的 pytest（x86 使用 32 位 Python 加载 x86 DLL）
  - ARM64 仅交叉编译验证（GH Actions 无 ARM64 Windows runner，无法运行测试）
- **自动 Release**：推送 `v*` 标签时自动发布，附带三个架构的 DLL
  - `fastscreen-win-x86_64.dll` / `fastscreen-win-x86.dll` / `fastscreen-win-arm64.dll`

## 快速开始

```python
# 需将仓库根加入 sys.path（或安装 fastscreencore）
from fastscreencore import CaptureEngine, CaptureMethod

engine = CaptureEngine()

# 枚举显示器
monitors = engine.enumerate_monitors()
for m in monitors:
    print(m)

# 枚举窗口
windows = engine.enumerate_windows()
for w in windows[:5]:
    print(w)

# 截取主显示器（Auto 自动选择最优方法）
frame = engine.capture_monitor(0)
frame.save("screenshot.png")

# 指定方法
frame = engine.capture_monitor(0, method=CaptureMethod.DXGI)
frame = engine.capture_window(hwnd, method=CaptureMethod.WGC)

# 连续捕获
def on_frame(frame):
    print(f"{frame.width}x{frame.height}")

engine.start_continuous(0, 0, on_frame, fps=60, method=CaptureMethod.AUTO)
# ...
engine.stop_continuous()
```

## 捕获方法

| 方法 | 目标 | 性能 | 说明 |
|------|------|------|------|
| DXGI | 显示器 | ★★★★★ | GPU 直读，最高性能，不支持窗口 |
| WGC | 窗口/显示器 | ★★★★ | 支持被遮挡窗口，persistent session 高帧率 |
| BitBlt | 窗口/显示器 | ★★★ | 通用回退，最小化窗口无法捕获 |
| Auto | 自动选择 | - | 显示器→DXGI，窗口→WGC，失败→BitBlt |

## GUI

```bash
python gui/main.py
```

- F5：单帧截图
- Ctrl+S：保存
- Esc：停止连续捕获

## 项目结构

```
fastscreen/
├── CMakeLists.txt        # CMake 构建配置
├── build.py              # 构建脚本（编译 DLL）
├── fastscreen.def        # DLL 导出定义
├── fastscreencore/       # Python 绑定包（绑定层源码，编译后含 fastscreen.dll）
│   ├── __init__.py
│   ├── _core.py          # ctypes 绑定层（C ABI 声明、按架构加载 DLL）
│   ├── capture.py        # 高级 API（CaptureEngine、CapturedFrame）
│   └── <arch>/           # 构建产物 fastscreen.dll（x64 / x86 / arm64 子目录）
├── gui/                  # PySide6 GUI 测试工具
│   ├── main.py
│   ├── main_window.py
│   └── requirements.txt
├── src/core/             # C++ 核心引擎
│   ├── api.cpp           # C API 导出层
│   ├── capture_session.* # 捕获会话管理
│   ├── dxgi_capture.*    # DXGI Desktop Duplication
│   ├── wgc_capture.*     # Windows Graphics Capture
│   ├── bitblt_capture.*  # BitBlt 回退
│   ├── image_encoder.*   # WIC 图像编码
│   ├── enum_helper.*     # 显示器/窗口枚举
│   ├── frame_pool.cpp    # 帧缓冲池（连续捕获内存复用）
│   ├── frame_buffer.h    # 帧缓冲/环形队列结构
│   └── common.h          # 公共类型定义
└── tests/                # pytest 测试
```

Python 绑定层（`fastscreencore`：`_core.py` + `capture.py` + 各架构的 `fastscreen.dll`）位于仓库根 `fastscreencore/`，DLL 由 `build.py` 编译后按架构安装到 `fastscreencore/<arch>/`。

## 测试

```bash
python -m pytest tests/ -v
```

## API 参考

### CaptureEngine

| 方法 | 说明 |
|------|------|
| `enumerate_monitors()` | 枚举所有显示器 |
| `enumerate_windows()` | 枚举所有窗口 |
| `capture_monitor(id, method)` | 截取指定显示器 |
| `capture_window(hwnd, method)` | 截取指定窗口 |
| `start_continuous(type, id, callback, fps, method)` | 开始连续捕获 |
| `stop_continuous()` | 停止连续捕获 |
| `is_running()` | 是否正在连续捕获 |

### CapturedFrame

| 属性/方法 | 说明 |
|-----------|------|
| `width`, `height` | 帧尺寸 |
| `stride`, `bpp` | 步长和每像素字节数 |
| `timestamp_ms` | 时间戳 |
| `to_bytes()` | 转为 bytes |
| `to_bytes_gil_safe()` | 转为 bytes（C 端 memcpy，GIL-safe） |
| `to_bytearray()` | 转为 bytearray |
| `to_numpy()` | 转为 numpy 数组（需 numpy） |
| `to_qimage()` | 转为 QImage（需 PySide6） |
| `to_image_bytes(format, quality, width, height)` | 编码为 PNG/JPEG/BMP 字节（可缩放） |
| `save(path, format)` | 保存为 PNG/JPEG/BMP |
| `release()` | 释放资源 |