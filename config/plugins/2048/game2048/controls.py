"""跨平台输入：读取键盘/鼠标事件（方向键、WASD、鼠标拖拽），忽略滚轮。

统一策略：开启 VT 输入模式后，从 stdin 读取**原始字节流**，再用同一套
字节解析器识别方向键 / 鼠标 SGR 序列 / 普通按键。Windows 用 os.read，
Linux/macOS 用 termios。

关键点：Windows 下 msvcrt.getwch() 读不到 VT 鼠标序列，须走原始字节读取
（现代终端应用的标准做法），故不经 getwch 解析。

纯标准库实现，无第三方依赖。
"""

from __future__ import annotations

import sys

from . import debuglog

# 各事件类型
MOUSE = "mouse"

# 字母键映射（方向键之外的手感补充）
_LETTER_MAP = {
    "w": "up", "a": "left", "s": "down", "d": "right",
    "q": "quit", "r": "restart", "\x03": "quit",  # q / r / Ctrl+C
}

# CSI 方向键（ESC [ A/B/C/D）
_CSI_ARROWS = {
    "A": "up", "B": "down", "C": "right", "D": "left",
}

# 跨调用共享的输入缓冲（未被一次解析消费完的字节）
_BUFFER = bytearray()


def available() -> bool:
    """非阻塞：判断是否有待处理的输入事件。"""
    if sys.platform == "win32":
        import msvcrt
        return bool(_BUFFER) or msvcrt.kbhit()
    import select
    return bool(_BUFFER) or bool(select.select([sys.stdin], [], [], 0)[0])


def read_key():
    """阻塞读取一个输入事件。

    返回:
        - 键盘动作: 'up' / 'down' / 'left' / 'right' / 'quit' / 'restart' / None
        - 鼠标事件: {"type": "mouse", "action": "press"/"move"/"release"/"wheel",
                    "button": int, "x": int, "y": int}（x/y 为终端 1 基列/行）
    """
    if sys.platform == "win32":
        return _next_event(lambda: __import__("os").read(0, 1024))
    return _next_event(_read_posix_byte)


def _next_event(read_chunk):
    """从缓冲（必要时用 read_chunk 补充）解析下一个完整事件。"""
    while True:
        data = bytes(_BUFFER)
        ev, consumed = _parse_bytes(data)
        if consumed:
            del _BUFFER[:consumed]
        if consumed > 0 or ev is not None:
            debuglog.log("in: raw_buf={!r} -> ev={!r}".format(data[:consumed], ev))
            return ev
        chunk = read_chunk()
        if not chunk:
            return None  # EOF
        _BUFFER.extend(chunk)
        debuglog.log("in: os.read chunk={!r}".format(chunk))


def _read_posix_byte():
    """POSIX：阻塞读取一个原始字节（termios 原始模式）。"""
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.buffer.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# --------------------------------------------------------------------- #
# 字节解析                                                                #
# --------------------------------------------------------------------- #
def _parse_bytes(buf: bytes):
    """解析 buffer 中的第一个完整输入事件。

    返回:
        (event, consumed)。event 为 None 且 consumed 为 0 表示序列不完整，
        需要继续读入字节；consumed > 0 时调用方应截断相应字节。
    """
    n = len(buf)
    if n == 0:
        return None, 0

    b0 = buf[0]
    if b0 != 0x1B:  # 普通字符
        ch = chr(b0).lower()
        return _LETTER_MAP.get(ch), 1

    # ESC 开头
    if n < 2:
        return None, 0
    if buf[1] != 0x5B:  # ESC 后非 '['（孤立 ESC / ALT+键）
        return None, 2

    # CSI 序列：buf = ESC '[' ...
    if n >= 3 and buf[2] == 0x3C:  # '<' → SGR 鼠标
        for i in range(3, n):
            if buf[i] in (0x4D, 0x6D):  # 'M' press / 'm' release
                data = buf[3:i].decode("latin-1")
                ev = _parse_sgr(data, buf[i] == 0x6D)
                return ev, i + 1
        return None, 0  # 未到终止符，不完整

    if n >= 3 and buf[2] == 0x4D:  # 'M' → 传统鼠标 ESC[M Cb Cx Cy（6 字节）
        if n < 6:
            return None, 0
        return _mouse_event(buf[3] - 32, buf[4] - 32, buf[5] - 32, False), 6

    # 其他 CSI：方向键（ESC [ A-D）或带参序列（ESC [ 1 ~ 等）
    for i in range(2, n):
        c = buf[i]
        if 0x40 <= c <= 0x7E:  # final byte
            if i == 2 and chr(c) in _CSI_ARROWS:
                return _CSI_ARROWS[chr(c)], i + 1
            return None, i + 1  # 未知/其他 CSI，整段消费
    return None, 0  # 未读到 final byte，不完整


def _parse_sgr(data: str, release: bool):
    parts = data.split(";")
    try:
        b = int(parts[0])
        x = int(parts[1])
        y = int(parts[2])
    except (ValueError, IndexError):
        return None
    return _mouse_event(b, x, y, release)


def _mouse_event(b: int, x: int, y: int, release: bool):
    """SGR 编码：0=左键按下，0+32=按住移动，64/65=滚轮，m 后缀=释放。"""
    if b in (64, 65):  # 滚轮上/下：忽略（不滚动终端）
        action = "wheel"
    elif release:
        action = "release"
    elif 32 <= b < 64:
        action = "move"  # motion 位（按住移动）
    else:
        action = "press"
    return {"type": MOUSE, "action": action, "button": b, "x": x, "y": y}