"""切り抜きの操作・保存・描画（要件定義 10.3 / `SAM3実装計画.md` 段階1〜3）。

**SAM 3 は動かさない。** 決め打ちの白黒マスクを使って、適用・取り消し・保存・
整理・点検・書き出しまでを先に固める。マスクそのものの中身は
tests/test_image_masks.py。

画面まわりの流儀は tests/test_ui_tone.py と同じ（持ち主が画像で、1手ずつ
履歴に積む）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, qAlpha

from manga_layout import Rect, check, storage
from manga_layout.assets import AssetStore
from manga_layout.errors import BrokenImageError, MaskSizeError
from manga_layout.images import size_px, to_png_bytes
from manga_layout.ui import EditorState, MainWindow

PANEL = Rect(120.0, 120.0, 720.0, 540.0)
IMAGE_PX = (120, 80)


@pytest.fixture
def window(qapp, tmp_path):
    win = MainWindow(EditorState())
    win.state.save(tmp_path / "作品")
    yield win
    win.state.history.mark_saved()
    win.close()


def opaque_png(px=IMAGE_PX) -> bytes:
    image = QImage(px[0], px[1], QImage.Format.Format_ARGB32)
    image.fill(QColor("#3C6EA5"))
    return to_png_bytes(image)


def mask_png(keep_left: int, px=IMAGE_PX) -> bytes:
    """左から `keep_left` 画素だけ残すマスク。"""
    mask = QImage(px[0], px[1], QImage.Format.Format_Grayscale8)
    mask.fill(Qt.GlobalColor.black)
    for x in range(keep_left):
        for y in range(px[1]):
            mask.setPixelColor(x, y, QColor(255, 255, 255))
    return to_png_bytes(mask)


@pytest.fixture
def window_with_image(window):
    """コマに画像を1枚貼った状態。"""
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    panel_id = window.state.page.panels[0].id
    window.state.place_image(panel_id, opaque_png())
    return window


def image_of(window):
    return window.state.page.panels[0].children[0]


class Test適用と取り消し:
    def test_掛けると参照が入る(self, window_with_image):
        state = window_with_image.state
        assert state.apply_image_mask(image_of(window_with_image).id, mask_png(40))
        assert image_of(window_with_image).mask_asset.startswith("assets/")
        assert image_of(window_with_image).asset, "元の絵の参照は残る"

    def test_Undoで元の絵に戻る(self, window_with_image):
        state = window_with_image.state
        image_id = image_of(window_with_image).id
        state.apply_image_mask(image_id, mask_png(40))

        state.undo()
        assert image_of(window_with_image).mask_asset == ""
        state.redo()
        assert image_of(window_with_image).mask_asset != ""

    def test_掛け直しも1手(self, window_with_image):
        state = window_with_image.state
        image_id = image_of(window_with_image).id
        state.apply_image_mask(image_id, mask_png(40))
        first = image_of(window_with_image).mask_asset
        state.apply_image_mask(image_id, mask_png(90))
        second = image_of(window_with_image).mask_asset

        assert first != second
        state.undo()
        assert image_of(window_with_image).mask_asset == first, "1手戻れば前のマスク"

    def test_外せる(self, window_with_image):
        state = window_with_image.state
        image_id = image_of(window_with_image).id
        state.apply_image_mask(image_id, mask_png(40))

        assert state.clear_image_mask(image_id) is True
        assert image_of(window_with_image).mask_asset == ""
        state.undo()
        assert image_of(window_with_image).mask_asset != "", "外すのも1手"

    def test_掛かっていなければ外せない(self, window_with_image):
        state = window_with_image.state
        assert state.clear_image_mask(image_of(window_with_image).id) is False

    def test_大きさが違えば断る(self, window_with_image):
        """縮めて合わせない。ずれた組み合わせは人が見て気づけない（→ 4.2）。"""
        state = window_with_image.state
        with pytest.raises(MaskSizeError):
            state.apply_image_mask(image_of(window_with_image).id, mask_png(10, (60, 40)))
        assert image_of(window_with_image).mask_asset == "", "断ったら作品は変わらない"

    def test_壊れたマスクは断る(self, window_with_image, fixture_dir):
        state = window_with_image.state
        with pytest.raises(BrokenImageError):
            state.apply_image_mask(
                image_of(window_with_image).id,
                (fixture_dir / "broken.png").read_bytes(),
            )
        assert image_of(window_with_image).mask_asset == ""

    def test_無い画像には掛からない(self, window_with_image):
        assert window_with_image.state.apply_image_mask("no-such-id", mask_png(40)) is False


class Test保存と読み直し:
    def test_保存して開き直しても掛かったまま(self, window_with_image, tmp_path):
        state = window_with_image.state
        state.apply_image_mask(image_of(window_with_image).id, mask_png(40))
        ref = image_of(window_with_image).mask_asset
        path = state.save()

        reopened = storage.load_project(path.parent)
        image = reopened.pages[0].panels[0].children[0]
        assert image.mask_asset == ref
        assert AssetStore(path.parent).exists(ref), "マスクの実体も assets/ に入る"

    def test_使っていない作品の保存形式は変わらない(self, window_with_image):
        """トーン・集中線と同じ線引き。掛けていない画像では項目ごと省く。"""
        assert "mask_asset" not in image_of(window_with_image).to_dict()

    def test_古い作品はそのまま読める(self, sample_project):
        """`mask_asset` の無い project.json は、今までどおり切り抜き無しで読む。"""
        data = sample_project.to_dict()
        image = data["pages"][0]["panels"][0]["children"][0]
        assert "mask_asset" not in image
        back = type(sample_project).from_dict(data)
        assert back.pages[0].panels[0].children[0].mask_asset == ""

    def test_整理の対象にしない(self, window_with_image, tmp_path):
        """数え漏らすと、次に開いたときに切り抜きだけが黙って外れる。"""
        state = window_with_image.state
        state.apply_image_mask(image_of(window_with_image).id, mask_png(40))
        path = state.save()

        moved = storage.prune_unused_assets(state.project, path.parent)
        assert moved == [], "元画像もマスクも使っている"
        assert image_of(window_with_image).mask_asset in state.project.referenced_assets()

    def test_外したマスクは整理で片付く(self, window_with_image):
        state = window_with_image.state
        image_id = image_of(window_with_image).id
        state.apply_image_mask(image_id, mask_png(40))
        ref = image_of(window_with_image).mask_asset
        path = state.save()
        state.clear_image_mask(image_id)
        state.save()

        moved = storage.prune_unused_assets(state.project, path.parent)
        assert moved == [ref], "使われなくなった実体は整理が拾う（消すのは利用者の操作）"


class Test点検:
    def test_マスクが欠けていると知らせる(self, window_with_image):
        state = window_with_image.state
        state.apply_image_mask(image_of(window_with_image).id, mask_png(40))
        ref = image_of(window_with_image).mask_asset

        findings = check.inspect_project(
            state.project, has_asset=lambda r: r != ref
        )
        kinds = [f.kind for f in findings]
        assert check.KIND_MISSING_MASK in kinds

    def test_絵ごと欠けているときは1件だけ(self, window_with_image):
        """1つの絵に2件並ぶと、どちらから直すのか分からなくなる。"""
        state = window_with_image.state
        state.apply_image_mask(image_of(window_with_image).id, mask_png(40))

        findings = check.inspect_project(state.project, has_asset=lambda r: False)
        kinds = [f.kind for f in findings]
        assert kinds.count(check.KIND_MISSING_ASSET) == 1
        assert check.KIND_MISSING_MASK not in kinds


class Test描画と書き出し:
    """**画面・サムネイル・PNG／JPG／PSD が同じ結果になる**（→ 計画 段階3）。

    経路が1本（`PageRenderer` と `images(image)`）なので、確かめるのは
    「その1本にマスクが乗っているか」と「PSD だけ別の経路を通っていないか」。
    """

    @pytest.fixture
    def masked(self, window_with_image):
        state = window_with_image.state
        state.apply_image_mask(image_of(window_with_image).id, mask_png(60))
        return state

    def test_画面用の1枚が切り抜かれている(self, masked):
        preview = masked.image_preview(image_of_state(masked))
        image = preview.image.convertToFormat(QImage.Format.Format_ARGB32)
        assert size_px(image) == IMAGE_PX, "小さい絵は縮まない"
        assert qAlpha(image.pixel(10, 40)) == 255, "残した側"
        assert qAlpha(image.pixel(110, 40)) == 0, "切り抜いた側"

    def test_書き出しの原寸も切り抜かれている(self, masked):
        from manga_layout.ui.export import FullImages

        full = FullImages(masked)(image_of_state(masked))
        assert not full.is_reduced
        image = full.image.convertToFormat(QImage.Format.Format_ARGB32)
        assert qAlpha(image.pixel(110, 40)) == 0

    def test_PNGの用紙に切り抜いた側が出ない(self, masked):
        from manga_layout.ui.export import render_page

        page = masked.page
        rendered = render_page(masked, page, 1.0)
        絵 = image_of_state(masked).rect
        右端 = (round(絵.x + 絵.w) - 4, round(絵.y + 絵.h / 2))
        左端 = (round(絵.x) + 4, 右端[1])
        assert rendered.pixelColor(*左端).alpha() == 255
        assert rendered.pixelColor(*左端) != rendered.pixelColor(*右端), (
            "切り抜いた側には絵が出ない"
        )

    def test_PSDを重ね直すとPNGと一致する(self, masked):
        """**PSD だけ切り抜き前の絵が出ていないか**（→ 計画 4.4）。

        既存の分解の検証と同じ形。ここが通るなら、「絵」レイヤーも
        トーンの3枚も、画面と同じ切り抜きを通っている。
        """
        from manga_layout.ui.export import render_page
        from manga_layout.ui.psd_export import flatten, page_layers

        page = masked.page
        layers = page_layers(masked, page, 1.0)
        merged = flatten(layers, round(page.size.w), round(page.size.h))
        expected = render_page(masked, page, 1.0)

        diff = max(
            abs(a - b)
            for a, b in zip(
                bytes(merged.convertToFormat(QImage.Format.Format_ARGB32).constBits()),
                bytes(expected.convertToFormat(QImage.Format.Format_ARGB32).constBits()),
                strict=True,
            )
        )
        assert diff <= 1, "重ね直したものと PNG が一致する（丸めの1まで）"

    def test_PSDの絵レイヤーが切り抜かれている(self, masked):
        """一致の確認だけでは足りない。**両方とも切り抜き前**でも一致する。

        「絵」レイヤー（`psd_export.BareImages`）そのものを見て、切り抜いた
        側が透明になっていることを確かめる（→ 計画 4.4）。
        """
        from manga_layout.psd import PsdGroup
        from manga_layout.ui.psd_export import page_layers

        layers = page_layers(masked, masked.page, 1.0)
        絵 = None
        for item in layers:
            if isinstance(item, PsdGroup):
                絵 = next((c for c in item.children if c.name == "絵"), 絵)
        assert 絵 is not None, "コマのフォルダに「絵」レイヤーがある"

        image = 絵.image.convertToFormat(QImage.Format.Format_ARGB32)
        rect = image_of_state(masked).rect
        assert qAlpha(image.pixel(round(rect.x) + 4, round(rect.y + rect.h / 2))) > 0
        assert (
            qAlpha(image.pixel(round(rect.x + rect.w) - 4, round(rect.y + rect.h / 2)))
            == 0
        ), "切り抜いた側は透明"

    def test_サムネイルも同じ経路を通る(self, masked):
        from PySide6.QtCore import QSize

        from manga_layout.ui.pages import render_thumbnail

        thumb = render_thumbnail(masked, masked.page, QSize(160, 220))
        assert not thumb.isNull()


def image_of_state(state):
    return state.page.panels[0].children[0]
