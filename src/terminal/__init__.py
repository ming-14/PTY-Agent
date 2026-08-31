"""终端模拟子包 — 终端模型与渲染后端

注意：本 __init__.py 刻意不做任何子模块导入。原因：screen 依赖 backends（pywezterm
引擎），而 pywezterm 属于 daemon 会话侧依赖。若包级导入，仅启动 CLI 也可能把
wezterm 后端带进进程。使用方一律从子模块按需导入：
  - TerminalScreen → from src.terminal.screen import TerminalScreen
  - 后端/引擎      → from src.terminal.backends import ...
"""
