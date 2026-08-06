"""黒ベタのトーン化と、その保存形式（要件定義 10.1）。

`manga_layout.tone` は `images.py` と同じく Qt に依存するので、ここでは
**小さな画像を組み立てて画素を読む**（座標のまま確かめられる `flow.py` /
`focus.py` とは違う）。

確かめるのは「どこが選ばれたか」まで。**斜線の見た目は数では確かめられない**
ので、そこは目で決める（集中線・流線と同じ流儀 → 6.16、6.26）。

操作まわり（つまみ・メニュー・履歴）は tests/test_ui_tone.py。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QImage, QPainter

from manga_layout import ProjectFormatError, Rect, tone as TN
from manga_layout.model import (
    TONE_KIND_GRAY as KIND_GRAY,
    TONE_KIND_WHITE as KIND_WHITE,
    TONE_KINDS,
    ImageObject,
    Tone,
)

WHITE = QColor("#FFFFFF")
BLACK = QColor("#000000")


def make(**kwargs) -> Tone:
    """既定のトーン。試したい項目だけ差し替える。

    `thin=0.0` を既定にしてあるので、断らない限り**細さで落とす段は通らない**。
    そこを混ぜると、しきい値だけを見たいときに結果が読めなくなる。
    """
    values = dict(threshold=30, angle=45.0, pitch=0.02, density=0.35, thin=0.0)
    values.update(kwargs)
    return Tone(**values)


def canvas(w: int = 200, h: int = 200, fill: QColor = WHITE) -> QImage:
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    image.fill(fill)
    return image


def fill_box(image: QImage, x: int, y: int, w: int, h: int, color: QColor = BLACK) -> None:
    painter = QPainter(image)
    painter.fillRect(x, y, w, h, color)
    painter.end()


def masked(mask: QImage, x: int, y: int) -> bool:
    """その画素がトーンにする側かどうか。"""
    return QColor(mask.pixel(x, y)).red() > 127


# -- しきい値 ---------------------------------------------------------------


def test_暗い所が選ばれ_白い所は選ばれない():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    mask = TN.build_mask(image, make())
    assert masked(mask, 50, 50)
    assert not masked(mask, 150, 150)


def test_しきい値より明るい灰色は選ばれない():
    image = canvas()
    fill_box(image, 20, 20, 60, 60, QColor(80, 80, 80))
    assert not masked(TN.build_mask(image, make(threshold=30)), 50, 50)
    assert masked(TN.build_mask(image, make(threshold=100)), 50, 50)


def test_真っ黒でなくても拾う():
    """JPG や生成画像の黒ベタは、圧縮や階調でわずかに浮く（要件定義 10.1）。"""
    image = canvas()
    fill_box(image, 20, 20, 60, 60, QColor(12, 12, 12))
    assert masked(TN.build_mask(image, make(threshold=30)), 50, 50)


# -- 透明 -------------------------------------------------------------------


def test_透明な所は選ばれない():
    """`Format_Grayscale8` はアルファを捨てるので、先に白へ倒してある。"""
    image = QImage(200, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))  # 透明かつ黒
    fill_box(image, 20, 20, 60, 60)
    mask = TN.build_mask(image, make())
    assert masked(mask, 50, 50), "不透明な黒は選ばれる"
    assert not masked(mask, 150, 150), "透明な黒を拾うと背景一面がトーンになる"


def test_焼いても元の透明は残る():
    image = QImage(200, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    fill_box(image, 20, 20, 60, 60)
    out = TN.apply_tone(image, make())
    assert out.pixelColor(150, 150).alpha() == 0
    assert out.pixelColor(50, 50).alpha() == 255


# -- 細さで落とす -----------------------------------------------------------


def test_細い線は落ち_太いベタは残る():
    """線画の線を守る手（要件定義 10.1）。ここがこの機能の要。"""
    image = canvas()
    fill_box(image, 20, 20, 60, 60)  # ベタ
    fill_box(image, 120, 20, 2, 160)  # 細い線（2px）

    mask = TN.build_mask(image, make(thin=0.04))  # 短辺 200px の 4% = 8px
    assert masked(mask, 50, 50), "ベタは残る"
    assert not masked(mask, 120, 100), "細い線は落ちる"


def test_細さ0なら何も落とさない():
    image = canvas()
    fill_box(image, 120, 20, 2, 160)
    assert masked(TN.build_mask(image, make(thin=0.0)), 120, 100)


def test_細さの物差しは画像の寸法に対する割合():
    """画面用の縮小版と原寸で、落ちる線の太さが変わらないこと。"""
    small, large = canvas(200, 200), canvas(400, 400)
    fill_box(small, 100, 20, 4, 160)
    fill_box(large, 200, 40, 8, 320)  # 同じ割合の太さ

    tone = make(thin=0.04)
    assert not masked(TN.build_mask(small, tone), 100, 100)
    assert not masked(TN.build_mask(large, tone), 200, 200)


# -- 矩形で絞る -------------------------------------------------------------


def test_矩形の外は選ばれない():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    fill_box(image, 120, 120, 60, 60)

    mask = TN.build_mask(image, make(area=Rect(0.0, 0.0, 0.5, 0.5)))
    assert masked(mask, 50, 50), "矩形の中のベタは選ばれる"
    assert not masked(mask, 150, 150), "外のベタは元の黒のまま残る"


def test_矩形を省くと画像全体():
    image = canvas()
    fill_box(image, 120, 120, 60, 60)
    assert masked(TN.build_mask(image, make(area=None)), 150, 150)


def test_はみ出した矩形は弾かずに画像の縁で切る():
    image = canvas()
    fill_box(image, 120, 120, 60, 60)
    mask = TN.build_mask(image, make(area=Rect(0.5, 0.5, 5.0, 5.0)))
    assert masked(mask, 150, 150)


# -- 焼いた結果 -------------------------------------------------------------


def test_ベタが白と黒の両方を持つようになる():
    """置き換わっていれば、元は真っ黒一色だった所に白が現れる。"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    out = TN.apply_tone(image, make())

    band = [QColor(out.pixel(x, 100)).red() for x in range(30, 170)]
    assert max(band) > 200, "白地が現れる"
    assert min(band) < 60, "線は黒のまま残る"


def test_選ばれなかった所は1画素も変えない():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    out = TN.apply_tone(image, make())
    assert QColor(out.pixel(150, 150)).name() == "#ffffff"


def test_元の画像は書き換えない():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    TN.apply_tone(image, make())
    assert QColor(image.pixel(50, 50)).name() == "#000000"


def test_向きを変えると絵が変わる():
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    a = TN.apply_tone(image, make(angle=0.0))
    b = TN.apply_tone(image, make(angle=90.0))
    assert a.constBits() != b.constBits()


# -- 種類（斜線・灰色・白抜き → 6.27）---------------------------------------


def beta(out: QImage) -> list[int]:
    """置き換えた所を横に読んだ明るさの並び。"""
    return [QColor(out.pixel(x, 100)).red() for x in range(30, 170)]


def test_灰色は置き換えた所が一様になる():
    """斜線は白と黒が交互に出るが、灰色はどこを読んでも同じ値。"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    band = beta(TN.apply_tone(image, make(kind=KIND_GRAY)))
    assert len(set(band)) == 1


def test_灰色の濃さは濃さの値で決まる():
    """`density` を斜線と共用する。0.35 なら 35% の黒（→ 6.27）。"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    light = beta(TN.apply_tone(image, make(kind=KIND_GRAY, density=0.1)))[0]
    dark = beta(TN.apply_tone(image, make(kind=KIND_GRAY, density=0.8)))[0]
    assert light == round(255 * 0.9)
    assert dark == round(255 * 0.2)
    assert light > dark, "濃くすると暗くなる"


def test_白抜きは置き換えた所が白くなる():
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    band = beta(TN.apply_tone(image, make(kind=KIND_WHITE)))
    assert set(band) == {255}


def test_白抜きは濃さも向きも効かない():
    """効かない値を持っていても絵は変わらない（メニューはグレーにする）。"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    a = TN.apply_tone(image, make(kind=KIND_WHITE, density=0.1, angle=0.0))
    b = TN.apply_tone(image, make(kind=KIND_WHITE, density=0.8, angle=90.0))
    assert a.constBits() == b.constBits()


def test_種類が違えば選ぶ所は同じでも絵が変わる():
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    tone = make()
    stripes = TN.apply_tone(image, tone)
    assert TN.build_mask(image, tone).constBits() == TN.build_mask(
        image, make(kind=KIND_GRAY)
    ).constBits(), "どこを置き換えるかは種類で変わらない"
    assert stripes.constBits() != TN.apply_tone(image, make(kind=KIND_GRAY)).constBits()


def test_種類を変えても選ばれなかった所は元のまま():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    for kind in (KIND_GRAY, KIND_WHITE):
        out = TN.apply_tone(image, make(kind=kind))
        assert QColor(out.pixel(150, 150)).name() == "#ffffff"


def test_種類には全部に日本語の名前がある():
    """メニューの項目名と状態表示が同じ名前を使う（→ `KIND_LABELS`）。"""
    assert set(TN.KIND_LABELS) == set(TONE_KINDS)


# -- PSD 用に3枚へ分ける（→ 6.28）-------------------------------------------


def test_トーン範囲の中は黒く_外は透明():
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    out = TN.tone_pieces(image, make()).area
    # **透明度は `pixelColor` で見る。** `QColor(image.pixel(...))` は
    # 透明度を捨てて常に不透明として読むので、外側も 255 に見える
    inside = out.pixelColor(50, 50)
    assert (inside.red(), inside.alpha()) == (0, 255)
    assert out.pixelColor(150, 150).alpha() == 0


def test_白ベタの中は白く_外は透明():
    """元の絵の黒ベタを隠すためのもの（→ 6.28）。"""
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    out = TN.tone_pieces(image, make()).fill
    inside = out.pixelColor(50, 50)
    assert (inside.red(), inside.alpha()) == (255, 255)
    assert out.pixelColor(150, 150).alpha() == 0


def test_トーンだけの1枚は外が透明():
    """絵を含まない。**利用者はこれを消して自分のトーンに差し替える。**"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    out = TN.tone_pieces(image, make(kind=KIND_GRAY)).pattern
    assert out.pixelColor(100, 100).alpha() == 255
    assert out.pixelColor(5, 5).alpha() == 0


def test_白ベタの上にトーンを重ねると焼いた1枚になる():
    """**等倍で重ねれば `apply_tone` と一致する**（→ 6.28）。"""
    from PySide6.QtGui import QPainter

    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    tone = make(kind=KIND_GRAY)
    pieces = TN.tone_pieces(image, tone)

    stacked = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(stacked)
    painter.drawImage(0, 0, pieces.fill)
    painter.drawImage(0, 0, pieces.pattern)
    painter.end()
    assert stacked.constBits() == TN.apply_tone(image, tone).constBits()


def test_3枚とも絵と同じ大きさ():
    """クリスタで位置を合わせ直さずに済む（→ 6.28）。"""
    image = canvas(240, 160)
    fill_box(image, 20, 20, 60, 60)
    pieces = TN.tone_pieces(image, make())
    assert pieces.area.size() == image.size()
    assert pieces.fill.size() == image.size()
    assert pieces.pattern.size() == image.size()


def test_トーン範囲は種類で変わらない():
    """置き換えた先が違うだけで、選ぶ所は同じ（→ `build_mask` を共用）。"""
    image = canvas()
    fill_box(image, 20, 20, 60, 60)
    a = TN.tone_pieces(image, make()).area
    b = TN.tone_pieces(image, make(kind=KIND_WHITE)).area
    assert a.constBits() == b.constBits()


def test_3枚とも矩形で絞られる():
    """絵と同じ範囲を指す。ずれると、クリスタで貼ってから気づくことになる。"""
    image = canvas()
    fill_box(image, 20, 20, 160, 160)
    pieces = TN.tone_pieces(image, make(area=Rect(0.0, 0.0, 0.5, 0.5)))
    for out in (pieces.area, pieces.fill, pieces.pattern):
        assert out.pixelColor(50, 50).alpha() == 255
        assert out.pixelColor(150, 150).alpha() == 0


# -- 保存形式 ---------------------------------------------------------------


def test_往復しても変わらない():
    tone = make(area=Rect(0.1, 0.2, 0.3, 0.4))
    again = Tone.from_dict(tone.to_dict(), "tone")
    assert again == tone


def test_矩形が無ければ項目ごと書かない():
    assert "area" not in make(area=None).to_dict()


def test_トーンの無い画像は項目ごと書かない():
    """使っていない作品の project.json は今までと同じ内容のままになる。"""
    image = ImageObject(id="img_0001", asset="assets/a.png")
    assert "tone" not in image.to_dict()


def test_画像に付けて往復できる():
    image = ImageObject(id="img_0001", asset="assets/a.png", tone=make())
    again = ImageObject.from_dict(image.to_dict(), "img")
    assert again.tone == image.tone


@pytest.mark.parametrize(
    "broken",
    [
        {"threshold": 300},
        {"threshold": -1},
        {"pitch": 1.5},
        {"density": -0.1},
        {"thin": 2.0},
    ],
)
def test_範囲外は切り詰めずに弾く(broken):
    """黙って直すと、保存のたびに設定が変わる（`FocusLines` と同じ方針）。"""
    data = make().to_dict()
    data.update(broken)
    with pytest.raises(ProjectFormatError):
        Tone.from_dict(data, "tone")


def test_向きだけは弾かずに畳む():
    """角度は周期的な量。弾くと -45 度のような正しい値まで読めなくなる。"""
    data = make().to_dict()
    data["angle"] = 405.0
    assert Tone.from_dict(data, "tone").angle == pytest.approx(45.0)


def test_既定値は設定から作る():
    """出発点を2か所に持たない（`FlowSettings` と同じ）。"""
    s = TN.DEFAULT_TONE_SETTINGS
    tone = TN.default_tone()
    assert (tone.threshold, tone.angle, tone.pitch) == (s.threshold, s.angle, s.pitch)
    assert tone.area is None, "入れた時点では絞らない"


# -- 何段目か（メニューに出す → 6.27）---------------------------------------


def test_端から端までの段数():
    """4つとも 0 から数え、上限は押し切ったときの回数。"""
    assert TN.level_max("thin") == 20
    assert TN.level_max("threshold") == 20
    assert TN.level_max("pitch") == 24
    assert TN.level_max("density") == 17


def test_入れた直後の段数():
    """**0 段目から始まるとは限らない。** 押した回数を数えられない理由。"""
    s = TN.DEFAULT_TONE_SETTINGS
    assert TN.level_of("thin", s.thin) == 2
    assert TN.level_of("threshold", s.threshold) == 3
    assert TN.level_of("pitch", s.pitch) == 3
    assert TN.level_of("density", s.density) == 6


def test_1回押すと1段進む():
    value = TN.DEFAULT_TONE_SETTINGS.thin
    for expected in (3, 4, 5):
        value = TN.stepped_thin(value, 1)
        assert TN.level_of("thin", value) == expected


def test_連打しても割り算の誤差で狂わない():
    """0.001 を足し続けると 0.006000000000000001 のような値になる。"""
    value = TN.THIN_MIN
    for expected in range(1, TN.level_max("thin") + 1):
        value = TN.stepped_thin(value, 1)
        assert TN.level_of("thin", value) == expected


def test_拾う黒は下端だけ刻みからはみ出す():
    """4 → 10 → 20 … と並ぶ（下限 4、刻み 10）。**端も 0 段目に収まる。**"""
    assert TN.level_of("threshold", TN.THRESHOLD_MIN) == 0
    assert TN.level_of("threshold", 10) == 1
    assert TN.level_of("threshold", TN.THRESHOLD_MAX) == TN.level_max("threshold")


def test_範囲の外は丸めて収める():
    """手で書き換えた project.json でも、表示だけは崩さない。"""
    assert TN.level_of("thin", -1.0) == 0
    assert TN.level_of("thin", 99.0) == TN.level_max("thin")


def test_出す形は何段目か_全部で何段か():
    assert TN.level_label("thin", TN.DEFAULT_TONE_SETTINGS.thin) == "2/20"


# -- 覚えておく1枚 -----------------------------------------------------------


def test_設定が違えば別の鍵になる():
    assert make().key() != make(angle=90.0).key()
    assert make(area=Rect(0, 0, 1, 1)).key() != make(area=None).key()


def test_設定が同じなら同じ鍵になる():
    assert make(area=Rect(0.1, 0.2, 0.3, 0.4)).key() == make(
        area=Rect(0.1, 0.2, 0.3, 0.4)
    ).key()
