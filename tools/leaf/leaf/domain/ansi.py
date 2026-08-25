"""ANSI 渲染辅助：Mux 门面增量渲染所需的常量。

逐格字符/颜色合成（cell_line）与光标定位序列（cursor_seq）已下沉到
pywezterm.Mux（Surface 合成 + 增量 diff + 光标序列生成），此处仅保留
宿主终端的清场/显隐光标常量。
"""

ANSI_RESET = "\x1b[0m"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"
