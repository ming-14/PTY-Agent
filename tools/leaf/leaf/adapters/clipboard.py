"""系统剪贴板适配器：pywezterm 绑定层提供读写（Rust winapi），此处仅薄映射。

原 ctypes 手写 Win32 剪贴板已下沉到 pywezterm.clipboard_read/write
（绑定层与 ConsoleInput 同级，与宿主终端共用一份实现）。
"""

from leaf.drivers import _engine


class Clipboard:
    """ClipboardPort 实现：系统剪贴板读/写（pywezterm 绑定层）。"""

    def __init__(self):
        _engine.ensure_engine()
        import pywezterm

        self._pywezterm = pywezterm

    def read(self) -> str:
        return self._pywezterm.clipboard_read()

    def write(self, text: str) -> None:
        self._pywezterm.clipboard_write(text)
