"""点検の結果を出す窓（要件定義 10.1）。

**閉じるまで消えない。** 状態表示の1行は6秒で消えるので、そこには件数だけを
出し、何が問題かはここに残す。選んでコピーもできる。

**そのページへ飛ぶ機能は持たない。** 紫の印が付いたサムネイルを押せば
ページは切り替わる（→ 6.1）。ここに飛ぶ経路をもう1本作ると、選択と
ページ送りの筋が2通りになる。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..check import Finding, headline, summary_lines

# 窓の初期の大きさ（画面ピクセル）。種類が6つ並んでもスクロールせずに
# 読める高さにしてある
DIALOG_SIZE = (400, 380)


class CheckResultDialog(QDialog):
    """点検の結果。**作りっぱなしで使い回す**（→ `MainWindow.run_check`）。

    押すたびに新しい窓を出すと、直しながら何度も押すうちに同じ窓が積み上がり、
    どれが最新か分からなくなる。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("抜けチェック")
        # **開いたまま直せるようにする。** 相手を止める窓にすると、
        # 読んだ内容を直すのに毎回閉じることになる
        self.setModal(False)
        self.resize(*DIALOG_SIZE)

        self._headline = QLabel()
        self._headline.setWordWrap(True)

        self._body = QPlainTextEdit()
        # **読むだけ。書き換えさせない。** ここを直しても作品は変わらない
        self._body.setReadOnly(True)
        self._body.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        note = QLabel("紫の印が付いたページをクリックすると、そのページへ移ります。")
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._headline)
        layout.addWidget(self._body)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def show_result(self, findings: list[Finding]) -> None:
        """結果を入れ替えて前に出す。開いていなければ開く。"""
        self._headline.setText(headline(findings))
        self._body.setPlainText("\n".join(summary_lines(findings)))
        # 先頭から読ませる。押し直したとき、前回のスクロール位置に残らない
        self._body.moveCursor(self._body.textCursor().MoveOperation.Start)
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        """閉じても捨てない。**紫の印は消さない。**

        窓を閉じたあとも一覧の印で残りを追える、というのが印を持つ理由
        （→ 要件定義 10.1）。ここで消すと、窓と印の寿命が同じになって
        印が要らなくなる。
        """
        event.accept()


# メニューに添える説明（ホバー中の状態表示に出る → 7章）
CHECK_MENU_HINT = (
    "全ページを見回して、直し忘れを探す"
    "（自動では直さない。見つかったページに紫の印が付く）"
)
