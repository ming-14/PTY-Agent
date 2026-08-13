from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar,
    QStatusBar, QComboBox, QLabel, QPushButton, QSpinBox,
    QFileDialog, QMessageBox, QTabWidget, QSplitter,
    QGroupBox, QCheckBox, QLineEdit, QSlider, QApplication,
)
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QThread
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut, QPixmap, QImage

import time
from datetime import datetime

from fastscreencore import CaptureEngine, CapturedFrame, TargetType, CaptureMethod


_METHOD_MAP = (CaptureMethod.AUTO, CaptureMethod.DXGI, CaptureMethod.WGC, CaptureMethod.BITBLT)
_METHOD_NAMES = {0: "自动", 1: "DXGI", 2: "WGC", 3: "BitBlt"}


class MainWindow(QMainWindow):
    frame_ready = Signal(object)
    first_frame_received = Signal()

    def __init__(self):
        super().__init__()
        self.engine = CaptureEngine()
        self.current_frame: CapturedFrame | None = None
        self.continuous_running = False
        self.frame_count = 0
        self.fps_timer_start = 0.0
        self._last_frame: CapturedFrame | None = None
        self._last_pixmap_size = None

        self.setWindowTitle("FastScreen - 高性能截屏工具")
        self.setMinimumSize(900, 600)
        self.resize(1200, 800)

        self._setup_ui()
        self._setup_shortcuts()
        self._refresh_targets()

        self.frame_ready.connect(self._on_frame_ready)
        self.first_frame_received.connect(self._on_first_frame)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_targets)
        self._refresh_timer.start(10000)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)

        self.target_type_combo = QComboBox()
        self.target_type_combo.addItems(["显示器", "窗口"])
        self.target_type_combo.currentIndexChanged.connect(self._refresh_targets)
        toolbar.addWidget(QLabel(" 目标: "))
        toolbar.addWidget(self.target_type_combo)

        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(250)
        toolbar.addWidget(self.target_combo)

        toolbar.addSeparator()

        self.method_combo = QComboBox()
        self.method_combo.addItems(["自动", "DXGI", "WGC", "BitBlt"])
        self.method_combo.setCurrentIndex(0)
        self.method_combo.currentIndexChanged.connect(self._on_method_changed)
        toolbar.addWidget(QLabel(" 方法: "))
        toolbar.addWidget(self.method_combo)

        toolbar.addSeparator()

        capture_btn = QPushButton("截图")
        capture_btn.clicked.connect(self._capture_single)
        toolbar.addWidget(capture_btn)

        self.continuous_btn = QPushButton("开始连续捕获")
        self.continuous_btn.clicked.connect(self._toggle_continuous)
        toolbar.addWidget(self.continuous_btn)

        toolbar.addSeparator()

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(60)
        self.fps_spin.setSuffix(" fps")
        toolbar.addWidget(QLabel(" FPS: "))
        toolbar.addWidget(self.fps_spin)

        toolbar.addSeparator()

        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self._save_frame)
        toolbar.addWidget(save_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel("等待截图...")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(400, 300)
        self.preview_label.setStyleSheet(
            "QLabel { background-color: #1e1e1e; color: #888; font-size: 16px; }"
        )
        preview_layout.addWidget(self.preview_label)
        splitter.addWidget(preview_group)

        info_group = QGroupBox("信息")
        info_layout = QVBoxLayout(info_group)

        self.info_label = QLabel("分辨率: -\n帧大小: -\n时间戳: -\n捕获方法: -")
        self.info_label.setStyleSheet("font-family: Consolas; font-size: 13px;")
        info_layout.addWidget(self.info_label)

        self.fps_label = QLabel("FPS: -")
        self.fps_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #4CAF50;")
        info_layout.addWidget(self.fps_label)

        self.frame_count_label = QLabel("帧数: 0")
        info_layout.addWidget(self.frame_count_label)

        info_layout.addStretch()
        splitter.addWidget(info_group)

        splitter.setSizes([800, 300])

        self.statusBar().showMessage("就绪")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("F5"), self, self._capture_single)
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_frame)
        QShortcut(QKeySequence("Escape"), self, self._stop_continuous_if_running)

    def _on_method_changed(self, index):
        is_dxgi = (index == 1)
        self.target_type_combo.setEnabled(not is_dxgi)
        if is_dxgi and self.target_type_combo.currentIndex() != 0:
            self.target_type_combo.setCurrentIndex(0)

    def _refresh_targets(self):
        current_data = self.target_combo.currentData()
        scrollbar = self.target_combo.view().verticalScrollBar()
        scroll_pos = scrollbar.value() if scrollbar else 0

        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        idx = self.target_type_combo.currentIndex()

        select_index = -1
        if idx == 0:
            monitors = self.engine.enumerate_monitors()
            for i, m in enumerate(monitors):
                primary_tag = " [主]" if m.primary else ""
                self.target_combo.addItem(
                    f"{m.name} - {m.width}x{m.height}{primary_tag}",
                    m.id,
                )
                if current_data is not None and m.id == current_data:
                    select_index = i
        else:
            windows = self.engine.enumerate_windows()
            for i, w in enumerate(windows):
                self.target_combo.addItem(
                    f"{w.title} ({w.width}x{w.height})",
                    int(w.hwnd) if w.hwnd else 0,
                )
                if current_data is not None and w.hwnd and int(w.hwnd) == current_data:
                    select_index = i

        if select_index >= 0:
            self.target_combo.setCurrentIndex(select_index)
        elif self.target_combo.count() > 0:
            self.target_combo.setCurrentIndex(0)

        self.target_combo.blockSignals(False)
        if scrollbar:
            scrollbar.setValue(scroll_pos)

    def _get_target_info(self):
        target_type = TargetType.MONITOR if self.target_type_combo.currentIndex() == 0 else TargetType.WINDOW
        target_id = self.target_combo.currentData()
        method = _METHOD_MAP[self.method_combo.currentIndex()]
        if target_id is None:
            target_id = 0
        return target_type, target_id, method

    def _capture_single(self):
        target_type, target_id, method = self._get_target_info()

        self.statusBar().showMessage("正在截图...")

        start = time.perf_counter()
        frame, hint = self.engine._capture(target_type, target_id, method)
        elapsed = (time.perf_counter() - start) * 1000

        if frame is None:
            msg = f"截图失败: {hint}" if hint else "截图失败"
            self.statusBar().showMessage(msg)
            return

        self.current_frame = frame
        self._display_frame(frame)
        self.statusBar().showMessage(f"截图完成 ({elapsed:.1f}ms)")

    def _display_frame(self, frame: CapturedFrame):
        qimg = frame.to_qimage()
        pixmap = QPixmap.fromImage(qimg)

        label_size = self.preview_label.size()
        if label_size.width() > 0 and label_size.height() > 0:
            if self._last_pixmap_size != label_size:
                self._last_pixmap_size = label_size
            scaled = pixmap.scaled(
                label_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self.preview_label.setPixmap(scaled)

        size_kb = (frame.stride * frame.height) / 1024
        self.info_label.setText(
            f"分辨率: {frame.width}x{frame.height}\n"
            f"步长: {frame.stride}\n"
            f"帧大小: {size_kb:.0f} KB\n"
            f"时间戳: {frame.timestamp_ms}\n"
            f"捕获方法: {_METHOD_NAMES.get(self.method_combo.currentIndex(), '?')}"
        )

    def _toggle_continuous(self):
        if self.continuous_running:
            self._stop_continuous_if_running()
        else:
            self._start_continuous()

    def _start_continuous(self):
        target_type, target_id, method = self._get_target_info()
        fps = self.fps_spin.value()

        self.frame_count = 0
        self.fps_timer_start = time.perf_counter()

        success = self.engine.start_continuous(
            target_type,
            target_id,
            self._on_continuous_frame,
            fps=fps,
            method=method,
        )

        if success:
            self.continuous_running = True
            self.continuous_btn.setText("停止连续捕获")
            self.continuous_btn.setStyleSheet("background-color: #f44336; color: white;")
            method_names = {CaptureMethod.AUTO: "Auto", CaptureMethod.DXGI: "DXGI",
                            CaptureMethod.WGC: "WGC", CaptureMethod.BITBLT: "BitBlt"}
            self.statusBar().showMessage(f"连续捕获中 ({fps} fps, {method_names.get(method, '?')})")

            self.fps_update_timer = QTimer()
            self.fps_update_timer.timeout.connect(self._update_fps)
            self.fps_update_timer.start(500)

            self._no_frame_timer = QTimer()
            self._no_frame_timer.setSingleShot(True)
            self._no_frame_timer.timeout.connect(self._on_no_frame_received)
            self._no_frame_timer.start(5000)
        else:
            hint = ""
            if method == CaptureMethod.DXGI and target_type == TargetType.WINDOW:
                hint = "DXGI 不支持窗口截屏，请使用 WGC 或 BitBlt"
            elif method == CaptureMethod.WGC:
                hint = "WGC 启动失败，请使用 BitBlt 或 Auto"
            elif method == CaptureMethod.DXGI:
                hint = "DXGI 启动失败，请使用 WGC 或 BitBlt"
            msg = f"启动连续捕获失败: {hint}" if hint else "启动连续捕获失败"
            self.statusBar().showMessage(msg)

    def _stop_continuous_if_running(self):
        if not self.continuous_running:
            return

        self.engine.stop_continuous()
        self.continuous_running = False
        self.continuous_btn.setText("开始连续捕获")
        self.continuous_btn.setStyleSheet("")

        if hasattr(self, "fps_update_timer"):
            self.fps_update_timer.stop()
        if hasattr(self, "_no_frame_timer"):
            self._no_frame_timer.stop()

        if self._last_frame is not None:
            self._last_frame.release()
            self._last_frame = None

        self.statusBar().showMessage("连续捕获已停止")

    def _on_continuous_frame(self, frame: CapturedFrame):
        self.frame_count += 1
        if self.frame_count == 1:
            self.first_frame_received.emit()
        try:
            if self._last_frame is not None:
                self._last_frame.release()
            self._last_frame = frame
            self.frame_ready.emit(frame)
        except Exception as e:
            frame.release()

    def _on_first_frame(self):
        self.statusBar().showMessage("连续捕获中 - 已收到帧")
        if hasattr(self, '_no_frame_timer'):
            self._no_frame_timer.stop()

    def _on_no_frame_received(self):
        if self.continuous_running and self.frame_count == 0:
            method = self._get_target_info()[2]
            hint = ""
            if method == CaptureMethod.WGC:
                hint = "WGC 未收到帧（窗口过小或不支持），请切换 BitBlt 或 Auto"
            elif method == CaptureMethod.DXGI:
                hint = "DXGI 未收到帧，请切换 WGC 或 BitBlt"
            msg = f"连续捕获无输出: {hint}" if hint else "连续捕获无输出，请尝试其他方法"
            self.statusBar().showMessage(msg)

    def _on_frame_ready(self, frame: CapturedFrame):
        try:
            qimg = frame.to_qimage()
            pixmap = QPixmap.fromImage(qimg)
            label_size = self.preview_label.size()
            if label_size.width() > 0 and label_size.height() > 0:
                scaled = pixmap.scaled(
                    label_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                )
                self.preview_label.setPixmap(scaled)

            size_kb = (frame.stride * frame.height) / 1024
            self.info_label.setText(
                f"分辨率: {frame.width}x{frame.height}\n"
                f"步长: {frame.stride}\n"
                f"帧大小: {size_kb:.0f} KB\n"
                f"时间戳: {frame.timestamp_ms}\n"
                f"捕获方法: {_METHOD_NAMES.get(self.method_combo.currentIndex(), '?')}"
            )
        except Exception:
            pass

    def _update_fps(self):
        if self.continuous_running and not self.engine.is_running():
            self._stop_continuous_if_running()
            self.statusBar().showMessage("目标窗口已关闭，连续捕获已停止")
            return

        elapsed = time.perf_counter() - self.fps_timer_start
        if elapsed > 0:
            fps = self.frame_count / elapsed
            self.fps_label.setText(f"FPS: {fps:.1f}")
            self.frame_count_label.setText(f"帧数: {self.frame_count}")

    def _save_frame(self):
        if not self.current_frame:
            self.statusBar().showMessage("没有可保存的帧")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"fastscreen_{timestamp}.png"

        path, _ = QFileDialog.getSaveFileName(
            self, "保存截图", default_name,
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)",
        )

        if path:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
            fmt_map = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "bmp": "bmp"}
            fmt = fmt_map.get(ext, "png")

            if self.current_frame.save(path, format=fmt):
                self.statusBar().showMessage(f"已保存: {path}")
            else:
                self.statusBar().showMessage("保存失败")

    def closeEvent(self, event):
        self._stop_continuous_if_running()
        super().closeEvent(event)
