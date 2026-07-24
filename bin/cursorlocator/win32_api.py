"""
@file win32_api.py
@brief Win32 常量 / 结构体 / API 声明（纯声明，无执行逻辑）
"""

import ctypes
import ctypes.wintypes as w
from ctypes import windll, byref, Structure, POINTER as CPOINTER, c_int, c_uint, c_void_p, c_ubyte

# ============================================================
# 窗口样式与消息 ID
# ============================================================
WS_EX_LAYERED      = 0x00080000
WS_EX_TRANSPARENT  = 0x00000020
WS_EX_TOPMOST      = 0x00000008
WS_EX_TOOLWINDOW   = 0x00000080
WS_POPUP           = 0x80000000

SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

WM_DESTROY      = 0x0002
WM_PAINT        = 0x000F
WM_TIMER        = 0x0113
WM_CREATE       = 0x0001
WM_ERASEBKGND   = 0x0014

WDA_EXCLUDEFROMCAPTURE = 0x11
WDA_NONE               = 0x00

ULW_ALPHA = 0x00000002
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
NULL = 0

ENUM_CURRENT_SETTINGS = 0xFFFFFFFF

# ============================================================
# 结构体
# ============================================================
class POINT(Structure):
    _fields_ = [('x', w.LONG), ('y', w.LONG)]

class SIZE(Structure):
    _fields_ = [('cx', w.LONG), ('cy', w.LONG)]

class BLENDFUNCTION(Structure):
    _fields_ = [
        ('BlendOp',             c_ubyte),
        ('BlendFlags',          c_ubyte),
        ('SourceConstantAlpha', c_ubyte),
        ('AlphaFormat',         c_ubyte),
    ]

class WNDCLASSEXW(Structure):
    _fields_ = [
        ('cbSize',        w.UINT),
        ('style',         w.UINT),
        ('lpfnWndProc',   c_void_p),
        ('cbClsExtra',    c_int),
        ('cbWndExtra',    c_int),
        ('hInstance',     c_void_p),
        ('hIcon',         c_void_p),
        ('hCursor',       c_void_p),
        ('hbrBackground', c_void_p),
        ('lpszMenuName',  w.LPCWSTR),
        ('lpszClassName', w.LPCWSTR),
        ('hIconSm',       c_void_p),
    ]

class BITMAPV5HEADER(Structure):
    _fields_ = [
        ('bV5Size',          w.DWORD),
        ('bV5Width',         w.LONG),
        ('bV5Height',        w.LONG),
        ('bV5Planes',        w.WORD),
        ('bV5BitCount',      w.WORD),
        ('bV5Compression',   w.DWORD),
        ('bV5SizeImage',     w.DWORD),
        ('bV5XPelsPerMeter', w.LONG),
        ('bV5YPelsPerMeter', w.LONG),
        ('bV5ClrUsed',       w.DWORD),
        ('bV5ClrImportant',  w.DWORD),
        ('bV5RedMask',       w.DWORD),
        ('bV5GreenMask',     w.DWORD),
        ('bV5BlueMask',      w.DWORD),
        ('bV5AlphaMask',     w.DWORD),
        ('bV5CSType',        w.DWORD),
        ('bV5Endpoints',     c_ubyte * 36),
        ('bV5GammaRed',      w.DWORD),
        ('bV5GammaGreen',    w.DWORD),
        ('bV5GammaBlue',     w.DWORD),
        ('bV5Intent',        w.DWORD),
        ('bV5ProfileData',   w.DWORD),
        ('bV5ProfileSize',   w.DWORD),
        ('bV5Reserved',      w.DWORD),
    ]

class PAINTSTRUCT(Structure):
    _fields_ = [
        ('hdc',        c_void_p),
        ('fErase',     w.BOOL),
        ('rcPaint',    c_ubyte * 16),
        ('fRestore',   w.BOOL),
        ('fIncUpdate', w.BOOL),
        ('rgbReserved', c_ubyte * 32),
    ]

class MSG(Structure):
    _fields_ = [
        ('hwnd',    c_void_p),
        ('message', w.UINT),
        ('wParam',  w.WPARAM),
        ('lParam',  w.LPARAM),
        ('time',    w.DWORD),
        ('pt',      POINT),
    ]

class DEVMODEW(Structure):
    _fields_ = [
        ('dmDeviceName',          w.WCHAR * 32),
        ('dmSpecVersion',         w.WORD),
        ('dmDriverVersion',       w.WORD),
        ('dmSize',                w.WORD),
        ('dmDriverExtra',         w.WORD),
        ('dmFields',              w.DWORD),
        ('dmPosition',            POINT),
        ('dmDisplayOrientation',  w.DWORD),
        ('dmDisplayFixedOutput',  w.DWORD),
        ('dmColor',               w.SHORT),
        ('dmDuplex',              w.SHORT),
        ('dmYResolution',         w.SHORT),
        ('dmTTOption',            w.SHORT),
        ('dmCollate',             w.SHORT),
        ('dmFormName',            w.WCHAR * 32),
        ('dmLogPixels',           w.WORD),
        ('dmBitsPerPel',          w.DWORD),
        ('dmPelsWidth',           w.DWORD),
        ('dmPelsHeight',          w.DWORD),
        ('dmDisplayFlags',        w.DWORD),
        ('dmDisplayFrequency',    w.DWORD),
    ]

# ============================================================
# Win32 API 加载
# ============================================================
user32   = windll.user32
gdi32    = windll.gdi32
kernel32 = windll.kernel32

_SetWindowDisplayAffinity = user32.SetWindowDisplayAffinity
_SetWindowDisplayAffinity.argtypes = [c_void_p, c_uint]
_SetWindowDisplayAffinity.restype = w.BOOL

_SetTimer = user32.SetTimer
_SetTimer.argtypes = [c_void_p, c_void_p, w.UINT, c_void_p]
_SetTimer.restype = c_void_p

_KillTimer = user32.KillTimer
_KillTimer.argtypes = [c_void_p, c_void_p]
_KillTimer.restype = w.BOOL

_GetCursorPos = user32.GetCursorPos
_GetCursorPos.argtypes = [CPOINTER(POINT)]
_GetCursorPos.restype = w.BOOL

_SetWindowPos = user32.SetWindowPos
_SetWindowPos.argtypes = [c_void_p, c_void_p, c_int, c_int, c_int, c_int, w.UINT]
_SetWindowPos.restype = w.BOOL

_CreateWindowExW = user32.CreateWindowExW
_CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                             c_int, c_int, c_int, c_int,
                             c_void_p, c_void_p, c_void_p, c_void_p]
_CreateWindowExW.restype = c_void_p

_DestroyWindow = user32.DestroyWindow
_DestroyWindow.argtypes = [c_void_p]
_DestroyWindow.restype = w.BOOL

_PostQuitMessage = user32.PostQuitMessage
_PostQuitMessage.argtypes = [c_int]
_PostQuitMessage.restype = None

_DefWindowProcW = user32.DefWindowProcW
_DefWindowProcW.argtypes = [c_void_p, w.UINT, w.WPARAM, w.LPARAM]
_DefWindowProcW.restype = c_void_p

_RegisterClassExW = user32.RegisterClassExW
_RegisterClassExW.argtypes = [CPOINTER(WNDCLASSEXW)]
_RegisterClassExW.restype = w.ATOM

_GetMessageW = user32.GetMessageW
_GetMessageW.argtypes = [CPOINTER(MSG), c_void_p, w.UINT, w.UINT]
_GetMessageW.restype = c_int

_TranslateMessage = user32.TranslateMessage
_TranslateMessage.argtypes = [CPOINTER(MSG)]
_TranslateMessage.restype = w.BOOL

_DispatchMessageW = user32.DispatchMessageW
_DispatchMessageW.argtypes = [CPOINTER(MSG)]
_DispatchMessageW.restype = c_void_p

_BeginPaint = user32.BeginPaint
_BeginPaint.argtypes = [c_void_p, CPOINTER(PAINTSTRUCT)]
_BeginPaint.restype = c_void_p

_EndPaint = user32.EndPaint
_EndPaint.argtypes = [c_void_p, CPOINTER(PAINTSTRUCT)]
_EndPaint.restype = w.BOOL

_UpdateLayeredWindow = user32.UpdateLayeredWindow
_UpdateLayeredWindow.argtypes = [
    c_void_p, c_void_p, CPOINTER(POINT), CPOINTER(SIZE),
    c_void_p, CPOINTER(POINT), w.DWORD, CPOINTER(BLENDFUNCTION), w.DWORD,
]
_UpdateLayeredWindow.restype = w.BOOL

_CreateCompatibleDC = gdi32.CreateCompatibleDC
_CreateCompatibleDC.argtypes = [c_void_p]
_CreateCompatibleDC.restype = c_void_p

_DeleteDC = gdi32.DeleteDC
_DeleteDC.argtypes = [c_void_p]
_DeleteDC.restype = w.BOOL

_SelectObject = gdi32.SelectObject
_SelectObject.argtypes = [c_void_p, c_void_p]
_SelectObject.restype = c_void_p

_DeleteObject = gdi32.DeleteObject
_DeleteObject.argtypes = [c_void_p]
_DeleteObject.restype = w.BOOL

_CreateDIBSection = gdi32.CreateDIBSection
_CreateDIBSection.argtypes = [c_void_p, c_void_p, w.UINT, CPOINTER(c_void_p), c_void_p, w.DWORD]
_CreateDIBSection.restype = c_void_p

_GetModuleHandleW = kernel32.GetModuleHandleW
_GetModuleHandleW.argtypes = [w.LPCWSTR]
_GetModuleHandleW.restype = c_void_p

_PostMessageW = user32.PostMessageW
_PostMessageW.argtypes = [c_void_p, w.UINT, w.WPARAM, w.LPARAM]
_PostMessageW.restype = w.BOOL

_EnumDisplaySettingsW = user32.EnumDisplaySettingsW
_EnumDisplaySettingsW.argtypes = [w.LPCWSTR, w.DWORD, CPOINTER(DEVMODEW)]
_EnumDisplaySettingsW.restype = w.BOOL

# ============================================================
# 回调类型
# ============================================================
WNDPROC = ctypes.WINFUNCTYPE(c_void_p, c_void_p, w.UINT, w.WPARAM, w.LPARAM)

# ============================================================
# 导出全部名称
# ============================================================
__all__ = [name for name in dir() if not name.startswith('__')
           and name not in ('w', 'user32', 'gdi32', 'kernel32',
                            'windll', 'wintypes')]
