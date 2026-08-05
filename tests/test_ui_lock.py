"""コマの位置ロックの操作まわりの検証（画面なし。要件定義 6.17）。

保存形式は tests/test_model.py の `TestPanelLock`。ここで見るのは
「メニュー・つまみ → モデルの変更 → 履歴に積む」と、**止まる操作だけが
止まり、中身には今まで通り触れること**。
"""

from __future__ import annotations

import pytest
from test_ui_balloon import click, drag

from manga_layout import Rect
from manga_layout.layout import handle_positions
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT, TOOL_SPLIT_H, TOOL_SPLIT_SLANT

# 座標は px（要件定義 3章）
PANEL = Rect(120.0, 120.0, 720.0, 540.0)
PANEL_CENTER = (480.0, 390.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_panel(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.set_tool(TOOL_SELECT)
    window.state.select(window.state.page.panels[0].id)
    return window


@pytest.fixture
def window_with_locked_panel(window_with_panel):
    """ロックしたコマが選ばれている状態。"""
    window_with_panel.state.toggle_panel_lock()
    return window_with_panel


def panel(window):
    return window.state.page.panels[0]


# -- ロック・解除 --------------------------------------------------------------


def test_選んだコマをロックできる(window_with_panel):
    assert window_with_panel.state.toggle_panel_lock() is True
    assert panel(window_with_panel).locked is True


def test_コマを選んでいないと切り替わらない(window):
    window.state.select(None)
    assert window.state.toggle_panel_lock() is False


def test_ロックを解除できる(window_with_locked_panel):
    window_with_locked_panel.state.toggle_panel_lock()
    assert panel(window_with_locked_panel).locked is False


def test_ロックは1手で戻る(window_with_panel):
    window_with_panel.state.toggle_panel_lock()
    window_with_panel.state.undo()
    assert panel(window_with_panel).locked is False


def test_解除も1手で戻る(window_with_locked_panel):
    window_with_locked_panel.state.toggle_panel_lock()
    window_with_locked_panel.state.undo()
    assert panel(window_with_locked_panel).locked is True


def test_メニューの文言がロック_ロックを解除で入れ替わる(window_with_panel):
    window = window_with_panel
    assert window.lock_toggle_action.text() == "ロック"
    window.toggle_panel_lock()
    assert window.lock_toggle_action.text() == "ロックを解除"
    window.toggle_panel_lock()
    assert window.lock_toggle_action.text() == "ロック"


def test_右クリックにもロック項目が出る(window_with_panel):
    window = window_with_panel
    menu = window._context_menu(*PANEL_CENTER)
    assert window.lock_toggle_action in menu.actions()


# -- 選んでいるだけでは分からない。ステータス表示にだけ出る -------------------------


def test_ステータス表示にロック中と出る(window_with_locked_panel):
    assert "ロック中" in window_with_locked_panel._hint()


def test_ロックしていなければステータス表示に出ない(window_with_panel):
    assert "ロック中" not in window_with_panel._hint()


def test_ロックしても選択はできる(window_with_locked_panel):
    """解除するための入り口（メニュー・右クリック）へたどり着けなくなっては
    いけない。選ぶこと自体は塞がない。
    """
    window = window_with_locked_panel
    window.state.select(None)
    click(window.view, *PANEL_CENTER)
    assert window.state.selected_panel is not None
    assert window.state.selected_panel.locked is True


# -- 止まる操作 ----------------------------------------------------------------


def test_ロック中は本体を動かせない(window_with_locked_panel):
    window = window_with_locked_panel
    before = panel(window).shape.bounds()
    drag(window.view, *PANEL_CENTER, PANEL_CENTER[0] + 50.0, PANEL_CENTER[1])
    assert panel(window).shape.bounds() == before


def test_ロック解除後は動かせる(window_with_locked_panel):
    window = window_with_locked_panel
    window.state.toggle_panel_lock()
    before = panel(window).shape.bounds()
    drag(window.view, *PANEL_CENTER, PANEL_CENTER[0] + 50.0, PANEL_CENTER[1])
    assert panel(window).shape.bounds() != before


def test_ロック中はつまみが出ない(window_with_locked_panel):
    window = window_with_locked_panel
    assert window.view._handle_at_point(PANEL.x, PANEL.y) is None


def test_ロックしていなければつまみが出る(window_with_panel):
    window = window_with_panel
    assert window.view._handle_at_point(PANEL.x, PANEL.y) == "nw"


def test_ロック中は大きさを変えられない(window_with_locked_panel):
    window = window_with_locked_panel
    before = panel(window).shape.bounds()
    corner = handle_positions(PANEL)["nw"]
    drag(window.view, corner[0], corner[1], corner[0] - 40.0, corner[1] - 40.0)
    assert panel(window).shape.bounds() == before


def test_ロック中は分割できない(window_with_locked_panel):
    window = window_with_locked_panel
    window.state.set_tool(TOOL_SPLIT_H)
    window.view._apply_split(PANEL_CENTER[0], PANEL_CENTER[1])
    assert len(window.state.page.panels) == 1


def test_ロック解除後は分割できる(window_with_locked_panel):
    window = window_with_locked_panel
    window.state.toggle_panel_lock()
    window.state.set_tool(TOOL_SPLIT_H)
    window.view._apply_split(PANEL_CENTER[0], PANEL_CENTER[1])
    assert len(window.state.page.panels) == 2


def test_ロック中は削除できない(window_with_locked_panel):
    window = window_with_locked_panel
    assert window.delete_target() is None
    window.delete_selected()
    assert len(window.state.page.panels) == 1


def test_ロック解除後は削除できる(window_with_locked_panel):
    window = window_with_locked_panel
    window.state.toggle_panel_lock()
    assert window.delete_target() is not None
    window.delete_selected()
    assert window.state.page.panels == []


# -- 止まらない操作（中身は今まで通り） ----------------------------------------


def test_ロック中でも画像は動かせる(window_with_locked_panel):
    window = window_with_locked_panel
    image = window.state.place_image(
        panel(window).id, _one_pixel_png()
    )
    before = image.rect
    ix, iy = before.center
    drag(window.view, ix, iy, ix + 30.0, iy + 10.0)
    moved = window.state.page.find(image.id)
    assert moved.rect != before


def test_ロック中でも集中線を入れられる(window_with_locked_panel):
    assert window_with_locked_panel.state.add_focus_lines() is True
    assert panel(window_with_locked_panel).focus_lines is not None


def test_ロック中でも紐づくフキダシは動かせる(window_with_locked_panel):
    window = window_with_locked_panel
    balloon = window.state.add_balloon(Rect(180.0, 180.0, 240.0, 156.0))
    window.state.select(balloon.id)
    before = balloon.rect
    bx, by = before.center
    drag(window.view, bx, by, bx + 40.0, by + 15.0)
    moved = window.state.page.find(balloon.id)
    assert moved.rect != before


def _one_pixel_png() -> bytes:
    import base64

    # 1x1 の透明 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )


# -- ページ一括 ----------------------------------------------------------------


def test_ページのコマをすべてロックできる(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], Rect(0.0, 0.0, 200.0, 200.0))
        project.add_panel(project.pages[0], Rect(300.0, 0.0, 200.0, 200.0))

    assert window.state.lock_all_panels() is True
    assert all(p.locked for p in window.state.page.panels)


def test_すべてロック済みなら二度目は何もしない(window_with_locked_panel):
    """押しても何も変わらない操作で Undo の一手を使わせない。"""
    window = window_with_locked_panel
    depth = len(window.state.history._undo)
    assert window.state.lock_all_panels() is False
    assert len(window.state.history._undo) == depth


def test_コマが無ければ何もしない(window):
    assert window.state.lock_all_panels() is False
    assert window.state.unlock_all_panels() is False


def test_すべて解除できる(window_with_locked_panel):
    window = window_with_locked_panel
    assert window.state.unlock_all_panels() is True
    assert panel(window).locked is False


def test_すべてロック_解除は1手で戻る(window_with_panel):
    window = window_with_panel
    window.state.lock_all_panels()
    window.state.undo()
    assert panel(window).locked is False


# -- 斜めに割った組 --------------------------------------------------------------


class TestSlantPairLock:
    def _split(self, window):
        window.add_full_page_panel()
        window.state.set_tool(TOOL_SPLIT_SLANT)
        window.view._apply_split(620.0, 877.0)
        # 分割の道具は使ったあとも残る。押下の検証では選択に戻しておく
        # （→ tests/test_ui.py の TestSlantSlideUI と同じ理由）
        window.state.set_tool(TOOL_SELECT)
        return window.state.page

    def test_片方をロックすると両方ロックされる(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        window.state.toggle_panel_lock()
        assert all(p.locked for p in window.state.page.panels)

    def test_片方を解除すると両方解除される(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        window.state.toggle_panel_lock()
        # もう片方を選んで解除する
        window.state.select(page.panels[1].id)
        window.state.toggle_panel_lock()
        assert all(not p.locked for p in window.state.page.panels)

    def test_斜めの境界のつまみが出ない(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        window.state.toggle_panel_lock()
        assert window.view._scene.slant_handle() is None

    def test_斜めの境界を動かせない(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        # つまみの位置はロックする前に控えておく（ロック中は None になる）
        handle = window.view._scene.slant_handle()
        before = page.slant_pairs[0].ratio
        window.state.toggle_panel_lock()

        drag(window.view, handle[0], handle[1], handle[0] + 80.0, handle[1])
        assert window.state.page.slant_pairs[0].ratio == before

    def test_組全体は移動できない(self, window):
        page = self._split(window)
        window.state.select(page.panels[0].id)
        window.state.toggle_panel_lock()
        before = window.state.selected_bounds
        # 外側の矩形の中心は境界の真上に乗ることがあるので、左側のコマの
        # 内側であることが確実な点（左上寄り）を掴む
        drag(window.view, 200.0, 200.0, 250.0, 200.0)
        assert window.state.selected_bounds == before
