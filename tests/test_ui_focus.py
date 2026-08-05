"""集中線の操作まわりの検証（画面なし。要件定義 6.16）。

形と保存形式は tests/test_focus.py。ここで見るのは「メニュー → モデルの
変更 → 履歴に積む」と、**つまみの取り合い**。

集中線は独立したオブジェクトではなくコマの属性なので、選択の対象は
コマのまま。つまみだけが増える、という構造の確認でもある。

画面に出る文字の扱いは tests/test_ui_balloon.py の冒頭と同じ方針。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout import focus as F
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT

from test_ui_balloon import click, drag, press, move_to, release

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
def window_with_focus(window_with_panel):
    """集中線を入れたコマが選ばれている状態。"""
    window_with_panel.state.add_focus_lines()
    return window_with_panel


def panel(window):
    return window.state.page.panels[0]


# -- 入れる・消す ------------------------------------------------------------


def test_選んだコマに入る(window_with_panel):
    assert window_with_panel.state.add_focus_lines() is True
    assert panel(window_with_panel).focus_lines is not None


def test_コマを選んでいないと入らない(window):
    window.state.select(None)
    assert window.state.add_focus_lines() is False


def test_1コマに2つは入らない(window_with_focus):
    """コマの属性として1つだけ持つ（→ 6.16）。"""
    assert window_with_focus.state.add_focus_lines() is False


def test_入れた分は1手で戻る(window_with_panel):
    window_with_panel.state.add_focus_lines()
    window_with_panel.state.undo()
    assert panel(window_with_panel).focus_lines is None


def test_消せる(window_with_focus):
    assert window_with_focus.state.remove_focus_lines() is True
    assert panel(window_with_focus).focus_lines is None


def test_消したものは1手で戻る(window_with_focus):
    seed = panel(window_with_focus).focus_lines.seed
    window_with_focus.state.remove_focus_lines()
    window_with_focus.state.undo()
    assert panel(window_with_focus).focus_lines.seed == seed


def test_コマを消せば一緒に消える(window_with_focus):
    """コマの属性なので、紐づけを外して残す相手がいない（→ 6.16）。"""
    window_with_focus.delete_selected()
    assert window_with_focus.state.page.panels == []


def test_メニューの文言が入れる_消すで入れ替わる(window_with_panel):
    window = window_with_panel
    assert window.focus_toggle_action.text() == "入れる"
    window.toggle_focus_lines()
    assert window.focus_toggle_action.text() == "消す"
    window.toggle_focus_lines()
    assert panel(window).focus_lines is None


def test_調整の項目は入っているときだけ押せる(window_with_panel):
    window = window_with_panel
    assert all(not action.isEnabled() for action in window.focus_actions)
    window.toggle_focus_lines()
    assert all(action.isEnabled() for action in window.focus_actions)


# -- 本数・太さ・振り直し ----------------------------------------------------


def test_本数を増減できる(window_with_focus):
    state = window_with_focus.state
    before = panel(window_with_focus).focus_lines.count
    assert state.step_focus_count(1) is True
    assert panel(window_with_focus).focus_lines.count == before + F.COUNT_STEP
    assert state.step_focus_count(-1) is True
    assert panel(window_with_focus).focus_lines.count == before


def test_太さを増減できる(window_with_focus):
    state = window_with_focus.state
    before = panel(window_with_focus).focus_lines.width
    assert state.step_focus_width(1) is True
    assert panel(window_with_focus).focus_lines.width > before


def test_端まで来たら履歴に積まない(window_with_focus):
    """押しても何も変わらない操作で Undo の一手を使わせない。"""
    state = window_with_focus.state
    with state.edit("本数を上限に") as project:
        project.pages[0].panels[0].focus_lines.count = F.COUNT_MAX
    depth = len(state.history._undo)
    assert state.step_focus_count(1) is False
    assert len(state.history._undo) == depth


def test_振り直すと形だけ変わる(window_with_focus):
    state = window_with_focus.state
    before = panel(window_with_focus).focus_lines
    center, count, width = before.center, before.count, before.width
    seed = before.seed

    # 種は乱数なので、まれに同じ値を引く。何度か試して変わることを見る
    assert any(state.reseed_focus() for _ in range(5))
    after = panel(window_with_focus).focus_lines
    assert (after.center, after.count, after.width) == (center, count, width)
    assert after.seed != seed


def test_振り直しは1手で戻る(window_with_focus):
    state = window_with_focus.state
    before = panel(window_with_focus).focus_lines.seed
    state.reseed_focus()
    state.undo()
    assert panel(window_with_focus).focus_lines.seed == before


def test_集中線の入っていないコマでは何も起きない(window_with_panel):
    state = window_with_panel.state
    assert state.step_focus_count(1) is False
    assert state.step_focus_width(1) is False
    assert state.reseed_focus() is False


# -- つまみ ------------------------------------------------------------------


def test_中心のつまみを引くと中心が動く(window_with_focus):
    window = window_with_focus
    view = window.view
    start = view._scene.focus_center_handle()
    drag(view, start[0], start[1], start[0] + 100.0, start[1] - 60.0)

    # マウスの位置は画面のピクセルを経由するので、端数はそこで落ちる。
    # ここで見たいのは「引いたぶん動いたか」なので 1px の幅で見る
    moved = F.center_point(panel(window).focus_lines, PANEL)
    assert moved == pytest.approx((start[0] + 100.0, start[1] - 60.0), abs=1.0)


def test_中心を動かすのは1手(window_with_focus):
    window = window_with_focus
    view = window.view
    start = view._scene.focus_center_handle()
    before = panel(window).focus_lines.center
    drag(view, start[0], start[1], start[0] + 100.0, start[1])
    window.state.undo()
    assert panel(window).focus_lines.center == before


def test_ドラッグ中はモデルに触らない(window_with_focus):
    """離すまで下見を描くだけ。Undo の一手がドラッグの途中経過で
    埋まらない（斜めの境界と同じ流儀 → 6.10）。
    """
    window = window_with_focus
    view = window.view
    start = view._scene.focus_center_handle()
    before = panel(window).focus_lines.center

    press(view, start[0], start[1])
    move_to(view, start[0] + 120.0, start[1])
    assert panel(window).focus_lines.center == before
    assert view._scene.focus_preview is not None
    release(view, start[0] + 120.0, start[1])
    assert panel(window).focus_lines.center != before
    assert view._scene.focus_preview is None


def test_内側のつまみは横だけ効く(window_with_focus):
    window = window_with_focus
    view = window.view
    start = view._scene.focus_hole_handle()
    drag(view, start[0], start[1], start[0] + 60.0, start[1] + 200.0)

    focus = panel(window).focus_lines
    assert focus.center == (0.5, 0.5)
    assert focus.hole == pytest.approx(
        (start[0] + 60.0 - PANEL_CENTER[0]) / F.short_side(PANEL), abs=0.005
    )


def test_中心のつまみはコマの移動より先に拾う(window_with_focus):
    """中心はコマの真ん中あたりに出るので、後に見ると掴んだつもりが
    コマの移動になる（→ 6.16）。
    """
    window = window_with_focus
    view = window.view
    start = view._scene.focus_center_handle()
    drag(view, start[0], start[1], start[0] + 40.0, start[1])

    assert panel(window).shape.bounds() == PANEL


def test_角のつまみは中心のつまみに邪魔されない(window_with_focus):
    """つまみを見る順が角・辺 → 集中線であることの確認。中心を隅へ
    寄せても、大きさを変えられなくなってはいけない。
    """
    window = window_with_focus
    view = window.view
    with window.state.edit("中心を隅へ") as project:
        project.pages[0].panels[0].focus_lines.center = (0.0, 0.0)

    drag(view, PANEL.x, PANEL.y, PANEL.x - 40.0, PANEL.y - 40.0)
    assert panel(window).shape.bounds().x == pytest.approx(PANEL.x - 40.0)


def test_集中線が無いコマではつまみが出ない(window_with_panel):
    view = window_with_panel.view
    assert view._scene.focus_center_handle() is None
    assert view._scene.focus_hole_handle() is None


def test_コマを選んでいなければつまみが出ない(window_with_focus):
    window_with_focus.state.select(None)
    assert window_with_focus.view._scene.focus_center_handle() is None


# -- 右クリック --------------------------------------------------------------


def focus_submenu(window):
    """コマの右クリックメニューに畳んである「集中線」の中身。"""
    menu = window._context_menu(*PANEL_CENTER)
    for action in menu.actions():
        sub = action.menu()
        if sub is not None:
            return [item for item in sub.actions() if not item.isSeparator()]
    return []


def test_右クリックにも同じ項目が畳んで出る(window_with_focus):
    """**メニューバーと同じ実体**を並べる。作り直すと、有効・無効と
    文言の書き換え（`_refresh`）が片方だけ効かなくなる（→ 6.12）。
    """
    window = window_with_focus
    items = focus_submenu(window)
    assert window.focus_toggle_action in items
    assert all(action in items for action in window.focus_actions)


def test_右クリックを何度出しても項目が入れ替わらない(window_with_focus):
    """2度目に作り直していると、メニューバー側が古い項目を持ったまま
    取り残される。
    """
    window = window_with_focus
    first = focus_submenu(window)
    assert focus_submenu(window) == first
    assert window.focus_toggle_action in first


def test_文言の書き換えが右クリック側にも出る(window_with_panel):
    window = window_with_panel
    assert focus_submenu(window)[0].text() == "入れる"
    window.toggle_focus_lines()
    assert focus_submenu(window)[0].text() == "消す"


def test_つまみの無い場所を押せばこれまでどおり選べる(window_with_focus):
    """集中線を入れたことで、コマを選び直せなくなっていないこと。"""
    window = window_with_focus
    window.state.select(None)
    click(window.view, PANEL.x + 20.0, PANEL.y + 20.0)
    assert window.state.selected_panel is not None
