"""次コマ提案の定石の検証。

`joseki.py` は**画面もモデルも知らない**ので、ここでも Qt を起動しない。
入るのは正規化した矩形の並びだけで、出るのも矩形。

押さえたいのは4点。

- **どの定石が、どんな並びで当たるか。** 条件を1つ外すと当たらなくなること
- **埋まったページには提案が出ないこと。** 余白に細いコマを置き始めると邪魔になる
- **順は優先度で決まること。** 余裕度（説明用の数）は順に関与しない
- **提案は「案」の単位で数えること。** 1つの定石が幅ちがいの案を複数出す

コマの座標は**この検証のために組んだ数字**で、特定のページを写したものではない。
"""

from __future__ import annotations

import pytest

from manga_layout.joseki import (
    NO_CONTEXT,
    NBox,
    PageContext,
    PreviousPage,
    match_all,
    proposals,
)

# --- 材料（すべて読み順） -----------------------------------------------------

# 右半分に縦4コマ。左半分は空。断ち切りなし
RIGHT_COLUMN = [
    NBox(0.52, 0.05, 0.42, 0.20),
    NBox(0.52, 0.29, 0.42, 0.20),
    NBox(0.52, 0.53, 0.42, 0.20),
    NBox(0.52, 0.77, 0.42, 0.20),
]

# 1段目だけ描かれたページ（左右2コマ）。断ち切りなし
TOP_BAND = [NBox(0.52, 0.06, 0.42, 0.28), NBox(0.06, 0.06, 0.42, 0.28)]

# 同じ形だが、1段目がページ端まで抜けている
TOP_BAND_BLEED = [NBox(0.50, 0.00, 0.50, 0.34), NBox(0.00, 0.00, 0.48, 0.34)]

# 2段目の右コマまで描かれ、その左が空いている
BAND_HALF_DRAWN = [*TOP_BAND, NBox(0.52, 0.38, 0.42, 0.22)]

# 3段とも埋まったページ
FULL_PAGE = [
    *TOP_BAND,
    NBox(0.52, 0.38, 0.42, 0.22),
    NBox(0.06, 0.38, 0.42, 0.22),
    NBox(0.52, 0.64, 0.42, 0.30),
    NBox(0.06, 0.64, 0.42, 0.30),
]

# 右上に細いコマが1つだけ（幅は 1/3 以下）
NARROW_TOP = [NBox(0.64, 0.06, 0.30, 0.28)]


def hit(boxes, joseki_id, ctx=NO_CONTEXT):
    """その定石が当たったか。当たっていれば Match を、外れていれば None を返す。"""
    for m in match_all(boxes, ctx):
        if m.joseki == joseki_id:
            return m if m.matched else None
    raise AssertionError(f"定石表にない: {joseki_id}")


def check_of(boxes, joseki_id, name):
    for m in match_all(boxes, NO_CONTEXT):
        if m.joseki == joseki_id:
            for c in m.checks:
                if c.name == name:
                    return c
    raise AssertionError(f"条件が見つからない: {name}")


def offered(boxes, ctx=NO_CONTEXT):
    return [m.joseki for m, _plan in proposals(match_all(boxes, ctx))]


class TestVerticalStripRepeat:
    """縦コマ列の左右反復。"""

    def test_右列だけなら左列を反転して出す(self):
        m = hit(RIGHT_COLUMN, "vertical_strip_repeat")
        assert m is not None
        assert len(m.plans) == 1
        assert len(m.plans[0].candidates) == 4

    def test_提案は右列の左右反転(self):
        m = hit(RIGHT_COLUMN, "vertical_strip_repeat")
        for candidate, source in zip(m.plans[0].candidates, RIGHT_COLUMN, strict=True):
            assert candidate.box.x == pytest.approx(1.0 - source.right)
            assert candidate.box.y == source.y
            assert candidate.box.w == source.w

    def test_読み順は右列の続き(self):
        m = hit(RIGHT_COLUMN, "vertical_strip_repeat")
        assert [c.order for c in m.plans[0].candidates] == [5, 6, 7, 8]

    def test_断ち切りがあれば当たらない(self):
        assert hit(TOP_BAND_BLEED, "vertical_strip_repeat") is None

    def test_コマが2個では当たらない(self):
        assert hit(RIGHT_COLUMN[:2], "vertical_strip_repeat") is None

    def test_コマ2個では間隔を判定できない(self):
        # 間隔が1個しかないと差は必ず 0 になる。**通しても落としてもいけない**
        c = check_of(RIGHT_COLUMN[:2], "vertical_strip_repeat", "縦の間隔がほぼ一定")
        assert c.passed is None
        assert c.mark == "?"


class TestBandRight:
    """帯の右コマ（三段の中段・下段）。幅を3案並べる。"""

    def test_1段目だけなら三段の中段右が出る(self):
        m = hit(TOP_BAND, "three_band_middle_right")
        assert m is not None
        assert [p.label for p in m.plans] == ["幅 1/3", "幅 1/2", "幅 2/3"]

    def test_2段目まで描かれていたら三段の予想はしない(self):
        assert hit(BAND_HALF_DRAWN, "three_band_middle_right") is None

    def test_断ち切りの有無で定石が入れ替わる(self):
        assert hit(TOP_BAND_BLEED, "bleed_top_bottom_right") is not None
        assert hit(TOP_BAND_BLEED, "two_tier_bottom_right") is None
        assert hit(TOP_BAND, "two_tier_bottom_right") is not None
        assert hit(TOP_BAND, "bleed_top_bottom_right") is None

    def test_提案は右端に寄る(self):
        m = hit(TOP_BAND, "two_tier_bottom_right")
        right_edges = {round(p.candidates[0].box.right, 6) for p in m.plans}
        assert len(right_edges) == 1      # 幅が変わっても右端は動かない


class TestNarrowTop:
    """細い右上コマの左隣。"""

    def test_幅が3分の1以下なら左へ(self):
        assert hit(NARROW_TOP, "narrow_top_beside_left") is not None

    def test_3分の1を超えたら当たらない(self):
        wide = [NBox(0.52, 0.06, 0.42, 0.28)]
        assert hit(wide, "narrow_top_beside_left") is None


class TestFillBandLeft:
    """同じ段の左を埋める。"""

    def test_右コマだけの段があれば左を出す(self):
        m = hit(BAND_HALF_DRAWN, "fill_band_left")
        assert m is not None
        box = m.plans[0].candidates[0].box
        # 段に合わせる。高さは引き算で出すので、丸め誤差を許して比べる
        assert box.y == BAND_HALF_DRAWN[2].y
        assert box.h == pytest.approx(BAND_HALF_DRAWN[2].h)

    def test_次のコマを飛ばさない(self):
        # 段の途中で止まっていたら、次は「その段の左」。下の段へ行ってはいけない
        assert offered(BAND_HALF_DRAWN)[0] == "fill_band_left"

    def test_段のコマより細い空きは埋めない(self):
        # 左に少しだけ空きがあるページ。**余白であって、コマを置く場所ではない**
        sliver = [NBox(0.52, 0.06, 0.42, 0.28), NBox(0.10, 0.06, 0.38, 0.28)]
        assert hit(sliver, "fill_band_left") is None


class TestSpreadOpening:
    """見開きドン（空白ページの描き出し）。"""

    def context(self, number):
        return PageContext(
            number=number, previous=PreviousPage(number=number - 1, boxes=FULL_PAGE)
        )

    def test_直前が奇数ページなら当たる(self):
        assert hit([], "spread_opening_bleed", self.context(4)) is not None

    def test_直前が偶数ページなら当たらない(self):
        assert hit([], "spread_opening_bleed", self.context(5)) is None

    def test_コマがあるページには出さない(self):
        assert hit(TOP_BAND, "spread_opening_bleed", self.context(4)) is None

    def test_提案は幅いっぱいの帯(self):
        m = hit([], "spread_opening_bleed", self.context(4))
        box = m.plans[0].candidates[0].box
        assert (box.x, box.w) == (0.0, 1.0)
        assert box.h < 1.0        # 全面ではない。次のコマを置く余地を残す


class TestBlankPageOpening:
    """空白ページの描き出し。"""

    def test_空白ページなら必ず1つは出る(self):
        assert hit([], "blank_page_opening") is not None

    def test_コマがあれば出さない(self):
        assert hit(TOP_BAND, "blank_page_opening") is None

    def test_幅ちがいの案を並べる(self):
        # **空白ページには手本が何も無い。** どれが良いかを決める材料がそもそも無い
        m = hit([], "blank_page_opening")
        assert [p.label for p in m.plans] == [
            "幅 1/3", "幅 1/2", "幅 2/3", "幅 いっぱい",
            "上と右を断ち切り（幅 1/2）", "4コマ×2列",
        ]

    def test_断ち切りの案は左辺と下辺が他の案と揃う(self):
        # **外へ出るのは上と右だけ。** 左辺や下辺が動くと、他の案と見比べられない
        m = hit([], "blank_page_opening")
        plain = next(p for p in m.plans if p.label == "幅 1/2").candidates[0].box
        bleed = next(p for p in m.plans if "断ち切り" in p.label).candidates[0].box
        assert bleed.x == pytest.approx(plain.x)
        assert bleed.bottom == pytest.approx(plain.bottom)
        assert bleed.y < 0          # 上はページの外
        assert bleed.right > 1.0    # 右もページの外

    def test_4コマ2列の雛形も案に入る(self):
        m = hit([], "blank_page_opening")
        grid = next(p for p in m.plans if p.label == "4コマ×2列")
        assert len(grid.candidates) == 8

    def test_雛形の読み順は右列を上から下(self):
        # **4コマのページはそう読む。** 番号もその順で振る
        m = hit([], "blank_page_opening")
        grid = next(p for p in m.plans if p.label == "4コマ×2列")
        boxes = [c.box for c in grid.candidates]
        assert all(boxes[i].x == boxes[0].x for i in range(4))       # 前半は同じ列
        assert all(boxes[i].x == boxes[4].x for i in range(4, 8))    # 後半も同じ列
        assert boxes[0].x > boxes[4].x                               # 右の列が先
        assert [b.y for b in boxes[:4]] == sorted(b.y for b in boxes[:4])

    def test_雛形は枠いっぱいに敷く(self):
        frame = NBox(0.07, 0.05, 0.86, 0.90)
        m = hit([], "blank_page_opening", PageContext(frame=frame, gutter_x=0.03, gutter_y=0.02))
        boxes = [c.box for p in m.plans if p.label == "4コマ×2列" for c in p.candidates]
        assert min(b.x for b in boxes) == pytest.approx(frame.x)
        assert max(b.right for b in boxes) == pytest.approx(frame.right)
        assert min(b.y for b in boxes) == pytest.approx(frame.y)
        assert max(b.bottom for b in boxes) == pytest.approx(frame.bottom)

    def test_どの案も右上に付く(self):
        frame = NBox(0.07, 0.05, 0.86, 0.90)
        m = hit([], "blank_page_opening", PageContext(frame=frame))
        for plan in m.plans:
            if plan.label == "4コマ×2列":
                continue        # 雛形は右上の1コマではなく、枠いっぱいに敷く
            box = plan.candidates[0].box
            if "断ち切り" in plan.label:
                assert box.y < 0            # 上と右はページの外まで伸ばす
                assert box.right > 1.0
                continue
            assert box.y == pytest.approx(frame.y)         # 枠の上辺
            assert box.right == pytest.approx(frame.right)  # 枠の右辺
            assert box.x >= frame.x - 1e-9                  # 枠から左へはみ出さない


class TestProposalOrder:
    def test_埋まったページには提案を出さない(self):
        assert offered(FULL_PAGE) == []

    def test_特殊な定石が先に来る(self):
        matched = [m for m in match_all(RIGHT_COLUMN) if m.matched]
        assert matched[0].joseki == "vertical_strip_repeat"
        assert all(
            matched[i].priority >= matched[i + 1].priority
            for i in range(len(matched) - 1)
        )

    def test_余裕度は順に関わらない(self):
        # 余裕度が最も高いものが先頭とは限らない（順を決めるのは優先度）
        matched = [m for m in match_all(RIGHT_COLUMN) if m.matched]
        assert max(m.score for m in matched) > matched[0].score

    def test_提案は案の単位で数える(self):
        matches = match_all(TOP_BAND)
        assert len(proposals(matches)) == sum(
            len(m.plans) for m in matches if m.matched
        )
