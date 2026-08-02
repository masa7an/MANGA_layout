"""画面まわりの結線の検証。

表示装置なし（offscreen）で動かす。見た目そのものは確かめられないが、
「操作 → モデルの変更 → 履歴に積む → 表示の更新」がつながっているか、
Undo でモデルの実体が差し替わったあとも画面が古い参照を掴んでいないかを
確かめられる。ここが切れていると、画面上は動くのに保存すると
何も入っていない、という気づきにくい壊れ方をする。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.layout import default_panel_rect, full_page_rect
from manga_layout.storage import load_project
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import (
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    # 未保存のまま閉じると確認ダイアログが出て応答待ちで止まる。
    # 保存済みにしてから閉じる
    win.state.history.mark_saved()
    win.close()


class TestBuild:
    def test_起動できる(self, window):
        assert window.state.page_count == 1
        assert window.state.page.panels == []

    def test_道具を切り替えられる(self, window):
        window.state.set_tool(TOOL_PANEL)
        assert window._tool_actions[TOOL_PANEL].isChecked()
        window.state.set_tool(TOOL_SELECT)
        assert window._tool_actions[TOOL_SELECT].isChecked()

    def test_未保存の印が題名に出る(self, window):
        assert "*" not in window._title()
        window.add_full_page_panel()
        assert "*" in window._title()


class TestPanelEditing:
    def test_全面コマを作れる(self, window):
        window.add_full_page_panel()
        page = window.state.page
        assert len(page.panels) == 1
        assert page.panels[0].shape.as_rect() == full_page_rect(page, window.state.settings)
        assert window.state.selected_id == page.panels[0].id

    def test_ドラッグでコマを作れる(self, window):
        window.view._apply_create(Rect(20.0, 20.0, 80.0, 60.0), (20.0, 20.0))
        assert len(window.state.page.panels) == 1
        assert window.state.page.panels[0].shape.as_rect() == Rect(20.0, 20.0, 80.0, 60.0)

    def test_クリックだけなら既定の大きさで作る(self, window):
        """ドラッグと呼べない動きは「そこに置く」とみなす。

        以前はここで何も作らなかった。作られないと、押したのに
        反応が無いように見える
        """
        tiny = 1.0 / window.view.view_scale
        window.view._apply_create(Rect(60.0, 90.0, tiny, tiny), (60.0, 90.0))

        assert len(window.state.page.panels) == 1
        rect = window.state.page.panels[0].shape.as_rect()
        assert rect == default_panel_rect(window.state.page, 60.0, 90.0, window.state.settings)
        # 見えない大きさのコマは作らない（選択も削除もできなくなるため）
        assert rect.w > 1.0 and rect.h > 1.0

    def test_コマを動かせる(self, window):
        window.add_full_page_panel()
        origin = window.state.selected_panel.shape.bounds()

        window.view._apply_move(origin, origin.translated(10.0, 5.0))

        moved = window.state.selected_panel.shape.bounds()
        assert (moved.x, moved.y) == pytest.approx((origin.x + 10.0, origin.y + 5.0))

    def test_コマの大きさを変えられる(self, window):
        window.add_full_page_panel()
        window.view._apply_resize(Rect(30.0, 30.0, 100.0, 90.0))
        assert window.state.selected_panel.shape.as_rect() == Rect(30.0, 30.0, 100.0, 90.0)

    def test_コマを削除できる(self, window):
        window.add_full_page_panel()
        window.delete_panel()
        assert window.state.page.panels == []
        assert window.state.selected_panel is None

    def test_分割できる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)

        window.view._apply_split(100.0, 150.0)

        assert len(window.state.page.panels) == 2
        upper, lower = window.state.page.panels
        assert upper.shape.bounds().bottom < lower.shape.bounds().y

    def test_縦に分割できる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_V)

        window.view._apply_split(105.0, 150.0)

        left, right = window.state.page.panels
        assert left.shape.bounds().right < right.shape.bounds().x

    def test_分割できない場所では知らせるだけ(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)

        # ページの上端すぐ。上側が小さくなりすぎる
        window.view._apply_split(100.0, 16.0)

        assert len(window.state.page.panels) == 1

    def test_コマの外での分割は何も起きない(self, window):
        window.state.set_tool(TOOL_SPLIT_H)
        window.view._apply_split(100.0, 150.0)
        assert window.state.page.panels == []


def press(view, x: float, y: float) -> None:
    """mm 座標を画面座標に直して、左ボタンの押下を送る。"""
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


class TestAddPanelMode:
    """コマ追加は1回きり。追加したらすぐ編集に移る（要件定義 6.9）。

    ここが崩れると、位置や大きさを整えようとした操作が
    次のコマの追加になってしまう。
    """

    def test_追加すると選択の道具に戻る(self, window):
        window.state.set_tool(TOOL_PANEL)
        window.view._apply_create(Rect(20.0, 20.0, 60.0, 40.0), (20.0, 20.0))
        assert window.state.tool == TOOL_SELECT

    def test_追加したコマが選ばれている(self, window):
        window.state.set_tool(TOOL_PANEL)
        window.view._apply_create(Rect(20.0, 20.0, 60.0, 40.0), (20.0, 20.0))
        assert window.state.selected_id == window.state.page.panels[0].id

    def test_続けて空白を押しても追加されない(self, window):
        window.state.set_tool(TOOL_PANEL)
        window.view._apply_create(Rect(20.0, 20.0, 60.0, 40.0), (20.0, 20.0))

        press(window.view, 160.0, 250.0)  # 何も無いところ

        assert window.view._mode is None
        assert len(window.state.page.panels) == 1

    def test_空白を押すと作成が始まる(self, window):
        window.state.set_tool(TOOL_PANEL)
        press(window.view, 160.0, 250.0)
        assert window.view._mode == "create"

    def test_コマの上を押すと移動になる(self, window):
        # 追加の道具のままでも、既にあるコマは掴んで動かせる
        window.add_full_page_panel()
        window.state.set_tool(TOOL_PANEL)

        press(window.view, 105.0, 150.0)  # ページ中央＝コマの中

        assert window.view._mode == "move"
        assert len(window.state.page.panels) == 1

    def test_つまみを押すと大きさ変更になる(self, window):
        window.add_full_page_panel()
        bounds = window.state.selected_panel.shape.bounds()
        window.state.set_tool(TOOL_PANEL)

        press(window.view, bounds.x, bounds.y)  # 左上のつまみ

        assert window.view._mode == "resize"
        assert window.view._handle == "nw"


def double_click(view, x: float, y: float) -> None:
    """mm 座標を画面座標に直して、左ボタンのダブルクリックを送る。"""
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


@pytest.fixture
def png(fixture_dir) -> bytes:
    """64×48 の不透明な PNG。"""
    return (fixture_dir / "rgb_opaque.png").read_bytes()


@pytest.fixture
def window_with_image(window, png):
    """全面コマに画像を1枚置いた状態。画像が選ばれている。"""
    window.add_full_page_panel()
    panel_id = window.state.selected_panel.id
    window.state.place_image(panel_id, png)
    return window


class TestImagePlacement:
    def test_コマに置ける(self, window, png):
        window.add_full_page_panel()
        panel = window.state.selected_panel

        image = window.state.place_image(panel.id, png)

        assert window.state.page.panels[0].children == [image]
        assert image.src_px == (64, 48)
        assert window.state.selected_id == image.id

    def test_置いた直後はコマに収まる(self, window_with_image):
        """全体が見える大きさで入る。埋めるのは コマにフィット の役目。"""
        image = window_with_image.state.selected_image
        panel = window_with_image.state.page.panels[0].shape.bounds()

        assert image.rect.w <= panel.w + 1e-9
        assert image.rect.h <= panel.h + 1e-9
        assert image.rect.center == pytest.approx(panel.center)

    def test_履歴に積まれる(self, window_with_image):
        assert window_with_image.state.history.undo_label == "画像の配置"
        window_with_image.state.undo()
        assert window_with_image.state.page.panels[0].children == []

    def test_壊れた画像は断る(self, window, fixture_dir):
        from manga_layout.errors import BrokenImageError

        window.add_full_page_panel()
        panel_id = window.state.selected_panel.id
        broken = (fixture_dir / "broken.png").read_bytes()

        with pytest.raises(BrokenImageError):
            window.state.place_image(panel_id, broken)

        # 壊れたものを assets に入れない。内容ハッシュ名なので
        # 一度入れると人が見分けられなくなる
        assert len(window.state.pending_assets) == 0
        assert window.state.page.panels[0].children == []

    def test_コマ未選択なら知らせるだけ(self, window):
        window.paste_image()  # クリップボードは空、コマも無い
        assert window.state.page.panels == []


class TestImageAssets:
    """未保存のうちに貼っても、保存すれば実体が残ること。"""

    def test_未保存でも置ける(self, window_with_image):
        assert window_with_image.state.project_dir is None
        assert len(window_with_image.state.pending_assets) == 1

    def test_保存すると実体が書かれる(self, window_with_image, tmp_path, png):
        from manga_layout import AssetStore

        ref = window_with_image.state.selected_image.asset
        window_with_image.state.save(tmp_path)

        assert AssetStore(tmp_path).read(ref) == png
        assert len(window_with_image.state.pending_assets) == 0

    def test_保存して開き直しても画像が残る(self, window_with_image, tmp_path):
        from manga_layout import find_missing_assets

        window_with_image.state.save(tmp_path)
        restored = load_project(tmp_path)

        assert len(restored.pages[0].panels[0].children) == 1
        # 参照が実体を指していること。ここが切れるのが一番痛い壊れ方
        assert find_missing_assets(restored, tmp_path) == []

    def test_同じ画像を2回置いても実体は1つ(self, window, png, tmp_path):
        window.add_full_page_panel()
        panel_id = window.state.selected_panel.id
        window.state.place_image(panel_id, png)
        window.state.place_image(panel_id, png)
        window.state.save(tmp_path)

        from manga_layout import AssetStore

        assert len(AssetStore(tmp_path).list_refs()) == 1
        assert len(window.state.page.panels[0].children) == 2

    def test_別の作品を開くと前の画像を持ち越さない(self, window_with_image, tmp_path):
        from manga_layout import new_project, save_project

        save_project(new_project(), tmp_path)
        window_with_image.state.load(tmp_path)

        assert len(window_with_image.state.pending_assets) == 0
        assert len(window_with_image.state.image_cache) == 0


class TestImageSelection:
    def test_ダブルクリックで画像を選ぶ(self, window_with_image):
        image = window_with_image.state.selected_image
        window_with_image.state.select(None)

        cx, cy = image.rect.center
        double_click(window_with_image.view, cx, cy)

        assert window_with_image.state.selected_id == image.id
        assert window_with_image.state.selected_panel is None

    def test_画像の外をダブルクリックしても入らない(self, window_with_image):
        window_with_image.state.select(None)
        double_click(window_with_image.view, 2.0, 2.0)  # ページの隅、コマの外
        assert window_with_image.state.selected_image is None

    def test_Escでコマに戻る(self, window_with_image):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent

        panel_id = window_with_image.state.page.panels[0].id
        window_with_image.view.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        )
        assert window_with_image.state.selected_id == panel_id

    def test_選択中の画像を押すと画像が動く(self, window_with_image):
        # ここでコマに持ち替わると、絵を動かしたつもりでコマが動く
        image = window_with_image.state.selected_image
        cx, cy = image.rect.center

        press(window_with_image.view, cx, cy)

        assert window_with_image.view._mode == "move"
        assert window_with_image.view._origin_rect == image.rect


def press_at(view, x: float, y: float, shift: bool = False) -> None:
    """Shift の有無を指定して左ボタンの押下を送る。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    modifiers = (
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    )
    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            modifiers,
        )
    )


@pytest.fixture
def messages(window_with_image):
    """状態表示に流れた文言を順に控える。"""
    seen = []
    window_with_image.state.message.connect(seen.append)
    return seen


class TestAspectHint:
    """斜めのつまみを掴んだときの Shift の案内。

    等比リサイズは知らないと使えない機能なので、使う直前に出す。
    """

    def test_角のつまみを掴むと案内が出る(self, window_with_image, messages):
        from manga_layout.ui.canvas import ASPECT_HINT

        bounds = window_with_image.state.selected_image.rect
        press_at(window_with_image.view, bounds.right, bounds.bottom)  # 右下の角

        assert ASPECT_HINT in messages

    @pytest.mark.parametrize("corner", ["nw", "ne", "se", "sw"])
    def test_4つの角すべてで出る(self, window_with_image, messages, corner):
        from manga_layout.layout import handle_positions
        from manga_layout.ui.canvas import ASPECT_HINT

        bounds = window_with_image.state.selected_image.rect
        x, y = handle_positions(bounds)[corner]
        press_at(window_with_image.view, x, y)

        assert ASPECT_HINT in messages

    @pytest.mark.parametrize("edge", ["n", "s", "e", "w"])
    def test_辺のつまみでは出ない(self, window_with_image, messages, edge):
        """斜めのときだけ縦横が同時に変わる。辺で出すと案内が邪魔になる。"""
        from manga_layout.layout import handle_positions
        from manga_layout.ui.canvas import ASPECT_HINT

        bounds = window_with_image.state.selected_image.rect
        x, y = handle_positions(bounds)[edge]
        press_at(window_with_image.view, x, y)

        assert ASPECT_HINT not in messages

    def test_コマの角では出ない(self, window):
        """コマは絵ではないので、等比に縛る意味がない。"""
        from manga_layout.ui.canvas import ASPECT_HINT

        window.add_full_page_panel()
        seen = []
        window.state.message.connect(seen.append)

        bounds = window.state.selected_panel.shape.bounds()
        press_at(window.view, bounds.right, bounds.bottom)

        assert ASPECT_HINT not in seen

    def test_Shiftを押していれば維持中と出る(self, window_with_image, messages):
        from manga_layout.ui.canvas import ASPECT_HINT, ASPECT_HINT_HELD

        bounds = window_with_image.state.selected_image.rect
        press_at(window_with_image.view, bounds.right, bounds.bottom, shift=True)

        assert ASPECT_HINT_HELD in messages
        assert ASPECT_HINT not in messages

    def test_同じ案内を出し続けない(self, window_with_image, messages):
        """ドラッグのたびに何度も流すと、他の知らせが押し流される。"""
        from manga_layout.ui.canvas import ASPECT_HINT

        view = window_with_image.view
        bounds = window_with_image.state.selected_image.rect
        press_at(view, bounds.right, bounds.bottom)
        for offset in range(1, 6):
            drag_to(view, bounds.right + offset, bounds.bottom + offset)

        # ドラッグが実際に届いていること（届いていなければ 1 回で当然）
        assert view._scene.preview_rect != bounds
        assert messages.count(ASPECT_HINT) == 1

    def test_ドラッグ中にShiftを押すと文面が変わる(self, window_with_image, messages):
        from manga_layout.ui.canvas import ASPECT_HINT, ASPECT_HINT_HELD

        view = window_with_image.view
        bounds = window_with_image.state.selected_image.rect
        press_at(view, bounds.right, bounds.bottom)
        drag_to(view, bounds.right + 5.0, bounds.bottom + 5.0)
        drag_to(view, bounds.right + 10.0, bounds.bottom + 10.0, shift=True)

        assert messages.index(ASPECT_HINT) < messages.index(ASPECT_HINT_HELD)


def drag_to(view, x: float, y: float, shift: bool = False) -> None:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    modifiers = (
        Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier
    )
    position = QPointF(view.mapFromScene(QPointF(x, y)))
    view.mouseMoveEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseMove,
            position,
            view.viewport().mapToGlobal(position),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            modifiers,
        )
    )


class TestImageEditing:
    def test_画像だけ動かせる(self, window_with_image):
        image = window_with_image.state.selected_image
        origin = image.rect
        panel_before = window_with_image.state.page.panels[0].shape.bounds()

        window_with_image.view._apply_move(origin, origin.translated(5.0, 3.0))

        moved = window_with_image.state.selected_image.rect
        assert (moved.x, moved.y) == pytest.approx((origin.x + 5.0, origin.y + 3.0))
        # コマは動いていない
        assert window_with_image.state.page.panels[0].shape.bounds() == panel_before

    def test_画像の大きさを変えられる(self, window_with_image):
        window_with_image.view._apply_resize(Rect(20.0, 20.0, 60.0, 40.0))
        assert window_with_image.state.selected_image.rect == Rect(20.0, 20.0, 60.0, 40.0)

    def test_コマを動かすと中の画像も動く(self, window_with_image):
        page = window_with_image.state.page
        image_before = page.panels[0].children[0].rect
        window_with_image.state.select(page.panels[0].id)
        origin = page.panels[0].shape.bounds()

        window_with_image.view._apply_move(origin, origin.translated(10.0, 0.0))

        after = window_with_image.state.page.panels[0].children[0].rect
        assert after.x == pytest.approx(image_before.x + 10.0)

    def test_コマにフィットするとコマを埋める(self, window_with_image):
        panel = window_with_image.state.page.panels[0].shape.bounds()

        window_with_image.fit_image()

        rect = window_with_image.state.selected_image.rect
        assert rect.w >= panel.w - 1e-9
        assert rect.h >= panel.h - 1e-9
        assert rect.center == pytest.approx(panel.center)

    def test_フィットは縦横比を保つ(self, window_with_image):
        window_with_image.fit_image()
        rect = window_with_image.state.selected_image.rect
        assert rect.w / rect.h == pytest.approx(64 / 48)

    def test_画像未選択でフィットしても何も起きない(self, window_with_image):
        window_with_image.state.select(window_with_image.state.page.panels[0].id)
        depth = window_with_image.state.history.depth
        window_with_image.fit_image()
        assert window_with_image.state.history.depth == depth


class TestImageDeletion:
    def test_画像を消してもコマは残る(self, window_with_image):
        panel_id = window_with_image.state.page.panels[0].id

        window_with_image.delete_selected()

        assert len(window_with_image.state.page.panels) == 1
        assert window_with_image.state.page.panels[0].children == []
        # 消したあとはコマを選び直す。選択が空になると操作の続きがしづらい
        assert window_with_image.state.selected_id == panel_id

    def test_コマを消すと中の画像も消える(self, window_with_image):
        window_with_image.state.select(window_with_image.state.page.panels[0].id)
        window_with_image.delete_selected()
        assert window_with_image.state.page.panels == []

    def test_消しても実体は残る(self, window_with_image, tmp_path):
        """Undo で戻せるようにしておく。整理は利用者が選んだときだけ。"""
        from manga_layout import AssetStore

        window_with_image.state.save(tmp_path)
        ref = window_with_image.state.page.panels[0].children[0].asset

        window_with_image.state.select(window_with_image.state.page.panels[0].children[0].id)
        window_with_image.delete_selected()

        assert AssetStore(tmp_path).exists(ref)
        window_with_image.state.undo()
        assert len(window_with_image.state.page.panels[0].children) == 1


def red_png(w: int = 40, h: int = 40) -> bytes:
    """真っ赤な PNG。描かれた場所を画素で数えるために使う。"""
    from PySide6.QtGui import QColor, QImage

    from manga_layout.images import to_png_bytes

    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 0, 0))
    return to_png_bytes(image)


def render_page(window):
    """ページを 1mm = 1px で描く。mm の座標がそのまま画素の座標になる。"""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    page = window.state.page
    area = QRectF(0.0, 0.0, page.size.w, page.size.h)
    target = QImage(int(page.size.w), int(page.size.h), QImage.Format.Format_ARGB32)
    target.fill(0)

    painter = QPainter(target)
    window.view._scene.render(painter, QRectF(target.rect()), area)
    painter.end()
    return target


def red_pixels(image) -> list[tuple[int, int]]:
    found = []
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if c.red() > 200 and c.green() < 60 and c.blue() < 60:
                found.append((x, y))
    return found


class TestImageClipping:
    """コマの形での切り抜き（要件定義 6.3）。

    ここが効いていないと、はみ出した絵が隣のコマや紙の外まで描かれる。
    """

    def test_画像が実際に描かれる(self, window):
        window.add_full_page_panel()
        window.state.place_image(window.state.selected_panel.id, red_png())
        assert len(red_pixels(render_page(window))) > 0

    def test_はみ出した分はコマの外に出ない(self, window):
        # コマをページの左上 1/4 に作り、画像をページ全体より大きくする
        with window.state.edit("コマの追加") as project:
            panel = project.add_panel(project.pages[0], Rect(20.0, 20.0, 60.0, 60.0))
        window.state.select(panel.id)
        window.state.place_image(panel.id, red_png())

        window.view._apply_resize(Rect(-50.0, -50.0, 400.0, 500.0))

        outside = [
            (x, y)
            for x, y in red_pixels(render_page(window))
            if not (20 <= x <= 80 and 20 <= y <= 80)
        ]
        assert outside == [], f"コマの外に {len(outside)} 画素はみ出した"

    def test_コマの中は埋まる(self, window):
        with window.state.edit("コマの追加") as project:
            panel = project.add_panel(project.pages[0], Rect(20.0, 20.0, 60.0, 60.0))
        window.state.select(panel.id)
        window.state.place_image(panel.id, red_png())
        window.view._apply_resize(Rect(-50.0, -50.0, 400.0, 500.0))

        # コマの中央付近は赤で埋まっているはず
        painted = set(red_pixels(render_page(window)))
        assert (50, 50) in painted


class TestImageDrawing:
    def test_実体が無くても描ける(self, window_with_image):
        """開いた作品の画像が1枚欠けていても、そこだけ枠で示して続行する。

        1枚欠けただけで作品全体が開けないのは割に合わない。
        """
        from manga_layout.assets import PendingAssets

        window_with_image.state.pending_assets = PendingAssets()
        window_with_image.state.image_cache.clear()

        render_page(window_with_image)  # 例外が出なければよい
        assert window_with_image.state.preview("assets/missing.png") is None


class TestHistoryWiring:
    def test_操作が履歴に積まれる(self, window):
        window.add_full_page_panel()
        assert window.state.history.can_undo
        assert window.state.history.undo_label == "コマの追加"

    def test_元に戻せる(self, window):
        window.add_full_page_panel()
        window.state.undo()
        assert window.state.page.panels == []

    def test_戻したあと画面が新しい実体を見る(self, window):
        # Undo でプロジェクトの実体が差し替わる。画面が古い Page を
        # 掴んだままだと、ここで元に戻っていないように見える
        window.add_full_page_panel()
        window.view._apply_move(
            window.state.selected_panel.shape.bounds(),
            window.state.selected_panel.shape.bounds().translated(20.0, 0.0),
        )
        moved_x = window.state.page.panels[0].shape.bounds().x

        window.state.undo()

        assert window.state.page.panels[0].shape.bounds().x != moved_x
        assert window.view._scene.state.page is window.state.page

    def test_やり直せる(self, window):
        window.add_full_page_panel()
        window.state.undo()
        window.state.redo()
        assert len(window.state.page.panels) == 1

    def test_選択したコマが消えても落ちない(self, window):
        window.add_full_page_panel()
        panel_id = window.state.selected_id
        window.state.undo()

        assert window.state.selected_id == panel_id
        assert window.state.selected_panel is None
        window._refresh()  # 選択が無効でも表示更新が通ること

    def test_変化しない操作は積まない(self, window):
        window.add_full_page_panel()
        depth = window.state.history.depth
        origin = window.state.selected_panel.shape.bounds()

        window.view._apply_move(origin, origin)
        window.view._apply_resize(origin)

        assert window.state.history.depth == depth


class TestPages:
    def test_ページを追加すると移動する(self, window):
        window.add_page()
        assert window.state.page_count == 2
        assert window.state.page_index == 1

    def test_ページを行き来できる(self, window):
        window.add_page()
        window.prev_page()
        assert window.state.page_index == 0
        window.next_page()
        assert window.state.page_index == 1

    def test_端では止まる(self, window):
        window.prev_page()
        assert window.state.page_index == 0
        window.next_page()
        assert window.state.page_index == 0

    def test_ページ移動で選択が外れる(self, window):
        window.add_full_page_panel()
        window.add_page()
        assert window.state.selected_id is None


class TestFile:
    def test_保存して開き直せる(self, window, tmp_path):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)
        window.view._apply_split(100.0, 150.0)

        window.state.save(tmp_path)
        assert not window.state.is_dirty

        restored = load_project(tmp_path)
        assert len(restored.pages[0].panels) == 2
        assert restored.load_warnings == []

    def test_保存後は未保存の印が消える(self, window, tmp_path):
        window.add_full_page_panel()
        assert "*" in window._title()
        window._write(tmp_path)
        assert "*" not in window._title()

    def test_未保存なら閉じる前に確認する(self, qapp, monkeypatch):
        """作業を失わせないための最後の砦。ここが黙って通ると被害が大きい。"""
        from PySide6.QtWidgets import QMessageBox

        class 取り消しを返す確認:
            StandardButton = QMessageBox.StandardButton
            asked = 0

            @classmethod
            def question(cls, *args, **kwargs):
                cls.asked += 1
                return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr("manga_layout.ui.window.QMessageBox", 取り消しを返す確認)

        win = MainWindow(EditorState())
        win.add_full_page_panel()
        assert win.state.is_dirty

        assert win._confirm_discard() is False
        assert 取り消しを返す確認.asked == 1

        win.state.history.mark_saved()
        win.close()

    def test_保存済みなら確認しない(self, window):
        assert window._confirm_discard() is True

    def test_サンプル作品を開ける(self, window):
        from tests.conftest import REPO_ROOT

        sample = REPO_ROOT / "samples" / "basic"
        warnings = window.state.load(sample)

        assert warnings == []
        assert window.state.page_count == 2
        assert len(window.state.page.panels) == 4


class TestSlantSplitUI:
    """斜めの縦割りの結線。

    形そのものは tests/test_slant.py で固めてある。ここでは
    「道具 → 分割 → 履歴 → 選択」がつながっているかを見る。
    """

    def _split(self, window, x: float = 105.0):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._apply_split(x, 150.0)
        return window.state.page

    def test_斜めに割れて組ができる(self, window):
        page = self._split(window)
        assert len(page.panels) == 2
        assert len(page.slant_pairs) == 1
        # どちらも軸並行の矩形ではなくなっている
        assert all(p.shape.as_rect() is None for p in page.panels)

    def test_割ったあとも履歴で戻せる(self, window):
        self._split(window)
        window.state.undo()
        assert len(window.state.page.panels) == 1
        assert window.state.page.slant_pairs == []
        window.state.redo()
        assert len(window.state.page.slant_pairs) == 1

    def test_選択枠は組の外側の矩形になる(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        bounds = window.state.selected_bounds
        outer = page.slant_bounds(page.slant_pairs[0])
        assert (bounds.x, bounds.y, bounds.w, bounds.h) == pytest.approx(
            (outer.x, outer.y, outer.w, outer.h)
        )

    def test_リサイズは2枚まとめて効く(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        window.view._apply_resize(Rect(20.0, 20.0, 150.0, 200.0))

        page = window.state.page
        outer = page.slant_bounds(page.slant_pairs[0])
        assert (outer.x, outer.y, outer.w, outer.h) == pytest.approx(
            (20.0, 20.0, 150.0, 200.0)
        )
        assert all(p.shape.as_rect() is None for p in page.panels)

    def test_移動は2枚まとめて効く(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        before = page.panels[1].shape.bounds()
        origin = window.state.selected_bounds
        window.view._apply_move(origin, origin.translated(8.0, 4.0))

        after = window.state.page.panels[1].shape.bounds()
        assert (after.x, after.y) == pytest.approx((before.x + 8.0, before.y + 4.0))

    def test_向きを反転できる(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        before = page.slant_pairs[0].direction

        window.flip_slant()

        assert window.state.page.slant_pairs[0].direction != before
        assert window.slant_flip_action.isEnabled()

    def test_斜めでないコマでは反転が選べない(self, window):
        window.add_full_page_panel()
        assert not window.slant_flip_action.isEnabled()

    def test_下見の分割線が斜めになる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._update_split_preview(105.0, 150.0)

        (x1, _), (x2, _) = window.view._scene.split_preview
        assert x1 != pytest.approx(x2)

    def test_割れない場所では知らせるだけ(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._apply_split(18.0, 150.0)
        assert len(window.state.page.panels) == 1
        assert window.state.page.slant_pairs == []
