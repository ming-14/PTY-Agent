import sys
from pathlib import Path

# fastscreencore 绑定层位于项目根 bin/（构建产物），运行时加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from PySide6.QtWidgets import QApplication
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FastScreen")
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
