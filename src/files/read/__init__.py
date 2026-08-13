"""file read 用例包 —— 对外导出读取入口、结果类型与模块对象"""

from . import reader
from .reader import ReadResult, read_file

__all__ = ["reader", "read_file", "ReadResult"]