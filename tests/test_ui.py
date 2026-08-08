"""画面まわりの結線の検証。

表示装置なし（offscreen）で動かす。見た目そのものは確かめられないが、
「操作 → モデルの変更 → 履歴に積む → 表示の更新」がつながっているか、
Undo でモデルの実体が差し替わったあとも画面が古い参照を掴んでいないかを
確かめられる。ここが切れていると、画面上は動くのに保存すると
何も入っていない、という気づきにくい壊れ方をする。
"""

from __future__ import annotations

import json

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

        window.view._apply_split(620.0, 877.0)

        assert len(window.state.page.panels) == 2
        upper, lower = window.state.page.panels
        assert upper.shape.bounds().bottom < lower.shape.bounds().y

    def test_縦に分割できる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_V)

        window.view._apply_split(620.0, 877.0)

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
        window.view._apply_split(620.0, 877.0)
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


def release(view, x: float, y: float) -> None:
    """左ボタンの離しを送る（→ `press` と対）。"""
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


def move_to(view, x: float, y: float) -> None:
    """左ボタンを押したままの移動を送る（→ `press` と対）。"""
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

        assert window.view._drag is None
        assert len(window.state.page.panels) == 1

    def test_空白を押すと作成が始まる(self, window):
        from manga_layout.ui.canvas import CreatePanelDrag

        window.state.set_tool(TOOL_PANEL)
        press(window.view, 160.0, 250.0)
        assert isinstance(window.view._drag, CreatePanelDrag)

    def test_コマの上を押すと移動になる(self, window):
        from manga_layout.ui.canvas import MoveDrag

        # 追加の道具のままでも、既にあるコマは掴んで動かせる
        window.add_full_page_panel()
        window.state.set_tool(TOOL_PANEL)

        press(window.view, 105.0, 150.0)  # ページ中央＝コマの中

        assert isinstance(window.view._drag, MoveDrag)
        assert len(window.state.page.panels) == 1

    def test_つまみを押すと大きさ変更になる(self, window):
        from manga_layout.ui.canvas import ResizeDrag

        window.add_full_page_panel()
        bounds = window.state.selected_panel.shape.bounds()
        window.state.set_tool(TOOL_PANEL)

        press(window.view, bounds.x, bounds.y)  # 左上のつまみ

        assert isinstance(window.view._drag, ResizeDrag)
        assert window.view._drag.handle == "nw"


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

    def test_同じコマに何枚でも重ねられる(self, window, png, fixture_dir):
        """背景の絵の上にキャラの絵を置く使い方（→ `open_image_file`）。"""
        window.add_full_page_panel()
        panel_id = window.state.selected_panel.id

        window.state.place_image(panel_id, png)
        window.state.place_image(panel_id, (fixture_dir / "gray8.png").read_bytes())

        children = window.state.page.panels[0].children
        assert len(children) == 2
        # あとから置いたほうが手前
        assert children[1].z > children[0].z


class TestImageReplace:
    """画像1枚を別のファイルの絵に入れ替える（→ `EditorState.replace_image`）。"""

    @pytest.fixture
    def gray(self, fixture_dir) -> bytes:
        """差し替え先。`png`（64×48）と縦横の違うもの。"""
        return (fixture_dir / "gray8.png").read_bytes()

    def test_前の絵が消えて1枚になる(self, window_with_image, gray):
        state = window_with_image.state
        before = state.selected_image

        after = state.replace_image(before.id, gray)

        children = state.page.panels[0].children
        assert children == [after]
        assert after.id != before.id
        assert after.asset != before.asset
        assert state.selected_id == after.id

    def test_前の絵のキャッシュを手放す(self, window_with_image, gray):
        """`ImageCache.forget` はあったが、呼ぶ場所が無かった
        （2026-08-08 に発見。→ `TestImageDeletion` の同種テストと対）。
        """
        state = window_with_image.state
        before = state.selected_image
        old_ref = before.asset
        assert state.preview(old_ref) is not None

        state.replace_image(before.id, gray)

        assert old_ref not in state.image_cache._items

    def test_重なり順を引き継ぐ(self, window, png, gray):
        """背景を差し替えても、手前のキャラの前に出てこないこと。

        置き直す形（末尾に足す）にすると、ここが逆になる。
        """
        window.add_full_page_panel()
        panel_id = window.state.selected_panel.id
        back = window.state.place_image(panel_id, png)
        front = window.state.place_image(panel_id, gray)

        replaced = window.state.replace_image(back.id, gray)

        assert replaced.z < front.z
        # 手前の1枚はそのまま残る
        ids = {c.id for c in window.state.page.panels[0].children}
        assert front.id in ids and len(ids) == 2

    def test_差し替えた絵もコマに収まる(self, window_with_image, gray):
        """置いたときと同じ「収める」から始める。埋めるのはフィットの役目。"""
        state = window_with_image.state
        image = state.replace_image(state.selected_image.id, gray)
        panel = state.page.panels[0].shape.bounds()

        assert image.rect.w <= panel.w + 1e-9
        assert image.rect.h <= panel.h + 1e-9
        assert image.rect.center == pytest.approx(panel.center)

    def test_履歴は1手(self, window_with_image, gray):
        state = window_with_image.state
        before = state.selected_image

        state.replace_image(before.id, gray)

        assert state.history.undo_label == "画像の差し替え"
        state.undo()
        children = state.page.panels[0].children
        assert len(children) == 1
        assert children[0].asset == before.asset

    def test_壊れた画像なら元の絵が残る(self, window_with_image, fixture_dir):
        from manga_layout.errors import BrokenImageError

        state = window_with_image.state
        before = state.selected_image
        broken = (fixture_dir / "broken.png").read_bytes()

        with pytest.raises(BrokenImageError):
            state.replace_image(before.id, broken)

        assert state.page.panels[0].children == [before]

    def test_無い画像を指しても何も起きない(self, window_with_image, gray):
        state = window_with_image.state
        assert state.replace_image("img-無い", gray) is None
        assert len(state.page.panels[0].children) == 1


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

    def test_上書き保存では実体を書き直さない(
        self, window_with_image, tmp_path, monkeypatch
    ):
        """同じフォルダへの保存が、毎回 assets/ を書き直さないこと。

        別名保存のために足した写し取り（`_carry_assets_to`）は、
        保存のたびに通る。既にあるものを読み直して書き直すようになると、
        絵が増えるほど普段の保存が重くなる。
        """
        from manga_layout import AssetStore
        from manga_layout import assets as assets_module

        window_with_image.state.save(tmp_path)

        writes: list = []
        real = assets_module._atomic_write_bytes
        monkeypatch.setattr(
            assets_module,
            "_atomic_write_bytes",
            lambda path, data: (writes.append(path), real(path, data))[1],
        )
        window_with_image.state.save(tmp_path)

        assert writes == []
        # 素通りしただけで、実体は残っている
        assert AssetStore(tmp_path).read(window_with_image.state.selected_image.asset)

    def test_上書き保存で足した画像だけが増える(self, window_with_image, png_bytes, tmp_path):
        """保存済みの作品に貼った画像も、上書き保存後に残ること。"""
        from manga_layout import AssetStore, find_missing_assets

        first = window_with_image.state.selected_image.asset
        window_with_image.state.save(tmp_path)

        panel_id = window_with_image.state.page.panels[0].id
        added = window_with_image.state.place_image(panel_id, png_bytes).asset
        window_with_image.state.save(tmp_path)

        assert AssetStore(tmp_path).list_refs() == sorted([first, added])
        assert find_missing_assets(load_project(tmp_path), tmp_path) == []

    def test_整理した未使用画像は上書き保存で戻らない(
        self, window_with_image, png_bytes, tmp_path
    ):
        """「未使用ファイルを整理」の結果を、次の保存が取り消さないこと。"""
        from manga_layout import AssetStore, prune_unused_assets

        panel_id = window_with_image.state.page.panels[0].id
        unused = window_with_image.state.place_image(panel_id, png_bytes).asset
        window_with_image.state.save(tmp_path)
        window_with_image.state.undo()  # 2枚目を置く前まで戻す
        assert prune_unused_assets(window_with_image.state.project, tmp_path) == [unused]

        window_with_image.state.save(tmp_path)

        assert unused not in AssetStore(tmp_path).list_refs()
        assert (tmp_path / "assets" / "_unused").is_dir()

    def test_別名保存で実体も新しい保存先へ移る(self, window_with_image, tmp_path):
        """保存済みの作品を別のフォルダへ保存し直したとき。

        預かり分（pending_assets）は1度目の保存で空になるので、
        そのままでは2度目の保存先の assets/ が空になり、
        project.json の参照が全部切れる。
        """
        from manga_layout import AssetStore, find_missing_assets

        ref = window_with_image.state.selected_image.asset
        first = tmp_path / "元"
        second = tmp_path / "別名"
        window_with_image.state.save(first)

        window_with_image.state.save(second)

        assert AssetStore(second).read(ref) == AssetStore(first).read(ref)
        assert find_missing_assets(load_project(second), second) == []
        # 元のフォルダは触らない
        assert AssetStore(first).list_refs() == [ref]

    def test_別名保存で参照の無い画像は運ばない(self, window_with_image, tmp_path):
        """「未使用ファイルを整理」で片付けたものが別名保存で戻らないこと。"""
        from manga_layout import AssetStore

        first = tmp_path / "元"
        second = tmp_path / "別名"
        window_with_image.state.save(first)
        window_with_image.state.undo()  # 画像を置く前まで戻す

        window_with_image.state.save(second)

        assert window_with_image.state.project.referenced_assets() == set()
        assert AssetStore(second).list_refs() == []
        assert len(AssetStore(first).list_refs()) == 1

    def test_project_json書き込みが失敗しても預かり分は残る(
        self, window_with_image, tmp_path, monkeypatch
    ):
        """実体は書けたが project.json の書き込みで失敗したとき。

        以前は画像実体を書き出すのと同時に控え（`pending_assets`）を
        手放していたため、ここで失敗すると控えが空のまま保存だけ失敗した
        扱いになっていた。続けて**別の場所**へ保存し直すと、実体が
        書かれないまま project.json だけができ、参照が切れていた
        （2026-08-08 に発見）。
        """
        from manga_layout.ui import state as state_module

        ref = window_with_image.state.selected_image.asset
        first = tmp_path / "元"

        def 失敗する保存(*args, **kwargs):
            raise OSError("模擬した書き込み失敗")

        monkeypatch.setattr(state_module, "save_project", 失敗する保存)
        with pytest.raises(OSError):
            window_with_image.state.save(first)

        assert len(window_with_image.state.pending_assets) == 1
        assert ref in window_with_image.state.pending_assets

    def test_失敗のあと別の場所へ保存し直しても実体が書かれる(
        self, window_with_image, png, tmp_path, monkeypatch
    ):
        """↑の続き。控えが残っていれば、次の保存でちゃんと書かれる。"""
        from manga_layout import AssetStore, find_missing_assets
        from manga_layout.ui import state as state_module

        ref = window_with_image.state.selected_image.asset
        first = tmp_path / "元"
        second = tmp_path / "別名"

        real_save_project = state_module.save_project
        calls = []

        def 一度だけ失敗する保存(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise OSError("模擬した書き込み失敗")
            return real_save_project(*args, **kwargs)

        monkeypatch.setattr(state_module, "save_project", 一度だけ失敗する保存)
        with pytest.raises(OSError):
            window_with_image.state.save(first)

        window_with_image.state.save(second)

        # 実体は project.json より先に書くので、失敗した保存先（元）にも
        # 残る（無害な余り）。**肝心なのは、控えが生きていたおかげで
        # 「別の場所」（second）にも実体がちゃんと書かれること**
        assert AssetStore(second).read(ref) == png
        assert find_missing_assets(load_project(second), second) == []
        assert len(window_with_image.state.pending_assets) == 0

    def test_元の実体が欠けていても別名保存は止まらない(
        self, window, png, png_bytes, tmp_path
    ):
        """1枚読めなかっただけで保存ごと失敗すると、残りまで失う。"""
        from manga_layout import AssetStore, find_missing_assets

        window.add_full_page_panel()
        panel_id = window.state.selected_panel.id
        window.state.place_image(panel_id, png)
        lost = window.state.place_image(panel_id, png_bytes).asset
        first = tmp_path / "元"
        second = tmp_path / "別名"
        window.state.save(first)
        AssetStore(first).resolve(lost).unlink()  # 元の assets/ が欠けた状態

        window.state.save(second)

        assert (second / "project.json").is_file()
        # 読めた分は運べている。欠けた分は「抜けチェック」で見つけられる
        assert AssetStore(second).list_refs() == sorted(
            r for r in window.state.project.referenced_assets() if r != lost
        )
        assert find_missing_assets(load_project(second), second) == [lost]

    def test_別の作品を開くと前の画像を持ち越さない(self, window_with_image, tmp_path):
        from manga_layout import new_project, save_project

        save_project(new_project(), tmp_path)
        window_with_image.state.load(tmp_path)

        assert len(window_with_image.state.pending_assets) == 0
        assert len(window_with_image.state.image_cache) == 0


class Test未使用ファイルを整理:
    """メニューの「未使用ファイルを整理」（`MainWindow.prune_assets`）。"""

    def test_整理できないと分かるエラーになる(
        self, window_with_image, png_bytes, tmp_path, monkeypatch
    ):
        """対象の画像が他アプリで開かれたままなど、移動そのものが失敗する場合。

        以前はここが無防備で、Qt のスロットの中で例外が漏れるだけだった
        （2026-08-08 に発見）。
        """
        from PySide6.QtWidgets import QMessageBox

        panel_id = window_with_image.state.page.panels[0].id
        window_with_image.state.place_image(panel_id, png_bytes)
        window_with_image.state.save(tmp_path)
        window_with_image.state.undo()  # 2枚目を置く前まで戻し、未使用を作る
        window_with_image.state.save(tmp_path)  # 戻した状態を保存し直し、未保存を消す

        shown: list[str] = []
        monkeypatch.setattr(
            "manga_layout.ui.window.prune_unused_assets",
            lambda *a, **k: (_ for _ in ()).throw(OSError("模擬した移動失敗")),
        )
        monkeypatch.setattr(
            QMessageBox,
            "critical",
            lambda self, title, text: (shown.append(text), QMessageBox.StandardButton.Ok)[1],
        )

        window_with_image.prune_assets()  # 例外を投げずに終わること

        assert shown and "模擬した移動失敗" in shown[0]


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
        from manga_layout.ui.canvas import MoveDrag

        # ここでコマに持ち替わると、絵を動かしたつもりでコマが動く
        image = window_with_image.state.selected_image
        cx, cy = image.rect.center

        press(window_with_image.view, cx, cy)

        assert isinstance(window_with_image.view._drag, MoveDrag)
        assert window_with_image.view._drag.origin_rect == image.rect


class TestImageOverflowSelection:
    """選択中の画像が、切り抜かれて見えない部分でも掴めていた問題。

    「コマにフィット」した画像はコマの縁を越えて広がるのが普通で、隙間を
    挟んだ隣のコマまで重なることもある。以前は「画像の矩形内 かつ
    どこかのコマの上」としか見ておらず、**その画像自身のコマかどうかを
    見ていなかった**ため、隣のコマを選ぼうとしたクリックが見えない画像を
    掴んだまま動かしてしまっていた（2026-08-08 に発見）。
    """

    @pytest.fixture
    def two_panels(self, window, png):
        """隙間を挟んで隣り合う2つのコマ。左（A）に画像を置き、右（B）と
        その間の隙間（x: 90〜110、どちらのコマでもない）まではみ出させる。
        """
        with window.state.edit("コマの追加") as project:
            panel_a = project.add_panel(project.pages[0], Rect(0.0, 0.0, 90.0, 100.0))
            panel_b = project.add_panel(project.pages[0], Rect(110.0, 0.0, 90.0, 100.0))
        window.state.select(panel_a.id)
        window.state.place_image(panel_a.id, png)
        # コマ A の縁（x=90）を越えて、隙間とコマ B の領域まで広げる
        window.view._apply_resize(Rect(50.0, 20.0, 100.0, 60.0))
        return window

    def test_はみ出した部分は隣のコマとして選ばれる(self, two_panels):
        panel_b = two_panels.state.page.panels[1]
        image = two_panels.state.selected_image
        # コマ B の内側で、はみ出した画像の矩形とも重なる点
        assert image.rect.x < 110.0 < image.rect.right
        px, py = 130.0, 50.0

        press(two_panels.view, px, py)

        assert two_panels.state.selected_id == panel_b.id

    def test_自分のコマの中では従来どおり画像のまま(self, two_panels):
        """指摘の前後で壊してはいけない、既存の正常系。"""
        from manga_layout.ui.canvas import MoveDrag

        image = two_panels.state.selected_image
        # コマ A の内側（はみ出していない側）
        px, py = 70.0, 50.0
        assert px < 90.0

        press(two_panels.view, px, py)

        assert two_panels.state.selected_id == image.id
        assert isinstance(two_panels.view._drag, MoveDrag)

    def test_どちらのコマでもない隙間では選ばれない(self, two_panels):
        """どちらのコマの中でもない場所は、画像のはみ出しがあっても掴めない。"""
        image = two_panels.state.selected_image
        px, py = 100.0, 50.0  # 隙間（90〜110）の中
        assert image.rect.x < px < image.rect.right

        press(two_panels.view, px, py)

        assert two_panels.state.selected_id is None

    def test_カーソルも同じ判定を通す(self, two_panels):
        """掴めない場所で「動かせる」形を出さない（→ `_update_cursor`）。

        以前のカーソル側の判定はコマの所属をまったく見ておらず、画像の
        矩形に入っているかだけで「動かせる」形を出していた。隙間（どちらの
        コマでもない）でも動かせると偽っていた。
        """
        from PySide6.QtCore import Qt

        two_panels.view._update_cursor(100.0, 50.0)  # 隙間の中

        assert two_panels.view.viewport().cursor().shape() != Qt.CursorShape.SizeAllCursor


class TestImageMoveGuard:
    """画像がコマの外へ完全に出て、二度と選べなくなる移動を弾く。

    `TestImageOverflowSelection` と同根の問題。選び直しの入り口を直しても、
    移動そのものに制約が無ければ、画像は先に「見えない孤児」になれてしまう
    （2026-08-08 に発見）。
    """

    def test_コマの外へ完全に出す移動は弾かれる(self, window_with_image):
        image = window_with_image.state.selected_image
        panel = window_with_image.state.page.panels[0]
        origin = image.rect
        # 右端をコマの右縁より外へ出す。画像の大きさによらず、
        # これで重なりは確実に無くなる
        final = origin.translated(panel.shape.bounds().right - origin.x + 10.0, 0.0)

        window_with_image.view._apply_move(origin, final)

        assert window_with_image.state.selected_image.rect == origin

    def test_理由を状態表示に出す(self, window_with_image):
        image = window_with_image.state.selected_image
        panel = window_with_image.state.page.panels[0]
        origin = image.rect
        final = origin.translated(panel.shape.bounds().right - origin.x + 10.0, 0.0)

        window_with_image.view._apply_move(origin, final)

        assert "動かせません" in window_with_image.statusBar().currentMessage()

    def test_履歴には積まれない(self, window_with_image):
        image = window_with_image.state.selected_image
        panel = window_with_image.state.page.panels[0]
        origin = image.rect
        final = origin.translated(panel.shape.bounds().right - origin.x + 10.0, 0.0)
        depth = window_with_image.state.history.depth

        window_with_image.view._apply_move(origin, final)

        assert window_with_image.state.history.depth == depth

    def test_縁を大きく越えるだけなら今までどおり動かせる(self, window_with_image):
        """指摘の前後で壊してはいけない、既存の正常系。

        重なりが少しでも残るはみ出しは制約しない
        （→ `layout.image_orphaned_at`）。
        """
        image = window_with_image.state.selected_image
        panel = window_with_image.state.page.panels[0]
        origin = image.rect
        bounds = panel.shape.bounds()
        # 右へ大きくはみ出すが、左端は残ってコマとまだ 5px 重なる
        final = Rect(bounds.right - 5.0, origin.y, origin.w, origin.h)

        window_with_image.view._apply_move(origin, final)

        assert window_with_image.state.selected_image.rect == final


class Testドラッグ中のUndo:
    """マウスを掴んだまま Ctrl+Z（Undo）を押した場合。

    以前は掴んでいた画像がその Undo で消えても `self._drag` が残ったまま
    になり、離した瞬間に `page.panel()` などが KeyError を投げていた
    （2026-08-08 に発見）。`state.changed` はドラッグ中には確定操作以外で
    発火しないので、Undo/Redo のような外からの変化を合図にドラッグ自体を
    打ち切る。
    """

    def test_対象が消えても離しても落ちない(self, window_with_image):
        image = window_with_image.state.selected_image
        cx, cy = image.rect.center

        press(window_with_image.view, cx, cy)
        # 実際に動かして preview_rect を origin から動かす。動いていないと
        # `_apply_move` が「変化なし」の早期リターンで抜け、再現にならない
        move_to(window_with_image.view, cx + 20.0, cy + 20.0)
        assert window_with_image.view._drag is not None

        window_with_image.state.undo()  # 画像の配置を取り消す
        assert window_with_image.state.selected_image is None
        assert window_with_image.view._drag is None, "モデルの変化でドラッグを打ち切っていない"

        release(window_with_image.view, cx + 20.0, cy + 20.0)  # 例外を投げずに終わること

    def test_通常のドラッグ中はchangedが飛ばない(self, window_with_image):
        """打ち切りの前提（→ 上のクラスの docstring）が崩れていないこと。"""
        image = window_with_image.state.selected_image
        cx, cy = image.rect.center
        seen = []
        window_with_image.state.changed.connect(lambda: seen.append(1))

        press(window_with_image.view, cx, cy)
        assert window_with_image.view._drag is not None
        assert seen == []


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

    def test_Shiftは今の形ではなく元画像の比に戻す(self, window_with_image):
        """文言どおり「維持」ではなく「元に戻す」であること。

        自由リサイズで既に歪ませたあとに Shift で掴むと、**今の（歪んだ）
        形を保つのではなく、元画像（`src_px`）の比へ戻る**。以前は文言が
        「維持」だったため、今の形を保つと誤読させていた
        （要件定義 5章の記載どおりの挙動で、2026-08-08 に文言だけ直した）。
        """
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        from manga_layout.layout import aspect_of

        image = window_with_image.state.selected_image
        # 自由リサイズで正方形に歪める（元画像は 64×48、比は 4:3）
        window_with_image.view._apply_resize(Rect(image.rect.x, image.rect.y, 100.0, 100.0))
        distorted = window_with_image.state.selected_image.rect
        assert distorted.w / distorted.h == pytest.approx(1.0)

        shift_event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(0.0, 0.0),
            QPointF(0.0, 0.0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.ShiftModifier,
        )

        locked = window_with_image.view._locked_aspect(shift_event)

        assert locked == pytest.approx(aspect_of(image.src_px))
        assert locked != pytest.approx(distorted.w / distorted.h)


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

    def test_他で使われていなければキャッシュも手放す(self, window_with_image):
        """`ImageCache.forget` はあったが、呼ぶ場所が無く使われていな
        かった（2026-08-08 に発見）。削除した画像のプレビューが縮小版の
        キャッシュに残り続けていた（実害はメモリだけで、実体は消えない
        ので描画結果が壊れることはない）。
        """
        image = window_with_image.state.page.panels[0].children[0]
        ref = image.asset
        # 先に一度描かせて、キャッシュに載せておく
        assert window_with_image.state.preview(ref) is not None
        assert ref in window_with_image.state.image_cache._items

        window_with_image.state.select(image.id)
        window_with_image.delete_selected()

        assert ref not in window_with_image.state.image_cache._items

    def test_他でも使われていればキャッシュは手放さない(self, window_with_image, png):
        """同じ絵を2箇所で使っている場合、片方を消しただけで
        もう片方まで展開し直しになってはいけない。
        """
        panel_id = window_with_image.state.page.panels[0].id
        first = window_with_image.state.page.panels[0].children[0]
        ref = first.asset
        second = window_with_image.state.place_image(panel_id, png)  # 同じ png、同じ ref
        assert second.asset == ref
        assert window_with_image.state.preview(ref) is not None

        window_with_image.state.select(first.id)
        window_with_image.delete_selected()

        assert ref in window_with_image.state.image_cache._items
        assert window_with_image.state.page.panels[0].children == [second]


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
        window.view._apply_split(620.0, 877.0)

        window.state.save(tmp_path)
        assert not window.state.is_dirty

        restored = load_project(tmp_path)
        assert len(restored.pages[0].panels) == 2
        assert restored.load_warnings == []

    def test_保存後は未保存の印が消える(self, window, tmp_path):
        window.add_full_page_panel()
        assert "*" in window._title()
        window.files.write(tmp_path)
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

        monkeypatch.setattr("manga_layout.ui.project_io.QMessageBox", 取り消しを返す確認)

        win = MainWindow(EditorState())
        win.add_full_page_panel()
        assert win.state.is_dirty

        assert win.files.confirm_discard() is False
        assert 取り消しを返す確認.asked == 1

        win.state.history.mark_saved()
        win.close()

    def test_保存済みなら確認しない(self, window):
        assert window.files.confirm_discard() is True

    def test_サンプル作品を開ける(self, window):
        """`samples/basic` は version 1（mm）のまま置いてある。

        **移行が効いていることの実地確認を兼ねている。** 変換したうえで
        「換算して開いた」と知らせる。
        """
        from tests.conftest import REPO_ROOT

        sample = REPO_ROOT / "samples" / "basic"
        warnings = window.state.load(sample)

        assert any("px に換算" in w for w in warnings)
        assert window.state.page.size.w == pytest.approx(1240.157, abs=0.01)
        assert window.state.page_count == 2
        assert len(window.state.page.panels) == 4


class TestAutosave:
    """タイマーからの自動バックアップ（要件定義 6.6）。"""

    def test_保存先が決まっていなければ何もしない(self, window):
        """一度も保存していない作品。退避先が無く、貼った画像もまだ
        ディスクに無いので、JSON だけ書いても参照先の無い退避になる。"""
        window.add_full_page_panel()
        assert window.state.project_dir is None

        assert window.state.autosave() is None

    def test_変化があれば退避する(self, window, tmp_path):
        window.state.save(tmp_path)
        window.add_full_page_panel()

        path = window.state.autosave()

        assert path is not None
        assert path.name == "autosave.1.json"
        written = json.loads(path.read_text(encoding="utf-8"))
        assert len(written["pages"][0]["panels"]) == 1

    def test_変化が無ければ書かない(self, window, tmp_path):
        window.state.save(tmp_path)
        window.add_full_page_panel()
        window.state.autosave()

        assert window.state.autosave() is None

    def test_変えて元に戻せば書かない(self, window, tmp_path):
        window.state.save(tmp_path)
        window.state.autosave()

        window.add_full_page_panel()
        window.state.undo()

        assert window.state.autosave() is None

    def test_保存した直後は書かない(self, window, tmp_path):
        # 保存した内容は project.json にそのまま入っている
        window.add_full_page_panel()
        window.state.save(tmp_path)

        assert window.state.autosave() is None

    def test_退避しても未保存の印は残る(self, window, tmp_path):
        """本体を書き換えていない以上、保存の確認は出さなければならない。"""
        window.state.save(tmp_path)
        window.add_full_page_panel()

        window.state.autosave()

        assert window.state.is_dirty
        assert "*" in window._title()

    def test_タイマーが実際に回って退避する(self, window, tmp_path):
        """**ここが 2026-08-05 に抜けていた穴。**

        それまでのテストは `autosave()` を直接呼んでいたため、
        「タイマーの合図が `_autosave` につながっているか」を1つも
        確かめていなかった。結線が切れていても全部通ってしまう。
        """
        from PySide6.QtTest import QTest

        window.state.save(tmp_path)
        window.add_full_page_panel()

        window.files.autosave_timer.setInterval(30)
        window.files.autosave_timer.start()
        QTest.qWait(300)

        assert (tmp_path / "backup" / "autosave.1.json").is_file()

    def test_間隔を設定から取る(self, qapp, tmp_path, monkeypatch):
        from manga_layout.settings import AppSettings

        monkeypatch.setattr(
            "manga_layout.ui.window.load_settings",
            lambda path: AppSettings(autosave_interval_sec=30),
        )
        win = MainWindow(EditorState())
        try:
            assert win.files.autosave_timer.interval() == 30_000
            assert win.files.autosave_timer.isActive()
        finally:
            win.state.history.mark_saved()
            win.close()

    def test_発火のたびに間隔を読み直す(self, qapp, tmp_path):
        """`default_parent` と同じく、使う直前（＝発火のたび）に読み直す。

        以前は起動時に一度読むだけだったため、`settings.json` を書き換えて
        も、開き直すまでタイマーの間隔に反映されなかった
        （2026-08-08 に発見）。
        """
        from manga_layout.settings import AppSettings, save_settings

        win = MainWindow(EditorState())
        win.settings_file = tmp_path / "settings.json"
        save_settings(AppSettings(autosave_interval_sec=300), win.settings_file)
        win.files.autosave_timer.setInterval(300_000)
        try:
            save_settings(AppSettings(autosave_interval_sec=7), win.settings_file)

            win.files.autosave()

            assert win.files.autosave_timer.interval() == 7_000
        finally:
            win.state.history.mark_saved()
            win.close()

    def test_起動を記録に残す(self, window):
        """記録が空なら、タイマーを積んだアプリがそもそも動いていないと分かる。"""
        text = window.files.autosave_log.path.read_text(encoding="utf-8")
        assert "起動" in text

    def test_何もしなかった理由を記録に残す(self, window, tmp_path):
        window.files.autosave()  # 保存先が決まっていない
        window.state.save(tmp_path)
        window.files.autosave()  # 変化が無い

        lines = window.files.autosave_log.path.read_text(encoding="utf-8").splitlines()
        assert any("保存先が未定" in line for line in lines)
        assert any("変更が無いため" in line for line in lines)

    def test_同じ理由が続く間は記録を増やさない(self, window):
        for _ in range(5):
            window.files.autosave()

        lines = window.files.autosave_log.path.read_text(encoding="utf-8").splitlines()
        assert sum("保存先が未定" in line for line in lines) == 1

    def test_退避できた回は毎回記録する(self, window, tmp_path):
        # いつの時点の内容が backup/ に入っているかは記録の目的そのもの
        window.state.save(tmp_path)
        for _ in range(3):
            window.add_page()  # 毎回ちがう内容にする
            window.files.autosave()

        lines = window.files.autosave_log.path.read_text(encoding="utf-8").splitlines()
        assert sum("自動バックアップしました" in line for line in lines) == 3

    def test_失敗しても作業を止めない(self, window, tmp_path, monkeypatch):
        # 保存先が外付けドライブで抜かれている場合など
        window.state.save(tmp_path)
        window.add_full_page_panel()

        def 書けない(*args, **kwargs):
            raise OSError("書き込めません")

        monkeypatch.setattr("manga_layout.ui.state.write_autosave", 書けない)

        messages = []
        window.state.message.connect(messages.append)
        window.files.autosave()  # 例外が外へ出ないこと

        assert any("自動バックアップできません" in m for m in messages)
        # 印を進めないので、次の回にまた試す
        assert window.state.history.is_autosave_pending


class TestSlantSplitUI:
    """斜めの縦割りの結線。

    形そのものは tests/test_slant.py で固めてある。ここでは
    「道具 → 分割 → 履歴 → 選択」がつながっているかを見る。
    """

    def _split(self, window, x: float = 620.0):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._apply_split(x, 877.0)
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
        assert window.panel_menu.slant_flip_action.isEnabled()

    def test_斜めでないコマでは反転が選べない(self, window):
        window.add_full_page_panel()
        assert not window.panel_menu.slant_flip_action.isEnabled()

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


def scene_point_of(view, x: float, y: float) -> tuple[float, float]:
    """`press` が実際に受け取る mm 座標。画面座標の整数丸めを通す。"""
    from PySide6.QtCore import QPointF

    pixel = QPointF(view.mapFromScene(QPointF(x, y))).toPoint()
    point = view.mapToScene(pixel)
    return point.x(), point.y()


class TestSlantSlideUI:
    """斜めの境界を左右にずらす操作の結線。"""

    def _split(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._apply_split(620.0, 877.0)
        # 分割の道具は使ったあとも残る。押下の検証では選択に戻しておく
        window.state.set_tool(TOOL_SELECT)
        page = window.state.page
        window.state.select(page.panels[0].id)
        return page

    def test_つまみは境界の中点に出る(self, window):
        page = self._split(window)
        pair = page.slant_pairs[0]
        outer = page.slant_bounds(pair)
        hx, hy = window.view._scene.slant_handle()
        assert hx == pytest.approx(outer.x + outer.w * pair.ratio)
        assert hy == pytest.approx(outer.y + outer.h / 2.0)

    def test_つまみを掴んで判定できる(self, window):
        self._split(window)
        hx, hy = window.view._scene.slant_handle()
        assert window.view._slant_handle_at(hx, hy)

    def test_掴める範囲は描く印より広い(self, window):
        """印は小さく描き、拾う範囲は SLANT_HANDLE_PX ぶん広く取る。"""
        from manga_layout.ui.canvas import HANDLE_PX, SLANT_HANDLE_PX

        self._split(window)
        view = window.view
        hx, hy = view._scene.slant_handle()
        drawn = HANDLE_PX / view.view_scale / 2.0
        grab = SLANT_HANDLE_PX / view.view_scale / 2.0

        # 印の外だが掴める範囲の中
        assert view._slant_handle_at(hx + (drawn + grab) / 2.0, hy)
        # 範囲の外
        assert not view._slant_handle_at(hx + grab * 1.5, hy)

    def test_リサイズのつまみのほうが優先される(self, window):
        """境界の掴み範囲が広いので、重なったら小さいほうを勝たせる。

        逆にすると、縮小したときや細いコマで左右のつまみが覆い隠され、
        大きさを変えられなくなる。
        """
        from manga_layout.ui.canvas import SLANT_HANDLE_PX, ResizeDrag

        page = self._split(window)
        view = window.view
        outer = page.slant_bounds(page.slant_pairs[0])
        # 左辺の中央のつまみ。境界の掴み範囲と重なるまで表示を縮める
        # 境界のつまみから左辺までは幅の半分。掴む範囲がそれを 20mm
        # 上回るところまで縮めて、確実に重ねる
        scale = SLANT_HANDLE_PX / 2.0 / (outer.w / 2.0 + 20.0)
        view.resetTransform()
        view.scale(scale, scale)
        wx, wy = outer.x, outer.y + outer.h / 2.0

        # 押下は画面座標を整数へ丸めてから mm に戻る。ここまで縮めると
        # 1px が数 mm になるので、実際に届く点で判定を確かめる
        ex, ey = scene_point_of(view, wx, wy)
        assert view._handle_at_point(ex, ey) == "w"
        assert view._slant_handle_at(ex, ey)  # 範囲としては重なっている

        press(view, wx, wy)
        assert isinstance(view._drag, ResizeDrag)
        assert view._drag.handle == "w"

    def test_斜めでないコマにはつまみが出ない(self, window):
        window.add_full_page_panel()
        assert window.view._scene.slant_handle() is None

    def test_ずらすとモデルに入る(self, window):
        page = self._split(window)
        before = page.slant_pairs[0].ratio
        window.view._apply_slant(page.panels[0].id, 0.35)

        page = window.state.page
        assert page.slant_pairs[0].ratio == pytest.approx(0.35)
        assert page.slant_pairs[0].ratio != before
        # 外側の矩形は動かない
        # 基本枠いっぱい（1240 - 余白 89 × 2）
        assert page.slant_bounds(page.slant_pairs[0]).w == pytest.approx(1062.0)

    def test_ずらしたあと履歴で戻せる(self, window):
        page = self._split(window)
        before = page.slant_pairs[0].ratio
        window.view._apply_slant(page.panels[0].id, 0.35)
        window.state.undo()
        assert window.state.page.slant_pairs[0].ratio == pytest.approx(before)

    def test_下見は履歴を汚さない(self, window):
        """ドラッグ中はモデルに触らない（しっぽの付け根と同じ流儀）。"""
        from manga_layout.ui.canvas import SlantDrag

        page = self._split(window)
        depth = len(window.state.history._undo)
        window.view._scene.active_drag = SlantDrag(page.panels[0].id, 0.3)
        assert window.state.page.slant_pairs[0].ratio == pytest.approx(
            page.slant_pairs[0].ratio
        )
        assert len(window.state.history._undo) == depth

    def test_行きすぎても押し戻して受け付ける(self, window):
        page = self._split(window)
        window.view._apply_slant(page.panels[0].id, 0.02)
        ratio = window.state.page.slant_pairs[0].ratio
        assert 0.02 < ratio < 0.5
