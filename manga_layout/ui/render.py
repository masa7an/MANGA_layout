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
from ..focus import focus_triangles
from ..geometry import Rect
from ..layout import balloon_outline, tail_triangle
from ..slant import slant_polygons
from ..model import (
    BalloonObject,
    FocusLines,
    Font,
    ImageObject,
    Page,
    Panel,
    StickerObject,
    TextObject,
    floating_order,
)

PAGE_BG = QColor("#FFFFFF")
PAGE_EDGE = QColor("#8A8A8A")
PAGE_SHADOW = QColor(0, 0, 0, 70)
# 用紙の影のずらし幅（px）。座標系と同じ単位なので、拡大すると影も大きくなる
PAGE_SHADOW_OFFSET = 9.0
MARGIN_GUIDE = QColor("#B7CEE8")
PANEL_FILL = QColor("#F4F4F4")
PLACEHOLDER = QColor("#9FB2BF")
MISSING_IMAGE = QColor("#D9534F")
# 集中線の色。**黒のみ**（要件定義 6.16）。白集中線は要求が立ってから
FOCUS_INK = QColor("#000000")

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
    # その場編集中のセリフ。二重に見えないよう、下地を描かない
    editing_text_id: str | None = None


NO_PREVIEW = DragPreview()


class PageRenderer:
    """ページの中身を描く。部品を持たず、その都度描く。

    `QGraphicsItem` を持たない理由は要件定義 11章のとおり。Undo でモデルの
    実体が差し替わるため、部品を保持すると古い `Panel` を掴んだままになる。

    `state` からは設定と画像の縮小版だけを読む。**描くページは引数で渡す。**
    表示中のページに縛られないので、サムネイル一覧が同じ処理を使える。

    `images` は画像を引く経路。既定は画面用の縮小版（`state.preview`）で、
    PNG 書き出しだけが原寸を返すものを渡す（`export.FullImages`）。
    描く手順そのものは共通のまま、解像度だけを差し替えられる。

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
        self.images = images if images is not None else state.preview
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
    ) -> None:
        """用紙とその中身を描く。

        `guides` は基本枠の目安線、`shadow` は用紙の影。どちらも作品には
        出ないので、サムネイルでは切る（小さく描くと線が潰れて汚れになる）。

        `edge` は用紙の輪郭線。画面とサムネイルでは、白い紙がどこまでかを
        示すのに要る。**書き出しでは切る**。用紙そのものが画像の範囲なので、
        輪郭線は絵の一部として四辺に残ってしまう。
        """
        self.draw_paper(painter, page, shadow=shadow, edge=edge)
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
    ) -> None:
        shape = self._preview_shape(page, panel, preview)
        if shape is None:
            shape = panel.shape
        polygon = polygon_of(shape.points)
        if self.aids:
            # 下地は画面で「どこがコマか」を見分けるための色。紙の上では
            # コマの中は白なので、書き出しでは塗らずに用紙の白を残す
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(PANEL_FILL))
            painter.drawPolygon(polygon)

        focus = self._focus_of(panel, preview)
        if panel.children or focus is not None:
            # 集中線の基準は、**いま描いている形**の外接矩形。斜めの境界を
            # ずらしている最中はモデルと形が違うので、`panel` から取り直すと
            # 線だけ前の位置に残る
            self._draw_inside(painter, panel, polygon, shape.bounds(), focus, preview)

        if panel.border.visible and panel.border.width > 0:
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

    def _draw_inside(
        self,
        painter: QPainter,
        panel: Panel,
        polygon: QPolygonF,
        bounds: Rect,
        focus: FocusLines | None,
        preview: DragPreview = NO_PREVIEW,
    ) -> None:
        """コマの中身を、コマの形で切り抜いて描く。

        切り抜きはコマのポリゴンそのものに対して行う。斜めのコマでも
        そのまま効く（要件定義 4章でポリゴン保存にした狙いのひとつ）。

        **切り抜きは画像の回転より外側に掛かる。** 絵を回してもコマの形は
        回らない、という当たり前の見え方になる。

        集中線は**画像より手前**。絵に乗せるものなので、下に潜られては
        用を成さない。一方**コマ枠より奥**に置く（枠線は呼ぶ側があとから
        描く）。線が枠線を覆うと、コマの輪郭が途切れて見える。
        """
        path = QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()

        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        for image in sorted(panel.children, key=lambda i: i.z):
            self._draw_image(painter, image, preview)
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
        """
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(FOCUS_INK))
        for triangle in focus_triangles(focus, bounds):
            painter.drawPolygon(polygon_of(triangle))

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
        preview = self.images(image.asset)
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
        self, painter: QPainter, page: Page, preview: DragPreview = NO_PREVIEW
    ) -> None:
        """ページ直下のもの。**段が先、z が後**（`model.floating_order`）。

        セリフは常に吹き出しより手前。z だけで重ねると、セリフを書いた
        あとに載せた吹き出しが文字を塗り潰してしまう。マークはその間で、
        吹き出しの上・セリフの下（要件定義 6.14）。
        """
        for obj in sorted(page.floating, key=floating_order):
            if isinstance(obj, BalloonObject):
                self._draw_balloon(painter, obj, preview)
            elif isinstance(obj, StickerObject):
                # 切り抜かない。コマからはみ出して置くためのもの
                self._draw_image(painter, obj)
            elif isinstance(obj, TextObject):
                self._draw_text(painter, obj, preview)

    def _draw_text(
        self, painter: QPainter, obj: TextObject, preview: DragPreview
    ) -> None:
        """セリフ。手動改行のみ（要件定義 6.5、9章）。

        その場編集の最中は描かない。編集中の文字が二重に見えてしまう。
        """
        if preview.editing_text_id == obj.id:
            return

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

    def _balloon_path(self, balloon: BalloonObject) -> QPainterPath:
        """本体としっぽを**1つの輪郭**にまとめた形。

        別々に描くと継ぎ目に枠線が残り、しっぽが貼り付けた三角形に見える。
        塗りを重ねて線を隠す手もあるが、半透明や色付きの塗りで破綻する。
        図形を合成してから一度だけ縁取るほうが、どの配色でも正しい。
        """
        settings = self.state.balloon_settings
        path = QPainterPath()
        path.addPolygon(polygon_of(balloon_outline(balloon, settings)))
        path.closeSubpath()

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
        path = self._balloon_path(balloon)
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
