"""画像の展開と、画面用の縮小版の検証。

ここは Qt を使うが画面は出さない（offscreen）。`assets.py` が見抜けない
「署名だけ正しく中身が壊れたファイル」を、この層が止められるかが要点。
"""

from __future__ import annotations

import pytest

from manga_layout.errors import BrokenImageError
from manga_layout.images import (
    PREVIEW_MAX_PX,
    ImageCache,
    decode,
    make_preview,
    preview_from_bytes,
    size_px,
)


@pytest.fixture
def large_bytes(fixture_dir) -> bytes:
    """長辺 2000px。縮小版の閾値（1600）を超える。"""
    return (fixture_dir / "large_2000x1500.png").read_bytes()


class TestDecode:
    def test_展開して寸法が取れる(self, fixture_dir, qapp):
        data = (fixture_dir / "rgb_opaque.png").read_bytes()
        assert size_px(decode(data)) == (64, 48)

    def test_透明度が保たれる(self, png_bytes, qapp):
        # クリスタからの貼り付けはアルファ付き。ここで潰すと縁が黒くなる
        assert decode(png_bytes).hasAlphaChannel()

    def test_壊れたファイルは断る(self, fixture_dir, qapp):
        # assets.py は署名しか見ないので通してしまう。ここが最後の砦
        broken = (fixture_dir / "broken.png").read_bytes()
        with pytest.raises(BrokenImageError):
            decode(broken)

    def test_画像でないものは断る(self, qapp):
        with pytest.raises(BrokenImageError):
            decode(b"this is not an image at all")


class TestPreview:
    def test_大きい画像は縮む(self, large_bytes, qapp):
        preview = preview_from_bytes(large_bytes)
        assert preview.source_px == (2000, 1500)
        assert max(size_px(preview.image)) == PREVIEW_MAX_PX
        assert preview.is_reduced

    def test_縮んでも縦横比は変わらない(self, large_bytes, qapp):
        preview = preview_from_bytes(large_bytes)
        w, h = size_px(preview.image)
        assert w / h == pytest.approx(2000 / 1500, rel=1e-3)

    def test_小さい画像はそのまま(self, fixture_dir, qapp):
        preview = preview_from_bytes((fixture_dir / "rgb_opaque.png").read_bytes())
        assert preview.source_px == (64, 48)
        assert not preview.is_reduced

    def test_原寸の寸法は縮小前の値(self, large_bytes, qapp):
        """縦横比の計算に使うので、ここが縮小後の値だと絵が歪む。"""
        preview = preview_from_bytes(large_bytes)
        assert preview.source_px == (2000, 1500)

    def test_極端に細長い画像でも潰れない(self, fixture_dir, qapp):
        preview = preview_from_bytes((fixture_dir / "tall_1x256.png").read_bytes())
        assert preview.source_px == (1, 256)
        assert min(size_px(preview.image)) >= 1

    def test_縮小の閾値ちょうどは縮めない(self, qapp):
        from PySide6.QtGui import QImage

        image = QImage(PREVIEW_MAX_PX, 100, QImage.Format.Format_ARGB32)
        assert make_preview(image) is image


class TestCache:
    def test_2回目は展開し直さない(self, png_bytes, qapp):
        cache = ImageCache()
        calls = []

        def read():
            calls.append(1)
            return png_bytes

        first = cache.get("assets/a.png", read)
        second = cache.get("assets/a.png", read)

        assert first is second
        assert len(calls) == 1

    def test_実体が無ければ何も返さない(self, qapp):
        cache = ImageCache()
        assert cache.get("assets/missing.png", lambda: None) is None

    def test_壊れていても落ちない(self, fixture_dir, qapp):
        cache = ImageCache()
        broken = (fixture_dir / "broken.png").read_bytes()
        assert cache.get("assets/broken.png", lambda: broken) is None

    def test_壊れた画像を何度も展開しない(self, fixture_dir, qapp):
        """覚えずにいると、描き直しのたびに展開を試して画面が固まる。"""
        cache = ImageCache()
        broken = (fixture_dir / "broken.png").read_bytes()
        calls = []

        def read():
            calls.append(1)
            return broken

        cache.get("assets/broken.png", read)
        cache.get("assets/broken.png", read)

        assert len(calls) == 1

    def test_読み直させられる(self, png_bytes, qapp):
        cache = ImageCache()
        calls = []

        def read():
            calls.append(1)
            return png_bytes

        cache.get("assets/a.png", read)
        cache.forget("assets/a.png")
        cache.get("assets/a.png", read)

        assert len(calls) == 2

    def test_作品を入れ替えると空になる(self, png_bytes, qapp):
        cache = ImageCache()
        cache.get("assets/a.png", lambda: png_bytes)
        cache.clear()
        assert len(cache) == 0
