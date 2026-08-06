"""集中線の形を作る。**Qt も描画も知らない**（要件定義 6.16）。

受け取るのは `FocusLines` と**コマの外接矩形**だけ。どのコマに付いて
いるか、どう切り抜かれるかは呼ぶ側の話にしてある。`vertical.py` と同じく
Qt から切り離してあるので、形が正しいかどうかを座標のまま検証できる
（テストは offscreen で動く → 要件定義 6.11）。

**線は棒ではなく楔形**（外周が太く、中心に向かって細くなる三角形）。
幅一定の棒を放射状に並べると中心付近で線が詰まって黒く潰れ、集中線に
見えない。三角形の頂点は**内側の空きの縁**に置く。中心そのものに置くと
空きの中へ食い込む。

長さはすべて**コマの外接矩形の短辺に対する割合**で受け取る。コマの
大小によらず同じ見え方になり、コマを縮めたときに中心が外へ飛び出す
こともない（→ 要件定義 5章の `FocusLines`）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Rect
from .model import FOCUS_COUNT_MAX, FOCUS_COUNT_MIN, FocusLines
from .noise import Noise, new_seed


@dataclass(frozen=True)
class FocusSettings:
    """集中線の見た目にかかわる設定。

    コマ割りの設定（`LayoutSettings`）や吹き出しの設定
    （`BalloonSettings`）とは別にしてある。理由も同じで、**調整したい
    場面がまったく違う**ため。

    ここにあるのは「新しく入れるときの出発点」で、**入れた時点でコマの
    側へ焼き付ける**。あとからここを変えても、既にあるコマの集中線は
    変わらない（斜めの角度と同じ → 要件定義 6.10）。

    既定値は数では決められない性質のものなので、実際に描いて決める
    （ふわふわ_フキダシと同じ → 要件定義 6.13）。
    """

    # 線の本数
    count: int = 72
    # 外周での線の太さ。短辺に対する割合
    width: float = 0.018
    # 内側の空きの半径。短辺に対する割合
    hole: float = 0.15


DEFAULT_FOCUS_SETTINGS = FocusSettings()

# つまみとメニューで動かせる範囲。
#
# **保存形式として弾く範囲（`FocusLines.from_dict`）とは別もの。**
# あちらは「読んでよい値かどうか」で、こちらは「掴んで動かしたときに
# どこで止めるか」。空きを 0.8 より大きくすると線が1本も見えなくなり、
# 何が起きたのか画面から分からなくなる
HOLE_MIN = 0.0
HOLE_MAX = 0.8
WIDTH_MIN = 0.002
WIDTH_MAX = 0.2
# 本数だけは読み込みで弾く範囲と同じ。**数字を2か所に書かない**
COUNT_MIN = FOCUS_COUNT_MIN
COUNT_MAX = FOCUS_COUNT_MAX

# メニューの「増やす／減らす」1回ぶん
COUNT_STEP = 12
WIDTH_STEP = 0.004

# 線を外側へ伸ばす余裕。外接矩形の隅までの距離ちょうどでも足りるが、
# 浮動小数の丸めで隅が1画素だけ空くことがある
OUTER_MARGIN = 1.02

# 角度のばらつき。線と線の間隔に対する割合。
# 0 にすると等間隔の車輪になり、定規で引いた線に見える
ANGLE_JITTER = 0.35
# 太さのばらつき。**細くする側にだけ振る。** 太くする側へ振ると、
# 隣と重なって塊になる
WIDTH_JITTER = 0.45
# 内側の空きのばらつき。**外へだけ振る。** 内へ振ると、空けたはずの
# 場所に線の先が入る
HOLE_JITTER = 0.30

def default_focus(settings: FocusSettings = DEFAULT_FOCUS_SETTINGS) -> FocusLines:
    """新しく入れる集中線。**中心はコマの真ん中。**

    置いた瞬間から絵に合わせて動かすものなので、出発点は迷わない場所に
    置く。値は設定から取ってコマの側へ焼き付ける（→ `FocusSettings`）。
    """
    return FocusLines(
        center=(0.5, 0.5),
        hole=settings.hole,
        count=settings.count,
        width=settings.width,
        seed=new_seed(),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def short_side(bounds: Rect) -> float:
    """長さの基準。**短辺。**

    長辺を基準にすると、細長いコマで空きがコマの外まで広がる。
    """
    return min(bounds.w, bounds.h)


def center_point(focus: FocusLines, bounds: Rect) -> tuple[float, float]:
    """中心のページ座標。`center` は外接矩形に対する割合で持っている。"""
    return (
        bounds.x + focus.center[0] * bounds.w,
        bounds.y + focus.center[1] * bounds.h,
    )


def center_at(bounds: Rect, x: float, y: float) -> tuple[float, float]:
    """マウスの位置を中心の割合に直す。**範囲は制限しない。**

    画面の外から集中させるために中心をコマの外へ置くことがあり、その
    場合も線は正しく作れる（→ 要件定義 5章）。
    """
    if bounds.w <= 0.0 or bounds.h <= 0.0:
        return (0.5, 0.5)
    return ((x - bounds.x) / bounds.w, (y - bounds.y) / bounds.h)


def hole_point(focus: FocusLines, bounds: Rect) -> tuple[float, float]:
    """空きの大きさを掴む印の位置。**中心の右**に置く。

    左右にしか動かさないので、上下のどちらかに置く理由が無い。中心の
    つまみと重ならない場所で、かつ空きの縁が目で追える位置にする。
    """
    cx, cy = center_point(focus, bounds)
    return (cx + focus.hole * short_side(bounds), cy)


def hole_at(focus: FocusLines, bounds: Rect, x: float) -> float:
    """つまみの x を空きの割合に直す。

    **横だけ見る。** 印は中心の右にあり左右にしか動かないので、縦を
    拾うと掴んだ瞬間に値が飛ぶ（しっぽの付け根が縦だけ見るのと同じ
    → 要件定義 6.4）。
    """
    size = short_side(bounds)
    if size <= 0.0:
        return focus.hole
    cx, _ = center_point(focus, bounds)
    return _clamp((x - cx) / size, HOLE_MIN, HOLE_MAX)


def stepped_count(count: int, steps: int) -> int:
    """本数を増減する。範囲の端で止める。"""
    return int(_clamp(count + steps * COUNT_STEP, COUNT_MIN, COUNT_MAX))


def stepped_width(width: float, steps: int) -> float:
    """太さを増減する。範囲の端で止める。"""
    return _clamp(width + steps * WIDTH_STEP, WIDTH_MIN, WIDTH_MAX)


def outer_radius(center: tuple[float, float], bounds: Rect) -> float:
    """線を伸ばす長さ。**外接矩形の隅までの、いちばん遠い距離。**

    矩形の中で中心からいちばん遠い点は必ず隅なので、ここまで伸ばせば
    どの向きの線もコマの外へ出る。**外へ出た分はコマの形で切り抜かれる**
    ので、端の処理を書かずに済む。斜めのコマでもそのまま効く
    （要件定義 6.16）。
    """
    cx, cy = center
    corners = (
        (bounds.x, bounds.y),
        (bounds.right, bounds.y),
        (bounds.right, bounds.bottom),
        (bounds.x, bounds.bottom),
    )
    return max(math.hypot(x - cx, y - cy) for x, y in corners) * OUTER_MARGIN


def focus_triangles(
    focus: FocusLines, bounds: Rect
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """線1本ぶんの三角形を、本数ぶん返す。

    頂点の順は（内側の先、外周の片側、外周のもう片側）。塗り潰して
    描くので向きは問わない。

    同じ `focus` からは**必ず同じ列**が出る。画面・サムネイル・PNG
    書き出しの3つが同じ形になるのは、ここが決定的だからで、乱数の並びを
    自前で持っている理由でもある（→ `manga_layout.noise`）。
    """
    size = short_side(bounds)
    if size <= 0.0 or focus.count <= 0:
        return []

    center = center_point(focus, bounds)
    cx, cy = center
    outer = outer_radius(center, bounds)
    hole = focus.hole * size
    half = focus.width * size / 2.0
    noise = Noise(focus.seed)
    step = math.tau / focus.count

    lines = []
    for i in range(focus.count):
        angle = i * step + noise.signed() * step * ANGLE_JITTER
        width = half * (1.0 - WIDTH_JITTER * noise.unit())
        start = hole * (1.0 + HOLE_JITTER * noise.unit())
        # 空きが外周に届くほど大きいと三角形が裏返る。手前で止める
        start = min(start, outer * 0.9)

        dx, dy = math.cos(angle), math.sin(angle)
        # 進む向きに直交する向き。外周側の底辺をここへ振り分ける
        nx, ny = -dy, dx
        bx, by = cx + dx * outer, cy + dy * outer
        lines.append(
            (
                (cx + dx * start, cy + dy * start),
                (bx + nx * width, by + ny * width),
                (bx - nx * width, by - ny * width),
            )
        )
    return lines
