"""縦書きの組み方の検証。

ここは **Qt を一切使わない**。テストは表示装置なし（offscreen）で動くが、
その環境では Windows のフォントが 1 つも読み込まれず、字の幅も高さも
でたらめな値になる（2026-08-03 実測: どの文字も送り幅が同じ、
`boundingRect` が 100000 を返す）。字形や寸法に依存した検証はここでは
成立しないので、計算だけを Qt から切り離してある。

字形そのもの（句読点の位置、長音符と括弧の回転）はフォントの縦書き字形が
行うため、**この層の責任ではない**。実機での確認は下見で済ませてある。
"""

from __future__ import annotations

import dataclasses

import pytest

from manga_layout.geometry import Rect
from manga_layout.vertical import COLUMN_PITCH, layout


def xs_of(glyphs) -> list[float]:
    """列の位置（重複を保ったまま、左上の x を並べる）。"""
    return [g.cell.x for g in glyphs]


class Test並び:
    def test_列は右から左へ進む(self):
        # 日本語の縦書きの並び。1 行目がいちばん右に来る
        glyphs = layout("あ\nい\nう", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        first, second, third = (g.cell.x for g in glyphs)
        assert first > second > third

    def test_文字は上から下へ並ぶ(self):
        glyphs = layout("あいう", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        ys = [g.cell.y for g in glyphs]
        assert ys == sorted(ys)
        # 同じ列なので x は揃う
        assert len(set(xs_of(glyphs))) == 1

    def test_手動改行で列が分かれる(self):
        # 折り返しはしない（要件定義 9章）。列の数は改行の数で決まる
        glyphs = layout("あい\nうえお", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        assert len(glyphs) == 5
        assert len(set(xs_of(glyphs))) == 2

    def test_文字の順序は元の並びのまま(self):
        glyphs = layout("あい\nうえ", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        assert [g.ch for g in glyphs] == ["あ", "い", "う", "え"]

    def test_空行も列を1つ使う(self):
        # 「あ」と「い」の間に空の列が入るので、間隔は 2 列ぶん開く
        glyphs = layout("あ\n\nい", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        assert [g.ch for g in glyphs] == ["あ", "い"]
        gap = glyphs[0].cell.x - glyphs[1].cell.x
        assert gap == pytest.approx(10.0 * COLUMN_PITCH * 2)


class Test字の枠:
    def test_1文字は字の大きさの正方形を占める(self):
        # 縦書きは字ごとの送り幅を使わず、正方形を単位に組む
        glyphs = layout("あ", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        cell = glyphs[0].cell
        assert (cell.w, cell.h) == (10.0, 10.0)

    def test_字は隙間なく縦に続く(self):
        glyphs = layout("あい", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        assert glyphs[1].cell.y - glyphs[0].cell.y == pytest.approx(10.0)

    def test_列の間隔は字の大きさに比例する(self):
        glyphs = layout("あ\nい", Rect(0.0, 0.0, 200.0, 100.0), 20.0)
        gap = glyphs[0].cell.x - glyphs[1].cell.x
        assert gap == pytest.approx(20.0 * COLUMN_PITCH)


class Test配置:
    def test_列の集まりは枠の左右中央に来る(self):
        rect = Rect(0.0, 0.0, 100.0, 100.0)
        glyphs = layout("あ\nい", rect, 10.0)
        centers = [g.cell.x + g.cell.w / 2.0 for g in glyphs]
        assert sum(centers) / len(centers) == pytest.approx(rect.center[0])

    def test_枠をずらすと字も同じだけずれる(self):
        base = layout("あい\nうえ", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        moved = layout("あい\nうえ", Rect(30.0, 7.0, 100.0, 100.0), 10.0)
        # 同じ文字列を置いただけなので字数は必ず揃う。strict=True で明示する
        for a, b in zip(base, moved, strict=True):
            assert b.cell.x == pytest.approx(a.cell.x + 30.0)
            assert b.cell.y == pytest.approx(a.cell.y + 7.0)


class Test整列:
    """`align` は横書き用の項目を読み替えて使う。

    「行の始まりに寄せる」が left。縦書きの列は上から始まるので上寄せになる。
    """

    def test_left_は上寄せ(self):
        glyphs = layout("あい", Rect(0.0, 0.0, 100.0, 100.0), 10.0, "left")
        assert glyphs[0].cell.y == pytest.approx(0.0)

    def test_right_は下寄せ(self):
        glyphs = layout("あい", Rect(0.0, 0.0, 100.0, 100.0), 10.0, "right")
        # 2 文字ぶん（20px）が枠の下端に接する
        assert glyphs[-1].cell.bottom == pytest.approx(100.0)

    def test_center_は上下中央(self):
        glyphs = layout("あい", Rect(0.0, 0.0, 100.0, 100.0), 10.0, "center")
        assert glyphs[0].cell.y == pytest.approx(40.0)

    def test_知らない値は中央として扱う(self):
        # 保存形式の検証を通った値しか来ないが、ここで落ちる必要はない
        glyphs = layout("あい", Rect(0.0, 0.0, 100.0, 100.0), 10.0, "なにか")
        assert glyphs[0].cell.y == pytest.approx(40.0)

    def test_列ごとに独立して寄せる(self):
        # 横書きが行ごとに独立して寄るのと同じ
        glyphs = layout("あ\nいうえ", Rect(0.0, 0.0, 100.0, 100.0), 10.0, "center")
        assert glyphs[0].cell.y == pytest.approx(45.0)  # 1 文字 → (100-10)/2
        assert glyphs[1].cell.y == pytest.approx(35.0)  # 3 文字 → (100-30)/2


class Testはみ出し:
    def test_枠に収まらなくても切り詰めない(self):
        # 隠すと、はみ出していることに気づけない（横書きで折り返さないのと同じ理由）
        glyphs = layout("あいうえおかきくけこ", Rect(0.0, 0.0, 50.0, 30.0), 10.0)
        assert len(glyphs) == 10
        assert glyphs[-1].cell.bottom > 30.0

    def test_列が多すぎても全部返す(self):
        many = "\n".join(["あ"] * 20)
        glyphs = layout(many, Rect(0.0, 0.0, 50.0, 50.0), 10.0)
        assert len(glyphs) == 20
        # 枠の左右にはみ出す。切り詰めないので左端は枠の外へ出る
        assert glyphs[-1].cell.x < 0.0


class Test境界:
    def test_空文字列は何も返さない(self):
        assert layout("", Rect(0.0, 0.0, 100.0, 100.0), 10.0) == []

    def test_大きさが0以下なら何も返さない(self):
        # 0 除算にはならないが、意味のある結果も出ないので早く抜ける
        assert layout("あ", Rect(0.0, 0.0, 100.0, 100.0), 0.0) == []
        assert layout("あ", Rect(0.0, 0.0, 100.0, 100.0), -5.0) == []

    def test_改行だけの内容は何も返さない(self):
        assert layout("\n\n", Rect(0.0, 0.0, 100.0, 100.0), 10.0) == []

    def test_結果は書き換えできない(self):
        glyphs = layout("あ", Rect(0.0, 0.0, 100.0, 100.0), 10.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            glyphs[0].ch = "い"  # type: ignore[misc]
