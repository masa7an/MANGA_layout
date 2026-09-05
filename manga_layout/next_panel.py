"""ページと定石の橋渡し（→ `joseki.py`）。

定石は**正規化した矩形しか知らない**。食い違いはここで吸収する。

  座標    px ←→ ページ幅・高さを 1.0 とした値
  読み順  `page.panels` は**重なり順**であって読む順ではない。ここで並べ直す
  文脈    ページ番号と直前のページ（見開きの定石が使う）
  反映    提案の矩形からコマを作る

**画面を知らない。** 提案を作るところまでが仕事で、いつ押されたか・どう見せるかは
`ui/` の話（`layout.py` を `ui/` から切り離してあるのと同じ切り方）。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Collection
from dataclasses import dataclass, field

from .geometry import Rect
from .joseki import NBox, PageContext, PreviousPage, match_all, proposals
from .model import Page, Panel, Project

# 断ち切りでページの端を越える量（px）。**線が出ない分だけあればよい。**
# 多く出しても仕上がりは変わらず、画面では紙からはみ出して見えるだけになる
BLEED_OVERFLOW = 10.0


# --------------------------------------------------------------------------
# 読み順
# --------------------------------------------------------------------------
#
# **`ui/psd_export.py` の `reading_order()` とは別物。** あちらは PSD のフォルダに
# 振る番号を決めるためのもので、行優先で固定されている。こちらは提案のための順で、
# **縦4コマ列のページを列優先で読む**必要がある（4コマ2列の8コマページは、右列を
# 上から下、続いて左列を上から下に読む。慣習で決まっており、迷う余地は無い）。
#
# あちらを直さないのは、**PSD の出力が変わるから。** 目的が違うものを1つにしない。
#
# **答えも食い違う。段の切り方が違うため。** こちらは「縦に重なるコマは同じ段」、
# あちらは「上端の差が隙間より小さいコマは同じ段」。段の高さが揃わないページで
# 別の答えになる（コマ割りを機械的に 400通り作って照合したところ 125通りで不一致。
# 4コマ2列のページは必ず不一致。2026-09-05 実測）。
#
# **利用者向けの周知は 要件定義 10.5 と README にある。** 揃えると PSD の出力が
# 約3割のページで変わるので、周知にとどめると決めた（本人判断・2026-09-05）。

# 「縦に並ぶ列」とみなす最小のコマ数。**4コマ用の特殊な読み方なので、3段には広げない。**
# 3段で左右の列がぴったり揃ったページは、ふつうの漫画のページとして行優先で読む。
MIN_PER_COLUMN = 4


def _bands(panels: list[Panel]) -> list[list[Panel]]:
    """段に分ける。**同じ段のコマは必ず縦に重なる**ので、重なりだけで決まる。

    **基準のコマは必ず自分の段に入れる**（`p is head`）。重なりの判定は
    「上端が相手の下端より上」で見るので、**高さ 0 のコマは自分自身とすら重ならない。**
    入れずに書くと段が空になり、`rest` が減らずにここで永久に回る（実際に固まった）。
    """
    rest, bands = list(panels), []
    while rest:
        head = min(rest, key=lambda p: p.bounds().y)
        top, bottom = head.bounds().y, head.bounds().bottom
        band = [
            p for p in rest
            if p is head or (p.bounds().y < bottom and p.bounds().bottom > top)
        ]
        bands.append(sorted(band, key=lambda p: -p.bounds().right))   # 右から左へ
        rest = [p for p in rest if p not in band]
    return bands


def _columns(panels: list[Panel]) -> list[list[Panel]]:
    """**ページを縦に切れる位置**で分ける。切れ目が1つも無ければ1つのまま。

    「横に重なるコマを集める」ではなく、**ページを貫く縦の切れ目**で分ける。
    集める側にすると、段ごとに幅が違うだけのふつうのページまで列に見えてしまう。
    """
    ordered = sorted(panels, key=lambda p: p.bounds().x)
    groups: list[list[Panel]] = [[ordered[0]]]
    edge = ordered[0].bounds().right
    for panel in ordered[1:]:
        box = panel.bounds()
        if box.x > edge:            # ここで縦に切れる
            groups.append([panel])
            edge = box.right
        else:
            groups[-1].append(panel)
            edge = max(edge, box.right)
    return [sorted(g, key=lambda p: p.bounds().y) for g in groups]   # 上から下へ


def reading_order(panels: list[Panel]) -> list[Panel]:
    """読み順に並べた一覧。

    **ページが縦に切れて、どの列も4個以上**なら列優先（右列を上から下、続いて左列）。
    それ以外は行優先（上から下・右から左）。**列優先は4コマのページだけの読み方。**
    """
    if not panels:
        return []
    columns = _columns(panels)
    if len(columns) >= 2 and all(len(c) >= MIN_PER_COLUMN for c in columns):
        ordered: list[Panel] = []
        for column in sorted(columns, key=lambda c: -c[0].bounds().right):
            ordered.extend(column)
        return ordered
    return [panel for band in _bands(panels) for panel in band]


# --------------------------------------------------------------------------
# 座標とページの文脈
# --------------------------------------------------------------------------


def is_rectangular(panel: Panel) -> bool:
    """そのコマが（傾いていない）矩形か。**斜めに割ったコマは False。**"""
    points = panel.shape.points
    if len(points) != 4:
        return False
    box = panel.bounds()
    corners = {
        (round(box.x), round(box.y)),
        (round(box.right), round(box.y)),
        (round(box.x), round(box.bottom)),
        (round(box.right), round(box.bottom)),
    }
    return {(round(x), round(y)) for x, y in points} == corners


def supported(page: Page) -> bool:
    """そのページに提案を出せるか。**斜めのコマがあるページは対象外。**

    定石は矩形を前提にしている。`layout.split_panel` が斜めのコマを断るのと同じ線引き。
    """
    return all(is_rectangular(p) for p in page.panels)


def to_boxes(page: Page, ignore: Collection[str] = ()) -> list[NBox]:
    """ページのコマを、読み順に並べた正規化座標にする。

    `ignore` に id を渡すと、そのコマは**無かったことにして**数える。
    直前の提案を次の案へ差し替えるとき、**自分が足したコマを材料に混ぜない**ために使う。
    """
    size = page.size
    panels = [p for p in page.panels if p.id not in ignore]
    return [
        NBox(
            box.x / size.w,
            box.y / size.h,
            box.w / size.w,
            box.h / size.h,
        )
        for box in (p.bounds() for p in reading_order(panels))
    ]


def to_rect(box: NBox, page: Page) -> Rect:
    """正規化座標を、そのページの px に戻す。"""
    size = page.size
    return Rect(box.x * size.w, box.y * size.h, box.w * size.w, box.h * size.h)


def guide_frame(page: Page, margin: float) -> NBox | None:
    """基本枠（`LayoutSettings.margin` の内側）を正規化座標で。**無ければ None。**

    **px の余白は、正規化すると縦横で値が変わる**（A4 なら 89px が横 0.072・縦 0.051）。
    ここで換算しておかないと、空白ページに置くコマが枠からはみ出す。

    **余白が入り切らないページでは None を返す。** ページの大きさは 50px まで
    下げられる（`ui/pages.PAGE_SIZE_MIN_PX`）のに余白は既定 89px なので、
    **左右の余白だけでページ幅を超える**ことがある。そのまま作ると幅も高さも
    負の枠になり、**裏返った枠を「置いてよい範囲」として配り歩く**ことになる
    （150x150px で幅 -0.187 の枠を返していた。2026-09-05 修正）。

    **無いことにするのは、間違った枠を渡すよりよい。** 受け取る側は枠が無ければ
    既定の余白へ落ちる（→ `joseki.DEFAULT_MARGIN`）。
    """
    if margin <= 0:
        return None
    size = page.size
    x, y = margin / size.w, margin / size.h
    width, height = 1.0 - x * 2, 1.0 - y * 2
    if width <= 0 or height <= 0:
        return None
    return NBox(x, y, width, height)


def context_for(
    project: Project, page: Page, margin: float = 0.0, gutter: float = 0.0
) -> PageContext:
    """ページ番号・直前のページ・基本枠・コマの隙間。

    **見開きの定石は直前のページが無いと成立しない。**
    `margin` と `gutter` は px（`LayoutSettings`）。**縦横で別の比になる**ので、
    ここでそれぞれ換算して渡す。
    """
    index = project.pages.index(page)
    previous = None
    if index > 0:
        before = project.pages[index - 1]
        previous = PreviousPage(number=index, boxes=to_boxes(before))
    size = page.size
    return PageContext(
        number=index + 1,
        previous=previous,
        frame=guide_frame(page, margin),
        gutter_x=gutter / size.w if gutter > 0 else None,
        gutter_y=gutter / size.h if gutter > 0 else None,
        bleed_x=BLEED_OVERFLOW / size.w,
        bleed_y=BLEED_OVERFLOW / size.h,
    )


# --------------------------------------------------------------------------
# 提案
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    """提案1件。**コマ1枚以上でひとまとまり。**

    同じ定石が幅ちがいの案を複数出すので、`label` で見分ける。
    **案どうしは重なる**ので、まとめて置いてはいけない（1件ずつ見せる）。
    """

    title: str
    label: str
    rects: list[Rect] = field(default_factory=list)

    def text(self) -> str:
        """画面に出す1行。"""
        return f"{self.title} / {self.label}" if self.label else self.title


def suggestions(
    project: Project,
    page: Page,
    ignore: Collection[str] = (),
    margin: float = 0.0,
    gutter: float = 0.0,
) -> list[Suggestion]:
    """そのページへの提案を、**出す順に全部**返す。

    **1つに絞らない。** どれが物語に合うかは幾何では決まらないので、ここでは決めない。
    【提案】を押すたびに、この並びを順繰りに見せる。

    `ignore` は「無かったことにするコマ」の id。**直前の提案を差し替えるときに使う。**
    `margin`・`gutter` は px（`LayoutSettings`）。**空白ページの置き場所に効く**
    （既にコマがあるページでは、そのコマから借りるので使わない）。
    """
    if not supported(page):
        return []
    boxes = to_boxes(page, ignore)
    ctx = context_for(project, page, margin, gutter)
    return [
        Suggestion(
            title=match.title,
            label=plan.label,
            rects=[to_rect(c.box, page) for c in plan.candidates],
        )
        for match, plan in proposals(match_all(boxes, ctx))
    ]


def add_suggestion(project: Project, page: Page, suggestion: Suggestion) -> list[Panel]:
    """提案どおりにコマを足す。足したコマを返す。

    枠線は**既にあるコマから写す**（`layout.split_panel` が分割で写すのと同じ）。
    ページに1枚も無ければ、`Panel` の既定のまま。
    """
    source = page.panels[0].border if page.panels else None
    added = []
    for rect in suggestion.rects:
        panel = project.add_panel(page, rect)
        if source is not None:
            panel.border = dataclasses.replace(source)
        added.append(panel)
    return added
