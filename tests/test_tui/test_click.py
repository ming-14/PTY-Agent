import ctypes as c
import sys
import pytest

if sys.platform != "win32":
    pytest.skip("Windows-Only Manual Test", allow_module_level=True)

kernel32 = c.windll.kernel32
h = kernel32.GetStdHandle(-10)

class COORD(c.Structure):
    _fields_ = [("X", c.c_short), ("Y", c.c_short)]

class MOUSE(c.Structure):
    _fields_ = [("pos", COORD), ("btn", c.c_ulong), ("ctrl", c.c_ulong), ("flags", c.c_ulong)]

class EVT(c.Union):
    _fields_ = [("mouse", MOUSE)]

class REC(c.Structure):
    _fields_ = [("type", c.c_ushort), ("evt", EVT)]

mode = c.c_ulong()
kernel32.GetConsoleMode(h, c.byref(mode))
kernel32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0010 | 0x0080)

r, n = REC(), c.c_ulong()
try:
    while kernel32.ReadConsoleInputW(h, c.byref(r), 1, c.byref(n)):
        if r.type == 2 and r.evt.mouse.btn & 1 and r.evt.mouse.flags == 0:
            print(f"({r.evt.mouse.pos.X}, {r.evt.mouse.pos.Y})")
except KeyboardInterrupt:
    pass
finally:
    kernel32.SetConsoleMode(h, mode.value)
