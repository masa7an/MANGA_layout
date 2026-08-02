"""画面まわりの結線の検証。

表示装置なし（offscreen）で動かす。見た目そのものは確かめられないが、
「操作 → モデルの変更 → 履歴に積む → 表示の更新」がつながっているか、
Undo でモデルの実体が差し替わったあとも画面が古い参照を掴んでいないかを
確かめられる。ここが切れていると、画面上は動くのに保存すると
何も入っていない、という気づきにくい壊れ方をする。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.layout import full_page_rect
from manga_layout.storage import load_project
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_PANEL, TOOL_SELECT, TOOL_SPLIT_H, TOOL_SPLIT_V


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    # 未保存のまま閉じると確認ダイアログが出て応答待ちで止まる。
    # 保存済みにしてから閉じる
    win.state.history.mark_saved()
    win.close()


class TestBuild:
    def test_起動できる(self, window):
        assert window.state.page_count == 1
        assert window.state.page.panels == []

    def test_道具を切り替えられる(self, window):
        window.state.set_tool(TOOL_PANEL)
        assert window._tool_actions[TOOL_PANEL].isChecked()
        window.state.set_tool(TOOL_SELECT)
        assert window._tool_actions[TOOL_SELECT].isChecked()

    def test_未保存の印が題名に出る(self, window):
        assert "*" not in window._title()
        window.add_full_page_panel()
        assert "*" in window._title()


class TestPanelEditing:
    def test_全面コマを作れる(self, window):
        window.add_full_page_panel()
        page = window.state.page
        assert len(page.panels) == 1
        assert page.panels[0].shape.as_rect() == full_page_rect(page, window.state.settings)
        assert window.state.selected_id == page.panels[0].id

    def test_ドラッグでコマを作れる(self, window):
        window.view._apply_create(Rect(20.0, 20.0, 80.0, 60.0))
        assert len(window.state.page.panels) == 1
        assert window.state.page.panels[0].shape.as_rect() == Rect(20.0, 20.0, 80.0, 60.0)

    def test_極小のコマは作らない(self, window):
        # 誤クリックで見えないコマができると、選択も削除もできなくなる
        tiny = 1.0 / window.view.view_scale
        window.view._apply_create(Rect(20.0, 20.0, tiny, tiny))
        assert window.state.page.panels == []

    def test_コマを動かせる(self, window):
        window.add_full_page_panel()
        origin = window.state.selected_panel.shape.bounds()

        window.view._apply_move(origin, origin.translated(10.0, 5.0))

        moved = window.state.selected_panel.shape.bounds()
        assert (moved.x, moved.y) == pytest.approx((origin.x + 10.0, origin.y + 5.0))

    def test_コマの大きさを変えられる(self, window):
        window.add_full_page_panel()
        window.view._apply_resize(Rect(30.0, 30.0, 100.0, 90.0))
        assert window.state.selected_panel.shape.as_rect() == Rect(30.0, 30.0, 100.0, 90.0)

    def test_コマを削除できる(self, window):
        window.add_full_page_panel()
        window.delete_panel()
        assert window.state.page.panels == []
        assert window.state.selected_panel is None

    def test_分割できる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)

        window.view._apply_split(100.0, 150.0)

        assert len(window.state.page.panels) == 2
        upper, lower = window.state.page.panels
        assert upper.shape.bounds().bottom < lower.shape.bounds().y

    def test_縦に分割できる(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_V)

        window.view._apply_split(105.0, 150.0)

        left, right = window.state.page.panels
        assert left.shape.bounds().right < right.shape.bounds().x

    def test_分割できない場所では知らせるだけ(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)

        # ページの上端すぐ。上側が小さくなりすぎる
        window.view._apply_split(100.0, 16.0)

        assert len(window.state.page.panels) == 1

    def test_コマの外での分割は何も起きない(self, window):
        window.state.set_tool(TOOL_SPLIT_H)
        window.view._apply_split(100.0, 150.0)
        assert window.state.page.panels == []


class TestHistoryWiring:
    def test_操作が履歴に積まれる(self, window):
        window.add_full_page_panel()
        assert window.state.history.can_undo
        assert window.state.history.undo_label == "コマの追加"

    def test_元に戻せる(self, window):
        window.add_full_page_panel()
        window.state.undo()
        assert window.state.page.panels == []

    def test_戻したあと画面が新しい実体を見る(self, window):
        # Undo でプロジェクトの実体が差し替わる。画面が古い Page を
        # 掴んだままだと、ここで元に戻っていないように見える
        window.add_full_page_panel()
        window.view._apply_move(
            window.state.selected_panel.shape.bounds(),
            window.state.selected_panel.shape.bounds().translated(20.0, 0.0),
        )
        moved_x = window.state.page.panels[0].shape.bounds().x

        window.state.undo()

        assert window.state.page.panels[0].shape.bounds().x != moved_x
        assert window.view._scene.state.page is window.state.page

    def test_やり直せる(self, window):
        window.add_full_page_panel()
        window.state.undo()
        window.state.redo()
        assert len(window.state.page.panels) == 1

    def test_選択したコマが消えても落ちない(self, window):
        window.add_full_page_panel()
        panel_id = window.state.selected_id
        window.state.undo()

        assert window.state.selected_id == panel_id
        assert window.state.selected_panel is None
        window._refresh()  # 選択が無効でも表示更新が通ること

    def test_変化しない操作は積まない(self, window):
        window.add_full_page_panel()
        depth = window.state.history.depth
        origin = window.state.selected_panel.shape.bounds()

        window.view._apply_move(origin, origin)
        window.view._apply_resize(origin)

        assert window.state.history.depth == depth


class TestPages:
    def test_ページを追加すると移動する(self, window):
        window.add_page()
        assert window.state.page_count == 2
        assert window.state.page_index == 1

    def test_ページを行き来できる(self, window):
        window.add_page()
        window.prev_page()
        assert window.state.page_index == 0
        window.next_page()
        assert window.state.page_index == 1

    def test_端では止まる(self, window):
        window.prev_page()
        assert window.state.page_index == 0
        window.next_page()
        assert window.state.page_index == 0

    def test_ページ移動で選択が外れる(self, window):
        window.add_full_page_panel()
        window.add_page()
        assert window.state.selected_id is None


class TestFile:
    def test_保存して開き直せる(self, window, tmp_path):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_H)
        window.view._apply_split(100.0, 150.0)

        window.state.save(tmp_path)
        assert not window.state.is_dirty

        restored = load_project(tmp_path)
        assert len(restored.pages[0].panels) == 2
        assert restored.load_warnings == []

    def test_保存後は未保存の印が消える(self, window, tmp_path):
        window.add_full_page_panel()
        assert "*" in window._title()
        window._write(tmp_path)
        assert "*" not in window._title()

    def test_未保存なら閉じる前に確認する(self, qapp, monkeypatch):
        """作業を失わせないための最後の砦。ここが黙って通ると被害が大きい。"""
        from PySide6.QtWidgets import QMessageBox

        class 取り消しを返す確認:
            StandardButton = QMessageBox.StandardButton
            asked = 0

            @classmethod
            def question(cls, *args, **kwargs):
                cls.asked += 1
                return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr("manga_layout.ui.window.QMessageBox", 取り消しを返す確認)

        win = MainWindow(EditorState())
        win.add_full_page_panel()
        assert win.state.is_dirty

        assert win._confirm_discard() is False
        assert 取り消しを返す確認.asked == 1

        win.state.history.mark_saved()
        win.close()

    def test_保存済みなら確認しない(self, window):
        assert window._confirm_discard() is True

    def test_サンプル作品を開ける(self, window):
        from tests.conftest import REPO_ROOT

        sample = REPO_ROOT / "samples" / "basic"
        warnings = window.state.load(sample)

        assert warnings == []
        assert window.state.page_count == 2
        assert len(window.state.page.panels) == 4
