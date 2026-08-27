"""切り抜きのマスク（要件定義 10.3 / `SAM3実装計画.md` 4.2）。

**SAM 3 は出てこない。** ここで確かめるのは共通形式のほうで、マスクが
どこから来たかは問わない。モデルを動かさないので GPU も要らない。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, qAlpha, qBlue, qRed

from manga_layout.errors import BrokenImageError, MaskSizeError
from manga_layout.image_masks import (
    apply_mask,
    decode_mask,
    is_binary,
    masked_preview,
    safe_masked_preview,
)
from manga_layout.images import PREVIEW_MAX_PX, decode, size_px, to_png_bytes
from manga_layout.tone import default_tone


def gray_mask(width: int, height: int, value: int) -> QImage:
    """一様な濃さのマスク。

    **`setPixelColor` で作る。`setPixel` は使わない。** 8bit の絵に対する
    `setPixel` は値を色表の番号として扱うため、255 を渡しても 39 になる
    （2026-08-27 実測 → `PySide6の落とし穴.md`）。
    """
    mask = QImage(width, height, QImage.Format.Format_Grayscale8)
    mask.fill(QColor(value, value, value))
    return mask


def half_mask(width: int, height: int, split: int | None = None) -> QImage:
    """`split` より左だけ残すマスク（左が白、右が黒）。既定は真ん中。"""
    mask = QImage(width, height, QImage.Format.Format_Grayscale8)
    mask.fill(Qt.GlobalColor.black)
    for x in range(width // 2 if split is None else split):
        for y in range(height):
            mask.setPixelColor(x, y, QColor(255, 255, 255))
    return mask


def two_color_image(width: int, height: int, split: int | None = None) -> QImage:
    """`split` より左が赤、右が青の不透明な絵。既定は真ん中。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 255))
    for x in range(width // 2 if split is None else split):
        for y in range(height):
            image.setPixelColor(x, y, QColor(255, 0, 0))
    return image


class Test読み込み:
    def test_濃淡の絵として読める(self, fixture_dir, qapp):
        mask = decode_mask((fixture_dir / "gray8.png").read_bytes())
        assert mask.format() == QImage.Format.Format_Grayscale8
        assert size_px(mask) == (32, 32)

    def test_色の付いたPNGでも濃淡に揃える(self, png_bytes, qapp):
        """色や透明度が付いていても、マスクとして意味を持つのは明るさだけ。"""
        mask = decode_mask(png_bytes)
        assert mask.format() == QImage.Format.Format_Grayscale8

    def test_壊れたデータは断る(self, fixture_dir, qapp):
        with pytest.raises(BrokenImageError):
            decode_mask((fixture_dir / "broken.png").read_bytes())


class Test掛け合わせ:
    def test_白は残り黒は消える(self, qapp):
        image = two_color_image(4, 2)
        out = apply_mask(image, half_mask(4, 2)).convertToFormat(
            QImage.Format.Format_ARGB32
        )
        assert qAlpha(out.pixel(0, 0)) == 255, "白い所は不透明のまま"
        assert qAlpha(out.pixel(3, 0)) == 0, "黒い所は透明になる"

    def test_元のアルファと掛け合わせる(self, png_bytes, qapp):
        """置き換えると、切り抜きのついでに元の透明が埋まる（→ 要件定義 9章）。

        基準画像の右上は半透明の緑（alpha=128）。ここに濃さ 128 のマスクを
        掛けたら 64 前後になるのが正しい（255 になったら置き換えている）。
        """
        image = decode(png_bytes)
        out = apply_mask(image, gray_mask(64, 64, 128)).convertToFormat(
            QImage.Format.Format_ARGB32
        )
        assert qAlpha(out.pixel(48, 16)) == pytest.approx(64, abs=2)
        assert qAlpha(out.pixel(16, 16)) == pytest.approx(128, abs=2), "不透明な赤の側"
        assert qAlpha(out.pixel(16, 48)) == 0, "元から透明な所は透明のまま"

    def test_大きさが違えば断る(self, qapp):
        with pytest.raises(MaskSizeError):
            apply_mask(two_color_image(4, 4), gray_mask(4, 3, 255))

    def test_元の絵は変えない(self, qapp):
        image = two_color_image(4, 2)
        apply_mask(image, gray_mask(4, 2, 0))
        assert qAlpha(image.pixel(0, 0)) == 255


class Test濃淡の確認:
    def test_白黒だけならTrue(self, qapp):
        assert is_binary(gray_mask(8, 8, 255))
        assert is_binary(half_mask(8, 8))

    def test_中間値が混じればFalse(self, qapp):
        mask = half_mask(8, 8)
        mask.setPixelColor(0, 0, QColor(128, 128, 128))
        assert not is_binary(mask)


class Test合成した1枚:
    """`masked_preview`。**掛けるのは必ず原寸**（→ 計画 4.3）。"""

    def test_画面用は縮み原寸は縮まない(self, qapp):
        width, height = 2000, 500
        data = to_png_bytes(two_color_image(width, height))
        mask = to_png_bytes(half_mask(width, height))

        full = masked_preview(data, mask, None, reduced=False)
        assert size_px(full.image) == (width, height)
        assert full.source_px == (width, height)

        small = masked_preview(data, mask, None, reduced=True)
        assert max(size_px(small.image)) == PREVIEW_MAX_PX
        assert small.source_px == (width, height), "縦横比の計算は原寸を使う"

    def test_縮めてから掛けると縁に別の色が混ざる(self, qapp):
        """**この順序を選んだ理由そのもの**（→ 計画 4.3）。

        左が赤・右が青の絵を、左半分だけ残すマスクで切り抜く。原寸で合成して
        から縮めれば、残った画素に青は1つも混じらない。先に縮めると、境目の
        画素が赤と青の平均になってから切り抜かれるので、**残した側の縁に
        消したはずの色が残る**。髪の輪郭で起きると、背景の色が縁に回り込む。
        """
        # **境目を画素の切れ目に合わせない。** 2000 → 1600 はちょうど 0.8 倍
        # なので、真ん中で分けると縮めても境目が画素の境に乗ってしまい、
        # どちらの順序でも混ざらない（この試験自体が意味を失う）
        width, height, split = 2000, 64, 1001
        data = to_png_bytes(two_color_image(width, height, split))
        mask = to_png_bytes(half_mask(width, height, split))

        composed = masked_preview(data, mask, None, reduced=True).image.convertToFormat(
            QImage.Format.Format_ARGB32
        )
        混ざった青 = max(
            qBlue(composed.pixel(x, 32))
            for x in range(composed.width())
            if qAlpha(composed.pixel(x, 32)) > 0
        )
        assert 混ざった青 == 0, "原寸で合成してから縮めれば、消した側の色は入らない"

        # 先に縮めてから掛けた場合（採らなかったほう）
        from manga_layout.images import make_preview

        縮めた絵 = make_preview(decode(data))
        縮めたマスク = make_preview(decode_mask(mask)).convertToFormat(
            QImage.Format.Format_Grayscale8
        )
        逆順 = apply_mask(縮めた絵, 縮めたマスク).convertToFormat(
            QImage.Format.Format_ARGB32
        )
        逆順の青 = max(
            qBlue(逆順.pixel(x, 32))
            for x in range(逆順.width())
            if qAlpha(逆順.pixel(x, 32)) > 0
        )
        assert 逆順の青 > 0, "先に縮めると、残した側の縁に消した色が混ざる"

    def test_トーンは切り抜いた外に乗らない(self, qapp):
        """マスク→トーンの順でよい理由（→ `image_masks.masked_preview`）。"""
        width, height = 64, 64
        image = QImage(width, height, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0))  # 真っ黒＝トーンが乗る明るさ
        data = to_png_bytes(image)
        mask = to_png_bytes(half_mask(width, height))

        out = masked_preview(data, mask, default_tone(), reduced=False).image
        out = out.convertToFormat(QImage.Format.Format_ARGB32)
        assert qAlpha(out.pixel(60, 32)) == 0, "切り抜いた外は透明のまま"
        assert qAlpha(out.pixel(4, 32)) == 255, "残した側にはトーンが乗る"
        assert qRed(out.pixel(4, 32)) >= 0


class Test材料が揃わないとき:
    """`safe_masked_preview`。**描くときはマスクの不備で止めない**（→ 段階1〜3）。"""

    def test_マスクが無ければ何も返さない(self, png_bytes, qapp):
        assert safe_masked_preview(png_bytes, None, None, reduced=True) is None

    def test_元画像が無ければ何も返さない(self, qapp):
        mask = to_png_bytes(gray_mask(4, 4, 255))
        assert safe_masked_preview(None, mask, None, reduced=True) is None

    def test_大きさが違っても例外にしない(self, png_bytes, qapp):
        """断るのは適用のとき（→ `EditorState.apply_image_mask`）で、描くときではない。"""
        mask = to_png_bytes(gray_mask(8, 8, 255))
        assert safe_masked_preview(png_bytes, mask, None, reduced=True) is None

    def test_壊れたマスクでも例外にしない(self, png_bytes, fixture_dir, qapp):
        broken = (fixture_dir / "broken.png").read_bytes()
        assert safe_masked_preview(png_bytes, broken, None, reduced=True) is None
