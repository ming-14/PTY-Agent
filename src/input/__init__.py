"""输入拦截子包 — SGR 鼠标拦截、键盘 VT 拦截与鼠标动作执行

注意：本 __init__.py 刻意不做任何子模块导入。原因：client 侧（src/client/__init__.py）
只需 input.text 的纯文本工具，而 interceptor/mouse 属于 daemon 会话侧（依赖
terminal.screen → pywezterm 后端）。包级导入会把 daemon 侧依赖链带进 CLI 进程，
导致仅启动 CLI 也加载 pywezterm。使用方一律从子模块按需导入：
  - InputInterceptor       → from src.input.interceptor import InputInterceptor
  - Coord/MouseActionEncoder/MouseError/grep_screen → from src.input.mouse import ...
  - process_input 等纯文本  → from src.input.text import ...
"""
