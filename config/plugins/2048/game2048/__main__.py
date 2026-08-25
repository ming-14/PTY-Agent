"""薄入口：仅负责导入并启动应用，逻辑见 app.py。

运行方式：
    python -m game2048          # 在项目根目录执行
"""

from .app import main

if __name__ == "__main__":
    main()
