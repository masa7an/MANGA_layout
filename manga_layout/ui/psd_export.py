"""ページを PSD のレイヤーへ分解する（要件定義 10.1）。

**形式の決まりは `manga_layout.psd` が持ち、ここは「何をレイヤーにするか」
だけを決める。** 分けておくと、PSD の細かい決まりと、この道具にとって
何が1枚かの判断が混ざらない。

## 構成（下が奥）

    セリフ / マーク / フキダシ
    [フォルダ] コマN ── 絵・集中線と流線・コマ枠
      …（コマの数だけ、奥から手前の順に）
    ラフ / 用紙

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
from PySide6.QtGui import QImage, QPainter

from ..images import ImageCache, Preview, full_from_bytes, full_rough_from_bytes
from ..model import BalloonObject, Page, Panel, StickerObject, TextObject
from ..psd import PsdGroup, PsdLayer, crop_to_content, write_psd
from .export import (
    DEFAULT_SCALE,
    FullImages,
    checked_page_px,
    export_dpi,
    page_filename,
)
from .render import PageRenderer

#: レイヤーの名前と、古い名前欄に入れる英字（→ `psd.PsdLayer`）。
#: **奥から手前の順**に並べてある
PAPER = ("用紙", "paper")
ROUGH = ("ラフ", "rough")
ART = ("絵", "art")
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


def page_layers(
    state, page: Page, scale: float = DEFAULT_SCALE
) -> list[PsdLayer | PsdGroup]:
    """1ページぶんのレイヤーとフォルダ。**下から上の順**で返す。

    画像を引く経路は1ページで1つだけ作って使い回す。コマごとに作ると、
    同じ絵を何度も展開し直すことになる（同じ画像を2コマで使える）。
    """
    width, height = checked_page_px(page, scale)
    renderer = PageRenderer(state, FullImages(state), aids=False)
    roughs = FullRoughs(state)

    def build(label, draw, visible: bool = True) -> PsdLayer | None:
        return _build(label, draw, page, width, height, visible)

    items: list[PsdLayer | PsdGroup] = [
        build(PAPER, lambda p: renderer.draw_paper(p, page, shadow=False, edge=False)),
        # ラフは**非表示**で入れる（→ 要件定義 10.1）。なぞる相手であって
        # 作品の中身ではないので、開いた直後に見えていては困る
        build(ROUGH, lambda p: renderer.draw_rough(p, page, images=roughs), False),
    ]

    numbers = {
        panel.id: i + 1
        for i, panel in enumerate(reading_order(page.panels, state.settings.gutter))
    }
    # 並べる順は**読み順ではなく重なり順**（奥から手前）。読み順は
    # 名前に付ける番号だけに使う
    for panel in sorted(page.panels, key=lambda p: p.z):
        items.append(_panel_group(renderer, page, panel, numbers[panel.id], build))

    items += [
        build(BALLOONS, lambda p: renderer.draw_floating(p, page, kinds=(BalloonObject,))),
        build(MARKS, lambda p: renderer.draw_floating(p, page, kinds=(StickerObject,))),
        build(TEXTS, lambda p: renderer.draw_floating(p, page, kinds=(TextObject,))),
    ]
    return [item for item in items if item is not None]


def _panel_group(
    renderer: PageRenderer, page: Page, panel: Panel, number: int, build
) -> PsdGroup | None:
    """コマ1つぶんのフォルダ。中身が1つも無ければ None。

    中身は**奥から手前**（絵 → 集中線・流線 → コマ枠）。`draw_panel` が
    描く順そのままで、ここでも独自の順は持たない。
    """
    plan = [
        (ART, {"contents": True, "effects": False, "border": False}),
        (EFFECTS, {"contents": False, "effects": True, "border": False}),
        (FRAMES, {"contents": False, "effects": False, "border": True}),
    ]
    children = []
    for label, parts in plan:
        layer = build(
            label,
            lambda p, parts=parts: renderer.draw_panel(p, page, panel, **parts),
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
