"""最初の1回だけ出す操作のヒント（要件定義 6.35）。

見るのは、出る条件（セリフ・フキダシ・画像を選んだとき。入力中は出さない）、
2回目から出ないこと、`Alt+矢印` を使ったら消えること、ヘルプから出し直せること。
起動時の『前回のファイルを開く』の案内（前回の作品が実在するときだけ）も見る。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication
from test_ui_nudge_keys import press
from test_ui_size_keys import (  # noqa: F401  （fixture を借りる）
    window,
    with_balloon,
    with_image,
    with_sticker,
)

from manga_layout import Rect
from manga_layout import hints_seen
from manga_layout.hints_seen import HINTS_SEEN_FILENAME, load_hints_seen, mark_hint_seen
from manga_layout.recent_project import save_recent_project
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.hints import HINT_NUDGE, HINT_RECENT, HINT_TEXTS


def settle() -> None:
    """判定は今の操作が済んだあとへ回してある（→ `_queue_once_hints`）。"""
    QApplication.processEvents()


def recorded_path():
    """記録の置き場所。**名前ごと取り込まない**——テストでは差し替えてあり
    （→ conftest `出したヒントの記録を逃がす`）、取り込むと差し替える前の
    本物の場所を見る。
    """
    return hints_seen.hints_seen_path()


def banner_up(window) -> bool:
    """出ているか。テストでは窓を表に出さないので `isVisible` は常に偽になる。"""
    return not window.hint_banner.isHidden()


class Test記録:
    def test_書いたものが読める(self, tmp_path):
        path = tmp_path / HINTS_SEEN_FILENAME
        mark_hint_seen("a", path)
        mark_hint_seen("b", path)
        mark_hint_seen("a", path)
        assert load_hints_seen(path) == {"a", "b"}

    def test_無ければ空(self, tmp_path):
        assert load_hints_seen(tmp_path / HINTS_SEEN_FILENAME) == set()


class Test出る条件:
    @pytest.mark.parametrize("fixture", ["with_balloon", "with_image"])
    def test_フキダシと画像で出る(self, fixture, request):
        win = request.getfixturevalue(fixture)
        settle()
        assert banner_up(win)
        assert win.hint_banner.text() == HINT_TEXTS[HINT_NUDGE]

    def test_セリフは打ち終えてから出る(self, window):
        window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
        window.view.begin_text_edit(window.state.selected_text.id)
        settle()
        # 入力中は Alt+矢印 が効かないので、案内しない
        assert not banner_up(window)
        window.view.finish_text_edit(commit=True)
        settle()
        assert banner_up(window)

    def test_マークでは出ない(self, with_sticker):
        settle()
        assert not banner_up(with_sticker)

    def test_コマでは出ない(self, window):
        window.state.select(window.state.page.panels[0].id)
        settle()
        assert not banner_up(window)


class Test一度きり:
    def test_出したら記録される(self, with_balloon):
        settle()
        assert HINT_NUDGE in load_hints_seen(recorded_path())

    def test_記録があれば出ない(self, qapp, tmp_path):
        mark_hint_seen(HINT_NUDGE)
        win = MainWindow(EditorState())
        try:
            win.state.save(tmp_path / "作品")
            win.state.add_balloon(Rect(300.0, 250.0, 200.0, 120.0))
            settle()
            assert not banner_up(win)
        finally:
            win.state.history.mark_saved()
            win.close()

    def test_Alt矢印で消えて以後出ない(self, with_balloon):
        settle()
        assert banner_up(with_balloon)
        press(with_balloon.view, "Right")
        assert not banner_up(with_balloon)

    def test_先にAlt矢印を使えば出ない(self, window):
        # コマを選んで先に使う（コマではヒントは出ない）
        window.state.select(window.state.page.panels[0].id)
        press(window.view, "Right")
        window.state.add_balloon(Rect(300.0, 250.0, 200.0, 120.0))
        settle()
        assert not banner_up(window)
        assert HINT_NUDGE in load_hints_seen(recorded_path())


class Testヘルプから出し直す:
    def test_記録があっても今すぐ出る(self, window):
        mark_hint_seen(HINT_NUDGE)
        window.show_hints_again()
        assert banner_up(window)

    def test_ヘルプに項目がある(self, window):
        titles = [
            a.text()
            for m in window.menuBar().actions()
            if m.text() == "ヘルプ(&H)"
            for a in m.menu().actions()
        ]
        assert "ヒントをもう一度見る" in titles

    def test_押すと消える(self, window):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        window.show_hints_again()
        QTest.mouseClick(window.hint_banner, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert not banner_up(window)


class Test前回のファイルの案内:
    """起動時、前回の作品が実在すれば『前回のファイルを開く』を案内する。"""

    @pytest.fixture
    def saved_project(self, qapp, tmp_path):
        """前回の作品を1つ実在させ、記録に載せておく。"""
        win = MainWindow(EditorState())
        path = tmp_path / "前回の作品"
        win.state.save(path)
        win.close()
        save_recent_project(path)
        return path

    def launch(self, state=None):
        win = MainWindow(state or EditorState())
        settle()
        return win

    def test_前回の作品があれば出る(self, saved_project):
        win = self.launch()
        try:
            assert banner_up(win)
            assert win.hint_banner.text() == HINT_TEXTS[HINT_RECENT]
            assert HINT_RECENT in load_hints_seen(recorded_path())
        finally:
            win.close()

    def test_記録が無ければ出ない(self, qapp):
        win = self.launch()
        try:
            assert not banner_up(win)
        finally:
            win.close()

    def test_作品が消えていれば出ない(self, qapp, tmp_path):
        save_recent_project(tmp_path / "消えた作品")
        win = self.launch()
        try:
            assert not banner_up(win)
        finally:
            win.close()

    def test_二度目の起動では出ない(self, saved_project):
        self.launch().close()
        win = self.launch()
        try:
            assert not banner_up(win)
        finally:
            win.close()

    def test_作品を開いて起動したら出ない(self, saved_project):
        state = EditorState()
        state.load(saved_project)
        win = self.launch(state)
        try:
            assert not banner_up(win)
            assert HINT_RECENT not in load_hints_seen(recorded_path())
        finally:
            win.close()

    def test_開けば消えて以後出ない(self, saved_project):
        win = self.launch()
        try:
            win.files.open_recent_project()
            assert not banner_up(win)
            assert win.state.project_dir == saved_project
        finally:
            win.close()

    def test_出る前に開いた人には出ない(self, saved_project):
        # 起動時の案内は窓が出てから。その前に開けば、案内は要らない
        win = MainWindow(EditorState())
        try:
            win.files.open_recent_project()
            settle()
            assert not banner_up(win)
            assert HINT_RECENT in load_hints_seen(recorded_path())
        finally:
            win.close()

    def test_ヘルプから出すと両方が並ぶ(self, saved_project):
        win = self.launch()
        try:
            win.show_hints_again()
            assert win.hint_banner.text() == "\n".join(
                [HINT_TEXTS[HINT_RECENT], HINT_TEXTS[HINT_NUDGE]]
            )
        finally:
            win.close()
