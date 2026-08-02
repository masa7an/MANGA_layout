"""吹き出しの操作まわりの検証（画面なし）。

形そのものは tests/test_balloon_shape.py で押さえている。ここでは
「操作 → モデルの変更 → 履歴に積む」がつながっているか、
コマ・画像・吹き出しの選択が取り合いにならないかを確かめる。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import BalloonObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import (
    TOOL_BALLOON,
    TOOL_BALLOON_JAGGED,
    TOOL_PANEL,
    TOOL_SELECT,
)

PANEL = Rect(20.0, 20.0, 120.0, 90.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_panel(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(None)
    return window


@pytest.fixture
def window_with_balloon(window_with_panel):
    """コマの中に吹き出しを1つ置いた状態。吹き出しが選ばれている。"""
    window_with_panel.state.add_balloon(Rect(30.0, 30.0, 40.0, 26.0))
    return window_with_panel


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


def drag(view, x1: float, y1: float, x2: float, y2: float) -> None:
    press(view, x1, y1)
    move_to(view, x2, y2)
    release(view, x2, y2)


def click(view, x: float, y: float) -> None:
    press(view, x, y)
    release(view, x, y)


def balloon_menu_items(window):
    """「吹き出し」メニューの項目（区切り線を除く）。"""
    for action in window.menuBar().actions():
        if action.text().startswith("吹き出し"):
            return [a for a in action.menu().actions() if not a.isSeparator()]
    raise AssertionError("吹き出しメニューが見つかりません")


class TestBalloonMenu:
    """メニューから何もできない状態を作らないこと。

    一時期、選択中の吹き出しに対する操作しか置いていなかったため、
    1つも作っていない間はメニュー全体がグレーになり、
    どこから作るのか分からなくなっていた。
    """

    def test_何も選んでいなくても作れる項目がある(self, window):
        usable = [a for a in balloon_menu_items(window) if a.isEnabled()]
        assert usable, "吹き出しメニューが全部グレーになっている"

    def test_追加の項目が先頭にある(self, window):
        items = balloon_menu_items(window)
        assert items[0].isEnabled() and items[1].isEnabled()
        assert "追加" in items[0].text()
        assert "追加" in items[1].text()

    def test_追加の項目から道具に切り替わる(self, window):
        items = balloon_menu_items(window)
        items[0].trigger()
        assert window.state.tool == TOOL_BALLOON
        items[1].trigger()
        assert window.state.tool == TOOL_BALLOON_JAGGED

    def test_道具バーと同じ項目を指す(self, window):
        """別々の項目にすると、選ばれている印が片方にしか付かない。"""
        items = balloon_menu_items(window)
        assert items[0] is window._tool_actions[TOOL_BALLOON]
        assert items[1] is window._tool_actions[TOOL_BALLOON_JAGGED]

    def test_選択中だけ使える項目もある(self, window_with_balloon):
        items = balloon_menu_items(window_with_balloon)
        assert all(a.isEnabled() for a in items)

    def test_選択を外すと編集の項目は戻る(self, window_with_balloon):
        window_with_balloon.state.select(None)
        labels = {a.text(): a.isEnabled() for a in balloon_menu_items(window_with_balloon)}
        assert labels["楕円にする"] is False
        assert labels["しっぽを消す"] is False


class TestAdd:
    def test_クリックで置ける(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 60.0, 50.0)

        floating = window_with_panel.state.page.floating
        assert len(floating) == 1
        assert isinstance(floating[0], BalloonObject)
        assert floating[0].rect.center == pytest.approx((60.0, 50.0))

    def test_ドラッグで大きさを決められる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        drag(window_with_panel.view, 40.0, 40.0, 90.0, 75.0)

        rect = window_with_panel.state.page.floating[0].rect
        assert rect.w == pytest.approx(50.0, abs=1.0)
        assert rect.h == pytest.approx(35.0, abs=1.0)

    def test_置いたら選択の道具に戻る(self, window_with_panel):
        """コマ追加と同じ「1回きり」（要件定義 6.9）。"""
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 60.0, 50.0)

        assert window_with_panel.state.tool == TOOL_SELECT
        assert window_with_panel.state.selected_balloon is not None

    def test_続けてクリックしても増えない(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 60.0, 50.0)
        click(window_with_panel.view, 100.0, 80.0)
        assert len(window_with_panel.state.page.floating) == 1

    def test_ギザギザの道具で種類が変わる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON_JAGGED)
        click(window_with_panel.view, 60.0, 50.0)
        assert window_with_panel.state.page.floating[0].style == "jagged"

    def test_コマの上でも作れる(self, window_with_panel):
        """吹き出しはコマの上に置くもの。空白限定にすると置き場所が無い。"""
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, *PANEL.center)
        assert len(window_with_panel.state.page.floating) == 1

    def test_履歴に積まれる(self, window_with_balloon):
        assert window_with_balloon.state.history.undo_label == "吹き出しの追加"
        window_with_balloon.state.undo()
        assert window_with_balloon.state.page.floating == []

    def test_しっぽが最初から付く(self, window_with_balloon):
        balloon = window_with_balloon.state.selected_balloon
        assert balloon.tail.enabled
        assert balloon.tail.tip[1] > balloon.rect.bottom


class TestAttach:
    def test_重なっているコマに自動で紐づく(self, window_with_balloon):
        panel_id = window_with_balloon.state.page.panels[0].id
        assert window_with_balloon.state.selected_balloon.attached_panel_id == panel_id

    def test_コマの外なら紐づかない(self, window_with_panel):
        window_with_panel.state.add_balloon(Rect(160.0, 250.0, 30.0, 20.0))
        assert window_with_panel.state.selected_balloon.attached_panel_id is None

    def test_解除できる(self, window_with_balloon):
        window_with_balloon.toggle_attachment()
        assert window_with_balloon.state.selected_balloon.attached_panel_id is None

    def test_付け直せる(self, window_with_balloon):
        window_with_balloon.toggle_attachment()
        window_with_balloon.toggle_attachment()
        panel_id = window_with_balloon.state.page.panels[0].id
        assert window_with_balloon.state.selected_balloon.attached_panel_id == panel_id

    def test_紐づいていればコマと一緒に動く(self, window_with_balloon):
        state = window_with_balloon.state
        before = state.selected_balloon.rect
        state.select(state.page.panels[0].id)

        window_with_balloon.view._apply_move(PANEL, PANEL.translated(15.0, 0.0))

        after = state.page.floating[0].rect
        assert after.x == pytest.approx(before.x + 15.0)

    def test_解除すればコマと一緒に動かない(self, window_with_balloon):
        state = window_with_balloon.state
        window_with_balloon.toggle_attachment()
        before = state.selected_balloon.rect
        state.select(state.page.panels[0].id)

        window_with_balloon.view._apply_move(PANEL, PANEL.translated(15.0, 0.0))

        assert state.page.floating[0].rect.x == pytest.approx(before.x)


class TestSelectAndMove:
    def test_クリックで選べる(self, window_with_balloon):
        state = window_with_balloon.state
        balloon_id = state.selected_balloon.id
        state.select(None)

        press(window_with_balloon.view, *state.page.floating[0].rect.center)

        assert state.selected_id == balloon_id

    def test_コマより先に拾われる(self, window_with_balloon):
        """吹き出しはコマより手前。ここを間違えると下のコマが動く。"""
        state = window_with_balloon.state
        balloon = state.page.floating[0]
        state.select(None)

        press(window_with_balloon.view, *balloon.rect.center)

        assert state.selected_balloon is not None
        assert window_with_balloon.view._mode == "move"

    def test_楕円の外側では拾われない(self, window_with_balloon):
        """外接矩形で判定すると、四隅の何もない所で下のコマが選べなくなる。"""
        state = window_with_balloon.state
        rect = state.page.floating[0].rect
        state.select(None)

        press(window_with_balloon.view, rect.x + 0.3, rect.y + 0.3)  # 左上の隅

        assert state.selected_balloon is None
        assert state.selected_panel is not None

    def test_動かせる(self, window_with_balloon):
        state = window_with_balloon.state
        origin = state.selected_balloon.rect

        window_with_balloon.view._apply_move(origin, origin.translated(10.0, 5.0))

        moved = state.selected_balloon.rect
        assert (moved.x, moved.y) == pytest.approx((origin.x + 10.0, origin.y + 5.0))

    def test_動かしてもしっぽの先端は残る(self, window_with_balloon):
        """先端はしゃべっている人物を指すページ座標（要件定義 4章）。"""
        state = window_with_balloon.state
        origin = state.selected_balloon.rect
        tip = state.selected_balloon.tail.tip

        window_with_balloon.view._apply_move(origin, origin.translated(30.0, 20.0))

        assert state.selected_balloon.tail.tip == tip

    def test_大きさを変えられる(self, window_with_balloon):
        window_with_balloon.view._apply_resize(Rect(25.0, 25.0, 60.0, 40.0))
        assert window_with_balloon.state.selected_balloon.rect == Rect(25.0, 25.0, 60.0, 40.0)


class TestTail:
    def test_先端を掴んで動かせる(self, window_with_balloon):
        state = window_with_balloon.state
        view = window_with_balloon.view
        tip = state.selected_balloon.tail.tip

        drag(view, tip[0], tip[1], tip[0] + 25.0, tip[1] + 15.0)

        moved = state.selected_balloon.tail.tip
        assert moved[0] == pytest.approx(tip[0] + 25.0, abs=1.0)
        assert moved[1] == pytest.approx(tip[1] + 15.0, abs=1.0)

    def test_先端は履歴に1手だけ積む(self, window_with_balloon):
        """ドラッグの途中経過で履歴が埋まると、Undo が使い物にならない。"""
        state = window_with_balloon.state
        view = window_with_balloon.view
        depth = state.history.depth
        tip = state.selected_balloon.tail.tip

        press(view, tip[0], tip[1])
        for step in range(1, 6):
            move_to(view, tip[0] + step * 3.0, tip[1] + step * 2.0)
        release(view, tip[0] + 15.0, tip[1] + 10.0)

        assert state.history.depth == depth + 1

    def test_先端を掴むと本体は動かない(self, window_with_balloon):
        state = window_with_balloon.state
        rect = state.selected_balloon.rect
        tip = state.selected_balloon.tail.tip

        drag(window_with_balloon.view, tip[0], tip[1], tip[0] + 20.0, tip[1] + 20.0)

        assert state.selected_balloon.rect == rect

    def test_消したり出したりできる(self, window_with_balloon):
        window_with_balloon.toggle_tail()
        assert not window_with_balloon.state.selected_balloon.tail.enabled
        window_with_balloon.toggle_tail()
        assert window_with_balloon.state.selected_balloon.tail.enabled

    def test_しっぽが無ければ先端を掴めない(self, window_with_balloon):
        state = window_with_balloon.state
        tip = state.selected_balloon.tail.tip
        window_with_balloon.toggle_tail()

        press(window_with_balloon.view, tip[0], tip[1])

        assert window_with_balloon.view._mode != "tail"


def render_page(window):
    """ページを 1mm = 1px で描く。mm の座標がそのまま画素の座標になる。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    page = window.state.page
    target = QImage(int(page.size.w), int(page.size.h), QImage.Format.Format_ARGB32)
    target.fill(0)
    painter = QPainter(target)
    window.view._scene.render(
        painter, QRectF(target.rect()), QRectF(0, 0, page.size.w, page.size.h)
    )
    painter.end()
    return target


def is_fill(color) -> bool:
    """吹き出しの塗り（純白）。コマの下地 #F4F4F4 とは区別する。"""
    return color.red() >= 250 and color.green() >= 250 and color.blue() >= 250


def is_ink(color) -> bool:
    return color.red() < 200


class TestBalloonDrawing:
    """描いた画素で確かめる。形の破綻は数字にしないと気づけない。"""

    @pytest.fixture
    def drawn(self, window_with_panel):
        """しっぽを真下へ長く伸ばした吹き出し。選択枠は描かせない。"""
        rect = Rect(50.0, 40.0, 60.0, 36.0)
        balloon = window_with_panel.state.add_balloon(rect)
        window_with_panel.state.set_tail_tip(balloon.id, (80.0, 120.0))
        window_with_panel.state.select(None)
        return window_with_panel, rect, balloon

    def test_中が塗られる(self, drawn):
        window, rect, _ = drawn
        image = render_page(window)
        assert is_fill(image.pixelColor(int(rect.center[0]), int(rect.center[1])))

    def test_外接矩形ではなく楕円で塗る(self, drawn):
        window, rect, _ = drawn
        image = render_page(window)
        # 角はコマの下地のまま
        assert not is_fill(image.pixelColor(int(rect.x) + 1, int(rect.y) + 1))

    @pytest.mark.parametrize("style", ["ellipse", "jagged"])
    def test_本体としっぽの継ぎ目に隙間が空かない(self, drawn, style):
        """別々に描くと、輪郭が凹んだ位置で本体と三角形が離れる。

        先端の近くは三角形が1画素未満に細るので、継ぎ目のある範囲だけ見る。
        """
        window, rect, balloon = drawn
        window.state.set_balloon_style(balloon.id, style)
        window.state.select(None)
        image = render_page(window)

        cx = int(rect.center[0])
        gaps = [
            (y, image.pixelColor(cx, y).name())
            for y in range(int(rect.center[1]), int(rect.bottom) + 15)
            if not (is_fill(image.pixelColor(cx, y)) or is_ink(image.pixelColor(cx, y)))
        ]
        assert gaps == [], f"継ぎ目に隙間: {gaps[:5]}"

    def test_しっぽが先端まで届く(self, drawn):
        window, rect, _ = drawn
        image = render_page(window)
        assert is_ink(image.pixelColor(int(rect.center[0]), 117))

    def test_しっぽを消すと三角形も消える(self, drawn):
        window, rect, balloon = drawn
        window.state.set_tail_enabled(balloon.id, False)
        window.state.select(None)
        image = render_page(window)
        assert not is_fill(
            image.pixelColor(int(rect.center[0]), int(rect.bottom) + 12)
        )

    def test_種類を変えると見た目が変わる(self, drawn):
        window, rect, balloon = drawn
        before = render_page(window)
        window.state.set_balloon_style(balloon.id, "jagged")
        window.state.select(None)
        after = render_page(window)

        diff = sum(
            1
            for y in range(int(rect.y) - 2, int(rect.bottom) + 2)
            for x in range(int(rect.x) - 2, int(rect.right) + 2)
            if before.pixelColor(x, y) != after.pixelColor(x, y)
        )
        assert diff > 100


class TestStyleAndDelete:
    def test_種類を変えられる(self, window_with_balloon):
        window_with_balloon.set_balloon_style("jagged")
        assert window_with_balloon.state.selected_balloon.style == "jagged"
        window_with_balloon.set_balloon_style("ellipse")
        assert window_with_balloon.state.selected_balloon.style == "ellipse"

    def test_同じ種類なら履歴に積まない(self, window_with_balloon):
        depth = window_with_balloon.state.history.depth
        window_with_balloon.set_balloon_style("ellipse")
        assert window_with_balloon.state.history.depth == depth

    def test_削除できる(self, window_with_balloon):
        window_with_balloon.delete_selected()
        assert window_with_balloon.state.page.floating == []
        assert window_with_balloon.state.selected_object is None

    def test_コマを消しても吹き出しは残る(self, window_with_balloon):
        """セリフはコマより手間がかかっている（要件定義 6.2）。"""
        state = window_with_balloon.state
        state.select(state.page.panels[0].id)

        window_with_balloon.delete_selected()

        assert len(state.page.floating) == 1
        assert state.page.floating[0].attached_panel_id is None

    def test_保存して開き直せる(self, window_with_balloon, tmp_path):
        from manga_layout import load_project

        window_with_balloon.set_balloon_style("jagged")
        window_with_balloon.state.save(tmp_path)

        restored = load_project(tmp_path)
        balloon = restored.pages[0].floating[0]
        assert balloon.style == "jagged"
        assert balloon.tail.enabled
        assert restored.load_warnings == []
