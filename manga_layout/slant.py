"""斜め割りコマの計算。

`layout.py` から独立させてある。斜めの計算はここだけで完結していて、
コマの矩形分割・吸着・吹き出しの形など他の計算を一切呼ばない
（呼ばれる側にもなっていない）ため、切り離しても支障がない。
"""

from __future__ import annotations

import dataclasses
import math

from .geometry import EPS, Polygon, Rect
from .layout import DEFAULT_SETTINGS, LayoutSettings
from .model import (
    SLANT_DIRECTIONS,
    SLANT_LEFT,
    SLANT_RIGHT,
    Page,
    Panel,
    Project,
    SlantPair,
)

# --------------------------------------------------------------------------
# 斜めの縦割り
# --------------------------------------------------------------------------

def slant_offset(height: float, angle: float) -> float:
    """斜めの境界が、上端と下端でどれだけ横にずれるか（片側ぶん、px）。

    角度が固定なので、ずれ幅は高さに比例する。コマを縦に伸ばすと
    自動で斜めも寝る、という追従はこの式ひとつで効いている。
    """
    return height * math.tan(math.radians(angle)) / 2.0


def slant_gap(gutter: float, angle: float) -> float:
    """隙間を保つために境界の左右へずらす量（片側ぶん、px）。

    真横に `gutter/2` ずらすだけだと、傾けたぶん**見た目の隙間が細くなる**
    （12° で約 2% ）。角度で割り戻し、垂直に測った隙間が `gutter` に
    なるようにしている。
    """
    return gutter / 2.0 / math.cos(math.radians(angle))


def slant_narrowest(rect: Rect, ratio: float, angle: float, gutter: float) -> float:
    """斜めに割ったとき、細いほうのコマの**一番細い箇所**の幅（px）。

    斜めのコマは上端と下端で幅が違う。狭いほうの端がここで返る値になり、
    分割してよいか・これ以上縮めてよいかの判定はすべてこの1本で足りる。
    """
    return (
        min(ratio, 1.0 - ratio) * rect.w
        - slant_offset(rect.h, angle)
        - slant_gap(gutter, angle)
    )


def slant_polygons(
    rect: Rect,
    ratio: float,
    angle: float,
    direction: str,
    gutter: float,
) -> tuple[Polygon, Polygon]:
    """外側の矩形から、斜めに割った左右2枚の形を作る。

    `ratio` は左端から何割の位置で割るか。**割合で持つのが要**で、
    絶対座標だと外側を縮めたときに境界が矩形の外へ出てしまう。

    2枚をここで同時に作るので、隙間の取り方が左右で食い違わない。
    """
    if direction not in SLANT_DIRECTIONS:
        raise ValueError(f"斜めの向きは / か \\ です（{direction!r}）")

    center = rect.x + rect.w * ratio
    offset = slant_offset(rect.h, angle)
    gap = slant_gap(gutter, angle)
    top, bottom = (center + offset, center - offset)
    if direction == SLANT_LEFT:
        top, bottom = bottom, top

    left = Polygon(
        (
            (rect.x, rect.y),
            (top - gap, rect.y),
            (bottom - gap, rect.bottom),
            (rect.x, rect.bottom),
        )
    )
    right = Polygon(
        (
            (top + gap, rect.y),
            (rect.right, rect.y),
            (rect.right, rect.bottom),
            (bottom + gap, rect.bottom),
        )
    )
    return left, right


def slant_min_width(
    height: float, ratio: float, angle: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> float:
    """その高さで斜めに割れる、外側の矩形の最小の幅（px）。

    リサイズを止める位置に使う。高さを伸ばすほど斜めの振れ幅が増えるので、
    必要な幅も一緒に増える。
    """
    return (
        slant_offset(height, angle)
        + slant_gap(settings.gutter, angle)
        + settings.min_panel_size
    ) / min(ratio, 1.0 - ratio)


def slant_max_height(
    width: float, ratio: float, angle: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> float:
    """その幅で斜めに割れる、外側の矩形の最大の高さ（px）。

    `slant_min_width` の裏返し。上下のつまみを引いたときの止め位置に使う。
    """
    room = (
        min(ratio, 1.0 - ratio) * width
        - slant_gap(settings.gutter, angle)
        - settings.min_panel_size
    )
    return max(room * 2.0 / math.tan(math.radians(angle)), 0.0)


def check_slant(
    rect: Rect, ratio: float, angle: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> None:
    """斜めに割れるかを確かめる。割れないなら理由を添えて断る。

    黙って角度を寝かせたり位置をずらしたりせず、断る。手が滑ったのか
    そういう仕様なのかが分からないほうが困る（既存の分割と同じ方針）。
    """
    if not 0.0 < ratio < 1.0:
        raise ValueError("コマの内側で位置を指定してください")
    narrowest = slant_narrowest(rect, ratio, angle, settings.gutter)
    # ちょうど限界に押し戻した値をここへ通すため、`EPS` ぶん緩める。
    # `clamp_slant_ratio` / `clamp_slant_rect` は最小幅ぴったりの値を作るので、
    # 厳密に比べると浮動小数の丸めで弾かれ、限界までドラッグした瞬間に落ちる
    if narrowest >= settings.min_panel_size - EPS:
        return
    raise ValueError(
        f"そこで斜めに割ると幅 {max(narrowest, 0.0):.0f}px のコマができます"
        f"（最小 {settings.min_panel_size:.0f}px）。"
        f"高さ {rect.h:.0f}px なら幅 "
        f"{slant_min_width(rect.h, ratio, angle, settings):.0f}px 以上必要です"
    )


def slant_boundary_x(
    rect: Rect, ratio: float, angle: float, direction: str, y: float
) -> float:
    """高さ `y` のところで、斜めの境界が通る x 座標。

    画像をどちらのコマへ振り分けるかの判定に使う。中心の高さで境界の
    位置を出して左右を見るので、傾きがどれだけ強くても取り違えない。
    """
    center = rect.x + rect.w * ratio
    offset = slant_offset(rect.h, angle)
    top, bottom = (center + offset, center - offset)
    if direction == SLANT_LEFT:
        top, bottom = bottom, top
    if rect.h <= 0.0:
        return top
    down = min(max((y - rect.y) / rect.h, 0.0), 1.0)
    return top + (bottom - top) * down


def split_panel_slant(
    project: Project,
    page: Page,
    panel_id: str,
    *,
    position: float,
    direction: str = SLANT_RIGHT,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> tuple[Panel, Panel]:
    """コマを斜めの縦線で2つに割り、`SlantPair` として結び付ける。

    `position` は割る位置（px）。ここで割合に直して覚えるので、あとから
    外側を拡大縮小しても境界が付いてくる。角度は `settings.slant_angle`
    を使い、**その値をペアに焼き付ける**。既定の角度を変えても、
    すでに作ったコマは変形しない。

    横の分割（`split_panel`）と同じく、元のコマは左側として id ごと残る。
    紐づいた吹き出しの追随先が変わらない。
    """
    if direction not in SLANT_DIRECTIONS:
        raise ValueError(f"斜めの向きは / か \\ です（{direction!r}）")

    panel = page.panel(panel_id)
    if page.slant_pair_of(panel_id) is not None:
        raise ValueError("斜めに割ったコマは、これ以上分割できません")
    rect = panel.shape.as_rect()
    if rect is None:
        raise ValueError("斜めのコマは分割できません")

    angle = settings.slant_angle
    ratio = (position - rect.x) / rect.w if rect.w > 0.0 else -1.0
    check_slant(rect, ratio, angle, settings)

    left_shape, right_shape = slant_polygons(
        rect, ratio, angle, direction, settings.gutter
    )
    panel.shape = left_shape

    new_panel = project.add_panel(page, rect)
    new_panel.shape = right_shape
    new_panel.border = dataclasses.replace(panel.border)
    # 並びを元のコマの直後にする（`split_panel` と同じ理由）
    page.panels.remove(new_panel)
    page.panels.insert(page.panels.index(panel) + 1, new_panel)

    for image in list(panel.children):
        cx, cy = image.rect.center
        if cx > slant_boundary_x(rect, ratio, angle, direction, cy):
            panel.children.remove(image)
            new_panel.children.append(image)

    page.slant_pairs.append(
        SlantPair(
            left_id=panel.id,
            right_id=new_panel.id,
            ratio=ratio,
            angle=angle,
            direction=direction,
        )
    )
    return panel, new_panel


def rebuild_slant_pair(
    page: Page,
    pair: SlantPair,
    rect: Rect,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> None:
    """外側の矩形からペアの2枚を作り直す。

    移動もリサイズも向きの反転も、最後はすべてここを通る。形を作る
    経路が1本しかないので、操作ごとに隙間や傾きがずれることがない。
    """
    check_slant(rect, pair.ratio, pair.angle, settings)
    left, right = slant_polygons(
        rect, pair.ratio, pair.angle, pair.direction, settings.gutter
    )
    page.panel(pair.left_id).shape = left
    page.panel(pair.right_id).shape = right


def set_slant_pair_rect(
    page: Page,
    pair: SlantPair,
    rect: Rect,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> None:
    """ペアの外側の矩形を差し替える（リサイズ）。

    中の画像は動かさない。矩形のコマのリサイズと同じ扱いにしてある。
    """
    rebuild_slant_pair(page, pair, rect.normalized(), settings)


def slant_handle_point(rect: Rect, ratio: float) -> tuple[float, float]:
    """境界をつまむ位置。境界線の中点。

    上下の中央では、傾きに関わらず境界がちょうど分割位置を通る。
    向きを反転しても掴み所が動かないので、目が迷わない。
    """
    return (rect.x + rect.w * ratio, rect.y + rect.h / 2.0)


def slant_ratio_at(rect: Rect, x: float) -> float:
    """ページ座標の x を、外側の矩形に対する割合に直す。"""
    return (x - rect.x) / rect.w if rect.w > 0.0 else 0.5


def slant_ratio_bounds(
    rect: Rect, angle: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> tuple[float, float]:
    """境界をずらせる割合の範囲。細いほうが最小幅を割らない限界。

    左右で対称なので、片側の限界 `k` から `(k, 1-k)` になる。
    そもそも割れない大きさなら幅ゼロの範囲（中央のみ）を返す。
    """
    if rect.w <= 0.0:
        return (0.5, 0.5)
    k = (
        slant_offset(rect.h, angle)
        + slant_gap(settings.gutter, angle)
        + settings.min_panel_size
    ) / rect.w
    return (0.5, 0.5) if k >= 0.5 else (k, 1.0 - k)


def clamp_slant_ratio(
    rect: Rect, angle: float, ratio: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> float:
    """割合を、割れる範囲まで押し戻す。"""
    low, high = slant_ratio_bounds(rect, angle, settings)
    return min(max(ratio, low), high)


def slide_slant_pair(
    page: Page,
    pair: SlantPair,
    ratio: float,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> SlantPair:
    """境界を左右にずらす。差し替えた `SlantPair` を返す。

    外側の矩形は変わらないので、隣のコマとの位置関係は動かない。
    範囲外を渡しても断らずに押し戻す（リサイズが最小の大きさで
    止まるのと同じ感触にするため）。

    **中の画像は動かさないし、所属も変えない。** 境界を絵の向こう側まで
    送ると、その絵はコマの外に出て切り抜かれ、見えなくなる。これは
    「窓の大きさを変えても中身は付いて回らない」という既存の方針
    （要件定義 4章）と同じで、絵が勝手に隣のコマへ飛ぶより読みやすい。
    """
    rect = page.slant_bounds(pair)
    moved = dataclasses.replace(
        pair, ratio=clamp_slant_ratio(rect, pair.angle, ratio, settings)
    )
    rebuild_slant_pair(page, moved, rect, settings)
    page.slant_pairs[page.slant_pairs.index(pair)] = moved
    return moved


def flip_slant_pair(
    page: Page, pair: SlantPair, settings: LayoutSettings = DEFAULT_SETTINGS
) -> SlantPair:
    """斜めの向きを反転する。利用者がつつける唯一のつまみ。

    差し替えた `SlantPair` を返す。外側の矩形は変わらないので、
    隣のコマとの位置関係は動かない。
    """
    rect = page.slant_bounds(pair)
    flipped = pair.flipped()
    rebuild_slant_pair(page, flipped, rect, settings)
    page.slant_pairs[page.slant_pairs.index(pair)] = flipped
    return flipped


def clamp_slant_rect(
    pair: SlantPair, rect: Rect, settings: LayoutSettings = DEFAULT_SETTINGS
) -> Rect:
    """リサイズ中の外側の矩形を、割れる大きさまで押し戻す。

    細いほうのコマが最小幅を割る手前で止める。既存のリサイズが最小の
    大きさで止まるのと同じ感触にするため、断らずに止める。

    幅と高さは独立していない。高さを伸ばすほど斜めの振れ幅が増え、
    必要な幅も増えるので、掴んだ辺の側だけを押し戻す。
    """
    r = rect.normalized()
    min_w = slant_min_width(r.h, pair.ratio, pair.angle, settings)
    if r.w >= min_w:
        return r
    max_h = slant_max_height(r.w, pair.ratio, pair.angle, settings)
    return Rect(r.x, r.y, r.w, min(r.h, max_h))
