"""最初の1回だけ出す操作のヒント（→ 要件定義 6.35）。

**状態表示（画面下の1行）には出さない。** あちらは「いま何を選んでいるか」を
出し続ける場所で、読む側は見慣れるほど目を向けなくなる。一度きりの案内を
そこに混ぜても気づかれないので、ページの上に重ねて出し、5秒で消す。

出したかどうかの記録は中核の `hints_seen.py`。ここは出し方だけを受け持つ。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractScrollArea, QLabel

# ヒントの名前（記録に残る文字列）と、出す文。
# **名前は変えない。** 変えると、既に出した人にもう一度出る
HINT_NUDGE = "nudge"
HINT_RECENT = "recent_project"
HINT_TONE = "tone"
HINT_PANEL = "panel_image"
HINT_TEXTS = {
    HINT_NUDGE: "Alt+矢印キーで微調整ができます",
    # 文言は本人の指定（2026-09-27）。区切りの空白は全角
    HINT_RECENT: "[ファイル]　→　[前回のファイルを開く]　で続きから作業できます",
    # 文言は本人の指定（2026-09-27）。オレンジ枠＝絵の選択枠（`canvas.IMAGE_ACCENT`）
    HINT_TONE: "トーンの調整は、絵を選択しているときに行えます（オレンジ枠の表示中）",
    # 文言は本人の指定（2026-09-27）。項目名の実物は「ファイル画像 読み込み...」
    HINT_PANEL: "右クリック　→　[ファイル画像読み込み]　で絵が置けます",
}

# 出してから消えるまで（本人の指定 2026-09-27）
HINT_DURATION_MS = 5000
# 文字の大きさ。アプリの文字に対する倍率で、**ここだけで決める**。
# 一度きりの案内なので、見落とされないことを優先して大きめにする
HINT_FONT_SCALE = 1.5
# 画面の下端からの距離（画面 px）
HINT_BOTTOM_MARGIN = 32

HINT_STYLE = (
    "QLabel {"
    " background-color: rgba(30, 30, 30, 170);"
    " color: white;"
    " border-radius: 10px;"
    " padding: 14px 28px;"
    "}"
)


class HintBanner(QLabel):
    """ページの上、下寄り中央に重ねる帯。押すか、時間が来ると消える。

    **画面の枠（`QGraphicsView`）の子にする。** 描画面（viewport）の子に
    すると、スクロールのたびに帯まで一緒に流れる。
    """

    def __init__(self, view: QAbstractScrollArea):
        super().__init__(view)
        self._view = view
        self.setStyleSheet(HINT_STYLE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(_scaled_font(self.font(), HINT_FONT_SCALE))
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        # 窓の大きさが変わったら置き直す（出ている5秒のあいだに限る）
        view.installEventFilter(self)

    def show_text(self, text: str, duration_ms: int = HINT_DURATION_MS) -> None:
        self.setText(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 読み終えた人が自分で消せるように。下のページには流さない
        self._timer.stop()
        self.hide()
        event.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._view and event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False

    def _place(self) -> None:
        area = self._view.viewport().geometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + area.height() - self.height() - HINT_BOTTOM_MARGIN
        self.move(max(area.x(), x), max(area.y(), y))


def _scaled_font(base: QFont, scale: float) -> QFont:
    """`base` を `scale` 倍にした太字。

    大きさが px で指定された書体では `pointSizeF()` が -1 を返すので
    （→ `window.py` の書体の大きさ）、そのときは px の側を伸ばす。
    """
    font = QFont(base)
    font.setBold(True)
    if font.pointSizeF() > 0:
        font.setPointSizeF(font.pointSizeF() * scale)
    elif font.pixelSize() > 0:
        font.setPixelSize(round(font.pixelSize() * scale))
    return font
