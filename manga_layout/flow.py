"""流線の形を作る。**Qt も描画も知らない**（要件定義 6.26）。

受け取るのは `FlowLines` と**コマの外接矩形**だけ。`focus.py`（集中線）と
同じ作りだが、**あちらを import していない**。共有しているのは乱数
（`manga_layout.noise`）だけで、線の形も長さの基準も別に持っている。

**線は棒ではなく紡錘形**（両端が細くなる）。集中線の楔形は「中心で詰まって
黒く潰れる」のを避けるためのものだったが、平行線には潰れる場所が無い。
ここで端を細くするのは、**漫画の流線は端が抜けて見える**ため。

集中線は線を隅より遠くまで伸ばし、はみ出しをコマの形で切り抜くことで
**端の処理を1行も書かずに済ませていた**。流線は端が画面に見えるので、
その手が使えるのは**直交方向だけ**（帯を広めに取る）。線に沿った方向は、
長さを値として持ち、線ごとに揺らす。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .geometry import Rect, normalize_angle
from .model import FLOW_COUNT_MAX, FLOW_COUNT_MIN, FlowLines
from .noise import Noise, new_seed


@dataclass(frozen=True)
class FlowSettings:
    """流線の見た目にかかわる設定。

    `FocusSettings`（集中線）と別にしてある。理由も同じで、**調整したい
    場面が違う**ため。

    ここにあるのは「新しく入れるときの出発点」で、**入れた時点でコマの
    側へ焼き付ける**。あとからここを変えても、既にあるコマの流線は
    変わらない（集中線・斜めの角度と同じ → 要件定義 6.16、6.10）。
    """

    # 線の本数
    count: int = 48
    # 線の太さ。**外接矩形の短辺**に対する割合（集中線と同じ基準）。
    # 集中線の 0.018 よりずっと細い。あちらは中心へ向かって細るので外周で
    # 太さが要るが、こちらは全長が同じ太さなので、同じ値だと塊になる
    width: float = 0.006
    # 線の長さ。**外接矩形の対角線**に対する割合。1.0 で必ずコマを貫く
    length: float = 0.60
    # 向き（度）。0 が水平・右向き。横に流すのがいちばん多い
    angle: float = 0.0


DEFAULT_FLOW_SETTINGS = FlowSettings()

# つまみとメニューで動かせる範囲。
#
# **保存形式として弾く範囲（`FlowLines.from_dict`）とは別もの。**
# あちらは「読んでよい値かどうか」で、こちらは「押し続けたときにどこで
# 止めるか」（集中線と同じ線引き → `focus.py`）
WIDTH_MIN = 0.002
WIDTH_MAX = 0.2
# 長さの下限。これより短いと点にしか見えず、何が起きたのか分からない
LENGTH_MIN = 0.05
LENGTH_MAX = 1.0
# 本数だけは読み込みで弾く範囲と同じ。**数字を2か所に書かない**
COUNT_MIN = FLOW_COUNT_MIN
COUNT_MAX = FLOW_COUNT_MAX

# メニューの「増やす／減らす」1回ぶん
COUNT_STEP = 8
WIDTH_STEP = 0.003
LENGTH_STEP = 0.05

# 端をすぼめる長さ。線1本の長さに対する割合を、両端それぞれに使う。
# 0.5 にすると菱形になって葉に見え、0 にすると定規で引いた棒になる
TAPER = 0.25

# 直交方向の位置のばらつき。線と線の間隔に対する割合。
# 0 にすると等間隔の縞模様になり、流れに見えない
ACROSS_JITTER = 0.40
# 長さのばらつき。**短くする側にだけ振る。** 長くすると `length` を超える
LENGTH_JITTER = 0.35
# 線に沿った位置のばらつき。**その線が使わずに余らせた長さ**に対する割合。
# 端が一直線に並ぶと、切った紙の縁に見える。余りを基準にしているので、
# `length` が 1.0（＝余りが無い）のときは自動的にずれ幅も 0 になり、
# 「1.0 なら必ず貫く」が保たれる
ALONG_JITTER = 0.25
# 太さのばらつき。**細くする側にだけ振る。** 太くすると隣と重なって塊になる
WIDTH_JITTER = 0.35

# 向きのつまみを出す距離。短辺に対する割合
HANDLE_GAP = 0.35


def default_flow(settings: FlowSettings = DEFAULT_FLOW_SETTINGS) -> FlowLines:
    """新しく入れる流線。**向きは水平（0 度）。**

    置いた瞬間から絵に合わせて回すものなので、出発点は迷わない場所に
    置く（集中線の「中心はコマの真ん中」と同じ → 要件定義 6.26）。
    """
    return FlowLines(
        angle=settings.angle,
        count=settings.count,
        width=settings.width,
        length=settings.length,
        seed=new_seed(),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def short_side(bounds: Rect) -> float:
    """太さの基準。**短辺**（集中線と同じ）。"""
    return min(bounds.w, bounds.h)


def diagonal(bounds: Rect) -> float:
    """長さと帯の基準。**対角線。**

    短辺基準だと、`length` を 1.0 にしても横長のコマを半分しか走らない。
    長辺基準だと、斜めに流したときに貫かない。**矩形の中でいちばん長い
    弦は対角線**なので、ここを基準にすると 1.0 が「向きによらず必ず
    貫く」と一致する（要件定義 6.26）。
    """
    return math.hypot(bounds.w, bounds.h)


def direction(angle: float) -> tuple[float, float]:
    """線が走る向きの単位ベクトル。"""
    radians = math.radians(angle)
    return (math.cos(radians), math.sin(radians))


def band_width(bounds: Rect, angle: float) -> float:
    """線を並べる帯の幅。**矩形を向きに直交する方向へ潰した長さ。**

    対角線ぶん取れば必ず足りるが、それだと横長のコマに水平の線を引いた
    ときに半分近くが外へ落ち、**本数を指定しても見える数がそれより
    少なくなる**。ちょうどの幅で並べれば、指定した本数がそのまま見える。
    """
    dx, dy = direction(angle)
    # 直交する向き (-dy, dx) へ矩形を投影した長さ
    return abs(bounds.w * dy) + abs(bounds.h * dx)


def handle_point(flow: FlowLines, bounds: Rect) -> tuple[float, float]:
    """向きを変えるつまみ（丸）の位置。

    コマの外接矩形の中心から `angle` の向きへ、短辺の一定割合だけ離す。
    流線は中心を持たないので、ここは常に外接矩形の中心が起点になる。
    """
    cx, cy = bounds.center
    dx, dy = direction(flow.angle)
    gap = short_side(bounds) * HANDLE_GAP
    return (cx + dx * gap, cy + dy * gap)


def angle_at(bounds: Rect, x: float, y: float) -> float:
    """マウスの位置を向きに直す。**距離は使わない。**

    つまみは決まった距離に出るので、掴んだ点がそこから離れていても
    向きだけを見る。畳み方は画像の回転と同じ（→ `normalize_angle`）。
    """
    cx, cy = bounds.center
    if x == cx and y == cy:
        # ちょうど中心。向きが決められないので既定（水平）に落とす
        return DEFAULT_FLOW_SETTINGS.angle
    return normalize_angle(math.degrees(math.atan2(y - cy, x - cx)))


def stepped_count(count: int, steps: int) -> int:
    """本数を増減する。範囲の端で止める。"""
    return int(_clamp(count + steps * COUNT_STEP, COUNT_MIN, COUNT_MAX))


def stepped_width(width: float, steps: int) -> float:
    """太さを増減する。範囲の端で止める。"""
    return _clamp(width + steps * WIDTH_STEP, WIDTH_MIN, WIDTH_MAX)


def stepped_length(length: float, steps: int) -> float:
    """長さを増減する。範囲の端で止める。"""
    return _clamp(length + steps * LENGTH_STEP, LENGTH_MIN, LENGTH_MAX)


def flow_polygons(
    flow: FlowLines, bounds: Rect
) -> list[tuple[tuple[float, float], ...]]:
    """線1本ぶんの多角形を、本数ぶん返す。

    1本は6つの頂点を持つ。両端が尖り、真ん中が幅一定の**紡錘形**で、
    頂点の順は端から時計回りにひと回りする。塗り潰して描くので向きは
    問わない。

    同じ `flow` からは**必ず同じ列**が出る。画面・サムネイル・PNG
    書き出しの3つが同じ形になるのは、ここが決定的だからで、乱数の並びを
    自前で持っている理由でもある（→ `manga_layout.noise`）。
    """
    size = short_side(bounds)
    if size <= 0.0 or flow.count <= 0:
        return []

    diag = diagonal(bounds)
    cx, cy = bounds.center
    # 線が走る向きと、それに直交する向き
    dx, dy = direction(flow.angle)
    nx, ny = -dy, dx

    band = band_width(bounds, flow.angle)
    step = band / flow.count
    half_width = flow.width * size / 2.0
    full_length = flow.length * diag
    noise = Noise(flow.seed)

    lines = []
    for i in range(flow.count):
        # 直交方向の位置。帯の端から等間隔に並べ、間隔ぶんだけ揺らす
        across = -band / 2.0 + (i + 0.5) * step + noise.signed() * step * ACROSS_JITTER
        length = full_length * (1.0 - LENGTH_JITTER * noise.unit())
        # 線に沿った位置。**その線が余らせた長さの中だけで**ずらすので、
        # コマを貫く長さの線は動かない
        along = noise.signed() * max(0.0, diag - length) * ALONG_JITTER
        half = half_width * (1.0 - WIDTH_JITTER * noise.unit())

        mx = cx + nx * across + dx * along
        my = cy + ny * across + dy * along
        head = (mx + dx * length / 2.0, my + dy * length / 2.0)
        tail = (mx - dx * length / 2.0, my - dy * length / 2.0)
        # すぼめ始める点。ここから端までが細くなる
        taper = length * TAPER
        hx, hy = head[0] - dx * taper, head[1] - dy * taper
        tx, ty = tail[0] + dx * taper, tail[1] + dy * taper
        lines.append(
            (
                head,
                (hx + nx * half, hy + ny * half),
                (tx + nx * half, ty + ny * half),
                tail,
                (tx - nx * half, ty - ny * half),
                (hx - nx * half, hy - ny * half),
            )
        )
    return lines
