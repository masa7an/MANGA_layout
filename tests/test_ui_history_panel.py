"""履歴パネル（要件定義 6.8）と、移動の手に添える動いた量。

作った理由は、ダブルクリックでそのまま掴めるようにした（→ 6.25）結果、
手ぶれで絵が 1〜2px 動くことがあり、**見た目では動いたか分からない**ため。
押さえたいのは次の4点。

- **移動の量がパネルに出る。** 1px 未満も 0 に丸めない
- **メニューの「元に戻す」には量を出さない。** 項目名が長くなる
- **Undo した手は消えずに残り、やり直せる手として並ぶ**
- **作品を開き直したら空になる**（`History` ごと差し替わる）
"""

from __future__ import annotations

import pytest
from mouse import drag

from manga_layout import Rect, new_project
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.canvas import move_amount_text
from manga_layout.ui.history_panel import EMPTY_TEXT

PANEL = Rect(100.0, 100.0, 400.0, 300.0)
CENTER = (300.0, 250.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def with_image(window, fixture_dir):
    """コマ1枚に絵を1枚。**絵が選ばれている**ので、そのまま引けば絵が動く。"""
    with window.state.edit("準備") as project:
        project.add_panel(project.pages[0], PANEL)
    panel = window.state.page.panels[0]
    window.state.place_image(panel.id, (fixture_dir / "rgb_opaque.png").read_bytes())
    return window


class Test動いた量の書き方:
    def test_整数は小数を付けない(self):
        assert move_amount_text(30.0, -20.0) == "+30, -20 px"

    def test_1px未満も丸めて消さない(self):
        """手ぶれを確かめるための表示。0.4px を 0 と出したら用を成さない。"""
        assert move_amount_text(0.4, -0.3) == "+0.4, -0.3 px"

    def test_動いていない向きは0(self):
        assert move_amount_text(0.0, 2.0) == "0, +2 px"


class Testパネル:
    def test_最初は閉じている(self, window):
        """開きっぱなしだと用紙を見る場所が狭くなる（→ 本人の方針）。"""
        assert window.history_dock.isHidden()
        assert not window.history_toggle_action.isChecked()

    def test_表示メニューから開ける(self, window):
        window.show()
        window.history_toggle_action.trigger()
        assert window.history_dock.isVisible()

    def test_何もしていなければその旨を出す(self, window):
        assert window.history_panel.rows() == [EMPTY_TEXT]

    def test_動かした量が先頭に出る(self, with_image):
        window = with_image
        drag(window.view, *CENTER, CENTER[0] + 30.0, CENTER[1] + 20.0)

        assert window.history_panel.rows()[0] == "画像の移動　+30, +20 px"

    def test_新しい手が上に来る(self, with_image):
        window = with_image
        drag(window.view, *CENTER, CENTER[0] + 30.0, CENTER[1] + 20.0)

        rows = window.history_panel.rows()
        assert rows[0].startswith("画像の移動")
        assert rows[1:] == ["画像の配置", "準備"]

    def test_メニューの元に戻すには量を出さない(self, with_image):
        window = with_image
        drag(window.view, *CENTER, CENTER[0] + 30.0, CENTER[1] + 20.0)

        assert window.edit_menu.undo_action.text() == "元に戻す: 画像の移動"

    def test_戻した手はやり直せる手として上に残る(self, with_image):
        window = with_image
        drag(window.view, *CENTER, CENTER[0] + 30.0, CENTER[1] + 20.0)

        window.state.undo()

        panel = window.history_panel
        assert panel.rows()[0] == "画像の移動　+30, +20 px"
        # 戻した手は灰色、いまの状態を作った手は太字
        assert panel.item(0).foreground().color() != panel.item(1).foreground().color()
        assert panel.item(1).font().bold()
        assert not panel.item(0).font().bold()

    def test_作品を開き直したら空になる(self, with_image):
        window = with_image
        window.state.history.mark_saved()
        window.state.reset(new_project(), None)

        assert window.history_panel.rows() == [EMPTY_TEXT]

    def test_押しても焦点を取らない(self, window):
        """取ると、矢印キーやショートカットが一覧に吸われる。"""
        from PySide6.QtCore import Qt

        assert window.history_panel.focusPolicy() == Qt.FocusPolicy.NoFocus
