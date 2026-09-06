"""画像の展開と、画面用の縮小版の検証。

ここは Qt を使うが画面は出さない（offscreen）。`assets.py` が見抜けない
「署名だけ正しく中身が壊れたファイル」を、この層が止められるかが要点。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from manga_layout.errors import AssetError, BrokenImageError
from manga_layout.images import (
    PREVIEW_MAX_PX,
    BakedCache,
    ImageCache,
    decode,
    make_preview,
    preview_from_bytes,
    size_px,
    toned,
)
from manga_layout.tone import default_tone


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

    def test_大きすぎる画像は理由をはっきり伝える(self, fixture_dir, qapp, monkeypatch):
        """壊れてはいない、Qt の確保上限を超えるだけの画像。

        以前は寸法を事前に確かめておらず、`loadFromData` がそのまま失敗
        して「壊れている可能性があります」という誤った理由になっていた
        （2026-08-08 に発見）。確保上限を人為的に下げて再現する
        （本物の巨大画像を用意せずに済む）。
        """
        from PySide6.QtGui import QImageReader

        # 64×48 は ARGB32 換算で約 0.012MB。上限をそれより下げて再現する
        monkeypatch.setattr(QImageReader, "allocationLimit", staticmethod(lambda: 0.005))
        data = (fixture_dir / "rgb_opaque.png").read_bytes()

        with pytest.raises(BrokenImageError, match="大きすぎます"):
            decode(data)

    def test_壊れたファイルは大きすぎるとは言わない(self, fixture_dir, qapp, monkeypatch):
        """寸法自体が読めない壊れたファイルは、サイズ判定に巻き込まれない。"""
        from PySide6.QtGui import QImageReader

        monkeypatch.setattr(QImageReader, "allocationLimit", staticmethod(lambda: 0.005))
        broken = (fixture_dir / "broken.png").read_bytes()

        with pytest.raises(BrokenImageError, match="壊れている可能性") as exc:
            decode(broken)
        assert "大きすぎます" not in str(exc.value)


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

    def test_実体があるのに読めなくても落ちない(self, qapp):
        """他アプリのロック・権限など、「ファイルはあるが読めない」場合。

        `AssetStore.read` は OSError を `AssetError` に包み直して投げる。
        以前はここで子の `BrokenImageError` だけを見ていたため、この形の
        失敗だけ素通りしていた（2026-08-08 に発見）。
        """
        cache = ImageCache()

        def read():
            raise AssetError("画像を読めませんでした: assets/locked.png")

        assert cache.get("assets/locked.png", read) is None

    def test_読めない画像を何度も読み直さない(self, qapp):
        """**このキャッシュが本来防ぐはずの壊れ方そのもの。**

        失敗を覚えられないと、描き直しのたびに例外が漏れ続ける。画面は
        コマを毎回描き直す作りなので、1枚読めないだけでページの残りが
        描かれなくなる（→ `test_壊れた画像を何度も展開しない` と対）。
        """
        cache = ImageCache()
        calls = []

        def read():
            calls.append(1)
            raise AssetError("画像を読めませんでした: assets/locked.png")

        cache.get("assets/locked.png", read)
        cache.get("assets/locked.png", read)

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


class Test焼き込みCache:
    """トーン（10.1）と切り抜き（10.3）を焼いた1枚の入れ物。中身は tests/test_tone.py。"""

    @staticmethod
    def make(png_bytes, tone=None):
        source = preview_from_bytes(png_bytes)
        return lambda: toned(source, tone) if tone is not None else source

    @staticmethod
    def key(ref, tone=None, mask=""):
        return (ref, mask, () if tone is None else tone.key())

    def test_2回目は焼き直さない(self, png_bytes, qapp):
        cache = BakedCache()
        tone = default_tone()
        make = self.make(png_bytes, tone)
        assert cache.get(self.key("assets/a.png", tone), make) is cache.get(
            self.key("assets/a.png", tone), make
        )

    def test_設定が違えば別の1枚になる(self, png_bytes, qapp):
        """同じ画像を2枚貼ってトーンだけ変える場面。参照だけを鍵にすると壊れる。"""
        cache = BakedCache()
        other = replace(default_tone(), angle=90.0)
        first = cache.get(self.key("assets/a.png", default_tone()), self.make(png_bytes, default_tone()))
        second = cache.get(self.key("assets/a.png", other), self.make(png_bytes, other))
        assert first is not second
        assert len(cache) == 2

    def test_切り抜きが違えば別の1枚になる(self, png_bytes, qapp):
        """同じ絵に別のマスクを掛けた2枚を、同じページに置ける（→ 10.3）。"""
        cache = BakedCache()
        焼いた回数 = 0

        def make():
            nonlocal 焼いた回数
            焼いた回数 += 1
            return preview_from_bytes(png_bytes)

        cache.get(self.key("assets/a.png", mask="assets/m1.png"), make)
        cache.get(self.key("assets/a.png", mask="assets/m2.png"), make)
        cache.get(self.key("assets/a.png", mask="assets/m1.png"), make)
        assert 焼いた回数 == 2, "マスク違いは別の1枚。同じマスクなら焼き直さない"
        assert len(cache) == 2

    def test_実体が無ければ何も返さない(self, qapp):
        cache = BakedCache()
        assert cache.get(self.key("assets/x.png", default_tone()), lambda: None) is None
        assert len(cache) == 0, "覚えない。元の入れ物が既に覚えている"

    def test_溜まりすぎたら古いものから捨てる(self, png_bytes, qapp):
        """鍵に設定が入るぶん増え続ける。1枚 10MB 前後なので上限が要る。"""
        cache = BakedCache(limit=3)
        for i in range(6):
            tone = replace(default_tone(), angle=float(i))
            cache.get(self.key("assets/a.png", tone), self.make(png_bytes, tone))
        assert len(cache) == 3

    def test_画像ごと忘れさせられる(self, png_bytes, qapp):
        cache = BakedCache()
        make = self.make(png_bytes)
        cache.get(self.key("assets/a.png", default_tone()), make)
        cache.get(self.key("assets/a.png", replace(default_tone(), angle=90.0)), make)
        cache.get(self.key("assets/b.png", default_tone()), make)

        cache.forget("assets/a.png")
        assert len(cache) == 1, "設定違いをまとめて落とす"

    def test_マスクの参照でも忘れさせられる(self, png_bytes, qapp):
        """マスクを外した直後に手放すのはマスク側の参照（→ `EditorState.forget_if_unused`）。"""
        cache = BakedCache()
        make = self.make(png_bytes)
        cache.get(self.key("assets/a.png", mask="assets/m.png"), make)
        cache.get(self.key("assets/b.png", mask="assets/m.png"), make)
        cache.get(self.key("assets/c.png"), make)

        cache.forget("assets/m.png")
        assert len(cache) == 1, "元画像が違っても、そのマスクを使った1枚は全部落とす"


class Test縮小版のCache:
    """`QPainter` に縮めさせないための写しの入れ物（→ PySide6の落とし穴 10）。

    **鍵は「参照＋縮めた先の大きさ」。** 拡大率を変えれば別の大きさが要るので、
    参照だけを鍵にすると、拡大した瞬間に前の大きさの写しが出てくる。
    """

    @staticmethod
    def _image(w: int = 400, h: int = 300):
        from PySide6.QtGui import QImage

        return QImage(w, h, QImage.Format.Format_ARGB32)

    def test_2回目は縮め直さない(self, qapp):
        from manga_layout.images import ReducedCache, reduced_for

        cache = ReducedCache()
        source = self._image()
        calls = []

        def make():
            calls.append(1)
            return reduced_for(source, 100, 75)

        first = cache.get("ref", (100, 75), make)
        again = cache.get("ref", (100, 75), make)

        assert again is first
        assert len(calls) == 1

    def test_大きさが違えば作り直す(self, qapp):
        from manga_layout.images import ReducedCache, reduced_for

        cache = ReducedCache()
        source = self._image()
        small = cache.get("ref", (100, 75), lambda: reduced_for(source, 100, 75))
        big = cache.get("ref", (200, 150), lambda: reduced_for(source, 200, 150))

        assert small is not big
        assert (big.width(), big.height()) == (200, 150)
        assert len(cache) == 2

    def test_上限を超えたら古いものから捨てる(self, qapp):
        """拡大率は連続的に変わるので、持ちっぱなしにすると際限なく溜まる。"""
        from manga_layout.images import ReducedCache, reduced_for

        cache = ReducedCache(limit=3)
        source = self._image()
        for i in range(5):
            cache.get("ref", (10 + i, 10), lambda i=i: reduced_for(source, 10 + i, 10))

        assert len(cache) == 3

    def test_参照ごとに手放せる(self, qapp):
        """絵を消したときに、大きさ違いの写しがまとめて落ちること。"""
        from manga_layout.images import ReducedCache, reduced_for

        cache = ReducedCache()
        source = self._image()
        for size in ((100, 75), (200, 150)):
            cache.get("a", size, lambda s=size: reduced_for(source, *s))
        cache.get("b", (100, 75), lambda: reduced_for(source, 100, 75))

        cache.forget("a")

        assert len(cache) == 1

    def test_縮めた1枚は範囲全体を平均する(self, qapp):
        """`QPainter` の 2×2 と違い、捨てられる画素が無いこと。

        市松模様を半分に縮めると、**どの画素も白と黒の中間**になる。
        間引きだと白か黒のどちらかがそのまま残る。
        """
        from PySide6.QtGui import QImage, qGray

        from manga_layout.images import reduced_for

        source = QImage(64, 64, QImage.Format.Format_ARGB32)
        for y in range(64):
            for x in range(64):
                source.setPixel(x, y, 0xFF000000 if (x + y) % 2 else 0xFFFFFFFF)

        small = reduced_for(source, 32, 32)
        grays = {qGray(small.pixel(x, y)) for y in range(32) for x in range(32)}

        assert grays and all(60 < g < 195 for g in grays)
