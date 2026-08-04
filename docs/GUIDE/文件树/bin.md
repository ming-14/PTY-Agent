# bin/ 辅助工具

```
bin/
├── aichat/                  # AI 聊天工具
│   ├── common.py
│   ├── _finderror.py
│   ├── talk.py
│   ├── config_manager.py
│   └── config/
│       └── config.yaml.example  # 配置模板（config.yaml 本身不存在，仅示例）
│                            # 注：该目录内另含一个非项目文件（误放入），不列入结构

├── cursorlocator/           # 光标定位器（Win32 API + 渲染）
│   ├── __init__.py
│   ├── ring_worker.py
│   ├── win32_api.py
│   ├── pixel_color.py
│   ├── rendering.py
│   └── config.py

└── fastscreencore/          # 快速屏幕捕获核心
    ├── __init__.py
    ├── capture.py           # Python 封装
    ├── _core.py             # 核心接口
    └── fastscreen.dll       # C++ 屏幕捕获 DLL（DXGI/WGC/BitBlt）
```

> 说明：原文档中的 `bin/ultravnc/` 整个目录不存在——UltraVNC 仅以 Python 代码形式存在于 `src/vnc/`，仓库未附带 winvnc.exe 等二进制。
