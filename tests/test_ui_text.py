"""セリフまわりの検証（画面なし）。

**文字そのものの見た目は確かめられない。** テストは offscreen で動かしており、
この環境には使えるフォントが1つも無い（`QFontDatabase.families()` が空）ため、
描いた画素を数える検証はフォントを要する部分だけ飛ばしてある。
代わりに「操作 → モデルの変更 → 履歴に積む」と、吹き出しへの追随、
保存の往復を押さえる。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import TextObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT, TOOL_TEXT

PANEL = Rect(20.0, 20.0, 140.0, 110.0)
BALLOON = Rect(40.0, 40.0, 60.0, 40.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.view.finish_text_edit(commit=False)
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_balloon(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(None)
    window.state.add_balloon(BALLOON)
    window.state.select(None)
    return window


@pytest.fixture
def window_with_text(window_with_balloon):
    """吹き出しの上にセリフを1つ置いた状態。セリフが選ばれている。"""
    window_with_balloon.state.add_text(Rect(45.0, 50.0, 50.0, 20.0), "セリフ")
    return window_with_balloon


def only_text(page) -> TextObject:
    """ページにある唯一のセリフ。並び順に頼らずに取る。"""
    texts = [f for f in page.floating if isinstance(f, TextObject)]
    assert len(texts) == 1, f"セリフが {len(texts)} 個ある"
    return texts[0]


def press(view, x: float, y: float) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def release(view, x: float, y: float) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mouseReleaseEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def double_click(view, x: float, y: float) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mouseDoubleClickEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def click(view, x: float, y: float) -> None:
    press(view, x, y)
    release(view, x, y)


class TestAdd:
    def test_道具で置ける(self, window_with_balloon):
        window_with_balloon.state.set_tool(TOOL_TEXT)
        click(window_with_balloon.view, *BALLOON.center)

        texts = [f for f in window_with_balloon.state.page.floating if isinstance(f, TextObject)]
        assert len(texts) == 1
        assert texts[0].rect.center == pytest.approx(BALLOON.center)

    def test_置いたら選択の道具に戻る(self, window_with_balloon):
        window_with_balloon.state.set_tool(TOOL_TEXT)
        click(window_with_balloon.view, *BALLOON.center)
        assert window_with_balloon.state.tool == TOOL_SELECT

    def test_置いたらすぐ入力できる(self, window_with_balloon):
        """空の枠だけ残しても仕方がないので、続けて打てる状態にする。"""
        window_with_balloon.state.set_tool(TOOL_TEXT)
        click(window_with_balloon.view, *BALLOON.center)
        assert window_with_balloon.view.is_editing_text

    def test_履歴に積まれる(self, window_with_text):
        assert window_with_text.state.history.undo_label == "セリフの追加"
        window_with_text.state.undo()
        assert window_with_text.state.selected_text is None

    def test_吹き出しの上なら吹き出しに紐づく(self, window_with_text):
        balloon = window_with_text.state.page.floating[0]
        assert window_with_text.state.selected_text.attached_balloon_id == balloon.id

    def test_吹き出しの外ならコマに紐づく(self, window_with_balloon):
        window_with_balloon.state.add_text(Rect(120.0, 100.0, 30.0, 15.0))
        text = window_with_balloon.state.selected_text
        assert text.attached_balloon_id is None
        assert text.attached_panel_id == window_with_balloon.state.page.panels[0].id

    def test_どこにも重ならなければ紐づかない(self, window_with_balloon):
        window_with_balloon.state.add_text(Rect(170.0, 250.0, 30.0, 15.0))
        text = window_with_balloon.state.selected_text
        assert text.attached_balloon_id is None
        assert text.attached_panel_id is None


class TestFollowBalloon:
    """吹き出しの上に置いたセリフは吹き出しに追随する（要件定義 6.5）。"""

    def test_吹き出しを動かすと付いてくる(self, window_with_text):
        state = window_with_text.state
        before = state.selected_text.rect
        balloon = state.page.floating[0]
        state.select(balloon.id)

        window_with_text.view._apply_move(BALLOON, BALLOON.translated(20.0, 10.0))

        after = [f for f in state.page.floating if isinstance(f, TextObject)][0].rect
        assert (after.x, after.y) == pytest.approx((before.x + 20.0, before.y + 10.0))

    def test_コマを動かすと吹き出しごと付いてくる(self, window_with_text):
        state = window_with_text.state
        before = state.selected_text.rect
        state.select(state.page.panels[0].id)

        window_with_text.view._apply_move(PANEL, PANEL.translated(15.0, 0.0))

        after = [f for f in state.page.floating if isinstance(f, TextObject)][0].rect
        assert after.x == pytest.approx(before.x + 15.0)

    def test_二重に動かさない(self, window_with_text):
        """コマにも吹き出しにも紐づいていると、二重に動いて位置がずれる。"""
        state = window_with_text.state
        text_id = state.selected_text.id
        with state.edit("両方に紐づける") as project:
            target = project.pages[0].find(text_id)
            target.attached_panel_id = project.pages[0].panels[0].id
        before = state.page.find(text_id).rect

        state.select(state.page.panels[0].id)
        window_with_text.view._apply_move(PANEL, PANEL.translated(10.0, 0.0))

        assert state.page.find(text_id).rect.x == pytest.approx(before.x + 10.0)

    def test_セリフだけ動かしても吹き出しは動かない(self, window_with_text):
        state = window_with_text.state
        balloon_rect = state.page.floating[0].rect
        origin = state.selected_text.rect

        window_with_text.view._apply_move(origin, origin.translated(5.0, 5.0))

        assert state.page.floating[0].rect == balloon_rect

    def test_吹き出しを消してもセリフは残る(self, window_with_text):
        state = window_with_text.state
        state.select(state.page.floating[0].id)

        window_with_text.delete_selected()

        texts = [f for f in state.page.floating if isinstance(f, TextObject)]
        assert len(texts) == 1
        assert texts[0].attached_balloon_id is None


class TestSelectAndEdit:
    def test_クリックで選べる(self, window_with_text):
        state = window_with_text.state
        text_id = state.selected_text.id
        state.select(None)

        press(window_with_text.view, *state.page.find(text_id).rect.center)

        assert state.selected_id == text_id

    def test_吹き出しより先に拾われる(self, window_with_text):
        """セリフは吹き出しの上。逆だと文字を掴んだつもりで吹き出しが動く。"""
        state = window_with_text.state
        center = only_text(state.page).rect.center
        state.select(None)

        press(window_with_text.view, *center)

        assert state.selected_text is not None
        assert state.selected_balloon is None

    def test_ダブルクリックで入力に入る(self, window_with_text):
        state = window_with_text.state
        center = only_text(state.page).rect.center
        state.select(None)

        double_click(window_with_text.view, *center)

        assert window_with_text.view.is_editing_text

    def test_入力した内容が入る(self, window_with_text):
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("あたらしい\nセリフ")
        view.finish_text_edit(commit=True)

        assert window_with_text.state.page.find(text_id).content == "あたらしい\nセリフ"

    def test_取り消せば元のまま(self, window_with_text):
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("捨てる")
        view.finish_text_edit(commit=False)

        assert window_with_text.state.page.find(text_id).content == "セリフ"

    def test_入力は履歴に1手だけ積む(self, window_with_text):
        view = window_with_text.view
        state = window_with_text.state
        depth = state.history.depth

        view.begin_text_edit(state.selected_text.id)
        for word in ("あ", "あい", "あいう"):
            view._text_editor.setPlainText(word)
        view.finish_text_edit(commit=True)

        assert state.history.depth == depth + 1

    def test_変えなければ履歴に積まない(self, window_with_text):
        view = window_with_text.view
        depth = window_with_text.state.history.depth

        view.begin_text_edit(window_with_text.state.selected_text.id)
        view.finish_text_edit(commit=True)

        assert window_with_text.state.history.depth == depth

    def test_画面を触ると確定する(self, window_with_text):
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("確定される")
        press(view, 170.0, 250.0)

        assert not view.is_editing_text
        assert window_with_text.state.page.find(text_id).content == "確定される"

    def test_元に戻せる(self, window_with_text):
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("書き換え")
        view.finish_text_edit(commit=True)
        window_with_text.state.undo()

        assert window_with_text.state.page.find(text_id).content == "セリフ"


class TestFormat:
    def test_整列を変えられる(self, window_with_text):
        for align in ("left", "right", "center"):
            window_with_text.set_text_align(align)
            assert window_with_text.state.selected_text.align == align

    def test_同じ整列なら履歴に積まない(self, window_with_text):
        depth = window_with_text.state.history.depth
        window_with_text.set_text_align(window_with_text.state.selected_text.align)
        assert window_with_text.state.history.depth == depth

    def test_大きさを段階で変えられる(self, window_with_text):
        before = window_with_text.state.selected_text.font.size_mm
        window_with_text.step_text_size(1)
        assert window_with_text.state.selected_text.font.size_mm > before
        window_with_text.step_text_size(-1)
        assert window_with_text.state.selected_text.font.size_mm == pytest.approx(before)

    def test_小さくしすぎない(self, window_with_text):
        for _ in range(50):
            window_with_text.step_text_size(-1)
        assert window_with_text.state.selected_text.font.size_mm >= 1.5

    def test_大きくしすぎない(self, window_with_text):
        for _ in range(200):
            window_with_text.step_text_size(1)
        assert window_with_text.state.selected_text.font.size_mm <= 30.0

    def test_太字にできる(self, window_with_text):
        window_with_text.toggle_bold()
        assert window_with_text.state.selected_text.font.bold
        window_with_text.toggle_bold()
        assert not window_with_text.state.selected_text.font.bold

    def test_書式は保存して開き直しても残る(self, window_with_text, tmp_path):
        from manga_layout import load_project

        window_with_text.set_text_align("left")
        window_with_text.toggle_bold()
        window_with_text.step_text_size(1)
        window_with_text.state.save(tmp_path)

        restored = load_project(tmp_path)
        text = [f for f in restored.pages[0].floating if isinstance(f, TextObject)][0]
        assert text.align == "left"
        assert text.font.bold
        assert text.content == "セリフ"
        assert text.attached_balloon_id is not None
        assert restored.load_warnings == []


class TestTextMenu:
    def items(self, window):
        for action in window.menuBar().actions():
            if action.text().startswith("セリフ"):
                return [a for a in action.menu().actions() if not a.isSeparator()]
        raise AssertionError("セリフメニューが見つかりません")

    def test_何も選んでいなくても作れる項目がある(self, window):
        usable = [a for a in self.items(window) if a.isEnabled()]
        assert usable, "セリフメニューが全部グレーになっている"

    def test_追加の項目が先頭にある(self, window):
        first = self.items(window)[0]
        assert first.isEnabled()
        assert first is window._tool_actions[TOOL_TEXT]

    def test_選択中なら書式も使える(self, window_with_text):
        assert all(a.isEnabled() for a in self.items(window_with_text))

    def test_太字の印が状態に追随する(self, window_with_text):
        assert not window_with_text.bold_action.isChecked()
        window_with_text.toggle_bold()
        assert window_with_text.bold_action.isChecked()


class TestDelete:
    def test_削除できる(self, window_with_text):
        window_with_text.delete_selected()
        texts = [f for f in window_with_text.state.page.floating if isinstance(f, TextObject)]
        assert texts == []

    def test_コマを消してもセリフは残る(self, window_with_text):
        state = window_with_text.state
        state.select(state.page.panels[0].id)

        window_with_text.delete_selected()

        texts = [f for f in state.page.floating if isinstance(f, TextObject)]
        assert len(texts) == 1
