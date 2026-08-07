"""セリフまわりの検証（画面なし）。

**文字そのものの見た目は確かめられない。** テストは offscreen で動かしており、
この環境には使えるフォントが1つも無い（`QFontDatabase.families()` が空）ため、
描いた画素を数える検証はフォントを要する部分だけ飛ばしてある。
代わりに「操作 → モデルの変更 → 履歴に積む」と、吹き出しへの追随、
保存の往復を押さえる。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QFont

from manga_layout import Rect
from manga_layout.model import PT_TO_PX, TextObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT, TOOL_TEXT

# 座標は px（要件定義 3章）。既定のセリフ 201×106 が中に収まる大きさにしてある
PANEL = Rect(120.0, 120.0, 840.0, 660.0)
BALLOON = Rect(240.0, 240.0, 360.0, 240.0)


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
    window_with_balloon.state.add_text(Rect(300.0, 300.0, 240.0, 120.0), "セリフ")
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
        window_with_balloon.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))
        text = window_with_balloon.state.selected_text
        assert text.attached_balloon_id is None
        assert text.attached_panel_id == window_with_balloon.state.page.panels[0].id

    def test_どこにも重ならなければ紐づかない(self, window_with_balloon):
        window_with_balloon.state.add_text(Rect(1020.0, 1500.0, 120.0, 60.0))
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
        before = window_with_text.state.selected_text.font.size_px
        window_with_text.step_text_size(1)
        assert window_with_text.state.selected_text.font.size_px > before
        window_with_text.step_text_size(-1)
        assert window_with_text.state.selected_text.font.size_px == pytest.approx(before)

    def test_1段階は2ポイント(self, window_with_text):
        """px ではなくポイントで決める。表示も窓もポイントで喋る。"""
        before = window_with_text.state.selected_text.font.size_px
        window_with_text.step_text_size(1)
        動いた = window_with_text.state.selected_text.font.size_px - before

        assert 動いた / PT_TO_PX == pytest.approx(2.0, abs=0.01)

    def test_2回押すと表示が4ポイント動く(self, window_with_text):
        """半端な幅だと、押しても表示が動かない回が出る。"""
        text = window_with_text.state.selected_text
        before = round(text.font.size_px / PT_TO_PX)
        window_with_text.step_text_size(1)
        window_with_text.step_text_size(1)
        after = round(window_with_text.state.selected_text.font.size_px / PT_TO_PX)

        assert after - before == 4

    def test_小さくしすぎない(self, window_with_text):
        for _ in range(50):
            window_with_text.step_text_size(-1)
        assert window_with_text.state.selected_text.font.size_px >= 9.0

    def test_大きくしすぎない(self, window_with_text):
        for _ in range(200):
            window_with_text.step_text_size(1)
        assert window_with_text.state.selected_text.font.size_px <= 180.0

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
        # 既定（縦書き）から外した値が残るかを見たいので、横書きへ倒す
        window_with_text.toggle_vertical()
        window_with_text.state.save(tmp_path)

        restored = load_project(tmp_path)
        text = [f for f in restored.pages[0].floating if isinstance(f, TextObject)][0]
        assert text.align == "left"
        assert text.font.bold
        assert text.direction == "horizontal"
        assert text.content == "セリフ"
        assert text.attached_balloon_id is not None
        assert restored.load_warnings == []


class Testフォント設定の窓:
    """**今の書式を持っていき、選んだ大きさを持ち帰る。**

    以前は種類と太さだけを持ち帰り、大きさは捨てていた。窓には Qt の
    既定の 12pt が出るので、**選択中のセリフとは無関係な数字**を見ながら
    操作することになり、そこから大きさを変えることもできなかった。
    """

    def test_今の大きさをポイントで持っていく(self, window_with_text, monkeypatch):
        window_with_text.state.set_text_font(
            window_with_text.state.selected_text.id, size_px=25.0
        )
        渡された = _capture_font(monkeypatch, accept=False)

        window_with_text.choose_font()

        # 25px は 150dpi 換算で 12pt。画面の解像度で換算してはいけない
        assert 渡された[0].pointSizeF() == pytest.approx(12.0)

    def test_太字も持っていく(self, window_with_text, monkeypatch):
        window_with_text.toggle_bold()
        渡された = _capture_font(monkeypatch, accept=False)

        window_with_text.choose_font()

        assert 渡された[0].bold()

    def test_選んだ大きさが効く(self, window_with_text, monkeypatch):
        _choose_font(monkeypatch, points=24.0)

        window_with_text.choose_font()

        # 24pt は 150dpi 換算で 50px
        assert window_with_text.state.selected_text.font.size_px == pytest.approx(50.0)

    def test_取り消せば何も変わらない(self, window_with_text, monkeypatch):
        before = window_with_text.state.selected_text.font
        _choose_font(monkeypatch, points=24.0, accept=False)

        window_with_text.choose_font()

        assert window_with_text.state.selected_text.font == before

    def test_大きさが取れなければ元の値を保つ(self, window_with_text, monkeypatch):
        """`pointSizeF()` は px 指定の QFont では -1 を返す。

        そのまま保存すると負の大きさになり、**文字が描かれなくなる**。
        """
        before = window_with_text.state.selected_text.font.size_px
        _choose_font(monkeypatch, pixels=18)

        window_with_text.choose_font()

        assert window_with_text.state.selected_text.font.size_px == before

    def test_大きさの表示にポイントを併記する(self):
        """px だけだと画面の点の数と取り違える。"""
        assert MainWindow._size_label(25.0) == "25px（約 12pt）"


def _capture_font(monkeypatch, *, accept: bool) -> list:
    """窓を出さずに、渡された QFont を控える。"""
    渡された: list = []

    def fake(font, *args, **kwargs):
        渡された.append(font)
        return font, accept

    monkeypatch.setattr("manga_layout.ui.window.QFontDialog.getFont", fake)
    return 渡された


def _choose_font(
    monkeypatch, *, points: float | None = None, pixels: int | None = None, accept=True
) -> None:
    """窓を出さずに、その書式を選んだことにして進める。"""

    def fake(font, *args, **kwargs):
        chosen = QFont(font)
        if points is not None:
            chosen.setPointSizeF(points)
        if pixels is not None:
            chosen.setPixelSize(pixels)
        return chosen, accept

    monkeypatch.setattr("manga_layout.ui.window.QFontDialog.getFont", fake)


class Test次のセリフの書式:
    """指定した書式は、次に作るセリフへ引き継ぐ（本人の指摘 2026-08-07）。

    以前は選択中の1つにしか効かず、次に置いたセリフは毎回既定へ戻っていた。
    「選んだのに反映されない」と見えるのがいちばんの躓きだった。
    """

    def test_選んだ書式が次のセリフに乗る(self, window_with_text, monkeypatch):
        _choose_font(monkeypatch, points=24.0)
        window_with_text.choose_font()

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))

        # 24pt は 150dpi 換算で 50px
        assert window_with_text.state.selected_text.font.size_px == pytest.approx(50.0)

    def test_大きさと太字も引き継ぐ(self, window_with_text):
        """引き継ぎは書式を指定する操作すべてに掛かる（→ `set_text_font`）。"""
        window_with_text.step_text_size(1)
        window_with_text.toggle_bold()
        期待 = window_with_text.state.selected_text.font

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))

        assert window_with_text.state.selected_text.font == 期待

    def test_取り消した書式は引き継がない(self, window_with_text, monkeypatch):
        before = window_with_text.state.next_text_font
        _choose_font(monkeypatch, points=24.0, accept=False)

        window_with_text.choose_font()

        assert window_with_text.state.next_text_font == before

    def test_既にあるセリフは巻き込まない(self, window_with_text):
        """引き継ぎ先は「これから作るもの」だけ。前に置いたものは動かさない。"""
        古い = window_with_text.state.selected_text
        古いid, 古い書式 = 古い.id, 古い.font

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))
        window_with_text.step_text_size(1)

        assert window_with_text.state.page.find(古いid).font == 古い書式

    def test_道具を持つと状態表示に出る(self, window):
        """置く前に何の書式で置かれるかが分かる。置いてから直すのは手数が同じ。"""
        window.state.set_tool(TOOL_TEXT)
        assert window._hint().startswith("セリフを追加: ")
        assert window.state.next_text_font.family in window._hint()

    def test_何も選んでいなくても出る(self, window):
        window.state.select(None)
        assert "次のセリフ: " in window._hint()

    def test_太字は表示にも出る(self, window_with_text):
        window_with_text.toggle_bold()
        window_with_text.state.set_tool(TOOL_TEXT)
        assert window_with_text._hint().endswith(" 太字")


class Test選択なしで書式を決める:
    """セリフを選んでいなくてもフォントの窓を開ける（本人の指摘 2026-08-07）。

    以前はここで断っていたため、**次の書式を決めるためだけに要らない
    セリフを1つ置く**必要があった。
    """

    def test_選んでいなくても窓が開く(self, window, monkeypatch):
        window.state.select(None)
        渡された = _capture_font(monkeypatch, accept=False)

        window.choose_font()

        assert len(渡された) == 1

    def test_今の次の書式を持っていく(self, window, monkeypatch):
        """窓に出る値は「次に置かれる書式」。無関係な既定を見せない。"""
        window.state.select(None)
        window.state.set_next_text_font(size_px=25.0, bold=True)
        渡された = _capture_font(monkeypatch, accept=False)

        window.choose_font()

        # 25px は 150dpi 換算で 12pt
        assert 渡された[0].pointSizeF() == pytest.approx(12.0)
        assert 渡された[0].bold()

    def test_選んだ書式が次のセリフに乗る(self, window_with_balloon, monkeypatch):
        window_with_balloon.state.select(None)
        _choose_font(monkeypatch, points=24.0)

        window_with_balloon.choose_font()
        window_with_balloon.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))

        # 24pt は 150dpi 換算で 50px
        assert window_with_balloon.state.selected_text.font.size_px == pytest.approx(50.0)

    def test_今あるセリフは変わらない(self, window_with_text, monkeypatch):
        """決めるのは「次に作るもの」だけ。選んでいないものへ及ばない。"""
        text_id = window_with_text.state.selected_text.id
        before = window_with_text.state.page.find(text_id).font
        window_with_text.state.select(None)
        _choose_font(monkeypatch, points=24.0)

        window_with_text.choose_font()

        assert window_with_text.state.page.find(text_id).font == before

    def test_履歴に積まない(self, window_with_balloon, monkeypatch):
        """作品を1文字も変えないので、決めただけで未保存にしない。"""
        depth = window_with_balloon.state.history.depth
        window_with_balloon.state.select(None)
        _choose_font(monkeypatch, points=24.0)

        window_with_balloon.choose_font()

        assert window_with_balloon.state.history.depth == depth

    def test_状態表示がすぐ変わる(self, window, monkeypatch):
        """`changed` が飛ばないので、出し直しを忘れると古い値が残る。"""
        window.state.select(None)
        _choose_font(monkeypatch, points=24.0)

        window.choose_font()

        assert "50px（約 24pt）" in window.hint_label.text()

    def test_取り消せば何も変わらない(self, window, monkeypatch):
        window.state.select(None)
        before = window.state.next_text_font
        _choose_font(monkeypatch, points=24.0, accept=False)

        window.choose_font()

        assert window.state.next_text_font == before

    def test_メニューは選んでいなくても押せる(self, window):
        """グレーだと、そこから決められること自体に気づけない。"""
        window.state.select(None)
        window._refresh()

        assert _text_menu(window).font_action.isEnabled()

    def test_他のセリフ操作はグレーのまま(self, window):
        """選んでいる1つへの操作は、選ぶまで押せない（従来どおり）。"""
        window.state.select(None)
        window._refresh()

        assert not _text_menu(window).bold_action.isEnabled()


def _text_menu(window):
    """セリフのメニュー。"""
    from manga_layout.ui.menus import TextMenu

    for menu in window._menus:
        if isinstance(menu, TextMenu):
            return menu
    raise AssertionError("セリフのメニューが見つからない")


class Test空のまま閉じたセリフ:
    """置いたばかりのセリフを1文字も入れずに閉じたら、追加ごと取り消す。

    押し間違いで置いてしまっただけのときに中身の無い枠が残り、**残って
    いること自体に気づけない**（本人の指摘 2026-08-07）。
    """

    def 置く(self, window) -> None:
        window.state.set_tool(TOOL_TEXT)
        click(window.view, *BALLOON.center)

    def texts(self, window) -> list:
        return [f for f in window.state.page.floating if isinstance(f, TextObject)]

    def test_空のまま確定すると作られない(self, window_with_balloon):
        self.置く(window_with_balloon)
        window_with_balloon.view.finish_text_edit(commit=True)

        assert self.texts(window_with_balloon) == []

    def test_取り消しで抜けても作られない(self, window_with_balloon):
        """Esc でも同じ。片方だけ残すと「Esc だと残る」の覚え直しが要る。"""
        self.置く(window_with_balloon)
        window_with_balloon.view.finish_text_edit(commit=False)

        assert self.texts(window_with_balloon) == []

    def test_空白だけでも作られない(self, window_with_balloon):
        """描いても何も出ないので、枠だけが残るのと区別が付かない。"""
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText(" 　\n ")
        window_with_balloon.view.finish_text_edit(commit=True)

        assert self.texts(window_with_balloon) == []

    def test_履歴にも残らない(self, window_with_balloon):
        """置いた覚えの無いものを Undo で呼び戻せても仕方がない。"""
        depth = window_with_balloon.state.history.depth
        self.置く(window_with_balloon)
        window_with_balloon.view.finish_text_edit(commit=True)

        assert window_with_balloon.state.history.depth == depth
        assert not window_with_balloon.state.history.can_redo

    def test_選択も外れる(self, window_with_balloon):
        self.置く(window_with_balloon)
        window_with_balloon.view.finish_text_edit(commit=True)

        assert window_with_balloon.state.selected_id is None

    def test_1文字でも入れれば残る(self, window_with_balloon):
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あ")
        window_with_balloon.view.finish_text_edit(commit=True)

        assert [t.content for t in self.texts(window_with_balloon)] == ["あ"]
        assert window_with_balloon.state.history.undo_label == "セリフの入力"

    def test_既にあるセリフは空にしても消さない(self, window_with_text):
        """一度消して打ち直す操作を塞がない。取り消しの対象は置いた直後だけ。"""
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("")
        view.finish_text_edit(commit=True)

        assert window_with_text.state.page.find(text_id).content == ""


class TestConfirmHint:
    """入力欄の外に出す「確定」の目印。

    Enter は改行なので、押しても入力から抜けられない。それが分からないと
    「閉じる手段が無い」と感じてしまうため、押し方をその場に出している。
    """

    def editor(self, window):
        return window.view._text_editor

    def test_入力中だけ出る(self, window_with_text):
        assert self.editor(window_with_text) is None
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        assert self.editor(window_with_text)._confirm is not None
        window_with_text.view.finish_text_edit(commit=False)
        assert self.editor(window_with_text) is None

    def test_入力欄の外に出る(self, window_with_text):
        # 中に重ねると文字が読めなくなる。
        # 目印は入力欄の子なので、比べる前に親（入力欄）の座標系へ写す
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = self.editor(window_with_text)
        confirm = editor._confirm
        top = confirm.mapRectToParent(confirm.boundingRect()).top()
        assert top >= editor.boundingRect().bottom()
        window_with_text.view.finish_text_edit(commit=False)

    def test_マウスの操作を自分で受け取らない(self, window_with_text):
        """クリックは下の画面へ素通りさせる。

        自分で受け取ると、確定処理の途中で自分がシーンから外されることに
        なって危うい。素通りさせれば「画面を触ったら確定」という既にある
        道に乗る（→ `test_押すと確定する`）。
        """
        from PySide6.QtCore import Qt

        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        confirm = self.editor(window_with_text)._confirm
        assert confirm.acceptedMouseButtons() == Qt.MouseButton.NoButton
        window_with_text.view.finish_text_edit(commit=False)

    def test_押すと確定する(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = self.editor(window_with_text)
        editor.setPlainText("打ち込んだ内容")
        spot = editor._confirm.mapToScene(editor._confirm.boundingRect().center())

        click(window_with_text.view, spot.x(), spot.y())

        assert window_with_text.view._text_editor is None, "入力から抜けていない"
        assert only_text(window_with_text.state.page).content == "打ち込んだ内容"

    def test_表示倍率を変えても大きさが変わらない(self, window_with_text):
        """作品の一部ではなく画面の道具なので、拡大しても太らせない。"""
        from PySide6.QtWidgets import QGraphicsItem

        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        confirm = self.editor(window_with_text)._confirm
        assert confirm.flags() & QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        window_with_text.view.finish_text_edit(commit=False)


class TestDirection:
    """縦書きの切り替え。

    **組んだ結果の見た目はここでは確かめられない**（offscreen にフォントが
    無い）。置き場所の計算は `test_vertical.py` が Qt 抜きで押さえている。
    ここは「操作 → モデルの変更 → 履歴・保存」だけを見る。
    """

    def test_既定は縦書き(self, window_with_text):
        # マンガのセリフは縦書きが普通なので、横書きのほうを選ぶ形にした
        assert window_with_text.state.selected_text.direction == "vertical"

    def test_切り替えられる(self, window_with_text):
        window_with_text.toggle_vertical()
        assert window_with_text.state.selected_text.direction == "horizontal"
        window_with_text.toggle_vertical()
        assert window_with_text.state.selected_text.direction == "vertical"

    def test_履歴に積まれる(self, window_with_text):
        depth = window_with_text.state.history.depth
        window_with_text.toggle_vertical()
        assert window_with_text.state.history.depth == depth + 1

    def test_元に戻せる(self, window_with_text):
        window_with_text.toggle_vertical()
        window_with_text.state.undo()
        assert only_text(window_with_text.state.page).direction == "vertical"

    def test_整列は持ち替えない(self, window_with_text):
        # 向きを往復したときに、どちらの値を使うのか決められなくなるのを避ける
        window_with_text.set_text_align("left")
        window_with_text.toggle_vertical()
        assert window_with_text.state.selected_text.align == "left"

    def test_何も選んでいなければ何も起きない(self, window):
        depth = window.state.history.depth
        window.toggle_vertical()
        assert window.state.history.depth == depth

    def test_縦書きの印が状態に追随する(self, window_with_text):
        # 既定が縦書きなので、置いた直後から印が付いている
        assert window_with_text.text_menu.vertical_action.isChecked()
        window_with_text.toggle_vertical()
        assert not window_with_text.text_menu.vertical_action.isChecked()

    def test_状態表示に向きが出る(self, window_with_text):
        assert "縦書き" in window_with_text._hint()
        window_with_text.toggle_vertical()
        assert "横書き" in window_with_text._hint()

    def test_縦書きでもその場編集に入れる(self, window_with_text):
        # **入力欄は横書きのまま出る。** Qt に縦書きの入力欄が無いため。
        # 見た目の食い違いは案内で断る方針にした（下のテスト）
        text_id = window_with_text.state.selected_text.id
        assert window_with_text.view.begin_text_edit(text_id)
        window_with_text.view.finish_text_edit(commit=True)
        assert only_text(window_with_text.state.page).direction == "vertical"

    def test_縦書きの入力では確定後どうなるかを断る(self, window_with_text):
        # 黙っていると「縦書きなのに横書きで入る」と受け取られる
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        message = window_with_text.statusBar().currentMessage()
        window_with_text.view.finish_text_edit(commit=False)
        assert "縦書き" in message

    def test_縦書きの入力でも操作キーの案内を落とさない(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        message = window_with_text.statusBar().currentMessage()
        window_with_text.view.finish_text_edit(commit=False)
        for key in ("Enter", "Ctrl+Enter", "Esc"):
            assert key in message

    def test_入力の案内を長くしすぎない(self):
        """状態表示に入り切る長さを保つ。

        案内に使える幅は約 560px しかない（右の常設表示が 696px を占める）。
        一度これを超えて **`Ctrl+Enter で確定、Esc で取り消し` が切れて
        消えた**（2026-08-03）。

        幅そのものは offscreen では測れない（フォントが無い）ので、文字数で
        代用している。**代用でしかないことは上限の定義側に書いてある。**
        """
        from manga_layout.ui.canvas import (
            TEXT_EDIT_HINT,
            TEXT_EDIT_HINT_MAX_CHARS,
            TEXT_EDIT_HINT_VERTICAL,
        )

        for hint in (TEXT_EDIT_HINT, TEXT_EDIT_HINT_VERTICAL):
            assert len(hint) <= TEXT_EDIT_HINT_MAX_CHARS, hint

    def test_横書きの入力では余計な断りを出さない(self, window_with_text):
        window_with_text.toggle_vertical()  # 横書きへ倒す
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        message = window_with_text.statusBar().currentMessage()
        window_with_text.view.finish_text_edit(commit=False)
        assert "縦書き" not in message

    def test_縦書きでは整列の呼び名が変わる(self, window_with_text):
        # align は横書き用の項目を読み替えて使っている（→ manga_layout.vertical）
        window_with_text.set_text_align("left")
        assert "上寄せ" in window_with_text._hint()
        window_with_text.toggle_vertical()
        assert "左寄せ" in window_with_text._hint()


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
        assert not window_with_text.text_menu.bold_action.isChecked()
        window_with_text.toggle_bold()
        assert window_with_text.text_menu.bold_action.isChecked()


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
