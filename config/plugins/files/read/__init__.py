"""file read 用例子包 —— 对外导出读取入口与结果类型"""

from . import reader
from .reader import ReadResult, read_file

__all__ = ["reader", "read_file", "ReadResult"]
