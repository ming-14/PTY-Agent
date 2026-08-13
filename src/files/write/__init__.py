"""file write / edit 用例包 —— 对外导出写入入口与结果类型"""

from .writer import WriteResult, edit_file, write_file

__all__ = ["write_file", "edit_file", "WriteResult"]