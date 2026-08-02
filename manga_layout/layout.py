"""コマ割りの計算。

当たり判定・リサイズ・分割・吸着を、**画面から切り離して**ここに置く。
Qt を使わないので画面なしでテストでき、操作の細かい挙動（最小の大きさ、
吸着の優先順位、分割時の画像の行き先）を目で確かめずに固定できる。

座標は要件定義 3章のとおりすべて mm。画面の倍率が変わっても、
ここの計算は一切変わらない。
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from .geometry import Polygon, Rect
from .model import BalloonObject, ImageObject, Page, Panel, Project, TextObject

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


@dataclass(frozen=True)
class BalloonSettings:
    """吹き出しの形にかかわる設定。長さは mm。

    コマ割りの設定（`LayoutSettings`）とは別にしてある。吹き出しの見た目は
    作風で変えたくなる一方、コマの隙間や余白は紙面の決まりごとで、
    調整したい場面がまったく違うため。
    """

    # クリックだけで置いたときの大きさ
    default_size: tuple[float, float] = (40.0, 26.0)
    # しっぽの付け根の幅
    tail_width: float = 6.0
    # ギザギザの山の数。増やすと細かく、減らすと荒くなる
    jagged_spikes: int = 14
    # ギザギザの谷の深さ。半径に対する割合（0.25 なら谷が 75% の位置）
    jagged_depth: float = 0.22
    # 楕円を何本の線分で近似するか。書き出しでも同じ値を使う
    ellipse_segments: int = 72


DEFAULT_BALLOON_SETTINGS = BalloonSettings()


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


# --------------------------------------------------------------------------
# 吹き出しの形
# --------------------------------------------------------------------------


def _on_ellipse(rect: Rect, angle: float, radius_ratio: float = 1.0) -> tuple[float, float]:
    """`rect` に内接する楕円の上の点。`angle` は媒介変数（ラジアン）。

    真円でない限り媒介変数は見た目の角度と一致しないが、輪郭を等間隔に
    刻む用途ではそれで足りる。
    """
    cx, cy = rect.center
    return (
        cx + rect.w / 2.0 * radius_ratio * math.cos(angle),
        cy + rect.h / 2.0 * radius_ratio * math.sin(angle),
    )


def ellipse_points(
    rect: Rect, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """楕円の輪郭。線分の集まりとして返す。

    Qt の `drawEllipse` を使わないのは、ギザギザと同じ経路で塗り・枠線・
    しっぽの合成ができるようにするため。形の生成をここに集めておくと、
    PNG 書き出し（Day 27）でも同じ輪郭が使える。
    """
    n = max(8, settings.ellipse_segments)
    step = 2.0 * math.pi / n
    return tuple(_on_ellipse(rect, i * step) for i in range(n))


def jagged_points(
    rect: Rect, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """ギザギザ（叫び）の輪郭。

    要件定義 9章のとおり、楕円の輪郭を角度で等分し、半径を交互に増減させて作る。
    山と谷を対にするため頂点数は必ず偶数にする。奇数だと一周したときに
    山が2つ隣り合い、そこだけ形が崩れる。
    """
    spikes = max(3, settings.jagged_spikes)
    depth = min(max(settings.jagged_depth, 0.0), 0.9)
    n = spikes * 2
    step = 2.0 * math.pi / n
    return tuple(
        _on_ellipse(rect, i * step, 1.0 if i % 2 == 0 else 1.0 - depth) for i in range(n)
    )


def balloon_outline(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """吹き出し本体の輪郭。種類で切り替える。"""
    if balloon.style == "jagged":
        return jagged_points(balloon.rect, settings)
    return ellipse_points(balloon.rect, settings)


def _tail_base_ratio(balloon: BalloonObject, settings: BalloonSettings) -> float:
    """しっぽの付け根を、楕円の何割の位置に置くか。

    輪郭より必ず内側に置く。外側だと、輪郭が凹んでいる箇所（ギザギザの谷）で
    本体と三角形が離れ、継ぎ目に隙間が空く。
    """
    if balloon.style == "jagged":
        return 1.0 - min(max(settings.jagged_depth, 0.0), 0.9)
    return 0.95


def tail_base_angle(balloon: BalloonObject) -> float | None:
    """付け根を置く媒介変数（ラジアン）。決められなければ None。

    `root_y` が指定されていれば**その高さ**に置く。先端から見て
    手前側（左右どちらか）の輪郭に付ける。指定が無ければ先端の向きに
    合わせる（それまでの挙動）。
    """
    rect = balloon.rect
    if rect.w <= 0.0 or rect.h <= 0.0:
        return None

    cx, cy = rect.center
    tip = balloon.tail.tip
    dx, dy = tip[0] - cx, tip[1] - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None  # 先端が中心に重なっている。向きが決まらない

    root_y = balloon.tail.root_y
    if root_y is None:
        # 先端を向く媒介変数。楕円の潰れ具合を打ち消してから角度を取る
        return math.atan2(dy / (rect.h / 2.0), dx / (rect.w / 2.0))

    # 高さから媒介変数を逆算する。同じ高さに左右2点あるので、
    # 先端のある側を選ぶ。上端・下端（±1）では左右が一致する
    angle = math.asin(min(max(root_y, -1.0), 1.0))
    return angle if dx >= 0.0 else math.pi - angle


def tail_root_point(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[float, float] | None:
    """付け根の中心。上下にずらす操作の掴み所として使う。"""
    angle = tail_base_angle(balloon)
    if angle is None:
        return None
    return _on_ellipse(balloon.rect, angle, _tail_base_ratio(balloon, settings))


def root_y_at(rect: Rect, y: float) -> float:
    """ページ座標の高さを `root_y`（割合）に直す。上端 -1、下端 +1。"""
    if rect.h <= 0.0:
        return 0.0
    ratio = (y - rect.center[1]) / (rect.h / 2.0)
    return min(max(ratio, -1.0), 1.0)


def tail_triangle(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """しっぽの三角形（付け根の2点と先端）。しっぽ無しなら None。

    付け根の幅は `Tail.width`、縦位置は `Tail.root_y`。
    """
    if not balloon.tail.enabled:
        return None

    angle = tail_base_angle(balloon)
    if angle is None:
        return None

    rect = balloon.rect
    cx, cy = rect.center
    tip = balloon.tail.tip
    ratio = _tail_base_ratio(balloon, settings)
    base_center = _on_ellipse(rect, angle, ratio)
    radius = math.hypot(base_center[0] - cx, base_center[1] - cy)
    if radius < 1e-9:
        return None

    # 付け根の幅を角度に直す。小さい吹き出しで付け根が一周しないよう頭を押さえる
    half = min(math.atan2(balloon.tail.width / 2.0, radius), math.pi / 3.0)
    return (
        _on_ellipse(rect, angle - half, ratio),
        tip,
        _on_ellipse(rect, angle + half, ratio),
    )


def default_tail_tip(rect: Rect) -> tuple[float, float]:
    """作ったばかりの吹き出しのしっぽの先端。

    真下に少し伸ばす。人物はコマの中央寄りに描かれることが多く、
    吹き出しは上に置かれるため、下向きが当たりやすい。
    """
    cx, _ = rect.center
    return (cx, rect.bottom + max(rect.h * 0.5, 4.0))


def default_balloon_rect(
    page: Page, x: float, y: float, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> Rect:
    """クリックした位置に置く、既定の大きさの吹き出し。用紙の中へ収める。"""
    w = min(settings.default_size[0], page.size.w)
    h = min(settings.default_size[1], page.size.h)
    left = min(max(x - w / 2.0, 0.0), page.size.w - w)
    top = min(max(y - h / 2.0, 0.0), page.size.h - h)
    return Rect(left, top, w, h)


def balloon_contains(balloon: BalloonObject, x: float, y: float) -> bool:
    """吹き出しの本体（楕円）の内側か。

    外接矩形ではなく楕円で判定する。矩形だと四隅の何もない場所で
    掴めてしまい、下のコマが選べなくなる。
    """
    rect = balloon.rect
    if rect.w <= 0.0 or rect.h <= 0.0:
        return False
    cx, cy = rect.center
    nx = (x - cx) / (rect.w / 2.0)
    ny = (y - cy) / (rect.h / 2.0)
    return nx * nx + ny * ny <= 1.0


def balloon_at(page: Page, x: float, y: float) -> BalloonObject | None:
    """その位置にある吹き出し。重なっていれば手前のものを返す。"""
    balloons = [f for f in page.floating if isinstance(f, BalloonObject)]
    for balloon in sorted(balloons, key=lambda b: b.z, reverse=True):
        if balloon_contains(balloon, x, y):
            return balloon
    return None


def text_at(page: Page, x: float, y: float) -> TextObject | None:
    """その位置にあるセリフ。重なっていれば手前のものを返す。

    枠は矩形なので、そのまま矩形で判定する。吹き出しと違って
    「四隅に何も無い」ということが起きない。
    """
    texts = [f for f in page.floating if isinstance(f, TextObject)]
    for text in sorted(texts, key=lambda t: t.z, reverse=True):
        if text.rect.contains(x, y):
            return text
    return None


def attach_target(page: Page, rect: Rect) -> str | None:
    """その吹き出しを紐づけるコマの id。重なっていなければ None。

    中心が乗っているコマを選ぶ。作成時に自動で紐づけるのに使う
    （要件定義 6.4）。
    """
    cx, cy = rect.center
    panel = panel_at(page, cx, cy)
    return panel.id if panel is not None else None


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
