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
from .geometry import (
    EPS,
    Polygon,
    Rect,
    rotate_point,
    rotated_bounds,
    rotated_rect_contains,
    unrotate_point,
)
from .model import (
    TAIL_SHAPE_TRIANGLE,
    BalloonObject,
    ImageObject,
    Page,
    Panel,
    Project,
    SceneObject,
    StickerObject,
    TextObject,
)
from .noise import Noise, seed_from_text

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
    jagged_depth: float = 0.33
    # トゲトゲ（ギザギザの曲線版）の山の数と谷の深さ。
    #
    # **深さはギザギザと同じ 0.33。** 同じ叫びの直線版・曲線版なので、
    # 深さまで違うと「曲線にした」以外の差が混ざる。
    #
    # **山だけギザギザ（14）より減らして 12。** 曲線にするとトゲ1本が
    # 太くなるぶん、同じ数では密に見える。セリフを入れて 10 / 12 / 14 を
    # 並べ、2026-08-07 に目で見て決めた（→ 6.32）
    spiky_spikes: int = 12
    spiky_depth: float = 0.33
    # トゲトゲの頂の鋭さ。**小さいほど尖る**（頂の近くで一気に落ちる）。
    # 1.0 より大きくすると頂の近くが平らになって角が消え、ふわふわ側へ
    # 寄っていく＝叫びに読めなくなる（→ `spiky_points`）
    spiky_sharpness: float = 0.7
    # 波形の波の数。ギザギザより多くして、細かく震えて見えるようにする
    wavy_waves: int = 16
    # 波形の谷の深さ。半径に対する割合。**ギザギザより浅くする。**
    # 深くすると花びらのように見え、叫びとの差でなく別の物になる
    wavy_depth: float = 0.09
    # 雲の膨らみの数。ギザギザ・波形より少なくして、1つ1つを大きく見せる。
    #
    # **9 から 5 へ減らした**（2026-08-06）。狙いは綿ではなく「少しモコモコ
    # した楕円と楕円のつながり」。4 以下まで減らすと落花生の殻に見え、
    # つながった楕円に読めなくなる（描いて確かめた）
    cloud_lobes: int = 5
    # 雲のくびれの深さ。半径に対する割合。**浅いと丸との差が出ない。**
    # 深くしすぎると膨らみが指のように分かれて、綿ではなく花に見える。
    #
    # 膨らみを減らすと1つ1つが大きくなるので、同じ深さでは彫りが深すぎる。
    # 0.22 から 0.12 へ浅くしてある（2026-08-06）
    cloud_depth: float = 0.12
    # 雲の山の頂の平たさ。**小さいほど頂が広がり、膨らみ1つの半径が大きく
    # 見える。** 1.0 でちょうど |sin| の山（尖った頂）になる
    cloud_roundness: float = 0.55
    # 雲の手描きのゆらぎ。膨らみ1つ1つの幅とくびれの深さをこの割合だけ
    # ばらす。**0.0 で全部同じ形。** 種はフキダシの ID から作るので、
    # 開き直しても同じ形に戻る（→ `cloud_points`）
    cloud_jitter: float = 0.30
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

# 付け根を、先端の向きからどこまで離してよいか（ラジアン）。
#
# しっぽは付け根から先端へ引いた三角形で、**見えるのは本体からはみ出した
# 部分だけ**（→ `PageRenderer.balloon_path` の合成）。付け根を先端の反対側へ
# 回すと三角形のほとんどが本体に隠れ、外に出るところが針のように細くなる。
# 付け根だけ動かしたつもりでも、しっぽは同じ場所から出たままに見える。
#
# 実測（333×496 のフキダシ、既定の長さのしっぽ、先端は真下）では、見えている
# しっぽの面積が、ずれ 26 度で 85%、37 度で 69%、46 度で 52% まで落ちた。
# 40 度なら下端から右へ約 100px（フキダシ幅の3割）滑らせても形が保つ
# （2026-08-05）。
#
# **大きく向きを変えたいときは、付け根ではなく先端ごと回す**
# （→ `tail_tip_turned_to`）。この上限はあくまで微調整の範囲を決めるもの。
TAIL_ROOT_MAX_GAP = math.radians(40.0)

# しっぽを向ける先。楕円の媒介変数（ラジアン）で持つ。
# 画面の y は下向きが正なので、上が -π/2 になる。
#
# **左右を別々に持つ。** 高さ（`root_y`）だけで指すと、真横がどちら側かを
# 先端の現在位置任せにするしかない。左右に2人が向かい合うコマでは、
# どちらを指すかを選べないと使えない（相談 2026-08-05）
TAIL_DIRECTIONS = {
    "up": -math.pi / 2.0,
    "right": 0.0,
    "left": math.pi,
    "down": math.pi / 2.0,
}

# 丸い飛びしっぽ（心の声・独り言 → 要件定義 10.1）の形。
#
# **円の数は固定する。** 長さで増減させると、先端を引いている最中に円が
# 生えて形が跳ねる。数が変わらなければ、引いた量がそのまま間隔の広がりとして
# 目に見える
TAIL_BUBBLE_COUNT = 3

# 1つ先の円を、手前の円の何倍にするか。先端へ向かって小さくなるのが
# この形の意味そのもの。1.0 に近づけると数珠つなぎに見えて意味が消え、
# 小さくしすぎると3つめが点になって数えられなくなる
TAIL_BUBBLE_SHRINK = 0.72

# 円と円（および本体と先頭の円）の隙間。**手前の円の半径に対する割合。**
# **離れていること自体がこの形の意味**なので、くっつけない
# （三角のしっぽを本体と合成する 6.4 とは逆向きの決め）
TAIL_BUBBLE_GAP_RATIO = 0.45

# 先頭の円の半径の上限。**フキダシの短辺に対する割合。**
# しっぽを遠くまで引くと円がそのぶん大きくなるので、頭を押さえる。
# 上限に当たった分だけ鎖は先端まで届かなくなるが、向きは変わらない
TAIL_BUBBLE_MAX_RATIO = 0.25

# **選ぶとき**のしっぽの当たり判定を、見えている形からどれだけ細くするか
# （→ `balloon_pick_at`）。
#
# 選んでいない吹き出しでも、しっぽを押せばその吹き出しが選べる。ただし
# しっぽは本体の外へ長く伸びるので、見えているとおりの太さで判定すると、
# **しっぽの下にあるコマや画像が選べなくなる**。細くしておけば、狙って
# 押したときだけ拾い、脇をかすめた分は下へ抜ける。
#
# 細くするのは幅だけで、長さはそのまま（先端まで押せる）。
#
# 既に選んでいる吹き出しのしっぽを掴むほうは**細くしない**
# （→ `PageView._tail_body_at`）。あちらは下のものと取り合いにならないうえ、
# 「しっぽの絵は見えているのに反応しない」というズレを潰すために
# 見えている形いっぱいに取った経緯がある（2026-08-05）
TAIL_PICK_NARROW = 0.6


# --------------------------------------------------------------------------
# 当たり判定
# --------------------------------------------------------------------------


def page_assets(page: Page):
    """ページにある「画像の実体を持つもの」を順に返す。

    **コマの中の画像とマークの両方。** マーク（→ 6.14）はページ直下に
    あってコマの子ではないが、実体が無ければ画面でも書き出しでも同じ×印
    で欠けが分かる（`render._draw_image_upright`）ので、数える側もここで
    一緒に扱う。

    以前は `ui.export.missing_assets_in` が使う版と `check.py` が使う版が
    別々に定義され、中身も一字一句同じだった。片方だけ直る危険を避けるため
    ここへ集約した（2026-08-08 に発見）。
    """
    for panel in page.panels:
        yield from panel.children
    for obj in page.floating:
        if isinstance(obj, StickerObject):
            yield obj


def panel_at(page: Page, x: float, y: float) -> Panel | None:
    """その位置にあるコマ。重なっている場合は手前のものを返す。

    **描く順（`render.py` の昇順ソート）を逆に辿る。** `z` が同じ2つが
    重なると、`sorted(..., reverse=True)` は同値の並びをそのまま保つため、
    描画では後ろ（手前に描かれる）ものが、この判定では逆に後回しになり、
    見えているものと掴めるものがずれていた（2026-08-08 に発見）。
    昇順ソートしてから並びごと逆順に辿れば、同値の並びも一緒にひっくり
    返るので、最後に描かれた（＝一番手前の）ものが最初に来る。
    """
    for panel in reversed(sorted(page.panels, key=lambda p: p.z)):
        if panel.shape.contains(x, y):
            return panel
    return None


def images_at(panel: Panel, x: float, y: float) -> list[ImageObject]:
    """コマの中のその位置にある画像を、**手前から奥の順**で全部返す。

    重なりを上から見ていきたい側（→ `PageView._wand_target`）が使う。
    切り抜き（→ 要件定義 10.3）で消えた所は、手前の絵を素通りして下の絵へ
    届かせたいが、**どこが消えているかは `assets/` を読まないと分からない**。
    ここは並べるところまでで、選ぶのは読める側に任せる。
    """
    if not panel.shape.contains(x, y):
        return []
    return [
        image
        for image in reversed(sorted(panel.children, key=lambda i: i.z))
        if rotated_rect_contains(image.rect, x, y, image.rotation)
    ]


def image_at(panel: Panel, x: float, y: float) -> ImageObject | None:
    """コマの中のその位置にある画像。重なっていれば手前のものを返す。

    コマの外にはみ出した部分は当たらない。そこは切り抜かれて見えておらず、
    見えていないものを掴めるとどこを触っているのか分からなくなる。

    **切り抜き（→ 10.3）で消えた所は見ない。** 消えた所も掴めるままにして
    あるのは、マスクが絵をまるごと消していると**選ぶ手立てが無くなる**ため
    （`image_orphaned_in` が防いでいる「見えない孤児」と同じ形。動かすことも
    消すこともできなくなる）。素通りさせるのは切り抜きの道具だけでよい。
    """
    found = images_at(panel, x, y)
    return found[0] if found else None


def image_orphaned_in(bounds: Rect, image_rect: Rect, rotation: float) -> bool:
    """コマの外接矩形 `bounds` と、傾き `rotation` の画像が1pxも重ならないか。

    `image_at` は「コマの中 かつ 画像の中」の点を要求する。両者が1pxも
    重ならなくなると、どの点を押してもこの条件を満たせなくなり、Undo 以外に
    取り戻す手段が無い「見えない孤児」になる（2026-08-08 に発見）。

    重なりは外接矩形どうしで見る。**傾いた形での正確な重なりではない。**
    ふつうの矩形のコマではこれがそのままコマの形と一致するので判定は
    正確になる。斜めに割ったコマ（→ 6.10）だけは外接矩形が実際の多角形
    よりわずかに広いが、それでも「制約なし」だった今までより安全で、
    万一のときは Undo で戻せる。

    **動かすのが画像側かコマ側かをここでは決めない。** 画像を動かす・
    複製する側は `image_orphaned_at`、コマの大きさを変える側は
    `panel_rect_orphans` が、それぞれ何を変えるかを決めて呼ぶ。
    """
    moved = rotated_bounds(image_rect, rotation)
    overlap_w = min(moved.right, bounds.right) - max(moved.x, bounds.x)
    overlap_h = min(moved.bottom, bounds.bottom) - max(moved.y, bounds.y)
    return overlap_w < 1.0 or overlap_h < 1.0


def image_orphaned_at(panel: Panel, moved_rect: Rect, rotation: float) -> bool:
    """画像を `moved_rect`（傾き `rotation`）にすると、自分のコマの外へ
    完全に出て、二度と選べなくなるか（→ `image_orphaned_in`）。

    **動かす場合と大きさを変える場合の両方で使う。** どちらも「コマは
    そのままで、画像の矩形だけが変わる」形なので、渡す矩形が違うだけ
    （移動と複製は 2026-08-08、画像のリサイズは 2026-08-09 に追加）。
    """
    return image_orphaned_in(panel.shape.bounds(), moved_rect, rotation)


def panel_rect_orphans(panel: Panel, bounds: Rect) -> bool:
    """コマの外接矩形を `bounds` にすると、中の画像が1枚でも孤児になるか。

    **コマの大きさ変更は中の画像を動かさない**（→ `set_panel_rect`。絵に
    対する窓の大きさを変える操作なので、中身が付いて回らないほうが扱い
    やすい）。そのため、つまみを反対側へ大きく引いてコマを画像の外へ
    出すと、移動で塞いだのと同じ「見えない孤児」が作れていた
    （2026-08-09 に発見。移動側の穴と対）。
    """
    return any(
        image_orphaned_in(bounds, child.rect, child.rotation)
        for child in panel.children
    )


def pick_stack(page: Page, x: float, y: float) -> list[str]:
    """その位置で選べるものの id を、**ダブルクリックで巡る順**に並べる
    （→ 要件定義 6.25）。

        手前のコマ → その中の画像（手前から）→ 次のコマ → その中の画像 → …

    **重なりの順（z）そのままではない。** 画像はコマの上に描かれるので、
    z の順に並べれば画像が先に来る。ここでコマを先に置いているのは、
    **1つめの段を「コマ → 中の画像」にするため**で、ダブルクリック1回の
    動きが今まで（→ 6.3）と変わらないようにしている。

    コマどうし・画像どうしの前後は z のまま（`panel_at`・`image_at` と
    同じ）。**見えていないものも入る。** 隠れたものを拾い上げるのが
    この並びの目的で、選択枠は最前面に描かれるので選べば必ず見える。
    """
    stack: list[str] = []
    for panel in reversed(sorted(page.panels, key=lambda p: p.z)):
        if not panel.shape.contains(x, y):
            continue
        stack.append(panel.id)
        stack.extend(
            image.id
            for image in reversed(sorted(panel.children, key=lambda i: i.z))
            if rotated_rect_contains(image.rect, x, y, image.rotation)
        )
    return stack


def next_in_stack(stack: list[str], current: str | None) -> str | None:
    """`current` の次に選ぶものの id。末尾まで行ったら先頭へ戻る。

    **`current` が並びに無いときは先頭の次（＝手前のコマの中の画像）。**
    フキダシの上でダブルクリックしたときがこれにあたり、今までどおり
    下にある画像が選ばれる（→ 6.25）。
    """
    if not stack:
        return None
    index = stack.index(current) if current in stack else 0
    return stack[(index + 1) % len(stack)]


def handle_positions(
    rect: Rect, rotation: float = 0.0
) -> dict[str, tuple[float, float]]:
    """8方向のつまみの中心座標。

    `rotation` を渡すと、矩形の中心まわりに回した位置を返す。傾いた画像で
    つまみだけ水平に残ると、掴む場所と絵の角がズレる（→ 要件定義 6.3）。
    名前（nw / n / ...）は**回す前の向き**のまま。リサイズの計算は回す前の
    座標で行うので、名前まで回すと対応が取れなくなる。
    """
    cx, cy = rect.center
    corners = {
        "nw": (rect.x, rect.y),
        "n": (cx, rect.y),
        "ne": (rect.right, rect.y),
        "e": (rect.right, cy),
        "se": (rect.right, rect.bottom),
        "s": (cx, rect.bottom),
        "sw": (rect.x, rect.bottom),
        "w": (rect.x, cy),
    }
    if rotation == 0.0:
        return corners
    return {
        name: rotate_point(hx, hy, cx, cy, rotation)
        for name, (hx, hy) in corners.items()
    }


def handle_at(
    rect: Rect, x: float, y: float, size: float, rotation: float = 0.0
) -> str | None:
    """その位置にあるつまみの名前。

    `size` はつまみの一辺（px）。画面上で常に同じ大きさに見せるため、
    呼ぶ側が表示倍率から換算して渡す。

    傾いているときは**つまみを回すのではなく、点を戻して**から見る。
    つまみは回っても正方形のままなので、どちらでも同じ結果になり、
    こちらは判定を1つも書き換えずに済む。
    """
    if rotation != 0.0:
        x, y = unrotate_point(x, y, rect, rotation)
    half = size / 2.0
    # 角を優先する。角と辺のつまみが重なる小さなコマで、
    # 角をつかんだつもりが辺になる誤操作を防ぐ
    positions = handle_positions(rect)
    for name in ("nw", "ne", "se", "sw", "n", "e", "s", "w"):
        hx, hy = positions[name]
        if abs(x - hx) <= half and abs(y - hy) <= half:
            return name
    return None


def anchor_of(rect: Rect, handle: str) -> tuple[float, float]:
    """つまみを引くあいだ動かない側の代表点（回す前の座標）。

    角のつまみなら対角、辺のつまみなら向かいの辺の中点。
    """
    cx, cy = rect.center
    x = rect.right if "w" in handle else rect.x if "e" in handle else cx
    y = rect.bottom if "n" in handle else rect.y if "s" in handle else cy
    return (x, y)


def keep_anchor(origin: Rect, resized: Rect, handle: str, rotation: float) -> Rect:
    """傾いた矩形のリサイズ結果を、掴んでいない側が動かないように直す。

    回転の中心が矩形の中心なので、**幅を変えると中心も動く**。回す前の
    座標で計算しただけだと、画面の上では固定したはずの反対側の角まで
    動いて見える。ここで平行移動して打ち消す。

    傾き 0 では絶対に再現しないズレなので、テストで固定してある
    （`tests/test_layout.py`）。
    """
    if rotation == 0.0:
        return resized
    ax, ay = anchor_of(origin, handle)
    ocx, ocy = origin.center
    rcx, rcy = resized.center
    # 同じ点が、回す前と後でどこへ行くか。その差だけ戻す
    wx, wy = rotate_point(ax, ay, ocx, ocy, rotation)
    nx, ny = rotate_point(ax, ay, rcx, rcy, rotation)
    return resized.translated(wx - nx, wy - ny)


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


def cover_rect_in(
    outer: Rect, src_px: tuple[int, int], rotation: float = 0.0
) -> Rect:
    """縦横比を保ったまま `outer` を**埋める**最小の矩形（中央寄せ）。

    「コマにフィット」の中身。はみ出した分はコマの形で切り抜かれるので、
    コマの中に隙間が残らない。

    `rotation` を渡すと、**傾けたまま**埋める大きさを返す（→ 要件定義 6.3）。
    傾いた矩形の外接矩形は `w|cos| + h|sin|` × `w|sin| + h|cos|` なので、
    これが `outer` を覆う最小の倍率を求める。傾き 0 では `|cos|=1, |sin|=0`
    となり、式は今までと同じ `max(w/比, h)` に戻る。
    """
    aspect = aspect_of(src_px)
    if aspect <= 0.0 or outer.w <= 0.0 or outer.h <= 0.0:
        return outer
    if rotation == 0.0:
        return _centered(outer, aspect, max(outer.w / aspect, outer.h))

    rad = math.radians(rotation)
    cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
    # 高さを t とすると幅は t*比。外接矩形の各辺が `outer` を覆う t を取る
    wide = aspect * cos + sin
    tall = aspect * sin + cos
    height = max(outer.w / wide if wide > EPS else 0.0,
                 outer.h / tall if tall > EPS else 0.0)
    return _centered(outer, aspect, height)


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


# トゲトゲの山1つを最低何本の線分で描くか（→ `spiky_points`）。
# **波形（8本）より多くする。** 山と谷の間が反っていることがこの形の意味
# なので、そこが直線に落ちるとギザギザとの差が消える（→ 6.32）
SPIKY_MIN_SEGMENTS_PER_SPIKE = 28


def spiky_points(
    rect: Rect, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """トゲトゲ（叫び・曲線版）の輪郭。

    ギザギザと同じ叫びの形だが、**山と谷を直線ではなく曲線でつなぐ。**
    頂は尖ったまま、谷だけが丸くなる（要件定義 6.32）。

    作りはふわふわ・雲と同じで、楕円の半径を角度ごとに増減させる。違うのは
    増減のさせ方だけ。

    山1つの中の位置 `s`（頂で 0.0、谷で 1.0）に対して
    `sin(π s / 2) ** sharpness` だけ半径を引く。

    - `s → 0`（頂）で傾きが残るので、**頂は角のまま**
    - `s = 1`（谷）で傾きが 0 になるので、**谷はなめらかに底を打つ**

    ふわふわ（`wavy_points`）が余弦で頂も谷も丸めるのと、ここが違う。

    `sharpness` は頂の近くの落ち方だけを変える。1.0 より小さいと急に
    落ちて鋭く、大きいと頂の近くが平らになって角そのものが消える。
    既定を 1.0 より小さくしてあるのは、**角が残ることがこの形の意味**
    だから（要件定義 6.32）。
    """
    spikes = max(3, settings.spiky_spikes)
    depth = min(max(settings.spiky_depth, 0.0), 0.9)
    sharpness = max(settings.spiky_sharpness, 0.05)
    # 頂点数は山の数の整数倍にする。半端だと最後の山だけ途中で打ち切られ、
    # 始点との継ぎ目に角が出る（ふわふわと同じ理由 → `wavy_points`）
    per_spike = max(
        SPIKY_MIN_SEGMENTS_PER_SPIKE, -(-max(8, settings.ellipse_segments) // spikes)
    )
    n = per_spike * spikes
    step = 2.0 * math.pi / n
    points = []
    for i in range(n):
        # 山1つの中での位置（0.0〜1.0）。0.0 が頂、0.5 で谷、1.0 で次の頂
        position = (i % per_spike) / per_spike
        s = 1.0 - abs(1.0 - 2.0 * position)
        ratio = 1.0 - depth * math.sin(math.pi * s / 2.0) ** sharpness
        points.append(_on_ellipse(rect, i * step, ratio))
    return tuple(points)


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


# 雲の膨らみ1つを最低何本の線分で描くか（→ `cloud_points`）。
# **波形（8本）より多くする。** 波形は「揺れていること」が伝わればよいが、
# 雲は膨らみ1つ1つが丸いことがこの形の意味なので、角が立つと綿に見えない。
#
# 膨らみを 9 個から 5 個へ減らした（2026-08-06）ぶん1つが大きくなるので、
# 12 本では頂に角が立つ。24 本へ増やしてある
CLOUD_MIN_SEGMENTS_PER_LOBE = 24

# ゆらぎが、くびれの深さをどこまで動かすか。`cloud_jitter` に対する割合。
# 幅ほど大きく振らない——深さまで同じだけ振ると、浅い谷と深い谷が混じって
# 「同じフキダシの輪郭」に見えなくなる
CLOUD_JITTER_DEPTH_RATIO = 0.8


def _arc_positions(rect: Rect, count: int) -> list[float]:
    """楕円を角度で `count` 等分したとき、各点が**一周の何割の位置**か。

    角度そのものではなく、輪郭に沿って測った長さで数える。

    **縦長のフキダシでは角度と長さが大きくずれる。** 角度で膨らみを等分
    すると、上下（曲がりのきつい側）に膨らみが密集し、左右の長い辺が
    のっぺり空く。実物の見た目は長さのほうに従うので、こちらを使う
    （要件定義 6.22）。
    """
    a, b = rect.w / 2.0, rect.h / 2.0
    cumulative = [0.0]
    prev = (a, 0.0)
    for i in range(1, count + 1):
        angle = 2.0 * math.pi * i / count
        point = (a * math.cos(angle), b * math.sin(angle))
        cumulative.append(cumulative[-1] + math.hypot(point[0] - prev[0], point[1] - prev[1]))
        prev = point
    total = cumulative[-1]
    if total <= EPS:
        return [0.0] * count
    return [value / total for value in cumulative[:count]]


def _cloud_lobe_edges(lobes: int, jitter: float, seed: int | None) -> tuple[list[float], list[float]]:
    """膨らみの境目（一周の何割の位置か）と、膨らみごとの深さの倍率。

    ゆらぎは**幅**を変える。高さだけ振っても型で抜いたように見え、
    手描きにならない（描いて確かめた → 要件定義 6.22）。
    """
    if seed is None or jitter <= 0.0:
        return [i / lobes for i in range(lobes + 1)], [1.0] * lobes

    noise = Noise(seed)
    widths = [1.0 + noise.signed() * jitter for _ in range(lobes)]
    scales = [1.0 + noise.signed() * jitter * CLOUD_JITTER_DEPTH_RATIO for _ in range(lobes)]

    total = sum(widths)
    edges = [0.0]
    for width in widths:
        edges.append(edges[-1] + width / total)
    return edges, scales


def cloud_points(
    rect: Rect,
    settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS,
    seed: int | None = None,
) -> tuple[tuple[float, float], ...]:
    """雲（心の声・回想）の輪郭。

    「雲」と呼んでいるが、狙いは綿ではなく**少しモコモコした楕円と楕円の
    つながり**（2026-08-06 に描いて決めた → 要件定義 6.22）。ふつうのセリフや
    つぶやきに使うので、ホラーや不安の側へ寄せない。

    作りはギザギザ・波形と同じで、楕円の半径を増減させる。違うのは3つ。

    1. **膨らみを弧の長さで等間隔に置く**（→ `_arc_positions`）
    2. **山の頂を平たくする**（`sin` を `cloud_roundness` 乗する）。
       頂が広がるぶん膨らみ1つの半径が大きくなり、円弧に近づく
    3. **数を減らす**（既定 5）。増やすと綿へ、減らしすぎると落花生になる

    `seed` を渡すと手描きのゆらぎが乗る（→ `_cloud_lobe_edges`）。
    **同じ種なら必ず同じ形。** フキダシの ID を種にすれば、保存する項目を
    増やさずに開き直しても形が変わらない（→ `balloon_outline`）。
    """
    lobes = max(3, settings.cloud_lobes)
    depth = min(max(settings.cloud_depth, 0.0), 0.9)
    roundness = max(settings.cloud_roundness, 0.05)
    jitter = min(max(settings.cloud_jitter, 0.0), 1.0)

    per_lobe = max(
        CLOUD_MIN_SEGMENTS_PER_LOBE, -(-max(8, settings.ellipse_segments) // lobes)
    )
    count = per_lobe * lobes
    positions = _arc_positions(rect, count)
    edges, scales = _cloud_lobe_edges(lobes, jitter, seed)

    points = []
    lobe = 0
    for i, position in enumerate(positions):
        while lobe + 1 < lobes and position >= edges[lobe + 1]:
            lobe += 1
        width = edges[lobe + 1] - edges[lobe]
        # 膨らみ1つの中での位置（0.0〜1.0）。両端がくびれ、真ん中が頂
        span = min(max((position - edges[lobe]) / width, 0.0), 1.0) if width > EPS else 0.0
        hill = math.sin(math.pi * span) ** roundness
        ratio = 1.0 - min(depth * scales[lobe], 0.9) * (1.0 - hill)
        points.append(_on_ellipse(rect, 2.0 * math.pi * i / count, ratio))
    return tuple(points)


def rect_points(rect: Rect) -> tuple[tuple[float, float], ...]:
    """四角（ナレーション・地の文）の輪郭。

    矩形の4隅そのまま。**角は丸めない。** 丸めると半径の設定項目が増える
    うえ、ナレーションの枠は尖っているほうが普通（要件定義 10.1）。

    他の3種と違って `settings` を見ない。分割数も谷の深さも効く余地が無い。
    """
    return (
        (rect.x, rect.y),
        (rect.right, rect.y),
        (rect.right, rect.bottom),
        (rect.x, rect.bottom),
    )


def balloon_outline(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], ...]:
    """吹き出し本体の輪郭。種類で切り替える。

    **画面・サムネイル・PNG 書き出しの3つがここを通る。** 種類を足すときは
    この分岐にだけ形を足せば、3つとも同時に新しい形になる（→ 6.13）。
    """
    if balloon.style == "jagged":
        return jagged_points(balloon.rect, settings)
    if balloon.style == "spiky":
        return spiky_points(balloon.rect, settings)
    if balloon.style == "wavy":
        return wavy_points(balloon.rect, settings)
    if balloon.style == "cloud":
        # **種はフキダシの ID から作る。** 保存する項目を増やさずに、
        # 開き直しても同じ形へ戻せる（→ `noise.seed_from_text`）。
        # 複製すると ID が変わるのでゆらぎ方も変わるが、手描きとしては
        # そのほうが自然（同じ形が2つ並ばない）
        return cloud_points(balloon.rect, settings, seed_from_text(balloon.id))
    if balloon.style == "rect":
        return rect_points(balloon.rect)
    return ellipse_points(balloon.rect, settings)


def _tail_base_ratio(balloon: BalloonObject, settings: BalloonSettings) -> float:
    """しっぽの付け根を、楕円の何割の位置に置くか。

    輪郭より必ず内側に置く。外側だと、輪郭が凹んでいる箇所（ギザギザの谷、
    波形の谷）で本体と三角形が離れ、継ぎ目に隙間が空く。

    **四角も 0.95 のまま**でよい。四角に内接する楕円はどの向きでも箱の
    内側にあるので、付け根はいつも本体に隠れ、三角形は辺を突き抜けて出る。
    辺に沿った厳密な付け根は作らない——四角はしっぽを消して置くもので、
    出したときに破綻しなければ足りる（要件定義 10.1）。
    """
    if balloon.style == "jagged":
        return 1.0 - min(max(settings.jagged_depth, 0.0), 0.9)
    if balloon.style == "spiky":
        return 1.0 - min(max(settings.spiky_depth, 0.0), 0.9)
    if balloon.style == "wavy":
        return 1.0 - min(max(settings.wavy_depth, 0.0), 0.9)
    if balloon.style == "cloud":
        return 1.0 - cloud_max_depth(settings)
    return 0.95


def cloud_max_depth(settings: BalloonSettings) -> float:
    """雲のくびれが、ゆらぎ込みでいちばん深くなったときの深さ。

    ゆらぎは膨らみごとに深さを振る（→ `_cloud_lobe_edges`）ので、
    設定値そのままではいちばん深い谷に届かない。**しっぽの付け根は
    いちばん深い谷より内側**に置かないと、そこだけ本体と離れて
    継ぎ目に隙間が空く（→ 6.4）。
    """
    depth = min(max(settings.cloud_depth, 0.0), 0.9)
    jitter = min(max(settings.cloud_jitter, 0.0), 1.0)
    return min(depth * (1.0 + jitter * CLOUD_JITTER_DEPTH_RATIO), 0.9)


def _tail_auto_angle(balloon: BalloonObject) -> float | None:
    """先端を向く媒介変数（ラジアン）。決められなければ None。

    楕円の潰れ具合を打ち消してから角度を取る。
    """
    rect = balloon.rect
    if rect.w <= 0.0 or rect.h <= 0.0:
        return None

    cx, cy = rect.center
    dx, dy = balloon.tail.tip[0] - cx, balloon.tail.tip[1] - cy
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None  # 先端が中心に重なっている。向きが決まらない
    return math.atan2(dy / (rect.h / 2.0), dx / (rect.w / 2.0))


def _tail_root_angle(balloon: BalloonObject, root_y: float) -> float | None:
    """`root_y` の高さに付け根を置く媒介変数。**上限をかける前の値。**

    同じ高さに左右2点あるので、先端のある側を選ぶ。反対側から生えると
    しっぽがフキダシを横切る。上端・下端（±1）では左右が一致する。
    """
    if _tail_auto_angle(balloon) is None:
        return None
    angle = math.asin(min(max(root_y, -1.0), 1.0))
    if balloon.tail.tip[0] - balloon.rect.center[0] >= 0.0:
        return angle
    return math.pi - angle


def tail_base_angle(balloon: BalloonObject) -> float | None:
    """付け根を置く媒介変数（ラジアン）。決められなければ None。

    `root_y` が指定されていれば**その高さ**に置く。ただし先端の向きから
    `TAIL_ROOT_MAX_GAP` より離れた指定は、そこで止める。離れるほど
    しっぽが針に痩せて、どのみち指定した場所からは生えないため。

    **上限はここ1箇所でかける。** 画面・サムネイル・PNG 書き出しの3つが
    この関数を通るうえ、`root_y` が飛んだまま保存された作品も、開いた
    時点で正しい形になる。
    """
    auto = _tail_auto_angle(balloon)
    if auto is None or balloon.tail.root_y is None:
        return auto

    wanted = _tail_root_angle(balloon, balloon.tail.root_y)
    # 差を -π〜+π に畳んでから抑える。畳まないと、真上と真下のように
    # 一周を跨ぐ組み合わせで「遠回りの側」を向いてしまう
    gap = (wanted - auto + math.pi) % (2.0 * math.pi) - math.pi
    return auto + min(max(gap, -TAIL_ROOT_MAX_GAP), TAIL_ROOT_MAX_GAP)


def tail_tip_turned_to(
    balloon: BalloonObject, direction: str
) -> tuple[float, float] | None:
    """しっぽを `direction` の向きへ回したときの、**先端の行き先**。

    付け根だけを反対側へ置いても、しっぽは本体に隠れて針になるだけで
    狙った場所からは生えない（→ `TAIL_ROOT_MAX_GAP`）。向きを大きく
    変えるときは先端ごと回す。

    **左右は名前で受け取る**（`TAIL_DIRECTIONS`）。高さ（`root_y`）で
    受け取ると、真横がどちら側になるかを先端の現在位置任せにするしかなく、
    向かい合う2人を左右から指し分けられない。

    楕円の潰れを打ち消した空間で回すので、**しっぽの長さと傾きの具合は
    そのまま残る**。回した先では付け根の高さと先端の向きが一致するので、
    呼ぶ側は `root_y` を自動へ戻してよい。
    """
    auto = _tail_auto_angle(balloon)
    wanted = TAIL_DIRECTIONS.get(direction)
    if auto is None or wanted is None:
        return None

    rect = balloon.rect
    cx, cy = rect.center
    a, b = rect.w / 2.0, rect.h / 2.0
    nx, ny = (balloon.tail.tip[0] - cx) / a, (balloon.tail.tip[1] - cy) / b
    cos, sin = math.cos(wanted - auto), math.sin(wanted - auto)
    return (cx + (nx * cos - ny * sin) * a, cy + (nx * sin + ny * cos) * b)


def tail_root_point(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[float, float] | None:
    """付け根の中心。上下にずらす操作の掴み所として使う。しっぽ無しなら None。

    **消えているしっぽには何も返さない**（`tail_triangle` `tail_bubbles`
    と同じ）。以前はここだけ `enabled` を見ず、呼ぶ側2か所が呼ぶ前に
    確かめることで実害を抑えていたが、**3つめの呼び出し側が確認を忘れると、
    消したはずのしっぽの掴み所が出る**形だった。3つで揃えた（2026-09-06）。
    """
    if not balloon.tail.enabled:
        return None

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


def _tail_base_half_angle(
    rect: Rect, angle: float, ratio: float, width: float
) -> float | None:
    """付け根の幅（px）を、`angle` の位置での半角（ラジアン）に直す。

    楕円は向きによって「輪郭を1ラジアン進んだときの長さ」が違う。半角を
    どの向きでも同じにすると、縦長の吹き出しでは真上・真下だけ幅が半分
    以下に痩せる（実測 333×496 で 51.8px → 23.4px、2026-08-05）。

    **左右（角度0）に見える幅を基準にする。** 均等にする直し方（設定の
    px 値どおりに全方向を揃える）も試したが、それだと左右のしっぽが
    今より細くなってしまう。左右は今のまま、上下だけ太くなる向きに
    寄せてほしいと利用者から指定があった（2026-08-06）。

    左右の半角は `atan2(width/2, a)`（`a` は横方向の半径）。他の向きは、
    「半角 × その向きで進む速さ」＝見える幅が、左右のときと同じになるよう
    半角を決め直す。
    """
    a = rect.w / 2.0 * ratio
    b = rect.h / 2.0 * ratio
    speed = math.hypot(a * math.sin(angle), b * math.cos(angle))
    if speed < 1e-9:
        return None
    reference_half = math.atan2(width / 2.0, a)
    # 小さい吹き出しで付け根が一周しないよう頭を押さえる
    return min(reference_half * b / speed, math.pi / 3.0)


def tail_triangle(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """しっぽの三角形（付け根の2点と先端）。しっぽ無しなら None。

    付け根の幅は `Tail.width`、縦位置は `Tail.root_y`。

    **飛びしっぽでは None を返す**（形が三角ではないため → `tail_bubbles`）。
    呼ぶ側は「三角が無い」と「しっぽが無い」を区別しなくてよい。
    """
    if not balloon.tail.enabled or balloon.tail.shape != TAIL_SHAPE_TRIANGLE:
        return None

    angle = tail_base_angle(balloon)
    if angle is None:
        return None

    rect = balloon.rect
    tip = balloon.tail.tip
    ratio = _tail_base_ratio(balloon, settings)
    half = _tail_base_half_angle(rect, angle, ratio, balloon.tail.width)
    if half is None:
        return None
    return (
        _on_ellipse(rect, angle - half, ratio),
        tip,
        _on_ellipse(rect, angle + half, ratio),
    )


def tail_bubbles(
    balloon: BalloonObject, settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS
) -> tuple[tuple[float, float, float], ...]:
    """丸い飛びしっぽの円（中心x, 中心y, 半径）。三角のしっぽなら空。

    付け根から先端へ向かって、**数を変えずに小さくなる円**を並べる
    （要件定義 10.1）。

    **本体とはくっつけない。** 三角のしっぽは合成して継ぎ目を消すが
    （→ 6.4）、飛びしっぽは離れていること自体が「口に出していない」という
    意味なので、円と円の間にも隙間を空ける。

    起点は**輪郭のいちばん外側**（割合 1.0）。ギザギザや波形は谷で内へ
    へこむが、外側から測っておけば隙間はその分だけ広がるので、
    どの種類でも本体に食い込まない。

    **大きさは、しっぽの長さから決める。** 円の大きさを決め打ちにすると、
    長いしっぽでは隙間だけが広がって鎖に見えず、点が散らばった絵になる。
    さらに、先端を引いて伸ばしても円が動くだけで**引いた量が絵に出ない**。
    隙間を半径に対する割合で持ち、鎖の全長が起点から先端までにちょうど
    収まるよう半径を逆算する（2026-08-05 に描いて決めた → 要件定義 10.1）。

    向きと付け根の高さは三角と同じ計算を通す（`tail_base_angle`）。
    先端のつまみ・付け根のひし形・40度の上限がそのまま効く。
    """
    if not balloon.tail.enabled or balloon.tail.shape == TAIL_SHAPE_TRIANGLE:
        return ()

    angle = tail_base_angle(balloon)
    if angle is None:
        return ()

    start = _on_ellipse(balloon.rect, angle, 1.0)
    dx = balloon.tail.tip[0] - start[0]
    dy = balloon.tail.tip[1] - start[1]
    length = math.hypot(dx, dy)
    if length < EPS:
        return ()  # 先端が輪郭に重なっている。並べる向きが決まらない

    weights, offsets = _tail_bubble_layout()
    radius = length / offsets[-1]
    # 大きくなりすぎないよう頭を押さえる。当たった分だけ鎖は先端に届かない
    radius = min(radius, min(balloon.rect.w, balloon.rect.h) * TAIL_BUBBLE_MAX_RATIO)

    ux, uy = dx / length, dy / length
    return tuple(
        (
            start[0] + ux * offsets[i] * radius,
            start[1] + uy * offsets[i] * radius,
            weights[i] * radius,
        )
        for i in range(TAIL_BUBBLE_COUNT)
    )


def _tail_bubble_layout() -> tuple[tuple[float, ...], tuple[float, ...]]:
    """半径が 1 のときの、各円の大きさと「起点から中心まで」の距離。

    実際の半径を掛ければそのまま使える形にしてある。最後の距離が
    鎖の全長にあたるので、長さをそれで割れば半径が出る（→ `tail_bubbles`）。
    """
    weights = tuple(TAIL_BUBBLE_SHRINK**i for i in range(TAIL_BUBBLE_COUNT))
    gap = 1.0 + TAIL_BUBBLE_GAP_RATIO
    offsets = [weights[0] * gap]
    for i in range(1, TAIL_BUBBLE_COUNT):
        offsets.append(offsets[-1] + weights[i - 1] * gap + weights[i])
    return weights, tuple(offsets)


def tail_body_contains(
    balloon: BalloonObject,
    x: float,
    y: float,
    settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS,
    narrow: float = 1.0,
) -> bool:
    """**見えているしっぽ**の内側を押しているか。形の違いはここで吸収する。

    先端の丸・付け根のひし形は小さく、そこだけしか掴めないと「しっぽの絵は
    見えているのに反応しない」というズレになる（→ `PageView._tail_body_at`）。
    三角と飛びしっぽで2通り書くと、片方を直し忘れて挙動が食い違う。

    `narrow` は見えている形より**細く**取るための倍率（1.0 でそのまま）。
    選ぶときだけ細くする（→ `TAIL_PICK_NARROW`）。ここに1本化してあるので、
    三角と飛びしっぽで細さの入れ忘れが起きない。
    """
    triangle = tail_triangle(balloon, settings)
    if triangle is not None:
        return Polygon(_narrowed_triangle(triangle, narrow)).contains(x, y)
    return any(
        math.hypot(x - cx, y - cy) <= r * narrow
        for cx, cy, r in tail_bubbles(balloon, settings)
    )


def _narrowed_triangle(
    triangle: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
    narrow: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
    """三角のしっぽを、付け根の幅だけ細くする。**長さは変えない。**

    重心へ向けて一様に縮めると先端側も引っ込み、いちばん狙いやすい
    「しっぽの先」が押せなくなる。付け根の2点を付け根の中央へ寄せれば、
    細いまま先端まで届く。
    """
    if narrow >= 1.0:
        return triangle
    left, tip, right = triangle
    mx = (left[0] + right[0]) / 2.0
    my = (left[1] + right[1]) / 2.0
    return (
        (mx + (left[0] - mx) * narrow, my + (left[1] - my) * narrow),
        tip,
        (mx + (right[0] - mx) * narrow, my + (right[1] - my) * narrow),
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
    """吹き出しの本体の内側か。

    **丸い種類は外接矩形ではなく楕円で判定する。** 矩形だと四隅の何もない
    場所で掴めてしまい、下のコマが選べなくなる（要件定義 6.4）。

    **四角だけは矩形で判定する。** 同じ理屈がそのまま裏返り、楕円のままだと
    見えている箱の四隅が判定から漏れて、押しても下のものが選ばれる。
    形が矩形なので、ここでは四隅も「何かある場所」になる。
    """
    rect = balloon.rect
    if rect.w <= 0.0 or rect.h <= 0.0:
        return False
    if balloon.style == "rect":
        return rect.contains(x, y)
    cx, cy = rect.center
    nx = (x - cx) / (rect.w / 2.0)
    ny = (y - cy) / (rect.h / 2.0)
    return nx * nx + ny * ny <= 1.0


def balloon_at(page: Page, x: float, y: float) -> BalloonObject | None:
    """その位置にある吹き出し。重なっていれば手前のものを返す。

    **本体だけを見る。しっぽは含まない。** セリフの紐づけ先を決めるのに
    使うので（→ `EditorState.add_text`）、しっぽの上に置いた文字まで
    その吹き出しの中身として扱われては困る。

    選ぶときは代わりに `balloon_pick_at` を使う。
    """
    balloons = [f for f in page.floating if isinstance(f, BalloonObject)]
    for balloon in reversed(sorted(balloons, key=lambda b: b.z)):
        if balloon_contains(balloon, x, y):
            return balloon
    return None


def balloon_pick_at(
    page: Page,
    x: float,
    y: float,
    settings: BalloonSettings = DEFAULT_BALLOON_SETTINGS,
) -> BalloonObject | None:
    """**選ぶとき**にその位置で拾う吹き出し。本体に加えてしっぽも見る。

    しっぽを押しても選べないと、「絵が見えているのに反応しない」場所が
    残る。選んでいない吹き出しでも、しっぽを押せばそれが選ばれる
    （本人の指定 2026-08-07）。

    しっぽの判定は見えている形より細い（→ `TAIL_PICK_NARROW`）。しっぽは
    本体の外へ長く伸びるので、そのままの太さだと下のコマや画像を覆う。

    本体としっぽを**吹き出し1つぶんずつまとめて**見ること。先に全部の
    本体を見てからしっぽを見る書き方だと、手前の吹き出しのしっぽより
    奥の吹き出しの本体が勝ってしまい、描いてある前後と食い違う。
    """
    balloons = [f for f in page.floating if isinstance(f, BalloonObject)]
    for balloon in reversed(sorted(balloons, key=lambda b: b.z)):
        if balloon_contains(balloon, x, y):
            return balloon
        if tail_body_contains(balloon, x, y, settings, TAIL_PICK_NARROW):
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


def image_pixel_at(
    image: ImageObject, x: float, y: float
) -> tuple[int, int] | None:
    """ページ座標の1点が、その絵の**元画像の何画素目**に当たるか。外なら None。

    切り抜き（→ 要件定義 10.3）で、押した所を元画像の座標へ翻訳するために使う。
    マスクは元画像の画素に結び付いているので、**画面の見え方（拡大縮小・回転・
    コマでの切り抜き）をすべて剥がしてから**でないと指せない。

    **傾きは `unrotate_point` で戻す**（回転を持ち込む境目は3か所だけ → 6.3）。
    戻したあとは、矩形の中の割合を出して元画像の画素数に掛けるだけ。

    縁は内側へ丸める。割合がちょうど 1.0 になる右端・下端をそのまま掛けると
    画素数と同じ値になり、1つ外を指す。
    """
    px, py = image.src_px
    if px <= 0 or py <= 0 or image.rect.w <= 0 or image.rect.h <= 0:
        return None

    lx, ly = unrotate_point(x, y, image.rect, image.rotation)
    u = (lx - image.rect.x) / image.rect.w
    v = (ly - image.rect.y) / image.rect.h
    if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
        return None
    return (min(px - 1, int(u * px)), min(py - 1, int(v * py)))


def sticker_at(page: Page, x: float, y: float) -> StickerObject | None:
    """その位置にあるマーク。重なっていれば手前のものを返す。

    **矩形で判定する。** 素材は透明な余白を削ってあるので、記号と矩形の
    ずれは傾いた記号の四隅くらいしか残らない。透明度を見る判定は、
    掴みにくさが実際に出てから入れる（要件定義 6.14）。
    """
    stickers = [f for f in page.floating if isinstance(f, StickerObject)]
    for sticker in reversed(sorted(stickers, key=lambda s: s.z)):
        if sticker.rect.contains(x, y):
            return sticker
    return None


def text_ink_bands(text: TextObject) -> list[Rect]:
    """セリフのうち、**字が並んでいる範囲**。空なら枠そのもの。

    枠は既定で 230×422 あるが、字が数文字ならその大半は空いている。
    空いた場所まで拾うと、そこに置いたマークが**見えているのに掴めない**
    （2026-08-05 の不具合）。吹き出しを外接矩形ではなく楕円で判定して
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


def text_frame(text: TextObject) -> Rect:
    """セリフの選択枠とつまみを置く矩形。**字の並びの外接矩形**。

    枠（`TextObject.rect`）そのものを使わないのは、**掴める範囲が既に枠では
    ないため**（→ `text_ink_bands`、`text_at`）。既定の枠は 230×422 あり、
    数文字のセリフではその大半が空く。そこに枠を描くと、**押しても掴めない
    場所まで枠が伸びる**ことになり、「どこを掴んでいるのか分からない」という
    指摘（本人談 2026-08-07）そのものになる。描く範囲と掴める範囲は揃える。

    **この矩形を大きさ変更でそのまま枠に入れてよい。** 字の並びは枠の中で
    寄せに従って置かれるので（`vertical.layout` / 上下中央の `drawText`）、
    外接矩形へ入れ直しても字は 1px も動かない——寄せが left でも right でも
    center でも、外接矩形の中で同じ位置に来る。**2 回目以降も動かない**
    （外接矩形の外接矩形は自分自身）ので、掴み直すたびに縮み続けることもない。

    横書きは**高さだけが縮む**。字送りが分からず幅を出せないためで、帯の幅は
    枠のまま（→ `text_ink_bands`）。既定の枠は縦長で余りもほとんどが上下に
    出るので、これで用は足りる。
    """
    bands = text_ink_bands(text)
    left = min(band.x for band in bands)
    top = min(band.y for band in bands)
    right = max(band.right for band in bands)
    bottom = max(band.bottom for band in bands)
    return Rect(left, top, right - left, bottom - top)


def text_contains(text: TextObject, x: float, y: float) -> bool:
    """その点がセリフの字の上か（→ `text_ink_bands`）。"""
    return any(band.contains(x, y) for band in text_ink_bands(text))


def text_at(page: Page, x: float, y: float) -> TextObject | None:
    """その位置にあるセリフ。重なっていれば手前のものを返す。

    **枠の矩形ではなく、字の並んでいる範囲で判定する**（→ `text_ink_bands`）。
    """
    texts = [f for f in page.floating if isinstance(f, TextObject)]
    for text in reversed(sorted(texts, key=lambda t: t.z)):
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
