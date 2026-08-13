# FastScreen

高性能 Windows 截屏库，C++ 核心 + Python 绑定，支持 DXGI / WGC / BitBlt 三级捕获策略。

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

## 构建

Python 绑定层（`fastscreencore` 包，含编译后的 DLL）位于项目根 `bin/fastscreencore/`，由构建脚本生成：

```bash
python build.py build   # 仅编译 C++ DLL 并复制到 bin/fastscreencore/
python build.py gui     # 启动 PySide6 GUI
python build.py test    # 运行 pytest 测试
python build.py all     # 构建 DLL + 启动 GUI

# 或走 PTY-Agent 整体发布构建
# 项目根目录执行 .\BUILD.ps1
```

## 快速开始

```python
# 需将 PTY-Agent/bin 加入 sys.path（或安装 fastscreencore）
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
python build.py gui
```

- F5：单帧截图
- Ctrl+S：保存
- Esc：停止连续捕获

## 项目结构

```
fastscreen/
├── CMakeLists.txt        # CMake 构建配置
├── build.py              # 构建脚本（build/gui/test/all）
├── fastscreen.def        # DLL 导出定义
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
└── tests/                # pytest 测试（依赖 bin/fastscreencore）
```

Python 绑定层（`fastscreencore`：`_core.py` + `capture.py` + `fastscreen.dll`）为构建产物，位于 `bin/fastscreencore/`；上游流服务层在 `src/fastscreen/`（见父项目文档）。

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