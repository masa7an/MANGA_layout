"""ページ座標の図形。単位はピクセル（px、浮動小数）。

要件定義 3章のとおり、ページ左上を原点とする絶対座標だけを扱う。
表示倍率をあとから変えても、ここの値は変わらない。画面上の位置への
換算はこの層では行わない（表示側の責務）。

`Rect` も `Polygon` も**書き換え不可**にしてある。
1つの矩形を複数のオブジェクトが共有してしまうと、片方を動かしたつもりが
両方動く、という追いにくい不具合になるため。移動や変形は新しい値を返す。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from . import validation as v
from .errors import ProjectFormatError

# 座標の比較に使う許容誤差（px）。1px の 100 万分の 1 で、浮動小数の
# 丸め誤差だけを吸収し、意味のある差は潰さない大きさ。
EPS = 1e-6


@dataclass(frozen=True)
class Size:
    """幅と高さ（px）。ページ寸法に使う。"""

    w: float
    h: float

    def scaled(self, factor: float) -> Size:
        return Size(self.w * factor, self.h * factor)

    def to_dict(self) -> dict[str, float]:
        return {"w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Size:
        d = v.req_mapping(data, where)
        return cls(w=v.positive(d, "w", where), h=v.positive(d, "h", where))


@dataclass(frozen=True)
class Rect:
    """左上の座標と大きさ（px）。画像・吹き出し・テキストの配置に使う。"""

    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def normalized(self) -> Rect:
        """幅や高さが負なら、左上を付け替えて正の値にする。

        ドラッグで矩形を描くとき、右下から左上へ引くと負の幅になる。
        """
        x, w = (self.x, self.w) if self.w >= 0 else (self.x + self.w, -self.w)
        y, h = (self.y, self.h) if self.h >= 0 else (self.y + self.h, -self.h)
        return Rect(x, y, w, h)

    def translated(self, dx: float, dy: float) -> Rect:
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def scaled(self, factor: float) -> Rect:
        """原点を軸に拡大縮小する。単位の換算（mm → px）に使う。"""
        return Rect(self.x * factor, self.y * factor, self.w * factor, self.h * factor)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def intersects(self, other: Rect) -> bool:
        return not (
            other.x > self.right
            or other.right < self.x
            or other.y > self.bottom
            or other.bottom < self.y
        )

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Rect:
        d = v.req_mapping(data, where)
        return cls(
            x=v.number(d, "x", where),
            y=v.number(d, "y", where),
            w=v.number(d, "w", where),
            h=v.number(d, "h", where),
        )


def normalize_angle(degrees: float) -> float:
    """角度を `-180`〜`180` に畳む。

    回転を足し続けると値が際限なく増える。保存する前にここを通しておくと、
    project.json に 3600 のような読んでも分からない数字が残らない。
    """
    angle = math.fmod(degrees, 360.0)
    if angle > 180.0:
        angle -= 360.0
    elif angle <= -180.0:
        angle += 360.0
    return angle


def rotate_point(
    x: float, y: float, cx: float, cy: float, degrees: float
) -> tuple[float, float]:
    """点 (x, y) を (cx, cy) のまわりに `degrees` 度回した位置。

    画面の y は下向きなので、**正の角度は時計回り**になる。`QPainter.rotate`
    と同じ向きで、描画と当たり判定で符号を合わせるためにこの向きに揃えてある。

    0 度のときは計算せずにそのまま返す。回転を使っていない作品で
    浮動小数の丸めが入らないようにするため（→ 要件定義 6.3）。
    """
    if degrees == 0.0:
        return (x, y)
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)
    dx, dy = x - cx, y - cy
    return (cx + dx * cos - dy * sin, cy + dx * sin + dy * cos)


def rotated_corners(rect: Rect, degrees: float) -> tuple[tuple[float, float], ...]:
    """矩形を中心まわりに回した4隅。左上から時計回り。"""
    cx, cy = rect.center
    return tuple(
        rotate_point(x, y, cx, cy, degrees)
        for x, y in (
            (rect.x, rect.y),
            (rect.right, rect.y),
            (rect.right, rect.bottom),
            (rect.x, rect.bottom),
        )
    )


def rotated_bounds(rect: Rect, degrees: float) -> Rect:
    """回した矩形を囲む、傾いていない矩形（外接矩形）。

    「コマにフィット」で、傾いた絵がコマを覆うかどうかを見るのに使う。
    """
    if degrees == 0.0:
        return rect
    corners = rotated_corners(rect, degrees)
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def unrotate_point(
    x: float, y: float, rect: Rect, degrees: float
) -> tuple[float, float]:
    """マウスの位置を、`rect` が傾いていなかったときの位置に戻す。

    **回転を持ち込む3か所の境目のうちの1つ**（→ 要件定義 6.3）。ここを
    通してから今までの矩形の判定に渡せば、当たり判定・つまみ・リサイズを
    書き換えずに済む。
    """
    cx, cy = rect.center
    return rotate_point(x, y, cx, cy, -degrees)


def rotated_rect_contains(rect: Rect, x: float, y: float, degrees: float) -> bool:
    """点が、回した矩形の内側にあるか。"""
    lx, ly = unrotate_point(x, y, rect, degrees)
    return rect.contains(lx, ly)


def _on_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> bool:
    """点 (px, py) が線分 (x1,y1)-(x2,y2) の上に乗っているか。

    外積で線の上かを見てから、内積で線分の範囲に収まっているかを見る。
    判定を `EPS` の幅で緩めるのは、頂点を計算で求めた座標と比べたときに
    浮動小数の丸めで外れるのを防ぐため。
    """
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length <= EPS:
        return math.hypot(px - x1, py - y1) <= EPS
    if abs(dx * (py - y1) - dy * (px - x1)) / length > EPS:
        return False
    dot = (px - x1) * dx + (py - y1) * dy
    return -EPS <= dot <= length * length + EPS


@dataclass(frozen=True)
class Polygon:
    """コマの形。頂点は時計回りに並べる。

    要件定義 4章のとおり、MVP では操作を軸並行の矩形に限定するが、
    **保存形式は最初から頂点リストにしてある**。
    斜めコマを足すときに、保存形式・分割処理・クリッピングを書き直さずに済む。
    """

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError(f"多角形には3点以上必要です（{len(self.points)}点）")

    def __len__(self) -> int:
        return len(self.points)

    @classmethod
    def from_rect(cls, rect: Rect) -> Polygon:
        """矩形から作る。MVP のコマ追加はすべてこの経路を通る。"""
        r = rect.normalized()
        return cls(
            (
                (r.x, r.y),
                (r.right, r.y),
                (r.right, r.bottom),
                (r.x, r.bottom),
            )
        )

    def scaled(self, factor: float) -> Polygon:
        """原点を軸に拡大縮小する。単位の換算（mm → px）に使う。"""
        return Polygon(tuple((x * factor, y * factor) for x, y in self.points))

    def bounds(self) -> Rect:
        """外接する矩形。当たり判定の粗い絞り込みや、コマの寸法表示に使う。"""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def translated(self, dx: float, dy: float) -> Polygon:
        return Polygon(tuple((x + dx, y + dy) for x, y in self.points))

    def fitted_to(self, bounds: Rect) -> Polygon:
        """外接矩形が `bounds` になるよう、頂点を比例で移し替える。

        軸並行の長方形に使うと結果は `bounds` そのものになるので、
        矩形のコマのリサイズは今までと変わらない。斜めのコマは
        **傾きを保ったまま**伸縮する。

        矩形で上書きしてしまうと、大きさを変えた瞬間に斜めが消える。
        頂点を移す形にしておくと、その壊れ方が構造的に起きない。
        """
        old = self.bounds()
        target = bounds.normalized()
        # 潰れた図形は比を取れない。全頂点を新しい辺の上へ寄せる
        sx = target.w / old.w if old.w > EPS else 0.0
        sy = target.h / old.h if old.h > EPS else 0.0
        return Polygon(
            tuple(
                (target.x + (x - old.x) * sx, target.y + (y - old.y) * sy)
                for x, y in self.points
            )
        )

    def contains(self, x: float, y: float) -> bool:
        """点が内側にあるか。辺の上も内側として扱う。

        外接矩形では代用できない。斜めのコマは隣同士で外接矩形が重なるため、
        斜めに削られた三角形の部分を押すと隣のコマが選ばれてしまう。

        辺の上を含めるのは `Rect.contains` に合わせるため。枠線をちょうど
        狙って押したときに何も選ばれないと、掴めない縁があるように感じる。
        """
        n = len(self.points)
        inside = False
        for i in range(n):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % n]
            if _on_segment(x, y, x1, y1, x2, y2):
                return True
            # 点から右へ半直線を伸ばし、辺と交わった回数を数える。奇数なら内側。
            # 「上端は含み下端は含まない」で辺を数えると、頂点をちょうど通る
            # ときに 1 本の辺を二重に数えることがない
            if (y1 > y) != (y2 > y):
                cross_x = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
                if x < cross_x:
                    inside = not inside
        return inside

    def is_axis_aligned_rect(self) -> bool:
        """軸に沿った長方形か。MVP が保つべき不変条件の確認に使う。

        4点で、辺が水平と垂直を交互に繰り返していれば真。
        """
        if len(self.points) != 4:
            return False
        edges = []
        for i in range(4):
            x1, y1 = self.points[i]
            x2, y2 = self.points[(i + 1) % 4]
            horizontal = abs(y2 - y1) <= EPS
            vertical = abs(x2 - x1) <= EPS
            if horizontal == vertical:
                # 斜めの辺、または長さ 0 の辺
                return False
            edges.append("h" if horizontal else "v")
        return edges[0] != edges[1] and edges[1] != edges[2] and edges[2] != edges[3]

    def as_rect(self) -> Rect | None:
        """軸並行の長方形なら Rect に変換する。そうでなければ None。"""
        return self.bounds() if self.is_axis_aligned_rect() else None

    def to_list(self) -> list[list[float]]:
        return [[x, y] for x, y in self.points]

    @classmethod
    def from_list(cls, data: Any, where: str) -> Polygon:
        seq: Sequence[Any] = v.req_list(data, where)
        if len(seq) < 3:
            raise ProjectFormatError(f"{where}: 多角形には3点以上必要です（{len(seq)}点）")
        return cls(tuple(v.point(p, f"{where}[{i}]") for i, p in enumerate(seq)))


def fit_size(
    src_w: float,
    src_h: float,
    max_w: float,
    max_h: float,
    *,
    cover: bool = False,
) -> tuple[float, float]:
    """縦横比を保ったまま `max_w` × `max_h` に合わせた大きさを返す。

    `cover=False` は枠に収める（余白が出る）、`True` は枠を埋める（はみ出す）。
    「コマにフィット」は後者を使う。

    1×1 や 1×256 のような極端な画像でも 0 や負の値を返さないこと。
    ここでゼロ除算や幅 0 が出ると、以降の拡大縮小がすべて壊れる
    （tests/fixtures の pixel_1x1 / tall_1x256 / wide_256x1 がその検証用）。
    """
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"元画像の寸法が不正です（{src_w} x {src_h}）")
    if max_w <= 0 or max_h <= 0:
        raise ValueError(f"収める枠の寸法が不正です（{max_w} x {max_h}）")

    scale_w = max_w / src_w
    scale_h = max_h / src_h
    scale = max(scale_w, scale_h) if cover else min(scale_w, scale_h)
    return (src_w * scale, src_h * scale)


def fit_rect(
    src_w: float,
    src_h: float,
    bounds: Rect,
    *,
    cover: bool = False,
) -> Rect:
    """`bounds` の中央に、縦横比を保って配置した矩形を返す。"""
    w, h = fit_size(src_w, src_h, bounds.w, bounds.h, cover=cover)
    cx, cy = bounds.center
    return Rect(cx - w / 2.0, cy - h / 2.0, w, h)
