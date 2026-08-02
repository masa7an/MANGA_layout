"""メインウィンドウ。メニュー・道具箱・ページ送り・ファイル操作。"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
)

from ..errors import MangaLayoutError
from ..images import to_png_bytes
from ..layout import cover_rect_in, full_page_rect
from ..model import ImageObject, Panel
from ..storage import is_project_dir, prune_unused_assets
from .canvas import IMAGE_FILE_FILTER, PageView
from .state import (
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_V,
    EditorState,
)

APP_TITLE = "漫画レイアウタ"

# 起動時の希望サイズ。画面に入らなければ後述の作業領域に合わせて縮める
WINDOW_SIZE = (1100, 860)

# 画面下端との間に必ず空ける余白（px）。
# ここを 0 にすると、下端いっぱいのときにステータス表示がタスクバーと
# 接して読みにくくなる
BOTTOM_GAP_PX = 20

# タイトルバーと枠のぶんの見込み（px）。
# 表示前は実寸（frameGeometry）が取れないため固定値で確保する。
# これが無いと、画面いっぱいの高さにしたときタイトルバーが画面外に出て
# ウィンドウを掴めなくなる
FRAME_ALLOWANCE_PX = 48


class MainWindow(QMainWindow):
    def __init__(self, state: EditorState | None = None):
        super().__init__()
        self.state = state or EditorState()
        self.view = PageView(self.state)
        self.setCentralWidget(self.view)
        self._apply_initial_geometry()

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

    def _apply_initial_geometry(self) -> None:
        """タスクバーに隠れないよう、画面の作業領域に収めて配置する。

        availableGeometry はタスクバーを除いた領域を返す。そこから
        下に BOTTOM_GAP_PX、上に FRAME_ALLOWANCE_PX を残した範囲に
        中央寄せする。画面が希望サイズより小さければ縮める。
        """
        width, height = WINDOW_SIZE
        screen = self.screen()
        if screen is None:  # 表示装置が無いとき（offscreen 等）
            self.resize(width, height)
            return

        area = screen.availableGeometry().adjusted(
            0, FRAME_ALLOWANCE_PX, 0, -BOTTOM_GAP_PX
        )
        width = min(width, area.width())
        height = min(height, area.height())
        self.setGeometry(
            area.x() + (area.width() - width) // 2,
            area.y() + (area.height() - height) // 2,
            width,
            height,
        )

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
        self.delete_action = self._act("削除", self.delete_selected, "Delete")
        edit_menu.addAction(self.delete_action)
        edit_menu.addAction(
            self._act("ページ全面にコマを作る", self.add_full_page_panel, "Ctrl+Shift+A")
        )

        image_menu = self.menuBar().addMenu("画像(&I)")
        image_menu.addAction(
            self._act("貼り付け", self.paste_image, "Ctrl+V", "クリップボードの画像を置く")
        )
        image_menu.addAction(self._act("ファイルから読み込み...", self.open_image_file))
        image_menu.addSeparator()
        self.fit_action = self._act(
            "コマにフィット", self.fit_image, "Ctrl+Shift+F", "選択中の画像でコマを埋める"
        )
        image_menu.addAction(self.fit_action)
        image_menu.addSeparator()
        image_menu.addAction(self._act("未使用ファイルを整理...", self.prune_assets))

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

        self.hint_label.setText(self._hint())

        history = self.state.history
        self.undo_action.setEnabled(history.can_undo)
        self.redo_action.setEnabled(history.can_redo)
        self.undo_action.setText(
            f"元に戻す: {history.undo_label}" if history.can_undo else "元に戻す"
        )
        self.redo_action.setText(
            f"やり直す: {history.redo_label}" if history.can_redo else "やり直す"
        )
        self.delete_action.setEnabled(self.state.selected_object is not None)
        self.fit_action.setEnabled(self.state.selected_image is not None)

    def _hint(self) -> str:
        """いま何を選んでいるかを状態表示に出す。

        コマと画像は見た目が似ているので、文字でも示さないと
        どちらを動かしているのか分からなくなる。
        """
        image = self.state.selected_image
        if image is not None:
            r = image.rect
            w, h = image.src_px
            return f"画像を選択中: {r.w:.1f} × {r.h:.1f} mm（元 {w}×{h} px）"

        panel = self.state.selected_panel
        if panel is not None:
            b = panel.shape.bounds()
            count = len(panel.children)
            inside = f" / 画像 {count} 枚" if count else ""
            return f"コマを選択中: {b.w:.1f} × {b.h:.1f} mm{inside}"

        return "コマ未選択"

    def _sync_tool_actions(self) -> None:
        self._tool_actions[self.state.tool].setChecked(True)

    def _title(self) -> str:
        name = self.state.project_dir.name if self.state.project_dir else "無題"
        mark = " *" if self.state.is_dirty else ""
        return f"{name}{mark} - {APP_TITLE}"

    # -- 編集 --------------------------------------------------------------

    def delete_selected(self) -> None:
        """Delete キー。選んでいるものに応じて、コマか画像を消す。"""
        if self.state.selected_image is not None:
            self.delete_image()
        else:
            self.delete_panel()

    def delete_panel(self) -> None:
        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id
        with self.state.edit("コマの削除") as project:
            project.pages[self.state.page_index].remove_panel(panel_id)
        self.state.select(None)
        self.state.message.emit("コマを削除しました")

    def delete_image(self) -> None:
        """画像だけ消す。入っていたコマは残り、そのコマを選び直す。

        画像の実体（assets/）はここでは消さない。Undo で戻せなくなるため。
        余った実体は「未使用ファイルを整理」で片付ける（要件定義 5章）。
        """
        image = self.state.selected_image
        if image is None:
            return
        image_id = image.id
        panel = self.state.page.panel_of_image(image_id)
        panel_id = panel.id if panel is not None else None

        with self.state.edit("画像の削除") as project:
            target = project.pages[self.state.page_index].panel_of_image(image_id)
            if target is not None:
                target.children = [c for c in target.children if c.id != image_id]

        self.state.select(panel_id)
        self.state.message.emit("画像を削除しました")

    # -- 画像 --------------------------------------------------------------

    def _target_panel(self) -> Panel | None:
        """画像を入れるコマ。画像を選んでいれば、それが入っているコマ。"""
        panel = self.state.selected_panel
        if panel is None:
            image = self.state.selected_image
            if image is not None:
                panel = self.state.page.panel_of_image(image.id)
        if panel is None:
            self.state.message.emit("先にコマを選んでください")
        return panel

    def _place_image(self, panel_id: str, data: bytes, source: str) -> bool:
        try:
            image = self.state.place_image(panel_id, data)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "画像を置けません", str(e))
            return False
        w, h = image.src_px
        self.state.message.emit(
            f"画像を置きました（{source} / {w}×{h} px）。"
            "コマを埋めるなら Ctrl+Shift+F"
        )
        return True

    def paste_image(self) -> None:
        panel = self._target_panel()
        if panel is None:
            return
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.state.message.emit("クリップボードに画像がありません")
            return
        try:
            data = to_png_bytes(image)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "画像を置けません", str(e))
            return
        self._place_image(panel.id, data, "貼り付け")

    def open_image_file(self) -> None:
        panel = self._target_panel()
        if panel is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "画像を選ぶ", "", IMAGE_FILE_FILTER)
        if not path:
            return
        file = pathlib.Path(path)
        try:
            data = file.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "画像を読めません", f"{file}\n{e}")
            return
        self._place_image(panel.id, data, file.name)

    def fit_image(self) -> None:
        """選択中の画像でコマを埋める。はみ出た分はコマの形で切り抜かれる。"""
        image = self.state.selected_image
        if image is None:
            self.state.message.emit("先に画像を選んでください（コマの中をダブルクリック）")
            return
        panel = self.state.page.panel_of_image(image.id)
        if panel is None:
            return

        rect = cover_rect_in(panel.shape.bounds(), image.src_px)
        if rect == image.rect:
            return
        image_id = image.id
        with self.state.edit("コマにフィット") as project:
            target = project.pages[self.state.page_index].find(image_id)
            if isinstance(target, ImageObject):
                target.rect = rect
        self.state.message.emit(f"コマを埋めました（{rect.w:.1f} × {rect.h:.1f} mm）")

    def prune_assets(self) -> None:
        """どこからも使われていない画像を assets/_unused/ へ移す。

        保存時に自動で行わない理由は要件定義 5章。Undo で戻した画像の
        実体が消えてしまうため、利用者が選んだときだけ動かす。
        """
        if self.state.project_dir is None:
            self.state.message.emit("先に作品を保存してください")
            return
        if self.state.is_dirty:
            QMessageBox.information(
                self,
                "先に保存してください",
                "保存していない変更があります。\n"
                "保存前に整理すると、まだ保存されていない画像まで未使用と判定されます。",
            )
            return

        moved = prune_unused_assets(self.state.project, self.state.project_dir)
        if not moved:
            self.state.message.emit("使われていない画像はありませんでした")
            return
        QMessageBox.information(
            self,
            "整理しました",
            f"{len(moved)} 件を assets/_unused/ へ移しました。\n"
            "削除はしていないので、戻したいときはフォルダから取り出せます。",
        )
        self.state.message.emit(f"{len(moved)} 件を _unused/ へ移しました")

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
