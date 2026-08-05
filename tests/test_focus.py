"""集中線の形と保存形式（要件定義 6.16）。

画面は出てこない。`manga_layout.focus` は Qt を知らないので、形が
正しいかどうかを座標のまま確かめられる。

操作まわり（つまみ・メニュー・履歴）は tests/test_ui_focus.py。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Panel, ProjectFormatError, Rect
from manga_layout.model import FocusLines
from manga_layout import focus as F


def make(**kwargs) -> FocusLines:
    """既定の集中線。試したい項目だけ差し替える。"""
    values = dict(center=(0.5, 0.5), hole=0.15, count=24, width=0.02, seed=7)
    values.update(kwargs)
    return FocusLines(**values)


BOUNDS = Rect(100.0, 200.0, 600.0, 400.0)


def distance(point, center) -> float:
    return math.hypot(point[0] - center[0], point[1] - center[1])


# -- 形 --------------------------------------------------------------------


def test_本数どおりの三角形が出る():
    lines = F.focus_triangles(make(count=36), BOUNDS)
    assert len(lines) == 36
    assert all(len(triangle) == 3 for triangle in lines)


def test_同じ種からは必ず同じ形が出る():
    """**保存する意味そのもの。** 開くたびに形が変われば、書き出した
    PNG と画面が一致しない。
    """
    assert F.focus_triangles(make(), BOUNDS) == F.focus_triangles(make(), BOUNDS)


def test_種が違えば形が変わる():
    assert F.focus_triangles(make(seed=1), BOUNDS) != F.focus_triangles(
        make(seed=2), BOUNDS
    )


def test_隣り合う種でも最初の値が似ない():
    """種をそのまま初期値にすると、線形合同法は**隣り合う種で最初の数個が
    近い値**になる（1本目・2本目の向きが揃って見える）。先に大きな奇数を
    掛けて混ぜている（→ `_Noise`）ことの確認。
    """
    got = [F._Noise(seed).unit() for seed in (100, 101, 102)]
    assert abs(got[0] - got[1]) > 0.1
    assert abs(got[1] - got[2]) > 0.1


def test_乱数は既知の並びを返す():
    """**将来この値が変わったら、既にある作品の形が変わっている。**

    `random` を使わず自前で回している理由がここ（→ 要件定義 6.16）。
    引っかかったときは、乱数の作り方を変えてよいかどうかから考えること。
    """
    noise = F._Noise(12345)
    got = [round(noise.unit(), 6) for _ in range(3)]
    assert got == [0.034107, 0.536223, 0.138532]


def test_線は内側の空きに入らない():
    """空きは白く塗るのではなく、線を引かないことで作る（→ 6.16）。
    ばらつきを**外へだけ**振っているので、どの線も空きより内から
    始まらない。
    """
    focus = make(count=60)
    center = F.center_point(focus, BOUNDS)
    hole = focus.hole * F.short_side(BOUNDS)
    for apex, _, _ in F.focus_triangles(focus, BOUNDS):
        assert distance(apex, center) >= hole - 1e-9


def test_線はコマの隅まで届く():
    """外周は**隅までの最遠距離**。ここが足りないと、コマの角が
    白いまま残る（切り抜きで落とす前提なので、余るぶんは害がない）。
    """
    focus = make(count=48)
    center = F.center_point(focus, BOUNDS)
    corner = max(
        distance(p, center)
        for p in (
            (BOUNDS.x, BOUNDS.y),
            (BOUNDS.right, BOUNDS.y),
            (BOUNDS.right, BOUNDS.bottom),
            (BOUNDS.x, BOUNDS.bottom),
        )
    )
    for _, left, right in F.focus_triangles(focus, BOUNDS):
        assert distance(left, center) >= corner
        assert distance(right, center) >= corner


def test_中心がコマの外にあっても隅まで届く():
    """画面の外から集中させる置き方（→ 5章で範囲を制限しなかった理由）。"""
    focus = make(center=(-0.4, 1.6), count=24)
    center = F.center_point(focus, BOUNDS)
    assert not BOUNDS.contains(*center)
    corner = distance((BOUNDS.right, BOUNDS.y), center)
    for _, left, _ in F.focus_triangles(focus, BOUNDS):
        assert distance(left, center) >= corner


def test_外周では太く_中心側では細い():
    """楔形であること。棒だと中心で潰れて集中線に見えない（→ 6.16）。"""
    for apex, left, right in F.focus_triangles(make(count=12), BOUNDS):
        outer = distance(left, right)
        assert outer > 0.0
        # 頂点は1点なので、そこでの幅は 0
        assert distance(apex, left) > outer


def test_太さは短辺に対する割合():
    """コマの大小によらず同じ見え方になる（→ 6.16 で割合にした理由）。"""
    small = Rect(0.0, 0.0, 200.0, 200.0)
    large = Rect(0.0, 0.0, 400.0, 400.0)
    thin = F.focus_triangles(make(count=8), small)[0]
    thick = F.focus_triangles(make(count=8), large)[0]
    assert distance(thick[1], thick[2]) == pytest.approx(
        distance(thin[1], thin[2]) * 2.0
    )


def test_潰れたコマでは何も作らない():
    """幅か高さが 0 のコマ。基準になる短辺が取れない。"""
    assert F.focus_triangles(make(), Rect(0.0, 0.0, 0.0, 100.0)) == []


def test_空きが大きすぎても三角形が裏返らない():
    """`hole` は読み込みでは 1.0 まで通る。外周に届くほど大きいと
    頂点が底辺の外へ出て、形が裏返る。
    """
    focus = make(hole=1.0, count=8)
    center = F.center_point(focus, BOUNDS)
    outer = F.outer_radius(center, BOUNDS)
    for apex, _, _ in F.focus_triangles(focus, BOUNDS):
        assert distance(apex, center) < outer


# -- つまみの位置と、そこから戻す計算 ----------------------------------------


def test_中心は割合で持つのでコマを縮めても中に残る():
    """絶対座標で持つと、コマを縮めたとき中心が外へ飛び出す
    （`SlantPair.ratio` と同じ理由 → 5章）。
    """
    focus = make(center=(0.25, 0.75))
    small = Rect(BOUNDS.x, BOUNDS.y, BOUNDS.w / 3.0, BOUNDS.h / 3.0)
    assert small.contains(*F.center_point(focus, small))


def test_中心のつまみは掴んだ位置と往復する():
    point = (BOUNDS.x + 150.0, BOUNDS.y + 320.0)
    focus = make(center=F.center_at(BOUNDS, *point))
    assert F.center_point(focus, BOUNDS) == pytest.approx(point)


def test_空きのつまみは中心の右に出る():
    focus = make()
    cx, cy = F.center_point(focus, BOUNDS)
    hx, hy = F.hole_point(focus, BOUNDS)
    assert hy == cy
    assert hx > cx


def test_空きのつまみは横だけ見る():
    """縦を拾うと掴んだ瞬間に値が飛ぶ（しっぽの付け根と同じ → 6.4）。"""
    focus = make()
    cx, _ = F.center_point(focus, BOUNDS)
    assert F.hole_at(focus, BOUNDS, cx + 80.0) == pytest.approx(
        80.0 / F.short_side(BOUNDS)
    )


def test_空きは範囲の端で止まる():
    focus = make()
    cx, _ = F.center_point(focus, BOUNDS)
    assert F.hole_at(focus, BOUNDS, cx - 500.0) == F.HOLE_MIN
    assert F.hole_at(focus, BOUNDS, cx + 5000.0) == F.HOLE_MAX


def test_本数と太さは端で止まる():
    assert F.stepped_count(F.COUNT_MAX, 5) == F.COUNT_MAX
    assert F.stepped_count(F.COUNT_MIN, -5) == F.COUNT_MIN
    assert F.stepped_width(F.WIDTH_MAX, 3) == F.WIDTH_MAX
    assert F.stepped_width(F.WIDTH_MIN, -3) == F.WIDTH_MIN


def test_新しい集中線は設定の値を焼き付ける():
    """あとから設定を変えても、既にあるコマは変わらない（→ 6.10 と同じ）。"""
    settings = F.FocusSettings(count=40, width=0.05, hole=0.3)
    created = F.default_focus(settings)
    assert (created.count, created.width, created.hole) == (40, 0.05, 0.3)
    assert created.center == (0.5, 0.5)


def test_種は毎回違う():
    assert len({F.new_seed() for _ in range(20)}) > 1


# -- 保存形式 --------------------------------------------------------------


def test_保存して読み戻すと同じ():
    focus = make(center=(0.3, 0.8), hole=0.2, count=90, width=0.03, seed=4242)
    assert FocusLines.from_dict(focus.to_dict(), "focus") == focus


def test_入れていないコマでは項目ごと省く():
    """使っていない作品の project.json が、この機能の追加前と同じ内容の
    ままになる（→ 5章）。
    """
    assert "focus_lines" not in Panel(id="panel_0001").to_dict()


def test_項目の無いコマは集中線なしとして読める():
    """この機能より前の作品がそのまま開ける。"""
    data = Panel(id="panel_0001").to_dict()
    assert Panel.from_dict(data, "panel").focus_lines is None


def test_コマごと往復する():
    panel = Panel(id="panel_0001", focus_lines=make())
    assert Panel.from_dict(panel.to_dict(), "panel").focus_lines == make()


def test_割ると前半に残る():
    """`split_panel` は元のコマを前半として id ごと残す。集中線はコマの
    属性なので、そのコマに付いたまま残り、新しくできた側には付かない。
    """
    from manga_layout import new_project
    from manga_layout.layout import split_panel

    project = new_project()
    page = project.pages[0]
    panel = project.add_panel(page, Rect(0.0, 0.0, 600.0, 400.0))
    panel.focus_lines = make()

    first, second = split_panel(project, page, panel.id, horizontal=True, position=200.0)
    assert first.focus_lines == make()
    assert second.focus_lines is None


@pytest.mark.parametrize(
    "key, value",
    [
        ("hole", 1.5),
        ("hole", -0.1),
        ("width", 2.0),
        ("count", 3),
        ("count", 401),
        ("seed", -1),
    ],
)
def test_範囲外は切り詰めずに弾く(key, value):
    """黙って直すと、書き出した値と読み戻した値が食い違い、保存のたびに
    形が変わる（`Tail.root_y` と同じ → 5章）。
    """
    data = make().to_dict()
    data[key] = value
    with pytest.raises(ProjectFormatError):
        FocusLines.from_dict(data, "focus")
