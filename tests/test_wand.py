"""自動領域選択（塗りつぶし選択 → `manga_layout/wand.py`）。

**この層は AI を知らない。** 指した1点から、線で囲まれた区画を取るだけ。
得意・不得意がはっきりしているので、**できることと同じくらい、崩れ方も
ここで固定する**（隙間から漏れる／グラデーションで止まる）。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from manga_layout.errors import MaskSizeError
from manga_layout.image_masks import is_binary
from manga_layout.images import size_px
from manga_layout.wand import (
    DEFAULT_TOLERANCE,
    LEAK_RATIO,
    combined,
    inverted,
    removed,
    select_at,
)

W = H = 120


def canvas(color="#FFFFFF") -> QImage:
    image = QImage(W, H, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def boxed(gap: int = 0) -> QImage:
    """白地の中に、黒い枠で囲った四角を1つ描く。`gap` は枠の右辺に開ける隙間。

    枠の中（30〜90）と外（それ以外）が、線1本で分かれている状態。
    """
    image = canvas()
    painter = QPainter(image)
    painter.setPen(QColor("#000000"))
    for x in range(30, 91):
        painter.drawPoint(x, 30)
        painter.drawPoint(x, 90)
    for y in range(30, 91):
        painter.drawPoint(30, y)
        if not (60 <= y < 60 + gap):  # 右辺にだけ隙間を開ける
            painter.drawPoint(90, y)
    painter.end()
    return image


def gradient() -> QImage:
    """左から右へ、白から黒へなめらかに変わる絵。"""
    image = QImage(W, H, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    for x in range(W):
        v = 255 - round(x * 255 / (W - 1))
        painter.setPen(QColor(v, v, v))
        painter.drawLine(x, 0, x, H - 1)
    painter.end()
    return image


class Test囲まれた区画を取る:
    def test_中を指すと中だけ選ばれる(self, qapp):
        chosen = select_at(boxed(), (60, 60))
        assert not chosen.empty
        # 枠の中は 59×59 ほど。外（1万画素以上）まで広がっていないことを見る
        assert 3000 < chosen.count < 4000
        assert chosen.mask.pixelColor(60, 60).value() == 255, "中は選ばれている"
        assert chosen.mask.pixelColor(10, 10).value() == 0, "外は選ばれていない"

    def test_外を指すと外だけ選ばれる(self, qapp):
        chosen = select_at(boxed(), (5, 5))
        assert chosen.mask.pixelColor(60, 60).value() == 0, "枠の中には入らない"
        assert chosen.mask.pixelColor(5, 5).value() == 255

    def test_マスクは元画像と同じ寸法で白黒だけ(self, qapp):
        chosen = select_at(boxed(), (60, 60))
        assert size_px(chosen.mask) == (W, H), "そのまま `apply_mask` に渡せる形"
        assert is_binary(chosen.mask)

    def test_線の上を指すと線そのものが選ばれる(self, qapp):
        """**指した所と似た濃さが続く範囲**を返すだけで、線か中身かは知らない。

        枠は 61×61 の口の字なので、選ばれるのは 240 画素前後になる。
        「線だけ選んで消す」使い方がそのまま乗る。
        """
        chosen = select_at(boxed(), (30, 60))
        assert 200 < chosen.count < 300
        assert chosen.mask.pixelColor(30, 60).value() == 255, "指した線"
        assert chosen.mask.pixelColor(60, 60).value() == 0, "囲まれた中は入らない"
        assert chosen.mask.pixelColor(5, 5).value() == 0, "外も入らない"

    def test_画像の外を指しても落ちない(self, qapp):
        assert select_at(boxed(), (-1, 5)).empty
        assert select_at(boxed(), (W, 5)).empty


class Test崩れ方を固定する:
    """**できることと同じくらい、どう崩れるかが大事**（→ `data/` の検討メモ）。"""

    def test_1画素の隙間で外とつながる(self, qapp):
        """線が閉じていることが前提。**隙間があれば、そこから外へ出る。**

        画面が「線に隙間があるかもしれません」と言えるように、
        `ratio` で気づける形にしてある（→ `Selection.leaked`）。
        """
        閉じている = select_at(boxed(gap=0), (60, 60))
        隙間あり = select_at(boxed(gap=1), (60, 60))
        assert 隙間あり.count > 閉じている.count * 3, "中だけでは済まなくなる"
        assert 隙間あり.mask.pixelColor(5, 5).value() == 255, "外まで出ている"

    def test_漏れたら割合で気づける(self, qapp):
        隙間あり = select_at(boxed(gap=1), (60, 60))
        assert 隙間あり.ratio >= LEAK_RATIO
        assert 隙間あり.leaked is True

    def test_背景を選ぶだけでは漏れ扱いにしない(self, qapp):
        """**正しい操作で警告を出さない。** 背景クリックは普通に広い。"""
        背景 = select_at(boxed(), (5, 5))
        # 実画像（2048×2048 の線画）で背景をクリックしたときが 84.5%。
        # ここは試験用の絵なので四角が大きく、74% ほどになる
        assert 背景.ratio > 0.7, "画面の大半が背景なのは普通"
        assert 背景.leaked is False, f"{LEAK_RATIO} までは漏れと見ない"

    def test_グラデーションでは許容差で決まる(self, qapp):
        """地が一様でない絵では、つまみ次第でどこまでも広がる（→ 検討メモ）。"""
        絵 = gradient()
        狭い = select_at(絵, (60, 60), tolerance=4)
        広い = select_at(絵, (60, 60), tolerance=64)
        assert 狭い.count < 広い.count / 5
        assert 広い.ratio > 0.4, "半分近くまで流れ出す"


class Test組み合わせ:
    def test_反転すると入れ替わる(self, qapp):
        中 = select_at(boxed(), (60, 60)).mask
        外 = inverted(中)
        assert 外.pixelColor(60, 60).value() == 0
        assert 外.pixelColor(5, 5).value() == 255

    def test_反転すると線が中身の側に付く(self, qapp):
        """**輪郭が痩せないのはこのため**（→ `inverted` の注記）。"""
        背景 = select_at(boxed(), (5, 5)).mask
        中身 = inverted(背景)
        assert 中身.pixelColor(30, 60).value() == 255, "枠線そのものが残る"
        assert 中身.pixelColor(60, 60).value() == 255, "囲まれた中も残る"

    def test_足せる(self, qapp):
        中 = select_at(boxed(), (60, 60)).mask
        外 = select_at(boxed(), (5, 5)).mask
        両方 = combined(中, 外)
        assert 両方.pixelColor(60, 60).value() == 255
        assert 両方.pixelColor(5, 5).value() == 255

    def test_引ける(self, qapp):
        全部 = inverted(select_at(canvas("#000000"), (0, 0)).mask)  # 何も無い
        中 = select_at(boxed(), (60, 60)).mask
        assert 全部.pixelColor(60, 60).value() == 0, "白紙を反転すれば空"
        残り = removed(combined(全部, 中), 中)
        assert 残り.pixelColor(60, 60).value() == 0, "足してから引けば元に戻る"

    def test_大きさが違えば断る(self, qapp):
        小さい = select_at(boxed(), (60, 60)).mask
        大きい = select_at(QImage(200, 200, QImage.Format.Format_ARGB32), (5, 5)).mask
        with pytest.raises(MaskSizeError):
            combined(小さい, 大きい)


def test_既定の許容差は白地の線画で通る(qapp):
    """白は 255 に張り付いているので、少しの汚れなら1区画にまとまる。"""
    汚れた白 = canvas("#FDFDFD")
    painter = QPainter(汚れた白)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawLine(0, 60, W - 1, 60)
    painter.end()
    chosen = select_at(汚れた白, (5, 5), tolerance=DEFAULT_TOLERANCE)
    assert chosen.ratio == 1.0, "2〜3 の濃さの違いで区画が割れない"


def test_空の画像でも落ちない(qapp):
    """手で書き換えた project.json など、思わぬ入力で止まらないこと。"""
    assert select_at(QImage(), (0, 0)).empty
