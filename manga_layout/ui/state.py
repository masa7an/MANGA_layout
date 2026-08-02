"""編集中の状態。画面の各部品が共通で見る一箇所。

`History` がプロジェクトの唯一の持ち主なので、ここでも `Project` を
直接抱えない。Undo で実体が差し替わったときに、古い `Page` や `Panel` を
掴んだままの画面が出ないようにするため（要件定義 6.8）。
"""

from __future__ import annotations

import contextlib
import pathlib
from typing import Iterator

from PySide6.QtCore import QObject, Signal

from ..history import History
from ..layout import LayoutSettings
from ..model import Page, Panel, Project, new_project
from ..storage import load_project, save_project

# 道具（ツール）
TOOL_SELECT = "select"
TOOL_PANEL = "panel"
TOOL_SPLIT_H = "split_h"
TOOL_SPLIT_V = "split_v"

TOOL_LABELS = {
    TOOL_SELECT: "選択",
    TOOL_PANEL: "コマ追加",
    TOOL_SPLIT_H: "横に分割",
    TOOL_SPLIT_V: "縦に分割",
}


class EditorState(QObject):
    """開いている作品と、画面上の選択・道具。"""

    # モデルが変わった（描き直しが要る）
    changed = Signal()
    # 選択が変わった
    selection_changed = Signal()
    # 道具が変わった
    tool_changed = Signal()
    # 表示中のページが変わった
    page_changed = Signal()
    # 状態表示に出すお知らせ
    message = Signal(str)

    def __init__(self, project: Project | None = None, project_dir: pathlib.Path | None = None):
        super().__init__()
        self.history = History(project if project is not None else new_project())
        self.project_dir = project_dir
        self.settings = LayoutSettings()
        self._page_index = 0
        self._tool = TOOL_SELECT
        self._selected_id: str | None = None

    # -- 参照 --------------------------------------------------------------

    @property
    def project(self) -> Project:
        return self.history.project

    @property
    def page(self) -> Page:
        return self.project.pages[self._page_index]

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def page_count(self) -> int:
        return len(self.project.pages)

    @property
    def tool(self) -> str:
        return self._tool

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected_panel(self) -> Panel | None:
        """選択中のコマ。Undo で消えていれば None。"""
        if self._selected_id is None:
            return None
        for panel in self.page.panels:
            if panel.id == self._selected_id:
                return panel
        return None

    # -- 操作 --------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        if tool == self._tool:
            return
        self._tool = tool
        self.tool_changed.emit()

    def select(self, panel_id: str | None) -> None:
        if panel_id == self._selected_id:
            return
        self._selected_id = panel_id
        self.selection_changed.emit()

    def set_page_index(self, index: int) -> None:
        index = max(0, min(index, self.page_count - 1))
        if index == self._page_index:
            return
        self._page_index = index
        self._selected_id = None
        self.page_changed.emit()
        self.selection_changed.emit()
        self.changed.emit()

    # -- 編集 --------------------------------------------------------------

    @contextlib.contextmanager
    def edit(self, label: str, *, merge_key: str | None = None) -> Iterator[Project]:
        """1手ぶんの編集。抜けたところで履歴に積み、画面を描き直す。"""
        with self.history.edit(label, merge_key=merge_key) as project:
            yield project
        self.changed.emit()

    def undo(self) -> None:
        label = self.history.undo()
        if label is None:
            self.message.emit("これ以上戻せません")
            return
        self._after_history_move(f"元に戻しました: {label}")

    def redo(self) -> None:
        label = self.history.redo()
        if label is None:
            self.message.emit("やり直せる操作がありません")
            return
        self._after_history_move(f"やり直しました: {label}")

    def _after_history_move(self, message: str) -> None:
        # ページが減っていた場合に備えて番号を丸める
        self._page_index = max(0, min(self._page_index, self.page_count - 1))
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()
        self.message.emit(message)

    # -- ファイル ----------------------------------------------------------

    def reset(self, project: Project, project_dir: pathlib.Path | None) -> None:
        """別の作品に入れ替える。履歴も作り直す。"""
        self.history = History(project)
        self.project_dir = project_dir
        self._page_index = 0
        self._selected_id = None
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()

    def load(self, project_dir: pathlib.Path) -> list[str]:
        """作品を開く。読み込み時に直した内容があれば返す。"""
        project = load_project(project_dir)
        warnings = list(project.load_warnings)
        self.reset(project, project_dir)
        return warnings

    def save(self, project_dir: pathlib.Path | None = None) -> pathlib.Path:
        target = project_dir or self.project_dir
        if target is None:
            raise ValueError("保存先が決まっていません")
        path = save_project(self.project, target)
        self.project_dir = target
        self.history.mark_saved()
        return path

    @property
    def is_dirty(self) -> bool:
        return self.history.is_dirty
