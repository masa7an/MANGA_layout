"""フキダシの操作まわりの検証（画面なし）。

形そのものは tests/test_balloon_shape.py で押さえている。ここでは
「操作 → モデルの変更 → 履歴に積む」がつながっているか、
コマ・画像・フキダシの選択が取り合いにならないかを確かめる。

## 画面に出る文字の扱い

**「探すための文字」と「確かめるための文字」を分ける。**

- **探すため**（メニューや項目を見つける）には文字を使わない。部品そのものか
  `BALLOON_STYLE_LABELS` を通す。呼び名を変えるたびに「見つかりません」で
  落ちると、**呼び名の変更なのか項目が消えたのかが区別できない**
- **確かめるため**（表示がその文字であること自体が仕様）は決め打ちのままにする。
  Undo の表示や削除の項目名がこれ。呼び名を変えたときにここが落ちるのは正しく、
  画面の文字を変えたことを見落とさないための歯止めになる

2026-08-04 の改名（吹き出し → フキダシ）で 11 件が落ち、その内訳が
「探すため」6 件・「確かめるため」5 件だったことからこの形にした。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Rect
from manga_layout.model import BalloonObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import (
    BALLOON_STYLE_LABELS,
    TOOL_BALLOON,
    TOOL_BALLOON_JAGGED,
    TOOL_BALLOON_WAVY,
    TOOL_PANEL,
    TOOL_SELECT,
)

# 座標は px（要件定義 3章）。既定の吹き出し 236×154 が中に収まる大きさ
PANEL = Rect(120.0, 120.0, 720.0, 540.0)


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
    window_with_panel.state.add_balloon(Rect(180.0, 180.0, 240.0, 156.0))
    return window_with_panel


@pytest.fixture
def messages(window_with_balloon):
    """状態表示に流れた文言を順に控える。"""
    seen = []
    window_with_balloon.state.message.connect(seen.append)
    return seen


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
    """フキダシメニューの項目（区切り線を除く）。

    **名前で探さない。中身で探す。**
    メニューバーを辿り、「種類を変える項目」を含むメニューを見つける。
    呼び名を変えるたびに「見つかりません」で落ちると、**呼び名の変更なのか
    メニューが消えたのか区別できない**ため。

    ここで使う `action.menu()` は、その QMenu を呼び出し側の QAction に
    引き取らせる。使い捨ての QAction が片付いた時点でメニューの Python 側の
    参照は無効になる（C++ の実体は生きたまま。→ `MainWindow._items_to_copy`）。
    2026-08-04、`window.balloon_menu` を直に見る形にした6件がこれで落ちた。
    アプリ側は QMenu を持たない形に直したので、この書き方のままでよい。

    目印に「種類を変える項目」を使うのは、道具の項目だと**道具メニューにも
    同じものが並んでいる**ため。こちらはフキダシメニューにしか無い。
    """
    marker = window.balloon_actions[0]
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None and marker in menu.actions():
            return [a for a in menu.actions() if not a.isSeparator()]
    raise AssertionError("フキダシメニューが見つかりません")


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
        assert all(a.isEnabled() for a in items[:3])
        assert all("追加" in a.text() for a in items[:3])

    def test_追加の項目から道具に切り替わる(self, window):
        items = balloon_menu_items(window)
        items[0].trigger()
        assert window.state.tool == TOOL_BALLOON
        items[1].trigger()
        assert window.state.tool == TOOL_BALLOON_JAGGED
        items[2].trigger()
        assert window.state.tool == TOOL_BALLOON_WAVY

    def test_道具バーと同じ項目を指す(self, window):
        """別々の項目にすると、選ばれている印が片方にしか付かない。"""
        items = balloon_menu_items(window)
        assert items[0] is window._tool_actions[TOOL_BALLOON]
        assert items[1] is window._tool_actions[TOOL_BALLOON_JAGGED]
        assert items[2] is window._tool_actions[TOOL_BALLOON_WAVY]

    def test_選択中だけ使える項目もある(self, window_with_balloon):
        items = balloon_menu_items(window_with_balloon)
        assert all(a.isEnabled() for a in items)

    def test_選択を外すと編集の項目は戻る(self, window_with_balloon):
        window_with_balloon.state.select(None)
        labels = {a.text(): a.isEnabled() for a in balloon_menu_items(window_with_balloon)}
        assert labels[f"{BALLOON_STYLE_LABELS['ellipse']}にする"] is False
        assert labels["しっぽを消す"] is False


class TestAdd:
    def test_クリックで置ける(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 400.0, 320.0)

        floating = window_with_panel.state.page.floating
        assert len(floating) == 1
        assert isinstance(floating[0], BalloonObject)
        assert floating[0].rect.center == pytest.approx((400.0, 320.0))

    def test_ドラッグで大きさを決められる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        drag(window_with_panel.view, 240.0, 240.0, 540.0, 450.0)

        rect = window_with_panel.state.page.floating[0].rect
        assert rect.w == pytest.approx(300.0, abs=6.0)
        assert rect.h == pytest.approx(210.0, abs=6.0)

    def test_置いたら選択の道具に戻る(self, window_with_panel):
        """コマ追加と同じ「1回きり」（要件定義 6.9）。"""
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 360.0, 300.0)

        assert window_with_panel.state.tool == TOOL_SELECT
        assert window_with_panel.state.selected_balloon is not None

    def test_続けてクリックしても増えない(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, 360.0, 300.0)
        click(window_with_panel.view, 600.0, 480.0)
        assert len(window_with_panel.state.page.floating) == 1

    def test_ギザギザの道具で種類が変わる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON_JAGGED)
        click(window_with_panel.view, 360.0, 300.0)
        assert window_with_panel.state.page.floating[0].style == "jagged"

    def test_波形の道具で種類が変わる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_BALLOON_WAVY)
        click(window_with_panel.view, 360.0, 300.0)
        assert window_with_panel.state.page.floating[0].style == "wavy"

    def test_コマの上でも作れる(self, window_with_panel):
        """吹き出しはコマの上に置くもの。空白限定にすると置き場所が無い。"""
        window_with_panel.state.set_tool(TOOL_BALLOON)
        click(window_with_panel.view, *PANEL.center)
        assert len(window_with_panel.state.page.floating) == 1

    def test_履歴に積まれる(self, window_with_balloon):
        # Undo の表示は画面に出る文字なので、ここは決め打ちのままにする。
        # 呼び名を変えたらこのテストが落ちるのが正しい（→ 冒頭の説明）
        assert window_with_balloon.state.history.undo_label == "フキダシの追加"
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
        window_with_panel.state.add_balloon(Rect(960.0, 1500.0, 180.0, 120.0))
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
        window_with_balloon.view._apply_resize(Rect(150.0, 150.0, 360.0, 240.0))
        assert window_with_balloon.state.selected_balloon.rect == Rect(150.0, 150.0, 360.0, 240.0)


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
        rect = Rect(300.0, 240.0, 360.0, 216.0)
        balloon = window_with_panel.state.add_balloon(rect)
        window_with_panel.state.set_tail_tip(balloon.id, (480.0, 720.0))
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

    @pytest.mark.parametrize("style", ["ellipse", "jagged", "wavy"])
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

    @pytest.mark.parametrize("style", ["ellipse", "jagged", "wavy"])
    @pytest.mark.parametrize("root_y", [-1.0, -0.5, 0.0, 0.5, 1.0])
    def test_付け根をどこにずらしても隙間が空かない(self, drawn, style, root_y):
        """付け根の位置が変われば、輪郭の凹凸との噛み合わせも変わる。

        ギザギザの谷にあたる高さに来たときが危ない。
        """
        window, rect, balloon = drawn
        window.state.set_balloon_style(balloon.id, style)
        window.state.set_tail_root(balloon.id, root_y)
        window.state.select(None)
        image = render_page(window)

        from manga_layout.layout import tail_triangle

        current = window.state.page.floating[0]
        base1, tip, base2 = tail_triangle(current, window.state.balloon_settings)
        mid = ((base1[0] + base2[0]) / 2.0, (base1[1] + base2[1]) / 2.0)
        center = current.rect.center

        # 中心 → 付け根の中点 → 少しだけ先端寄り、の順にたどる。
        # **2本に折る必要がある。** 中心から先端寄りの点へ一直線に引くと、
        # 継ぎ目を通らず輪郭の脇を抜けてしまい、塗りの外を拾う。
        # 先端の側は三角形が1画素未満に細るので、15% までにとどめる
        beyond = (mid[0] + (tip[0] - mid[0]) * 0.15, mid[1] + (tip[1] - mid[1]) * 0.15)
        gaps = []
        for start, finish in ((center, mid), (mid, beyond)):
            for i in range(41):
                t = i / 40
                x = int(round(start[0] + (finish[0] - start[0]) * t))
                y = int(round(start[1] + (finish[1] - start[1]) * t))
                color = image.pixelColor(x, y)
                if not (is_fill(color) or is_ink(color)):
                    gaps.append((x, y, color.name()))
        assert gaps == [], f"付け根 {root_y} で隙間: {gaps[:5]}"

    def test_付け根を上端に寄せると絵が変わる(self, drawn):
        window, rect, balloon = drawn
        window.state.set_tail_root(balloon.id, 1.0)
        window.state.select(None)
        low = render_page(window)

        window.state.set_tail_root(balloon.id, -1.0)
        window.state.select(None)
        high = render_page(window)

        diff = sum(
            1
            for y in range(int(rect.y) - 5, int(rect.bottom) + 20)
            for x in range(int(rect.x) - 5, int(rect.right) + 5)
            if low.pixelColor(x, y) != high.pixelColor(x, y)
        )
        assert diff > 100

    def test_しっぽが先端まで届く(self, drawn):
        window, rect, _ = drawn
        image = render_page(window)
        # 先端（y=720）のすぐ手前。しっぽの内側は白なので、輪郭が寄り合う
        # 先端近くを見る。ここが白なら、しっぽが途中で切れている
        assert is_ink(image.pixelColor(int(rect.center[0]), 718))

    def test_しっぽを消すと三角形も消える(self, drawn):
        window, rect, balloon = drawn
        window.state.set_tail_enabled(balloon.id, False)
        window.state.select(None)
        image = render_page(window)
        assert not is_fill(
            image.pixelColor(int(rect.center[0]), int(rect.bottom) + 72)
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


class TestTailRoot:
    """しっぽの付け根を上下にずらす操作。"""

    def test_付け根の印を掴める(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        press(window_with_balloon.view, root[0], root[1])

        assert window_with_balloon.view._mode == "tail_root"

    def test_上下にドラッグすると付け根が動く(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        rect = state.selected_balloon.rect
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(view, root[0], root[1], root[0], rect.y)  # 上端まで

        assert state.selected_balloon.tail.root_y == pytest.approx(-1.0, abs=0.05)

    def test_横に動かしても縦しか変わらない(self, window_with_balloon):
        """付け根は輪郭の上を滑る。横を拾うと形が飛ぶ。"""
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        rect = state.selected_balloon.rect
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(view, root[0], root[1], root[0] + 200.0, rect.center[1])

        assert state.selected_balloon.tail.root_y == pytest.approx(0.0, abs=0.05)

    def test_吹き出しの外まで引いても範囲に収まる(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(view, root[0], root[1], root[0], root[1] + 500.0)

        assert state.selected_balloon.tail.root_y == pytest.approx(1.0)

    def test_先端の反対側までは動かない(self, window_with_balloon):
        """離れるほどしっぽが針に痩せる（→ `TAIL_ROOT_MAX_GAP`）。

        止めずに通すと、印だけ上へ飛んでしっぽは下から出たままになる。
        """
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        rect = state.selected_balloon.rect
        before = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(view, before[0], before[1], before[0], rect.y)  # 上端まで

        after = tail_root_point(state.selected_balloon, state.balloon_settings)
        assert after[1] < before[1]  # 少しは上がる
        assert after[1] > rect.center[1]  # が、先端の側に留まる

    def test_止まった先の高さを知らせる(self, window_with_balloon, messages):
        """言われた値を出すと、印が動いていないのに「上端」と名乗る。"""
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        rect = state.selected_balloon.rect
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(view, root[0], root[1], root[0], rect.y)

        assert messages[-1].startswith("しっぽの付け根: ")
        assert "上端" not in messages[-1]

    def test_履歴に1手だけ積む(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        view = window_with_balloon.view
        depth = state.history.depth
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        press(view, root[0], root[1])
        for step in range(1, 6):
            move_to(view, root[0], root[1] - step * 2.0)
        release(view, root[0], root[1] - 10.0)

        assert state.history.depth == depth + 1

    def test_付け根を動かしても本体と先端は動かない(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        rect = state.selected_balloon.rect
        tip = state.selected_balloon.tail.tip
        root = tail_root_point(state.selected_balloon, state.balloon_settings)

        drag(window_with_balloon.view, root[0], root[1], root[0], rect.y)

        assert state.selected_balloon.rect == rect
        assert state.selected_balloon.tail.tip == tip

    def test_当たり判定はひし形より広い(self, window_with_balloon):
        """ひし形は同じ大きさの四角より面積が半分で、狙っても外れやすい。"""
        from manga_layout.layout import tail_root_point
        from manga_layout.ui.canvas import HANDLE_PX, TAIL_ROOT_HANDLE_PX

        state = window_with_balloon.state
        root = tail_root_point(state.selected_balloon, state.balloon_settings)
        # 描いてある印の外、広げた判定の内。判定は画面ピクセルなので、
        # 表示倍率で割ってから使う
        scale = window_with_balloon.view.view_scale
        off = (HANDLE_PX + TAIL_ROOT_HANDLE_PX) / 2.0 / scale / 2.0

        press(window_with_balloon.view, root[0] + off, root[1])

        assert window_with_balloon.view._mode == "tail_root"

    def test_角のつまみまでは奪わない(self, window_with_balloon):
        """付け根は角のつまみより先に判定される。広げすぎると覆い隠す。"""
        rect = window_with_balloon.state.selected_balloon.rect

        press(window_with_balloon.view, rect.right, rect.bottom)

        assert window_with_balloon.view._mode == "resize"


class TestTailTurn:
    """メニューからしっぽの向きを変える。付け根だけでなく**先端も回る**。"""

    @pytest.mark.parametrize(
        "ratio,expected", [(-1.0, "上"), (0.0, "横"), (1.0, "下")]
    )
    def test_先端が指定した側へ回る(self, window_with_balloon, ratio, expected):
        state = window_with_balloon.state
        rect = state.selected_balloon.rect

        window_with_balloon.turn_tail(ratio)

        tip = state.selected_balloon.tail.tip
        if expected == "上":
            assert tip[1] < rect.y
        elif expected == "下":
            assert tip[1] > rect.bottom
        else:
            assert tip[0] > rect.right

    def test_付け根も指定した高さへ動く(self, window_with_balloon):
        from manga_layout.layout import tail_root_point

        state = window_with_balloon.state
        rect = state.selected_balloon.rect

        window_with_balloon.turn_tail(-1.0)

        root = tail_root_point(state.selected_balloon, state.balloon_settings)
        assert root[1] == pytest.approx(
            rect.center[1] - rect.h / 2.0 * 0.95, abs=0.01
        )

    def test_しっぽの長さは変わらない(self, window_with_panel):
        """先端は回すだけ。長さまで変わると、しっぽの印象が変わってしまう。"""
        state = window_with_panel.state
        # 真円で測る。楕円だと縦横の伸びが効いて px の長さは変わる
        # （変わらないのは**フキダシに対する割合**のほう）
        balloon = state.add_balloon(Rect(200.0, 200.0, 200.0, 200.0))
        center = balloon.rect.center
        before = balloon.tail.tip
        length = math.dist(center, before)

        window_with_panel.turn_tail(-1.0)

        after = state.selected_balloon.tail.tip
        assert after != before
        assert math.dist(center, after) == pytest.approx(length)

    def test_付け根の指定は自動へ戻る(self, window_with_balloon):
        """回した先では高さと先端の向きが一致する。値を残すと後で効く。"""
        state = window_with_balloon.state
        state.set_tail_root(state.selected_balloon.id, 0.5)

        window_with_balloon.turn_tail(-1.0)

        assert state.selected_balloon.tail.root_y is None

    def test_元に戻せる(self, window_with_balloon):
        state = window_with_balloon.state
        before = state.selected_balloon.tail.tip

        window_with_balloon.turn_tail(-1.0)
        state.undo()

        assert state.selected_balloon.tail.tip == before

    def test_しっぽが無ければ知らせる(self, window_with_balloon, messages):
        state = window_with_balloon.state
        state.set_tail_enabled(state.selected_balloon.id, False)
        tip = state.selected_balloon.tail.tip

        window_with_balloon.turn_tail(-1.0)

        assert state.selected_balloon.tail.tip == tip
        assert messages[-1] == "しっぽが出ていません"

    def test_保存して開き直しても残る(self, window_with_balloon, tmp_path):
        from manga_layout import load_project

        state = window_with_balloon.state
        state.set_tail_root(state.selected_balloon.id, -0.5)
        state.save(tmp_path)

        restored = load_project(tmp_path)
        assert restored.pages[0].floating[0].tail.root_y == pytest.approx(-0.5)
        assert restored.load_warnings == []

    def test_項目が無い作品は自動として開ける(self, window_with_balloon, tmp_path):
        """root_y を足す前に保存したファイルでも、それまでと同じ形で開ける。"""
        import json

        from manga_layout import load_project

        window_with_balloon.state.save(tmp_path)
        path = tmp_path / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        del data["pages"][0]["floating"][0]["tail"]["root_y"]
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        restored = load_project(tmp_path)
        assert restored.pages[0].floating[0].tail.root_y is None

    def test_範囲外の値は読み込みで弾く(self, window_with_balloon, tmp_path):
        """黙って直すと、保存のたびに形が変わる。"""
        import json

        import pytest as _pytest

        from manga_layout import load_project
        from manga_layout.errors import ProjectFormatError

        window_with_balloon.state.save(tmp_path)
        path = tmp_path / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["pages"][0]["floating"][0]["tail"]["root_y"] = 3.5
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        with _pytest.raises(ProjectFormatError):
            load_project(tmp_path)


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
