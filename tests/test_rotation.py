"""画像の回転の検証（要件定義 6.3）。

回転は `rect` を傾けず、**描画・当たり判定・つまみの3か所の境目でだけ**
角度を掛ける作りにしてある。そのため確かめるべきことは2つに分かれる。

- 傾けた絵に対して、掴む場所・当たり判定・フィットが正しく回っているか
- **傾き 0 のときに今までと同じ結果を返すか**（既にある作品への影響を閉じる）

とくに `keep_anchor` の中心ズレ補正は、傾き 0 では絶対に再現しない。
実機で見ればすぐ分かるが、傾けない限り誰も気づけないのでここで固定する。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Rect, new_project
from manga_layout.geometry import (
    normalize_angle,
    rotate_point,
    rotated_bounds,
    rotated_rect_contains,
    unrotate_point,
)
from manga_layout.layout import (
    anchor_of,
    cover_rect_in,
    handle_at,
    handle_positions,
    image_at,
    keep_anchor,
    resize_rect,
)


class TestAngle:
    def test_180を超えたら畳む(self):
        # 回し続けても project.json に 3600 のような数字を残さない
        assert normalize_angle(370.0) == pytest.approx(10.0)
        assert normalize_angle(-370.0) == pytest.approx(-10.0)
        assert normalize_angle(270.0) == pytest.approx(-90.0)

    def test_範囲内はそのまま(self):
        for angle in (-179.0, -45.0, 0.0, 45.0, 180.0):
            assert normalize_angle(angle) == pytest.approx(angle)


class TestRotatePoint:
    def test_正の角度は時計回り(self):
        # 画面の y は下向き。QPainter.rotate と向きを揃えてある。
        # 原点の右にある点を 90 度回すと、画面では真下に来る
        x, y = rotate_point(1.0, 0.0, 0.0, 0.0, 90.0)
        assert (x, y) == pytest.approx((0.0, 1.0))

    def test_中心は動かない(self):
        assert rotate_point(5.0, 7.0, 5.0, 7.0, 33.0) == pytest.approx((5.0, 7.0))

    def test_0度では計算せずそのまま返す(self):
        # 傾けていない作品に浮動小数の丸めを持ち込まないための分岐
        assert rotate_point(1.3, 2.7, 0.0, 0.0, 0.0) == (1.3, 2.7)

    def test_逆に回すと戻る(self):
        rect = Rect(10.0, 20.0, 40.0, 30.0)
        moved = rotate_point(12.0, 34.0, *rect.center, 37.0)
        assert unrotate_point(*moved, rect, 37.0) == pytest.approx((12.0, 34.0))


class TestRotatedBounds:
    def test_45度に回すと外接矩形が広がる(self):
        rect = Rect(0.0, 0.0, 10.0, 10.0)
        bounds = rotated_bounds(rect, 45.0)
        side = 10.0 * math.sqrt(2.0)
        assert (bounds.w, bounds.h) == pytest.approx((side, side))
        # 中心は変わらない
        assert bounds.center == pytest.approx(rect.center)

    def test_90度では縦横が入れ替わる(self):
        bounds = rotated_bounds(Rect(0.0, 0.0, 40.0, 10.0), 90.0)
        assert (bounds.w, bounds.h) == pytest.approx((10.0, 40.0))

    def test_0度では同じ矩形(self):
        rect = Rect(3.0, 4.0, 5.0, 6.0)
        assert rotated_bounds(rect, 0.0) is rect


class TestRotatedContains:
    def test_傾けると角が外れ辺が入る(self):
        # 45 度に回した正方形。元の左上の角は外へ出て、
        # 元の外だった上辺の中央の少し上が中に入る
        rect = Rect(0.0, 0.0, 10.0, 10.0)
        assert rect.contains(0.2, 0.2)
        assert not rotated_rect_contains(rect, 0.2, 0.2, 45.0)
        assert not rect.contains(5.0, -2.0)
        assert rotated_rect_contains(rect, 5.0, -2.0, 45.0)

    def test_中心は傾きによらず内側(self):
        rect = Rect(10.0, 10.0, 30.0, 20.0)
        for angle in (0.0, 17.0, 90.0, -123.0):
            assert rotated_rect_contains(rect, *rect.center, angle)


class TestHandlesWithRotation:
    def test_つまみの位置が回る(self):
        rect = Rect(0.0, 0.0, 10.0, 10.0)
        positions = handle_positions(rect, 90.0)
        # 左上の角は、時計回りに 90 度回すと右上へ来る
        assert positions["nw"] == pytest.approx((10.0, 0.0))
        # 名前は回す前の向きのまま。ここまで回すとリサイズの計算と対応が取れない
        assert set(positions) == set(handle_positions(rect))

    def test_傾いたつまみを掴める(self):
        rect = Rect(0.0, 0.0, 10.0, 10.0)
        x, y = handle_positions(rect, 30.0)["se"]
        assert handle_at(rect, x, y, 2.0, 30.0) == "se"
        # 回す前の位置にはもう無い
        assert handle_at(rect, rect.right, rect.bottom, 2.0, 30.0) is None

    def test_傾き0では今までと同じ(self):
        rect = Rect(1.0, 2.0, 30.0, 40.0)
        assert handle_positions(rect, 0.0) == handle_positions(rect)
        assert handle_at(rect, rect.x, rect.y, 2.0, 0.0) == "nw"


class TestAnchor:
    @pytest.mark.parametrize(
        "handle, expected",
        [
            ("se", (0.0, 0.0)),
            ("nw", (10.0, 20.0)),
            ("ne", (0.0, 20.0)),
            ("sw", (10.0, 0.0)),
            ("n", (5.0, 20.0)),
            ("s", (5.0, 0.0)),
            ("e", (0.0, 10.0)),
            ("w", (10.0, 10.0)),
        ],
    )
    def test_掴んでいない側の代表点(self, handle, expected):
        rect = Rect(0.0, 0.0, 10.0, 20.0)
        assert anchor_of(rect, handle) == pytest.approx(expected)


class TestKeepAnchor:
    """傾いた矩形のリサイズで、掴んでいない側が動かないこと。

    回転の中心が矩形の中心なので、幅を変えると中心も動く。補正しないと
    画面の上では固定したはずの反対側の角まで動いて見える。
    """

    @pytest.mark.parametrize("handle", ["nw", "ne", "se", "sw", "n", "s", "e", "w"])
    def test_反対側の角が画面上で動かない(self, handle):
        origin = Rect(0.0, 0.0, 40.0, 20.0)
        rotation = 30.0
        # つまみを引いた先（回す前の座標）。どの向きへも 5px 広げる
        hx, hy = handle_positions(origin)[handle]
        cx, cy = origin.center
        target = (hx + (5.0 if hx >= cx else -5.0), hy + (5.0 if hy >= cy else -5.0))

        resized = resize_rect(origin, handle, *target, 1.0)
        fixed = keep_anchor(origin, resized, handle, rotation)

        # 掴んでいない側の代表点が、画面上（＝回したあと）で同じ位置にある。
        # 代表点は矩形ごとに求める（補正で平行移動するため、同じ座標では
        # 比べられない）
        before = rotate_point(*anchor_of(origin, handle), *origin.center, rotation)
        after = rotate_point(*anchor_of(fixed, handle), *fixed.center, rotation)
        assert after == pytest.approx(before)

    def test_大きさは補正で変わらない(self):
        origin = Rect(0.0, 0.0, 40.0, 20.0)
        resized = resize_rect(origin, "se", 60.0, 30.0, 1.0)
        fixed = keep_anchor(origin, resized, "se", 25.0)
        assert (fixed.w, fixed.h) == pytest.approx((resized.w, resized.h))

    def test_補正しないとズレる(self):
        # 補正が本当に効いているかの裏取り。傾き 0 では出ない差
        origin = Rect(0.0, 0.0, 40.0, 20.0)
        resized = resize_rect(origin, "se", 60.0, 30.0, 1.0)
        naive = rotate_point(*anchor_of(origin, "se"), *resized.center, 30.0)
        before = rotate_point(*anchor_of(origin, "se"), *origin.center, 30.0)
        assert naive != pytest.approx(before)

    def test_傾き0では何もしない(self):
        origin = Rect(0.0, 0.0, 40.0, 20.0)
        resized = resize_rect(origin, "se", 60.0, 30.0, 1.0)
        assert keep_anchor(origin, resized, "se", 0.0) is resized


class TestCoverWithRotation:
    def test_傾けたままコマを覆う(self):
        outer = Rect(0.0, 0.0, 100.0, 60.0)
        rect = cover_rect_in(outer, (200, 100), 20.0)
        bounds = rotated_bounds(rect, 20.0)
        assert bounds.w >= outer.w - 1e-9
        assert bounds.h >= outer.h - 1e-9
        assert rect.center == pytest.approx(outer.center)

    def test_無駄に大きくしない(self):
        # 「埋める最小」なので、外接矩形のどちらかの辺はちょうど接する
        outer = Rect(0.0, 0.0, 100.0, 60.0)
        bounds = rotated_bounds(cover_rect_in(outer, (200, 100), 20.0), 20.0)
        assert min(bounds.w - outer.w, bounds.h - outer.h) == pytest.approx(0.0)

    def test_縦横比は保つ(self):
        rect = cover_rect_in(Rect(0.0, 0.0, 100.0, 60.0), (200, 100), 35.0)
        assert rect.w / rect.h == pytest.approx(2.0)

    def test_傾き0では今までと同じ(self):
        outer = Rect(0.0, 0.0, 100.0, 60.0)
        assert cover_rect_in(outer, (200, 100), 0.0) == cover_rect_in(outer, (200, 100))

    def test_90度でも覆う(self):
        outer = Rect(0.0, 0.0, 100.0, 60.0)
        bounds = rotated_bounds(cover_rect_in(outer, (200, 100), 90.0), 90.0)
        assert bounds.w >= outer.w - 1e-9
        assert bounds.h >= outer.h - 1e-9


@pytest.fixture
def window(qapp):
    from manga_layout.ui import EditorState, MainWindow

    win = MainWindow(EditorState())
    yield win
    # 未保存のまま閉じると確認ダイアログで止まる
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_image(window, png_bytes):
    """全面コマに画像を1枚置いた状態。画像が選ばれている。"""
    window.add_full_page_panel()
    window.state.place_image(window.state.selected_panel.id, png_bytes)
    return window


def _mouse(view, kind, x, y, shift=False):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    modifiers = (
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    )
    position = QPointF(view.mapFromScene(QPointF(x, y)))
    buttons = {
        "press": (Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        "move": (Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
        "release": (Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
    }[kind]
    types = {
        "press": QMouseEvent.Type.MouseButtonPress,
        "move": QMouseEvent.Type.MouseMove,
        "release": QMouseEvent.Type.MouseButtonRelease,
    }
    event = QMouseEvent(
        types[kind],
        position,
        view.viewport().mapToGlobal(position),
        buttons[0],
        buttons[1],
        modifiers,
    )
    {"press": view.mousePressEvent, "move": view.mouseMoveEvent,
     "release": view.mouseReleaseEvent}[kind](event)


def rotate_image(window, angle):
    """選択中の画像を、確定と同じ道（履歴に積む）で傾ける。

    `image.rotation` を直に書き換えると、履歴の基準（baseline）とモデルが
    食い違い、次の1手が「変化なし」と判定されて積まれない。
    """
    window.view._apply_rotate(window.state.selected_image.id, angle)


def point_at(rect, degrees, radius):
    """矩形の中心から `degrees` の向きへ `radius` 離れた点。

    回転つまみを掴んだあとの引き先を作るのに使う。つまみは真上（-90 度）に
    出るので、そこから引いた角度がそのまま結果の傾きになる。
    """
    cx, cy = rect.center
    rad = math.radians(degrees)
    return (cx + radius * math.cos(rad), cy + radius * math.sin(rad))


class TestRotateHandle:
    def test_画像を選ぶとつまみが上に出る(self, window_with_image):
        view = window_with_image.view
        rect = window_with_image.state.selected_image.rect
        point = view._rotate_handle_point()

        assert point is not None
        assert point[0] == pytest.approx(rect.center[0])
        assert point[1] < rect.y  # 上辺の外

    def test_コマを選んでいるときは出ない(self, window_with_image):
        panel = window_with_image.state.page.panels[0]
        window_with_image.state.select(panel.id)
        assert window_with_image.view._rotate_handle_point() is None

    def test_つまみを掴むと回転が始まる(self, window_with_image):
        from manga_layout.ui.canvas import RotateDrag

        view = window_with_image.view
        _mouse(view, "press", *view._rotate_handle_point())

        assert isinstance(view._drag, RotateDrag)
        assert view._scene.rotate_preview is not None

    def test_案内が出る(self, window_with_image):
        from manga_layout.ui.canvas import ROTATE_HINT

        seen = []
        window_with_image.state.message.connect(seen.append)
        view = window_with_image.view
        _mouse(view, "press", *view._rotate_handle_point())

        assert ROTATE_HINT in seen

    def test_傾けるとつまみも一緒に回る(self, window_with_image):
        view = window_with_image.view
        image = window_with_image.state.selected_image
        image.rotation = 90.0
        point = view._rotate_handle_point()

        # 真上に出ていた印が、時計回りに 90 度回って右に来る
        assert point[0] > image.rect.right
        assert point[1] == pytest.approx(image.rect.center[1])


class TestRotateDrag:
    def test_引いたぶんだけ傾く(self, window_with_image):
        view = window_with_image.view
        rect = window_with_image.state.selected_image.rect
        _mouse(view, "press", *view._rotate_handle_point())
        # つまみは真上（-90 度）にある。右（0 度）まで引けば 90 度回る
        target = point_at(rect, 0.0, 40.0)
        _mouse(view, "move", *target)
        _mouse(view, "release", *target)

        # 画面のピクセルは整数なので、掴んだ位置がわずかに丸まる。
        # 1度の幅で見れば「引いた向きへ回った」ことは確かめられる
        assert window_with_image.state.selected_image.rotation == pytest.approx(
            90.0, abs=1.0
        )

    def test_離すまでモデルに触らない(self, window_with_image):
        view = window_with_image.view
        rect = window_with_image.state.selected_image.rect
        _mouse(view, "press", *view._rotate_handle_point())
        _mouse(view, "move", *point_at(rect, 0.0, 40.0))

        # 下見には出るが、モデルはまだ 0
        assert view._scene.rotate_preview[1] == pytest.approx(90.0, abs=1.0)
        assert window_with_image.state.selected_image.rotation == 0.0

    def test_Shiftで15度ずつ刻む(self, window_with_image):
        view = window_with_image.view
        rect = window_with_image.state.selected_image.rect
        _mouse(view, "press", *view._rotate_handle_point())
        # -90 + 47 = -43 の向きへ引く → 47 度ぶん回したことになる
        target = point_at(rect, -43.0, 40.0)
        _mouse(view, "move", *target, shift=True)
        _mouse(view, "release", *target)

        assert window_with_image.state.selected_image.rotation == pytest.approx(45.0)

    def test_履歴に1手として積む(self, window_with_image):
        view = window_with_image.view
        rect = window_with_image.state.selected_image.rect
        depth = window_with_image.state.history.depth

        _mouse(view, "press", *view._rotate_handle_point())
        target = point_at(rect, 0.0, 40.0)
        _mouse(view, "move", *target)
        _mouse(view, "release", *target)

        assert window_with_image.state.history.depth == depth + 1
        assert window_with_image.state.history.undo_label == "画像の回転"
        window_with_image.state.undo()
        assert window_with_image.state.selected_image.rotation == 0.0

    def test_回さずに離せば履歴に積まない(self, window_with_image):
        view = window_with_image.view
        depth = window_with_image.state.history.depth
        point = view._rotate_handle_point()
        _mouse(view, "press", *point)
        _mouse(view, "release", *point)

        assert window_with_image.state.history.depth == depth


class TestRotatedEditing:
    def test_傾いた絵の中を掴むと移動になる(self, window_with_image):
        from manga_layout.ui.canvas import MoveDrag

        view = window_with_image.view
        image = window_with_image.state.selected_image
        image.rotation = 45.0
        _mouse(view, "press", *image.rect.center)

        assert isinstance(view._drag, MoveDrag)

    def test_傾いた角のつまみを掴むとリサイズになる(self, window_with_image):
        from manga_layout.ui.canvas import ResizeDrag

        view = window_with_image.view
        image = window_with_image.state.selected_image
        image.rotation = 30.0
        x, y = handle_positions(image.rect, 30.0)["se"]
        _mouse(view, "press", x, y)

        assert isinstance(view._drag, ResizeDrag)
        assert view._drag.handle == "se"

    def test_傾いていると吸着しない(self, window_with_image):
        view = window_with_image.view
        assert view._rect_snap_threshold() > 0.0
        window_with_image.state.selected_image.rotation = 12.0
        assert view._rect_snap_threshold() == 0.0

    def test_コマの吸着までは止めない(self, window_with_image):
        # 傾いた画像を選んだままコマを作るときは、今までどおり吸着する
        view = window_with_image.view
        window_with_image.state.selected_image.rotation = 12.0
        assert view._snap_threshold() > 0.0


class TestFitAndReset:
    def test_フィットしても傾きが残る(self, window_with_image):
        rotate_image(window_with_image, 20.0)
        panel = window_with_image.state.page.panels[0].shape.bounds()

        window_with_image.fit_image()

        after = window_with_image.state.selected_image
        assert after.rotation == pytest.approx(20.0)
        bounds = rotated_bounds(after.rect, after.rotation)
        assert bounds.w >= panel.w - 1e-9
        assert bounds.h >= panel.h - 1e-9

    def test_回転をリセットできる(self, window_with_image):
        rotate_image(window_with_image, 33.0)
        window_with_image.reset_image_rotation()

        assert window_with_image.state.selected_image.rotation == 0.0
        assert window_with_image.state.history.undo_label == "回転をリセット"

    def test_傾いていなければ履歴に積まない(self, window_with_image):
        depth = window_with_image.state.history.depth
        window_with_image.reset_image_rotation()
        assert window_with_image.state.history.depth == depth

    def test_傾いているときだけメニューに出す(self, window_with_image):
        window_with_image._refresh()
        assert not window_with_image.image_menu.reset_rotation_action.isEnabled()
        rotate_image(window_with_image, 5.0)
        window_with_image._refresh()
        assert window_with_image.image_menu.reset_rotation_action.isEnabled()


class TestPersistence:
    def test_保存して読み直しても角度が残る(self, sample_project, tmp_path):
        from manga_layout.storage import load_project, save_project

        sample_project.pages[0].panels[0].children[0].rotation = -37.5
        save_project(sample_project, tmp_path)

        restored = load_project(tmp_path)
        assert restored.pages[0].panels[0].children[0].rotation == pytest.approx(-37.5)

    def test_角度を持たない作品も開ける(self, tmp_path):
        """`rotation` は 3章の時点から形式にあるので、既存の作品は無傷。"""
        from manga_layout.model import ImageObject

        image = ImageObject.from_dict(
            {
                "id": "img_0001",
                "type": "image",
                "asset": "assets/a.png",
                "rect": {"x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0},
                "src_px": [100, 100],
            },
            "page[0].panel[0].children[0]",
        )
        assert image.rotation == 0.0


class TestImageAt:
    def test_傾けた絵の当たり判定も回る(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(0.0, 0.0, 200.0, 200.0))
        image = project.add_image(
            panel, "assets/a.png", Rect(50.0, 50.0, 100.0, 100.0), (100, 100)
        )

        assert image_at(panel, 52.0, 52.0) is image
        image.rotation = 45.0
        # 元の左上の角は、回すと絵の外へ出る
        assert image_at(panel, 52.0, 52.0) is None
        assert image_at(panel, 100.0, 100.0) is image
