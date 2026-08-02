"""メインウィンドウ。メニュー・道具箱・ページ送り・ファイル操作。"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from ..errors import MangaLayoutError
from ..layout import full_page_rect
from ..storage import is_project_dir
from .canvas import PageView
from .state import (
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_V,
    EditorState,
)

APP_TITLE = "漫画レイアウタ"


class MainWindow(QMainWindow):
    def __init__(self, state: EditorState | None = None):
        super().__init__()
        self.state = state or EditorState()
        self.view = PageView(self.state)
        self.setCentralWidget(self.view)
        self.resize(1100, 860)

        self._tool_actions: dict[str, QAction] = {}
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()

        self.state.changed.connect(self._refresh)
        self.state.selection_changed.connect(self._refresh)
        self.state.page_changed.connect(self._refresh)
        self.state.tool_changed.connect(self._sync_tool_actions)
        self.state.message.connect(lambda text: self.statusBar().showMessage(text, 6000))

        self._refresh()

    # -- 組み立て ----------------------------------------------------------

    def _act(self, text: str, slot, shortcut: str | None = None, tip: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction(self._act("新規作成", self.new_project, "Ctrl+N"))
        file_menu.addAction(self._act("開く...", self.open_project, "Ctrl+O"))
        file_menu.addSeparator()
        file_menu.addAction(self._act("保存", self.save_project, "Ctrl+S"))
        file_menu.addAction(
            self._act("名前を付けて保存...", self.save_project_as, "Ctrl+Shift+S")
        )
        file_menu.addSeparator()
        file_menu.addAction(self._act("終了", self.close, "Ctrl+Q"))

        edit_menu = self.menuBar().addMenu("編集(&E)")
        self.undo_action = self._act("元に戻す", self.state.undo, "Ctrl+Z")
        self.redo_action = self._act("やり直す", self.state.redo, "Ctrl+Y")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        self.delete_action = self._act("コマを削除", self.delete_panel, "Delete")
        edit_menu.addAction(self.delete_action)
        edit_menu.addAction(
            self._act("ページ全面にコマを作る", self.add_full_page_panel, "Ctrl+Shift+A")
        )

        tool_menu = self.menuBar().addMenu("道具(&T)")
        group = QActionGroup(self)
        group.setExclusive(True)
        for tool, shortcut in (
            (TOOL_SELECT, "V"),
            (TOOL_PANEL, "P"),
            (TOOL_SPLIT_H, "H"),
            (TOOL_SPLIT_V, "J"),
        ):
            action = QAction(f"{TOOL_LABELS[tool]} ({shortcut})", self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, t=tool: self.state.set_tool(t))
            group.addAction(action)
            tool_menu.addAction(action)
            self.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[TOOL_SELECT].setChecked(True)

        page_menu = self.menuBar().addMenu("ページ(&P)")
        page_menu.addAction(self._act("ページを追加", self.add_page, "Ctrl+Shift+N"))
        page_menu.addSeparator()
        page_menu.addAction(self._act("前のページ", self.prev_page, "PgUp"))
        page_menu.addAction(self._act("次のページ", self.next_page, "PgDown"))

        view_menu = self.menuBar().addMenu("表示(&V)")
        view_menu.addAction(self._act("拡大", lambda: self.view.scale(1.2, 1.2), "Ctrl++"))
        view_menu.addAction(self._act("縮小", lambda: self.view.scale(1 / 1.2, 1 / 1.2), "Ctrl+-"))
        view_menu.addAction(self._act("ページ全体を表示", self.view.fit_page, "Ctrl+0"))

    def _build_toolbar(self) -> None:
        bar = QToolBar("道具", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for tool in (TOOL_SELECT, TOOL_PANEL, TOOL_SPLIT_H, TOOL_SPLIT_V):
            bar.addAction(self._tool_actions[tool])
        bar.addSeparator()
        bar.addAction(self._act("← 前ページ", self.prev_page))
        bar.addAction(self._act("次ページ →", self.next_page))

    def _build_status_bar(self) -> None:
        self.page_label = QLabel()
        self.hint_label = QLabel()
        self.statusBar().addPermanentWidget(self.hint_label)
        self.statusBar().addPermanentWidget(self.page_label)

    # -- 表示の更新 --------------------------------------------------------

    def _refresh(self) -> None:
        self.setWindowTitle(self._title())
        self.page_label.setText(
            f"ページ {self.state.page_index + 1} / {self.state.page_count}"
        )

        panel = self.state.selected_panel
        if panel is None:
            self.hint_label.setText("コマ未選択")
        else:
            b = panel.shape.bounds()
            self.hint_label.setText(f"選択中: {b.w:.1f} × {b.h:.1f} mm")

        history = self.state.history
        self.undo_action.setEnabled(history.can_undo)
        self.redo_action.setEnabled(history.can_redo)
        self.undo_action.setText(
            f"元に戻す: {history.undo_label}" if history.can_undo else "元に戻す"
        )
        self.redo_action.setText(
            f"やり直す: {history.redo_label}" if history.can_redo else "やり直す"
        )
        self.delete_action.setEnabled(panel is not None)

    def _sync_tool_actions(self) -> None:
        self._tool_actions[self.state.tool].setChecked(True)

    def _title(self) -> str:
        name = self.state.project_dir.name if self.state.project_dir else "無題"
        mark = " *" if self.state.is_dirty else ""
        return f"{name}{mark} - {APP_TITLE}"

    # -- 編集 --------------------------------------------------------------

    def delete_panel(self) -> None:
        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id
        with self.state.edit("コマの削除") as project:
            project.pages[self.state.page_index].remove_panel(panel_id)
        self.state.select(None)
        self.state.message.emit("コマを削除しました")

    def add_full_page_panel(self) -> None:
        rect = full_page_rect(self.state.page, self.state.settings)
        with self.state.edit("コマの追加") as project:
            panel = project.add_panel(project.pages[self.state.page_index], rect)
        self.state.select(panel.id)

    def add_page(self) -> None:
        with self.state.edit("ページの追加") as project:
            project.add_page(index=self.state.page_index + 1)
        self.state.set_page_index(self.state.page_index + 1)

    def prev_page(self) -> None:
        self.state.set_page_index(self.state.page_index - 1)

    def next_page(self) -> None:
        self.state.set_page_index(self.state.page_index + 1)

    # -- ファイル ----------------------------------------------------------

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        from ..model import new_project as make

        self.state.reset(make(), None)
        self.state.message.emit("新しい作品を作りました")

    def open_project(self) -> None:
        if not self._confirm_discard():
            return
        folder = QFileDialog.getExistingDirectory(self, "作品フォルダを開く")
        if not folder:
            return
        path = pathlib.Path(folder)
        if not is_project_dir(path):
            QMessageBox.warning(
                self,
                "開けません",
                f"このフォルダに project.json がありません。\n{path}",
            )
            return
        try:
            warnings = self.state.load(path)
        except MangaLayoutError as e:
            QMessageBox.critical(self, "開けません", str(e))
            return

        self.view.fit_page()
        if warnings:
            QMessageBox.information(
                self,
                "読み込み時に直した箇所があります",
                "\n".join(f"・{w}" for w in warnings),
            )
        self.state.message.emit(f"開きました: {path}")

    def save_project(self) -> bool:
        if self.state.project_dir is None:
            return self.save_project_as()
        return self._write(self.state.project_dir)

    def save_project_as(self) -> bool:
        folder = QFileDialog.getExistingDirectory(self, "保存先のフォルダを選ぶ")
        if not folder:
            return False
        return self._write(pathlib.Path(folder))

    def _write(self, path: pathlib.Path) -> bool:
        try:
            self.state.save(path)
        except (MangaLayoutError, OSError) as e:
            QMessageBox.critical(self, "保存できません", str(e))
            return False
        self._refresh()
        self.state.message.emit(f"保存しました: {path}")
        return True

    # -- 終了時 ------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        """未保存の変更があれば確認する。続けてよければ True。"""
        if not self.state.is_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "保存しますか",
            "保存していない変更があります。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
