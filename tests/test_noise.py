"""形のばらつきに使う擬似乱数（要件定義 6.16、6.26）。

集中線と流線の両方がここを使う。**どちらの形の話も入れない**——ここが
落ちたときに、乱数そのものの話なのか、それを使う側の話なのかを取り違え
ないようにするため。

元は tests/test_focus.py にあった。流線（6.26）が同じ乱数を使うことに
なった時点で、`manga_layout/noise.py` として集中線から切り出している。
"""

from __future__ import annotations

from manga_layout import noise as N


def test_隣り合う種でも最初の値が似ない():
    """種をそのまま初期値にすると、線形合同法は**隣り合う種で最初の数個が
    近い値**になる（1本目・2本目の向きが揃って見える）。先に大きな奇数を
    掛けて混ぜている（→ `Noise._scramble`）ことの確認。
    """
    got = [N.Noise(seed).unit() for seed in (100, 101, 102)]
    assert abs(got[0] - got[1]) > 0.1
    assert abs(got[1] - got[2]) > 0.1


def test_乱数は既知の並びを返す():
    """**将来この値が変わったら、既にある作品の形が変わっている。**

    `random` を使わず自前で回している理由がここ（→ 要件定義 6.16）。
    引っかかったときは、乱数の作り方を変えてよいかどうかから考えること。
    """
    n = N.Noise(12345)
    got = [round(n.unit(), 6) for _ in range(3)]
    assert got == [0.034107, 0.536223, 0.138532]


def test_同じ種からは同じ並びが出る():
    """集中線と流線の両方が、この性質の上に立っている。"""
    assert [N.Noise(7).unit() for _ in range(5)] == [N.Noise(7).unit() for _ in range(5)]


def test_符号つきは負も正も返す():
    values = [N.Noise(99).signed() for _ in range(1)]
    many = [N.Noise(seed).signed() for seed in range(40)]
    assert all(-1.0 <= v < 1.0 for v in many + values)
    assert any(v < 0.0 for v in many)
    assert any(v > 0.0 for v in many)


def test_種は毎回違う():
    assert len({N.new_seed() for _ in range(20)}) > 1
