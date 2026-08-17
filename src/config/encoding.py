"""编码取值校验 — CLI 与 daemon 两侧共享

校验规则：经 codecs.lookup(name) 判定（大小写不敏感，如 UTF-8 / Utf8 均合法）。
编码模块（src/encoding/）的自动探测语义由 encoding=None 表达，codec.py / detector.py
中并不存在 "auto"/"detect" 之类的字符串特殊取值分支，因此这类字符串同样判为非法。
"""

import codecs


def is_valid_encoding(name) -> bool:
    """编码名称是否合法（codecs.lookup 判定，大小写不敏感）

    None / 空串 / 非字符串 / 未知编码 → False。
    """
    if not isinstance(name, str) or not name:
        return False
    try:
        codecs.lookup(name)
        return True
    except LookupError:
        return False


def validate_encoding(name) -> str:
    """校验编码名称；非法时抛出 ValueError（含清晰错误信息）"""
    if not is_valid_encoding(name):
        raise ValueError(
            f"Invalid encoding: {name!r}. "
            "Use a valid codec name (e.g. utf-8, gbk, cp936, latin-1) "
            "or leave it unset for auto detection."
        )
    return name
