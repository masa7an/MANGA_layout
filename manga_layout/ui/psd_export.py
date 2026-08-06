"""ページを PSD のレイヤーへ分解する（要件定義 10.1）。

**形式の決まりは `manga_layout.psd` が持ち、ここは「何をレイヤーにするか」
だけを決める。** 分けておくと、PSD の細かい決まりと、この道具にとって
何が1枚かの判断が混ざらない。

## 第1段階は8枚で固定（下が奥）

    セリフ / マーク / フキダシ / コマ枠 / 集中線・流線 / 絵 / ラフ / 用紙

並びは `render.PageRenderer.draw` の描く順そのまま。**ここで独自の順を
持たない。** 持つと、画面と書き出しで重なりが食い違ったときに、どちらが
正なのか決める相手がいなくなる。

コマごとに割るのは第2段階。**先に種類ごとで出すのは、クリスタが期待
どおり開くかを本人が開くまで確かめられないため**（自動テストで言えるのは
「PSD の構造として正しい」までで、そこまでは下の突き合わせで見ている）。

## 中身の無いレイヤーは出さない

フキダシを1つも置いていないページに空の「フキダシ」レイヤーを残しても、
クリスタ側では邪魔になるだけ。**大きさ0のレイヤーという例外的な形を
書かずに済む**という実務上の得もある。

## コマが重なっているページでは、重ね方が PNG と変わる

種類でまとめる以上これは避けられない。PNG では「コマ1の枠線 → コマ2の絵」
の順に描かれるが、レイヤーに分けると枠線が全部まとめて絵の上に乗る。
**コマが重なっていないページ（普通のコマ割り）では起きない。** 第2段階で
コマごとのフォルダに割れば解消する。

なお**合成済みの1枚（merged image）はレイヤーを重ねた結果から作る**ので、
ファイルの中で食い違うことはない。
"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from ..images import ImageCache, Preview, full_from_bytes, full_rough_from_bytes
from ..model import BalloonObject, Page, StickerObject, TextObject
from ..psd import PsdLayer, crop_to_content, write_psd
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


def page_layers(state, page: Page, scale: float = DEFAULT_SCALE) -> list[PsdLayer]:
    """1ページぶんのレイヤー。**下から上の順**で返す。

    画像を引く経路は1ページで1つだけ作って使い回す。レイヤーごとに
    作ると、同じ絵を何度も展開し直すことになる（絵とマークの2枚が
    同じ経路を通る）。
    """
    width, height = checked_page_px(page, scale)
    renderer = PageRenderer(state, FullImages(state), aids=False)
    roughs = FullRoughs(state)
    panels = sorted(page.panels, key=lambda p: p.z)

    def draw_panels(painter: QPainter, **parts: bool) -> None:
        for panel in panels:
            renderer.draw_panel(painter, page, panel, **parts)

    plan = [
        (PAPER, lambda p: renderer.draw_paper(p, page, shadow=False, edge=False), True),
        # ラフは**非表示**で入れる（→ 要件定義 10.1）。なぞる相手であって
        # 作品の中身ではないので、開いた直後に見えていては困る
        (ROUGH, lambda p: renderer.draw_rough(p, page, images=roughs), False),
        (ART, lambda p: draw_panels(p, contents=True, effects=False, border=False), True),
        (EFFECTS, lambda p: draw_panels(p, contents=False, effects=True, border=False), True),
        (FRAMES, lambda p: draw_panels(p, contents=False, effects=False, border=True), True),
        (BALLOONS, lambda p: renderer.draw_floating(p, page, kinds=(BalloonObject,)), True),
        (MARKS, lambda p: renderer.draw_floating(p, page, kinds=(StickerObject,)), True),
        (TEXTS, lambda p: renderer.draw_floating(p, page, kinds=(TextObject,)), True),
    ]

    layers = []
    for label, draw, visible in plan:
        layer = _build(label, draw, page, width, height, visible)
        if layer is not None:
            layers.append(layer)
    return layers


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


def flatten(layers: list[PsdLayer], width: int, height: int) -> QImage:
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
    for layer in layers:
        if not layer.visible:
            continue
        painter.setOpacity(layer.opacity)
        painter.drawImage(layer.x, layer.y, layer.image)
    painter.end()
    return out


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
