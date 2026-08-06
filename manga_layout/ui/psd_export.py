"""ページを PSD のレイヤーへ分解する（要件定義 10.1）。

**形式の決まりは `manga_layout.psd` が持ち、ここは「何をレイヤーにするか」
だけを決める。** 分けておくと、PSD の細かい決まりと、この道具にとって
何が1枚かの判断が混ざらない。

## 構成（下が奥）

    セリフ / マーク / フキダシ
    [フォルダ] コマN ── 絵・トーン範囲（非表示）・集中線と流線・コマ枠
      …（コマの数だけ）
    ラフ / 用紙

フォルダの並びは**重なっていないページなら読み順**（一覧の上から コマ1、
コマ2……）。重なっているページだけ重なり順を保つ（→ `_stacking`）。

並びは `render.PageRenderer.draw` の描く順そのまま。**ここで独自の順を
持たない。** 持つと、画面と書き出しで重なりが食い違ったときに、どちらが
正なのか決める相手がいなくなる。

## コマをフォルダにまとめる理由（要件定義 10.1 の第2段階）

第1段階では枠線・集中線・絵を種類ごとに1枚ずつまとめていた。すると
**枠線が全部の絵より手前**に来るので、コマを重ねたページで**下のコマの
枠線が上のコマの絵を貫いて出た**。

コマごとのフォルダにすれば、上のコマの絵が下のコマの枠線より手前に来る。
**隠れる線はレイヤーの重なりが自動で隠す**ので、幾何の計算は要らない。
6.24 が持っている重なり順をそのまま並びに移すだけ。

フキダシ・マーク・セリフは**これまでどおり種類ごと1枚**。ページ直下に
あってコマの子ではない（→ 4章）ので、コマに紐づけようがない。

## トーンの範囲は、絵のすぐ上に非表示で入れる（要件定義 6.28）

クリスタのトーンは種類がはるかに多く、こだわるなら結局そちらで貼り直す
ことになる。**貼る場所を手で選び直さずに済むよう、マスクをそのまま
1枚のレイヤーとして渡す**（→ `ToneMasks`）。「レイヤーから選択範囲」で
そのまま選択範囲にできる。

- **非表示。** 作品の中身ではない（ラフと同じ扱い）。合成済みの1枚には
  入らないので、**PNG 書き出しと1画素も違わない**まま
- **絵のすぐ上。** ここを選んだまま新しいレイヤーを作れば、集中線とコマ枠の
  **下**に入る。一番上に置くと、貼ったトーンが枠線を覆う
- トーンの入った絵が1枚も無いコマには出さない（下の「中身の無いレイヤー」）

## 中身の無いレイヤー・フォルダは出さない

フキダシを1つも置いていないページに空の「フキダシ」レイヤーを残しても、
クリスタ側では邪魔になるだけ。中身の無いコマも、フォルダごと出さない。

## 合成済みの1枚はレイヤーから作る

`render_page` を呼び直さない。**ファイルの中で食い違わない**うえ、同じ絵を
2回展開せずに済む（→ `flatten`）。
"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPainterPathStroker

from ..images import ImageCache, Preview, full_from_bytes, full_rough_from_bytes
from ..model import BalloonObject, Page, Panel, StickerObject, TextObject
from ..psd import PsdGroup, PsdLayer, crop_to_content, write_psd
from ..tone import mask_silhouette
from .export import (
    DEFAULT_SCALE,
    FullImages,
    checked_page_px,
    export_dpi,
    page_filename,
)
from .render import PageRenderer, polygon_of

#: レイヤーの名前と、古い名前欄に入れる英字（→ `psd.PsdLayer`）。
#: **奥から手前の順**に並べてある
PAPER = ("用紙", "paper")
ROUGH = ("ラフ", "rough")
ART = ("絵", "art")
TONE_MASK = ("トーン範囲", "tonemask")
EFFECTS = ("集中線・流線", "effects")
FRAMES = ("コマ枠", "frames")
BALLOONS = ("フキダシ", "balloons")
MARKS = ("マーク", "marks")
TEXTS = ("セリフ", "text")

PSD_FORMAT = "PSD"


class FullRoughs:
    """書き出しのあいだだけ原寸のラフを持つ置き場。

    `FullImages`（→ `export`）のラフ版。画面用は長辺 1,600px に縮めて
    あるが、**PSD のラフはクリスタでなぞる相手**なので、縮めたものを
    引き伸ばして入れると入れた意味が薄れる。

    青く染めるかどうかで入れ物を分けるのは `EditorState.rough_preview`
    と同じ理由（染めていないほうは普通の画像と変わらない）。
    """

    def __init__(self, state) -> None:
        self.state = state
        self._plain = ImageCache(full_from_bytes)
        self._blue = ImageCache(full_rough_from_bytes)

    def __call__(self, ref: str, faded: bool) -> Preview | None:
        cache = self._blue if faded else self._plain
        return cache.get(ref, lambda: self.state.read_asset(ref))


class ToneMasks:
    """トーンにする所だけを黒く塗った1枚を返す置き場（→ 要件定義 6.28）。

    **`PageRenderer` の画像を引く経路をそのまま差し替える。** こうすると
    コマの形での切り抜き・画像の位置・回転が絵と完全に同じになる。
    別に描くと、斜めのコマや回した絵でマスクだけがずれる。

    **トーンの入っていない絵には `None` を返す。** 描く側は「その場所に
    何も描かない」で進む（`aids=False` なので欠けた画像の目印も出ない）。
    ページのどのコマにもトーンが無ければ、レイヤーごと出ない。

    絵は `FullImages` と**同じ入れ物から引く**（`base`）。原寸から焼くのは
    トーンを焼き直すのと同じ理由——縮小版から作ったマスクを引き伸ばすと、
    縁が階段状になったまま貼ることになる。
    """

    def __init__(self, images: FullImages) -> None:
        self._images = images
        self._masks: dict[tuple[str, tuple], Preview] = {}

    def __call__(self, image) -> Preview | None:
        tone = getattr(image, "tone", None)
        if tone is None:
            return None
        ref = image.asset
        key = (ref, tone.key())
        found = self._masks.get(key)
        if found is not None:
            return found

        source = self._images.base(ref)
        if source is None:
            return None
        made = Preview(
            image=mask_silhouette(source.image, tone), source_px=source.source_px
        )
        # **上限を持たない**（`ToneCache` と違う）。1ページ書き出すあいだ
        # だけの入れ物で、鍵は貼ってある絵の数で頭打ちになる
        self._masks[key] = made
        return made


def reading_order(panels: list[Panel], gutter: float) -> list[Panel]:
    """読み順（上から下・右から左）に並べた一覧。

    **フォルダに振る番号を決めるためだけに使う。** このアプリはコマに
    番号を持っておらず（→ 要件定義 10.1）、ここで初めて振る。

    段の切れ目は**上端の差が隙間（`gutter`）より小さいかどうか**で見る。
    同じ段に並べたコマは上端が揃っているのが普通で、揃っていない縦の
    ずれは隙間より大きい。

    **番号はラベルでしかない。** 変わったコマ割りで直感と合わないことは
    ありうるが、中身が入れ替わるわけではない。
    """
    rows: list[tuple[float, list[Panel]]] = []
    for panel in sorted(panels, key=lambda p: p.bounds().y):
        top = panel.bounds().y
        if rows and top - rows[-1][0] < gutter:
            rows[-1][1].append(panel)
        else:
            rows.append((top, [panel]))

    ordered: list[Panel] = []
    for _top, row in rows:
        ordered.extend(sorted(row, key=lambda p: -p.bounds().x))
    return ordered


def _inked(panel: Panel) -> QPainterPath:
    """そのコマが1画素でも触りうる範囲。

    形そのものではなく、**枠線の太さぶん外へ膨らませた**もの。枠線は
    形の線の上に中心を置いて引かれるので、太さの半分だけ外へはみ出す。
    形だけで見ると、隣り合っただけのコマが「触れていない」ことになる。
    """
    path = QPainterPath()
    path.addPolygon(polygon_of(panel.shape.points))
    path.closeSubpath()
    if not panel.border.visible or panel.border.width <= 0:
        return path
    stroker = QPainterPathStroker()
    stroker.setWidth(panel.border.width)
    return path.united(stroker.createStroke(path))


def panels_overlap(panels: list[Panel]) -> bool:
    """コマ同士が1画素でも重なるか。

    **重ならないなら、コマの前後はどう並べても絵が変わらない。**
    フォルダを読み順に並べ替えてよいかの判断に使う（→ 要件定義 10.1）。

    枚数は多くて数十なので、総当たりで足りる。
    """
    inked = [_inked(panel) for panel in panels]
    for i, first in enumerate(inked):
        for second in inked[i + 1 :]:
            if first.intersects(second):
                return True
    return False


def page_layers(
    state, page: Page, scale: float = DEFAULT_SCALE
) -> list[PsdLayer | PsdGroup]:
    """1ページぶんのレイヤーとフォルダ。**下から上の順**で返す。

    画像を引く経路は1ページで1つだけ作って使い回す。コマごとに作ると、
    同じ絵を何度も展開し直すことになる（同じ画像を2コマで使える）。
    """
    width, height = checked_page_px(page, scale)
    images = FullImages(state)
    renderer = PageRenderer(state, images, aids=False)
    # トーンの範囲だけを描く写し。**描く手順は同じで、引く絵だけが違う**
    # （→ `ToneMasks`）
    masks = PageRenderer(state, ToneMasks(images), aids=False)
    roughs = FullRoughs(state)

    def build(label, draw, visible: bool = True) -> PsdLayer | None:
        return _build(label, draw, page, width, height, visible)

    items: list[PsdLayer | PsdGroup] = [
        build(PAPER, lambda p: renderer.draw_paper(p, page, shadow=False, edge=False)),
        # ラフは**非表示**で入れる（→ 要件定義 10.1）。なぞる相手であって
        # 作品の中身ではないので、開いた直後に見えていては困る
        build(ROUGH, lambda p: renderer.draw_rough(p, page, images=roughs), False),
    ]

    ordered = reading_order(page.panels, state.settings.gutter)
    numbers = {panel.id: i + 1 for i, panel in enumerate(ordered)}
    for panel in _stacking(page, ordered):
        items.append(
            _panel_group(renderer, masks, page, panel, numbers[panel.id], build)
        )

    items += [
        build(BALLOONS, lambda p: renderer.draw_floating(p, page, kinds=(BalloonObject,))),
        build(MARKS, lambda p: renderer.draw_floating(p, page, kinds=(StickerObject,))),
        build(TEXTS, lambda p: renderer.draw_floating(p, page, kinds=(TextObject,))),
    ]
    return [item for item in items if item is not None]


def _stacking(page: Page, ordered: list[Panel]) -> list[Panel]:
    """フォルダを並べる順（下から上）。

    **重なっていないページでは読み順に並べる。** PSD には並び順と重なり順の
    区別が無く、一覧の並びがそのまま重なり順になる。だが**コマが重ならない
    なら前後は絵に出ない**ので、一覧で探しやすい向き——上から コマ1、コマ2
    ——にできる（→ 要件定義 10.1）。一覧は上が手前なので、下から上へは
    読み順の逆に積む。

    **1組でも重なっていれば重なり順のまま。** そちらでは並びが絵に出る。
    読みやすさのために、下のコマの枠線が上のコマの絵を貫く状態へ戻したら
    本末転倒（第2段階でそれを直したところ → 6.28）。
    """
    if panels_overlap(page.panels):
        return sorted(page.panels, key=lambda p: p.z)
    return list(reversed(ordered))


def _panel_group(
    renderer: PageRenderer, masks: PageRenderer, page: Page, panel: Panel,
    number: int, build
) -> PsdGroup | None:
    """コマ1つぶんのフォルダ。中身が1つも無ければ None。

    中身は**奥から手前**（絵 → トーン範囲 → 集中線・流線 → コマ枠）。
    `draw_panel` が描く順そのままで、ここでも独自の順は持たない。

    **トーン範囲だけが非表示**で、しかも絵と同じ「中身」を別の絵で描いた
    もの（→ `ToneMasks`）。置き場所を絵のすぐ上にした理由はモジュールの
    冒頭にある。
    """
    art = {"contents": True, "effects": False, "border": False}
    plan = [
        (ART, renderer, art, True),
        (TONE_MASK, masks, art, False),
        (EFFECTS, renderer, {"contents": False, "effects": True, "border": False}, True),
        (FRAMES, renderer, {"contents": False, "effects": False, "border": True}, True),
    ]
    children = []
    for label, draws, parts, visible in plan:
        layer = build(
            label,
            lambda p, draws=draws, parts=parts: draws.draw_panel(p, page, panel, **parts),
            visible,
        )
        if layer is not None:
            children.append(layer)
    if not children:
        return None
    return PsdGroup(name=f"コマ{number}", alias=f"panel{number}", children=children)


def _build(
    label: tuple[str, str],
    draw,
    page: Page,
    width: int,
    height: int,
    visible: bool,
) -> PsdLayer | None:
    """1枚ぶん描いて、透明な縁を落とす。何も描かれなければ None。

    描く前の下ごしらえ（描画の質・倍率）は `export.render_page` と
    同じにする。違えると、レイヤーを重ねた結果が PNG と食い違う。

    **透明な紙の上に描く。** `render_page` が用紙の白で塗るのに当たる
    ものはここには無く、白は「用紙」レイヤーが受け持つ。

    **形式も `render_page` と同じ `Format_ARGB32` にする。**
    `_Premultiplied` にすると、**セリフだけが 2px ずれて描かれる**
    （→ [PySide6の落とし穴.md](../../PySide6の落とし穴.md) の 4）。
    塗りつぶしの色とは関係なく形式だけで決まるので、透明な紙に描いている
    こととは無関係。
    """
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(width / page.size.w, height / page.size.h)
    draw(painter)
    painter.end()

    cropped = crop_to_content(canvas)
    if cropped is None:
        return None
    image, x, y = cropped
    name, alias = label
    return PsdLayer(name=name, alias=alias, image=image, x=x, y=y, visible=visible)


def flatten(
    layers: list[PsdLayer | PsdGroup], width: int, height: int
) -> QImage:
    """レイヤーを下から重ねた1枚。

    PSD には合成済みのものも入れる決まりで、**これが無いと開けない
    ソフトがある**（→ `psd.psd_bytes`）。`render_page` をもう一度
    呼ばずにここで作るのは、同じ絵を2回展開しないため——と、
    **ファイルの中で食い違わないようにする**ため。

    非表示のレイヤー（ラフ）は重ねない。開いた直後の見た目に合わせる。

    形式は `render_page` と揃える（`_build` と同じ理由）。
    """
    out = QImage(width, height, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    _paint(painter, layers)
    painter.end()
    return out


def _paint(painter: QPainter, items: list[PsdLayer | PsdGroup]) -> None:
    """下から順に重ねる。フォルダは中身をそのまま続けて描く。

    **フォルダの不透明度は掛けない。** PSD では中身を合成してから
    フォルダの不透明度が掛かるが、この道具は 1.0 しか使わないので
    区別が出ない。使うようになったら、フォルダごとに別の1枚へ描いてから
    重ねる形が要る。
    """
    for item in items:
        if not item.visible:
            continue
        if isinstance(item, PsdGroup):
            _paint(painter, item.children)
            continue
        painter.setOpacity(item.opacity)
        painter.drawImage(item.x, item.y, item.image)


def export_psd_pages(
    state,
    indexes,
    dest: pathlib.Path,
    scale: float = DEFAULT_SCALE,
) -> list[pathlib.Path]:
    """指定したページを PSD にする。書いたファイルの一覧を返す。

    1ページ1ファイル。途中で失敗したらそこで止める（`export_pages` と同じ）。
    """
    total = state.page_count
    written: list[pathlib.Path] = []
    for i in indexes:
        page = state.project.pages[i]
        width, height = checked_page_px(page, scale)
        layers = page_layers(state, page, scale)
        path = dest / page_filename(i, total, PSD_FORMAT)
        write_psd(path, layers, flatten(layers, width, height), export_dpi(scale))
        written.append(path)
    return written
