"""leaf 分屏终端入口（薄 shim）：全部实现见 leaf 包。"""

import sys

from leaf.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
