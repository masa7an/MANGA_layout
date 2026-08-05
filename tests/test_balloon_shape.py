"""吹き出しの形の検証。

見た目そのものは目で見ないと分からないが、**形が壊れる条件**は数で押さえられる。
ギザギザの山と谷が対になっているか、しっぽの付け根が本体の内側に入っているか、
極端な大きさで潰れないか。ここが崩れると、継ぎ目に隙間が空いたり
輪郭が自己交差したりする。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Rect, new_project
from manga_layout.layout import (
    CLOUD_MIN_SEGMENTS_PER_LOBE,
    TAIL_BUBBLE_COUNT,
    TAIL_BUBBLE_MAX_RATIO,
    TAIL_LENGTH_MIN_PX,
    TAIL_LENGTH_RATIO,
    TAIL_ROOT_MAX_GAP,
    WAVY_MIN_SEGMENTS_PER_WAVE,
    BalloonSettings,
    attach_target,
    balloon_at,
    balloon_contains,
    balloon_outline,
    cloud_points,
    default_balloon_rect,
    default_tail_tip,
    ellipse_points,
    jagged_points,
    root_y_at,
    tail_base_angle,
    tail_body_contains,
    tail_bubbles,
    tail_root_point,
    tail_triangle,
    wavy_points,
)
from manga_layout.model import TAIL_SHAPE_BUBBLES

SETTINGS = BalloonSettings()
RECT = Rect(20.0, 30.0, 40.0, 26.0)


def distance_ratio(rect: Rect, point: tuple[float, float]) -> float:
    """楕円の半径に対する、中心からその点までの割合。1.0 なら輪郭の上。"""
    cx, cy = rect.center
    nx = (point[0] - cx) / (rect.w / 2.0)
    ny = (point[1] - cy) / (rect.h / 2.0)
    return math.hypot(nx, ny)


@pytest.fixture
def balloon():
    project = new_project()
    return project.add_balloon(project.pages[0], RECT)


class TestEllipse:
    def test_すべて輪郭の上に乗る(self):
        for point in ellipse_points(RECT, SETTINGS):
            assert distance_ratio(RECT, point) == pytest.approx(1.0)

    def test_外接矩形に収まる(self):
        for x, y in ellipse_points(RECT, SETTINGS):
            assert RECT.x - 1e-9 <= x <= RECT.right + 1e-9
            assert RECT.y - 1e-9 <= y <= RECT.bottom + 1e-9

    def test_点が少なすぎても形になる(self):
        # 設定を極端に小さくされても三角形未満にはしない
        points = ellipse_points(RECT, BalloonSettings(ellipse_segments=1))
        assert len(points) >= 8

    def test_同じ点が重複しない(self):
        points = ellipse_points(RECT, SETTINGS)
        assert len(set(points)) == len(points)


class TestJagged:
    def test_山と谷が交互に並ぶ(self):
        points = jagged_points(RECT, SETTINGS)
        ratios = [distance_ratio(RECT, p) for p in points]
        for i, ratio in enumerate(ratios):
            want = 1.0 if i % 2 == 0 else 1.0 - SETTINGS.jagged_depth
            assert ratio == pytest.approx(want)

    def test_頂点数は必ず偶数(self):
        """奇数だと一周したときに山が隣り合い、そこだけ形が崩れる。"""
        for spikes in (3, 7, 14, 21):
            points = jagged_points(RECT, BalloonSettings(jagged_spikes=spikes))
            assert len(points) == spikes * 2
            assert len(points) % 2 == 0

    def test_谷が深すぎても中心を越えない(self):
        points = jagged_points(RECT, BalloonSettings(jagged_depth=5.0))
        assert min(distance_ratio(RECT, p) for p in points) > 0.0

    def test_山の数が少なすぎても三角形以上(self):
        points = jagged_points(RECT, BalloonSettings(jagged_spikes=1))
        assert len(points) >= 6


class TestWavy:
    def test_山と谷の間に収まる(self):
        """半径は 1.0（山）から 1 - depth（谷）の間だけを動く。"""
        for point in wavy_points(RECT, SETTINGS):
            ratio = distance_ratio(RECT, point)
            assert 1.0 - SETTINGS.wavy_depth - 1e-9 <= ratio <= 1.0 + 1e-9

    def test_頂点数は波の数の整数倍(self):
        """半端だと最後の波だけ途中で切れ、始点との継ぎ目に角が出る。"""
        for waves in (2, 5, 16, 23):
            points = wavy_points(RECT, BalloonSettings(wavy_waves=waves))
            assert len(points) % waves == 0

    def test_始点は山で一周して戻る(self):
        """位相が閉じていないと、始点と終点の間だけ形が違う。"""
        points = wavy_points(RECT, SETTINGS)
        assert distance_ratio(RECT, points[0]) == pytest.approx(1.0)
        # 1波ぶん進んだ点も山。ここがずれていれば位相が閉じていない
        per_wave = len(points) // SETTINGS.wavy_waves
        assert distance_ratio(RECT, points[per_wave]) == pytest.approx(1.0)

    def test_ギザギザと違って隣同士が跳ねない(self):
        """なめらかさがこの形の意味そのもの。角が立つとギザギザになる。

        ギザギザは隣り合う頂点で 1.0 と 1 - depth を往復するが、
        波形は谷の深さより小さい幅でしか動かない。
        """
        ratios = [distance_ratio(RECT, p) for p in wavy_points(RECT, SETTINGS)]
        # 1つ回転させただけなので長さは必ず揃う。strict=True で明示する
        steps = [
            abs(b - a) for a, b in zip(ratios, ratios[1:] + ratios[:1], strict=True)
        ]
        assert max(steps) < SETTINGS.wavy_depth

    def test_1波あたりの本数に下限がある(self):
        """波が多いほど1波は細かくなるが、角が立つ手前で止める。"""
        points = wavy_points(RECT, BalloonSettings(wavy_waves=60))
        assert len(points) == 60 * WAVY_MIN_SEGMENTS_PER_WAVE

    def test_谷が深すぎても中心を越えない(self):
        points = wavy_points(RECT, BalloonSettings(wavy_depth=5.0))
        assert min(distance_ratio(RECT, p) for p in points) > 0.0

    def test_波が少なすぎても形になる(self):
        points = wavy_points(RECT, BalloonSettings(wavy_waves=0))
        assert len(points) >= 8

    def test_外接矩形に収まる(self):
        for x, y in wavy_points(RECT, SETTINGS):
            assert RECT.x - 1e-9 <= x <= RECT.right + 1e-9
            assert RECT.y - 1e-9 <= y <= RECT.bottom + 1e-9

    def test_同じ点が重複しない(self):
        points = wavy_points(RECT, SETTINGS)
        assert len(set(points)) == len(points)


class TestOutline:
    def test_種類で切り替わる(self, balloon):
        balloon.style = "ellipse"
        assert len(balloon_outline(balloon, SETTINGS)) == SETTINGS.ellipse_segments
        balloon.style = "jagged"
        assert len(balloon_outline(balloon, SETTINGS)) == SETTINGS.jagged_spikes * 2
        balloon.style = "wavy"
        assert balloon_outline(balloon, SETTINGS) == wavy_points(RECT, SETTINGS)


class TestTail:
    def test_先端がそのまま頂点になる(self, balloon):
        balloon.tail.tip = (30.0, 90.0)
        base1, tip, base2 = tail_triangle(balloon, SETTINGS)
        assert tip == (30.0, 90.0)

    def test_付け根は本体の内側にある(self, balloon):
        """外側だと、ギザギザの谷で本体と離れて継ぎ目に隙間が空く。"""
        balloon.style = "jagged"
        balloon.tail.tip = (30.0, 90.0)
        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        limit = 1.0 - SETTINGS.jagged_depth
        assert distance_ratio(RECT, base1) <= limit + 1e-9
        assert distance_ratio(RECT, base2) <= limit + 1e-9

    def test_波形でも付け根は谷より内側にある(self, balloon):
        balloon.style = "wavy"
        balloon.tail.tip = (30.0, 90.0)
        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        limit = 1.0 - SETTINGS.wavy_depth
        assert distance_ratio(RECT, base1) <= limit + 1e-9
        assert distance_ratio(RECT, base2) <= limit + 1e-9

    def test_楕円でも付け根は輪郭より内側(self, balloon):
        balloon.tail.tip = (30.0, 90.0)
        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        assert distance_ratio(RECT, base1) < 1.0
        assert distance_ratio(RECT, base2) < 1.0

    @pytest.mark.parametrize(
        "tip",
        [(30.0, 90.0), (30.0, -20.0), (100.0, 43.0), (-30.0, 43.0), (90.0, 95.0)],
        ids=["下", "上", "右", "左", "斜め"],
    )
    def test_どの向きでも付け根が先端側を向く(self, balloon, tip):
        balloon.tail.tip = tip
        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        cx, cy = RECT.center
        mid = ((base1[0] + base2[0]) / 2.0, (base1[1] + base2[1]) / 2.0)

        # 中心→付け根中点 と 中心→先端 が同じ側を向いていること
        dot = (mid[0] - cx) * (tip[0] - cx) + (mid[1] - cy) * (tip[1] - cy)
        assert dot > 0.0

    def test_付け根の幅が反映される(self, balloon):
        balloon.tail.tip = (30.0, 90.0)
        # 既定（BalloonSettings.tail_width）ではなく、この吹き出し（40×26）に
        # 対して収まる幅を明示する。既定より狭い値を「太い側」に使うと、
        # 比べる向きが逆になって意味が消える
        balloon.tail.width = 6.0
        narrow = tail_triangle(balloon, SETTINGS)
        balloon.tail.width = 12.0
        wide = tail_triangle(balloon, SETTINGS)

        def base_length(tri):
            (x1, y1), _, (x2, y2) = tri
            return math.hypot(x2 - x1, y2 - y1)

        assert base_length(wide) > base_length(narrow)

    def test_縦長では上下も左右と同じ太さになる(self, balloon):
        """向きによって幅が変わっていた不具合（2026-08-05）の直し。

        縦長の吹き出しでは、輪郭を1ラジアン進む長さが上下と左右で違うため、
        半角を揃えるだけでは見た目の太さが変わってしまう
        （実測 333×496 で左右 51.8px、上下 23.4px）。
        """
        balloon.rect = Rect(0.0, 0.0, 333.0, 496.0)
        cx, cy = balloon.rect.center

        def width_at(dx: float, dy: float) -> float:
            balloon.tail.tip = (cx + dx, cy + dy)
            (x1, y1), _, (x2, y2) = tail_triangle(balloon, SETTINGS)
            return math.hypot(x2 - x1, y2 - y1)

        left_right = width_at(400.0, 0.0)
        up_down = width_at(0.0, 400.0)
        assert up_down == pytest.approx(left_right, rel=0.01)

    def test_左右の太さは基準のまま変わらない(self, balloon):
        """上下を太くする直しで、左右まで変わっては本末転倒。

        左右（角度0）での太さは、直す前の式（半径をそのまま使う）と
        一致する必要がある。
        """
        balloon.rect = Rect(0.0, 0.0, 333.0, 496.0)
        cx, cy = balloon.rect.center
        balloon.tail.tip = (cx + 400.0, cy)

        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        got = math.hypot(base2[0] - base1[0], base2[1] - base1[1])

        # 直す前の式：半径そのものを atan2 の分母に使う。角度0での2点は
        # x が等しく y だけ ±b*sin(half) に離れるので、弦の長さは 2*b*sin(half)
        ratio = 0.95  # 楕円の _tail_base_ratio
        a = balloon.rect.w / 2.0 * ratio
        b = balloon.rect.h / 2.0 * ratio
        half = math.atan2(balloon.tail.width / 2.0, a)
        want = 2.0 * b * math.sin(half)
        assert got == pytest.approx(want, rel=1e-6)

    def test_小さな吹き出しでも付け根が一周しない(self, balloon):
        """付け根が広がりすぎると、しっぽが本体を飲み込む。"""
        balloon.rect = Rect(0.0, 0.0, 4.0, 4.0)
        balloon.tail.width = 50.0
        balloon.tail.tip = (2.0, 40.0)
        base1, _, base2 = tail_triangle(balloon, SETTINGS)

        cx, cy = balloon.rect.center
        a1 = math.atan2(base1[1] - cy, base1[0] - cx)
        a2 = math.atan2(base2[1] - cy, base2[0] - cx)
        spread = abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi)
        assert spread <= 2 * math.pi / 3 + 1e-6  # 120度まで

    def test_しっぽ無しなら作らない(self, balloon):
        balloon.tail.enabled = False
        assert tail_triangle(balloon, SETTINGS) is None

    def test_先端が中心と重なっていたら作らない(self, balloon):
        balloon.tail.tip = RECT.center
        assert tail_triangle(balloon, SETTINGS) is None

    def test_既定の先端は下に出る(self):
        tip = default_tail_tip(RECT)
        assert tip[0] == pytest.approx(RECT.center[0])
        assert tip[1] > RECT.bottom

    def test_長さは吹き出しの高さに比例する(self):
        """既定を縦長にしたとき、高さ基準のしっぽも一緒に伸びる。"""
        tall = Rect(RECT.x, RECT.y, RECT.w, RECT.h * 2.0)
        伸びた = default_tail_tip(tall)[1] - tall.bottom
        もと = default_tail_tip(RECT)[1] - RECT.bottom

        assert 伸びた == pytest.approx(もと * 2.0)
        assert もと == pytest.approx(RECT.h * TAIL_LENGTH_RATIO)

    def test_小さい吹き出しでも潰れない(self):
        """割合だけだと、小さい吹き出しでしっぽが消える。"""
        tiny = Rect(0.0, 0.0, 4.0, 1.0)
        assert default_tail_tip(tiny)[1] - tiny.bottom == pytest.approx(
            TAIL_LENGTH_MIN_PX
        )


class TestTailRoot:
    """付け根の縦位置（root_y）。上端 -1、中央 0、下端 +1。"""

    def test_指定しなければ先端の向きに合わせる(self, balloon):
        balloon.tail.tip = (200.0, 43.0)  # 真横
        assert balloon.tail.root_y is None
        base1, _, base2 = tail_triangle(balloon, SETTINGS)
        mid_y = (base1[1] + base2[1]) / 2.0
        assert mid_y == pytest.approx(RECT.center[1], abs=0.5)

    @pytest.mark.parametrize("root_y", [-0.5, 0.0, 0.5], ids=["上寄り", "中央", "下寄り"])
    def test_指定した高さに付く(self, balloon, root_y):
        # 先端は真横。ここを起点にすると、上下どちらへも上限まで余裕がある
        balloon.tail.tip = (200.0, RECT.center[1])
        balloon.tail.root_y = root_y
        root = tail_root_point(balloon, SETTINGS)
        # 付け根は輪郭より少し内側に置くので、その分だけ縮む
        ratio = distance_ratio(RECT, root)
        assert ratio <= 1.0
        want = RECT.center[1] + RECT.h / 2.0 * 0.95 * root_y
        assert root[1] == pytest.approx(want, abs=0.01)

    def test_先端から離れすぎた付け根はそこで止まる(self, balloon):
        """離れるほどしっぽが針に痩せる（→ `TAIL_ROOT_MAX_GAP`）。

        止めずに通すと、付け根を動かしたつもりでもしっぽは同じ場所から
        出たままに見える。
        """
        balloon.tail.tip = (200.0, RECT.center[1])  # 真横＝自動は 0 度
        angles = []
        for root_y in (0.5, 0.9, 1.0):
            balloon.tail.root_y = root_y
            angles.append(tail_base_angle(balloon))

        assert angles[0] == pytest.approx(math.asin(0.5))  # 30度。上限の内側
        assert angles[1] == pytest.approx(TAIL_ROOT_MAX_GAP)  # 64度→40度で頭打ち
        assert angles[2] == pytest.approx(TAIL_ROOT_MAX_GAP)

    def test_上限は保存済みの値にも効く(self, balloon):
        """`root_y` が飛んだまま保存された作品も、開いた時点で正しい形になる。

        読み込みで弾けない値。どこを向くべきかは先端との関係で決まるので、
        保存された時点では判定できない。
        """
        balloon.tail.tip = (RECT.center[0], 200.0)  # 真下
        balloon.tail.root_y = -1.0  # 真上＝先端の正反対
        root = tail_root_point(balloon, SETTINGS)
        assert root[1] > RECT.center[1]  # 上へは飛ばず、先端側に留まる

    def test_上端と下端を越えない(self, balloon):
        """要望どおり上端〜下端の範囲。外に飛び出すと形が壊れる。"""
        balloon.tail.tip = (200.0, 200.0)
        for value in (-5.0, -1.0, 0.3, 1.0, 5.0):
            balloon.tail.root_y = value
            root = tail_root_point(balloon, SETTINGS)
            assert RECT.y - 1e-9 <= root[1] <= RECT.bottom + 1e-9

    def test_先端のある側に付く(self, balloon):
        """反対側から生えると、しっぽが吹き出しを横切る。"""
        balloon.tail.root_y = 0.0

        balloon.tail.tip = (200.0, 43.0)  # 右
        assert tail_root_point(balloon, SETTINGS)[0] > RECT.center[0]

        balloon.tail.tip = (-200.0, 43.0)  # 左
        assert tail_root_point(balloon, SETTINGS)[0] < RECT.center[0]

    def test_上端下端では左右が一致する(self, balloon):
        """極では左右の解が同じ点になる。ここで飛ぶと操作中に形が跳ねる。

        先端は真下の近くに置く。真横に近い先端だと上限（→
        `TAIL_ROOT_MAX_GAP`）に掛かってしまい、極まで届かない。
        """
        for tip in ((70.0, 200.0), (10.0, 200.0)):
            balloon.tail.tip = tip
            balloon.tail.root_y = 1.0
            root = tail_root_point(balloon, SETTINGS)
            assert root[0] == pytest.approx(RECT.center[0])

    def test_大きさを変えても同じ割合に残る(self, balloon):
        """mm ではなく割合で持つ理由。"""
        balloon.tail.tip = (200.0, 200.0)
        balloon.tail.root_y = 0.5

        before = tail_root_point(balloon, SETTINGS)
        before_ratio = (before[1] - RECT.center[1]) / (RECT.h / 2.0)

        balloon.rect = Rect(RECT.x, RECT.y, RECT.w * 2, RECT.h * 2)
        after = tail_root_point(balloon, SETTINGS)
        after_ratio = (after[1] - balloon.rect.center[1]) / (balloon.rect.h / 2.0)

        assert after_ratio == pytest.approx(before_ratio)

    def test_付け根を動かしても三角形は先端に届く(self, balloon):
        balloon.tail.tip = (30.0, 120.0)
        for value in (-1.0, -0.5, 0.0, 0.5, 1.0):
            balloon.tail.root_y = value
            _, tip, _ = tail_triangle(balloon, SETTINGS)
            assert tip == (30.0, 120.0)

    def test_しっぽ無しなら付け根も無い(self, balloon):
        balloon.tail.enabled = False
        balloon.tail.root_y = 0.0
        assert tail_triangle(balloon, SETTINGS) is None

    def test_先端が中心に重なると決められない(self, balloon):
        balloon.tail.tip = RECT.center
        balloon.tail.root_y = 0.0
        assert tail_base_angle(balloon) is None
        assert tail_root_point(balloon, SETTINGS) is None

    @pytest.mark.parametrize(
        "y,want",
        [
            (RECT.y, -1.0),
            (RECT.center[1], 0.0),
            (RECT.bottom, 1.0),
            (RECT.y - 100.0, -1.0),
            (RECT.bottom + 100.0, 1.0),
        ],
    )
    def test_高さから割合に直せる(self, y, want):
        assert root_y_at(RECT, y) == pytest.approx(want)

    def test_潰れた吹き出しでも落ちない(self):
        assert root_y_at(Rect(0.0, 0.0, 10.0, 0.0), 5.0) == 0.0


class TestHitTest:
    def test_中心は当たる(self, balloon):
        assert balloon_contains(balloon, *RECT.center)

    def test_四隅は当たらない(self, balloon):
        """外接矩形で判定すると、何もない隅で掴めて下のコマが選べなくなる。"""
        assert not balloon_contains(balloon, RECT.x, RECT.y)
        assert not balloon_contains(balloon, RECT.right, RECT.bottom)

    def test_辺の中点は当たる(self, balloon):
        cx, cy = RECT.center
        assert balloon_contains(balloon, RECT.x + 0.1, cy)
        assert balloon_contains(balloon, cx, RECT.y + 0.1)

    def test_潰れた吹き出しは当たらない(self, balloon):
        balloon.rect = Rect(10.0, 10.0, 0.0, 0.0)
        assert not balloon_contains(balloon, 10.0, 10.0)

    def test_重なっていれば手前を返す(self):
        project = new_project()
        page = project.pages[0]
        lower = project.add_balloon(page, RECT)
        upper = project.add_balloon(page, RECT)
        assert upper.z > lower.z
        assert balloon_at(page, *RECT.center) is upper

    def test_何も無ければ返さない(self):
        project = new_project()
        assert balloon_at(project.pages[0], 5.0, 5.0) is None


class TestCloud:
    """雲_フキダシ（心の声・回想 → 要件定義 6.22）。"""

    def test_山と谷の間に収まる(self):
        for point in cloud_points(RECT, SETTINGS):
            ratio = distance_ratio(RECT, point)
            assert 1.0 - SETTINGS.cloud_depth - 1e-9 <= ratio <= 1.0 + 1e-9

    def test_頂点数は膨らみの数の整数倍(self):
        """半端だとくびれが線分の途中に来て、尖りが丸められる。"""
        for lobes in (3, 6, 9, 23):
            points = cloud_points(RECT, BalloonSettings(cloud_lobes=lobes))
            assert len(points) % lobes == 0

    def test_くびれの数は膨らみの数と一致する(self):
        """**尖りが頂点の上に乗ること。** 乗らないと谷が浅く丸められ、
        膨らみの区切りが消えて「ふわふわを深くしただけ」になる。
        """
        floor = 1.0 - SETTINGS.cloud_depth
        ratios = [distance_ratio(RECT, p) for p in cloud_points(RECT, SETTINGS)]
        assert sum(r == pytest.approx(floor) for r in ratios) == SETTINGS.cloud_lobes

    def test_山は谷より数がずっと多い(self):
        """**山は丸く、谷は尖らせる**（→ 6.22）。丸い頂は点を多く使い、
        尖ったくびれは1点で折れる。余弦波（ふわふわ）ではこの差が出ない。
        """
        ratios = [distance_ratio(RECT, p) for p in cloud_points(RECT, SETTINGS)]
        floor = 1.0 - SETTINGS.cloud_depth
        near_top = sum(r > (1.0 + floor) / 2.0 for r in ratios)
        assert near_top > len(ratios) // 2

    def test_ふわふわより深く動く(self):
        """浅いと丸との差が出ず、ふわふわとも見分けが付かない。"""
        assert SETTINGS.cloud_depth > SETTINGS.wavy_depth

    def test_1つの膨らみあたりの本数に下限がある(self):
        """角が立つと綿に見えない（ふわふわの下限と同じ考え方 → 6.13）。"""
        points = cloud_points(RECT, BalloonSettings(cloud_lobes=40))
        assert len(points) == 40 * CLOUD_MIN_SEGMENTS_PER_LOBE

    def test_谷が深すぎても中心を越えない(self):
        points = cloud_points(RECT, BalloonSettings(cloud_depth=5.0))
        assert min(distance_ratio(RECT, p) for p in points) > 0.0

    def test_膨らみが少なすぎても形になる(self):
        points = cloud_points(RECT, BalloonSettings(cloud_lobes=1))
        assert len(points) >= 3 * CLOUD_MIN_SEGMENTS_PER_LOBE

    def test_しっぽの付け根はくびれより内側(self, balloon):
        """輪郭の上に置くと、くびれの位置で本体と離れて隙間が空く（→ 6.4）。"""
        balloon.style = "cloud"
        balloon.tail.tip = default_tail_tip(RECT)
        root = tail_root_point(balloon, SETTINGS)
        assert root is not None
        assert distance_ratio(RECT, root) <= 1.0 - SETTINGS.cloud_depth + 1e-9


class TestBubbleTail:
    """丸い飛びしっぽ（心の声・独り言 → 要件定義 10.1）。"""

    @pytest.fixture
    def thinking(self, balloon):
        balloon.tail.shape = TAIL_SHAPE_BUBBLES
        balloon.tail.tip = default_tail_tip(RECT)
        return balloon

    def test_三角のしっぽでは円を作らない(self, balloon):
        assert tail_bubbles(balloon, SETTINGS) == ()

    def test_しっぽを消していれば円も出ない(self, thinking):
        thinking.tail.enabled = False
        assert tail_bubbles(thinking, SETTINGS) == ()

    def test_飛びしっぽでは三角を作らない(self, thinking):
        """呼ぶ側が「三角が無い」と「しっぽが無い」を区別せずに済む。"""
        assert tail_triangle(thinking, SETTINGS) is None

    def test_数は長さによらず変わらない(self, thinking):
        """**先端を引いている最中に円が生えない**（要件定義 10.1）。"""
        for extra in (5.0, 50.0, 500.0):
            thinking.tail.tip = (RECT.center[0], RECT.bottom + extra)
            assert len(tail_bubbles(thinking, SETTINGS)) == TAIL_BUBBLE_COUNT

    def test_先端へ向かって小さくなる(self, thinking):
        radii = [r for _, _, r in tail_bubbles(thinking, SETTINGS)]
        assert radii == sorted(radii, reverse=True)
        assert radii[-1] > 0.0

    def test_伸ばすと円も大きくなる(self, thinking):
        """引いた量が絵に出ること。**大きさを決め打ちにすると出ない。**"""
        thinking.tail.tip = (RECT.center[0], RECT.bottom + 20.0)
        small = tail_bubbles(thinking, SETTINGS)[0][2]
        thinking.tail.tip = (RECT.center[0], RECT.bottom + 60.0)
        assert tail_bubbles(thinking, SETTINGS)[0][2] > small

    def test_本体に食い込まない(self, thinking):
        """離れていること自体がこの形の意味（→ 6.4 とは逆向きの決め）。"""
        for cx, cy, r in tail_bubbles(thinking, SETTINGS):
            # 中心から円の手前側の縁までが、輪郭より外にある
            assert distance_ratio(RECT, (cx, cy)) * (1.0 - 1e-9) > 0.0
            near = distance_ratio(
                RECT, (cx, cy - r) if cy > RECT.center[1] else (cx, cy + r)
            )
            assert near > 1.0

    def test_円どうしも離れている(self, thinking):
        found = tail_bubbles(thinking, SETTINGS)
        # 隣り合う組を作るためのずらし。長さが1つ違うのは意図どおりなので
        # strict=False（`test_ui_context_menu.py` の区切り線チェックと同じ形）
        for (x1, y1, r1), (x2, y2, r2) in zip(found, found[1:], strict=False):
            assert math.hypot(x2 - x1, y2 - y1) > r1 + r2

    def test_先端が輪郭に重なっていれば作らない(self, thinking):
        thinking.tail.tip = (RECT.center[0], RECT.bottom)
        assert tail_bubbles(thinking, SETTINGS) == ()

    def test_長く引いても円は際限なく大きくならない(self, thinking):
        thinking.tail.tip = (RECT.center[0], RECT.bottom + 5000.0)
        capped = tail_bubbles(thinking, SETTINGS)[0][2]
        assert capped <= min(RECT.w, RECT.h) * TAIL_BUBBLE_MAX_RATIO + 1e-9

    def test_円の内側を押すと掴める(self, thinking):
        """先端の丸だけしか掴めないと「見えているのに反応しない」になる。"""
        for cx, cy, _ in tail_bubbles(thinking, SETTINGS):
            assert tail_body_contains(thinking, cx, cy, SETTINGS)

    def test_円と円の隙間では掴めない(self, thinking):
        found = tail_bubbles(thinking, SETTINGS)
        (x1, y1, r1), (x2, y2, _) = found[0], found[1]
        # 1つめの縁のすぐ外側。2つめには届かない
        t = (r1 + 1e-6) / math.hypot(x2 - x1, y2 - y1)
        gap = (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        assert not tail_body_contains(thinking, *gap, SETTINGS)

    def test_三角でも同じ掴み方が効く(self, balloon):
        """形の違いは `tail_body_contains` が吸収する。"""
        balloon.tail.tip = default_tail_tip(RECT)
        triangle = tail_triangle(balloon, SETTINGS)
        assert triangle is not None
        inside = (
            sum(p[0] for p in triangle) / 3.0,
            sum(p[1] for p in triangle) / 3.0,
        )
        assert tail_body_contains(balloon, *inside, SETTINGS)


class TestRect:
    """四角_フキダシ（ナレーション・地の文 → 要件定義 10.1）。"""

    @pytest.fixture
    def boxed(self, balloon):
        balloon.style = "rect"
        return balloon

    def test_輪郭は矩形の4隅(self, boxed):
        assert balloon_outline(boxed, SETTINGS) == (
            (RECT.x, RECT.y),
            (RECT.right, RECT.y),
            (RECT.right, RECT.bottom),
            (RECT.x, RECT.bottom),
        )

    def test_四隅も当たる(self, boxed):
        """**丸い3種とは逆になる**（→ `TestHitTest.test_四隅は当たらない`）。

        楕円のまま判定すると、見えている箱の隅を押しても判定から漏れ、
        下のものが選ばれる。押しても掴めない形で出るので気づきにくい。
        """
        for x, y in balloon_outline(boxed, SETTINGS):
            assert balloon_contains(boxed, x, y)

    def test_外は当たらない(self, boxed):
        assert not balloon_contains(boxed, RECT.x - 0.1, RECT.y)
        assert not balloon_contains(boxed, RECT.right + 0.1, RECT.bottom)

    def test_種類を変えると判定も入れ替わる(self, boxed):
        """判定は保存された `style` だけで決まる（形と食い違わない）。"""
        assert balloon_contains(boxed, RECT.x, RECT.y)
        boxed.style = "ellipse"
        assert not balloon_contains(boxed, RECT.x, RECT.y)

    def test_しっぽの付け根は箱の内側に入る(self, boxed):
        """出したときに破綻しないこと。三角形は辺を突き抜けて出る。"""
        boxed.tail.tip = (RECT.center[0], RECT.bottom + 40.0)
        root = tail_root_point(boxed, SETTINGS)
        assert root is not None
        assert RECT.contains(*root)


class TestAttach:
    def test_中心が乗っているコマに紐づく(self):
        project = new_project()
        page = project.pages[0]
        panel = project.add_panel(page, Rect(10.0, 10.0, 100.0, 80.0))
        assert attach_target(page, Rect(20.0, 20.0, 30.0, 20.0)) == panel.id

    def test_コマの外なら紐づけない(self):
        project = new_project()
        page = project.pages[0]
        project.add_panel(page, Rect(10.0, 10.0, 40.0, 30.0))
        assert attach_target(page, Rect(150.0, 200.0, 30.0, 20.0)) is None


class TestDefaultRect:
    def test_クリック位置が中心になる(self):
        page = new_project().pages[0]
        rect = default_balloon_rect(page, 620.0, 877.0, SETTINGS)
        assert rect.center == pytest.approx((620.0, 877.0))

    @pytest.mark.parametrize("x,y", [(0.0, 0.0), (210.0, 297.0), (-40.0, 400.0)])
    def test_用紙からはみ出さない(self, x, y):
        page = new_project().pages[0]
        rect = default_balloon_rect(page, x, y, SETTINGS)
        assert rect.x >= 0.0 and rect.y >= 0.0
        assert rect.right <= page.size.w and rect.bottom <= page.size.h
