"""编码子包 — 编解码函数与编码探测状态管理"""

from .codec import (
    auto_detect,
    check_encoding_ok,
    decode_strip_tail,
    detect_decode,
    detect_decode_ext,
)
from .detector import EncodingDetector
