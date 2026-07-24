import ctypes

from fastscreencore._core import _Lib, Frame, ImageFormat
from fastscreencore.capture import CapturedFrame


def frame_to_jpeg(frame: CapturedFrame, quality: float = 0.8, width: int = 0, height: int = 0) -> bytes:
    data = frame.to_image_bytes(format="jpeg", quality=quality, width=width, height=height)
    if data:
        return data
    return b""


def frame_to_png(frame: CapturedFrame, width: int = 0, height: int = 0) -> bytes:
    data = frame.to_image_bytes(format="png", width=width, height=height)
    if data:
        return data
    return b""


def encode_bgra_to_jpeg(bgra_data: bytes, width: int, height: int, stride: int,
                        quality: float = 0.8, scale_width: int = 0, scale_height: int = 0) -> bytes:
    lib = _Lib.get()

    # 用 c_char_p 直接引用 bytes 内部缓冲区，避免 ctypes.cast(c_ubyte_Array, POINTER) 创建
    # b_objects dict 循环引用。
    # 旧实现 (c_uint8 * size).from_buffer_copy(bgra_data) 会分配 5MB c_ubyte_Array 并复制数据，
    # 然后 ctypes.cast(c_array, POINTER(c_uint8)) 会在 c_array.b_objects 创建 dict，
    # dict 的 key 指向 c_array 自身，形成循环引用 (c_array → b_objects dict → c_array)，
    # 导致 refcnt 永远 ≥1，只能依赖 GC 回收。mjpeg 高帧率下 GC 来不及，5MB 块累积导致内存泄漏。
    c_ptr = ctypes.c_char_p(bgra_data)

    frame = Frame()
    frame.width = width
    frame.height = height
    frame.stride = stride
    frame.bpp = 4
    frame.format = 0
    frame.data = ctypes.cast(c_ptr, ctypes.POINTER(ctypes.c_uint8))
    frame.owns_data = 0
    frame.timestamp_ms = 0

    buf_ptr = ctypes.POINTER(ctypes.c_uint8)()
    out_size = ctypes.c_int()

    if scale_width > 0 and scale_height > 0:
        result = lib.lib.fs_frame_encode_to_buffer_scaled(
            ctypes.byref(frame), ImageFormat.JPEG, quality,
            scale_width, scale_height,
            ctypes.byref(buf_ptr), ctypes.byref(out_size),
        )
    else:
        result = lib.lib.fs_frame_encode_to_buffer(
            ctypes.byref(frame), ImageFormat.JPEG, quality,
            ctypes.byref(buf_ptr), ctypes.byref(out_size),
        )

    if result != 0 or not buf_ptr or out_size.value <= 0:
        return b""

    try:
        return ctypes.string_at(buf_ptr, out_size.value)
    finally:
        lib.lib.fs_free_buffer(buf_ptr)
