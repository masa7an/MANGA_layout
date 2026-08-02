"""拡大・縮小の検証。

倍率そのものより、**キーを横取りしないこと**が要点。画面はキー入力を
最初に受け取るので、ここで拾いすぎると文字として打てないキーができる。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.canvas import (
    KEY_ZOOM_STEP,
    MAX_VIEW_SCALE,
    MIN_VIEW_SCALE,
    WHEEL_ZOOM_STEP,
)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.view.finish_text_edit(commit=False)
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_text(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], Rect(20.0, 20.0, 140.0, 110.0))
    window.state.select(None)
    window.state.add_text(Rect(40.0, 40.0, 60.0, 24.0), "セリフ")
    return window


def key(view, qt_key, text: str = "", modifiers=None) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            qt_key,
            modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier,
            text,
        )
    )


def wheel(view, up: bool = True) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    position = QPointF(view.viewport().rect().center())
    view.wheelEvent(
        QWheelEvent(
            position,
            view.viewport().mapToGlobal(position),
            QPoint(0, 0),
            QPoint(0, 120 if up else -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )
    )


class TestWheel:
    def test_ホイール上で拡大する(self, window):
        before = window.view.view_scale
        wheel(window.view, up=True)
        assert window.view.view_scale == pytest.approx(before * WHEEL_ZOOM_STEP)

    def test_ホイール下で縮小する(self, window):
        before = window.view.view_scale
        wheel(window.view, up=False)
        assert window.view.view_scale == pytest.approx(before / WHEEL_ZOOM_STEP)

    def test_ホイールでは画面が流れない(self, window):
        """割り当てを変えた以上、上下移動に化けてはいけない。"""
        bar = window.view.verticalScrollBar()
        before = bar.value()
        wheel(window.view, up=True)
        wheel(window.view, up=False)
        assert bar.value() == before


class TestKeys:
    def test_プラスで拡大する(self, window):
        from PySide6.QtCore import Qt

        before = window.view.view_scale
        key(window.view, Qt.Key.Key_Plus, "+")
        assert window.view.view_scale == pytest.approx(before * KEY_ZOOM_STEP)

    def test_マイナスで縮小する(self, window):
        from PySide6.QtCore import Qt

        before = window.view.view_scale
        key(window.view, Qt.Key.Key_Minus, "-")
        assert window.view.view_scale == pytest.approx(before / KEY_ZOOM_STEP)

    def test_イコールでも拡大する(self, window):
        """配列によっては + が Shift+= になる。"""
        from PySide6.QtCore import Qt

        before = window.view.view_scale
        key(window.view, Qt.Key.Key_Equal, "=")
        assert window.view.view_scale > before

    def test_行って戻る(self, window):
        from PySide6.QtCore import Qt

        before = window.view.view_scale
        key(window.view, Qt.Key.Key_Plus, "+")
        key(window.view, Qt.Key.Key_Minus, "-")
        assert window.view.view_scale == pytest.approx(before)


class TestLimits:
    def test_縮小しすぎない(self, window):
        from PySide6.QtCore import Qt

        for _ in range(60):
            key(window.view, Qt.Key.Key_Minus, "-")
        assert window.view.view_scale == pytest.approx(MIN_VIEW_SCALE)

    def test_拡大しすぎない(self, window):
        from PySide6.QtCore import Qt

        for _ in range(60):
            key(window.view, Qt.Key.Key_Plus, "+")
        assert window.view.view_scale == pytest.approx(MAX_VIEW_SCALE)

    def test_上限に達したら変わらないと答える(self, window):
        for _ in range(60):
            window.view.zoom_in()
        assert window.view.zoom_in() is False

    def test_原寸で表示できる(self, window):
        window.zoom_actual()
        assert window.view.zoom_percent() == pytest.approx(100.0, abs=0.5)


class TestDoesNotStealTyping:
    """入力中はキーを横取りしない。ここが本命。"""

    def test_入力中のマイナスで縮小しない(self, window_with_text):
        from PySide6.QtCore import Qt

        view = window_with_text.view
        view.begin_text_edit(window_with_text.state.selected_text.id)
        before = view.view_scale

        key(view, Qt.Key.Key_Minus, "-")

        assert view.view_scale == pytest.approx(before), "文字を打っただけで縮んだ"

    def test_入力中のプラスで拡大しない(self, window_with_text):
        from PySide6.QtCore import Qt

        view = window_with_text.view
        view.begin_text_edit(window_with_text.state.selected_text.id)
        before = view.view_scale

        key(view, Qt.Key.Key_Plus, "+")

        assert view.view_scale == pytest.approx(before)

    def test_入力中のマイナスは文字として入る(self, window_with_text):
        from PySide6.QtCore import Qt

        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id
        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("")

        key(view, Qt.Key.Key_Minus, "-")
        view.finish_text_edit(commit=True)

        assert window_with_text.state.page.find(text_id).content == "-"

    def test_入力中のEscで入力を取り消す(self, window_with_text):
        """以前は画面側が Esc を先に食べてしまい、取り消せなかった。"""
        from PySide6.QtCore import Qt

        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id
        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("捨てる文字")

        key(view, Qt.Key.Key_Escape)

        assert not view.is_editing_text, "Esc で入力が終わっていない"
        assert window_with_text.state.page.find(text_id).content == "セリフ"

    def test_入力中のEnterは改行になる(self, window_with_text):
        from PySide6.QtCore import Qt

        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id
        view.begin_text_edit(text_id)

        key(view, Qt.Key.Key_Return)

        assert view.is_editing_text, "Enter で入力が終わってしまった"

    def test_入力していなければEnterで入力に入る(self, window_with_text):
        from PySide6.QtCore import Qt

        view = window_with_text.view
        key(view, Qt.Key.Key_Return)
        assert view.is_editing_text
