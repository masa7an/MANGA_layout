"""「バックアップから復元」の窓（要件定義 6.6）。

`backup/` に貯まった世代を1つ選ぶだけの窓。**選んだ結果は
`project.json` へ書かない。** 画面の中身を入れ替えて「未保存」にし、
確定は利用者が保存を押したときに行う（段取りは `ProjectIO.restore_backup`、
差し替えそのものは `EditorState.restore_backup`）。

一覧は日時の新しい順に、保存済みと作業中を**混ぜて**並べる。系列ごとに
分けると、どちらが新しいのかが読み取れない（→ `storage.list_backups`）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..storage import BackupEntry

# 窓の初期幅（px）。1行が「日時・種別・中身の手がかり」の3つ組なので、
# Qt に任せた幅では末尾のコマ数が切れる
INITIAL_WIDTH = 460


class RestoreDialog(QDialog):
    """戻す世代を1つ選ぶ。"""

    def __init__(self, entries: list[BackupEntry], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("バックアップから復元")
        self._entries = entries

        self.list = QListWidget(self)
        for entry in entries:
            item = QListWidgetItem(entry.label, self.list)
            # 読めない世代は選ばせない。一覧からは消さず、
            # 「あるが使えない」ことが分かるようにする
            if entry.pages is None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        # 一番新しいものを選んでおく。戻したい先はたいてい直前
        self.list.setCurrentRow(self._first_usable_row())
        self.list.itemDoubleClicked.connect(self._accept_if_usable)

        hint = QLabel(
            "戻しても project.json は書き換わりません。"
            "気に入らなければ「元に戻す」（Ctrl+Z）で戻せます。"
            "確定するには保存してください。",
            self,
        )
        hint.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addWidget(hint)
        layout.addWidget(self.buttons)
        self.resize(INITIAL_WIDTH, self.sizeHint().height())
        self._sync_ok()
        self.list.currentRowChanged.connect(lambda _: self._sync_ok())

    def _first_usable_row(self) -> int:
        """最初に選んでおく行。読めない世代は飛ばす。"""
        for i, entry in enumerate(self._entries):
            if entry.pages is not None:
                return i
        return -1

    def _sync_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.chosen() is not None
        )

    def _accept_if_usable(self) -> None:
        if self.chosen() is not None:
            self.accept()

    def chosen(self) -> BackupEntry | None:
        """選ばれている世代。読めないものを選んでいる場合は None。"""
        row = self.list.currentRow()
        if not 0 <= row < len(self._entries):
            return None
        entry = self._entries[row]
        return entry if entry.pages is not None else None
