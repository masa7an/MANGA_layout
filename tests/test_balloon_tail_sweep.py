"""しっぽの総当たり。

[test_balloon_shape.py](test_balloon_shape.py) が**1つ1つの決まりごと**を
代表的な1例で確かめるのに対し、ここは**組み合わせを掛け算で回して、
どの組でも崩れない条件だけ**を見る。

種類としっぽの形の組み合わせに制限は無い（→ `model.BALLOON_STYLES` の説明）。
制限しない代わりに、**選ばれうる組み合わせ全部で形が壊れないこと**を
ここで押さえる。

回している軸は6つ。

| 軸 | 数 |
|---|---|
| 吹き出しの種類（`BALLOON_STYLES`） | 6 |
| しっぽの形（`TAIL_SHAPES`） | 2 |
| 吹き出しの大きさ・縦横比 | 6 |
| しっぽの先端（8方向 × 4距離 ＋ 中心） | 33 |
| 付け根の高さ（`root_y`） | 6 |
| 付け根の幅（`Tail.width`） | 3 |

**1件ずつテストにすると2万件を超える**ので、種類ごとに1件のテストにまとめ、
中で回している。**落ちた組み合わせは全部数えて、頭のいくつかを名前付きで出す**
——1件目で止めると、1か所の不具合なのか全面的に崩れているのかが分からない。

**「見た目が正しいか」はここでは見ない。** 数で判定できるものだけを置く。
"""

from __future__ import annotations

import functools
import itertools
import math
from typing import NamedTuple

import pytest

from manga_layout import Rect
from manga_layout.layout import (
    TAIL_BUBBLE_COUNT,
    BalloonSettings,
    tail_base_angle,
    tail_body_contains,
    tail_bubbles,
    tail_root_point,
    tail_triangle,
)
from manga_layout.model import (
    BALLOON_STYLES,
    TAIL_SHAPE_BUBBLES,
    TAIL_SHAPE_TRIANGLE,
    BalloonObject,
    Tail,
)

SETTINGS = BalloonSettings()

# 三角のしっぽ（付け根の2点と先端）。`layout.tail_triangle` の戻り値
Triangle = tuple[tuple[float, float], tuple[float, float], tuple[float, float]]

# 大きさの軸。**縦長 333×496 は 2026-08-05 の不具合の実測値**（上下だけ
# しっぽが痩せた）。細長い2つは、楕円の潰れを打ち消す計算がいちばん効く形
RECTS: dict[str, Rect] = {
    "普通": Rect(20.0, 30.0, 40.0, 26.0),
    "極小": Rect(0.0, 0.0, 4.0, 4.0),
    "大": Rect(-500.0, -200.0, 1000.0, 700.0),
    "縦長": Rect(0.0, 0.0, 333.0, 496.0),
    "横に細い": Rect(0.0, 0.0, 300.0, 8.0),
    "縦に細い": Rect(0.0, 0.0, 8.0, 300.0),
}

# 先端の向き。**真上・真下（dx が 0）を必ず入れる。** 付け根を先端の
# ある側に置く判定が `tip_x - cx >= 0` なので、ちょうど 0 が境目になる
TIP_ANGLES = tuple(range(0, 360, 45))

# 先端までの距離。楕円の半径に対する倍率で持つ。
# **1.0 未満（吹き出しの内側）と、ちょうど輪郭の上も入れる**——
# 先端は掴んで動かせるので、利用者はそこへ持っていける
TIP_SCALES = (0.6, 1.0, 1.5, 20.0)

# 付け根の高さ。None は自動（先端の向きに合わせる）。
# **範囲外（±5）はここに入れない**——保存できない値なので、
# `TestExtreme.test_範囲外の付け根でも壊れない` で別に見る
ROOT_YS: tuple[float | None, ...] = (None, -1.0, -0.5, 0.0, 0.5, 1.0)

# 付け根の幅。既定は 35.0。**極端に太い 400 を入れる**のは、小さな
# 吹き出しで付け根が一周しないよう頭を押さえている所を通すため
WIDTHS = (4.0, 35.0, 400.0)

# 落ちたときに名前で出す件数
REPORT_LIMIT = 8


class Case(NamedTuple):
    """1つの組み合わせ。失敗したときに名前で言えるようにしてある。"""

    style: str
    shape: str
    size: str
    tip_angle: int
    tip_scale: float
    root_y: float | None
    width: float

    @property
    def rect(self) -> Rect:
        return RECTS[self.size]

    @property
    def tip(self) -> tuple[float, float]:
        rect = self.rect
        cx, cy = rect.center
        if self.tip_scale == 0.0:
            return (cx, cy)
        radians = math.radians(self.tip_angle)
        return (
            cx + math.cos(radians) * rect.w / 2.0 * self.tip_scale,
            cy + math.sin(radians) * rect.h / 2.0 * self.tip_scale,
        )

    def balloon(self) -> BalloonObject:
        return BalloonObject(
            id="bal_0001",
            style=self.style,
            rect=self.rect,
            tail=Tail(
                enabled=True,
                tip=self.tip,
                width=self.width,
                root_y=self.root_y,
                shape=self.shape,
            ),
        )

    def __str__(self) -> str:
        return (
            f"{self.style}/{self.shape}/{self.size}/"
            f"{self.tip_angle}度x{self.tip_scale}/root_y={self.root_y}/幅{self.width}"
        )


class Result(NamedTuple):
    """組み合わせと、そこから出た形。**1回だけ計算して使い回す。**"""

    case: Case
    triangle: Triangle | None
    bubbles: tuple[tuple[float, float, float], ...]
    root: tuple[float, float] | None
    angle: float | None


def all_cases(style: str) -> list[Case]:
    """その種類の組み合わせを全部。先端が中心に重なる分を最後に足す。"""
    cases = [
        Case(style, shape, size, angle, scale, root_y, width)
        for shape, size, angle, scale, root_y, width in itertools.product(
            (TAIL_SHAPE_TRIANGLE, TAIL_SHAPE_BUBBLES),
            RECTS,
            TIP_ANGLES,
            TIP_SCALES,
            ROOT_YS,
            WIDTHS,
        )
    ]
    cases += [
        Case(style, shape, size, 0, 0.0, root_y, 35.0)
        for shape, size, root_y in itertools.product(
            (TAIL_SHAPE_TRIANGLE, TAIL_SHAPE_BUBBLES), RECTS, ROOT_YS
        )
    ]
    return cases


@functools.cache
def results(style: str) -> tuple[Result, ...]:
    """その種類の全組み合わせと、計算した形。

    **同じ種類を何度も引く**（不変条件ごとに1件のテストがある）ので、
    ここで1回だけ計算して覚えておく。
    """
    found = []
    for case in all_cases(style):
        balloon = case.balloon()
        found.append(
            Result(
                case=case,
                triangle=tail_triangle(balloon, SETTINGS),
                bubbles=tail_bubbles(balloon, SETTINGS),
                root=tail_root_point(balloon, SETTINGS),
                angle=tail_base_angle(balloon),
            )
        )
    return tuple(found)


def triangles(style: str) -> list[Result]:
    """三角のしっぽが実際に出た組み合わせだけ。"""
    return [r for r in results(style) if r.triangle is not None]


def chains(style: str) -> list[Result]:
    """飛びしっぽの円が実際に出た組み合わせだけ。"""
    return [r for r in results(style) if r.bubbles]


def report(broken: list[str]) -> None:
    """崩れた組み合わせを数えて出す。**件数を先に言う。**

    1件目で止めると、1か所だけの話なのか全面的に崩れているのかが
    分からない。全部数えてから、頭のいくつかを名前付きで見せる。
    """
    if not broken:
        return
    head = "\n".join(broken[:REPORT_LIMIT])
    rest = len(broken) - REPORT_LIMIT
    more = "" if rest <= 0 else f"\n（ほか {rest} 件）"
    pytest.fail(f"崩れた組み合わせ {len(broken)} 件:\n{head}{more}", pytrace=False)


def finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def normalized(rect: Rect, point: tuple[float, float]) -> tuple[float, float]:
    """楕円の潰れを打ち消した座標。中心が原点、輪郭が半径 1 の円になる。

    向きを比べるときはこの空間で見る。実座標のままだと、細長い吹き出しで
    「同じ40度」が見た目の何十度にもなり、比べる意味が変わってしまう
    （計算の側も、この空間で角度を決めている → `_tail_auto_angle`）。
    """
    cx, cy = rect.center
    return ((point[0] - cx) / (rect.w / 2.0), (point[1] - cy) / (rect.h / 2.0))


def outline_ratio(rect: Rect, point: tuple[float, float]) -> float:
    """楕円の輪郭を 1.0 としたときの、中心からの遠さ。"""
    nx, ny = normalized(rect, point)
    return math.hypot(nx, ny)


def root_is_opposite(case: Case) -> bool:
    """指定した付け根が、先端の**ちょうど真反対**を向いているか。

    このときだけ「どちら回りで 40 度の上限まで寄せるか」が決まらない。
    計算は差を -π〜+π に畳んでから抑える（→ `tail_base_angle`）ので、
    ちょうど ±π の組はどちらも -π 側へ倒れる。**倒れる先が同じなので、
    映した入力でも同じ向きへ回る**——対称にはならない。

    **これは形の不具合ではなく、引き分けの決め方。** 真反対を指定された
    ときは、どちらへ回しても等しく正しい。対称の確認からは外す。
    """
    if case.root_y is None:
        return False
    rect = case.rect
    if rect.w <= 0.0 or rect.h <= 0.0:
        return False
    nx, ny = normalized(rect, case.tip)
    if abs(nx) < 1e-12 and abs(ny) < 1e-12:
        return False
    auto = math.atan2(ny, nx)
    wanted = math.asin(min(max(case.root_y, -1.0), 1.0))
    if case.tip[0] - rect.center[0] < 0.0:
        wanted = math.pi - wanted
    gap = (wanted - auto + math.pi) % (2.0 * math.pi) - math.pi
    return abs(abs(gap) - math.pi) < 1e-9


@pytest.mark.parametrize("style", BALLOON_STYLES)
class TestTriangle:
    """三角のしっぽ。**どの組み合わせでも崩れない条件だけ**を見る。"""

    def test_座標が有限(self, style):
        broken = [
            str(r.case)
            for r in triangles(style)
            if not finite(*(value for point in r.triangle for value in point))
        ]
        report(broken)

    def test_先端がそのまま頂点になる(self, style):
        """先端は指す相手の位置。**計算で動かしてはならない。**"""
        broken = [
            f"{r.case}: 頂点 {r.triangle[1]} ≠ 先端 {r.case.tip}"
            for r in triangles(style)
            if r.triangle[1] != r.case.tip
        ]
        report(broken)

    def test_付け根は輪郭より内側(self, style):
        """外に出ると、輪郭が凹む所で本体と離れ、継ぎ目に隙間が空く。"""
        broken = []
        for r in triangles(style):
            for point in (r.triangle[0], r.triangle[2]):
                ratio = outline_ratio(r.case.rect, point)
                if ratio > 1.0 + 1e-9:
                    broken.append(f"{r.case}: 付け根が輪郭の {ratio:.3f} 倍の所")
        report(broken)

    def test_付け根がひと回りしない(self, style):
        """広がりすぎると、しっぽが本体を飲み込む（上限は120度）。"""
        broken = []
        for r in triangles(style):
            left = normalized(r.case.rect, r.triangle[0])
            right = normalized(r.case.rect, r.triangle[2])
            a1 = math.atan2(left[1], left[0])
            a2 = math.atan2(right[1], right[0])
            spread = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
            if spread > 2 * math.pi / 3 + 1e-6:
                broken.append(f"{r.case}: 付け根の広がり {math.degrees(spread):.1f} 度")
        report(broken)

    def test_付け根に幅がある(self, style):
        """2点が重なると三角形が線になり、しっぽが消える。"""
        broken = []
        for r in triangles(style):
            (x1, y1), _, (x2, y2) = r.triangle
            if math.hypot(x2 - x1, y2 - y1) <= 0.0:
                broken.append(f"{r.case}: 付け根の2点が同じ場所")
        report(broken)

    def test_付け根が先端側を向く(self, style):
        """反対側から生えると、しっぽが吹き出しを横切る。

        **潰れを打ち消した空間で見る**（→ `normalized`）。付け根は先端の
        向きから最大40度しか離れないので、この空間では必ず同じ側にある。
        """
        broken = []
        for r in triangles(style):
            rect = r.case.rect
            mid = (
                (r.triangle[0][0] + r.triangle[2][0]) / 2.0,
                (r.triangle[0][1] + r.triangle[2][1]) / 2.0,
            )
            mx, my = normalized(rect, mid)
            tx, ty = normalized(rect, r.case.tip)
            if mx * tx + my * ty <= 0.0:
                broken.append(f"{r.case}: 付け根が先端の反対側")
        report(broken)

    def test_掴み所と三角形の付け根が揃う(self, style):
        """`tail_root_point`（掴み所）と三角形の付け根が別々に動くと、
        **見えている付け根とつまめる場所がずれる。**"""
        broken = []
        for r in triangles(style):
            if r.root is None:
                broken.append(f"{r.case}: 三角形はあるのに掴み所が無い")
                continue
            rect = r.case.rect
            left = normalized(rect, r.triangle[0])
            right = normalized(rect, r.triangle[2])
            mid = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
            root = normalized(rect, r.root)
            # 付け根の2点は輪郭に沿って開くので、中点は弦のぶん内側に来る。
            # 位置ではなく向きが揃っていることを見る
            if mid[0] * root[0] + mid[1] * root[1] <= 0.0:
                broken.append(f"{r.case}: 掴み所と三角形の付け根が別の向き")
        report(broken)

    def test_しっぽの内側と判定が食い違わない(self, style):
        """見えている三角形の重心を押して掴めないなら、絵と判定がずれている。"""
        broken = []
        for r in triangles(style):
            (x1, y1), (x2, y2), (x3, y3) = r.triangle
            area = abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) / 2.0
            if area < 1e-6:
                continue  # 潰れた三角形。重心は内側を持たない
            center = ((x1 + x2 + x3) / 3.0, (y1 + y2 + y3) / 3.0)
            if not tail_body_contains(r.case.balloon(), *center, SETTINGS):
                broken.append(f"{r.case}: 重心 {center} で掴めない")
        report(broken)


@pytest.mark.parametrize("style", BALLOON_STYLES)
class TestBubbles:
    """丸い飛びしっぽ（心の声・独り言 → 要件定義 10.1）。"""

    def test_数は常に同じ(self, style):
        """**先端を引いている最中に円が生えない。**"""
        broken = [
            f"{r.case}: {len(r.bubbles)} 個"
            for r in results(style)
            if r.case.shape == TAIL_SHAPE_BUBBLES
            and len(r.bubbles) not in (0, TAIL_BUBBLE_COUNT)
        ]
        report(broken)

    def test_座標と半径が有限(self, style):
        broken = [
            str(r.case)
            for r in chains(style)
            if not finite(*(value for circle in r.bubbles for value in circle))
        ]
        report(broken)

    def test_先端へ向かって小さくなる(self, style):
        broken = []
        for r in chains(style):
            radii = [radius for _, _, radius in r.bubbles]
            if radii != sorted(radii, reverse=True) or radii[-1] <= 0.0:
                broken.append(f"{r.case}: 半径 {radii}")
        report(broken)

    def test_円どうしが離れている(self, style):
        """くっつくと鎖ではなく1つの塊に見える。"""
        broken = []
        for r in chains(style):
            for (x1, y1, r1), (x2, y2, r2) in zip(
                r.bubbles, r.bubbles[1:], strict=False
            ):
                if math.hypot(x2 - x1, y2 - y1) <= r1 + r2:
                    broken.append(f"{r.case}: 隣の円と重なっている")
                    break
        report(broken)

    def test_円の中は掴める(self, style):
        """先端の丸だけしか掴めないと「見えているのに反応しない」になる。"""
        broken = []
        for r in chains(style):
            balloon = r.case.balloon()
            for cx, cy, _ in r.bubbles:
                if not tail_body_contains(balloon, cx, cy, SETTINGS):
                    broken.append(f"{r.case}: 円の中心 ({cx:.1f}, {cy:.1f}) で掴めない")
                    break
        report(broken)

    def test_鎖は先端を越えない(self, style):
        """越えると、指したい相手より先に円が飛び出す。"""
        broken = []
        for r in chains(style):
            tip = r.case.tip
            first = r.bubbles[0]
            last = r.bubbles[-1]
            start_to_tip = math.hypot(tip[0] - first[0], tip[1] - first[1])
            start_to_last = math.hypot(last[0] - first[0], last[1] - first[1])
            if start_to_last > start_to_tip + 1e-6:
                over = start_to_last - start_to_tip
                broken.append(f"{r.case}: 鎖が先端を {over:.2f} 越えた")
        report(broken)


@pytest.mark.parametrize("style", BALLOON_STYLES)
class TestSymmetry:
    """左右・上下を映した入力では、出てくる形も映したものになる。

    **映しても変わらないこと自体が仕様ではない。** 向きによって太さが
    変わった 2026-08-05 の不具合は、まさにこの対称が崩れた形で出た
    （→ `test_balloon_shape.py` の `test_縦長では上下も左右と同じ太さになる`）。
    """

    def mirrored(self, case: Case, axis: str) -> BalloonObject:
        """先端を映した吹き出し。上下では付け根の高さも一緒に映す。"""
        balloon = case.balloon()
        cx, cy = case.rect.center
        tx, ty = case.tip
        if axis == "左右":
            balloon.tail.tip = (2.0 * cx - tx, ty)
        else:
            balloon.tail.tip = (tx, 2.0 * cy - ty)
            if case.root_y is not None:
                balloon.tail.root_y = -case.root_y
        return balloon

    @pytest.mark.parametrize("axis", ["左右", "上下"])
    def test_映した先端では形も映る(self, style, axis):
        broken = []
        for r in triangles(style):
            case = r.case
            # 軸の上に乗っている先端は、映しても同じ位置。左右どちら側かの
            # 判定（`tip_x - cx >= 0`）の境目に当たるので、ここでは見ない。
            #
            # **度で判定する。** `cos(radians(90))` は 0 ではなく 6.1e-17
            # なので、三角関数で「軸の上か」を見ると素通りする——素通りした
            # 先では、映した先端が境目の反対側へ落ちて別の答えになる
            if axis == "左右" and case.tip_angle in (90, 270):
                continue
            if axis == "上下" and case.tip_angle in (0, 180):
                continue
            if root_is_opposite(case):
                continue
            got = tail_triangle(self.mirrored(case, axis), SETTINGS)
            if got is None:
                broken.append(f"{case}: 映すとしっぽが消えた")
                continue
            cx, cy = case.rect.center
            # **付け根の2点は入れ替わる。** 三角形は輪郭に沿って
            # 「角度 − 半角」「先端」「角度 + 半角」の順で作られるので、
            # 映すと角度の向きが反転し、左右の付け根が逆順で出てくる
            left, tip, right = r.triangle
            for before, after in zip((right, tip, left), got, strict=True):
                if axis == "左右":
                    want = (2.0 * cx - before[0], before[1])
                else:
                    want = (before[0], 2.0 * cy - before[1])
                if math.hypot(after[0] - want[0], after[1] - want[1]) > 1e-6:
                    broken.append(f"{case}: {before} を映すと {want} のはずが {after}")
                    break
        report(broken)


@pytest.mark.parametrize("style", BALLOON_STYLES)
class TestRoundTrip:
    def test_保存して読み直しても同じ形(self, style):
        """保存のたびに形が変わると、開き直すたびにしっぽが動く。

        `root_y` は範囲外を保存できない（→ `validation.opt_ratio`）ので、
        ここで回している -1.0〜1.0 と自動だけが対象。
        """
        broken = []
        for r in results(style):
            again = BalloonObject.from_dict(r.case.balloon().to_dict(), "balloon")
            if tail_triangle(again, SETTINGS) != r.triangle:
                broken.append(f"{r.case}: 三角が変わった")
            elif tail_bubbles(again, SETTINGS) != r.bubbles:
                broken.append(f"{r.case}: 飛びしっぽが変わった")
            elif tail_root_point(again, SETTINGS) != r.root:
                broken.append(f"{r.case}: 掴み所が変わった")
        report(broken)

    def test_同じ入力なら何度でも同じ形(self, style):
        """呼ぶたびに変わると、画面・サムネイル・書き出しで形が食い違う。"""
        broken = []
        for r in results(style):
            balloon = r.case.balloon()
            if (
                tail_triangle(balloon, SETTINGS) != r.triangle
                or tail_bubbles(balloon, SETTINGS) != r.bubbles
                or tail_root_point(balloon, SETTINGS) != r.root
            ):
                broken.append(str(r.case))
        report(broken)


class TestExtreme:
    """総当たりの外に置いた、極端な入力。**落ちないことだけ**を見る。"""

    @pytest.mark.parametrize("style", BALLOON_STYLES)
    @pytest.mark.parametrize("shape", [TAIL_SHAPE_TRIANGLE, TAIL_SHAPE_BUBBLES])
    @pytest.mark.parametrize(
        "rect",
        [
            Rect(0.0, 0.0, 0.0, 0.0),
            Rect(0.0, 0.0, 40.0, 0.0),
            Rect(0.0, 0.0, 0.0, 26.0),
        ],
        ids=["点", "高さ0", "幅0"],
    )
    def test_潰れた吹き出しでも落ちない(self, style, shape, rect):
        balloon = BalloonObject(
            id="bal_0001",
            style=style,
            rect=rect,
            tail=Tail(tip=(100.0, 100.0), shape=shape),
        )
        assert tail_triangle(balloon, SETTINGS) is None
        assert tail_bubbles(balloon, SETTINGS) == ()
        assert tail_root_point(balloon, SETTINGS) is None
        assert tail_body_contains(balloon, 100.0, 100.0, SETTINGS) is False

    @pytest.mark.parametrize("style", BALLOON_STYLES)
    @pytest.mark.parametrize("root_y", [-5.0, 5.0, 1e9, -1e9])
    def test_範囲外の付け根でも壊れない(self, style, root_y):
        """保存はできない値だが、操作の途中では通りうる。

        上限は `tail_base_angle` の1か所でかけている（→ 要件定義 10.1）ので、
        範囲外でも先端の側に留まるはず。
        """
        for size, rect in RECTS.items():
            balloon = BalloonObject(
                id="bal_0001",
                style=style,
                rect=rect,
                tail=Tail(tip=(rect.center[0], rect.bottom + 50.0), root_y=root_y),
            )
            root = tail_root_point(balloon, SETTINGS)
            assert root is not None, size
            assert finite(*root), size
            # 先端は真下。付け根も下半分に留まる
            assert root[1] >= rect.center[1] - 1e-9, size

    @pytest.mark.parametrize("style", BALLOON_STYLES)
    @pytest.mark.parametrize("shape", [TAIL_SHAPE_TRIANGLE, TAIL_SHAPE_BUBBLES])
    def test_しっぽを消すと形は出ないが掴み所だけは残る(self, style, shape):
        """**今の挙動をそのまま書き留めたもの。**

        三角と飛びしっぽは `enabled` を見て何も返さないが、
        `tail_root_point` は見ない——「しっぽがあるならここ」を答える
        だけの関数で、消えているかどうかは**呼ぶ側が見ている**
        （`PageScene._draw_tail_handles` と `PageView._tail_root_at` の
        2か所とも、呼ぶ前に `tail.enabled` を確かめている）。

        **揃っていないので、3つめの呼び出し側が確認を忘れると、
        消したはずのしっぽの掴み所が出る。** 今の2か所は確かめているので
        実害は無く、ここでは**揃っていないことが黙って変わらないよう**
        書き留めておく。
        """
        for size, rect in RECTS.items():
            balloon = BalloonObject(
                id="bal_0001",
                style=style,
                rect=rect,
                tail=Tail(enabled=False, tip=(0.0, 0.0), shape=shape),
            )
            assert tail_triangle(balloon, SETTINGS) is None, size
            assert tail_bubbles(balloon, SETTINGS) == (), size
            assert tail_body_contains(balloon, *rect.center, SETTINGS) is False, size
            assert tail_root_point(balloon, SETTINGS) is not None, size
