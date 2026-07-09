"""编码子包 — 编解码函数与编码探测状态管理"""

from .codec import (
    detect_decode,
    decode_strip_tail,
    detect_decode_ext,
    auto_detect,
    check_encoding_ok,
)
from .detector import EncodingDetector
