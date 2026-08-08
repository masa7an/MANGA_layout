"""ページを PSD のレイヤーへ分解する（要件定義 10.1）。

**形式の決まりは `manga_layout.psd` が持ち、ここは「何をレイヤーにするか」
だけを決める。** 分けておくと、PSD の細かい決まりと、この道具にとって
何が1枚かの判断が混ざらない。

## 構成（下が奥）

    セリフ / マーク / フキダシ
    [フォルダ] コマN ── 絵・白ベタ・トーン範囲（非表示）・トーン・集中線と流線・コマ枠
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

## トーンは4枚に分ける（要件定義 6.28）

クリスタのトーンは種類がはるかに多く、こだわるなら結局そちらで貼り直す
ことになる。**そのとき1手で差し替えられる形にして渡す。**

    トーン       ← 消して、自分のトーンに差し替える
    トーン範囲   ← 非表示。選択範囲を作るための目印
    白ベタ       ← 元の絵の黒ベタを隠す
    絵           ← **トーンを焼く前**

**「絵」にトーンを焼き込んだままでは差し替えられない。** 上に網点を貼っても、
隙間から下のトーンが透けて干渉する（本人の指摘 2026-08-06）。**白ベタが
その下敷きを断つ**ので、利用者は「トーンを消す → 好きなトーンを貼る」だけで
済む。

- **重ねた結果は、トーンの範囲の境目 1px を除いて PNG 書き出しと一致する。**
  分けると絵とトーンが**別々に縮んでから重なる**ので、混ざる順番が入れ替わる。
  避けるには焼き込んだままにするしかなく、それでは分ける意味が無い
  （→ 要件定義 6.28。2026-08-06 本人確認済み）。白ベタがマスクの縁を持たない
  のは、そのずれを最小にするため（→ `tone._fully_masked`）
- **トーン範囲だけ非表示。** 合成済みの1枚にも入らない（ラフと同じ扱い）
- **並びは 白ベタ → トーン範囲 → トーン。** トーン範囲を選んだまま新しい
  レイヤーを作ると、白ベタより手前・コマ枠より奥に入る
- トーンの入った絵が1枚も無いコマには1枚も出さない（下の「中身の無いレイヤー」）
- **分けられないコマもある**（→ `_splittable`）。トーンの入った絵の上に別の絵を
  重ねている場合だけ、今までどおり焼いた1枚とトーン範囲を出す

## 中身の無いレイヤー・フォルダは出さない

フキダシを1つも置いていないページに空の「フキダシ」レイヤーを残しても、
クリスタ側では邪魔になるだけ。中身の無いコマも、フォルダごと出さない。

## 合成済みの1枚はレイヤーから作る

`render_page` を呼び直さない。**ファイルの中で食い違わない**うえ、同じ絵を
2回展開せずに済む（→ `flatten`）。
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPainterPathStroker

from ..images import ImageCache, Preview, full_from_bytes, full_rough_from_bytes
from ..model import BalloonObject, ImageObject, Page, Panel, StickerObject, TextObject
from ..psd import PsdGroup, PsdLayer, crop_to_content, write_psd
from ..tone import TonePieces, tone_pieces
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
TONE_FILL = ("白ベタ", "tonefill")
TONE_MASK = ("トーン範囲", "tonemask")
TONE_PATTERN = ("トーン", "tone")
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


class BareImages:
    """**トーンを焼く前**の絵を返す置き場（→ 要件定義 6.28）。

    PSD の「絵」レイヤーはこちらを使う。焼いた1枚を入れると、利用者が
    クリスタでトーンを貼り替えたときに**下から元のトーンが透ける**。
    """

    def __init__(self, images: FullImages) -> None:
        self._images = images

    def __call__(self, image) -> Preview | None:
        return self._images.base(image.asset)


class TonePieceImages:
    """トーンを3枚（トーン範囲・白ベタ・トーン）に分けて返す置き場。

    **`PageRenderer` の画像を引く経路をそのまま差し替える。** こうすると
    コマの形での切り抜き・画像の位置・回転が絵と完全に同じになる。
    別に描くと、斜めのコマや回した絵でマスクだけがずれる。

    **トーンの入っていない絵には `None` を返す。** 描く側は「その場所に
    何も描かない」で進む（`aids=False` なので欠けた画像の目印も出ない）。
    ページのどのコマにもトーンが無ければ、レイヤーごと出ない。

    絵は `FullImages` と**同じ入れ物から引く**（`base`）。原寸から焼くのは
    トーンを焼き直すのと同じ理由——縮小版から作ったマスクを引き伸ばすと、
    縁が階段状になったまま貼ることになる。

    **3枚は1回のマスクから作って覚えておく**（→ `tone.tone_pieces`）。
    描く経路が3つに分かれていても、焼くのは絵ごとに1回で済む。
    """

    def __init__(self, images: FullImages) -> None:
        self._images = images
        # **上限を持たない**（`ToneCache` と違う）。1ページ書き出すあいだ
        # だけの入れ物で、鍵は貼ってある絵の数で頭打ちになる
        self._pieces: dict[tuple[str, tuple], tuple[TonePieces, tuple[int, int]]] = {}

    def _of(self, image) -> tuple[TonePieces, tuple[int, int]] | None:
        tone = getattr(image, "tone", None)
        if tone is None:
            return None
        ref = image.asset
        key = (ref, tone.key())
        found = self._pieces.get(key)
        if found is not None:
            return found

        source = self._images.base(ref)
        if source is None:
            return None
        made = (tone_pieces(source.image, tone), source.source_px)
        self._pieces[key] = made
        return made

    def _piece(self, image, name: str) -> Preview | None:
        found = self._of(image)
        if found is None:
            return None
        pieces, source_px = found
        return Preview(image=getattr(pieces, name), source_px=source_px)

    def area(self, image) -> Preview | None:
        return self._piece(image, "area")

    def fill(self, image) -> Preview | None:
        return self._piece(image, "fill")

    def pattern(self, image) -> Preview | None:
        return self._piece(image, "pattern")


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
    painters = _Painters.of(state, renderer, images)
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
        items.append(_panel_group(painters, page, panel, numbers[panel.id], build))

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


@dataclass(frozen=True)
class _Painters:
    """コマの中身を描く写しの一式。**引く絵だけが違い、描く手順は同じ。**

    トーンを4枚に分けるために4通りの絵が要る（→ 要件定義 6.28）。
    `PageRenderer` は `images` を差し替えるだけで別の絵を描けるので、
    ここで写しを作り分ければ、切り抜き・位置・回転は自動で揃う。
    """

    #: トーンを焼いた絵。**分けられないコマ**でだけ使う（→ `_splittable`）
    full: PageRenderer
    #: トーンを焼く前の絵
    bare: PageRenderer
    #: トーン範囲（黒いシルエット）
    area: PageRenderer
    #: 白ベタ
    fill: PageRenderer
    #: 敷いたトーンそのもの
    pattern: PageRenderer

    @classmethod
    def of(cls, state, full: PageRenderer, images: FullImages) -> _Painters:
        pieces = TonePieceImages(images)
        return cls(
            full=full,
            bare=PageRenderer(state, BareImages(images), aids=False),
            area=PageRenderer(state, pieces.area, aids=False),
            fill=PageRenderer(state, pieces.fill, aids=False),
            pattern=PageRenderer(state, pieces.pattern, aids=False),
        )


def _splittable(panel: Panel) -> bool:
    """このコマのトーンを4枚に分けてよいか（→ 要件定義 6.28）。

    分けると、**コマの中の絵が全部「絵」レイヤーへ、トーンが全部その上の
    レイヤーへ**まとまる。絵が1枚なら順番は変わらないが、**トーンの入った
    絵の上に別の絵を重ねている**場合、上の絵より手前にトーンが出てしまう。

    そこだけは分けずに焼いた1枚を出す（今までどおり）。**重なっているか
    どうかまでは見ない**——見るには回転を含む形の判定が要るうえ、外した
    ときの結果が「絵の上にトーンが乗る」という気づきにくい間違いになる。
    """
    images = sorted(
        (c for c in panel.children if isinstance(c, ImageObject)), key=lambda c: c.z
    )
    return not any(img.tone is not None for img in images[:-1])


def _panel_group(
    painters: _Painters, page: Page, panel: Panel, number: int, build
) -> PsdGroup | None:
    """コマ1つぶんのフォルダ。中身が1つも無ければ None。

    中身は**奥から手前**（絵 → 白ベタ → トーン範囲 → トーン → 集中線・流線
    → コマ枠）。`draw_panel` が描く順そのままで、ここでも独自の順は持たない。

    **トーンの3枚は「絵」と同じ中身を別の絵で描いたもの**（→ `_Painters`）。
    重ねた結果は、トーンの境目 1px を除いて焼いた1枚と一致する。置き場所と
    並びの理由はモジュールの冒頭にある。
    """
    art = {"contents": True, "effects": False, "border": False}
    if _splittable(panel):
        # **トーン範囲は白ベタとトーンのあいだ。** ここを選んだまま新しい
        # レイヤーを作ると、白ベタより手前・コマ枠より奥に入る（→ 冒頭）
        plan = [
            (ART, painters.bare, art, True),
            (TONE_FILL, painters.fill, art, True),
            (TONE_MASK, painters.area, art, False),
            (TONE_PATTERN, painters.pattern, art, True),
        ]
    else:
        plan = [(ART, painters.full, art, True), (TONE_MASK, painters.area, art, False)]
    plan += [
        (EFFECTS, painters.full, {"contents": False, "effects": True, "border": False}, True),
        (FRAMES, painters.full, {"contents": False, "effects": False, "border": True}, True),
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
    on_page: Callable[[int, int], bool] | None = None,
) -> list[pathlib.Path]:
    """指定したページを PSD にする。書いたファイルの一覧を返す。

    1ページ1ファイル。途中で失敗したらそこで止める（`export_pages` と同じ）。

    `on_page` の意味も `export_pages` と同じ（→ そちらの docstring）。
    PSD は1ページ 10〜30MB あり全ページで数百MB になり得るため
    （→ 要件定義 10.1）、進捗と中止の口は特にこちらで効いてくる。
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
        if on_page is not None and not on_page(len(written), len(indexes)):
            break
    return written
