"""斜めの縦割りの検証。

形を作る計算・当たり判定・組の維持を、画面なしで固定する。
数字は角度 12°（水平の辺から 78°）・隙間 6mm を前提にしている。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Polygon, Rect, new_project
from manga_layout.model import SLANT_LEFT, SLANT_RIGHT, Project, SlantPair
from manga_layout.layout import (
    LayoutSettings,
    image_at,
    panel_at,
    set_panel_rect,
    split_panel,
)
from manga_layout.slant import (
    check_slant,
    clamp_slant_rect,
    flip_slant_pair,
    set_slant_pair_rect,
    slant_gap,
    slant_max_height,
    slant_min_width,
    slant_narrowest,
    slant_offset,
    slant_polygons,
    slant_ratio_at,
    slant_ratio_bounds,
    slide_slant_pair,
    split_panel_slant,
)

SETTINGS = LayoutSettings(gutter=6.0, margin=15.0, min_panel_size=5.0, slant_angle=12.0)

# 検証しやすいよう 100mm 角のコマを中央で割る。
# このとき境界は上端 60.628 / 下端 39.372 を通り、隙間は左右へ 3.067 ずつ
SQUARE = Rect(0.0, 0.0, 100.0, 100.0)


def flat(points) -> list[float]:
    """頂点列を平らにする。`pytest.approx` は入れ子を扱えない。"""
    return [value for point in points for value in point]


@pytest.fixture
def slant_page():
    """100mm 角のコマを1つだけ持つページ。"""
    project = new_project()
    page = project.pages[0]
    project.add_panel(page, SQUARE)
    return project, page


@pytest.fixture
def split_page(slant_page):
    """上の1コマを中央で斜めに割ったページ。"""
    project, page = slant_page
    left, right = split_panel_slant(
        project, page, page.panels[0].id, position=50.0, settings=SETTINGS
    )
    return project, page, left, right


class TestPolygonContains:
    def test_矩形は辺の上も内側(self):
        poly = Polygon.from_rect(Rect(10.0, 10.0, 20.0, 20.0))
        assert poly.contains(20.0, 20.0)
        assert poly.contains(10.0, 10.0)
        assert poly.contains(30.0, 30.0)
        assert not poly.contains(9.0, 20.0)
        assert not poly.contains(31.0, 20.0)

    def test_三角形の外側は外接矩形の中でも外(self):
        poly = Polygon(((0.0, 0.0), (10.0, 0.0), (0.0, 10.0)))
        assert poly.contains(1.0, 1.0)
        # 外接矩形には入るが、斜辺の外側にある
        assert not poly.contains(9.0, 9.0)


class TestSlantShape:
    def test_境界の傾きが指定した角度になる(self):
        left, _ = slant_polygons(SQUARE, 0.5, 12.0, SLANT_RIGHT, 6.0)
        (_, top), (_, bottom) = (left.points[1], left.points[2])
        top_x, bottom_x = left.points[1][0], left.points[2][0]
        angle = math.degrees(math.atan2(abs(top_x - bottom_x), bottom - top))
        assert angle == pytest.approx(12.0)

    def test_向きで傾きが反転する(self):
        right_lean, _ = slant_polygons(SQUARE, 0.5, 12.0, SLANT_RIGHT, 6.0)
        left_lean, _ = slant_polygons(SQUARE, 0.5, 12.0, SLANT_LEFT, 6.0)
        # "/" は上端が右、"\" は上端が左
        assert right_lean.points[1][0] > right_lean.points[2][0]
        assert left_lean.points[1][0] < left_lean.points[2][0]

    def test_隙間は垂直に測って設定どおり(self):
        """真横にずらすだけだと、傾けたぶん隙間が細く見えてしまう。"""
        left, right = slant_polygons(SQUARE, 0.5, 12.0, SLANT_RIGHT, 6.0)
        horizontal = right.points[0][0] - left.points[1][0]
        assert horizontal * math.cos(math.radians(12.0)) == pytest.approx(6.0)

    def test_外側の矩形をはみ出さない(self):
        left, right = slant_polygons(SQUARE, 0.5, 12.0, SLANT_RIGHT, 6.0)
        for poly in (left, right):
            for x, y in poly.points:
                assert SQUARE.x - 1e-9 <= x <= SQUARE.right + 1e-9
                assert SQUARE.y - 1e-9 <= y <= SQUARE.bottom + 1e-9

    def test_細いほうの幅が計算どおり(self):
        narrowest = slant_narrowest(SQUARE, 0.5, 12.0, 6.0)
        expected = 50.0 - slant_offset(100.0, 12.0) - slant_gap(6.0, 12.0)
        assert narrowest == pytest.approx(expected)

    def test_高さを伸ばすほど必要な幅が増える(self):
        narrow = slant_min_width(100.0, 0.5, 12.0, SETTINGS)
        wide = slant_min_width(200.0, 0.5, 12.0, SETTINGS)
        assert wide > narrow

    def test_最大の高さは最小の幅と裏表(self):
        width = slant_min_width(150.0, 0.4, 12.0, SETTINGS)
        assert slant_max_height(width, 0.4, 12.0, SETTINGS) == pytest.approx(150.0)


class TestCheckSlant:
    def test_細すぎると断る(self):
        with pytest.raises(ValueError, match="最小"):
            check_slant(Rect(0.0, 0.0, 20.0, 200.0), 0.5, 12.0, SETTINGS)

    def test_コマの外を指したら断る(self):
        with pytest.raises(ValueError, match="内側"):
            check_slant(SQUARE, 0.0, 12.0, SETTINGS)


class TestSplitSlant:
    def test_組ができる(self, split_page):
        _, page, left, right = split_page
        assert len(page.panels) == 2
        assert len(page.slant_pairs) == 1
        pair = page.slant_pairs[0]
        assert pair.members() == (left.id, right.id)
        assert pair.ratio == pytest.approx(0.5)
        assert pair.angle == pytest.approx(12.0)

    def test_元のコマが左として残る(self, slant_page):
        project, page = slant_page
        original = page.panels[0].id
        left, right = split_panel_slant(
            project, page, original, position=50.0, settings=SETTINGS
        )
        assert left.id == original
        assert page.panels.index(right) == page.panels.index(left) + 1

    def test_外側の矩形が元に戻る(self, split_page):
        _, page, _, _ = split_page
        bounds = page.slant_bounds(page.slant_pairs[0])
        assert bounds.x == pytest.approx(SQUARE.x)
        assert bounds.y == pytest.approx(SQUARE.y)
        assert bounds.w == pytest.approx(SQUARE.w)
        assert bounds.h == pytest.approx(SQUARE.h)

    def test_角度をペアに焼き付ける(self, slant_page):
        """既定の角度を変えても、作成済みのコマは変形しない。"""
        project, page = slant_page
        split_panel_slant(project, page, page.panels[0].id, position=50.0, settings=SETTINGS)
        assert page.slant_pairs[0].angle == pytest.approx(12.0)

        other = LayoutSettings(gutter=6.0, margin=15.0, min_panel_size=5.0, slant_angle=25.0)
        before = flat(page.panels[0].shape.points)
        flip_slant_pair(page, page.slant_pairs[0], other)
        flip_slant_pair(page, page.slant_pairs[0], other)
        assert flat(page.panels[0].shape.points) == pytest.approx(before)

    def test_画像は境界の左右で振り分ける(self, slant_page):
        project, page = slant_page
        panel = page.panels[0]
        # 下端では境界が x=39.4 付近まで寄る。同じ x でも高さで行き先が変わる
        top = project.add_image(panel, "a.png", Rect(45.0, 5.0, 10.0, 10.0), (100, 100))
        bottom = project.add_image(panel, "b.png", Rect(45.0, 85.0, 10.0, 10.0), (100, 100))
        left, right = split_panel_slant(
            project, page, panel.id, position=50.0, settings=SETTINGS
        )
        assert top in left.children
        assert bottom in right.children

    def test_斜めのコマは再分割できない(self, split_page):
        project, page, left, _ = split_page
        with pytest.raises(ValueError, match="これ以上分割できません"):
            split_panel_slant(project, page, left.id, position=30.0, settings=SETTINGS)
        with pytest.raises(ValueError, match="斜めのコマは"):
            split_panel(
                project, page, left.id, horizontal=True, position=50.0, settings=SETTINGS
            )

    def test_割れない位置は断る(self, slant_page):
        project, page = slant_page
        with pytest.raises(ValueError, match="最小"):
            split_panel_slant(project, page, page.panels[0].id, position=8.0, settings=SETTINGS)


class TestSlantHitTest:
    def test_斜めに削られた側は隣のコマにならない(self, split_page):
        """外接矩形で判定していたときに取り違えていた場所。"""
        _, page, left, right = split_page
        assert panel_at(page, 50.0, 10.0) is left
        assert panel_at(page, 50.0, 90.0) is right

    def test_隙間の上はどちらでもない(self, split_page):
        """(50, 50) は左右どちらの外接矩形にも入るが、どちらのコマでもない。"""
        _, page, _, _ = split_page
        assert panel_at(page, 50.0, 50.0) is None

    def test_コマの外にはみ出た画像は掴めない(self, split_page):
        project, page, left, _ = split_page
        image = project.add_image(left, "a.png", Rect(25.0, 80.0, 25.0, 15.0), (100, 100))
        # 画像の矩形の中だが、斜めに削られてコマの外に出ている位置
        assert image_at(left, 48.0, 90.0) is None
        assert image_at(left, 30.0, 82.0) is image


class TestSlantPairOperations:
    def test_移動は相方も一緒に動く(self, split_page):
        _, page, left, right = split_page
        before = right.shape.points
        page.move_panel(left.id, 10.0, 5.0)
        assert flat(right.shape.points) == pytest.approx(
            flat((x + 10.0, y + 5.0) for x, y in before)
        )

    def test_リサイズしても傾きと隙間が保たれる(self, split_page):
        _, page, left, _ = split_page
        pair = page.slant_pairs[0]
        set_slant_pair_rect(page, pair, Rect(0.0, 0.0, 160.0, 200.0), SETTINGS)

        assert page.slant_bounds(pair).w == pytest.approx(160.0)
        top_x, bottom_x = left.shape.points[1][0], left.shape.points[2][0]
        angle = math.degrees(math.atan2(abs(top_x - bottom_x), 200.0))
        assert angle == pytest.approx(12.0)

    def test_リサイズで分割位置が割合のまま付いてくる(self, slant_page):
        project, page = slant_page
        split_panel_slant(project, page, page.panels[0].id, position=30.0, settings=SETTINGS)
        pair = page.slant_pairs[0]
        set_slant_pair_rect(page, pair, Rect(0.0, 0.0, 200.0, 100.0), SETTINGS)
        # 割合 0.3 のまま。絶対値で持っていたら 30mm に留まってしまう
        left_bounds = page.panel(pair.left_id).shape.bounds()
        assert left_bounds.right == pytest.approx(
            60.0 + slant_offset(100.0, 12.0) - slant_gap(6.0, 12.0)
        )

    def test_縮めすぎると押し戻す(self, split_page):
        _, page, _, _ = split_page
        pair = page.slant_pairs[0]
        clamped = clamp_slant_rect(pair, Rect(0.0, 0.0, 20.0, 100.0), SETTINGS)
        assert clamped.h < 100.0
        # 押し戻した後は割れる大きさになっている
        check_slant(clamped, pair.ratio, pair.angle, SETTINGS)

    def test_反転しても外側の矩形は変わらない(self, split_page):
        _, page, _, _ = split_page
        before = page.slant_bounds(page.slant_pairs[0])
        flipped = flip_slant_pair(page, page.slant_pairs[0], SETTINGS)
        after = page.slant_bounds(flipped)
        assert flipped.direction == SLANT_LEFT
        assert (after.x, after.y, after.w, after.h) == pytest.approx(
            (before.x, before.y, before.w, before.h)
        )

    def test_2回反転すると元に戻る(self, split_page):
        _, page, left, _ = split_page
        before = flat(left.shape.points)
        pair = flip_slant_pair(page, page.slant_pairs[0], SETTINGS)
        flip_slant_pair(page, pair, SETTINGS)
        assert flat(left.shape.points) == pytest.approx(before)

    def test_片方を消すと組が解け_残りは斜めのまま(self, split_page):
        _, page, left, right = split_page
        shape = right.shape.points
        page.remove_panel(left.id)
        assert page.slant_pairs == []
        assert right.shape.points == shape
        assert page.slant_pair_of(right.id) is None

    def test_組が解けた後もリサイズで傾きが残る(self, split_page):
        _, page, left, right = split_page
        page.remove_panel(left.id)
        set_panel_rect(right, Rect(0.0, 0.0, 50.0, 50.0))
        assert right.shape.as_rect() is None
        assert right.shape.bounds().w == pytest.approx(50.0)


class TestSlantPersistence:
    def test_往復しても組が残る(self, split_page):
        project, page, left, right = split_page
        pair = project.copy().pages[0].slant_pairs[0]
        assert pair.members() == (left.id, right.id)
        assert pair.direction == SLANT_RIGHT
        assert pair.ratio == pytest.approx(page.slant_pairs[0].ratio)

    def test_斜めが無いページには項目を書かない(self, slant_page):
        _, page = slant_page
        assert "slant_pairs" not in page.to_dict()

    def test_存在しないコマを指した組は解いて開く(self, split_page):
        project, page, left, _ = split_page
        data = project.to_dict()
        data["pages"][0]["slant_pairs"][0]["left"] = "panel_9999"
        loaded = Project.from_dict(data)
        assert loaded.pages[0].slant_pairs == []
        assert any("斜めの組" in w for w in loaded.load_warnings)
        # コマ自体は消えず、形も保たれる
        assert len(loaded.pages[0].panels) == 2

    def test_1枚が2つの組に入っていたら後ろを解く(self, split_page):
        project, page, left, right = split_page
        data = project.to_dict()
        data["pages"][0]["slant_pairs"].append(
            SlantPair(left.id, right.id, 0.3, 12.0, SLANT_LEFT).to_dict()
        )
        loaded = Project.from_dict(data)
        assert len(loaded.pages[0].slant_pairs) == 1
        assert loaded.pages[0].slant_pairs[0].direction == SLANT_RIGHT

    def test_壊れた値は読み込みを断る(self, split_page):
        from manga_layout.errors import ProjectFormatError

        project, _, _, _ = split_page
        for key, bad in (("ratio", 1.5), ("angle", 90.0), ("direction", "|")):
            data = project.to_dict()
            data["pages"][0]["slant_pairs"][0][key] = bad
            with pytest.raises(ProjectFormatError):
                Project.from_dict(data)


class TestSlideSlant:
    """境界を左右にずらす。"""

    def test_ずらすと形が付いてくる(self, split_page):
        _, page, left, _ = split_page
        pair = slide_slant_pair(page, page.slant_pairs[0], 0.3, SETTINGS)
        assert pair.ratio == pytest.approx(0.3)
        # 左が細くなり、右が太くなる
        assert left.shape.bounds().w < 50.0

    def test_外側の矩形は変わらない(self, split_page):
        _, page, _, _ = split_page
        before = page.slant_bounds(page.slant_pairs[0])
        after = page.slant_bounds(slide_slant_pair(page, page.slant_pairs[0], 0.3, SETTINGS))
        assert (after.x, after.y, after.w, after.h) == pytest.approx(
            (before.x, before.y, before.w, before.h)
        )

    def test_角度と向きは変わらない(self, split_page):
        _, page, _, _ = split_page
        before = page.slant_pairs[0]
        after = slide_slant_pair(page, before, 0.28, SETTINGS)
        assert after.angle == before.angle
        assert after.direction == before.direction

    def test_行きすぎたら押し戻す(self, split_page):
        _, page, _, _ = split_page
        pair = slide_slant_pair(page, page.slant_pairs[0], 0.01, SETTINGS)
        low, high = slant_ratio_bounds(SQUARE, 12.0, SETTINGS)
        assert pair.ratio == pytest.approx(low)
        # 押し戻した先はちゃんと割れる
        check_slant(SQUARE, pair.ratio, pair.angle, SETTINGS)

    def test_範囲は左右対称(self):
        low, high = slant_ratio_bounds(SQUARE, 12.0, SETTINGS)
        assert low + high == pytest.approx(1.0)
        assert 0.0 < low < 0.5

    def test_割れない大きさでは動かせない(self):
        """縦に長すぎるコマ。中央のみを返し、ドラッグしても動かない。"""
        assert slant_ratio_bounds(Rect(0.0, 0.0, 20.0, 300.0), 12.0, SETTINGS) == (0.5, 0.5)

    def test_中の画像は動かないし所属も変わらない(self, slant_page):
        """境界を絵の向こうへ送っても、絵は元のコマに残る（切り抜かれる）。"""
        project, page = slant_page
        panel = page.panels[0]
        image = project.add_image(panel, "a.png", Rect(20.0, 20.0, 15.0, 15.0), (100, 100))
        left, right = split_panel_slant(
            project, page, panel.id, position=50.0, settings=SETTINGS
        )
        assert image in left.children

        before = image.rect
        slide_slant_pair(page, page.slant_pairs[0], 0.2, SETTINGS)
        assert image in left.children
        assert image not in right.children
        assert image.rect == before

    def test_割合への変換(self):
        assert slant_ratio_at(Rect(10.0, 0.0, 100.0, 50.0), 60.0) == pytest.approx(0.5)

    def test_限界ちょうどまでずらしても落ちない(self, split_page):
        """押し戻した値をそのまま作り直しへ渡す経路の回帰。

        `clamp_slant_ratio` は最小幅ぴったりの割合を作る。厳密に比べると
        浮動小数の丸めで `check_slant` に弾かれ、限界まで引いた瞬間に
        ValueError で落ちていた。
        """
        _, page, _, _ = split_page
        low, high = slant_ratio_bounds(SQUARE, 12.0, SETTINGS)
        for target in (low, high):
            pair = slide_slant_pair(page, page.slant_pairs[0], target, SETTINGS)
            assert pair.ratio == pytest.approx(target)

    def test_限界ちょうどまで縮めても落ちない(self, split_page):
        """リサイズ側の同じ経路。`clamp_slant_rect` の返り値を通す。"""
        _, page, _, _ = split_page
        pair = page.slant_pairs[0]
        clamped = clamp_slant_rect(pair, Rect(0.0, 0.0, 20.0, 100.0), SETTINGS)
        set_slant_pair_rect(page, pair, clamped, SETTINGS)
        assert page.slant_bounds(pair).h == pytest.approx(clamped.h)
