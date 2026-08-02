"""1ページぶんの絵を描く処理。

画面（`PageScene`）とページ一覧のサムネイルが**同じ経路**を通るようにするため、
用紙・コマ・画像・吹き出し・セリフの描画をここへ集めてある。分けて書くと、
片方だけ直したときにサムネイルと本画面が食い違い、しかも気づきにくい。

選択枠・つまみ・下書きの矩形といった「画面の道具」はここに入れない。
それらは作品の一部ではないので、サムネイルにも書き出しにも出したくない。

シーンの座標はそのまま mm（要件定義 3章）。倍率は呼ぶ側が painter に
掛けてから渡す。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF

from ..geometry import Rect
from ..layout import balloon_outline, slant_polygons, tail_triangle
from ..model import BalloonObject, Font, ImageObject, Page, Panel, TextObject

PAGE_BG = QColor("#FFFFFF")
PAGE_EDGE = QColor("#8A8A8A")
PAGE_SHADOW = QColor(0, 0, 0, 70)
MARGIN_GUIDE = QColor("#B7CEE8")
PANEL_FILL = QColor("#F4F4F4")
PLACEHOLDER = QColor("#9FB2BF")
MISSING_IMAGE = QColor("#D9534F")

# 文字の大きさは mm で持っているが、Qt のフォントは**整数の画素数**でしか
# 指定できない。3.5mm をそのまま渡すと 3 か 4 に丸められ、狙った大きさに
# ならない。いったんこの倍率で大きく作り、描くときに同じ倍率で縮めて合わせる
TEXT_FONT_SCALE = 20.0

TEXT_ALIGN_FLAGS = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
}


def qrect(rect: Rect) -> QRectF:
    return QRectF(rect.x, rect.y, rect.w, rect.h)


def polygon_of(points) -> QPolygonF:
    """mm の点列を Qt の多角形にする。座標はそのまま（シーン＝mm）。"""
    return QPolygonF([QPointF(x, y) for x, y in points])


def cosmetic_pen(
    color: QColor, width: float = 1.0, style=Qt.PenStyle.SolidLine
) -> QPen:
    """表示倍率によらず同じ太さで描かれる線。

    目安線や選択枠のような「画面の道具」に使う。作品の一部である
    コマ枠には使わない（そちらは mm で太さが決まる）。
    """
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


def text_font(font: Font, scale: float = TEXT_FONT_SCALE) -> QFont:
    """mm 指定の書式から Qt のフォントを作る。

    `scale` 倍の大きさで作る。使う側は同じ倍率で縮めてから描くこと。
    そうしないと文字が `scale` 倍で出る。
    """
    qfont = QFont(font.family)
    qfont.setPixelSize(max(1, round(font.size_mm * scale)))
    qfont.setBold(font.bold)
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
    # その場編集中のセリフ。二重に見えないよう、下地を描かない
    editing_text_id: str | None = None


NO_PREVIEW = DragPreview()


class PageRenderer:
    """ページの中身を描く。部品を持たず、その都度描く。

    `QGraphicsItem` を持たない理由は要件定義 11章のとおり。Undo でモデルの
    実体が差し替わるため、部品を保持すると古い `Panel` を掴んだままになる。

    `state` からは設定と画像の縮小版だけを読む。**描くページは引数で渡す。**
    表示中のページに縛られないので、サムネイル一覧が同じ処理を使える。
    """

    def __init__(self, state) -> None:
        self.state = state

    # -- 全体 --------------------------------------------------------------

    def draw(
        self,
        painter: QPainter,
        page: Page,
        preview: DragPreview = NO_PREVIEW,
        *,
        guides: bool = True,
        shadow: bool = True,
    ) -> None:
        """用紙とその中身を描く。

        `guides` は基本枠の目安線、`shadow` は用紙の影。どちらも作品には
        出ないので、サムネイルでは切る（小さく描くと線が潰れて汚れになる）。
        """
        self.draw_paper(painter, page, shadow=shadow)
        if guides:
            self.draw_margin(painter, page)
        for panel in sorted(page.panels, key=lambda p: p.z):
            self.draw_panel(painter, page, panel, preview)
        self.draw_floating(painter, page, preview)

    def draw_paper(self, painter: QPainter, page: Page, *, shadow: bool = True) -> None:
        rect = QRectF(0.0, 0.0, page.size.w, page.size.h)
        if shadow:
            painter.fillRect(rect.translated(1.5, 1.5), PAGE_SHADOW)
        painter.fillRect(rect, PAGE_BG)
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
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(PANEL_FILL))
        painter.drawPolygon(polygon)

        if panel.children:
            self._draw_children(painter, panel, polygon)

        if panel.border.visible and panel.border.width > 0:
            # 枠線は作品の一部なので、太さは mm のまま（倍率で見た目が変わる）
            # 画像より後に描く。先に描くと、はみ出した絵が枠線を覆ってしまう
            painter.setPen(QPen(QColor(panel.border.color), panel.border.width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)

    def _draw_children(
        self, painter: QPainter, panel: Panel, polygon: QPolygonF
    ) -> None:
        """コマの中の画像を、コマの形で切り抜いて描く。

        切り抜きはコマのポリゴンそのものに対して行う。斜めのコマでも
        そのまま効く（要件定義 4章でポリゴン保存にした狙いのひとつ）。
        """
        path = QPainterPath()
        path.addPolygon(polygon)
        path.closeSubpath()

        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        for image in sorted(panel.children, key=lambda i: i.z):
            self._draw_image(painter, image)
        painter.restore()

    def _draw_image(self, painter: QPainter, image: ImageObject) -> None:
        preview = self.state.preview(image.asset)
        if preview is None:
            self._draw_missing(painter, image)
            return
        painter.setOpacity(image.opacity)
        painter.drawImage(qrect(image.rect), preview.image)
        painter.setOpacity(1.0)

    def _draw_missing(self, painter: QPainter, image: ImageObject) -> None:
        """実体が無い・壊れている画像の場所。

        何も描かないと、絵が消えたのか最初から無かったのか分からない。
        枠だけ出して「ここに1枚あるはず」と示す。
        """
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
        """ページ直下のもの。z の小さい順に重ねる。"""
        for obj in sorted(page.floating, key=lambda f: f.z):
            if isinstance(obj, BalloonObject):
                self._draw_balloon(painter, obj, preview)
            elif isinstance(obj, TextObject):
                self._draw_text(painter, obj, preview)

    def _draw_text(
        self, painter: QPainter, obj: TextObject, preview: DragPreview
    ) -> None:
        """セリフ。横書き・手動改行のみ（要件定義 6.5、9章）。

        その場編集の最中は描かない。編集中の文字が二重に見えてしまう。
        """
        if preview.editing_text_id == obj.id:
            return

        if not obj.content:
            # 空のセリフは枠だけ出す。何も描かないと、作った直後に
            # 見失って選び直せなくなる
            painter.setPen(cosmetic_pen(PLACEHOLDER, 1.0, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(qrect(obj.rect))
            return

        scale = TEXT_FONT_SCALE
        painter.save()
        painter.setFont(text_font(obj.font, scale))
        painter.setPen(QPen(QColor("#000000")))
        # 大きく作ったフォントを縮めて mm に合わせる。矩形も同じ倍率で拡げる
        painter.scale(1.0 / scale, 1.0 / scale)
        flags = (
            TEXT_ALIGN_FLAGS.get(obj.align, Qt.AlignmentFlag.AlignHCenter)
            | Qt.AlignmentFlag.AlignVCenter
            # 折り返さない（要件定義 9章: MVP は手動改行のみ）。
            # 枠に収まらない字も隠さずに出し、はみ出しに気づけるようにする
            | Qt.TextFlag.TextDontClip
        )
        painter.drawText(
            QRectF(
                obj.rect.x * scale,
                obj.rect.y * scale,
                obj.rect.w * scale,
                obj.rect.h * scale,
            ),
            flags,
            obj.content,
        )
        painter.restore()

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
            # コマの枠線と同じく、太さは mm（作品の一部なので倍率で変わる）
            pen = QPen(QColor(balloon.border.color), balloon.border.width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
