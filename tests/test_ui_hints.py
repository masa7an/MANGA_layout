"""最初の1回だけ出す操作のヒント（要件定義 6.35）。

見るのは、出る条件（セリフ・フキダシ・画像を選んだとき。入力中は出さない）、
2回目から出ないこと、`Alt+矢印` を使ったら消えること、ヘルプから出し直せること。
起動時の『前回のファイルを開く』の案内（前回の作品が実在するときだけ）と、
「トーン」の畳みを初めて開いたとき・コマを初めて置いたときの案内も見る。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMenu
from test_ui_nudge_keys import press
from test_ui_size_keys import (  # noqa: F401  （fixture を借りる）
    CENTER,
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
from manga_layout.ui.hints import (
    HINT_ADJUST,
    HINT_BALLOON_KEY,
    HINT_CHECK,
    HINT_CYCLE,
    HINT_DOUBLE_CLICK,
    HINT_NOTE,
    HINT_NUDGE,
    HINT_OPEN,
    HINT_ORPHAN,
    HINT_PAN,
    HINT_PANEL,
    HINT_RASTER,
    HINT_RECENT,
    HINT_SLANT,
    HINT_SUGGEST,
    HINT_TEXT_KEY,
    HINT_TEXTS,
    HINT_THIN,
    HINT_TONE,
)


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

    def test_ヘルプからは最後に出したものだけ(self, saved_project):
        win = self.launch()
        try:
            win.show_hints_again()
            assert win.hint_banner.text() == HINT_TEXTS[HINT_RECENT]
        finally:
            win.close()

    def test_最後に出したものは再起動しても覚えている(self, saved_project):
        self.launch().close()
        win = self.launch()
        try:
            win.show_hints_again()
            assert win.hint_banner.text() == HINT_TEXTS[HINT_RECENT]
        finally:
            win.close()

    def test_後から出た微調整の案内に切り替わる(self, saved_project):
        win = self.launch()
        try:
            win.state.save(saved_project)
            win.state.add_balloon(Rect(300.0, 250.0, 200.0, 120.0))
            settle()
            win.show_hints_again()
            assert win.hint_banner.text() == HINT_TEXTS[HINT_NUDGE]
        finally:
            win.state.history.mark_saved()
            win.close()


class Testリセット:
    def test_ヘルプに項目がある(self, window):
        titles = [
            a.text()
            for m in window.menuBar().actions()
            if m.text() == "ヘルプ(&H)"
            for a in m.menu().actions()
        ]
        assert "ヒント機能のリセット" in titles

    def test_記録が消えてまた出る(self, window):
        window.state.add_balloon(Rect(300.0, 250.0, 200.0, 120.0))
        balloon_id = window.state.selected_balloon.id
        settle()
        window.hint_banner.hide()
        window.reset_hints()
        assert load_hints_seen(recorded_path()) == set()
        window.state.select(window.state.page.panels[0].id)
        window.state.select(balloon_id)
        settle()
        assert banner_up(window)
        assert window.hint_banner.text() == HINT_TEXTS[HINT_NUDGE]

    def test_前回のファイルの案内も次の起動でまた出る(self, qapp, tmp_path):
        win = MainWindow(EditorState())
        path = tmp_path / "前回の作品"
        win.state.save(path)
        win.close()
        save_recent_project(path)
        mark_hint_seen(HINT_RECENT)
        win = MainWindow(EditorState())
        try:
            win.reset_hints()
        finally:
            win.close()
        win = MainWindow(EditorState())
        settle()
        try:
            assert win.hint_banner.text() == HINT_TEXTS[HINT_RECENT]
            assert banner_up(win)
        finally:
            win.close()

    def test_記録が無くても落ちない(self, window):
        window.reset_hints()
        window.reset_hints()


class Testトーンの案内:
    """「トーン」の畳みを初めて開いたとき、効く場面を案内する。"""

    def test_メニューバーから開くと出る(self, window):
        window.tone_menu.menu.aboutToShow.emit()
        assert banner_up(window)
        assert window.hint_banner.text() == HINT_TEXTS[HINT_TONE]
        assert HINT_TONE in load_hints_seen(recorded_path())

    def test_二度目は出ない(self, window):
        window.tone_menu.menu.aboutToShow.emit()
        window.hint_banner.hide()
        window.tone_menu.menu.aboutToShow.emit()
        assert not banner_up(window)

    def test_右クリックから開いても出る(self, with_image):
        mark_hint_seen(HINT_NUDGE)  # 絵を置いたときの案内と混ぜない
        with_image.hint_banner.hide()
        with_image.state.select(with_image.state.page.panels[0].id)
        menu = with_image.context_menu.build(*CENTER)
        # 畳みの QAction から `menu()` で辿ると実体が消える
        # （→ PySide6の落とし穴.md の 1）。子の QMenu を直に探す
        tone = next(m for m in menu.findChildren(QMenu) if m.title() == "トーン")
        tone.aboutToShow.emit()
        assert banner_up(with_image)
        assert with_image.hint_banner.text() == HINT_TEXTS[HINT_TONE]

    def test_もう一度見るで出る(self, window):
        window.tone_menu.menu.aboutToShow.emit()
        window.hint_banner.hide()
        window.show_hints_again()
        assert window.hint_banner.text() == HINT_TEXTS[HINT_TONE]


class Testコマの案内:
    """コマを初めて置いたとき、右クリックから絵を置けることを案内する。"""

    @pytest.fixture
    def empty(self, qapp, tmp_path):
        win = MainWindow(EditorState())
        win.state.save(tmp_path / "作品")
        yield win
        win.state.history.mark_saved()
        win.close()

    def test_道具で置くと出る(self, empty):
        empty.view._apply_create(Rect(120.0, 120.0, 400.0, 300.0), (120.0, 120.0))
        assert banner_up(empty)
        assert empty.hint_banner.text() == HINT_TEXTS[HINT_PANEL]
        assert HINT_PANEL in load_hints_seen(recorded_path())

    def test_ページ全面でも出る(self, empty):
        empty.add_full_page_panel()
        assert empty.hint_banner.text() == HINT_TEXTS[HINT_PANEL]

    def test_次のコマの提案でも出る(self, empty):
        # 全面のコマだと次を置く余地が無いので、上半分に置いておく
        empty.view._apply_create(Rect(120.0, 120.0, 720.0, 540.0), (120.0, 120.0))
        empty.hint_banner.hide()
        empty.reset_hints()
        empty.suggest_next_panel()
        assert banner_up(empty)
        assert empty.hint_banner.text() == HINT_TEXTS[HINT_PANEL]

    def test_二度目は出ない(self, empty):
        empty.add_full_page_panel()
        empty.hint_banner.hide()
        empty.add_full_page_panel()
        # 2つ目では次のコマの提案の案内（B-7）が出るので、帯の有無では見ない
        assert empty.hint_banner.text() != HINT_TEXTS[HINT_PANEL]

    def test_二つ目では提案を案内する(self, empty):
        empty.add_full_page_panel()
        empty.hint_banner.hide()
        empty.add_full_page_panel()
        assert banner_up(empty)
        assert empty.hint_banner.text() == HINT_TEXTS[HINT_SUGGEST]

    def test_ファイル画像読み込みを使った人には出ない(self, empty, monkeypatch):
        empty.add_full_page_panel()
        empty.hint_banner.hide()
        empty.reset_hints()
        monkeypatch.setattr(empty, "_choose_image_file", lambda: None)
        empty.open_image_file()
        assert HINT_PANEL in load_hints_seen(recorded_path())


# -- 2026-09-27 にまとめて足した13件（A: 挙動の違い / B: 気づきにくい / C: 分かりにくい）


def shown(win, hint_id) -> bool:
    """その案内が今出ているか。"""
    return banner_up(win) and win.hint_banner.text() == HINT_TEXTS[hint_id]


@pytest.fixture
def buried_image(qapp, fixture_dir):
    """絵が2枚入ったコマ1つ。選択は外してある（ダブルクリックの巡回用）。"""
    win = MainWindow(EditorState())
    state = win.state
    with state.edit("準備") as project:
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 300.0))
    panel = state.page.panels[0]
    data = (fixture_dir / "rgb_opaque.png").read_bytes()
    state.place_image(panel.id, data)
    state.place_image(panel.id, data)
    mark_hint_seen(HINT_NUDGE)  # 絵を選んだときの案内と混ぜない
    state.select(None)
    settle()
    win.hint_banner.hide()
    yield win
    win.state.history.mark_saved()
    win.close()


def click_pair(view, x: float, y: float) -> None:
    """利用者から見たダブルクリック1回（→ test_pick_cycle）。"""
    from mouse import double_click, press as mouse_press

    mouse_press(view, x, y)
    double_click(view, x, y)


class TestA挙動の違い:
    def test_A1_絵の入ったコマを選ぶと出る(self, buried_image):
        buried_image.state.select(buried_image.state.page.panels[0].id)
        settle()
        assert shown(buried_image, HINT_DOUBLE_CLICK)

    def test_A1_絵の無いコマでは出ない(self, window):
        mark_hint_seen(HINT_NUDGE)
        window.state.select(window.state.page.panels[0].id)
        settle()
        assert HINT_DOUBLE_CLICK not in load_hints_seen(recorded_path())

    def test_A1_ダブルクリックで入れた人には出ない(self, buried_image):
        click_pair(buried_image.view, 300.0, 250.0)
        settle()
        assert HINT_DOUBLE_CLICK in load_hints_seen(recorded_path())
        assert not shown(buried_image, HINT_DOUBLE_CLICK)

    def test_A2_ホイールで出る(self, window):
        from test_ui_zoom import wheel

        wheel(window.view)
        assert shown(window, HINT_PAN)

    def test_A2_画面を動かした人には出ない(self, window):
        from mouse import press as mouse_press
        from test_ui_zoom import wheel

        # スペースを押したままの押下＝画面の移動
        window.view._space_held = True
        mouse_press(window.view, 300.0, 300.0)
        window.view._space_held = False
        assert HINT_PAN in load_hints_seen(recorded_path())
        wheel(window.view)
        assert not banner_up(window)

    def test_A4_道具で置いたフキダシで出る(self, window):
        from mouse import click

        from manga_layout.ui.state import TOOL_BALLOON

        window.state.set_tool(TOOL_BALLOON)
        click(window.view, 300.0, 300.0)
        settle()
        # 同じ操作で選択の案内（Alt+矢印）が上書きしない
        assert shown(window, HINT_BALLOON_KEY)
        assert HINT_NUDGE not in load_hints_seen(recorded_path())

    def test_A4_道具で置いたセリフは入力を閉じてから出る(self, window):
        from mouse import click

        from manga_layout.ui.state import TOOL_TEXT

        window.state.set_tool(TOOL_TEXT)
        click(window.view, 300.0, 300.0)
        assert window.view.is_editing_text
        assert not banner_up(window)
        window.view.finish_text_edit(commit=False)
        settle()
        assert shown(window, HINT_TEXT_KEY)

    def test_A4_キーで置いた人には出ない(self, window, monkeypatch):
        from PySide6.QtGui import QKeySequence, QShortcutEvent

        from manga_layout.ui.state import TOOL_TEXT

        monkeypatch.setattr(window.view, "page_point_under_cursor", lambda: (300.0, 300.0))
        QApplication.sendEvent(
            window._tool_actions[TOOL_TEXT], QShortcutEvent(QKeySequence("T"), None)
        )
        window.view.finish_text_edit(commit=False)
        assert HINT_TEXT_KEY in load_hints_seen(recorded_path())

    def test_A5_巡回の2段目で出る(self, buried_image):
        click_pair(buried_image.view, 300.0, 250.0)
        settle()
        assert not shown(buried_image, HINT_CYCLE)
        click_pair(buried_image.view, 300.0, 250.0)
        assert shown(buried_image, HINT_CYCLE)

    def test_A6_調整の道具を持つと出る(self, window):
        from manga_layout.ui.state import TOOL_WAND

        window._pick_tool(TOOL_WAND)
        assert shown(window, HINT_ADJUST)

    def test_A6_同じ項目で抜けたら消える(self, window):
        from manga_layout.ui.state import TOOL_WAND

        window._pick_tool(TOOL_WAND)
        window._pick_tool(TOOL_WAND)
        assert not banner_up(window)


class TestB気づきにくい機能:
    def test_B7_提案を使った人には出ない(self, window):
        window.suggest_next_panel()
        assert HINT_SUGGEST in load_hints_seen(recorded_path())

    def test_B8_書き出しの窓と同時に出る(self, window, monkeypatch):
        from PySide6.QtWidgets import QDialog

        class Rejected:
            def __init__(self, *args, **kwargs):
                pass

            def exec(self):
                return QDialog.DialogCode.Rejected

        monkeypatch.setattr("manga_layout.ui.project_io.ExportDialog", Rejected)
        window.files.export_image()
        assert shown(window, HINT_CHECK)

    def test_B8_抜けチェックを使った人には出ない(self, window):
        window.run_check()
        assert HINT_CHECK in load_hints_seen(recorded_path())

    def test_B9_2ページ目で出る(self, window):
        window.add_page()
        assert shown(window, HINT_NOTE)


class TestC分かりにくい機能:
    def test_C1_画像にしたら出る(self, window):
        text = window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0), "ぼそっ")
        window.state.select(text.id)
        window.rasterize_text()
        assert shown(window, HINT_RASTER)

    def test_C2_細い線を初めて押したら出る(self, with_image):
        with_image.toggle_tone()
        with_image.hint_banner.hide()
        with_image.step_tone_thin(1)
        assert shown(with_image, HINT_THIN)

    def test_C3_斜めに割ったら出る(self, window):
        from manga_layout.ui.state import TOOL_SPLIT_SLANT

        window.view._apply_split(*CENTER, TOOL_SPLIT_SLANT)
        assert shown(window, HINT_SLANT)

    def test_C3_まっすぐ割ったら提案を案内する(self, window):
        from manga_layout.ui.state import TOOL_SPLIT_V

        mark_hint_seen(HINT_PANEL)
        window.view._apply_split(*CENTER, TOOL_SPLIT_V)
        assert shown(window, HINT_SUGGEST)

    def test_C4_絵がコマから出る操作を断ったら出る(self, with_image):
        image = with_image.state.selected_image
        with_image.hint_banner.hide()
        assert with_image.view._orphan_rejected(image, Rect(5000.0, 5000.0, 10.0, 10.0), "x")
        assert shown(with_image, HINT_ORPHAN)

    def test_C5_開く窓と同時に出る(self, qapp, monkeypatch):
        monkeypatch.setattr(
            "manga_layout.ui.project_io.QFileDialog.getOpenFileName",
            lambda *args, **kwargs: ("", ""),
        )
        win = MainWindow(EditorState())
        try:
            win.files.open_project()
            assert shown(win, HINT_OPEN)
        finally:
            win.close()


class Test帯の位置:
    def test_上端ぎりぎりの中央に出る(self, window):
        from manga_layout.ui.hints import HINT_TOP_MARGIN

        window.resize(1200, 900)
        window.show_hints_again()
        area = window.view.viewport().geometry()
        banner = window.hint_banner.geometry()
        assert banner.y() == area.y() + HINT_TOP_MARGIN
        assert abs(banner.center().x() - area.center().x()) <= 1
