"""図形（mm 単位のページ座標）の検証。"""

from __future__ import annotations

import dataclasses

import pytest

from manga_layout import Polygon, Rect, Size, fit_rect, fit_size
from manga_layout.errors import ProjectFormatError


class TestRect:
    def test_端の座標を求められる(self):
        r = Rect(10.0, 20.0, 30.0, 40.0)
        assert r.right == 40.0
        assert r.bottom == 60.0
        assert r.center == (25.0, 40.0)

    def test_右下から左上へ描いた矩形を正の値に直す(self):
        # ドラッグで矩形を描くとき、逆方向に引くと幅・高さが負になる
        r = Rect(50.0, 60.0, -30.0, -40.0).normalized()
        assert (r.x, r.y, r.w, r.h) == (20.0, 20.0, 30.0, 40.0)

    def test_移動しても元の矩形は変わらない(self):
        original = Rect(10.0, 10.0, 20.0, 20.0)
        moved = original.translated(5.0, -3.0)
        assert (moved.x, moved.y) == (15.0, 7.0)
        assert (original.x, original.y) == (10.0, 10.0)

    def test_書き換えできない(self):
        # 複数のオブジェクトが同じ矩形を共有して片方の移動が両方に及ぶ、
        # という追いにくい不具合を構造的に防いでいる
        with pytest.raises(dataclasses.FrozenInstanceError):
            Rect(0.0, 0.0, 1.0, 1.0).x = 5.0  # type: ignore[misc]

    def test_重なりを判定できる(self):
        a = Rect(0.0, 0.0, 10.0, 10.0)
        assert a.intersects(Rect(5.0, 5.0, 10.0, 10.0))
        assert not a.intersects(Rect(20.0, 20.0, 5.0, 5.0))

    def test_辞書と往復できる(self):
        r = Rect(1.5, 2.5, 3.5, 4.5)
        assert Rect.from_dict(r.to_dict(), "where") == r

    def test_数値でない値を拒む(self):
        with pytest.raises(ProjectFormatError, match="rect.x"):
            Rect.from_dict({"x": "10", "y": 0, "w": 1, "h": 1}, "rect")

    def test_真偽値を数値として受け入れない(self):
        # bool は Python では int の一種なので、素通ししやすい
        with pytest.raises(ProjectFormatError):
            Rect.from_dict({"x": True, "y": 0, "w": 1, "h": 1}, "rect")

    def test_項目が足りなければ位置を示して落ちる(self):
        with pytest.raises(ProjectFormatError, match="必須の項目 'h'"):
            Rect.from_dict({"x": 0, "y": 0, "w": 1}, "pages[0].floating[2].rect")


class TestSize:
    def test_ゼロや負の寸法を拒む(self):
        with pytest.raises(ProjectFormatError, match="0 より大きい"):
            Size.from_dict({"w": 0, "h": 297.0}, "size")
        with pytest.raises(ProjectFormatError):
            Size.from_dict({"w": 210.0, "h": -1.0}, "size")


class TestPolygon:
    def test_矩形から作ると時計回りの4点になる(self):
        p = Polygon.from_rect(Rect(10.0, 20.0, 30.0, 40.0))
        assert p.points == ((10.0, 20.0), (40.0, 20.0), (40.0, 60.0), (10.0, 60.0))

    def test_外接矩形を求められる(self):
        p = Polygon(((0.0, 0.0), (10.0, 5.0), (8.0, 12.0)))
        assert p.bounds() == Rect(0.0, 0.0, 10.0, 12.0)

    def test_軸並行の長方形かを見分ける(self):
        assert Polygon.from_rect(Rect(0.0, 0.0, 10.0, 10.0)).is_axis_aligned_rect()
        # 斜めコマ（台形）は長方形ではない
        slanted = Polygon(((0.0, 0.0), (10.0, 2.0), (10.0, 12.0), (0.0, 10.0)))
        assert not slanted.is_axis_aligned_rect()
        # 三角形も
        assert not Polygon(((0.0, 0.0), (10.0, 0.0), (5.0, 10.0))).is_axis_aligned_rect()

    def test_矩形として取り出せる(self):
        r = Rect(5.0, 6.0, 7.0, 8.0)
        assert Polygon.from_rect(r).as_rect() == r
        assert Polygon(((0.0, 0.0), (10.0, 0.0), (5.0, 10.0))).as_rect() is None

    def test_斜めコマも保存形式として扱える(self):
        # MVP の操作は矩形だけだが、保存形式は最初から多角形にしてある
        slanted = Polygon(((0.0, 0.0), (100.0, 8.0), (100.0, 60.0), (0.0, 52.0)))
        assert Polygon.from_list(slanted.to_list(), "shape.points") == slanted

    def test_3点未満は作れない(self):
        with pytest.raises(ValueError):
            Polygon(((0.0, 0.0), (1.0, 1.0)))
        with pytest.raises(ProjectFormatError, match="3点以上"):
            Polygon.from_list([[0, 0], [1, 1]], "shape.points")

    def test_壊れた頂点は位置を示して落ちる(self):
        with pytest.raises(ProjectFormatError, match=r"points\[1\]"):
            Polygon.from_list([[0, 0], [1, 2, 3], [4, 5]], "shape.points")


class TestFitSize:
    """「コマにフィット」の計算。極端な画像で 0 や負を返さないことが肝。"""

    def test_枠に収める(self):
        assert fit_size(1200, 900, 90.0, 60.0) == pytest.approx((80.0, 60.0))

    def test_枠を埋める(self):
        w, h = fit_size(1200, 900, 90.0, 60.0, cover=True)
        assert (w, h) == pytest.approx((90.0, 67.5))
        # 埋める側は枠より小さくなってはいけない
        assert w >= 90.0 - 1e-9 and h >= 60.0 - 1e-9

    def test_1x1の画像でも破綻しない(self):
        # fixtures/pixel_1x1.png 相当。極端な拡大でゼロ除算しないこと
        w, h = fit_size(1, 1, 90.0, 60.0)
        assert (w, h) == pytest.approx((60.0, 60.0))

    def test_極端な縦横比でも幅が0にならない(self):
        # fixtures/tall_1x256.png / wide_256x1.png 相当。
        # ここで 0 が出ると、以降の拡大縮小がすべて壊れる
        w, h = fit_size(1, 256, 90.0, 60.0)
        assert w > 0.0 and h == pytest.approx(60.0)

        w, h = fit_size(256, 1, 90.0, 60.0)
        assert h > 0.0 and w == pytest.approx(90.0)

    def test_不正な寸法は例外にする(self):
        with pytest.raises(ValueError):
            fit_size(0, 100, 90.0, 60.0)
        with pytest.raises(ValueError):
            fit_size(100, 100, 0.0, 60.0)

    def test_枠の中央に配置される(self):
        bounds = Rect(10.0, 10.0, 90.0, 60.0)
        placed = fit_rect(1200, 900, bounds)
        assert placed.center == pytest.approx(bounds.center)
        assert placed.w == pytest.approx(80.0)
