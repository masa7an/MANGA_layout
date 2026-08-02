"""ページ一覧（サムネイル）と、ページサイズの設定（要件定義 6.1）。

一覧は `PageRenderer` で描く。本画面とまったく同じ経路なので、コマや
吹き出しの見え方が一覧と食い違わない。

**並べ替えは Qt に任せず、モデルを直してから一覧を作り直す。** 一覧側と
モデル側の両方が並び順を持つと、片方だけ動いた状態（Undo で戻したのに
一覧は動いたまま、など）が作れてしまう。並び順の出所は `Project.pages`
ひとつに絞ってある。
"""

from __future__ import annotations

import json

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QIntValidator, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..geometry import EPS, Size
from ..model import PAGE_SIZES, Page
from .render import PageRenderer
from .state import EditorState

# サムネイルの幅（画面ピクセル）。A4 なら高さは約 150px になる。
# 小さすぎるとコマ割りが読めず、大きすぎると一覧が縦に伸びて使いにくい
THUMB_WIDTH = 108

# ページ番号を書く欄の幅と、まわりの余白（画面ピクセル）。
# 番号はサムネイルの**左**に、右詰めで縦に揃える
NUMBER_WIDTH = 22
ITEM_PADDING = 4
ITEM_GAP = 6

# 見出しの番号欄（「ページ [10]/20」の [10] の部分）の幅。
# 3桁まで入る大きさにしてある。表示と入力で幅を変えない
NUMBER_FIELD_WIDTH = 40

# 一覧の1行ぶんの幅。ここに枠・項目の間隔・スクロールバーを足したものが
# 一覧全体の幅になる（`PageListPanel._fitted_width`）
ITEM_WIDTH = ITEM_PADDING * 2 + NUMBER_WIDTH + ITEM_GAP + THUMB_WIDTH

CUSTOM_LABEL = "カスタム"


def thumbnail_box(pages: list[Page], width: int = THUMB_WIDTH) -> QSize:
    """サムネイル1枚ぶんの枠。**全ページで同じ大きさにする。**

    ページごとに変えると、縦長と横長が混ざったときに一覧の行が
    ガタガタになる。いちばん縦長のページが収まる高さに合わせ、
    他のページはその中に収めて描く。
    """
    ratios = [p.size.h / p.size.w for p in pages if p.size.w > 0]
    return QSize(width, max(1, round(width * max(ratios, default=297.0 / 210.0))))


def render_thumbnail(state: EditorState, page: Page, box: QSize) -> QPixmap:
    """1ページを枠の中に収めて描く。

    目安線と用紙の影は描かない。この大きさでは線が潰れて汚れに見えるだけで、
    位置を測る役には立たない。
    """
    pixmap = QPixmap(box)
    pixmap.fill(Qt.GlobalColor.transparent)
    if page.size.w <= 0 or page.size.h <= 0:
        return pixmap

    scale = min(box.width() / page.size.w, box.height() / page.size.h)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.translate(
        (box.width() - page.size.w * scale) / 2.0,
        (box.height() - page.size.h * scale) / 2.0,
    )
    painter.scale(scale, scale)
    PageRenderer(state).draw(painter, page, guides=False, shadow=False)
    painter.end()
    return pixmap


def reorder_target(source: int, insert_at: int, count: int) -> int:
    """挿入位置を、並べ替えたあとの番号に直す。

    一覧が示すのは「どの項目の手前に入れるか」。`Project.move_page` は
    抜いてから挿すので、**自分より後ろへ運ぶときは 1 つ手前**になる。
    ここを間違えると、1つ隣へ動かしたつもりが動かない。
    """
    if insert_at > source:
        insert_at -= 1
    return min(max(insert_at, 0), count - 1)


class ClickableLabel(QLabel):
    """押せる文字。押されたことだけ知らせる。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class PageNumberEdit(QLineEdit):
    """ページ番号の入力欄。

    Esc と、欄から離れたときは**書きかけを捨てて元に戻す**。移動は Enter を
    押したときだけ。触っただけで飛ばされると、番号を確かめるつもりで
    押せなくなる。
    """

    cancelled = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.cancelled.emit()


class PageJumpBar(QWidget):
    """一覧の見出し。「ページ [10]/20」を出し、番号を押すと入力欄になる。

    ドックの題名の代わりに置く。題名の文字を書き換える形にすると、Qt は
    表示メニューの項目名まで同じ文字に書き換えてしまううえ、そもそも
    題名には入力欄を置けない。

    代わりに、既定で付いてくる「浮かす」「閉じる」のボタンは自前で並べる。
    """

    def __init__(self, state: EditorState, dock: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        self._dock = dock
        self._editing = False

        self.number = ClickableLabel(self)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 枠を付けて、押せる場所だと分かるようにする
        self.number.setFrameShape(QFrame.Shape.StyledPanel)
        self.number.setToolTip("押すとページ番号を入力できます（Enter で移動）")

        self.edit = PageNumberEdit(self)
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 数字だけ受け付ける。**ページ数に合わせて上限を絞らない。**
        # 絞ると、範囲外の数字は「入力途中」の扱いになって Enter が
        # 何も起こさず、なぜ動かないのか分からなくなる。範囲の確認は
        # 決定したときに行い、外れていれば言葉で知らせる
        self.edit.setValidator(QIntValidator(0, 9999, self))
        self.edit.returnPressed.connect(self._commit)
        self.edit.cancelled.connect(self._end_edit)

        # 表示と入力で幅が変わると、押した瞬間に見出しが動いて狙いが外れる
        self.field = QStackedWidget(self)
        self.field.addWidget(self.number)
        self.field.addWidget(self.edit)
        self.field.setFixedWidth(NUMBER_FIELD_WIDTH)

        self.total = QLabel(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(ITEM_PADDING, 2, 2, 2)
        layout.setSpacing(4)
        layout.addWidget(QLabel("ページ", self))
        layout.addWidget(self.field)
        layout.addWidget(self.total)
        layout.addStretch(1)
        layout.addWidget(
            self._button(
                QStyle.StandardPixmap.SP_TitleBarNormalButton,
                "切り離す / 戻す",
                self._toggle_floating,
            )
        )
        layout.addWidget(
            self._button(
                QStyle.StandardPixmap.SP_TitleBarCloseButton, "閉じる", dock.close
            )
        )

        self.number.clicked.connect(self._begin_edit)
        state.changed.connect(self.sync)
        state.page_changed.connect(self.sync)
        self.sync()

    def _button(self, pixmap, tip: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(self.style().standardIcon(pixmap))
        button.setAutoRaise(True)
        button.setToolTip(tip)
        # ここに入力の焦点を持っていかない（番号の入力欄から奪ってしまう）
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def _toggle_floating(self) -> None:
        self._dock.setFloating(not self._dock.isFloating())

    # -- 表示 --------------------------------------------------------------

    def sync(self) -> None:
        index, count = self.state.page_index, self.state.page_count
        self.number.setText(str(index + 1))
        self.total.setText(f"/{count}")

    # -- 入力 --------------------------------------------------------------

    def _begin_edit(self) -> None:
        """番号を押したところ。いまの番号を入れて選んでおく。"""
        self._editing = True
        self.edit.setText(self.number.text())
        self.field.setCurrentWidget(self.edit)
        self.edit.setFocus(Qt.FocusReason.MouseFocusReason)
        self.edit.selectAll()
        self.state.message.emit("移動したいページ番号を入力してください（Esc で取り消し）")

    def _end_edit(self) -> None:
        """入力をやめて表示に戻す。**二重に呼ばれても何も起きない。**

        Enter で確定したあと、欄が焦点を失ってもう一度ここへ来る。
        """
        if not self._editing:
            return
        self._editing = False
        self.field.setCurrentWidget(self.number)
        self.sync()

    def _commit(self) -> None:
        text = self.edit.text().strip()
        self._end_edit()
        if not text.isdigit():
            return

        number = int(text)
        count = self.state.page_count
        if not 1 <= number <= count:
            self.state.message.emit(f"1 〜 {count} のページ番号を入力してください")
            return
        self.state.set_page_index(number - 1)


class PageItemDelegate(QStyledItemDelegate):
    """一覧の1行を描く。**番号が左、サムネイルが右。**

    既定の並び（アイコン → 文字）だと番号が縮小画像の右に出る。ページを
    探すときは番号を縦に目で追うので、行の左端に揃っているほうが速い。
    画像の幅はページの縦横比で変わるため、右側に置くと番号の位置も
    行ごとにずれてしまう。

    背景と選択の色は既定の描き方に任せる。自前で塗ると、配色を変えた
    ときにここだけ取り残される。
    """

    def paint(self, painter: QPainter, option, index) -> None:
        # **描くものはモデルから取る。** `option` の中の項目を読んでから
        # 同じ場所を空にすると、控えたつもりの値まで一緒に空になる
        # （C++ 側の同じ実体を指しているため）
        number = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        icon = index.data(Qt.ItemDataRole.DecorationRole)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        width, height = opt.decorationSize.width(), opt.decorationSize.height()
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)

        # 中身は自分で描くので、既定の描画からは外す
        opt.text = ""
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        role = (
            QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        )
        rect = option.rect
        painter.save()
        painter.setPen(opt.palette.color(QPalette.ColorGroup.Normal, role))
        painter.drawText(
            QRect(rect.x() + ITEM_PADDING, rect.y(), NUMBER_WIDTH, rect.height()),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            number,
        )

        if isinstance(icon, QIcon) and not icon.isNull() and width > 0 and height > 0:
            painter.drawPixmap(
                QRect(
                    rect.x() + ITEM_PADDING + NUMBER_WIDTH + ITEM_GAP,
                    rect.y() + (rect.height() - height) // 2,
                    width,
                    height,
                ),
                icon.pixmap(width, height),
            )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        # **幅と高さをその場で取り出す。** `option` の中の QSize を持ち出すと、
        # 呼び出しから戻ったときには C++ 側が解放済みで参照が壊れる
        box = option.decorationSize
        width, height = box.width(), box.height()
        return QSize(
            ITEM_PADDING * 2 + NUMBER_WIDTH + ITEM_GAP + width,
            height + ITEM_PADDING * 2,
        )


class PageListPanel(QListWidget):
    """ページのサムネイル一覧。選択で移動、ドラッグで並べ替え。"""

    def __init__(self, state: EditorState, parent: QWidget | None = None):
        super().__init__(parent)
        self.state = state
        # 見た目の指紋 → サムネイル。中身が変わったページだけ描き直す
        self._thumbs: dict[str, QPixmap] = {}
        # 一覧を作り直している最中の印。選択の変更をモデルへ返さないため
        self._syncing = False
        self._drag_row: int | None = None

        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setItemDelegate(PageItemDelegate(self))
        self.setSpacing(3)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFixedWidth(self._fitted_width())

        self.currentRowChanged.connect(self._on_row_changed)
        state.changed.connect(self.sync)
        state.page_changed.connect(self.sync)
        self.sync()

    # -- 大きさ ------------------------------------------------------------

    def _fitted_width(self) -> int:
        """1行がちょうど収まる幅。**これ以上は広げない。**

        用紙を見る場所（中央）をできるだけ広く取るため、一覧は中身に必要な
        ぶんだけ占める。余らせると、その幅ぶん本画面が狭くなり続ける。

        縦のスクロールバーの幅は**出ていなくても確保する**。ページが増えて
        出てきた瞬間に一覧の幅が変わると画面全体が揺れるうえ、確保しないと
        スクロールバーが縮小画像の右端に重なる。
        """
        return (
            ITEM_WIDTH
            + self.spacing() * 2
            + self.frameWidth() * 2
            + self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        )

    # -- 表示の更新 --------------------------------------------------------

    def _key(self, page: Page, box: QSize) -> str:
        """そのページの見た目を決めるものすべての指紋。

        保存形式をそのまま使う。**保存に載らないものは見た目にも出ない**
        ので、これが変わらなければサムネイルも変わらない（要件定義 6.8 で
        Undo が同じ性質に乗っているのと同じ理由）。
        """
        body = json.dumps(page.to_dict(), ensure_ascii=False, separators=(",", ":"))
        return f"{box.width()}x{box.height()}:{body}"

    def _thumbnail(self, page: Page, box: QSize, key: str) -> QPixmap:
        cached = self._thumbs.get(key)
        if cached is None:
            cached = render_thumbnail(self.state, page, box)
            self._thumbs[key] = cached
        return cached

    def sync(self) -> None:
        """モデルに合わせて一覧を作り直す。

        並べ替え・追加・削除・Undo のどれで変わっても、同じこの一本で
        追いつく。経路を分けると、どれか1つを直し忘れて一覧だけずれる。
        """
        pages = self.state.project.pages
        box = thumbnail_box(pages)
        keys: list[str] = []

        self._syncing = True
        try:
            while self.count() > len(pages):
                self.takeItem(self.count() - 1)
            while self.count() < len(pages):
                self.addItem(QListWidgetItem())

            self.setIconSize(box)
            for row, page in enumerate(pages):
                key = self._key(page, box)
                keys.append(key)
                item = self.item(row)
                if item.data(Qt.ItemDataRole.UserRole) != key:
                    item.setIcon(QIcon(self._thumbnail(page, box, key)))
                    item.setData(Qt.ItemDataRole.UserRole, key)
                # 番号は並べ替えで変わる。中身が同じでも付け直す
                item.setText(f"{row + 1}")
                item.setToolTip(
                    f"{row + 1} ページ / {page.size.w:.0f} × {page.size.h:.0f} mm"
                    f" / コマ {len(page.panels)}"
                )
            self.setCurrentRow(self.state.page_index)
        finally:
            self._syncing = False

        # 使われなくなったサムネイルを捨てる。放っておくと編集のたびに増える
        alive = set(keys)
        self._thumbs = {k: v for k, v in self._thumbs.items() if k in alive}

    def _on_row_changed(self, row: int) -> None:
        if self._syncing or row < 0:
            return
        self.state.set_page_index(row)

    # -- 並べ替え ----------------------------------------------------------

    def startDrag(self, actions) -> None:
        # 掴んだ行を控える。落とした時点では選択が動いていることがある
        self._drag_row = self.currentRow()
        super().startDrag(actions)

    def drop_row(self, position) -> int:
        """落とした位置から、挿入先（何番の手前か）を求める。"""
        index = self.indexAt(position)
        if not index.isValid():
            return self.count()  # 一覧の下の空白＝末尾へ
        row = index.row()
        below = (
            self.dropIndicatorPosition()
            == QAbstractItemView.DropIndicatorPosition.BelowItem
        )
        return row + 1 if below else row

    def dropEvent(self, event) -> None:
        """並べ替えを1手として積む。

        **Qt に項目を動かさせない。** ここで動かすと一覧とモデルの両方が
        並び順を持つことになり、Undo で戻したときに食い違う。
        受け取るのは「どこへ落としたか」だけで、並びは `sync()` が
        モデルから作り直す。
        """
        source = self._drag_row if self._drag_row is not None else self.currentRow()
        target = reorder_target(source, self.drop_row(event.position().toPoint()), self.count())
        self._drag_row = None

        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()

        if source >= 0 and self.state.move_page(source, target):
            self.state.message.emit(f"{source + 1} ページ目を {target + 1} ページ目へ移しました")


class PageSizeDialog(QDialog):
    """ページの大きさを選ぶ（A4 / B5 / カスタム）。"""

    def __init__(self, current: Size, page_count: int = 1, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("ページサイズ")

        self.preset = QComboBox(self)
        for name, size in PAGE_SIZES.items():
            self.preset.addItem(f"{name}（{size.w:.0f} × {size.h:.0f} mm）", name)
        self.preset.addItem(CUSTOM_LABEL, None)

        self.width_mm = self._spin(current.w)
        self.height_mm = self._spin(current.h)

        self.all_pages = QCheckBox("すべてのページに適用", self)
        self.all_pages.setEnabled(page_count > 1)

        form = QFormLayout()
        form.addRow("用紙", self.preset)
        form.addRow("幅", self.width_mm)
        form.addRow("高さ", self.height_mm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        note = QLabel(
            "大きさを変えても、置いてあるコマや吹き出しは動きません。\n"
            "小さくすると用紙からはみ出すことがあります。",
            self,
        )
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.all_pages)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        self.preset.setCurrentIndex(self._index_of(current))
        self._on_preset_changed()

    def _spin(self, value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setRange(10.0, 2000.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" mm")
        spin.setValue(value)
        return spin

    def _index_of(self, size: Size) -> int:
        """いまの大きさに当たる選択肢。合うものが無ければカスタム。"""
        for row in range(self.preset.count()):
            name = self.preset.itemData(row)
            preset = PAGE_SIZES.get(name) if name else None
            if preset is not None and _same_size(preset, size):
                return row
        return self.preset.count() - 1

    def _on_preset_changed(self) -> None:
        """決まった用紙を選んだら、寸法は触らせない。

        数字だけ書き換えられると「A4 と書いてあるのに A4 でない」状態が
        作れてしまう。カスタムのときだけ打ち込めるようにする。
        """
        name = self.preset.currentData()
        size = PAGE_SIZES.get(name) if name else None
        for spin in (self.width_mm, self.height_mm):
            spin.setEnabled(size is None)
        if size is not None:
            self.width_mm.setValue(size.w)
            self.height_mm.setValue(size.h)

    def chosen_size(self) -> Size:
        return Size(round(self.width_mm.value(), 1), round(self.height_mm.value(), 1))

    def apply_to_all(self) -> bool:
        return self.all_pages.isChecked()


def _same_size(a: Size, b: Size) -> bool:
    return abs(a.w - b.w) <= EPS and abs(a.h - b.h) <= EPS
