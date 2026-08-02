"""ページの表示と、コマの操作。

**シーンの座標をそのまま mm として使う。** 拡大縮小は表示側の変換だけで行い、
モデルの値は一切触らない。おかげでどの倍率でも同じ計算が使え、
当たり判定も `manga_layout.layout`（Qt を使わない側）に任せられる。

コマを `QGraphicsItem` にはせず、その都度描いている。Undo でモデルの実体が
差し替わるため、部品を保持すると古い `Panel` を掴んだままになりやすい。
描き直しの費用は1ページぶんなので、素直に毎回描くほうが安全で速い。
"""

from __future__ import annotations

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from ..geometry import Rect
from ..layout import (
    default_panel_rect,
    handle_at,
    handle_positions,
    panel_at,
    resize_rect,
    set_panel_rect,
    snap_candidates,
    snap_moved_rect,
    snap_point,
    split_panel,
)
from ..model import BalloonObject, Panel, TextObject
from .state import TOOL_PANEL, TOOL_SELECT, TOOL_SPLIT_H, TOOL_SPLIT_V, EditorState

CANVAS_BG = QColor("#3C3F41")
PAGE_BG = QColor("#FFFFFF")
PAGE_EDGE = QColor("#8A8A8A")
PAGE_SHADOW = QColor(0, 0, 0, 70)
MARGIN_GUIDE = QColor("#B7CEE8")
PANEL_FILL = QColor("#F4F4F4")
ACCENT = QColor("#1E88E5")
PLACEHOLDER = QColor("#9FB2BF")

# 画面上での大きさ（ピクセル）。表示倍率で割って mm に直して使う
HANDLE_PX = 9.0
# これ以下の大きさで離した場合、ドラッグではなくクリックとみなして
# 既定の大きさのコマを置く
MIN_CREATE_PX = 6.0
# 吸着が効き始める距離（ピクセル）
SNAP_PX = 8.0

_HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}


def _qrect(rect: Rect) -> QRectF:
    return QRectF(rect.x, rect.y, rect.w, rect.h)


def _cosmetic_pen(color: QColor, width: float = 1.0, style=Qt.PenStyle.SolidLine) -> QPen:
    """表示倍率によらず同じ太さで描かれる線。

    目安線や選択枠のような「画面の道具」に使う。作品の一部である
    コマ枠には使わない（そちらは mm で太さが決まる）。
    """
    pen = QPen(color, width, style)
    pen.setCosmetic(True)
    return pen


class PageScene(QGraphicsScene):
    """1ページぶんの描画。部品を持たず、その場で描く。"""

    def __init__(self, state: EditorState):
        super().__init__()
        self.state = state
        # 操作中の下書き。確定するまでモデルには触らない
        self.preview_rect: Rect | None = None
        self.split_preview: tuple[Rect, bool, float] | None = None
        self.update_scene_rect()

    def update_scene_rect(self) -> None:
        size = self.state.page.size
        pad = max(size.w, size.h) * 0.25
        self.setSceneRect(-pad, -pad, size.w + pad * 2, size.h + pad * 2)

    # -- 用紙とコマ --------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, CANVAS_BG)

        page = self.state.page
        page_rect = QRectF(0.0, 0.0, page.size.w, page.size.h)

        painter.fillRect(page_rect.translated(1.5, 1.5), PAGE_SHADOW)
        painter.fillRect(page_rect, PAGE_BG)
        painter.setPen(_cosmetic_pen(PAGE_EDGE))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(page_rect)

        self._draw_margin(painter, page)
        for panel in sorted(page.panels, key=lambda p: p.z):
            self._draw_panel(painter, panel)
        self._draw_placeholders(painter, page)

    def _draw_margin(self, painter: QPainter, page) -> None:
        """基本枠（内側の目安線）。作品には出ない、置き場所の目印。"""
        m = self.state.settings.margin
        if m <= 0:
            return
        painter.setPen(_cosmetic_pen(MARGIN_GUIDE, 1.0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(m, m, page.size.w - m * 2, page.size.h - m * 2))

    def _draw_panel(self, painter: QPainter, panel: Panel) -> None:
        polygon = QPolygonF([QPointF(x, y) for x, y in panel.shape.points])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(PANEL_FILL))
        painter.drawPolygon(polygon)

        if panel.border.visible and panel.border.width > 0:
            # 枠線は作品の一部なので、太さは mm のまま（倍率で見た目が変わる）
            painter.setPen(QPen(QColor(panel.border.color), panel.border.width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(polygon)

    def _draw_placeholders(self, painter: QPainter, page) -> None:
        """吹き出しとセリフの仮表示。

        本来の描画は Day 18〜24。ここで何も出さないと、読み込んだ作品の
        セリフが消えたように見えてしまうため、位置だけ示しておく。
        """
        painter.setPen(_cosmetic_pen(PLACEHOLDER, 1.0, Qt.PenStyle.DotLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for obj in page.floating:
            if isinstance(obj, BalloonObject):
                painter.drawEllipse(_qrect(obj.rect))
                if obj.tail.enabled:
                    cx, cy = obj.rect.center
                    painter.drawLine(QLineF(cx, cy, obj.tail.tip[0], obj.tail.tip[1]))
            elif isinstance(obj, TextObject):
                painter.drawRect(_qrect(obj.rect))

    # -- 選択と下書き ------------------------------------------------------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        scale = painter.transform().m11()
        if scale <= 0:
            return

        panel = self.state.selected_panel
        if panel is not None and self.preview_rect is None:
            self._draw_selection(painter, panel.shape.bounds(), scale)

        if self.preview_rect is not None:
            painter.setPen(_cosmetic_pen(ACCENT, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(30, 136, 229, 30)))
            painter.drawRect(_qrect(self.preview_rect))
            self._draw_size_hint(painter, self.preview_rect)

        if self.split_preview is not None:
            bounds, horizontal, position = self.split_preview
            painter.setPen(_cosmetic_pen(ACCENT, 1.5, Qt.PenStyle.DashLine))
            if horizontal:
                line = QLineF(bounds.x, position, bounds.right, position)
            else:
                line = QLineF(position, bounds.y, position, bounds.bottom)
            painter.drawLine(line)

    def _draw_selection(self, painter: QPainter, bounds: Rect, scale: float) -> None:
        painter.setPen(_cosmetic_pen(ACCENT, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(_qrect(bounds))

        size = HANDLE_PX / scale
        painter.setPen(_cosmetic_pen(ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        for cx, cy in handle_positions(bounds).values():
            painter.drawRect(QRectF(cx - size / 2, cy - size / 2, size, size))

    def _draw_size_hint(self, painter: QPainter, rect: Rect) -> None:
        """操作中のコマの寸法を、その場に mm で出す。

        文字は表示倍率の影響を受けないよう、変換を外してから描く。
        位置だけは外す前の変換で求めておく。
        """
        corner = painter.transform().map(QPointF(rect.x, rect.y))
        painter.save()
        painter.resetTransform()
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(
            QPointF(corner.x() + 4, corner.y() - 6), f"{rect.w:.1f} × {rect.h:.1f} mm"
        )
        painter.restore()


class PageView(QGraphicsView):
    """マウスとキーの受け口。当たり判定は mm 空間で行う。"""

    def __init__(self, state: EditorState):
        # Qt の初期化より先に属性を持たせない（基底の __init__ が済むまで代入できない）
        scene = PageScene(state)
        super().__init__(scene)
        self.state = state
        self._scene = scene

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        # ここで背景ブラシを設定してはいけない。設定すると Qt はビュー側で
        # 背景を塗って終わりにし、シーンの drawBackground を呼ばなくなる
        # （＝用紙もコマも描かれない）

        self._mode: str | None = None
        self._handle: str | None = None
        self._origin_rect: Rect | None = None
        self._grab: tuple[float, float] = (0.0, 0.0)
        self._space_held = False
        self._pan_from: QPointF | None = None

        state.changed.connect(self._on_model_changed)
        state.selection_changed.connect(self.viewport().update)
        state.tool_changed.connect(self._on_tool_changed)

        self.scale(2.2, 2.2)  # A4 が画面に収まる程度の初期倍率

    # -- 便利 --------------------------------------------------------------

    @property
    def view_scale(self) -> float:
        return self.transform().m11()

    def _mm(self, event) -> tuple[float, float]:
        point = self.mapToScene(event.position().toPoint())
        return point.x(), point.y()

    def _snap_threshold(self) -> float:
        return SNAP_PX / self.view_scale

    def _candidates(self, exclude_id: str | None):
        return snap_candidates(self.state.page, exclude_id, self.state.settings)

    def _on_model_changed(self) -> None:
        self._scene.update_scene_rect()
        self.viewport().update()

    def _on_tool_changed(self) -> None:
        self._scene.split_preview = None
        self._reset_drag()
        # 道具ごとに形が変わるので、次に動かすまで前の形が残らないようにする
        self.viewport().unsetCursor()
        self.viewport().update()

    def _reset_drag(self) -> None:
        self._mode = None
        self._handle = None
        self._origin_rect = None
        self._scene.preview_rect = None

    def fit_page(self) -> None:
        page = self.state.page
        self.fitInView(QRectF(-5, -5, page.size.w + 10, page.size.h + 10), Qt.AspectRatioMode.KeepAspectRatio)

    # -- 拡大縮小・画面移動 ------------------------------------------------

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # -- マウス ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if self._space_held or event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        x, y = self._mm(event)
        tool = self.state.tool

        if tool in (TOOL_SPLIT_H, TOOL_SPLIT_V):
            self._apply_split(x, y)
            event.accept()
            return

        handle = self._handle_at_point(x, y)
        hit = panel_at(self.state.page, x, y)

        # コマ追加の道具でも、既にあるコマやそのつまみの上なら編集を優先する。
        # 何も無いところを押したときだけ新しいコマを作る
        if tool == TOOL_PANEL and handle is None and hit is None:
            self._mode = "create"
            self._grab = (x, y)
            self._scene.preview_rect = Rect(x, y, 0.0, 0.0)
            event.accept()
            return

        if handle is not None and self.state.selected_panel is not None:
            self._mode = "resize"
            self._handle = handle
            self._origin_rect = self.state.selected_panel.shape.bounds()
            self._scene.preview_rect = self._origin_rect
            event.accept()
            return

        self.state.select(hit.id if hit is not None else None)
        if hit is not None:
            self._mode = "move"
            self._origin_rect = hit.shape.bounds()
            self._grab = (x, y)
            self._scene.preview_rect = self._origin_rect
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return

        x, y = self._mm(event)

        if self.state.tool in (TOOL_SPLIT_H, TOOL_SPLIT_V):
            self._update_split_preview(x, y)
            event.accept()
            return

        if self._mode is None:
            self._update_cursor(x, y)
            super().mouseMoveEvent(event)
            return

        threshold = self._snap_threshold()

        if self._mode == "create":
            rect = Rect(self._grab[0], self._grab[1], x - self._grab[0], y - self._grab[1])
            xs, ys = self._candidates(None)
            self._scene.preview_rect = snap_moved_rect(rect.normalized(), xs, ys, threshold)

        elif self._mode == "move" and self._origin_rect is not None:
            moved = self._origin_rect.translated(x - self._grab[0], y - self._grab[1])
            xs, ys = self._candidates(self.state.selected_id)
            self._scene.preview_rect = snap_moved_rect(moved, xs, ys, threshold)

        elif self._mode == "resize" and self._origin_rect is not None and self._handle:
            xs, ys = self._candidates(self.state.selected_id)
            sx, sy = snap_point(self._handle, x, y, xs, ys, threshold)
            self._scene.preview_rect = resize_rect(
                self._origin_rect, self._handle, sx, sy, self.state.settings.min_panel_size
            )

        self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_from is not None:
            self._pan_from = None
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_held else Qt.CursorShape.ArrowCursor
            )
            if not self._space_held:
                self.viewport().unsetCursor()
            event.accept()
            return

        if self._mode is None:
            super().mouseReleaseEvent(event)
            return

        preview = self._scene.preview_rect
        mode, origin, press = self._mode, self._origin_rect, self._grab
        self._reset_drag()

        if preview is not None:
            if mode == "create":
                self._apply_create(preview, press)
            elif mode == "move" and origin is not None:
                self._apply_move(origin, preview)
            elif mode == "resize":
                self._apply_resize(preview)

        self.viewport().update()
        event.accept()

    def _handle_at_point(self, x: float, y: float) -> str | None:
        """その位置にある、選択中のコマのつまみ。無ければ None。"""
        panel = self.state.selected_panel
        if panel is None:
            return None
        return handle_at(panel.shape.bounds(), x, y, HANDLE_PX / self.view_scale)

    def _update_cursor(self, x: float, y: float) -> None:
        handle = self._handle_at_point(x, y)
        if handle is not None:
            self.viewport().setCursor(_HANDLE_CURSORS[handle])
        elif panel_at(self.state.page, x, y) is not None:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.state.tool == TOOL_PANEL:
            # ここを押せばコマが作られる、と分かるようにする
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().unsetCursor()

    # -- 確定 --------------------------------------------------------------

    def _apply_create(self, rect: Rect, press: tuple[float, float]) -> None:
        """コマを1つ作り、選択して編集できる状態にする。

        作り終えたら選択の道具に戻す。追加のあとは位置と大きさを整える
        のが普通で、続けて追加することは少ない。道具を残しておくと、
        整えようとした操作が次のコマの追加になってしまう（要件定義 6.9）。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            # ドラッグと呼べない動き。クリックで置いたものとして扱う
            rect = default_panel_rect(self.state.page, press[0], press[1], self.state.settings)

        with self.state.edit("コマの追加") as project:
            panel = project.add_panel(self.state.page, rect)
        self.state.select(panel.id)
        self.state.set_tool(TOOL_SELECT)
        self.state.message.emit(
            f"コマを追加しました（{rect.w:.1f} × {rect.h:.1f} mm）。"
            "位置と大きさを調整できます"
        )

    def _apply_move(self, origin: Rect, final: Rect) -> None:
        dx, dy = final.x - origin.x, final.y - origin.y
        if dx == 0.0 and dy == 0.0:
            return
        panel_id = self.state.selected_id
        if panel_id is None:
            return
        with self.state.edit("コマの移動") as project:
            project.pages[self.state.page_index].move_panel(panel_id, dx, dy)

    def _apply_resize(self, rect: Rect) -> None:
        panel = self.state.selected_panel
        if panel is None or panel.shape.bounds() == rect:
            return
        panel_id = panel.id
        with self.state.edit("コマの大きさ変更") as project:
            set_panel_rect(project.pages[self.state.page_index].panel(panel_id), rect)
        self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")

    # -- 分割 --------------------------------------------------------------

    def _split_target(self, x: float, y: float):
        panel = panel_at(self.state.page, x, y)
        if panel is None or panel.shape.as_rect() is None:
            return None
        return panel

    def _update_split_preview(self, x: float, y: float) -> None:
        panel = self._split_target(x, y)
        if panel is None:
            self._scene.split_preview = None
        else:
            horizontal = self.state.tool == TOOL_SPLIT_H
            bounds = panel.shape.bounds()
            self._scene.split_preview = (bounds, horizontal, y if horizontal else x)
        self.viewport().update()

    def _apply_split(self, x: float, y: float) -> None:
        panel = self._split_target(x, y)
        if panel is None:
            self.state.message.emit("コマの上でクリックしてください")
            return

        horizontal = self.state.tool == TOOL_SPLIT_H
        panel_id = panel.id
        try:
            with self.state.edit("コマの分割") as project:
                split_panel(
                    project,
                    project.pages[self.state.page_index],
                    panel_id,
                    horizontal=horizontal,
                    position=y if horizontal else x,
                    settings=self.state.settings,
                )
        except ValueError as e:
            self.state.message.emit(str(e))
            return

        self._scene.split_preview = None
        self.state.select(panel_id)
        self.state.message.emit("コマを分割しました")

    def leaveEvent(self, event) -> None:
        if self._scene.split_preview is not None:
            self._scene.split_preview = None
            self.viewport().update()
        super().leaveEvent(event)
