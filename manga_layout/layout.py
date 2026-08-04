"""コマ割りの計算。

当たり判定・リサイズ・分割・吸着を、**画面から切り離して**ここに置く。
Qt を使わないので画面なしでテストでき、操作の細かい挙動（最小の大きさ、
吸着の優先順位、分割時の画像の行き先）を目で確かめずに固定できる。

座標は要件定義 3章のとおりすべて px。画面の倍率が変わっても、
ここの計算は一切変わらない。
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from . import vertical
from .geometry import EPS, Polygon, Rect
from .model import (
    SLANT_DIRECTIONS,
    SLANT_LEFT,
    SLANT_RIGHT,
    BalloonObject,
    ImageObject,
    Page,
    Panel,
    Project,
    SceneObject,
    SlantPair,
    StickerObject,
    TextObject,
)

# 8方向のつまみ。n=上 s=下 w=左 e=右
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")

# クリックだけでコマを置いたときの大きさ。基本枠の何割にするか
DEFAULT_PANEL_RATIO = 1.0 / 3.0


@dataclass(frozen=True)
class LayoutSettings:
    """コマ割りの操作にかかわる設定。単位はすべて px。"""

    # コマとコマの隙間。分割したときにこの幅を空ける
    gutter: float = 35.0
    # ページの余白。吸着の目安線としても使う
    margin: float = 89.0
    # コマの最小の辺。これ以下には縮まない
    min_panel_size: float = 30.0
    # 斜め割りの傾き。垂直から何度倒すか（12° ＝ 水平の辺から 78°）。
    # 角度を固定にしてあるので、コマの大小によらず傾きが揃って見える。
    # 保存済みのペアは自分の角度を持つので、ここを変えても変形しない
    slant_angle: float = 12.0


DEFAULT_SETTINGS = LayoutSettings()


@dataclass(frozen=True)
class BalloonSettings:
    """吹き出しの形にかかわる設定。長さは px。

    コマ割りの設定（`LayoutSettings`）とは別にしてある。吹き出しの見た目は
    作風で変えたくなる一方、コマの隙間や余白は紙面の決まりごとで、
    調整したい場面がまったく違うため。
    """

    # クリックだけで置いたときの大きさ。**縦長。**
    # 日本語のマンガは縦書きなので、中に入るセリフが縦に長くなる。
    # 横長の吹き出しに縦書きを入れると、左右が空いて下がはみ出す。
    #
    # **セリフの枠（`DEFAULT_TEXT_SIZE`）より一回り大きく保つ。**
    # 中に入るセリフのほうが大きいと、置いた瞬間にはみ出す
    default_size: tuple[float, float] = (333.0, 496.0)
    # しっぽの付け根の幅
    tail_width: float = 35.0
    # ギザギザの山の数。増やすと細かく、減らすと荒くなる
    jagged_spikes: int = 14
    # ギザギザの谷の深さ。半径に対する割合（0.25 なら谷が 75% の位置）
    jagged_depth: float = 0.22
    # 波形の波の数。ギザギザより多くして、細かく震えて見えるようにする
    wavy_waves: int = 16
    # 波形の谷の深さ。半径に対する割合。**ギザギザより浅くする。**
    # 深くすると花びらのように見え、叫びとの差でなく別の物になる
    wavy_depth: float = 0.09
    # 楕円を何本の線分で近似するか。書き出しでも同じ値を使う
    ellipse_segments: int = 72


DEFAULT_BALLOON_SETTINGS = BalloonSettings()

# 作ったばかりのしっぽが、吹き出しの下へ伸びる長さ。**吹き出しの高さに
# 対する割合。**
#
# 吹き出しの既定を縦長（高さ 496px）にしたことで、高さ基準のしっぽが
# 一緒に伸びて長すぎになった。0.5 から 25% 減らして 0.375 にしてある
# （2026-08-03）。既にある吹き出しのしっぽは自分の先端を持つので変わらない
TAIL_LENGTH_RATIO = 0.375

# 吹き出しがごく小さいときでも、しっぽが潰れない最小の長さ（px）
TAIL_LENGTH_MIN_PX = 4.0


# --------------------------------------------------------------------------
# 当たり判定
# --------------------------------------------------------------------------


def panel_at(page: Page, x: float, y: float) -> Panel | None:
    """その位置にあるコマ。重なっている場合は手前のものを返す。"""
    for panel in sorted(page.panels, key=lambda p: p.z, reverse=True):
        if panel.shape.contains(x, y):
            return panel
    return None


def image_at(panel: Panel, x: float, y: float) -> ImageObject | None:
    """コマの中のその位置にある画像。重なっていれば手前のものを返す。

    コマの外にはみ出した部分は当たらない。そこは切り抜かれて見えておらず、
    見えていないものを掴めるとどこを触っているのか分からなくなる。
    """
    if not panel.shape.contains(x, y):
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

    `size` はつまみの一辺（px）。画面上で常に同じ大きさに見せるため、
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

    # 斜めの組は外側の矩形を1つの候補として扱う。1枚ずつ外接矩形を取ると、
    # 斜めに削られた側に**実在しない縦線**ができ、そこへ吸い付いてしまう。
    # 組は必ず一緒に動くので、片方が除外対象なら両方を飛ばす
    done: set[str] = set()
    for panel in page.panels:
        if panel.id in done:
            continue
        pair = page.slant_pair_of(panel.id)
        if pair is None:
            if panel.id == exclude_panel_id:
                continue
            r = panel.shape.bounds()
        else:
            done.update(pair.members())
            if exclude_panel_id in pair.members():
                continue
            r = page.slant_bounds(pair)
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
    """コマの外接矩形が `rect` になるよう形を作り直す。

    中の画像も紐づいた吹き出しも動かさない。リサイズは「絵に対する窓の
    大きさを変える操作」なので、中身が付いて回らないほうが扱いやすい。
    位置をまとめて動かすのは `Page.move_panel()` の役目。

    矩形で上書きせず頂点を比例で移すのは、斜めのコマを潰さないため。
    軸並行の長方形なら結果は `rect` そのものなので、矩形のコマの挙動は
    変わらない。
    """
    panel.shape = panel.shape.fitted_to(rect.normalized())


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
    分ける。`position` は切る線の座標（px）で、その前後に隙間ぶんを空ける。

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
            f"分割すると幅 {min(sizes):.0f}px のコマができます"
            f"（最小 {settings.min_panel_size:.0f}px）"
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


# 波形の1波を最低何本の線分で描くか（→ `wavy_points`）
WAVY_MIN_SEGMENTS_PER_WAVE = 8


def wavy_points(
    rect: Rect, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """波形（不安）の輪郭。

    ギザギザと同じく楕円の半径を増減させて作るが、**交互ではなく
    なめらかに**増減させる。角が立たないぶん、叫びではなく震えに見える。

    **頂点数は波の数の整数倍にする。** 半端だと最後の波だけ途中で
    打ち切られ、始点との継ぎ目に角が出る。

    1波あたりの本数には下限を置く。少ない本数で描くと山と谷の間が
    直線になって**角が立ち、ギザギザとの差が消える**。なめらかさが
    この形の意味そのものなので、分割数の設定より下限を優先する。
    """
    waves = max(2, settings.wavy_waves)
    depth = min(max(settings.wavy_depth, 0.0), 0.9)
    per_wave = max(
        WAVY_MIN_SEGMENTS_PER_WAVE, -(-max(8, settings.ellipse_segments) // waves)
    )
    n = per_wave * waves
    step = 2.0 * math.pi / n
    half = depth / 2.0
    # 山で 1.0、谷で 1.0 - depth。cos なので始点（角度 0）は必ず山になり、
    # 一周してちょうど山へ戻る
    return tuple(
        _on_ellipse(rect, i * step, 1.0 - half + half * math.cos(waves * i * step))
        for i in range(n)
    )


def balloon_outline(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """吹き出し本体の輪郭。種類で切り替える。"""
    if balloon.style == "jagged":
        return jagged_points(balloon.rect, settings)
    if balloon.style == "wavy":
        return wavy_points(balloon.rect, settings)
    return ellipse_points(balloon.rect, settings)


def _tail_base_ratio(balloon: BalloonObject, settings: BalloonSettings) -> float:
    """しっぽの付け根を、楕円の何割の位置に置くか。

    輪郭より必ず内側に置く。外側だと、輪郭が凹んでいる箇所（ギザギザの谷、
    波形の谷）で本体と三角形が離れ、継ぎ目に隙間が空く。
    """
    if balloon.style == "jagged":
        return 1.0 - min(max(settings.jagged_depth, 0.0), 0.9)
    if balloon.style == "wavy":
        return 1.0 - min(max(settings.wavy_depth, 0.0), 0.9)
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
    return (cx, rect.bottom + max(rect.h * TAIL_LENGTH_RATIO, TAIL_LENGTH_MIN_PX))


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


# 置いた直後のマークの長辺（px）。**素材の原寸に近い値にしてある。**
#
# 組み込み素材は長辺 240〜297px（余白を削ったあと）。ここを大きく取ると
# 置いた瞬間から拡大されて輪郭がぼけ、小さく取ると「大きな！」として
# 置き直す手数が毎回かかる。A4（1240×1754px）の幅の 2 割弱にあたる
STICKER_DEFAULT_LONG_PX = 240.0


def default_sticker_rect(
    page: Page, x: float, y: float, src_px: tuple[int, int]
) -> Rect:
    """クリックした位置に置く、既定の大きさのマーク。用紙の中へ収める。

    **縦横比は必ず保つ。** 画像なので、比を崩すと記号の形が壊れる。
    """
    aspect = aspect_of(src_px)
    if aspect <= 0.0:
        w = h = STICKER_DEFAULT_LONG_PX
    elif aspect >= 1.0:
        w, h = STICKER_DEFAULT_LONG_PX, STICKER_DEFAULT_LONG_PX / aspect
    else:
        w, h = STICKER_DEFAULT_LONG_PX * aspect, STICKER_DEFAULT_LONG_PX

    w, h = min(w, page.size.w), min(h, page.size.h)
    left = min(max(x - w / 2.0, 0.0), max(page.size.w - w, 0.0))
    top = min(max(y - h / 2.0, 0.0), max(page.size.h - h, 0.0))
    return Rect(left, top, w, h)


def sticker_at(page: Page, x: float, y: float) -> StickerObject | None:
    """その位置にあるマーク。重なっていれば手前のものを返す。

    **矩形で判定する。** 素材は透明な余白を削ってあるので、記号と矩形の
    ずれは傾いた記号の四隅くらいしか残らない。透明度を見る判定は、
    掴みにくさが実際に出てから入れる（要件定義 6.14）。
    """
    stickers = [f for f in page.floating if isinstance(f, StickerObject)]
    for sticker in sorted(stickers, key=lambda s: s.z, reverse=True):
        if sticker.rect.contains(x, y):
            return sticker
    return None


def text_ink_bands(text: TextObject) -> list[Rect]:
    """セリフのうち、**字が並んでいる範囲**。空なら枠そのもの。

    枠は既定で 230×422 あるが、字が数文字ならその大半は空いている。
    空いた場所まで拾うと、そこに置いたマークが**見えているのに掴めない**
    （2026-08-04 の不具合）。吹き出しを外接矩形ではなく楕円で判定して
    いるのと同じ理由で、描いていない場所は下へ譲る（要件定義 6.4、6.5）。

    **空のセリフは枠全体を返す。** 空のときは点線の枠を描いているので
    （`PageRenderer._draw_text`）、そこが「描いてある範囲」になる。
    ここを字の無い扱いにすると、作った直後のセリフを選べなくなる。

    字ではなく**帯**を返すのは、字と字の間・列と列の間で下へ抜けると
    掴み所が虫食いになるため。列の送りは字の大きさの 1.33 倍あり、
    その隙間はセリフの一部と見るのが自然。
    """
    rect = text.rect
    if not text.content or text.font.size_px <= 0.0:
        return [rect]

    size = text.font.size_px
    if text.direction == "vertical":
        # 列ごとに1本。位置の出所は縦書きの計算そのもの（→ `vertical.layout`）。
        # ここで組み直すと、字の置き場所と掴み所が別々にずれていく
        bands: dict[float, Rect] = {}
        for glyph in vertical.layout(text.content, rect, size, text.align):
            cell = glyph.cell
            left = cell.center[0] - size * vertical.COLUMN_PITCH / 2.0
            band = bands.get(left)
            if band is None:
                bands[left] = Rect(left, cell.y, size * vertical.COLUMN_PITCH, cell.h)
            else:
                bottom = max(band.bottom, cell.bottom)
                top = min(band.y, cell.y)
                bands[left] = Rect(left, top, band.w, bottom - top)
        return list(bands.values())

    # 横書きは Qt が行を組むので、**字送りが分からない＝横幅を出せない**。
    # 幅は枠のままにし、行の帯だけに絞る。既定の枠は縦長（230×422）で
    # 余っているのもほとんどが上下なので、これで用は足りる。
    # 行送りは縦書きの列の送りと同じ値を使う（→ `vertical.COLUMN_PITCH`）
    lines = text.content.split("\n")
    used = size * vertical.COLUMN_PITCH * len(lines)
    top = rect.y + (rect.h - used) / 2.0
    return [Rect(rect.x, top, rect.w, used)]


def text_contains(text: TextObject, x: float, y: float) -> bool:
    """その点がセリフの字の上か（→ `text_ink_bands`）。"""
    return any(band.contains(x, y) for band in text_ink_bands(text))


def text_at(page: Page, x: float, y: float) -> TextObject | None:
    """その位置にあるセリフ。重なっていれば手前のものを返す。

    **枠の矩形ではなく、字の並んでいる範囲で判定する**（→ `text_ink_bands`）。
    """
    texts = [f for f in page.floating if isinstance(f, TextObject)]
    for text in sorted(texts, key=lambda t: t.z, reverse=True):
        if text_contains(text, x, y):
            return text
    return None


def attach_target(page: Page, rect: Rect) -> str | None:
    """それを紐づけるコマの id。重なっていなければ None。

    中心が乗っているコマを選ぶ。作成時に自動で紐づけるのに使う
    （吹き出し → 6.4、マーク → 6.14）。
    """
    cx, cy = rect.center
    panel = panel_at(page, cx, cy)
    return panel.id if panel is not None else None


def full_page_rect(page: Page, settings: LayoutSettings = DEFAULT_SETTINGS) -> Rect:
    """余白を除いたページ全面の矩形。最初の1コマを作るときに使う。"""
    m = settings.margin
    return Rect(m, m, page.size.w - m * 2, page.size.h - m * 2)


def outside_page(page: Page) -> list[SceneObject]:
    """用紙からはみ出しているコマ・吹き出し・マーク・セリフ。

    ページの大きさを変えたあとに数えて知らせるためのもの（要件定義 6.1）。
    小さい用紙に変えると、それまで紙の上にあったものが黙って外へ出る。
    **勝手に動かして詰め直したりはしない。** 位置は利用者が決めたもので、
    直し方（縮める／動かす／サイズを戻す）も場面ごとに違う。

    コマの中の画像は数えない。コマ枠で切り抜かれるので、コマが紙の中に
    あるかぎり画像が紙からはみ出して見えることはない。
    """
    paper = Rect(0.0, 0.0, page.size.w, page.size.h)

    def sticks_out(r: Rect) -> bool:
        return (
            r.x < -EPS
            or r.y < -EPS
            or r.right > paper.right + EPS
            or r.bottom > paper.bottom + EPS
        )

    found: list[SceneObject] = [p for p in page.panels if sticks_out(p.shape.bounds())]
    found.extend(f for f in page.floating if sticks_out(f.rect))
    return found


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
