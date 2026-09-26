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
# ここから下は 2026-09-27 にまとめて足したもの（文言は本人の確認済み）。
# 他のエディタと挙動が違う所（A）・気づきにくい機能（B）・分かりにくい機能（C）
HINT_DOUBLE_CLICK = "double_click"
HINT_PAN = "pan"
HINT_TEXT_KEY = "text_key"
HINT_BALLOON_KEY = "balloon_key"
HINT_CYCLE = "pick_cycle"
HINT_ADJUST = "adjust_exit"
HINT_SUGGEST = "suggest"
HINT_CHECK = "check"
HINT_NOTE = "page_note"
HINT_RASTER = "rasterize"
HINT_THIN = "tone_thin"
HINT_SLANT = "slant"
HINT_ORPHAN = "image_orphan"
HINT_OPEN = "open_project"
HINT_TEXTS = {
    HINT_NUDGE: "Alt+矢印キーで微調整ができます",
    # 文言は本人の指定（2026-09-27）。区切りの空白は全角
    HINT_RECENT: "[ファイル]　→　[前回のファイルを開く]　で続きから作業できます",
    # 文言は本人の指定（2026-09-27）。オレンジ枠＝絵の選択枠（`canvas.IMAGE_ACCENT`）
    HINT_TONE: "トーンの調整は、絵を選択しているときに行えます（オレンジ枠の表示中）",
    # 文言は本人の指定（2026-09-27）。空白は項目名の実物に揃えた
    HINT_PANEL: "次は　右クリック　→　[ファイル画像 読み込み]　で絵が置けます",
    HINT_DOUBLE_CLICK: "中の絵はダブルクリックで選べます（Esc でコマに戻る）",
    HINT_PAN: "画面の移動は　スペース＋ドラッグ　です",
    HINT_TEXT_KEY: "T キーでカーソルの位置にすぐ置けます",
    HINT_BALLOON_KEY: "B・W・G キーでカーソルの位置にすぐ置けます",
    HINT_CYCLE: "同じ場所をダブルクリックするたびに、下のものへ順に移ります",
    HINT_ADJUST: "終わるときは、同じ項目をもう一度押します",
    HINT_SUGGEST: "N キーで次のコマの位置を提案できます（押すたびに別の案）",
    HINT_CHECK: "書き出す前に　ファイル　→　抜けチェック　で漏れを点検できます",
    HINT_NOTE: "ページ一覧の項目を右クリックすると、付箋を貼れます",
    HINT_RASTER: "画像にしたテキストは、文字の打ち直しができません（戻すときは　元に戻す）",
    HINT_THIN: "細い線を残す　は、何度か押して調整できます",
    HINT_SLANT: "斜めに割った2枚は一緒に動きます（もう一度割ることはできません）",
    HINT_ORPHAN: "絵はコマから完全には出せません（一部がかかっていれば置けます）",
    HINT_OPEN: "作品フォルダの中の　project.json　を選んでください",
}

# 出してから消えるまで（本人の指定 2026-09-27）
HINT_DURATION_MS = 5000
# 文字の大きさ。アプリの文字に対する倍率で、**ここだけで決める**。
# 一度きりの案内なので、見落とされないことを優先して大きめにする
HINT_FONT_SCALE = 1.5
# 画面の上端からの距離（画面 px）。**上端ぎりぎりに出す**（本人の指定 2026-09-27）。
# 下寄りに出していたときは、開く窓（エクスプローラー）が帯を隠した
HINT_TOP_MARGIN = 4

HINT_STYLE = (
    "QLabel {"
    " background-color: rgba(30, 30, 30, 170);"
    " color: white;"
    " border-radius: 10px;"
    " padding: 14px 28px;"
    "}"
)


class HintBanner(QLabel):
    """ページの上、上端ぎりぎりの中央に重ねる帯。押すか、時間が来ると消える。

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
        self.move(max(area.x(), x), area.y() + HINT_TOP_MARGIN)


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
