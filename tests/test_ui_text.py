"""セリフまわりの検証（画面なし）。

**文字そのものの見た目は確かめられない。** テストは offscreen で動かしており、
この環境には使えるフォントが1つも無い（`QFontDatabase.families()` が空）ため、
描いた画素を数える検証はフォントを要する部分だけ飛ばしてある。
代わりに「操作 → モデルの変更 → 履歴に積む」と、吹き出しへの追随、
保存の往復を押さえる。
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtGui import QFont

from manga_layout import Rect
from manga_layout.layout import text_frame
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


def move_to(view, x: float, y: float) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mouseMoveEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseMove,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def drag(view, x1: float, y1: float, x2: float, y2: float) -> None:
    press(view, x1, y1)
    move_to(view, x2, y2)
    release(view, x2, y2)


def click(view, x: float, y: float) -> None:
    press(view, x, y)
    release(view, x, y)


def click_pair(view, x: float, y: float) -> None:
    """Qt が実際に送る順（押下 → 離す → ダブルクリック → 離す）。

    **ダブルクリックの事象だけを送っては駄目。** 手前の押下で選択が動く
    ことが、セリフの「1回目は選ぶ、2回目で入力へ」の判定そのものになって
    いる（→ `PageView._double_click_text`、`tests/test_pick_cycle.py`）。
    """
    press(view, x, y)
    release(view, x, y)
    double_click(view, x, y)
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

    def test_選んでいないセリフはダブルクリックでも選ぶだけ(self, window_with_text):
        """狙いを外して別のセリフを叩いても、入力へ飛び込まない。

        入力欄は縦書きのセリフでも横書きで開くので、飛び込むとそのセリフが
        その場で横書きに化けて見える。**縦書きの設定は変わっていないのに
        変わったように見える**（本人談 2026-08-07、要件定義 6.5）。
        """
        state = window_with_text.state
        text = only_text(state.page)
        state.select(None)

        click_pair(window_with_text.view, *text.rect.center)

        assert not window_with_text.view.is_editing_text
        assert state.selected_id == text.id
        assert only_text(state.page).direction == text.direction

    def test_選んでいるセリフをダブルクリックすると入力に入る(self, window_with_text):
        """1回目で選び、2回目で入力へ（→ `PageView._double_click_text`）。"""
        state = window_with_text.state
        center = only_text(state.page).rect.center
        state.select(None)

        click_pair(window_with_text.view, *center)
        click_pair(window_with_text.view, *center)

        assert window_with_text.view.is_editing_text

    def test_はじめから選んでいれば1回で入力に入る(self, window_with_text):
        """選んでおいてからのダブルクリックは、今までどおり入力へ。"""
        state = window_with_text.state
        text = only_text(state.page)
        state.select(text.id)

        click_pair(window_with_text.view, *text.rect.center)

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


class TestSelectionFrame:
    """選択枠とつまみは、枠ではなく**字の並び**に沿う（→ `layout.text_frame`）。

    セリフは掴める範囲が枠と別（→ `layout.text_at`）。枠のまま描くと、
    **押しても掴めない場所まで枠が伸びる**——「オレンジの枠が文字より大きすぎて
    どこを掴んでいるのか分からない」という指摘（本人談 2026-08-07）はこの
    食い違いそのもの。描く範囲・つまみ・移動の起点をすべてここへ揃える。
    """

    @pytest.fixture
    def window_with_wide_text(self, window_with_balloon):
        """既定の大きさ（230×422）の枠に3文字だけ。実際の使い方に近い形。"""
        window_with_balloon.state.add_text(
            Rect(300.0, 300.0, 230.0, 422.0), "セリフ"
        )
        return window_with_balloon

    def test_枠より狭い(self, window_with_wide_text):
        state = window_with_wide_text.state
        text = state.selected_text

        bounds = state.selected_bounds
        assert bounds == text_frame(text)
        assert bounds.w < text.rect.w and bounds.h < text.rect.h

    def test_つまみは字の角に出る(self, window_with_wide_text):
        """枠の角にはもう出ない。出すと掴めない場所につまみが残る。"""
        state = window_with_wide_text.state
        view = window_with_wide_text.view
        bounds = state.selected_bounds
        rect = state.selected_text.rect

        assert view._handle_at_point(bounds.x, bounds.y) == "nw"
        assert view._handle_at_point(bounds.right, bounds.bottom) == "se"
        assert view._handle_at_point(rect.x, rect.y) is None

    def test_掴んだだけでは何も起きない(self, window_with_wide_text):
        """つまみを押して動かさずに離した場合（→ `ResizeDrag.commit`）。

        枠を字に合わせるのは大きさを変えたときだけ。掴んだだけで枠が縮むと、
        見た目が変わらないまま履歴に1手積まれる。
        """
        state = window_with_wide_text.state
        before = state.selected_text.rect
        depth = state.history.depth
        corner = state.selected_bounds
        view = window_with_wide_text.view

        press(view, corner.x, corner.y)
        release(view, corner.x, corner.y)

        assert only_text(state.page).rect == before
        assert state.history.depth == depth

    def test_つまみを引くと枠が字にそろう(self, window_with_wide_text):
        """大きさを変えた時点で、枠そのものが字の外接矩形を起点になる。

        起点が字の側にあるので、**掴んでいない側は動かない**。以前は枠が
        起点だったため、右下を引くと左上が 87px 離れた枠の角のままだった。
        """
        state = window_with_wide_text.state
        before = state.selected_bounds

        drag(
            window_with_wide_text.view,
            before.right,
            before.bottom,
            before.right + 30.0,
            before.bottom + 20.0,
        )

        after = only_text(state.page).rect
        assert (after.x, after.y) == pytest.approx((before.x, before.y))
        assert after.w > before.w and after.h > before.h

    def test_引いて動かすと枠も字も同じだけ動く(self, window_with_wide_text):
        """移動の起点も字の外接矩形（→ `MoveDrag.begin`）。"""
        state = window_with_wide_text.state
        before_rect = state.selected_text.rect
        before_frame = state.selected_bounds
        cx, cy = before_frame.center

        drag(window_with_wide_text.view, cx, cy, cx + 40.0, cy + 30.0)

        after = only_text(state.page)
        dx = after.rect.x - before_rect.x
        dy = after.rect.y - before_rect.y
        assert (dx, dy) != (0.0, 0.0)
        assert state.selected_bounds.x == pytest.approx(before_frame.x + dx)
        assert state.selected_bounds.y == pytest.approx(before_frame.y + dy)

    def test_空のセリフは枠のまま(self, window_with_balloon):
        """空のときは点線の枠が唯一の掴み所（→ `layout.text_ink_bands`）。"""
        state = window_with_balloon.state
        rect = Rect(300.0, 300.0, 230.0, 422.0)
        state.add_text(rect, "")

        assert state.selected_bounds == rect


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

    def test_窓を広げてから開く(self, window, monkeypatch):
        """Qt の既定は書体の一覧が数行しか見えず、隅を引けることにも気づけない。

        `QFontDialog.getFont`（静的な呼び方）では大きさを指定できないので、
        自分で組み立てて `resize` してから開いている（→ `_ask_font`）。
        """
        from PySide6.QtWidgets import QDialog, QFontDialog

        大きさ: list = []

        def fake_exec(dialog):
            大きさ.append((dialog.width(), dialog.height()))
            return QDialog.DialogCode.Rejected

        既定 = QFontDialog(QFont(), window).size()
        monkeypatch.setattr(QFontDialog, "exec", fake_exec)

        window.choose_font()

        assert 大きさ[0][0] > 既定.width()
        assert 大きさ[0][1] > 既定.height()

    def test_絞り込みの欄が付いた窓を出す(self, window, monkeypatch):
        """書体は 200 件近く並ぶので、広げただけでは送る手間が残る。

        絞り込みの中身は `tests/test_ui_font_dialog.py` で見ている。
        ここで確かめるのは、**窓を出す1か所がそちらを通っている**こと。
        """
        from PySide6.QtWidgets import QDialog, QFontDialog

        from manga_layout.ui.font_dialog import FontChooser

        出した: list = []

        def fake_exec(dialog):
            出した.append(dialog)
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(QFontDialog, "exec", fake_exec)

        window.choose_font()

        assert isinstance(出した[0], FontChooser)
        assert 出した[0].filter_field.isVisibleTo(出した[0])

    def test_大きさの表示にポイントを併記する(self):
        """px だけだと画面の点の数と取り違える。"""
        assert MainWindow._size_label(25.0) == "25px（約 12pt）"


def _capture_font(monkeypatch, *, accept: bool) -> list:
    """窓を出さずに、渡された QFont を控える。"""
    渡された: list = []

    def fake(self, font):
        渡された.append(font)
        return font, accept

    monkeypatch.setattr(MainWindow, "_ask_font", fake)
    return 渡された


def _choose_font(
    monkeypatch, *, points: float | None = None, pixels: int | None = None, accept=True
) -> None:
    """窓を出さずに、その書式を選んだことにして進める。

    差し替えるのは `MainWindow._ask_font`（窓を組み立てて出す1か所）。
    **`QFontDialog.getFont` は使っていない**——窓の大きさを指定できず、
    書体の一覧が数行しか見えない状態で開いてしまうため（→ `_ask_font`）。
    """

    def fake(self, font):
        chosen = QFont(font)
        if points is not None:
            chosen.setPointSizeF(points)
        if pixels is not None:
            chosen.setPixelSize(pixels)
        return chosen, accept

    monkeypatch.setattr(MainWindow, "_ask_font", fake)


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

    def test_向きも引き継ぐ(self, window_with_text):
        """横書きにしたら、次に作るセリフも横書き（本人の指摘 2026-08-07）。

        既定は縦書きだが、横書きの箇条書きを作っている最中に1つ置くたび
        縦書きへ戻ると、そのつど直すことになる。**書式だけ引き継いで向きが
        戻るのは、使う側からは「勝手に縦にされる」としか映らない。**
        """
        window_with_text.toggle_vertical()  # 既定の縦書き → 横書き
        assert window_with_text.state.selected_text.direction == "horizontal"

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))

        assert window_with_text.state.selected_text.direction == "horizontal"

    def test_縦書きへ戻せば次も縦書き(self, window_with_text):
        window_with_text.toggle_vertical()  # 横書き
        window_with_text.toggle_vertical()  # 縦書きへ戻す

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))

        assert window_with_text.state.selected_text.direction == "vertical"

    def test_向きは既にあるセリフを巻き込まない(self, window_with_text):
        """引き継ぎ先は「これから作るもの」だけ（→ `test_既にあるセリフは巻き込まない`）。"""
        古い = window_with_text.state.selected_text
        古いid, 古い向き = 古い.id, 古い.direction

        window_with_text.state.add_text(Rect(700.0, 600.0, 120.0, 60.0))
        window_with_text.toggle_vertical()

        assert window_with_text.state.page.find(古いid).direction == 古い向き

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
        assert " 太字" in window_with_text._hint()

    def test_向きも表示に出る(self, window_with_text):
        """向きも引き継ぐので、置く前に名乗る（→ `MainWindow._next_font_label`）。"""
        window = window_with_text
        window.state.set_tool(TOOL_TEXT)
        assert window._hint().endswith(" 縦書き")

        window.state.set_tool(TOOL_SELECT)
        window.toggle_vertical()  # 横書きへ
        window.state.set_tool(TOOL_TEXT)
        assert window._hint().endswith(" 横書き")


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
    """置いたばかりのセリフが空のまま残るなら、追加ごと取り消す。

    押し間違いで置いてしまっただけのときに中身の無い枠が残り、**残って
    いること自体に気づけない**（本人の指摘 2026-08-07）。

    1文字も入れずに閉じた場合と、**打ってから Esc で取り消した場合**の
    両方が対象（2026-08-08）。Esc は打った内容を捨てるので、どちらも
    残るのは中身の無い枠で同じ。
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

    def test_打ってから取り消しても作られない(self, window_with_balloon):
        """Esc は打った内容を捨てるので、残るのは中身の無い枠になる。

        以前はここだけ枠が残っていた（2026-08-08 の修正）。空のまま
        閉じた場合と見た目は同じなのに、片方だけ残っていた。
        """
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")
        window_with_balloon.view.finish_text_edit(commit=False)

        assert self.texts(window_with_balloon) == []

    def test_打ってから取り消すと履歴にも残らない(self, window_with_balloon):
        depth = window_with_balloon.state.history.depth
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")
        window_with_balloon.view.finish_text_edit(commit=False)

        assert window_with_balloon.state.history.depth == depth
        assert not window_with_balloon.state.history.can_redo
        assert window_with_balloon.state.selected_id is None

    def test_取り消したことを状態表示で知らせる(self, window_with_balloon):
        """黙って消すと、置いたはずのものが無い理由が分からない。"""
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")
        window_with_balloon.view.finish_text_edit(commit=False)

        assert "作りませんでした" in window_with_balloon.statusBar().currentMessage()

    def test_打ってから確定すれば残る(self, window_with_balloon):
        """取り消しでないなら消さない（→ `test_1文字でも入れれば残る`）。"""
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")
        window_with_balloon.view.finish_text_edit(commit=True)

        assert [t.content for t in self.texts(window_with_balloon)] == ["あいうえお"]

    def test_既にあるセリフは取り消しても消さない(self, window_with_text):
        """F2 で打ち直して Esc は「元の文へ戻る」。セリフ自体は残る。"""
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id

        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("打ち直した文")
        view.finish_text_edit(commit=False)

        assert window_with_text.state.page.find(text_id).content == "セリフ"


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


class Test縦書きの下見:
    """入力中に、確定後の縦書きを枠の中へ出す（2026-09-05）。

    **入力欄は横書きでしか開けない**（Qt に日本語の縦書きの入力欄が無い
    → 要件定義 6.11）。打っている間は横書きの1行しか見えず、列が何本に
    なるか・どこで改行されるか・枠からはみ出すかが確定するまで分からな
    かった。確定後と同じ経路（`render._draw_text_vertical`）へ流して枠の
    中に出し、入力欄のほうを枠の下へ逃がす。

    **組んだ字そのものは確かめられない**（offscreen にフォントが無い）。
    ここで押さえるのは「下見に何が渡るか」と「入力欄が枠に重ならないか」。
    """

    def scene(self, window):
        return window.view._scene

    def test_入力を始めると今の内容が下見へ渡る(self, window_with_text):
        text = window_with_text.state.selected_text
        window_with_text.view.begin_text_edit(text.id)
        assert self.scene(window_with_text).editing_text_content == text.content
        window_with_text.view.finish_text_edit(commit=False)

    def test_打つたびに下見が追いつく(self, window_with_text):
        text_id = window_with_text.state.selected_text.id
        window_with_text.view.begin_text_edit(text_id)

        window_with_text.view._text_editor.setPlainText("あい\nうえお")

        assert self.scene(window_with_text).editing_text_content == "あい\nうえお"
        window_with_text.view.finish_text_edit(commit=False)

    def test_確定すると下見を畳む(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        window_with_text.view.finish_text_edit(commit=True)
        assert self.scene(window_with_text).editing_text_content is None

    def test_取り消しても下見を畳む(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        window_with_text.view.finish_text_edit(commit=False)
        assert self.scene(window_with_text).editing_text_content is None

    def test_縦書きの入力欄は枠の下へ逃げる(self, window_with_text):
        # 枠の中には確定後の姿が出ている。重ねると二重になって両方読めない
        text = window_with_text.state.selected_text
        window_with_text.view.begin_text_edit(text.id)

        editor = window_with_text.view._text_editor
        assert editor.pos().y() > text.rect.y + text.rect.h

        window_with_text.view.finish_text_edit(commit=False)

    def test_逃がした先で枠にくっつけない(self, window_with_text):
        # 空ける幅は書体の大きさから決まる（大きい書体で詰まって見える）
        text = window_with_text.state.selected_text
        window_with_text.view.begin_text_edit(text.id)

        editor = window_with_text.view._text_editor
        空き = editor.pos().y() - (text.rect.y + text.rect.h)
        assert 空き == pytest.approx(editor._mode_label.gap)

        window_with_text.view.finish_text_edit(commit=False)

    def test_横書きの入力欄は枠に重ねたまま(self, window_with_text):
        # 入力中と確定後で字が 1px も動かないのが一番よい（要件定義 6.5）
        window_with_text.toggle_vertical()
        text = window_with_text.state.selected_text
        assert text.direction == "horizontal"

        window_with_text.view.begin_text_edit(text.id)
        editor = window_with_text.view._text_editor
        assert text.rect.y <= editor.pos().y() <= text.rect.y + text.rect.h

        window_with_text.view.finish_text_edit(commit=False)

    def test_横書きでは打っても下見を渡さない(self, window_with_text):
        # 入力欄が枠に重なったまま同じ字を出すので、二重に見えるだけになる
        window_with_text.toggle_vertical()
        text_id = window_with_text.state.selected_text.id
        window_with_text.view.begin_text_edit(text_id)

        window_with_text.view._text_editor.setPlainText("あいうえお")

        assert self.scene(window_with_text).editing_text_content != "あいうえお"
        window_with_text.view.finish_text_edit(commit=False)


class Test下見の描画:
    """下見が実際に画面まで届くか（2026-09-05）。

    **組んだ字は数えられない**（offscreen にフォントが無いので、文字を
    描いても画素が1つも増えない）。数えられるのは線だけなので、ここでは
    **空のときに出る点線枠**を見る。中身のあるときは `test_vertical.py` が
    Qt 抜きで置き場所を押さえており、描く経路は確定後と同じ1本
    （`_draw_text_vertical`）なので、そちらの検証が効く。
    """

    def 描いた画素数(self, state, text, preview) -> int:
        from PySide6.QtGui import QImage, QPainter

        from manga_layout.ui.render import PageRenderer

        image = QImage(600, 600, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        PageRenderer(state)._draw_text(painter, text, preview)
        painter.end()
        return sum(
            1
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        )

    def preview(self, text_id, content):
        from manga_layout.ui.render import DragPreview

        return DragPreview(editing_text_id=text_id, editing_text_content=content)

    def test_空のまま打ち始めても枠が残る(self, window_with_text):
        """入力欄は枠の外へ逃げている。ここに何も描かないと、**どこへ字が
        入るのかが画面から消える**（空のセリフに点線枠を出すのと同じ理由）。
        """
        state = window_with_text.state
        text = dataclasses.replace(state.selected_text, content="")

        画素 = self.描いた画素数(state, text, self.preview(text.id, ""))

        assert 画素 > 0

    def test_横書きの入力中は何も描かない(self, window_with_text):
        # 入力欄が枠に重なったまま同じ字を出すことになり、二重に見える
        state = window_with_text.state
        text = dataclasses.replace(
            state.selected_text, content="", direction="horizontal"
        )

        画素 = self.描いた画素数(state, text, self.preview(text.id, "あいうえお"))

        assert 画素 == 0


class Testテキスト入力モードの札:
    """入力中に出す【テキスト入力モード】の札（2026-09-05）。

    **札そのものが書体の見本を兼ねる。** 空のセリフを打ち始めるときは
    入力欄に1文字も無く、「どの書体の何 px で入るのか」が画面のどこにも
    出ていなかった。状態表示に名前を書き足しても、**名前を読んで大きさは
    分からない**（本人の指摘 2026-09-05）。
    """

    def label(self, window):
        return window.view._text_editor._mode_label

    def test_入力中だけ出る(self, window_with_text):
        assert window_with_text.view._text_editor is None
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        assert self.label(window_with_text).LABEL == "【テキスト入力モード】"
        window_with_text.view.finish_text_edit(commit=False)
        assert window_with_text.view._text_editor is None

    def test_打つ書体と大きさで描く(self, window_with_text):
        state = window_with_text.state
        text = state.selected_text
        state.set_text_font(text.id, family="Meiryo", size_px=48.0)

        window_with_text.view.begin_text_edit(text.id)
        font = self.label(window_with_text)._font
        window_with_text.view.finish_text_edit(commit=False)

        assert font.family() == "Meiryo"
        assert font.pixelSize() == 48

    def test_太字も写す(self, window_with_text):
        text_id = window_with_text.state.selected_text.id
        window_with_text.toggle_bold()

        window_with_text.view.begin_text_edit(text_id)
        font = self.label(window_with_text)._font
        window_with_text.view.finish_text_edit(commit=False)

        assert font.bold()

    def test_表示倍率を無視しない(self, window_with_text):
        """見本である以上、拡大したら札も同じだけ大きくならないと嘘になる。

        `ConfirmHintItem`（確定の目印）とはここが逆で、あちらは画面の道具
        なので倍率を無視する。**同じ入力欄に付く2つの札で扱いが違う**ので、
        取り違えないよう両方を1つのテストで見る。
        """
        from PySide6.QtWidgets import QGraphicsItem

        無視する = QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = window_with_text.view._text_editor

        assert not (editor._mode_label.flags() & 無視する)
        assert editor._confirm.flags() & 無視する

        window_with_text.view.finish_text_edit(commit=False)

    def test_札も確定の目印も入力欄の下に並ぶ(self, window_with_text):
        """上から 入力欄 → 札 → 確定の目印 の順。

        札を入力欄の上に出すと**フキダシの輪郭と重なりやすい**（本人の
        指摘 2026-09-05）。セリフはフキダシの中に置くのが普通なので、
        入力欄の真上は輪郭が通っている確率が高い。
        """
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = window_with_text.view._text_editor

        入力欄の下端 = editor.boundingRect().bottom()
        assert editor._mode_label.pos().y() >= 入力欄の下端
        assert editor._confirm.pos().y() > editor._mode_label.pos().y()

        window_with_text.view.finish_text_edit(commit=False)

    def test_行が増えたら札も付いていく(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = window_with_text.view._text_editor
        前 = editor._mode_label.pos().y()

        editor.setPlainText("あ" + chr(10) + "い" + chr(10) + "う")

        assert editor._mode_label.pos().y() > 前
        assert editor._confirm.pos().y() > editor._mode_label.pos().y()
        window_with_text.view.finish_text_edit(commit=False)

    def test_押しても自分では受け取らない(self, window_with_text):
        # クリックは下の画面へ素通りし、「触ったら確定」の道に乗る
        from PySide6.QtCore import Qt

        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        受け取る = self.label(window_with_text).acceptedMouseButtons()
        window_with_text.view.finish_text_edit(commit=False)

        assert 受け取る == Qt.MouseButton.NoButton


class Test文字の大きさの看板:
    """確定の目印の下に積む、大きさを変えるキーの看板（2026-09-05）。

    **看板と実際の割り当てが食い違うと、読んだとおりに押しても動かない。**
    ここはメニューの `QAction` と突き合わせて、片方だけ直したときに落ちる
    ようにしてある。
    """

    def 看板(self):
        from manga_layout.ui.canvas import SizeKeysHintItem

        return SizeKeysHintItem

    def 割り当て(self, window, 名前: str) -> str:
        for action in window.text_menu.actions:
            if action.text() == 名前:
                return action.shortcut().toString()
        raise AssertionError(f"セリフメニューに「{名前}」が無い")

    def test_確定の目印の下に積む(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        editor = window_with_text.view._text_editor

        # 目印の子。倍率を無視する親の座標系に並ぶので、置き直しが要らない
        assert editor._size_keys.parentItem() is editor._confirm
        assert editor._size_keys.pos().y() >= editor._confirm.total_height()

        window_with_text.view.finish_text_edit(commit=False)

    def test_看板のキーはメニューの割り当てと同じ(self, window_with_text):
        label = self.看板().LABEL
        assert self.割り当て(window_with_text, "大きく") in label
        assert self.割り当て(window_with_text, "小さく") in label

    def test_拡大と縮小を取り違えていない(self, window_with_text):
        """**`Ctrl+.` が大きく、`Ctrl+,` が小さく**（右が増える側）。

        取り違えても看板の文字は正しく見えるので、目視では捕まらない。
        語とキーが交互に同じ順で並んでいることで確かめる。
        """
        label = self.看板().LABEL
        並び = [
            label.index("拡大"),
            label.index(self.割り当て(window_with_text, "大きく")),
            label.index("縮小"),
            label.index(self.割り当て(window_with_text, "小さく")),
        ]
        assert 並び == sorted(並び)

    def test_大きさのキーは入力を確定してから効く(self, window_with_text):
        """**押すと入力から抜ける。** 看板を読んで「打ちながら変えられる」と
        期待すると外れるので、その差をここに残す。

        メニューのショートカットは入力欄より手前で解決されるため、項目の
        実行前に必ず確定する形に揃えてある（`MainWindow.run_action`、
        2026-08-08）。塞がずにいると、打った文字が黙って消えることまで
        起きていた。**打った内容は捨てられない**（`commit=True`）。
        """
        window = window_with_text
        text_id = window.state.selected_text.id
        window.view.begin_text_edit(text_id)
        window.view._text_editor.setPlainText("あいう")
        前 = window.state.selected_text.font.size_px

        window.run_action(lambda: window.step_text_size(1))

        assert not window.view.is_editing_text
        assert window.state.page.find(text_id).content == "あいう"
        assert window.state.page.find(text_id).font.size_px > 前

    def test_押しても自分では受け取らない(self, window_with_text):
        from PySide6.QtCore import Qt

        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        受け取る = window_with_text.view._text_editor._size_keys.acceptedMouseButtons()
        window_with_text.view.finish_text_edit(commit=False)

        assert 受け取る == Qt.MouseButton.NoButton


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
        # 仕上がりは枠の中に出す（→ `Test縦書きの下見`）
        text_id = window_with_text.state.selected_text.id
        assert window_with_text.view.begin_text_edit(text_id)
        window_with_text.view.finish_text_edit(commit=True)
        assert only_text(window_with_text.state.page).direction == "vertical"

    def test_縦書きの入力では仕上がりの在りかを指す(self, window_with_text):
        # 黙っていると「縦書きなのに横書きで入る」と受け取られる。
        # 下見を入れてからは、断りではなく**どこを見ればよいか**を出す
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


class Test入力中に項目が押されたとき:
    """メニューの項目は、実行の前に入力を確定する（→ `MainWindow.run_action`）。

    「入力中は1つも横取りしない」の防壁はキーが画面へ届いた場合にしか
    効かず、**メニューのショートカットはそれより手前で解決される**。
    塞ぐ前は、入力中の `F7` や `PgDown` がそのまま発火していた
    （2026-08-08 に発見）。

    ここは**項目を押す道**（`trigger()`）だけを見る。キーを押す道の前提は
    `Test入力中のキーがどちらへ行くか` にある。
    """

    def 置く(self, window) -> None:
        window.state.set_tool(TOOL_TEXT)
        click(window.view, *BALLOON.center)

    def texts(self, window) -> list:
        return [f for f in window.state.page.floating if isinstance(f, TextObject)]

    def item(self, window, menu_name: str, label: str):
        """メニューバーから項目を1つ取る。

        **押す道を通したい**ので、スロット（`window.add_page` など）を
        直に呼んではいけない。確定を挟むのは項目のほう（→ `run_action`）
        """
        for top in window.menuBar().actions():
            if top.text().startswith(menu_name):
                for action in top.menu().actions():
                    if action.text() == label:
                        return action
        raise AssertionError(f"項目が見つかりません: {menu_name} / {label}")

    def test_書式の項目で打った内容が確定する(self, window_with_balloon):
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")

        window_with_balloon.text_menu.vertical_action.trigger()

        assert window_with_balloon.view._text_editor is None, "入力欄が残っている"
        assert [t.content for t in self.texts(window_with_balloon)] == ["あいうえお"]

    def test_書式の項目のあとに空の枠が残らない(self, window_with_balloon):
        """打ってから項目 → Esc で、中身の無い枠が残っていた。

        項目が履歴に1手挟むと、置いたばかりのセリフを追加ごと取り消す
        処理（→ `Test空のまま閉じたセリフ`）の照合が外れる。先に確定すれば
        入力から抜けているので、Esc はセリフの取り消しに回らない。
        """
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")

        window_with_balloon.text_menu.vertical_action.trigger()
        window_with_balloon.view.finish_text_edit(commit=False)  # Esc 相当

        assert [t.content for t in self.texts(window_with_balloon)] == ["あいうえお"]

    def test_空のまま項目を押せば追加ごと取り消す(self, window_with_balloon):
        """確定を挟むので、空のセリフの扱いは閉じたときと同じになる。"""
        self.置く(window_with_balloon)

        window_with_balloon.text_menu.vertical_action.trigger()

        assert self.texts(window_with_balloon) == []

    def test_ページを移す項目で打った内容が消えない(self, window_with_text):
        """入力欄を開いたままページだけ変わると、書き戻し先を見失う。

        確定は今のページからセリフを探すので、**打った文字が黙って捨てられ**、
        元のページには中身の無い枠が残っていた（この修正の主目的）。
        """
        view = window_with_text.view
        text_id = window_with_text.state.selected_text.id
        view.begin_text_edit(text_id)
        view._text_editor.setPlainText("消えては困る文")

        self.item(window_with_text, "ページ", "ページを追加").trigger()

        assert window_with_text.state.page_count == 2
        assert view._text_editor is None, "入力欄が残っている"
        first = window_with_text.state.history.project.pages[0]
        assert first.find(text_id).content == "消えては困る文"

    def test_道具の項目でも確定する(self, window_with_balloon):
        """道具の切り替えも同じ道を通す（→ `_build_tool_actions`）。"""
        self.置く(window_with_balloon)
        window_with_balloon.view._text_editor.setPlainText("あいうえお")

        window_with_balloon._tool_actions[TOOL_SELECT].trigger()

        assert window_with_balloon.view._text_editor is None
        assert [t.content for t in self.texts(window_with_balloon)] == ["あいうえお"]

    def test_引数を取る項目も壊さない(self, window_with_text):
        """`triggered` が渡す checked を、包んだあとも落として呼ぶ。

        整列の項目は `lambda _=False, a=align:` の形で、包み方を間違えると
        既定値のほうが使われて別の値が入る
        """
        self.item(window_with_text, "セリフ", "左寄せ").trigger()

        assert window_with_text.state.selected_text.align == "left"

    def test_書き戻せなければ知らせる(self, window_with_text):
        """項目を通らずにページが変わった場合の受け皿（→ `finish_text_edit`）。

        今はここへ来る道が無いが、**黙って捨てるのが元の壊れ方**だった。
        """
        view = window_with_text.view
        view.begin_text_edit(window_with_text.state.selected_text.id)
        view._text_editor.setPlainText("打った文")

        window_with_text.state.add_page()  # 確定を挟まずにページだけ移す
        view.finish_text_edit(commit=True)

        assert "残せませんでした" in window_with_text.statusBar().currentMessage()


def menu_actions(window):
    """メニューバーに並んでいる項目を、入れ子も含めて全部たどる。"""

    def walk(menu):
        for action in menu.actions():
            if action.isSeparator():
                continue
            sub = action.menu()
            if sub is not None:
                yield from walk(sub)
            else:
                yield action

    for top in window.menuBar().actions():
        if top.menu() is not None:
            yield from walk(top.menu())


class Test入力中のキーがどちらへ行くか:
    """`run_action` が要る理由そのもの（→ `PySide6の落とし穴.md`「入力中の…」）。

    上の `Test入力中に項目が押されたとき` は「項目が押されたあと」を見ている。
    ここで押さえるのは、その手前にある**前提**——入力中に押したキーが
    入力欄に譲られるのか、ショートカットへ抜けるのか。

    Qt はキーを押されると、まず焦点のある部品へ `ShortcutOverride`
    （「このキーをショートカットとして処理してよいか」の問い合わせ）を
    投げ、受理されなければショートカットとして処理する。**この問い合わせは
    自分で作って送れる**ので、窓が活性にならない offscreen でも答えが取れる。

    前提が動けば防ぎ方の要否も動く。**ここが落ちたら、テストを直す前に
    落とし穴の文書を読み直すこと。**
    """

    def 譲られるか(self, window, key, mods, text: str) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QApplication

        assert window.view.is_editing_text, "入力欄が開いていない"
        event = QKeyEvent(QEvent.Type.ShortcutOverride, key, mods, text)
        event.setAccepted(False)
        QApplication.instance().sendEvent(window.view, event)
        return event.isAccepted()

    @pytest.fixture
    def 入力中(self, window_with_text):
        window_with_text.view.begin_text_edit(window_with_text.state.selected_text.id)
        return window_with_text

    def test_文字として打てるキーは入力欄へ譲られる(self, 入力中):
        """道具の1文字キー（`V`『選択』・`P`『コマ追加』）が無事なのは偶然。"""
        from PySide6.QtCore import Qt

        なし = Qt.KeyboardModifier.NoModifier
        assert self.譲られるか(入力中, Qt.Key.Key_V, なし, "v")
        assert self.譲られるか(入力中, Qt.Key.Key_P, なし, "p")

    def test_標準の編集キーも入力欄へ譲られる(self, 入力中):
        from PySide6.QtCore import Qt

        ctrl = Qt.KeyboardModifier.ControlModifier
        assert self.譲られるか(入力中, Qt.Key.Key_Z, ctrl, "\x1a")
        assert self.譲られるか(入力中, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier, "")

    def test_ファンクションキーはショートカットへ抜ける(self, 入力中):
        from PySide6.QtCore import Qt

        assert not self.譲られるか(入力中, Qt.Key.Key_F7, Qt.KeyboardModifier.NoModifier, "")

    def test_移動キーはショートカットへ抜ける(self, 入力中):
        """これがいちばん重い。抜けると入力欄を開いたままページが変わる。"""
        from PySide6.QtCore import Qt

        なし = Qt.KeyboardModifier.NoModifier
        assert not self.譲られるか(入力中, Qt.Key.Key_PageDown, なし, "")

    def test_修飾キー付きも抜けるものがある(self, 入力中):
        """`Ctrl+Z` は譲られるのに `Ctrl+B`・`Ctrl+S` は抜ける。

        **修飾キーが付いているかどうかでは決まらない。** 入力欄が受理するのは
        文字として打てるキーと標準の編集キーだけで、それ以外は抜ける。
        """
        from PySide6.QtCore import Qt

        ctrl = Qt.KeyboardModifier.ControlModifier
        assert not self.譲られるか(入力中, Qt.Key.Key_B, ctrl, "\x02")
        assert not self.譲られるか(入力中, Qt.Key.Key_S, ctrl, "\x13")


class Testキーを持つ項目の作られ方:
    """**塞ぐ場所が1つで足りるのは、項目の作り方が決まっているから。**

    `run_action` を繋いでいるのは2箇所だけ——`MainWindow._act` と、道具の
    `_build_tool_actions`（同じ項目を2つのメニューに出すため手で組んでいる）。
    項目を1つでも `QAction(...)` から作って `triggered` に直に繋ぐと、
    **その項目だけ入力中に素通りする**。上の `Test入力中に項目が押されたとき`
    は実在の項目を名指しで見ているので、あとから足された項目は素通りしても
    誰も気づかない。

    ここは**数え上げの側**から見張る。項目を押す道を通さないので、
    保存や終了の項目まで実行してしまう心配が無い（＝この検証で窓が開いて
    止まることはない）。

    3つ目の作り方が現れたらここが落ちる。**そのときは除外を足す前に、
    その項目が `run_action` を通っているかを確かめること。**
    """

    def test_キーを持つ項目は決まった作り方だけで作られる(self, qapp, monkeypatch):
        素の_act = MainWindow._act
        作られた = []

        def 記録しながら作る(self, *args, **kwargs):
            action = 素の_act(self, *args, **kwargs)
            作られた.append(action)
            return action

        monkeypatch.setattr(MainWindow, "_act", 記録しながら作る)
        win = MainWindow(EditorState())
        try:
            通った = {id(action) for action in 作られた}
            通った |= {id(action) for action in win._tool_actions.values()}
            素通り = [
                action.text()
                for action in menu_actions(win)
                if not action.shortcut().isEmpty() and id(action) not in 通った
            ]
            assert 素通り == [], "この項目は入力中に素通りする（→ `run_action`）"
        finally:
            win.state.history.mark_saved()
            win.close()


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
