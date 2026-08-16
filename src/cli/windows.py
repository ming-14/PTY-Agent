"""Windows 专属的 CLI 命令行引号修复

Windows 下 exec -c 的命令值若被外层 Shell 错误加引号，用 CommandLineToArgvW
重新解析原始命令行以还原正确的命令文本。
"""

import sys


def fix_windows_exec_quoting() -> None:
    """Windows: 修复 exec -c 命令被额外加引号的问题

    若 argv 中 exec 的 -c 命令值以 '"' 包裹，说明被外层解析破坏；
    用 GetCommandLineW + CommandLineToArgvW 重新解析原始命令行并还原。
    """
    if sys.platform != "win32":
        return

    import ctypes
    import ctypes.wintypes

    argv = sys.argv
    exec_idx = None
    c_idx = None

    for i, arg in enumerate(argv):
        if arg == "exec":
            exec_idx = i
            break
    if exec_idx is None:
        return

    for i in range(exec_idx + 1, len(argv)):
        if argv[i] in ("-c", "--command"):
            c_idx = i
            break
    if c_idx is None or c_idx + 1 >= len(argv):
        return

    cmd_val = argv[c_idx + 1]

    if not (cmd_val.startswith('"') and cmd_val.endswith('"')):
        return

    try:
        kernel32 = ctypes.windll.kernel32
        GetCommandLineW = kernel32.GetCommandLineW
        GetCommandLineW.argtypes = []
        GetCommandLineW.restype = ctypes.wintypes.LPCWSTR
        raw_cmdline = GetCommandLineW()
        if not raw_cmdline:
            return

        shell32 = ctypes.windll.shell32
        CommandLineToArgvW = shell32.CommandLineToArgvW
        CommandLineToArgvW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ]
        CommandLineToArgvW.restype = ctypes.POINTER(ctypes.wintypes.LPWSTR)

        argc = ctypes.c_int(0)
        argv_ptr = CommandLineToArgvW(raw_cmdline, ctypes.byref(argc))

        if not argv_ptr or argc.value < 2:
            return

        try:
            parsed_argv = [argv_ptr[i] for i in range(argc.value)]
        finally:
            LocalFree = kernel32.LocalFree
            LocalFree.argtypes = [ctypes.wintypes.HLOCAL]
            LocalFree(argv_ptr)

        new_c_idx = None
        for i, arg in enumerate(parsed_argv):
            if arg in ("-c", "--command"):
                new_c_idx = i
                break

        if new_c_idx is not None and new_c_idx + 1 < len(parsed_argv):
            new_cmd_val = parsed_argv[new_c_idx + 1]
            if new_cmd_val != cmd_val and len(new_cmd_val) > len(cmd_val):
                sys.argv = parsed_argv
    except Exception:
        pass
