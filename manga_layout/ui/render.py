"""1ページぶんの絵を描く処理。

画面（`PageScene`）とページ一覧のサムネイルが**同じ経路**を通るようにするため、
用紙・コマ・画像・吹き出し・マーク・セリフの描画をここへ集めてある。分けて
書くと、片方だけ直したときにサムネイルと本画面が食い違い、しかも気づきにくい。

重ねる順（奥から手前）は**種類で決まる**。z は同じ種類の中でしか効かない。

    用紙 → コマ（と中の画像）→ 吹き出し → マーク → セリフ

段の定義は `model.floating_order` にある。詳しい理由はそちらに書いた。

選択枠・つまみ・下書きの矩形といった「画面の道具」はここに入れない。
それらは作品の一部ではないので、サムネイルにも書き出しにも出したくない。

シーンの座標はそのまま px（要件定義 3章）。表示倍率は呼ぶ側が painter に
掛けてから渡す。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF

from .. import vertical
from ..flow import flow_polygons
from ..focus import focus_triangles
from ..geometry import Rect
from ..layout import balloon_outline, tail_bubbles, tail_triangle
from ..model import (
    BalloonObject,
    FlowLines,
    FocusLines,
    Font,
    ImageObject,
    Page,
    Panel,
    StickerObject,
    TextObject,
    floating_order,
)
from ..slant import slant_polygons

PAGE_BG = QColor("#FFFFFF")
PAGE_EDGE = QColor("#8A8A8A")
PAGE_SHADOW = QColor(0, 0, 0, 70)
# 用紙の影のずらし幅（px）。座標系と同じ単位なので、拡大すると影も大きくなる
PAGE_SHADOW_OFFSET = 9.0
MARGIN_GUIDE = QColor("#B7CEE8")
PANEL_FILL = QColor("#F4F4F4")
PLACEHOLDER = QColor("#9FB2BF")
MISSING_IMAGE = QColor("#D9534F")
# 集中線の色。既定は黒。`FocusLines.white` が立っていれば白（要件定義 6.19）
FOCUS_INK = QColor("#000000")
FOCUS_INK_WHITE = QColor("#FFFFFF")
# 流線の色。集中線と同じ2色（要件定義 6.26）。**定数は分けて持つ。**
# 片方の色を試しに変えたときに、もう片方まで変わらないようにする
FLOW_INK = QColor("#000000")
FLOW_INK_WHITE = QColor("#FFFFFF")

TEXT_ALIGN_FLAGS = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
}


def qrect(rect: Rect) -> QRectF:
    return QRectF(rect.x, rect.y, rect.w, rect.h)


def polygon_of(points) -> QPolygonF:
    """px の点列を Qt の多角形にする。座標はそのまま（シーン＝px）。"""
    return QPolygonF([QPointF(x, y) for x, y in points])


def cosmetic_pen(
    color: QColor, width: float = 1.0, style=Qt.PenStyle.SolidLine
) -> QPen:
    """表示倍率によらず同じ太さで描かれる線。

    目安線や選択枠のような「画面の道具」に使う。作品の一部である
    コマ枠には使わない（そちらは px で太さが決まる）。
    """
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def text_font(font: Font) -> QFont:
    """書式から Qt のフォントを作る。

    Qt が受け取れるのは**整数の画素数**だけ。座標系が px なので、
    そのまま丸めて渡せる（mm だった頃は 3.5 のような小さな値になり、
    3 か 4 に丸められて狙った大きさにならなかった）。
    """
    qfont = QFont(font.family)
    qfont.setPixelSize(max(1, round(font.size_px)))
    qfont.setBold(font.bold)
    return qfont


# 縦書き用の字形に差し替える OpenType の指定。句読点を右上へ寄せ、
# 長音符「ー」と括弧を 90 度回し、小書き文字をずらす、といった処理を
# **フォント側がまとめて行う**。文字ごとの例外表を自前で持たずに済む。
VERTICAL_FEATURE = QFont.Tag("vert")


def vertical_font(font: Font) -> QFont:
    """縦書きのセリフ用のフォント。

    `text_font` との違いは縦書き字形を有効にすることだけ。手元の日本語
    フォント 9 種すべてで効くことを確認済み（2026-08-03）。持っていない
    書体では横書きの字形のまま出るが、落ちはしない。
    """
    qfont = text_font(font)
    qfont.setFeature(VERTICAL_FEATURE, 1)
    return qfont


@dataclass(frozen=True)
class TextScale:
    """四隅のドラッグで拡大縮小している最中のセリフ（→ `canvas.TextScaleDrag`）。

    **枠と文字の大きさを一緒に持つ。** 片方だけでは字組みが相似にならない
    ——列の位置と行の高さは枠から、字の大きさはフォントから決まるので、
    どちらか一方だけを変えると改行位置や列数が動いてしまう。
    """

    rect: Rect
    size_px: float


@dataclass(frozen=True)
class DragPreview:
    """ドラッグ中の下見。

    確定するまでモデルには触らないので、途中経過はここに載せて渡す。
    サムネイルや書き出しは何も渡さない（`NO_PREVIEW`）。
    """

    # 斜めの境界をずらしている最中の (組のどちらかのコマの id, 割合)
    slant: tuple[str, float] | None = None
    # しっぽの先端をドラッグ中の (吹き出しの id, 先端の位置)
    tail: tuple[str, tuple[float, float]] | None = None
    # しっぽの付け根を上下にずらしている最中の (吹き出しの id, 割合)
    root: tuple[str, float] | None = None
    # 回している最中の (画像の id, 角度)
    rotate: tuple[str, float] | None = None
    # 集中線の中心・内側の空きを動かしている最中の (コマの id, その値)。
    # 中心と空きで分けていないのは、どちらも `FocusLines` を1つ差し替える
    # だけで表せるため。増やすと下見の経路が種類ぶん増える
    focus: tuple[str, FocusLines] | None = None
    # 向きを変えている最中の (コマの id, 流線)。集中線と同じ形で持つ
    flow: tuple[str, FlowLines] | None = None
    # 四隅で拡大縮小している最中の (セリフの id, 枠と文字の大きさ)。
    # **離すまでモデルに触らない**という他の下見と同じ流儀だが、これだけは
    # 出さないと操作の意味が画面に出ない——枠だけが動いて字がその場に
    # 残ると、まさに直そうとしている「字が大きくならない」見え方になる
    text_scale: tuple[str, TextScale] | None = None
    # その場編集中のセリフ。二重に見えないよう、下地を描かない
    editing_text_id: str | None = None
    # 入力欄にいま入っている文字列。**縦書きの下見のためだけに渡す**。
    # 入力欄は横書きでしか開けない（Qt に縦書きの入力欄が無い → 要件定義
    # 6.11）ので、打っている最中は列の並びも改行位置も確かめられなかった。
    # モデルには確定するまで触らないので、途中経過はしっぽや回転と同じく
    # ここに載せて渡す。**サムネイルと書き出しは `NO_PREVIEW` を受け取る**
    # ので、下見が焼き付くことはない
    editing_text_content: str | None = None


NO_PREVIEW = DragPreview()


class PageRenderer:
    """ページの中身を描く。部品を持たず、その都度描く。

    `QGraphicsItem` を持たない理由は要件定義 11章のとおり。Undo でモデルの
    実体が差し替わるため、部品を保持すると古い `Panel` を掴んだままになる。

    `state` からは設定と画像の縮小版だけを読む。**描くページは引数で渡す。**
    表示中のページに縛られないので、サムネイル一覧が同じ処理を使える。

    `images` は画像を引く経路。既定は画面用の縮小版（`state.image_preview`）で、
    PNG 書き出しだけが原寸を返すものを渡す（`export.FullImages`）。
    描く手順そのものは共通のまま、解像度だけを差し替えられる。

    **渡すのは参照文字列ではなく画像そのもの。** トーン（→ 要件定義 10.1）を
    焼いた1枚は画像ごとに違うので、同じ `asset` でも別の絵になる。参照だけを
    渡していると、同じ画像を2枚貼ってトーンだけ変えたときに**後から引いた
    ほうが先の1枚を掴む**。

    `aids` は「画面でだけ要る補助表示」。次の3つがこれに当たる。

    - コマの下地（薄い灰色）。紙の上ではコマの中は白なので、これは
      「どこがコマか」を画面で見分けるための色でしかない
    - 空のセリフの点線枠。無いと、作った直後に見失って選び直せなくなる
    - 見つからない画像の×印。無いと、絵が消えたのか最初から無かったのか
      分からない

    どれも作品の一部ではないので**書き出しでは切る**。コマの範囲は枠線が
    示すし、欠けた画像は目印の代わりに書き出し前の警告で知らせる
    （`export.missing_assets_in`）。

    この2つを構築時に決めるのは、「何のために描くか」で決まるものだからで、
    目安線・影・用紙の縁は同じ描き手でも呼びごとに変わるので `draw()` の
    引数に置いてある。
    """

    def __init__(self, state, images=None, *, aids: bool = True) -> None:
        self.state = state
        self.images = images if images is not None else state.image_preview
        self.aids = aids

    # -- 全体 --------------------------------------------------------------

    def draw(
        self,
        painter: QPainter,
        page: Page,
        preview: DragPreview = NO_PREVIEW,
        *,
        guides: bool = True,
        shadow: bool = True,
        edge: bool = True,
        rough: bool = True,
    ) -> None:
        """用紙とその中身を描く。

        `guides` は基本枠の目安線、`shadow` は用紙の影。どちらも作品には
        出ないので、サムネイルでは切る（小さく描くと線が潰れて汚れになる）。

        `edge` は用紙の輪郭線。画面とサムネイルでは、白い紙がどこまでかを
        示すのに要る。**書き出しでは切る**。用紙そのものが画像の範囲なので、
        輪郭線は絵の一部として四辺に残ってしまう。

        `rough` はラフ（下敷き → 6.23）。**書き出しでは切る。** なぞる相手で
        あって作品の中身ではない。`aids` に相乗りさせていないのは、一覧の
        サムネイルには出したいため（`aids` は書き出しだけが False）。
        """
        self.draw_paper(painter, page, shadow=shadow, edge=edge)
        if rough:
            self.draw_rough(painter, page)
        if guides:
            self.draw_margin(painter, page)
        for panel in sorted(page.panels, key=lambda p: p.z):
            self.draw_panel(painter, page, panel, preview)
        self.draw_floating(painter, page, preview)

    def draw_paper(
        self, painter: QPainter, page: Page, *, shadow: bool = True, edge: bool = True
    ) -> None:
        rect = QRectF(0.0, 0.0, page.size.w, page.size.h)
        if shadow:
            painter.fillRect(
                rect.translated(PAGE_SHADOW_OFFSET, PAGE_SHADOW_OFFSET), PAGE_SHADOW
            )
        painter.fillRect(rect, PAGE_BG)
        if edge:
            painter.setPen(cosmetic_pen(PAGE_EDGE))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

    def draw_rough(
        self, painter: QPainter, page: Page, *, images=None
    ) -> None:
        """ラフ（下敷き → 要件定義 6.23）。**用紙のすぐ上、一番奥に敷く。**

        目安線よりも奥に描く。目安線はラフの上でも見えている必要がある
        （どこまでが基本枠かは、ラフを敷いても変わらない）。

        コマより奥なので、**コマを置いた部分は隠れる**。画面ではコマの
        下地（薄い灰色）が不透明に塗られるためで、狙った挙動そのもの
        （置き終わった場所からラフが消えていく）。

        **青くするのと薄くするのは、切り替えの1手にまとめてある**（→ 6.23）。
        `faded` を落としたときは元の写真がそのまま出る——細かいところを
        確かめるための状態なので、そこで薄いままだと確かめられない。
        濃さは設定から取る（→ `settings.rough_opacity`）。

        **実体が無いときは何も描かない。** 画像の×印（`_draw_missing`）に
        当たるものは出さない——ラフは作品の一部ではないので、欠けていても
        書き出しには響かず、用紙の上に赤い×だけが残るほうが邪魔になる。

        `images` はラフを引く経路。既定は画面用の縮小版で、**PSD 書き出し
        だけが原寸を返すものを渡す**（→ 要件定義 10.1）。`PageRenderer.images`
        が画像でしている切り替えと同じだが、ラフは1ページに1枚しか無いので
        構築時ではなくここで受け取る。
        """
        rough = page.rough
        if rough is None:
            return
        preview = (images or self.state.rough_preview)(rough.asset, rough.faded)
        if preview is None:
            return
        if rough.faded:
            painter.setOpacity(self.state.rough_opacity)
        painter.drawImage(qrect(rough.rect), preview.image)
        painter.setOpacity(1.0)

    def draw_margin(self, painter: QPainter, page: Page) -> None:
        """基本枠（内側の目安線）。作品には出ない、置き場所の目印。"""
        m = self.state.settings.margin
        if m <= 0:
            return
        painter.setPen(cosmetic_pen(MARGIN_GUIDE, 1.0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(m, m, page.size.w - m * 2, page.size.h - m * 2))

    # -- コマ --------------------------------------------------------------

    def _preview_shape(self, page: Page, panel: Panel, preview: DragPreview):
        """境界をドラッグ中のコマの、下見の形。関係なければ None。

        モデルには触らずここで作り直す。確定するまで履歴を汚さない。
        """
        if preview.slant is None:
            return None
        held_id, ratio = preview.slant
        pair = page.slant_pair_of(held_id)
        if pair is None or panel.id not in pair.members():
            return None
        left, right = slant_polygons(
            page.slant_bounds(pair),
            ratio,
            pair.angle,
            pair.direction,
            self.state.settings.gutter,
        )
        return left if panel.id == pair.left_id else right

    def draw_panel(
        self,
        painter: QPainter,
        page: Page,
        panel: Panel,
        preview: DragPreview = NO_PREVIEW,
        *,
        contents: bool = True,
        effects: bool = True,
        border: bool = True,
    ) -> None:
        """コマ1つ。**既定では画面と同じく全部描く。**

        3つの `bool` は PSD 書き出し（→ 要件定義 10.1）が中身・効果線・
        枠線を別のレイヤーへ振り分けるためのもの。**画面はどれも既定の
        まま通る**ので、ここを足したことで見た目は変わらない。

        分けられる単位をこの3つにしたのは、レイヤーとして意味を持つのが
        この粒度だから。「絵だけ薄くする」「枠線だけ残す」がクリスタ側で
        したいことで、それより細かく割っても使い道が無い。
        """
        shape = self._preview_shape(page, panel, preview)
        if shape is None:
            shape = panel.shape
        polygon = polygon_of(shape.points)
        if self.aids and contents:
            # 下地は画面で「どこがコマか」を見分けるための色。紙の上では
            # コマの中は白なので、書き出しでは塗らずに用紙の白を残す
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(PANEL_FILL))
            painter.drawPolygon(polygon)

        focus = self._focus_of(panel, preview) if effects else None
        flow = self._flow_of(panel, preview) if effects else None
        if (panel.children and contents) or focus is not None or flow is not None:
            # 集中線・流線の基準は、**いま描いている形**の外接矩形。斜めの
            # 境界をずらしている最中はモデルと形が違うので、`panel` から
            # 取り直すと線だけ前の位置に残る
            self._draw_inside(
                painter,
                panel,
                polygon,
                shape.bounds(),
                focus,
                flow,
                preview,
                images=contents,
            )

        if border and panel.border.visible and panel.border.width > 0:
            # 枠線は作品の一部なので、太さは px のまま（表示倍率で見た目が変わる）
            # 画像より後に描く。先に描くと、はみ出した絵が枠線を覆ってしまう
            painter.setPen(QPen(QColor(panel.border.color), panel.border.width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)

    @staticmethod
    def _focus_of(panel: Panel, preview: DragPreview) -> FocusLines | None:
        """描くときの集中線。動かしている最中は下見の値を使う。"""
        if preview.focus is not None and preview.focus[0] == panel.id:
            return preview.focus[1]
        return panel.focus_lines

    @staticmethod
    def _flow_of(panel: Panel, preview: DragPreview) -> FlowLines | None:
        """描くときの流線。回している最中は下見の値を使う。"""
        if preview.flow is not None and preview.flow[0] == panel.id:
            return preview.flow[1]
        return panel.flow_lines

    def _draw_inside(
        self,
        painter: QPainter,
        panel: Panel,
        polygon: QPolygonF,
        bounds: Rect,
        focus: FocusLines | None,
        flow: FlowLines | None = None,
        preview: DragPreview = NO_PREVIEW,
        *,
        images: bool = True,
    ) -> None:
        """コマの中身を、コマの形で切り抜いて描く。

        切り抜きはコマのポリゴンそのものに対して行う。斜めのコマでも
        そのまま効く（要件定義 4章でポリゴン保存にした狙いのひとつ）。

        **切り抜きは画像の回転より外側に掛かる。** 絵を回してもコマの形は
        回らない、という当たり前の見え方になる。

        集中線と流線は**画像より手前**。絵に乗せるものなので、下に潜られては
        用を成さない。一方**コマ枠より奥**に置く（枠線は呼ぶ側があとから
        描く）。線が枠線を覆うと、コマの輪郭が途切れて見える。

        両方入っているときは**流線が奥、集中線が手前**（要件定義 6.26）。
        流線は面を流す背景側、集中線は一点へ集める前景側なので、集中が
        上に乗るほうが意味と合う。z は持たない（各1つずつなので、順を
        決める相手が最初から1つしかいない）。
        """
        path = QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()

        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        for image in sorted(panel.children if images else (), key=lambda i: i.z):
            self._draw_image(painter, image, preview)
        if flow is not None:
            self._draw_flow(painter, bounds, flow)
        if focus is not None:
            self._draw_focus(painter, bounds, focus)
        painter.restore()

    def _draw_focus(
        self, painter: QPainter, bounds: Rect, focus: FocusLines
    ) -> None:
        """集中線（要件定義 6.16）。楔形を本数ぶん塗る。

        **内側の空きは白く塗らない。** ここでは集中線が画像の上に乗るので、
        塗ると中心に置いた人物が消える。線を引かない領域を空けるだけに
        すれば、下の絵はそのまま見える。

        線はコマの外まで伸びているが、切り抜きの中で描いているので端は
        自動で落ちる。形を作る側は `manga_layout.focus`。

        色は `focus.white` だけで決まる**単純な色違い**（要件定義 6.19）。
        暗いコマの上で使う想定で、形の計算には影響しない。
        """
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(FOCUS_INK_WHITE if focus.white else FOCUS_INK))
        for triangle in focus_triangles(focus, bounds):
            painter.drawPolygon(polygon_of(triangle))

    def _draw_flow(self, painter: QPainter, bounds: Rect, flow: FlowLines) -> None:
        """流線（要件定義 6.26）。紡錘形を本数ぶん塗る。

        **抜き（主役を残す領域）は持たない。** 集中線に空きが要るのは線が
        1点へ集まって中心が潰れるためで、平行線には潰れる場所が無い。

        直交方向にはみ出した線は切り抜きで落ちるが、**線に沿った方向の端は
        画面に見える**。そこが集中線と違うところで、長さは値として持つ
        （形を作る側は `manga_layout.flow`）。
        """
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(FLOW_INK_WHITE if flow.white else FLOW_INK))
        for line in flow_polygons(flow, bounds):
            painter.drawPolygon(polygon_of(line))

    @staticmethod
    def _rotation_of(
        image: ImageObject | StickerObject, preview: DragPreview
    ) -> float:
        """描くときの角度。回している最中は下見の角度を使う。"""
        if preview.rotate is not None and preview.rotate[0] == image.id:
            return preview.rotate[1]
        return image.rotation

    def _draw_image(
        self,
        painter: QPainter,
        image: ImageObject | StickerObject,
        preview: DragPreview = NO_PREVIEW,
    ) -> None:
        """1枚の画像を矩形いっぱいに描く。傾いていれば中心まわりに回す。

        マーク（→ 6.14）もここを通る。持っている項目が同じで、違うのは
        切り抜かれるかどうか＝**呼ばれる場所**だけ。描き方を書き分けると、
        透明度の扱いや欠けたときの目印が片方だけ古くなる。マークの
        `rotation` は常に 0 なので、回転の分岐には入らない。
        """
        rotation = self._rotation_of(image, preview)
        if rotation == 0.0:
            self._draw_image_upright(painter, image)
            return

        painter.save()
        cx, cy = image.rect.center
        painter.translate(cx, cy)
        painter.rotate(rotation)
        painter.translate(-cx, -cy)
        # 回すと画素が斜めに並び、そのままでは縁が階段状になる。
        # **回っているときだけ**入れる（真っ直ぐな絵まで滑らかにすると、
        # 等倍で見たときにわずかに眠くなる）
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._draw_image_upright(painter, image)
        painter.restore()

    def _draw_image_upright(
        self, painter: QPainter, image: ImageObject | StickerObject
    ) -> None:
        """傾きを考えずに1枚描く。回転は呼ぶ側が painter に掛けてある。"""
        preview = self.images(image)
        if preview is None:
            self._draw_missing(painter, image)
            return
        painter.setOpacity(image.opacity)
        painter.drawImage(qrect(image.rect), preview.image)
        painter.setOpacity(1.0)

    def _draw_missing(
        self, painter: QPainter, image: ImageObject | StickerObject
    ) -> None:
        """実体が無い・壊れている画像の場所。

        何も描かないと、絵が消えたのか最初から無かったのか分からない。
        枠だけ出して「ここに1枚あるはず」と示す。
        """
        if not self.aids:
            return
        painter.setPen(cosmetic_pen(MISSING_IMAGE, 1.0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = qrect(image.rect)
        painter.drawRect(rect)
        painter.drawLine(QLineF(rect.topLeft(), rect.bottomRight()))
        painter.drawLine(QLineF(rect.topRight(), rect.bottomLeft()))

    # -- 吹き出しとセリフ --------------------------------------------------

    def draw_floating(
        self,
        painter: QPainter,
        page: Page,
        preview: DragPreview = NO_PREVIEW,
        *,
        kinds: tuple[type, ...] | None = None,
    ) -> None:
        """ページ直下のもの。**段が先、z が後**（`model.floating_order`）。

        セリフは常に吹き出しより手前。z だけで重ねると、セリフを書いた
        あとに載せた吹き出しが文字を塗り潰してしまう。マークはその間で、
        吹き出しの上・セリフの下（要件定義 6.14）。

        `kinds` を渡すと、その種類だけを描く。PSD 書き出しが吹き出し・
        マーク・セリフを別のレイヤーへ振り分けるためのもの（→ 10.1）。
        **None が既定**なので、画面はこれまでどおり全部通る。
        """
        for obj in sorted(page.floating, key=floating_order):
            if kinds is not None and not isinstance(obj, kinds):
                continue
            if isinstance(obj, BalloonObject):
                self._draw_balloon(painter, obj, preview)
            elif isinstance(obj, StickerObject):
                # 切り抜かない。コマからはみ出して置くためのもの。
                # **下見を渡す。** 渡さないと、回している最中だけ絵が
                # 元の角度のまま止まり、選択枠だけが回って見える
                self._draw_image(painter, obj, preview)
            elif isinstance(obj, TextObject):
                self._draw_text(painter, obj, preview)

    def draw_text_alone(self, painter: QPainter, obj: TextObject) -> None:
        """セリフを1つだけ、確定後の姿で描く（ラスタライズ → 6.34）。

        **焼くときも画面と同じ経路を通す**ための入口。別に書くと、縦書きの
        組み方や寄せが画面と食い違ったときに、焼いたあとで初めて気づく
        ことになる（`render.py` を画面・PNG・PSD の3つで共有しているのと
        同じ理由 → 2章）。
        """
        self._draw_text(painter, obj, NO_PREVIEW)

    def _draw_text(
        self, painter: QPainter, obj: TextObject, preview: DragPreview
    ) -> None:
        """セリフ。手動改行のみ（要件定義 6.5、9章）。

        その場編集の最中は描かない。編集中の文字が二重に見えてしまう。
        **縦書きだけは例外**で、確定後の姿を実時間で出す（下記）。
        """
        if preview.editing_text_id == obj.id:
            self._draw_text_editing(painter, obj, preview)
            return

        # 拡大縮小の最中は、下見の枠と大きさで描く。**空のセリフの点線枠も
        # ここを通る**ので、字を1つも入れていないセリフでも引いた大きさが
        # その場で分かる
        if preview.text_scale is not None and preview.text_scale[0] == obj.id:
            scale = preview.text_scale[1]
            obj = dataclasses.replace(
                obj,
                rect=scale.rect,
                font=dataclasses.replace(obj.font, size_px=scale.size_px),
            )

        if not obj.content:
            # 空のセリフは枠だけ出す。何も描かないと、作った直後に
            # 見失って選び直せなくなる
            if not self.aids:
                return
            painter.setPen(cosmetic_pen(PLACEHOLDER, 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(qrect(obj.rect))
            return

        painter.save()
        painter.setPen(QPen(QColor("#000000")))
        if obj.direction == "vertical":
            self._draw_text_vertical(painter, obj)
        else:
            self._draw_text_horizontal(painter, obj)
        painter.restore()

    def _draw_text_editing(
        self, painter: QPainter, obj: TextObject, preview: DragPreview
    ) -> None:
        """その場編集の最中に、縦書きの仕上がりを枠の中へ出す。

        **入力欄は縦書きにできない**（Qt に日本語の縦書きの入力欄が無い
        → 要件定義 6.11）。打っている間は横書きの1行しか見えないので、
        列が何本になるか・どこで改行されるか・枠からはみ出すかが、確定して
        みるまで分からなかった。ここで確定後と同じ経路（`_draw_text_vertical`）
        へ流し、**入力欄のほうを枠の外へ逃がす**ことで両方が同時に見える。

        **確定後と同じ描き方でなければ意味がない**ので、色も字形も変えない。
        別の描き方にすると「下見では収まっていたのに確定したらはみ出した」
        が起こり、下見を見る理由そのものが無くなる。

        横書きのセリフでは何もしない。入力欄が枠に重なったまま同じ字を
        出すので、二重に見えるだけになる。
        """
        if obj.direction != "vertical":
            return

        content = preview.editing_text_content
        if not content:
            # 1文字も入っていない間は枠だけ出す。入力欄は枠の外へ逃げて
            # いるので、ここに何も描かないと**どこへ字が入るのかが画面から
            # 消える**（空のセリフに点線枠を出すのと同じ理由）
            if not self.aids:
                return
            painter.setPen(cosmetic_pen(PLACEHOLDER, 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(qrect(obj.rect))
            return

        painter.save()
        painter.setPen(QPen(QColor("#000000")))
        self._draw_text_vertical(painter, dataclasses.replace(obj, content=content))
        painter.restore()

    def _draw_text_horizontal(self, painter: QPainter, obj: TextObject) -> None:
        painter.setFont(text_font(obj.font))
        flags = (
            TEXT_ALIGN_FLAGS.get(obj.align, Qt.AlignmentFlag.AlignHCenter)
            | Qt.AlignmentFlag.AlignVCenter
            # 折り返さない（要件定義 9章: MVP は手動改行のみ）。
            # 枠に収まらない字も隠さずに出し、はみ出しに気づけるようにする
            | Qt.TextFlag.TextDontClip
        )
        painter.drawText(qrect(obj.rect), flags, obj.content)

    def _draw_text_vertical(self, painter: QPainter, obj: TextObject) -> None:
        """縦書き。1 文字ずつ置く。

        Qt には日本語の縦書きが無いので、まとめて渡す方法が使えない
        （→ `manga_layout.vertical`）。置き場所の計算はそちらが持ち、
        ここは受け取った正方形の中央へ 1 文字ずつ描くだけ。

        **正方形の中央に置いてよい**のは、句読点を右上へ寄せる・長音符を
        回すといった正方形の中での調整をフォントの縦書き字形が済ませて
        いるため。こちらでずらすと二重に効いて崩れる。
        """
        painter.setFont(vertical_font(obj.font))
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextDontClip
        for glyph in vertical.layout(
            obj.content, obj.rect, obj.font.size_px, obj.align
        ):
            painter.drawText(qrect(glyph.cell), flags, glyph.ch)

    def with_preview_tail(
        self, balloon: BalloonObject, preview: DragPreview
    ) -> BalloonObject:
        """しっぽをドラッグ中なら、その値を当てはめた写しを返す。

        モデルは確定するまで触らない。写しを描くことで、Undo の1手が
        ドラッグの途中経過で埋まるのを避けられる。
        """
        tail = balloon.tail
        if preview.tail is not None and preview.tail[0] == balloon.id:
            tail = dataclasses.replace(tail, tip=preview.tail[1], enabled=True)
        if preview.root is not None and preview.root[0] == balloon.id:
            tail = dataclasses.replace(tail, root_y=preview.root[1], enabled=True)
        if tail is balloon.tail:
            return balloon
        return dataclasses.replace(balloon, tail=tail)

    def balloon_path(self, balloon: BalloonObject) -> QPainterPath:
        """本体としっぽを**1つの輪郭**にまとめた形。

        別々に描くと継ぎ目に枠線が残り、しっぽが貼り付けた三角形に見える。
        塗りを重ねて線を隠す手もあるが、半透明や色付きの塗りで破綻する。
        図形を合成してから一度だけ縁取るほうが、どの配色でも正しい。

        **飛びしっぽだけは合成しない。** 離れていることが意味そのものなので、
        本体とくっつけては困る（→ 要件定義 10.1）。ただし QPainterPath は
        離れた図形を1つのパスに持てるので、**塗り1回・線1回という描き方は
        変えなくてよい**。円を足すだけで済む。
        """
        settings = self.state.balloon_settings
        path = QPainterPath()
        path.addPolygon(polygon_of(balloon_outline(balloon, settings)))
        path.closeSubpath()

        for cx, cy, radius in tail_bubbles(balloon, settings):
            path.addEllipse(QPointF(cx, cy), radius, radius)

        triangle = tail_triangle(balloon, settings)
        if triangle is None:
            return path

        tail = QPainterPath()
        tail.addPolygon(polygon_of(triangle))
        tail.closeSubpath()
        return path.united(tail)

    def _draw_balloon(
        self, painter: QPainter, balloon: BalloonObject, preview: DragPreview
    ) -> None:
        balloon = self.with_preview_tail(balloon, preview)
        path = self.balloon_path(balloon)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(balloon.fill)))
        painter.drawPath(path)

        if balloon.border.visible and balloon.border.width > 0:
            # コマの枠線と同じく、太さは px（作品の一部なので表示倍率で変わる）
            pen = QPen(QColor(balloon.border.color), balloon.border.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
