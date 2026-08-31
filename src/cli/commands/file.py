"""file 命令：文件工具（read/write/edit/grep/glob/upload/download）"""

import argparse
import os
from typing import Optional

from ..base import Command, CommandContext
from ..common_args import add_common_args


class _IntermixedParser(argparse.ArgumentParser):
    """子解析器：允许可选位置参数（如 grep/glob 的 path）与选项任意交错。

    argparse 在“可选位置参数位于必选选项之后”时会把尾随位置参数当成
    无法识别而报错。标准库提供 parse_intermixed_args 解决，但子解析器
    派发走的是 parse_known_args，且 parse_known_intermixed_args 内部又会
    回调 parse_known_args，直接覆盖会无限递归。这里在调用期间临时把
    parse_known_args 指回基类实现以打破递归。
    """

    def parse_known_args(self, args=None, namespace=None):
        orig = type(self).parse_known_args
        try:
            type(self).parse_known_args = argparse.ArgumentParser.parse_known_args
            return self.parse_known_intermixed_args(args, namespace)
        finally:
            type(self).parse_known_args = orig

    def parse_args(self, args=None, namespace=None):
        ns, extra = self.parse_known_args(args, namespace)
        if extra:
            self.error("unrecognized arguments: %s" % " ".join(extra))
        return ns


def _resolve_cli_content(
    inline: Optional[str], content_file: Optional[str], inline_opt: str, file_opt: str
) -> Optional[str]:
    """file write/edit 内容解析：inline 与 --*-file 二选一"""
    if inline and content_file is not None:
        raise ValueError("%s and %s are mutually exclusive" % (inline_opt, file_opt))
    if content_file is not None:
        with open(content_file, "rb") as f:
            try:
                content = f.read().decode("utf-8")
            except UnicodeDecodeError as e:
                raise ValueError(
                    "content file is not valid UTF-8: %s (%s)" % (content_file, e)
                )
        return content.replace("\r\n", "\n")
    return inline


class FileCommand(Command):
    """file 命令"""

    name = "file"
    help = "文件工具（read/write/edit/grep/glob/upload/download）"
    # 公共参数已在各子子命令解析器手动注册（add_common_args），避免两级重复
    use_common_args = False

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        file_sub = parser.add_subparsers(
            dest="file_subcmd", help="文件子命令", parser_class=_IntermixedParser
        )

        # file read
        p_read = file_sub.add_parser("read", help="读取文件内容（带行号）")
        add_common_args(p_read)
        self._add_cwd_session_arg(p_read)
        p_read.add_argument("path", help="文件路径（绝对或相对会话 cwd，支持 ~ 展开）")
        p_read.add_argument("--offset", type=int, default=None, help="起始行号（0-based）")
        p_read.add_argument("--limit", type=int, default=None, help="读取行数（默认 2000）")

        # file write
        p_write = file_sub.add_parser("write", help="覆盖写/新建文件（自动建父目录）")
        add_common_args(p_write)
        self._add_cwd_session_arg(p_write)
        p_write.add_argument("path", help="文件路径（绝对或相对会话 cwd，支持 ~ 展开）")
        p_write.add_argument("--content", default=None, help="写入内容（与 --content-file 二选一；已存在文件需先 file read）")
        p_write.add_argument("--content-file", metavar="FILE", default=None, help="从文件读取写入内容（--content 与 --content-file 二选一；UTF-8）")

        # file edit
        p_edit = file_sub.add_parser("edit", help="唯一匹配替换/删除/新建")
        add_common_args(p_edit)
        self._add_cwd_session_arg(p_edit)
        p_edit.add_argument("path", help="文件路径（绝对或相对会话 cwd，支持 ~ 展开）")
        p_edit.add_argument("--old", default="", help="待替换文本（留空=新建；须唯一匹配）")
        p_edit.add_argument("--old-file", metavar="FILE", default=None, help="从文件读取待替换文本（与 --old 二选一；UTF-8）")
        p_edit.add_argument("--new", default="", help="新文本（留空=删除）")
        p_edit.add_argument("--new-file", metavar="FILE", default=None, help="从文件读取新文本（与 --new 二选一；UTF-8）")

        # file grep
        p_grep = file_sub.add_parser("grep", help="内容搜索（rg 优先）")
        add_common_args(p_grep)
        self._add_cwd_session_arg(p_grep)
        p_grep.add_argument("pattern", help="正则（--literal-text 时按字面量）")
        p_grep.add_argument("path", nargs="?", default=None, help="搜索根（默认会话 cwd）")
        p_grep.add_argument("--include", default=None, help="文件名 glob 过滤（如 *.py）")
        p_grep.add_argument("--literal-text", action="store_true", help="按字面量匹配而非正则")

        # file glob
        p_glob = file_sub.add_parser("glob", help="文件名匹配（rg 优先）")
        add_common_args(p_glob)
        self._add_cwd_session_arg(p_glob)
        p_glob.add_argument("pattern", help="路径 glob（如 *.py、src/**/*.go）")
        p_glob.add_argument("path", nargs="?", default=None, help="搜索根（默认会话 cwd）")

        # file upload
        p_upload = file_sub.add_parser("upload", help="上传本地文件/目录到会话侧（scp -r 语义）")
        add_common_args(p_upload)
        self._add_cwd_session_arg(p_upload, "取该会话 cwd 作为远端路径解析基准（不操作该会话）")
        p_upload.add_argument("local_path", help="本地路径（文件或目录，CLI 本机解析）")
        p_upload.add_argument("remote_path", help="远端路径（绝对或相对会话 cwd，支持 ~ 展开）")
        p_upload.add_argument("--force", action="store_true", help="远端目标已存在且内容不同时允许覆盖")
        p_upload.add_argument("--timeout", type=float, default=None, help="整个传输命令的总时限（秒，默认 120）")

        # file download
        p_download = file_sub.add_parser("download", help="下载会话侧文件/目录到本地（scp -r 语义）")
        add_common_args(p_download)
        self._add_cwd_session_arg(p_download, "取该会话 cwd 作为远端路径解析基准（不操作该会话）")
        p_download.add_argument("remote_path", help="远端路径（绝对或相对会话 cwd，支持 ~ 展开）")
        p_download.add_argument("local_path", help="本地路径（文件或目录，CLI 本机解析）")
        p_download.add_argument("--force", action="store_true", help="本地目标已存在且内容不同时允许覆盖")
        p_download.add_argument("--timeout", type=float, default=None, help="整个传输命令的总时限（秒，默认 120）")

    @staticmethod
    def _add_cwd_session_arg(parser: argparse.ArgumentParser, help_text: str = None) -> None:
        parser.add_argument(
            "-s",
            "--cwd-session",
            metavar="SESSION_ID",
            required=True,
            help=help_text or "取该会话 cwd 作为路径解析基准（不操作该会话）",
        )

    def run(self, args, ctx: CommandContext) -> None:
        if args.file_subcmd == "read":
            ctx.client.cmd_file_read(
                path=args.path,
                cwd_session=args.cwd_session,
                offset=args.offset,
                limit=args.limit,
            )
        elif args.file_subcmd == "write":
            content = _resolve_cli_content(
                args.content, args.content_file, "--content", "--content-file"
            )
            ctx.client.cmd_file_write(
                path=args.path,
                cwd_session=args.cwd_session,
                content=content,
            )
        elif args.file_subcmd == "edit":
            old = _resolve_cli_content(
                args.old, args.old_file, "--old", "--old-file"
            )
            new = _resolve_cli_content(
                args.new, args.new_file, "--new", "--new-file"
            )
            ctx.client.cmd_file_edit(
                path=args.path,
                cwd_session=args.cwd_session,
                old=old,
                new=new,
            )
        elif args.file_subcmd == "grep":
            ctx.client.cmd_file_grep(
                pattern=args.pattern,
                cwd_session=args.cwd_session,
                path=args.path,
                include=args.include,
                literal_text=args.literal_text,
            )
        elif args.file_subcmd == "glob":
            ctx.client.cmd_file_glob(
                pattern=args.pattern,
                cwd_session=args.cwd_session,
                path=args.path,
            )
        elif args.file_subcmd == "upload":
            ctx.client.cmd_file_upload(
                local_path=os.path.abspath(os.path.expanduser(args.local_path)),
                remote_path=args.remote_path,
                cwd_session=args.cwd_session,
                force=args.force,
                timeout=args.timeout,
            )
        elif args.file_subcmd == "download":
            ctx.client.cmd_file_download(
                remote_path=args.remote_path,
                local_path=os.path.abspath(os.path.expanduser(args.local_path)),
                cwd_session=args.cwd_session,
                force=args.force,
                timeout=args.timeout,
            )
        else:
            ctx.parser.print_help()