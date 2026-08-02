"""ページ座標の図形。単位はミリメートル（mm、浮動小数）。

要件定義 3章のとおり、ページ左上を原点とする絶対座標だけを扱う。
表示倍率や書き出し dpi をあとから変えても、ここの値は変わらない。
画面ピクセルへの換算はこの層では行わない（表示側の責務）。

`Rect` も `Polygon` も**書き換え不可**にしてある。
1つの矩形を複数のオブジェクトが共有してしまうと、片方を動かしたつもりが
両方動く、という追いにくい不具合になるため。移動や変形は新しい値を返す。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from . import validation as v
from .errors import ProjectFormatError

# 座標の比較に使う許容誤差（mm）。0.000001mm＝1ナノメートル相当で、
# 浮動小数の丸め誤差だけを吸収し、意味のある差は潰さない大きさ。
EPS = 1e-6


@dataclass(frozen=True)
class Size:
    """幅と高さ（mm）。ページ寸法に使う。"""

    w: float
    h: float

    def to_dict(self) -> dict[str, float]:
        return {"w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Size":
        d = v.req_mapping(data, where)
        return cls(w=v.positive(d, "w", where), h=v.positive(d, "h", where))


@dataclass(frozen=True)
class Rect:
    """左上の座標と大きさ（mm）。画像・吹き出し・テキストの配置に使う。"""

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

    def normalized(self) -> "Rect":
        """幅や高さが負なら、左上を付け替えて正の値にする。

        ドラッグで矩形を描くとき、右下から左上へ引くと負の幅になる。
        """
        x, w = (self.x, self.w) if self.w >= 0 else (self.x + self.w, -self.w)
        y, h = (self.y, self.h) if self.h >= 0 else (self.y + self.h, -self.h)
        return Rect(x, y, w, h)

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.right and self.y <= y <= self.bottom

    def intersects(self, other: "Rect") -> bool:
        return not (
            other.x > self.right
            or other.right < self.x
            or other.y > self.bottom
            or other.bottom < self.y
        )

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Rect":
        d = v.req_mapping(data, where)
        return cls(
            x=v.number(d, "x", where),
            y=v.number(d, "y", where),
            w=v.number(d, "w", where),
            h=v.number(d, "h", where),
        )


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
    def from_rect(cls, rect: Rect) -> "Polygon":
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

    def bounds(self) -> Rect:
        """外接する矩形。当たり判定の粗い絞り込みや、コマの寸法表示に使う。"""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def translated(self, dx: float, dy: float) -> "Polygon":
        return Polygon(tuple((x + dx, y + dy) for x, y in self.points))

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
    def from_list(cls, data: Any, where: str) -> "Polygon":
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
