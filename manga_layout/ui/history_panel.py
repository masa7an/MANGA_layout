"""履歴パネル。積まれた手を新しい順に並べる（要件定義 6.8）。

**見るだけ。** 行を押しても、その時点へは戻らない。戻すのは今までどおり
「元に戻す」「やり直す」で行う。押して戻れるようにすると、Undo を何回か
続けて押すのと同じことになり、未保存の印・自動保存・選択との組み合わせを
まとめて確かめ直すことになる（2026-09-25 に、見るだけで足りると本人が判断）。

作った理由は、**動いたかどうかが見た目では分かりにくい操作**があるため。
ダブルクリックでそのまま掴めるようにした（→ 6.25）結果、手ぶれで画像が
1〜2px 動くことがある。移動の手には動いた量を添えてあり（→ `Step.detail`）、
ここに出る。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyle

from .state import EditorState

# 幅を決めるための見本。**長い手の名前に、大きく動いたときの量を足したもの。**
# これより長い行は右端を「…」で詰め、全文は行の上に指を置くと出す
WIDTH_SAMPLE = "フキダシの移動　+1234.5, -1234.5 px"

EMPTY_TEXT = "まだ操作はありません"
REDO_TIP = "元に戻した手です。「やり直す」で戻ります"


def entry_text(label: str, detail: str) -> str:
    """1行ぶんの文字。補足があれば全角の空白を挟んで後ろに付ける。"""
    return f"{label}　{detail}" if detail else label


class HistoryPanel(QListWidget):
    """積まれた手の一覧。**上が新しい。**

    並びは上から次のとおり。

        やり直せる手（灰色。いちばん上が、いちばん先の手）
        いまの状態を作った手（太字）
        それより前の手

    新しいものを上に置くのは、確かめたいのがたいてい直前の手だから。
    古い順にすると、手が増えるたびに一番下までスクロールすることになる。
    """

    def __init__(self, state: EditorState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # 焦点を取らない。取ると、押したあとの矢印キーやショートカットが
        # 一覧に吸われ、画面の操作が効かなくなる
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setFixedWidth(self._fitted_width())
        state.changed.connect(self.sync)
        self.sync()

    def _fitted_width(self) -> int:
        """見本の1行がちょうど収まる幅（→ `PageListPanel._fitted_width` と同じ考え）。

        **中身に合わせて毎回変えない。** 手が積まれるたびに幅が揺れると、
        用紙を見る場所まで揺れる。スクロールバーの幅も出ていなくても確保する。
        """
        return (
            self.fontMetrics().horizontalAdvance(WIDTH_SAMPLE)
            + self.frameWidth() * 2
            + self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            + self.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin) * 4
        )

    def sync(self) -> None:
        """履歴を読み直して並べ直す。**`state.history` は毎回引き直す**
        （作品を開くと `History` ごと差し替わる → `EditorState.reset`）。
        """
        history = self.state.history
        self.clear()

        grey = self.palette().color(QPalette.ColorRole.PlaceholderText)
        for label, detail in reversed(history.redo_entries):
            item = self._add(entry_text(label, detail))
            item.setForeground(grey)
            item.setToolTip(f"{item.text()}\n{REDO_TIP}")

        for index, (label, detail) in enumerate(reversed(history.undo_entries)):
            item = self._add(entry_text(label, detail))
            if index == 0:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)

        if self.count() == 0:
            item = self._add(EMPTY_TEXT)
            item.setForeground(grey)

    def _add(self, text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setToolTip(text)
        self.addItem(item)
        return item

    def rows(self) -> list[str]:
        """並んでいる文字を上から。テストと、画面を読まずに中身を確かめる用。"""
        return [self.item(i).text() for i in range(self.count())]
