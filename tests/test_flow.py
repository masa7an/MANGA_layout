"""流線の形と保存形式（要件定義 6.26）。

画面は出てこない。`manga_layout.flow` は Qt を知らないので、形が正しい
かどうかを座標のまま確かめられる（`focus.py` と同じ作り）。

操作まわり（つまみ・メニュー・履歴）は tests/test_ui_flow.py。
"""

from __future__ import annotations

import math

import pytest

from manga_layout import Panel, ProjectFormatError, Rect, flow as FL
from manga_layout.model import FlowLines


def make(**kwargs) -> FlowLines:
    """既定の流線。試したい項目だけ差し替える。"""
    values = dict(angle=0.0, count=24, width=0.02, length=0.6, seed=7)
    values.update(kwargs)
    return FlowLines(**values)


BOUNDS = Rect(100.0, 200.0, 600.0, 400.0)


def along_axis(point, bounds: Rect, angle: float) -> tuple[float, float]:
    """点を「線に沿った量・直交する量」に直す。中心が原点。"""
    cx, cy = bounds.center
    dx, dy = FL.direction(angle)
    px, py = point[0] - cx, point[1] - cy
    return (px * dx + py * dy, px * -dy + py * dx)


# -- 形 --------------------------------------------------------------------


def test_本数どおりの線が出る():
    lines = FL.flow_polygons(make(count=36), BOUNDS)
    assert len(lines) == 36


def test_1本は6つの頂点を持つ():
    """両端が尖り、真ん中が幅一定の紡錘形（→ 6.26）。四角形だと端が
    切り落とされた棒になり、定規で引いた線に見える。
    """
    assert all(len(line) == 6 for line in FL.flow_polygons(make(), BOUNDS))


def test_同じ種からは必ず同じ形が出る():
    """**保存する意味そのもの。** 開くたびに形が変われば、書き出した
    PNG と画面が一致しない（集中線と同じ → 6.16）。
    """
    assert FL.flow_polygons(make(), BOUNDS) == FL.flow_polygons(make(), BOUNDS)


def test_種が違えば形が変わる():
    assert FL.flow_polygons(make(seed=1), BOUNDS) != FL.flow_polygons(
        make(seed=2), BOUNDS
    )


def test_線はすべて平行():
    """流線は平行線。**向きだけで決まり、線ごとに傾かない**（集中線との
    いちばん大きな違い）。
    """
    for angle in (0.0, 30.0, -125.0):
        for line in FL.flow_polygons(make(angle=angle), BOUNDS):
            head, tail = line[0], line[3]
            got = math.degrees(math.atan2(head[1] - tail[1], head[0] - tail[0]))
            assert abs((got - angle + 180.0) % 360.0 - 180.0) < 1e-6


def test_向きを変えると形も変わる():
    assert FL.flow_polygons(make(angle=0.0), BOUNDS) != FL.flow_polygons(
        make(angle=45.0), BOUNDS
    )


def test_長さ1_0の線はコマを貫く():
    """**`length` の基準は対角線**（→ 6.26）。矩形の中でいちばん長い弦が
    対角線なので、1.0 なら向きによらず必ず貫く。短辺基準や長辺基準だと、
    向き次第で貫かない。

    ばらつきは短くする側にも振れるが、そのぶん**線に沿ったずらし幅も
    余りの中に収まる**ので、コマの外へ出る側は必ず残る。ここで見るのは
    「ばらつきの前の長さが対角線ぶんある」ことなので、`LENGTH_JITTER` を
    掛ける前の値で確かめる。
    """
    for angle in (0.0, 22.5, 90.0, -60.0):
        diag = FL.diagonal(BOUNDS)
        # コマの中でいちばん長い弦。どの向きでもこれ以下
        assert diag >= math.hypot(BOUNDS.w, BOUNDS.h) - 1e-9
        lines = FL.flow_polygons(make(angle=angle, length=1.0, count=12), BOUNDS)
        for line in lines:
            head = along_axis(line[0], BOUNDS, angle)[0]
            tail = along_axis(line[3], BOUNDS, angle)[0]
            assert abs(head - tail) >= diag * (1.0 - FL.LENGTH_JITTER) - 1e-6


def test_線はコマの幅いっぱいに散る():
    """帯は**矩形を向きに直交する方向へ潰した長さ**。対角線ぶん取ると、
    横長のコマに水平の線を引いたときに半分近くが外へ落ち、**指定した
    本数より見える数が少なくなる**（→ 6.26）。
    """
    lines = FL.flow_polygons(make(angle=0.0, count=40), BOUNDS)
    across = [along_axis(line[0], BOUNDS, 0.0)[1] for line in lines]
    # 水平の線なら、散らばる範囲はコマの高さ
    assert min(across) < -BOUNDS.h * 0.4
    assert max(across) > BOUNDS.h * 0.4
    assert all(abs(a) <= BOUNDS.h for a in across)


def test_帯の幅は向きで変わる():
    """水平ならコマの高さ、垂直ならコマの幅。"""
    assert FL.band_width(BOUNDS, 0.0) == pytest.approx(BOUNDS.h)
    assert FL.band_width(BOUNDS, 90.0) == pytest.approx(BOUNDS.w)
    assert FL.band_width(BOUNDS, 180.0) == pytest.approx(BOUNDS.h)


def test_端は細く_真ん中は太い():
    """紡錘形であることの確認。端の頂点は線の軸の上に乗り、真ん中の
    2点は軸から太さの半分だけ離れる。
    """
    flow = make(angle=0.0, count=4, width=0.05)
    half = flow.width * FL.short_side(BOUNDS) / 2.0
    for line in FL.flow_polygons(flow, BOUNDS):
        head, upper, lower = line[0], line[1], line[5]
        # 端の点と、そのすぐ内側の2点。直交方向の差が太さの半分
        assert abs(along_axis(head, BOUNDS, 0.0)[1] - along_axis(upper, BOUNDS, 0.0)[1]) <= half + 1e-9
        assert abs(along_axis(head, BOUNDS, 0.0)[1] - along_axis(lower, BOUNDS, 0.0)[1]) <= half + 1e-9


def test_太さのばらつきは細くする側だけ():
    """太くする側へ振ると、隣と重なって塊になる（集中線と同じ）。"""
    flow = make(count=30, width=0.03)
    half = flow.width * FL.short_side(BOUNDS) / 2.0
    for line in FL.flow_polygons(flow, BOUNDS):
        got = abs(along_axis(line[1], BOUNDS, 0.0)[1] - along_axis(line[5], BOUNDS, 0.0)[1])
        assert got <= half * 2.0 + 1e-9


def test_つぶれたコマでは何も出ない():
    """幅か高さが 0 のコマ。0 除算で落ちない。"""
    assert FL.flow_polygons(make(), Rect(0.0, 0.0, 0.0, 100.0)) == []


# -- つまみ ----------------------------------------------------------------


def test_つまみは向きの先に出る():
    """コマの中心から `angle` の向きへ、短辺の一定割合だけ離す。"""
    handle = FL.handle_point(make(angle=0.0), BOUNDS)
    cx, cy = BOUNDS.center
    assert handle[1] == pytest.approx(cy)
    assert handle[0] > cx


def test_つまみの位置から向きが戻る():
    """描く側と掴む側で答えが一致すること。片方だけ直すと、見えている
    印と掴める場所がズレる。
    """
    for angle in (0.0, 30.0, 120.0, -90.0):
        handle = FL.handle_point(make(angle=angle), BOUNDS)
        assert FL.angle_at(BOUNDS, *handle) == pytest.approx(angle)


def test_距離は見ない():
    """つまみから離れた場所を掴んでも、向きだけを見る（→ 6.26）。"""
    cx, cy = BOUNDS.center
    near = FL.angle_at(BOUNDS, cx + 10.0, cy + 10.0)
    far = FL.angle_at(BOUNDS, cx + 1000.0, cy + 1000.0)
    assert near == pytest.approx(far)


def test_中心ちょうどでは既定の向きに落ちる():
    """向きが決められない場所。ここで例外を出すと、掴んだ瞬間に落ちる。"""
    assert FL.angle_at(BOUNDS, *BOUNDS.center) == FL.DEFAULT_FLOW_SETTINGS.angle


# -- 増減 ------------------------------------------------------------------


def test_本数と太さと長さは端で止まる():
    assert FL.stepped_count(FL.COUNT_MAX, 5) == FL.COUNT_MAX
    assert FL.stepped_count(FL.COUNT_MIN, -5) == FL.COUNT_MIN
    assert FL.stepped_width(FL.WIDTH_MAX, 5) == FL.WIDTH_MAX
    assert FL.stepped_width(FL.WIDTH_MIN, -5) == FL.WIDTH_MIN
    assert FL.stepped_length(FL.LENGTH_MAX, 5) == FL.LENGTH_MAX
    assert FL.stepped_length(FL.LENGTH_MIN, -5) == FL.LENGTH_MIN


def test_新しい流線は設定の値を焼き付ける():
    """あとから設定を変えても、既にあるコマは変わらない（→ 6.16 と同じ）。"""
    settings = FL.FlowSettings(count=16, width=0.05, length=0.9, angle=45.0)
    created = FL.default_flow(settings)
    assert (created.count, created.width, created.length, created.angle) == (
        16,
        0.05,
        0.9,
        45.0,
    )


# -- 保存形式 --------------------------------------------------------------


def test_保存して読み戻すと同じ():
    flow = make(angle=-30.0, count=90, width=0.03, length=0.8, seed=4242)
    assert FlowLines.from_dict(flow.to_dict(), "flow") == flow


def test_黒のときは色の項目を持たない():
    """入れていない作品の project.json を、この機能の追加前と同じ内容の
    ままにする（集中線・ロックと同じ線引き → 6.19、6.17）。
    """
    assert "white" not in make().to_dict()
    assert make(white=True).to_dict()["white"] is True


def test_角度は弾かずに畳む():
    """角度は周期的な量。範囲外を弾くと -10 度のような正しい値まで
    読めなくなる（→ 6.26）。
    """
    assert FlowLines.from_dict(make(angle=370.0).to_dict(), "flow").angle == pytest.approx(10.0)
    assert FlowLines.from_dict(make(angle=-10.0).to_dict(), "flow").angle == pytest.approx(-10.0)


def test_畳んだ角度は何度読み戻しても変わらない():
    """保存と読み込みで値が食い違うと、保存のたびに形が変わる
    （`Tail.root_y` と同じ → 5章）。
    """
    once = FlowLines.from_dict(make(angle=540.0).to_dict(), "flow")
    twice = FlowLines.from_dict(once.to_dict(), "flow")
    assert once.angle == twice.angle


@pytest.mark.parametrize(
    "field, value",
    [("width", 1.5), ("width", -0.1), ("length", 1.5), ("count", 2), ("count", 1000)],
)
def test_範囲外は切り詰めずに弾く(field, value):
    data = make().to_dict()
    data[field] = value
    with pytest.raises(ProjectFormatError):
        FlowLines.from_dict(data, "flow")


def test_種が負なら弾く():
    data = make().to_dict()
    data["seed"] = -1
    with pytest.raises(ProjectFormatError):
        FlowLines.from_dict(data, "flow")


# -- コマとの関係 ----------------------------------------------------------


def test_入れていないコマは項目ごと省く():
    """この機能より前の作品と同じ内容のままになる（→ 6.26）。"""
    panel = Panel(id="panel_0001")
    assert "flow_lines" not in panel.to_dict()


def test_集中線と同時に持てる():
    """別の項目なので、止めなければ両方入る。止めるほうがコードが増える
    （→ 6.26）。
    """
    from manga_layout import focus as F

    panel = Panel(id="panel_0001")
    panel.flow_lines = make()
    panel.focus_lines = F.default_focus()
    restored = Panel.from_dict(panel.to_dict(), "panel")
    assert restored.flow_lines == panel.flow_lines
    assert restored.focus_lines == panel.focus_lines


def test_コマを往復しても流線が残る():
    panel = Panel(id="panel_0001")
    panel.flow_lines = make(angle=15.0)
    assert Panel.from_dict(panel.to_dict(), "panel").flow_lines == panel.flow_lines
