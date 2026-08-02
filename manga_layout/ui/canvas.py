"""ページの表示と、コマの操作。

**シーンの座標をそのまま mm として使う。** 拡大縮小は表示側の変換だけで行い、
モデルの値は一切触らない。おかげでどの倍率でも同じ計算が使え、
当たり判定も `manga_layout.layout`（Qt を使わない側）に任せられる。

コマを `QGraphicsItem` にはせず、その都度描いている。Undo でモデルの実体が
差し替わるため、部品を保持すると古い `Panel` を掴んだままになりやすい。
描き直しの費用は1ページぶんなので、素直に毎回描くほうが安全で速い。

用紙の中身そのものは `render.PageRenderer` が描く。ページ一覧のサムネイルと
**同じ経路**を通すため。ここに残っているのは選択枠・つまみ・下書きといった
「画面の道具」で、作品には出ない。
"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import QGraphicsScene, QGraphicsTextItem, QGraphicsView

from ..errors import MangaLayoutError
from ..geometry import Rect
from ..layout import (
    aspect_of,
    attach_target,
    balloon_at,
    default_balloon_rect,
    default_panel_rect,
    default_tail_tip,
    handle_at,
    handle_positions,
    image_at,
    panel_at,
    resize_rect,
    resize_rect_keep_aspect,
    set_panel_rect,
    set_slant_pair_rect,
    clamp_slant_rect,
    clamp_slant_ratio,
    snap_candidates,
    root_y_at,
    slant_boundary_x,
    slant_handle_point,
    slant_ratio_at,
    snap_moved_rect,
    snap_point,
    split_panel,
    split_panel_slant,
    tail_root_point,
    text_at,
)
from ..model import SLANT_RIGHT, BalloonObject, ImageObject, Panel, TextObject
from .render import (
    TEXT_ALIGN_FLAGS,
    TEXT_FONT_SCALE,
    DragPreview,
    PageRenderer,
    cosmetic_pen,
    polygon_of,
    qrect,
    text_font,
)
from .state import (
    BALLOON_TOOLS,
    DEFAULT_TEXT_SIZE,
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_TEXT,
    EditorState,
)

# 分割の道具。押した位置で1回きり切る、という扱いが共通している
SPLIT_TOOLS = (TOOL_SPLIT_H, TOOL_SPLIT_V, TOOL_SPLIT_SLANT)

CANVAS_BG = QColor("#3C3F41")
ACCENT = QColor("#1E88E5")
# 画像を選んでいるときの色。コマの選択（青）と見分けるために変える。
# 同じ色だと、いま動かすのがコマなのか中の絵なのか分からない
IMAGE_ACCENT = QColor("#FB8C00")
# 吹き出しを選んでいるときの色。コマ（青）・画像（橙）と重ならない色にする
BALLOON_ACCENT = QColor("#8E24AA")

# 画面上での大きさ（ピクセル）。表示倍率で割って mm に直して使う
HANDLE_PX = 9.0
# 斜めの境界を掴める範囲（ピクセル）。**描く印の大きさは `HANDLE_PX` のまま。**
# 印を大きく描くとコマの上に居座って絵の邪魔になるので、
# 「小さく描いて広く拾う」形にしてある
SLANT_HANDLE_PX = 50.0
# これ以下の大きさで離した場合、ドラッグではなくクリックとみなして
# 既定の大きさのコマを置く
MIN_CREATE_PX = 6.0
# 吸着が効き始める距離（ピクセル）
SNAP_PX = 8.0

# ホイール1目盛り／キー1回あたりの倍率。キーのほうが回数を稼ぎにくいので大きめ
WHEEL_ZOOM_STEP = 1.15
KEY_ZOOM_STEP = 1.25

# 表示倍率の下限・上限（画面のピクセル数 ÷ mm）。
# 際限なく縮小・拡大できると、行き過ぎたときに戻ってこられなくなる
MIN_VIEW_SCALE = 0.3
MAX_VIEW_SCALE = 40.0

# 拡大・縮小のキー。`+` は配列によって Shift+= になるので `=` も拾う
ZOOM_IN_KEYS = (Qt.Key.Key_Plus, Qt.Key.Key_Equal)
ZOOM_OUT_KEYS = (Qt.Key.Key_Minus,)

# ファイル選択ダイアログとドロップ受け入れで共通の対象。
# assets.sniff_format が見分けられる形式に合わせてある
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
IMAGE_FILE_FILTER = "画像 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;すべてのファイル (*)"

# 縦と横が同時に変わるつまみ。ここでだけ等比かどうかが問題になる
CORNER_HANDLES = ("nw", "ne", "se", "sw")
ASPECT_HINT = "Shift キーを押しながらドラッグで縦横比率を維持"
ASPECT_HINT_HELD = "縦横比率を維持中（Shift）"

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


class PageScene(QGraphicsScene):
    """1ページぶんの描画。部品を持たず、その場で描く。"""

    def __init__(self, state: EditorState):
        super().__init__()
        self.state = state
        self.renderer = PageRenderer(state)
        # 操作中の下書き。確定するまでモデルには触らない
        self.preview_rect: Rect | None = None
        # 分割線の下見。両端の座標で持つ。斜め・横・縦を同じ描き方で扱える
        self.split_preview: tuple[tuple[float, float], tuple[float, float]] | None = None
        # しっぽの先端をドラッグ中の (吹き出しの id, 先端の位置)
        self.tail_preview: tuple[str, tuple[float, float]] | None = None
        # しっぽの付け根を上下にずらしている最中の (吹き出しの id, 割合)
        self.root_preview: tuple[str, float] | None = None
        # 斜めの境界をずらしている最中の (組のどちらかのコマの id, 割合)
        self.slant_preview: tuple[str, float] | None = None
        # その場編集中のセリフ。編集中は下地を描かない
        self.editing_text_id: str | None = None
        self.update_scene_rect()

    def update_scene_rect(self) -> None:
        size = self.state.page.size
        pad = max(size.w, size.h) * 0.25
        self.setSceneRect(-pad, -pad, size.w + pad * 2, size.h + pad * 2)

    # -- 用紙とコマ --------------------------------------------------------

    def drag_preview(self) -> DragPreview:
        """いまドラッグ中の下見を、描画側へ渡せる形にまとめる。"""
        return DragPreview(
            slant=self.slant_preview,
            tail=self.tail_preview,
            root=self.root_preview,
            editing_text_id=self.editing_text_id,
        )

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, CANVAS_BG)
        self.renderer.draw(painter, self.state.page, self.drag_preview())

    # -- 選択と下書き ------------------------------------------------------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        scale = painter.transform().m11()
        if scale <= 0:
            return

        bounds = self.state.selected_bounds
        balloon = self.state.selected_balloon
        if bounds is not None and self.preview_rect is None:
            self._draw_selection(painter, bounds, scale, self._accent())
        if balloon is not None:
            self._draw_tail_handle(painter, balloon, scale)
        self._draw_slant_handle(painter, scale)

        if self.preview_rect is not None:
            painter.setPen(cosmetic_pen(ACCENT, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(QColor(30, 136, 229, 30)))
            painter.drawRect(qrect(self.preview_rect))
            self._draw_size_hint(painter, self.preview_rect)

        if self.split_preview is not None:
            (x1, y1), (x2, y2) = self.split_preview
            painter.setPen(cosmetic_pen(ACCENT, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QLineF(x1, y1, x2, y2))

    def _accent(self) -> QColor:
        """選択枠の色。何を選んでいるかで変える。"""
        if self.state.selected_image is not None:
            return IMAGE_ACCENT
        if self.state.selected_balloon is not None:
            return BALLOON_ACCENT
        return ACCENT

    def _draw_tail_handle(
        self, painter: QPainter, balloon: BalloonObject, scale: float
    ) -> None:
        """しっぽの先端（丸）と付け根（ひし形）を掴む印。

        角を掴むつまみ（四角）と形を変える。同じ形だと、大きさを変える
        つもりで引っぱってしまう。先端と付け根も互いに別の形にして、
        どちらを動かすのか掴む前に分かるようにする。
        """
        balloon = self.renderer.with_preview_tail(balloon, self.drag_preview())
        if not balloon.tail.enabled:
            return

        size = HANDLE_PX / scale
        painter.setPen(cosmetic_pen(BALLOON_ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))

        tx, ty = balloon.tail.tip
        painter.drawEllipse(QPointF(tx, ty), size / 2.0, size / 2.0)

        root = tail_root_point(balloon, self.state.balloon_settings)
        if root is None:
            return
        half = size / 2.0
        painter.drawPolygon(
            polygon_of(
                (
                    (root[0], root[1] - half),
                    (root[0] + half, root[1]),
                    (root[0], root[1] + half),
                    (root[0] - half, root[1]),
                )
            )
        )

    def slant_handle(self) -> tuple[float, float] | None:
        """斜めの境界をつまむ位置。選んでいなければ None。

        描く側と掴む側で同じ答えが要るので、1箇所にまとめてある。
        ずらしている最中は下見の位置に付いてくる。
        """
        pair = self.state.selected_slant_pair
        if pair is None:
            return None
        ratio = pair.ratio
        if self.slant_preview is not None and self.slant_preview[0] in pair.members():
            ratio = self.slant_preview[1]
        return slant_handle_point(self.state.page.slant_bounds(pair), ratio)

    def _draw_slant_handle(self, painter: QPainter, scale: float) -> None:
        """境界をずらすつまみ。左右向きの矢羽根で描く。

        角の四角つまみ・しっぽのひし形と形を変える。同じ形だと、
        大きさを変えるつもりで境界を引っぱってしまう。
        """
        point = self.slant_handle()
        if point is None:
            return
        size = HANDLE_PX / scale
        half = size / 2.0
        x, y = point
        painter.setPen(cosmetic_pen(ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawPolygon(
            polygon_of(
                (
                    (x - size, y),
                    (x - half, y - half),
                    (x + half, y - half),
                    (x + size, y),
                    (x + half, y + half),
                    (x - half, y + half),
                )
            )
        )

    def _draw_selection(
        self, painter: QPainter, bounds: Rect, scale: float, color: QColor = ACCENT
    ) -> None:
        painter.setPen(cosmetic_pen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(qrect(bounds))

        size = HANDLE_PX / scale
        painter.setPen(cosmetic_pen(color, 1.2))
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


class TextEditorItem(QGraphicsTextItem):
    """その場編集の入力欄。

    画面に重ねた別の部品ではなく、シーンに置いた項目にしてある。
    拡大縮小や画面移動に自動で付いてくるので、位置合わせを自分で
    やらずに済む（要件定義 6.5「画面上でその場編集」）。
    """

    def __init__(self, view: "PageView", text: TextObject):
        super().__init__(text.content)
        self._view = view
        self._closing = False

        scale = TEXT_FONT_SCALE
        self.setFont(text_font(text.font, scale))
        self.setDefaultTextColor(QColor("#000000"))

        option = self.document().defaultTextOption()
        option.setAlignment(
            TEXT_ALIGN_FLAGS.get(text.align, Qt.AlignmentFlag.AlignHCenter)
        )
        # 折り返さない。確定後の描画と食い違うと、入力中と結果で
        # 改行位置が変わって驚く（要件定義 9章: 手動改行のみ）
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        self.document().setDefaultTextOption(option)
        self.document().setDocumentMargin(0.0)
        self.setTextWidth(text.rect.w * scale)

        self.setScale(1.0 / scale)
        self.setZValue(1000)
        self._center_in(text.rect)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)

    def _center_in(self, rect: Rect) -> None:
        """確定後の描画（上下中央）に合わせて置く。"""
        height = self.boundingRect().height() / TEXT_FONT_SCALE
        self.setPos(rect.x, rect.y + max(0.0, (rect.h - height) / 2.0))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._view.finish_text_edit(commit=False)
            event.accept()
            return
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Enter だけなら改行。確定は Ctrl+Enter
            self._view.finish_text_edit(commit=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # 取り外す最中の focusOut で呼び戻されないようにする
        if not self._closing:
            self._view.finish_text_edit(commit=True)

    def close_editor(self) -> str:
        """入力内容を返して、自分を畳む。"""
        self._closing = True
        return self.toPlainText()


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
        self.setAcceptDrops(True)
        # ここで背景ブラシを設定してはいけない。設定すると Qt はビュー側で
        # 背景を塗って終わりにし、シーンの drawBackground を呼ばなくなる
        # （＝用紙もコマも描かれない）

        self._mode: str | None = None
        self._handle: str | None = None
        self._origin_rect: Rect | None = None
        self._grab: tuple[float, float] = (0.0, 0.0)
        self._space_held = False
        self._pan_from: QPointF | None = None
        # 状態表示に出している案内。同じ文を出し続けないための控え
        self._hint_shown: str | None = None
        self._text_editor: TextEditorItem | None = None

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
        self._hint_shown = None
        self._scene.preview_rect = None
        self._scene.tail_preview = None
        self._scene.root_preview = None
        self._scene.slant_preview = None

    def fit_page(self) -> None:
        page = self.state.page
        self.fitInView(QRectF(-5, -5, page.size.w + 10, page.size.h + 10), Qt.AspectRatioMode.KeepAspectRatio)

    # -- 拡大縮小・画面移動 ------------------------------------------------

    def zoom_percent(self) -> float:
        """いまの表示倍率（%）。100% で用紙が原寸に見える。

        画面の物理的な解像度から求める。mm で作る道具なので、
        「紙に刷ったときの大きさ」を基準にできるほうが分かりやすい。
        """
        screen = self.screen()
        if screen is None or screen.physicalDotsPerInch() <= 0:
            return self.view_scale * 100.0
        return self.view_scale / (screen.physicalDotsPerInch() / 25.4) * 100.0

    def zoom_by(self, factor: float, *, at_mouse: bool = True) -> bool:
        """表示倍率を `factor` 倍する。上下限で止める。変わったら True。

        ホイールならマウスの位置を、キーなら画面の中心を動かさない。
        キーで押したときにカーソルの下を軸にすると、画面外へ飛んでいく。
        """
        target = self.view_scale * factor
        clamped = min(max(target, MIN_VIEW_SCALE), MAX_VIEW_SCALE)
        factor = clamped / self.view_scale
        if abs(factor - 1.0) < 1e-9:
            return False

        previous = self.transformationAnchor()
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
            if at_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.scale(factor, factor)
        self.setTransformationAnchor(previous)
        self.state.message.emit(f"表示倍率 {self.zoom_percent():.0f}%")
        return True

    def zoom_in(self, *, at_mouse: bool = False) -> bool:
        return self.zoom_by(KEY_ZOOM_STEP, at_mouse=at_mouse)

    def zoom_out(self, *, at_mouse: bool = False) -> bool:
        return self.zoom_by(1.0 / KEY_ZOOM_STEP, at_mouse=at_mouse)

    def wheelEvent(self, event) -> None:
        """ホイールは拡大・縮小に割り当てる。

        画面の上下移動はスペース+ドラッグと中ボタン+ドラッグで足りる。
        文字の細部を見るために倍率を変える回数のほうが、ずっと多い。
        """
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        self.zoom_by(WHEEL_ZOOM_STEP if delta > 0 else 1.0 / WHEEL_ZOOM_STEP)
        event.accept()

    def keyPressEvent(self, event) -> None:
        # **入力中は1つも横取りしない。** キー入力はまずこの部品に届くので、
        # ここで拾うと入力欄まで下りず、文字として打てないキーができる。
        # Esc（取り消し）と Ctrl+Enter（確定）は入力欄自身が受け取る
        if self.is_editing_text:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._select_parent()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # 選択中のセリフを打ち始める。ダブルクリックより速い
            text = self.state.selected_text
            if text is not None:
                self.begin_text_edit(text.id)
                event.accept()
                return
        if key in ZOOM_IN_KEYS:
            self.zoom_in()
            event.accept()
            return
        if key in ZOOM_OUT_KEYS:
            self.zoom_out()
            event.accept()
            return
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    def _select_parent(self) -> None:
        """Esc。画像を選んでいれば入っているコマへ、そうでなければ選択解除。

        踏み込んだぶんを1段ずつ戻す。いきなり選択が消えると、
        コマを選び直す操作が余計に要る。
        """
        self._reset_drag()
        image = self.state.selected_image
        if image is None:
            self.state.select(None)
            return
        panel = self.state.page.panel_of_image(image.id)
        self.state.select(panel.id if panel is not None else None)

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

        # 入力中に画面を触ったら、そこで確定してから次の操作へ移る
        self.finish_text_edit(commit=True)

        if tool in SPLIT_TOOLS:
            self._apply_split(x, y)
            event.accept()
            return

        # 吹き出しとセリフはコマの上に置くものなので、下に何があっても作れる。
        # コマ追加と違って「空白のときだけ」にすると、ほとんどの場所で作れない
        if tool in BALLOON_TOOLS or tool == TOOL_TEXT:
            self._mode = "create_balloon" if tool in BALLOON_TOOLS else "create_text"
            self._grab = (x, y)
            self._scene.preview_rect = Rect(x, y, 0.0, 0.0)
            event.accept()
            return

        handle = self._handle_at_point(x, y)
        text = text_at(self.state.page, x, y)
        balloon = balloon_at(self.state.page, x, y)
        hit = panel_at(self.state.page, x, y)

        # コマ追加の道具でも、既にあるコマやそのつまみの上なら編集を優先する。
        # 何も無いところを押したときだけ新しいコマを作る
        if (
            tool == TOOL_PANEL
            and handle is None
            and hit is None
            and balloon is None
            and text is None
        ):
            self._mode = "create"
            self._grab = (x, y)
            self._scene.preview_rect = Rect(x, y, 0.0, 0.0)
            event.accept()
            return

        # しっぽの先端と付け根。つまみより先に見る。小さな吹き出しでは
        # これらと角のつまみが近づくため、狙って掴んだほうを優先する
        if self._tail_tip_at(x, y):
            self._mode = "tail"
            self._scene.tail_preview = (
                self.state.selected_balloon.id,
                self.state.selected_balloon.tail.tip,
            )
            event.accept()
            return

        if self._tail_root_at(x, y):
            selected = self.state.selected_balloon
            self._mode = "tail_root"
            self._scene.root_preview = (
                selected.id,
                root_y_at(selected.rect, y),
            )
            self.state.message.emit("上下にドラッグすると、しっぽの付け根が動きます")
            event.accept()
            return

        selected_bounds = self.state.selected_bounds
        if handle is not None and selected_bounds is not None:
            self._mode = "resize"
            self._handle = handle
            self._origin_rect = selected_bounds
            self._scene.preview_rect = self._origin_rect
            # 掴んだ時点で出す。動かし始めてからでは遅い
            self._update_aspect_hint(self._shift_held(event))
            event.accept()
            return

        # 斜めの境界のつまみ。**角と辺のつまみより後に見る。**
        # こちらは掴む範囲が広いので、先に見ると縮小したときや細いコマで
        # 左右のつまみを覆い隠し、大きさを変えられなくなる
        if self._slant_handle_at(x, y):
            panel = self.state.selected_panel
            self._mode = "slant"
            self._scene.slant_preview = (
                panel.id,
                self.state.page.slant_pair_of(panel.id).ratio,
            )
            self.state.message.emit("左右にドラッグすると、斜めの境界が動きます")
            event.accept()
            return

        # セリフは吹き出しより手前。吹き出しの上に乗せるものなので、
        # 先に拾わないと文字を掴んだつもりで吹き出しが動く
        if text is not None:
            self.state.select(text.id)
            self._mode = "move"
            self._origin_rect = text.rect
            self._grab = (x, y)
            self._scene.preview_rect = text.rect
            event.accept()
            return

        # 吹き出しはコマより手前にある。コマより先に拾わないと、
        # 吹き出しを掴んだつもりで下のコマが動く
        if balloon is not None:
            self.state.select(balloon.id)
            self._mode = "move"
            self._origin_rect = balloon.rect
            self._grab = (x, y)
            self._scene.preview_rect = balloon.rect
            event.accept()
            return

        # 選択中の画像の上なら、コマに持ち替えずにその画像を動かす。
        # ここで奪われると、選んだ絵をドラッグした瞬間にコマが動く
        image = self.state.selected_image
        if image is not None and image.rect.contains(x, y) and hit is not None:
            self._mode = "move"
            self._origin_rect = image.rect
            self._grab = (x, y)
            self._scene.preview_rect = self._origin_rect
            event.accept()
            return

        self.state.select(hit.id if hit is not None else None)
        if hit is not None:
            self._mode = "move"
            # 斜めの組なら組の外側を掴む。片方だけ動く見た目にならない
            pair = self.state.page.slant_pair_of(hit.id)
            self._origin_rect = (
                hit.shape.bounds()
                if pair is None
                else self.state.page.slant_bounds(pair)
            )
            self._grab = (x, y)
            self._scene.preview_rect = self._origin_rect
            if pair is not None:
                self.state.message.emit("斜めに割った2枚は、まとめて動きます")
        event.accept()

    # -- セリフのその場編集 --------------------------------------------------

    @property
    def is_editing_text(self) -> bool:
        return self._text_editor is not None

    def begin_text_edit(self, text_id: str) -> bool:
        """セリフの入力を始める。始められたら True。"""
        self.finish_text_edit(commit=True)

        text = self.state.page.find(text_id)
        if not isinstance(text, TextObject):
            return False

        self.state.select(text_id)
        self._reset_drag()
        editor = TextEditorItem(self, text)
        self._scene.addItem(editor)
        self._scene.editing_text_id = text_id
        self._text_editor = editor

        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)

        self.state.message.emit(
            "文字を入力してください。Enter で改行、Ctrl+Enter で確定、Esc で取り消し"
        )
        self.viewport().update()
        return True

    def finish_text_edit(self, commit: bool = True) -> None:
        """入力を終える。`commit` が False なら書き戻さない。"""
        editor = self._text_editor
        if editor is None:
            return

        text_id = self._scene.editing_text_id
        content = editor.close_editor()

        self._text_editor = None
        self._scene.editing_text_id = None
        self._scene.removeItem(editor)

        if commit and text_id is not None:
            current = self.state.page.find(text_id)
            if isinstance(current, TextObject) and current.content != content:
                self.state.set_text_content(text_id, content)
        self.viewport().update()

    def _tail_tip_at(self, x: float, y: float) -> bool:
        """選択中の吹き出しの、しっぽの先端を掴んでいるか。"""
        balloon = self.state.selected_balloon
        if balloon is None or not balloon.tail.enabled:
            return False
        tx, ty = balloon.tail.tip
        half = HANDLE_PX / self.view_scale / 2.0
        return abs(x - tx) <= half and abs(y - ty) <= half

    def _slant_handle_at(self, x: float, y: float) -> bool:
        """斜めの境界のつまみを掴んでいるか。

        描いてある印より**ずっと広く**取る（`SLANT_HANDLE_PX`）。左右に
        しか動かないうえ、境界の周りは隙間で他に掴むものが無いため、
        狙いを外しても拾えるほうが扱いやすい。

        代わりに、角と辺のつまみより**後**に判定すること。先に見ると
        こちらが広いぶん、それらを覆い隠してしまう。
        """
        point = self._scene.slant_handle()
        if point is None:
            return False
        half = SLANT_HANDLE_PX / self.view_scale / 2.0
        return abs(x - point[0]) <= half and abs(y - point[1]) <= half

    def _tail_root_at(self, x: float, y: float) -> bool:
        """選択中の吹き出しの、しっぽの付け根を掴んでいるか。"""
        balloon = self.state.selected_balloon
        if balloon is None or not balloon.tail.enabled:
            return False
        root = tail_root_point(balloon, self.state.balloon_settings)
        if root is None:
            return False
        half = HANDLE_PX / self.view_scale / 2.0
        return abs(x - root[0]) <= half and abs(y - root[1]) <= half

    def mouseDoubleClickEvent(self, event) -> None:
        """セリフなら文字の入力へ、コマの中なら画像そのものを選ぶ。

        1回のクリックでコマではなく画像が選ばれると、コマを動かすつもりの
        ドラッグが絵だけを動かしてしまう。踏み込む操作を分けておく。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        x, y = self._mm(event)

        text = text_at(self.state.page, x, y)
        if text is not None:
            self.begin_text_edit(text.id)
            event.accept()
            return

        panel = panel_at(self.state.page, x, y)
        image = image_at(panel, x, y) if panel is not None else None
        if image is None:
            super().mouseDoubleClickEvent(event)
            return

        self._reset_drag()
        self.state.select(image.id)
        self.state.message.emit(
            "画像を選びました。ドラッグで移動、つまみで拡大縮小"
            "（Shift で縦横比を保つ）。Esc でコマに戻ります"
        )
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

        if self.state.tool in SPLIT_TOOLS:
            self._update_split_preview(x, y)
            event.accept()
            return

        if self._mode is None:
            self._update_cursor(x, y)
            super().mouseMoveEvent(event)
            return

        threshold = self._snap_threshold()

        if self._mode == "tail":
            # 先端は吸着させない。人物の口元を指すもので、
            # コマの辺に吸い付いても意味がない
            if self._scene.tail_preview is not None:
                self._scene.tail_preview = (self._scene.tail_preview[0], (x, y))

        elif self._mode == "tail_root":
            # **縦だけ見る。** 付け根は輪郭の上を滑るので、横位置は
            # 高さから決まる。マウスの左右を拾うと形が飛ぶ
            balloon = self.state.selected_balloon
            if balloon is not None and self._scene.root_preview is not None:
                self._scene.root_preview = (
                    self._scene.root_preview[0],
                    root_y_at(balloon.rect, y),
                )

        elif self._mode == "create":
            rect = Rect(self._grab[0], self._grab[1], x - self._grab[0], y - self._grab[1])
            xs, ys = self._candidates(None)
            self._scene.preview_rect = snap_moved_rect(rect.normalized(), xs, ys, threshold)

        elif self._mode in ("create_balloon", "create_text"):
            rect = Rect(self._grab[0], self._grab[1], x - self._grab[0], y - self._grab[1])
            self._scene.preview_rect = rect.normalized()

        elif self._mode == "slant" and self._scene.slant_preview is not None:
            held_id = self._scene.slant_preview[0]
            pair = self.state.page.slant_pair_of(held_id)
            if pair is not None:
                rect = self.state.page.slant_bounds(pair)
                ratio = clamp_slant_ratio(
                    rect, pair.angle, slant_ratio_at(rect, x), self.state.settings
                )
                self._scene.slant_preview = (held_id, ratio)

        elif self._mode == "move" and self._origin_rect is not None:
            moved = self._origin_rect.translated(x - self._grab[0], y - self._grab[1])
            xs, ys = self._candidates(self.state.selected_id)
            self._scene.preview_rect = snap_moved_rect(moved, xs, ys, threshold)

        elif self._mode == "resize" and self._origin_rect is not None and self._handle:
            xs, ys = self._candidates(self.state.selected_id)
            sx, sy = snap_point(self._handle, x, y, xs, ys, threshold)
            minimum = self.state.settings.min_panel_size
            aspect = self._locked_aspect(event)
            # ドラッグ中も出し直す。案内は数秒で消えるため、
            # ゆっくり合わせているうちに見えなくなってしまう
            self._update_aspect_hint(self._shift_held(event))
            if aspect > 0.0:
                resized = resize_rect_keep_aspect(
                    self._origin_rect, self._handle, sx, sy, minimum, aspect
                )
            else:
                resized = resize_rect(
                    self._origin_rect, self._handle, sx, sy, minimum
                )
            # 斜めの組は、細いほうが最小幅を割る手前で止める。下見のうちに
            # 押し戻しておけば、離した瞬間に形が飛ぶことがない
            pair = self.state.selected_slant_pair
            if pair is not None:
                resized = clamp_slant_rect(pair, resized, self.state.settings)
            self._scene.preview_rect = resized

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
        tail = self._scene.tail_preview
        root = self._scene.root_preview
        slant = self._scene.slant_preview
        mode, origin, press = self._mode, self._origin_rect, self._grab
        self._reset_drag()

        if mode == "tail":
            if tail is not None:
                self._apply_tail(tail[0], tail[1])
        elif mode == "tail_root":
            if root is not None:
                self._apply_tail_root(root[0], root[1])
        elif mode == "slant":
            if slant is not None:
                self._apply_slant(slant[0], slant[1])
        elif preview is not None:
            if mode == "create":
                self._apply_create(preview, press)
            elif mode == "create_balloon":
                self._apply_create_balloon(preview, press)
            elif mode == "create_text":
                self._apply_create_text(preview, press)
            elif mode == "move" and origin is not None:
                self._apply_move(origin, preview)
            elif mode == "resize":
                self._apply_resize(preview)

        self.viewport().update()
        event.accept()

    def _handle_at_point(self, x: float, y: float) -> str | None:
        """その位置にある、選択中のもののつまみ。無ければ None。"""
        bounds = self.state.selected_bounds
        if bounds is None:
            return None
        return handle_at(bounds, x, y, HANDLE_PX / self.view_scale)

    def _update_aspect_hint(self, shift_held: bool) -> None:
        """斜めのつまみで画像を伸縮しているあいだ、Shift の案内を出す。

        角のつまみは縦と横が同時に変わるので、ここでだけ等比かどうかが
        効いてくる。辺のつまみや、コマの伸縮では出さない（コマは絵では
        ないので等比に縛る意味がなく、案内が邪魔になる）。

        押している最中は文面を変える。効いているかどうかが分からないと、
        Shift を押したつもりで歪んだまま確定してしまう。
        """
        if self.state.selected_image is None or self._handle not in CORNER_HANDLES:
            return
        text = ASPECT_HINT_HELD if shift_held else ASPECT_HINT
        if text == self._hint_shown:
            return
        self._hint_shown = text
        self.state.message.emit(text)

    @staticmethod
    def _shift_held(event) -> bool:
        return bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _locked_aspect(self, event) -> float:
        """Shift を押しながら画像をリサイズしているときの縦横比。

        画像以外、または Shift を押していなければ 0（自由に伸縮）。
        コマは絵ではないので、等比に縛る意味がない。
        """
        image = self.state.selected_image
        if image is None or not self._shift_held(event):
            return 0.0
        return aspect_of(image.src_px)

    def _update_cursor(self, x: float, y: float) -> None:
        handle = self._handle_at_point(x, y)
        image = self.state.selected_image
        if self.state.tool in BALLOON_TOOLS or self.state.tool == TOOL_TEXT:
            # どこを押しても作れるので、常に十字
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif text_at(self.state.page, x, y) is not None:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._tail_tip_at(x, y):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._tail_root_at(x, y):
            # 上下にしか動かないことを形で示す
            self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
        elif handle is not None:
            self.viewport().setCursor(_HANDLE_CURSORS[handle])
        elif self._slant_handle_at(x, y):
            # 左右にしか動かないことを形で示す。掴む順と同じく、
            # 角と辺のつまみより後に見る（先に見ると出る形が実際と食い違う）
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        elif image is not None and image.rect.contains(x, y):
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif balloon_at(self.state.page, x, y) is not None:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
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

    def _apply_create_balloon(self, rect: Rect, press: tuple[float, float]) -> None:
        """吹き出しを1つ作り、選択の道具に戻す。

        コマ追加と同じ「1回きり」の扱い（要件定義 6.9）。置いたあとは
        位置と大きさ、しっぽの向きを整えるほうが先に来る。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            rect = default_balloon_rect(
                self.state.page, press[0], press[1], self.state.balloon_settings
            )

        style = BALLOON_TOOLS.get(self.state.tool, "ellipse")
        balloon = self.state.add_balloon(rect, style)
        self.state.set_tool(TOOL_SELECT)

        where = "コマに紐づけました" if balloon.attached_panel_id else "コマの外です"
        self.state.message.emit(
            f"吹き出しを追加しました（{where}）。丸い印を引くとしっぽの向きが変わります"
        )

    def _apply_create_text(self, rect: Rect, press: tuple[float, float]) -> None:
        """セリフを1つ作り、そのまま入力を始める。

        作っただけでは空の枠が残るだけなので、続けて打てる状態にする。
        道具は選択に戻す（コマ・吹き出しと同じ「1回きり」）。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            w, h = DEFAULT_TEXT_SIZE
            page = self.state.page
            rect = Rect(
                min(max(press[0] - w / 2.0, 0.0), max(page.size.w - w, 0.0)),
                min(max(press[1] - h / 2.0, 0.0), max(page.size.h - h, 0.0)),
                w,
                h,
            )

        text = self.state.add_text(rect)
        self.state.set_tool(TOOL_SELECT)
        self.begin_text_edit(text.id)

    def _apply_tail(self, balloon_id: str, tip: tuple[float, float]) -> None:
        balloon = self.state.page.find(balloon_id)
        if not isinstance(balloon, BalloonObject) or balloon.tail.tip == tip:
            return
        self.state.set_tail_tip(balloon_id, tip)

    def _apply_tail_root(self, balloon_id: str, root_y: float) -> None:
        balloon = self.state.page.find(balloon_id)
        if not isinstance(balloon, BalloonObject) or balloon.tail.root_y == root_y:
            return
        self.state.set_tail_root(balloon_id, root_y)
        self.state.message.emit(f"しっぽの付け根: {self._root_label(root_y)}")

    def _apply_slant(self, panel_id: str, ratio: float) -> None:
        pair = self.state.page.slant_pair_of(panel_id)
        if pair is None or pair.ratio == ratio:
            return
        self.state.slide_slant(panel_id, ratio)
        self.state.message.emit(f"斜めの境界: 左から {ratio * 100:.0f}%")

    @staticmethod
    def _root_label(root_y: float) -> str:
        """割合を言葉にする。数字だけでは上下どちらか分かりにくい。"""
        if root_y <= -0.66:
            return "上端"
        if root_y < -0.15:
            return "やや上"
        if root_y <= 0.15:
            return "中央"
        if root_y < 0.66:
            return "やや下"
        return "下端"

    def _apply_move(self, origin: Rect, final: Rect) -> None:
        dx, dy = final.x - origin.x, final.y - origin.y
        if dx == 0.0 and dy == 0.0:
            return
        object_id = self.state.selected_id
        if object_id is None:
            return

        if self.state.selected_image is not None:
            with self.state.edit("画像の移動") as project:
                image = project.pages[self.state.page_index].find(object_id)
                if isinstance(image, ImageObject):
                    image.rect = image.rect.translated(dx, dy)
            return

        if self.state.selected_text is not None:
            with self.state.edit("セリフの移動") as project:
                text = project.pages[self.state.page_index].find(object_id)
                if isinstance(text, TextObject):
                    text.rect = text.rect.translated(dx, dy)
            return

        if self.state.selected_balloon is not None:
            # **しっぽの先端は動かさない。** 先端はしゃべっている人物を
            # 指すページ座標なので、吹き出しの置き場所を変えても
            # 指す相手は変わらない（要件定義 4章）。
            # 上に乗ったセリフは一緒に動く
            with self.state.edit("吹き出しの移動") as project:
                project.pages[self.state.page_index].move_balloon(object_id, dx, dy)
            return

        with self.state.edit("コマの移動") as project:
            project.pages[self.state.page_index].move_panel(object_id, dx, dy)

    def _apply_resize(self, rect: Rect) -> None:
        image = self.state.selected_image
        if image is not None:
            if image.rect == rect:
                return
            image_id = image.id
            with self.state.edit("画像の大きさ変更") as project:
                target = project.pages[self.state.page_index].find(image_id)
                if isinstance(target, ImageObject):
                    target.rect = rect
            self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")
            return

        text = self.state.selected_text
        if text is not None:
            if text.rect == rect:
                return
            text_id = text.id
            with self.state.edit("セリフの大きさ変更") as project:
                target = project.pages[self.state.page_index].find(text_id)
                if isinstance(target, TextObject):
                    target.rect = rect
            self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")
            return

        balloon = self.state.selected_balloon
        if balloon is not None:
            if balloon.rect == rect:
                return
            balloon_id = balloon.id
            with self.state.edit("吹き出しの大きさ変更") as project:
                target = project.pages[self.state.page_index].find(balloon_id)
                if isinstance(target, BalloonObject):
                    target.rect = rect
            self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")
            return

        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id

        # 斜めの組は外側の矩形を差し替え、2枚を作り直す。1枚ずつ変形すると
        # 傾きと隙間が左右で食い違う
        pair = self.state.page.slant_pair_of(panel_id)
        if pair is not None:
            if self.state.page.slant_bounds(pair) == rect:
                return
            with self.state.edit("斜めのコマの大きさ変更") as project:
                page = project.pages[self.state.page_index]
                set_slant_pair_rect(
                    page, page.slant_pair_of(panel_id), rect, self.state.settings
                )
            self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")
            return

        if panel.shape.bounds() == rect:
            return
        with self.state.edit("コマの大きさ変更") as project:
            set_panel_rect(project.pages[self.state.page_index].panel(panel_id), rect)
        self.state.message.emit(f"{rect.w:.1f} × {rect.h:.1f} mm")

    # -- 分割 --------------------------------------------------------------

    def _split_target(self, x: float, y: float):
        """そこで分割できるコマ。できないなら None。

        斜めに割ったコマは（矩形でなくなるため）どの分割にも出さない。
        分割は「軸並行の矩形を切る」操作として閉じている。
        """
        panel = panel_at(self.state.page, x, y)
        if panel is None or panel.shape.as_rect() is None:
            return None
        return panel

    def _split_line(self, panel: Panel, x: float, y: float):
        """分割線の両端。押した位置に合わせて引く。"""
        bounds = panel.shape.bounds()
        tool = self.state.tool
        if tool == TOOL_SPLIT_H:
            return ((bounds.x, y), (bounds.right, y))
        if tool == TOOL_SPLIT_V:
            return ((x, bounds.y), (x, bounds.bottom))

        # 斜めは、実際に割ったときと同じ計算で下見を引く。見えている線と
        # 出来上がる形がずれない
        settings = self.state.settings
        angle = settings.slant_angle
        ratio = (x - bounds.x) / bounds.w if bounds.w > 0.0 else 0.5
        top = slant_boundary_x(bounds, ratio, angle, SLANT_RIGHT, bounds.y)
        bottom = slant_boundary_x(
            bounds, ratio, angle, SLANT_RIGHT, bounds.bottom
        )
        return ((top, bounds.y), (bottom, bounds.bottom))

    def _update_split_preview(self, x: float, y: float) -> None:
        panel = self._split_target(x, y)
        self._scene.split_preview = (
            None if panel is None else self._split_line(panel, x, y)
        )
        self.viewport().update()

    def _apply_split(self, x: float, y: float) -> None:
        panel = self._split_target(x, y)
        if panel is None:
            self.state.message.emit("コマの上でクリックしてください")
            return

        tool = self.state.tool
        panel_id = panel.id
        try:
            with self.state.edit("コマの分割") as project:
                page = project.pages[self.state.page_index]
                if tool == TOOL_SPLIT_SLANT:
                    split_panel_slant(
                        project,
                        page,
                        panel_id,
                        position=x,
                        direction=SLANT_RIGHT,
                        settings=self.state.settings,
                    )
                else:
                    split_panel(
                        project,
                        page,
                        panel_id,
                        horizontal=tool == TOOL_SPLIT_H,
                        position=y if tool == TOOL_SPLIT_H else x,
                        settings=self.state.settings,
                    )
        except ValueError as e:
            self.state.message.emit(str(e))
            return

        self._scene.split_preview = None
        self.state.select(panel_id)
        if tool == TOOL_SPLIT_SLANT:
            self.state.message.emit(
                "斜めに割りました。2枚はまとめて動きます。"
                "向きは「コマ > 斜めの向きを反転」で変えられます"
            )
        else:
            self.state.message.emit("コマを分割しました")

    # -- ドラッグ&ドロップ --------------------------------------------------

    def _dropped_images(self, mime) -> list[pathlib.Path]:
        """ドロップされたもののうち、画像として扱えるファイル。"""
        if not mime.hasUrls():
            return []
        files = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = pathlib.Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_SUFFIXES:
                files.append(path)
        return files

    def dragEnterEvent(self, event) -> None:
        if self._dropped_images(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        # ここを受け取らないと、Windows では入った瞬間だけ許可されて
        # 動かした途端に拒否に変わり、落とせなくなる
        if self._dropped_images(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        files = self._dropped_images(event.mimeData())
        if not files:
            super().dropEvent(event)
            return

        point = event.position() if hasattr(event, "position") else event.pos()
        scene_point = self.mapToScene(point.toPoint())
        panel = panel_at(self.state.page, scene_point.x(), scene_point.y())
        if panel is None:
            self.state.message.emit("コマの上に落としてください")
            event.ignore()
            return

        event.acceptProposedAction()
        placed = 0
        for path in files:
            try:
                self.state.place_image(panel.id, path.read_bytes())
            except (MangaLayoutError, OSError) as e:
                self.state.message.emit(f"{path.name}: {e}")
                continue
            placed += 1

        if placed:
            self.state.message.emit(
                f"{placed} 枚を置きました。コマを埋めるなら Ctrl+Shift+F"
            )

    def leaveEvent(self, event) -> None:
        if self._scene.split_preview is not None:
            self._scene.split_preview = None
            self.viewport().update()
        super().leaveEvent(event)
