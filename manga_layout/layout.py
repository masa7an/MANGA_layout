"""コマ割りの計算。

当たり判定・リサイズ・分割・吸着を、**画面から切り離して**ここに置く。
Qt を使わないので画面なしでテストでき、操作の細かい挙動（最小の大きさ、
吸着の優先順位、分割時の画像の行き先）を目で確かめずに固定できる。

座標は要件定義 3章のとおりすべて mm。画面の倍率が変わっても、
ここの計算は一切変わらない。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .geometry import Polygon, Rect
from .model import ImageObject, Page, Panel, Project

# 8方向のつまみ。n=上 s=下 w=左 e=右
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

# クリックだけでコマを置いたときの大きさ。基本枠の何割にするか
DEFAULT_PANEL_RATIO = 1.0 / 3.0


@dataclass(frozen=True)
class LayoutSettings:
    """コマ割りの操作にかかわる設定。単位はすべて mm。"""

    # コマとコマの隙間。分割したときにこの幅を空ける
    gutter: float = 6.0
    # ページの余白。吸着の目安線としても使う
    margin: float = 15.0
    # コマの最小の辺。これ以下には縮まない
    min_panel_size: float = 5.0


DEFAULT_SETTINGS = LayoutSettings()


# --------------------------------------------------------------------------
# 当たり判定
# --------------------------------------------------------------------------


def panel_at(page: Page, x: float, y: float) -> Panel | None:
    """その位置にあるコマ。重なっている場合は手前のものを返す。"""
    for panel in sorted(page.panels, key=lambda p: p.z, reverse=True):
        if panel.shape.bounds().contains(x, y):
            return panel
    return None


def image_at(panel: Panel, x: float, y: float) -> ImageObject | None:
    """コマの中のその位置にある画像。重なっていれば手前のものを返す。

    コマの外にはみ出した部分は当たらない。そこは切り抜かれて見えておらず、
    見えていないものを掴めるとどこを触っているのか分からなくなる。
    """
    if not panel.shape.bounds().contains(x, y):
        return None
    for image in sorted(panel.children, key=lambda i: i.z, reverse=True):
        if image.rect.contains(x, y):
            return image
    return None


def handle_positions(rect: Rect) -> dict[str, tuple[float, float]]:
    """8方向のつまみの中心座標。"""
    cx, cy = rect.center
    return {
        "nw": (rect.x, rect.y),
        "n": (cx, rect.y),
        "ne": (rect.right, rect.y),
        "e": (rect.right, cy),
        "se": (rect.right, rect.bottom),
        "s": (cx, rect.bottom),
        "sw": (rect.x, rect.bottom),
        "w": (rect.x, cy),
    }


def handle_at(rect: Rect, x: float, y: float, size: float) -> str | None:
    """その位置にあるつまみの名前。

    `size` はつまみの一辺（mm）。画面上で常に同じ大きさに見せるため、
    呼ぶ側が表示倍率から換算して渡す。
    """
    half = size / 2.0
    # 角を優先する。角と辺のつまみが重なる小さなコマで、
    # 角をつかんだつもりが辺になる誤操作を防ぐ
    positions = handle_positions(rect)
    for name in ("nw", "ne", "se", "sw", "n", "e", "s", "w"):
        hx, hy = positions[name]
        if abs(x - hx) <= half and abs(y - hy) <= half:
            return name
    return None


def resize_rect(rect: Rect, handle: str, x: float, y: float, min_size: float) -> Rect:
    """つまみを (x, y) までドラッグした結果の矩形。

    最小の大きさで止める。反対側の辺を追い越して裏返ることはない。
    """
    left, top, right, bottom = rect.x, rect.y, rect.right, rect.bottom
    if "w" in handle:
        left = min(x, right - min_size)
    if "e" in handle:
        right = max(x, left + min_size)
    if "n" in handle:
        top = min(y, bottom - min_size)
    if "s" in handle:
        bottom = max(y, top + min_size)
    return Rect(left, top, right - left, bottom - top)


# --------------------------------------------------------------------------
# 吸着
# --------------------------------------------------------------------------


def snap_candidates(
    page: Page,
    exclude_panel_id: str | None,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> tuple[list[float], list[float]]:
    """吸着先の座標（縦線の x、横線の y）。

    ページの端と余白、他のコマの辺に加えて、**他のコマから隙間ぶん離れた位置**
    も候補に入れる。コマを並べるとき、隙間を目分量で合わせずに済む。
    """
    xs = [0.0, page.size.w]
    ys = [0.0, page.size.h]
    if settings.margin > 0:
        xs += [settings.margin, page.size.w - settings.margin]
        ys += [settings.margin, page.size.h - settings.margin]

    for panel in page.panels:
        if panel.id == exclude_panel_id:
            continue
        r = panel.shape.bounds()
        xs += [r.x, r.right, r.x - settings.gutter, r.right + settings.gutter]
        ys += [r.y, r.bottom, r.y - settings.gutter, r.bottom + settings.gutter]

    return xs, ys


def snap_delta(values: list[float], candidates: list[float], threshold: float) -> float:
    """`values` のどれかが `candidates` のどれかに最も近づく補正量。

    近いものが無ければ 0（吸着しない）。
    """
    best = 0.0
    best_distance = threshold
    for value in values:
        for candidate in candidates:
            diff = candidate - value
            if abs(diff) < best_distance:
                best_distance = abs(diff)
                best = diff
    return best


def snap_moved_rect(
    rect: Rect, xs: list[float], ys: list[float], threshold: float
) -> Rect:
    """移動中の矩形を、左右いずれかの辺で吸着させる。"""
    dx = snap_delta([rect.x, rect.right], xs, threshold)
    dy = snap_delta([rect.y, rect.bottom], ys, threshold)
    return rect.translated(dx, dy)


def snap_point(
    handle: str, x: float, y: float, xs: list[float], ys: list[float], threshold: float
) -> tuple[float, float]:
    """リサイズ中のつまみ位置を吸着させる。

    動かしている辺だけを対象にする。上辺をつかんでいるのに
    左右へ吸着すると、意図しない方向に形が変わってしまう。
    """
    if "w" in handle or "e" in handle:
        x += snap_delta([x], xs, threshold)
    if "n" in handle or "s" in handle:
        y += snap_delta([y], ys, threshold)
    return x, y


# --------------------------------------------------------------------------
# 編集
# --------------------------------------------------------------------------


def aspect_of(src_px: tuple[int, int]) -> float:
    """元画像の縦横比（幅÷高さ）。取れなければ 0。"""
    w, h = src_px
    if w <= 0 or h <= 0:
        return 0.0
    return w / h


def _centered(outer: Rect, aspect: float, height: float) -> Rect:
    """`outer` の中心に、縦横比 `aspect`・高さ `height` の矩形を置く。"""
    w = height * aspect
    cx, cy = outer.center
    return Rect(cx - w / 2.0, cy - height / 2.0, w, height)


def contain_rect_in(outer: Rect, src_px: tuple[int, int]) -> Rect:
    """縦横比を保ったまま `outer` に**収まる**最大の矩形（中央寄せ）。

    貼り付け直後の置き場所に使う。絵の全体が見えるので、どこが写るかを
    利用者が見てから「コマにフィット」で埋めるか決められる。
    """
    aspect = aspect_of(src_px)
    if aspect <= 0.0 or outer.w <= 0.0 or outer.h <= 0.0:
        return outer
    return _centered(outer, aspect, min(outer.w / aspect, outer.h))


def cover_rect_in(outer: Rect, src_px: tuple[int, int]) -> Rect:
    """縦横比を保ったまま `outer` を**埋める**最小の矩形（中央寄せ）。

    「コマにフィット」の中身。はみ出した分はコマの形で切り抜かれるので、
    コマの中に隙間が残らない。
    """
    aspect = aspect_of(src_px)
    if aspect <= 0.0 or outer.w <= 0.0 or outer.h <= 0.0:
        return outer
    return _centered(outer, aspect, max(outer.w / aspect, outer.h))


def resize_rect_keep_aspect(
    rect: Rect, handle: str, x: float, y: float, min_size: float, aspect: float
) -> Rect:
    """縦横比を保ったままリサイズする（Shift 併用時）。

    `aspect` は 幅÷高さ。0 以下なら普通のリサイズと同じ。

    角のつまみは対角を固定して伸縮する。辺のつまみは動かせる向きが
    1方向しかないため、もう一方の辺は中央から均等に伸ばす。
    """
    free = resize_rect(rect, handle, x, y, min_size)
    if aspect <= 0.0:
        return free

    w, h = free.w, free.h
    if handle in ("n", "s"):
        w = h * aspect
    elif handle in ("e", "w"):
        h = w / aspect
    elif w / h > aspect:
        # 横に伸びすぎている。高さのほうに合わせる
        w = h * aspect
    else:
        h = w / aspect

    # 最小の大きさは縦横まとめて効かせる。片方ずつ持ち上げると
    # そこで縦横比が崩れ、Shift を押しているのに絵が歪む
    if w < min_size or h < min_size:
        scale = max(min_size / w, min_size / h)
        w *= scale
        h *= scale

    # つまんでいない側の辺を動かさない
    left = free.right - w if "w" in handle else free.x
    top = free.bottom - h if "n" in handle else free.y
    if handle in ("n", "s"):
        left = rect.center[0] - w / 2.0
    elif handle in ("e", "w"):
        top = rect.center[1] - h / 2.0
    return Rect(left, top, w, h)


def set_panel_rect(panel: Panel, rect: Rect) -> None:
    """コマの形を矩形で置き換える。

    中の画像も紐づいた吹き出しも動かさない。リサイズは「絵に対する窓の
    大きさを変える操作」なので、中身が付いて回らないほうが扱いやすい。
    位置をまとめて動かすのは `Page.move_panel()` の役目。
    """
    panel.shape = Polygon.from_rect(rect.normalized())


def split_panel(
    project: Project,
    page: Page,
    panel_id: str,
    *,
    horizontal: bool,
    position: float,
    settings: LayoutSettings = DEFAULT_SETTINGS,
) -> tuple[Panel, Panel]:
    """コマを2つに割る。

    `horizontal=True` なら横線で切って上下に、`False` なら縦線で切って左右に
    分ける。`position` は切る線の座標（mm）で、その前後に隙間ぶんを空ける。

    元のコマは前半（上または左）として id ごと残る。紐づいた吹き出しは
    元のコマを指したままなので、追随先が変わらない。
    中の画像は、中心がどちら側に入るかで振り分ける。
    """
    panel = page.panel(panel_id)
    rect = panel.shape.as_rect()
    if rect is None:
        raise ValueError("斜めのコマはまだ分割できません")

    half_gutter = settings.gutter / 2.0
    if horizontal:
        first = Rect(rect.x, rect.y, rect.w, position - half_gutter - rect.y)
        second_y = position + half_gutter
        second = Rect(rect.x, second_y, rect.w, rect.bottom - second_y)
        sizes = (first.h, second.h)
    else:
        first = Rect(rect.x, rect.y, position - half_gutter - rect.x, rect.h)
        second_x = position + half_gutter
        second = Rect(second_x, rect.y, rect.right - second_x, rect.h)
        sizes = (first.w, second.w)

    if min(sizes) < settings.min_panel_size:
        raise ValueError(
            f"分割すると幅 {min(sizes):.1f}mm のコマができます"
            f"（最小 {settings.min_panel_size:.1f}mm）"
        )

    set_panel_rect(panel, first)

    new_panel = project.add_panel(page, second)
    new_panel.border = dataclasses.replace(panel.border)
    # 並びを元のコマの直後にする。id 順と見た目の順を揃えておくと、
    # あとで一覧に出したときに追いやすい
    page.panels.remove(new_panel)
    page.panels.insert(page.panels.index(panel) + 1, new_panel)

    for image in list(panel.children):
        if _belongs_to(image, second, first):
            panel.children.remove(image)
            new_panel.children.append(image)

    return panel, new_panel


def _belongs_to(image: ImageObject, target: Rect, other: Rect) -> bool:
    """画像の中心が `target` 側にあるか。

    どちらにも入らない（隙間の上に中心がある）場合は、近いほうに寄せる。
    """
    cx, cy = image.rect.center
    if target.contains(cx, cy):
        return True
    if other.contains(cx, cy):
        return False
    return _distance_to(target, cx, cy) < _distance_to(other, cx, cy)


def _distance_to(rect: Rect, x: float, y: float) -> float:
    dx = max(rect.x - x, 0.0, x - rect.right)
    dy = max(rect.y - y, 0.0, y - rect.bottom)
    return (dx * dx + dy * dy) ** 0.5


def full_page_rect(page: Page, settings: LayoutSettings = DEFAULT_SETTINGS) -> Rect:
    """余白を除いたページ全面の矩形。最初の1コマを作るときに使う。"""
    m = settings.margin
    return Rect(m, m, page.size.w - m * 2, page.size.h - m * 2)


def default_panel_rect(
    page: Page, x: float, y: float, settings: LayoutSettings = DEFAULT_SETTINGS
) -> Rect:
    """クリックした位置に置く、既定の大きさのコマ。

    ドラッグせずにクリックしただけのときに使う。大きさは基本枠のおよそ
    `DEFAULT_PANEL_RATIO`。あとから位置と大きさを整える前提の仮置きなので、
    厳密な値である必要はない。

    クリック位置を中心に置き、用紙からはみ出す場合は用紙の中へ寄せる。
    はみ出したまま作ると、つまみが画面外に出て掴めなくなる。
    """
    inner = full_page_rect(page, settings)
    w = min(max(inner.w * DEFAULT_PANEL_RATIO, settings.min_panel_size), page.size.w)
    h = min(max(inner.h * DEFAULT_PANEL_RATIO, settings.min_panel_size), page.size.h)
    left = min(max(x - w / 2.0, 0.0), page.size.w - w)
    top = min(max(y - h / 2.0, 0.0), page.size.h - h)
    return Rect(left, top, w, h)
