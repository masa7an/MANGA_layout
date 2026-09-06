"""押したものに手が届くか（`PageView.mousePressEvent` の判定順）。

`mousePressEvent` は**17か所で打ち切る順序依存のチェーン**で、並び順そのものが
仕様になっている（→ `manga_layout/ui/canvas.py`）。順序が壊れたときの症状は
**覆い隠し**——広い判定を先に見ると、細い判定が押し出されて掴めなくなる。

ここで見るのは1つだけ。

> **どのつまみも、そのつまみ自身の中心を押したら、そのつまみが勝つ。**

つまみの位置は**描く側と同じ関数**から取る（`PageScene.slant_handle` など）。
座標を書き写すと、印の位置を変えたときにテストだけが古い場所を押し続ける。

**表示倍率を軸に入れてある。** 掴む範囲は全部「画面の px ÷ 表示倍率」で決まる
ので、**縮小すると判定どうしが実際に食い合う**（→ `TestZoomedOut`）。倍率を
1通りしか見ないと、その食い合いに気づけない。

観測するのは「どのドラッグが始まったか」。押下の答えはこれで、
`ResizeDrag` は掴んだつまみ（`handle`）、`FocusDrag` はどちらのつまみか
（`kind`）まで見る。
"""

from __future__ import annotations

import math

import pytest
from mouse import press

from manga_layout import Rect
from manga_layout.layout import (
    handle_positions,
    tail_bubbles,
    tail_root_point,
    tail_triangle,
)
from manga_layout.model import TAIL_SHAPE_BUBBLES
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.canvas import Drag
from manga_layout.ui.state import (
    TOOL_BALLOON,
    TOOL_PANEL,
    TOOL_ROUGH,
    TOOL_SELECT,
    TOOL_SPLIT_SLANT,
    TOOL_TONE_AREA,
)

# 座標は px（要件定義 3章）
PANEL = Rect(120.0, 120.0, 720.0, 540.0)
BALLOON = Rect(300.0, 250.0, 240.0, 160.0)
ROUGH = Rect(100.0, 200.0, 400.0, 400.0)

# 確かめる表示倍率。**等倍から上だけ**を見る。
# 下は判定どうしが食い合うので、そちらは `TestZoomedOut` で別に扱う
SCALES = (1.0, 2.5, 8.0)


@pytest.fixture
def make(qapp):
    """場面を作って窓を返す。後始末（保存済みにして閉じる）もここで持つ。"""
    windows = []

    def build(setup) -> MainWindow:
        window = MainWindow(EditorState())
        window.state.set_tool(TOOL_SELECT)
        windows.append(window)
        setup(window)
        return window

    yield build

    for window in windows:
        window.view.finish_text_edit(commit=False)
        window.state.history.mark_saved()
        window.close()


def at_scale(view, want: float) -> None:
    """表示倍率をその値にする。**上下限で止まる**ので、確かめてから使う。"""
    view.zoom_by(want / view.view_scale, at_mouse=False)
    assert view.view_scale == pytest.approx(want, rel=1e-6)


def grabbed(view, x: float, y: float) -> str:
    """そこを押したときに始まるドラッグの名前。何も始まらなければ「なし」。

    **押すたびに掴んだものを外す。** 外さないと次の押下が前のドラッグの
    続きとして扱われ、2つめ以降の答えが変わる。
    """
    view._drag = None
    press(view, x, y)
    drag = view._drag
    name = type(drag).__name__ if drag is not None else "なし"
    for attr in ("handle", "kind"):
        # **None は出さない。** 掴んだつまみを持たない引き方（範囲を囲い
        # 直すときなど）で「[None]」が混ざると、名前が読みにくくなる
        if getattr(drag, attr, None) is not None:
            name += f"[{getattr(drag, attr)}]"
    view._drag = None
    return name


# -- 場面を作る --------------------------------------------------------------


def png(name: str = "rgb_opaque.png") -> bytes:
    import pathlib

    return (pathlib.Path(__file__).parent / "fixtures" / name).read_bytes()


def 場面_コマ(window) -> None:
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(window.state.page.panels[0].id)


def 場面_フキダシ(window) -> None:
    場面_コマ(window)
    window.state.select(window.state.add_balloon(BALLOON).id)


def 場面_飛びしっぽ(window) -> None:
    場面_フキダシ(window)
    window.state.selected_balloon.tail.shape = TAIL_SHAPE_BUBBLES


def 場面_画像(window) -> None:
    場面_コマ(window)
    window.state.place_image(window.state.page.panels[0].id, png())


def 場面_集中線(window) -> None:
    場面_コマ(window)
    assert window.state.add_focus_lines()


def 場面_流線(window) -> None:
    場面_コマ(window)
    assert window.state.add_flow_lines()


def 場面_斜め(window) -> None:
    window.add_full_page_panel()
    window.state.set_tool(TOOL_SPLIT_SLANT)
    window.view._apply_split(620.0, 877.0)
    # 分割の道具は使ったあとも残る。押下を見るので選択に戻しておく
    window.state.set_tool(TOOL_SELECT)
    window.state.select(window.state.page.panels[0].id)


def 場面_ラフ(window) -> None:
    window.state.place_rough(png())
    window.state.set_rough_rect(ROUGH, "ラフの位置")
    window.state.set_tool(TOOL_ROUGH)


def 場面_トーン(window) -> None:
    場面_画像(window)
    assert window.state.add_tone()
    window.state.set_tool(TOOL_TONE_AREA)


def 場面_コマ追加の道具(window) -> None:
    window.state.set_tool(TOOL_PANEL)


def 場面_フキダシの道具(window) -> None:
    場面_コマ(window)
    window.state.set_tool(TOOL_BALLOON)


# -- つまみの位置。**描く側と同じ関数から取る** ------------------------------


def 選択枠(window) -> Rect:
    return window.state.selected_bounds


def 先端の少し外(window):
    """先端の丸の内側で、**三角形より外**の点。

    先端そのものは三角形の頂点でもあるので、そこを押しただけでは
    「先端の丸が効いている」のか「胴の内側と見なされた」のかが分からない
    （どちらも `TailDrag` になる）。丸の効き目だけを見るために、
    三角形の届かない少し外を押す。
    """
    from manga_layout.ui.canvas import TAIL_TIP_HANDLE_PX

    balloon = window.state.selected_balloon
    tip = balloon.tail.tip
    cx, cy = balloon.rect.center
    length = max(math.hypot(tip[0] - cx, tip[1] - cy), 1e-9)
    # 丸の半分より内側。掴む範囲は画面の px なので表示倍率で割る
    off = TAIL_TIP_HANDLE_PX / window.view.view_scale / 2.0 * 0.8
    return (
        tip[0] + (tip[0] - cx) / length * off,
        tip[1] + (tip[1] - cy) / length * off,
    )


def しっぽの胴(window):
    """三角の重心。**先端でも付け根でもない、見えている形の内側。**"""
    balloon = window.state.selected_balloon
    triangle = tail_triangle(balloon, window.state.balloon_settings)
    return tuple(sum(point[i] for point in triangle) / 3.0 for i in (0, 1))


# 名前 → （場面、つまみの位置、始まってほしいドラッグ）
CASES: dict[str, tuple] = {
    "コマ本体": (場面_コマ, lambda w: 選択枠(w).center, "MoveDrag"),
    "角のつまみ": (
        場面_コマ,
        lambda w: handle_positions(選択枠(w))["nw"],
        "ResizeDrag[nw]",
    ),
    "辺のつまみ": (
        場面_コマ,
        lambda w: handle_positions(選択枠(w))["e"],
        "ResizeDrag[e]",
    ),
    "しっぽの先端": (
        場面_フキダシ,
        lambda w: w.state.selected_balloon.tail.tip,
        "TailDrag",
    ),
    "しっぽの付け根": (
        場面_フキダシ,
        lambda w: tail_root_point(w.state.selected_balloon, w.state.balloon_settings),
        "TailRootDrag",
    ),
    "しっぽの先端の外側": (場面_フキダシ, 先端の少し外, "TailDrag"),
    "しっぽの胴": (場面_フキダシ, しっぽの胴, "TailDrag"),
    "飛びしっぽの円": (
        場面_飛びしっぽ,
        lambda w: tail_bubbles(w.state.selected_balloon, w.state.balloon_settings)[-1][
            :2
        ],
        "TailDrag",
    ),
    "フキダシ本体": (場面_フキダシ, lambda w: 選択枠(w).center, "MoveDrag"),
    "回転のつまみ": (場面_画像, lambda w: w.view._rotate_handle_point(), "RotateDrag"),
    "斜めの境界": (場面_斜め, lambda w: w.view._scene.slant_handle(), "SlantDrag"),
    "集中線の空き": (
        場面_集中線,
        lambda w: w.view._scene.focus_hole_handle(),
        "FocusDrag[focus_hole]",
    ),
    "集中線の中心": (
        場面_集中線,
        lambda w: w.view._scene.focus_center_handle(),
        "FocusDrag[focus_center]",
    ),
    "流線の向き": (
        場面_流線,
        lambda w: w.view._scene.flow_angle_handle(),
        "FlowDrag",
    ),
    "ラフの内側": (場面_ラフ, lambda w: ROUGH.center, "RoughMoveDrag"),
    "ラフのつまみ": (
        場面_ラフ,
        lambda w: handle_positions(ROUGH)["se"],
        "RoughResizeDrag[se]",
    ),
    "トーンの範囲": (場面_トーン, lambda w: 選択枠(w).center, "ToneAreaDrag"),
    "コマ追加の道具": (
        場面_コマ追加の道具,
        lambda w: (300.0, 300.0),
        "CreatePanelDrag",
    ),
    "フキダシの道具": (
        場面_フキダシの道具,
        lambda w: PANEL.center,
        "CreateFloatingDrag[balloon]",
    ),
}


@pytest.mark.parametrize("scale", SCALES, ids=[f"{s:g}倍" for s in SCALES])
@pytest.mark.parametrize("name", list(CASES), ids=list(CASES))
def test_つまみの中心を押せばそのつまみが掴める(make, name, scale):
    """**このファイルの主眼。** 中心で負けるつまみは、事実上掴めない。"""
    setup, where, want = CASES[name]
    window = make(setup)
    at_scale(window.view, scale)
    point = where(window)
    assert point is not None, f"{name}: つまみの位置が取れない"
    assert grabbed(window.view, *point) == want


def test_全部のドラッグに手が届く():
    """**表に載っていないドラッグを作ったら、ここで気づく。**

    新しいつまみを足すときは `Drag` を継承したクラスが1つ増える。上の表に
    足し忘れると「どこからも掴めない」まま気づかれないので、
    クラスの一覧と突き合わせる。
    """
    covered = {want.split("[")[0] for _, _, want in CASES.values()}
    assert covered == {cls.__name__ for cls in Drag.__subclasses__()}


class TestTailOverEdgeHandle:
    """**しっぽの出ている辺の中央のつまみは、しっぽに奪われる。**

    見えているしっぽの内側は、角・辺のつまみより**先に**見る
    （→ `mousePressEvent`）。しっぽは輪郭の上（付け根）から外の先端へ伸びる
    ので、**その向きの辺の中央は必ず三角形の内側に入る。** 大きさにも
    表示倍率にもよらず、しっぽを出している限りそうなる。

    **今そうなっていることの書き留めで、これでよいという意味ではない**
    （直すかどうかは別の判断 → 2026-09-06 の記録）。角のつまみは奪われない
    （→ `test_ui_balloon.py` の `test_角のつまみまでは奪わない`）。
    """

    向き = {
        "下": (0.0, 400.0),
        "右": (400.0, 0.0),
        "上": (0.0, -400.0),
        "左": (-400.0, 0.0),
    }
    奪われる辺 = {"下": "s", "右": "e", "上": "n", "左": "w"}

    @pytest.mark.parametrize(
        "size", [BALLOON, Rect(300.0, 250.0, 70.0, 50.0)], ids=["普通", "小さい"]
    )
    @pytest.mark.parametrize("direction", list(向き), ids=list(向き))
    def test_しっぽの向きの辺だけが奪われる(self, make, direction, size):
        window = make(場面_コマ)
        balloon = window.state.add_balloon(size)
        window.state.select(balloon.id)
        cx, cy = size.center
        dx, dy = self.向き[direction]
        balloon.tail.tip = (cx + dx, cy + dy)
        at_scale(window.view, 1.0)

        positions = handle_positions(選択枠(window))
        got = {name: grabbed(window.view, *point) for name, point in positions.items()}

        奪われた = {
            name
            for name, drag in got.items()
            if drag.startswith(("TailDrag", "TailRootDrag"))
        }
        assert 奪われた == {self.奪われる辺[direction]}
        # 残りの7つは今までどおり大きさを変えるつまみ
        残り = set(positions) - 奪われた
        assert all(got[name] == f"ResizeDrag[{name}]" for name in 残り)


class TestZoomedOut:
    """**縮小すると、つまみどうしが食い合う。**

    掴む範囲は「画面の px ÷ 表示倍率」なので、縮小するほどページの上では
    広くなる。一方でつまみどうしの距離はページの上で決まっているため、
    ある倍率から先は**先に見るほうが後のものを覆い隠す。**

    直せる話ではない（つまみを画面上で同じ大きさに見せる限り必ず起きる）。
    **今そうなっていることを書き留めておく**ためのテスト。
    """

    def test_縮めきると付け根が先端に覆われる(self, make):
        """先端は付け根より**先に**見る（→ `mousePressEvent`）。

        近づいたときに狙って掴んだほうを優先する並びなので、覆うのは
        いつも先端の側になる。
        """
        window = make(場面_フキダシ)
        view = window.view
        at_scale(view, 0.05)  # MIN_VIEW_SCALE
        root = tail_root_point(
            window.state.selected_balloon, window.state.balloon_settings
        )

        assert grabbed(view, *root) == "TailDrag"

    def test_等倍まで戻せば掴める(self, make):
        """**縮小したときだけの話**であることの裏取り。"""
        window = make(場面_フキダシ)
        view = window.view
        at_scale(view, 0.05)
        root = tail_root_point(
            window.state.selected_balloon, window.state.balloon_settings
        )
        assert grabbed(view, *root) == "TailDrag"

        at_scale(view, 1.0)
        assert grabbed(view, *root) == "TailRootDrag"

    def test_角のつまみがコマの内側まで広がる(self, make):
        """5%まで縮めると、角のつまみは**ページの上で 180px 四方**になる。

        コマの角から 30px の所——普通なら本体を掴んで動かせる場所——が、
        大きさ変更のつまみに入る。
        """
        window = make(場面_コマ)
        view = window.view
        at_scale(view, 0.05)
        inside = (PANEL.x + 30.0, PANEL.y + 30.0)

        assert grabbed(view, *inside) == "ResizeDrag[nw]"

        at_scale(view, 1.0)
        assert grabbed(view, *inside) == "MoveDrag"
