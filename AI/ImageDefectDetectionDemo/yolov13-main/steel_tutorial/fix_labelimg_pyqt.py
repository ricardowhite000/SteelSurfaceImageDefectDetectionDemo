"""Patch LabelImg 1.8.6 for PyQt5 versions with strict overload checking."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


OLD_VERTICAL = (
    "p.drawLine(self.prev_point.x(), 0, self.prev_point.x(), self.pixmap.height())"
)
OLD_HORIZONTAL = (
    "p.drawLine(0, self.prev_point.y(), self.pixmap.width(), self.prev_point.y())"
)
OLD_RECT = "p.drawRect(left_top.x(), left_top.y(), rect_width, rect_height)"
OLD_PAN_HORIZONTAL = "self.scrollRequest.emit(delta_x, Qt.Horizontal)"
OLD_PAN_VERTICAL = "self.scrollRequest.emit(delta_y, Qt.Vertical)"
NEW_VERTICAL = (
    "p.drawLine(QPointF(self.prev_point.x(), 0), "
    "QPointF(self.prev_point.x(), self.pixmap.height()))"
)
NEW_HORIZONTAL = (
    "p.drawLine(QPointF(0, self.prev_point.y()), "
    "QPointF(self.pixmap.width(), self.prev_point.y()))"
)
NEW_RECT = "p.drawRect(QRectF(left_top.x(), left_top.y(), rect_width, rect_height))"
NEW_PAN_HORIZONTAL = (
    "self.scrollRequest.emit(int(round(delta_x)), Qt.Horizontal)"
)
NEW_PAN_VERTICAL = "self.scrollRequest.emit(int(round(delta_y)), Qt.Vertical)"

OLD_SCROLL_VALUE = "bar.setValue(bar.value() + bar.singleStep() * units)"
OLD_ZOOM_VALUE = "self.zoom_widget.setValue(value)"
OLD_HORIZONTAL_BAR_VALUE = "h_bar.setValue(new_h_bar_value)"
OLD_VERTICAL_BAR_VALUE = "v_bar.setValue(new_v_bar_value)"
NEW_SCROLL_VALUE = (
    "bar.setValue(int(round(bar.value() + bar.singleStep() * units)))"
)
NEW_ZOOM_VALUE = "self.zoom_widget.setValue(int(round(value)))"
NEW_HORIZONTAL_BAR_VALUE = "h_bar.setValue(int(round(new_h_bar_value)))"
NEW_VERTICAL_BAR_VALUE = "v_bar.setValue(int(round(new_v_bar_value)))"

OLD_DRAW_TEXT = "painter.drawText(min_x, min_y, self.label)"
NEW_DRAW_TEXT = "painter.drawText(QPointF(min_x, min_y), self.label)"


def _patch_file(path: Path, replacements: tuple[tuple[str, str], ...]) -> bool:
    """Apply exact, idempotent replacements and keep the first source backup."""
    path = path.resolve()
    source = path.read_text(encoding="utf-8")

    pending = []
    for old, new in replacements:
        if old in source:
            pending.append((old, new))
        elif new not in source:
            raise RuntimeError(
                f"{path} 中未找到 LabelImg 1.8.6 的目标代码；为避免误改，已停止。"
            )
    if not pending:
        return False

    backup_path = path.with_suffix(".py.bak")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)

    patched = source
    for old, new in pending:
        patched = patched.replace(old, new, 1)
    compile(patched, str(path), "exec")
    path.write_text(patched, encoding="utf-8")
    return True


def patch_canvas_file(canvas_path: Path) -> bool:
    """Patch canvas drawing and integer scroll-signal boundaries."""
    return _patch_file(
        canvas_path,
        (
        (OLD_RECT, NEW_RECT),
        (OLD_VERTICAL, NEW_VERTICAL),
        (OLD_HORIZONTAL, NEW_HORIZONTAL),
        (OLD_PAN_HORIZONTAL, NEW_PAN_HORIZONTAL),
        (OLD_PAN_VERTICAL, NEW_PAN_VERTICAL),
        ),
    )


def patch_labelimg_main_file(main_path: Path) -> bool:
    """Patch scrollbar and zoom values passed to Qt integer widgets."""
    return _patch_file(
        main_path,
        (
            (OLD_SCROLL_VALUE, NEW_SCROLL_VALUE),
            (OLD_ZOOM_VALUE, NEW_ZOOM_VALUE),
            (OLD_HORIZONTAL_BAR_VALUE, NEW_HORIZONTAL_BAR_VALUE),
            (OLD_VERTICAL_BAR_VALUE, NEW_VERTICAL_BAR_VALUE),
        ),
    )


def patch_shape_file(shape_path: Path) -> bool:
    """Use QPainter's QPointF text overload for fractional label positions."""
    return _patch_file(shape_path, ((OLD_DRAW_TEXT, NEW_DRAW_TEXT),))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canvas",
        type=Path,
        default=Path(r"D:\anaconda\envs\labelimg\Lib\site-packages\libs\canvas.py"),
        help="LabelImg 的 libs/canvas.py 路径",
    )
    parser.add_argument(
        "--labelimg-main",
        dest="labelimg_main",
        type=Path,
        default=Path(
            r"D:\anaconda\envs\labelimg\Lib\site-packages\labelImg\labelImg.py"
        ),
        help="LabelImg 的 labelImg/labelImg.py 路径",
    )
    parser.add_argument(
        "--shape",
        type=Path,
        default=Path(r"D:\anaconda\envs\labelimg\Lib\site-packages\libs\shape.py"),
        help="LabelImg 的 libs/shape.py 路径",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    targets = (
        (args.canvas, patch_canvas_file),
        (args.labelimg_main, patch_labelimg_main_file),
        (args.shape, patch_shape_file),
    )
    for path, patcher in targets:
        changed = patcher(path)
        if changed:
            print(f"已修复: {path.resolve()}")
            print(f"原文件备份: {path.resolve().with_suffix('.py.bak')}")
        else:
            print(f"无需重复修复: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
