"""Regression check for LabelImg 1.8.6 on strict PyQt5 bindings."""

import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from steel_tutorial.fix_labelimg_pyqt import patch_canvas_file


CANVAS_PATH = Path(
    r"D:\anaconda\envs\labelimg\Lib\site-packages\libs\canvas.py"
)
LABELIMG_MAIN_PATH = Path(
    r"D:\anaconda\envs\labelimg\Lib\site-packages\labelImg\labelImg.py"
)
SHAPE_PATH = Path(
    r"D:\anaconda\envs\labelimg\Lib\site-packages\libs\shape.py"
)
QT_APP = None


def get_qt_app():
    global QT_APP
    from PyQt5.QtWidgets import QApplication

    QT_APP = QApplication.instance() or QApplication([])
    return QT_APP


class LabelImgPyQtCompatibilityTest(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PyQt5"), "PyQt5 is not installed")
    def test_scroll_request_accepts_standard_wheel_delta(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QScrollBar
        from labelImg.labelImg import MainWindow

        app = get_qt_app()
        self.assertIsNotNone(app)
        bar = QScrollBar()
        bar.setRange(0, 1000)
        bar.setSingleStep(20)
        bar.setValue(500)
        window = SimpleNamespace(scroll_bars={Qt.Vertical: bar})

        try:
            MainWindow.scroll_request(window, 120, Qt.Vertical)
        except TypeError as error:
            self.fail(f"scroll_request passed a float to QScrollBar: {error}")

        self.assertEqual(480, bar.value())

    @unittest.skipUnless(importlib.util.find_spec("PyQt5"), "PyQt5 is not installed")
    def test_shape_can_paint_fractional_label_position(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtCore import QPointF
        from PyQt5.QtGui import QImage, QPainter
        from libs.shape import Shape

        get_qt_app()
        image = QImage(40, 40, QImage.Format_ARGB32)
        painter = QPainter(image)
        shape = Shape("defect", paint_label=True)
        for point in (
            QPointF(2.5, 3.5),
            QPointF(30.5, 3.5),
            QPointF(30.5, 25.5),
            QPointF(2.5, 25.5),
        ):
            shape.add_point(point)
        shape.close()

        try:
            shape.paint(painter)
        except TypeError as error:
            self.fail(f"Shape.paint passed float coordinates to QPainter: {error}")
        finally:
            painter.end()

    @unittest.skipUnless(importlib.util.find_spec("PyQt5"), "PyQt5 is not installed")
    def test_qpointf_drawline_overload_accepts_fractional_coordinates(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtCore import QPointF, QRectF
        from PyQt5.QtGui import QImage, QPainter

        image = QImage(20, 20, QImage.Format_ARGB32)
        painter = QPainter(image)
        painter.drawLine(QPointF(3.5, 0), QPointF(3.5, 19))
        painter.drawLine(QPointF(0, 7.5), QPointF(19, 7.5))
        painter.drawRect(QRectF(2.5, 4.5, 10.0, 8.0))
        self.assertTrue(painter.end())

    def test_patcher_rewrites_canvas_compatibility_calls(self):
        old_source = """\
before
p.drawRect(left_top.x(), left_top.y(), rect_width, rect_height)
p.drawLine(self.prev_point.x(), 0, self.prev_point.x(), self.pixmap.height())
p.drawLine(0, self.prev_point.y(), self.pixmap.width(), self.prev_point.y())
self.scrollRequest.emit(delta_x, Qt.Horizontal)
self.scrollRequest.emit(delta_y, Qt.Vertical)
after
"""
        with tempfile.TemporaryDirectory() as directory:
            canvas_path = Path(directory) / "canvas.py"
            canvas_path.write_text(old_source, encoding="utf-8")

            changed = patch_canvas_file(canvas_path)
            new_source = canvas_path.read_text(encoding="utf-8")

        self.assertTrue(changed)
        self.assertIn("before", new_source)
        self.assertIn("after", new_source)
        self.assertIn(
            "p.drawRect(QRectF(left_top.x(), left_top.y(), rect_width, rect_height))",
            new_source,
        )
        self.assertIn("p.drawLine(QPointF(self.prev_point.x(), 0),", new_source)
        self.assertIn("p.drawLine(QPointF(0, self.prev_point.y()),", new_source)
        self.assertIn(
            "self.scrollRequest.emit(int(round(delta_x)), Qt.Horizontal)",
            new_source,
        )
        self.assertIn(
            "self.scrollRequest.emit(int(round(delta_y)), Qt.Vertical)",
            new_source,
        )

    def test_installed_canvas_uses_safe_qt_overloads_and_signal_types(self):
        source = CANVAS_PATH.read_text(encoding="utf-8")
        compile(source, str(CANVAS_PATH), "exec")

        self.assertIn(
            "p.drawRect(QRectF(left_top.x(), left_top.y(), rect_width, rect_height))",
            source,
            "LabelImg still passes float coordinates to drawRect's int overload",
        )
        self.assertIn(
            "p.drawLine(QPointF(self.prev_point.x(), 0),",
            source,
            "LabelImg still passes float coordinates to drawLine's int overload",
        )
        self.assertIn(
            "p.drawLine(QPointF(0, self.prev_point.y()),",
            source,
            "LabelImg still passes float coordinates to drawLine's int overload",
        )
        self.assertIn(
            "self.scrollRequest.emit(int(round(delta_x)), Qt.Horizontal)",
            source,
            "Canvas still emits a float through an integer scroll signal",
        )
        self.assertIn(
            "self.scrollRequest.emit(int(round(delta_y)), Qt.Vertical)",
            source,
            "Canvas still emits a float through an integer scroll signal",
        )

    def test_mainwindow_qt_integer_apis_receive_integers(self):
        source = LABELIMG_MAIN_PATH.read_text(encoding="utf-8")
        compile(source, str(LABELIMG_MAIN_PATH), "exec")

        self.assertIn(
            "bar.setValue(int(round(bar.value() + bar.singleStep() * units)))",
            source,
        )
        self.assertIn("self.zoom_widget.setValue(int(round(value)))", source)
        self.assertIn("h_bar.setValue(int(round(new_h_bar_value)))", source)
        self.assertIn("v_bar.setValue(int(round(new_v_bar_value)))", source)

    def test_shape_uses_qpointf_text_overload(self):
        source = SHAPE_PATH.read_text(encoding="utf-8")
        compile(source, str(SHAPE_PATH), "exec")

        self.assertIn(
            "painter.drawText(QPointF(min_x, min_y), self.label)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
