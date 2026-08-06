"""流線の操作まわりの検証（画面なし。要件定義 6.26）。

形と保存形式は tests/test_flow.py。ここで見るのは「メニュー → モデルの
変更 → 履歴に積む」と、**つまみの取り合い**。

集中線（tests/test_ui_focus.py）と作りは同じだが、**取り合いの相手が
1つ増えている**のがここの要点。丸のつまみは集中線の十字・四角より後に
判定する（→ 6.26）。

画面に出る文字の扱いは tests/test_ui_balloon.py の冒頭と同じ方針。
"""

from __future__ import annotations

import pytest
from test_ui_balloon import click, drag, move_to, press, release

from manga_layout import Rect, flow as FL, focus as F
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT

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
def window_with_flow(window_with_panel):
    """流線を入れたコマが選ばれている状態。"""
    window_with_panel.state.add_flow_lines()
    return window_with_panel


def panel(window):
    return window.state.page.panels[0]


def shift_drag(view, x1, y1, x2, y2) -> None:
    """Shift を押しながら引く。刻みが効くかを見るため。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    def send(kind, x, y):
        position = QPointF(view.mapFromScene(QPointF(x, y)))
        buttons = {
            "press": (Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            "move": (Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            "release": (Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        }[kind]
        types = {
            "press": QMouseEvent.Type.MouseButtonPress,
            "move": QMouseEvent.Type.MouseMove,
            "release": QMouseEvent.Type.MouseButtonRelease,
        }
        event = QMouseEvent(
            types[kind],
            position,
            view.viewport().mapToGlobal(position),
            buttons[0],
            buttons[1],
            Qt.KeyboardModifier.ShiftModifier,
        )
        {
            "press": view.mousePressEvent,
            "move": view.mouseMoveEvent,
            "release": view.mouseReleaseEvent,
        }[kind](event)

    send("press", x1, y1)
    send("move", x2, y2)
    send("release", x2, y2)


# -- 入れる・消す ------------------------------------------------------------


def test_選んだコマに入る(window_with_panel):
    assert window_with_panel.state.add_flow_lines() is True
    assert panel(window_with_panel).flow_lines is not None


def test_コマを選んでいないと入らない(window):
    window.state.select(None)
    assert window.state.add_flow_lines() is False


def test_1コマに2つは入らない(window_with_flow):
    """コマの属性として1つだけ持つ（→ 6.26）。"""
    assert window_with_flow.state.add_flow_lines() is False


def test_既定の向きは水平(window_with_flow):
    """横に流すのがいちばん多いので、出発点で迷わせない（→ 6.26）。"""
    assert panel(window_with_flow).flow_lines.angle == 0.0


def test_入れた分は1手で戻る(window_with_panel):
    window_with_panel.state.add_flow_lines()
    window_with_panel.state.undo()
    assert panel(window_with_panel).flow_lines is None


def test_消せる(window_with_flow):
    assert window_with_flow.state.remove_flow_lines() is True
    assert panel(window_with_flow).flow_lines is None


def test_消したものは1手で戻る(window_with_flow):
    seed = panel(window_with_flow).flow_lines.seed
    window_with_flow.state.remove_flow_lines()
    window_with_flow.state.undo()
    assert panel(window_with_flow).flow_lines.seed == seed


def test_コマを消せば一緒に消える(window_with_flow):
    """コマの属性なので、紐づけを外して残す相手がいない（→ 6.26）。"""
    window_with_flow.delete_selected()
    assert window_with_flow.state.page.panels == []


def test_メニューの文言が入れる_消すで入れ替わる(window_with_panel):
    window = window_with_panel
    assert window.flow_menu.toggle_action.text() == "入れる"
    window.toggle_flow_lines()
    assert window.flow_menu.toggle_action.text() == "消す"
    window.toggle_flow_lines()
    assert panel(window).flow_lines is None


def test_調整の項目は入っているときだけ押せる(window_with_panel):
    window = window_with_panel
    assert all(not action.isEnabled() for action in window.flow_menu.actions)
    window.toggle_flow_lines()
    assert all(action.isEnabled() for action in window.flow_menu.actions)


# -- 集中線との同居（→ 6.26） -------------------------------------------------


def test_集中線と両方入れられる(window_with_panel):
    """別の項目なので、止めなければ両方入る。止めるほうがコードが増える。"""
    window = window_with_panel
    assert window.state.add_focus_lines() is True
    assert window.state.add_flow_lines() is True
    assert panel(window).focus_lines is not None
    assert panel(window).flow_lines is not None


def test_流線を消しても集中線は残る(window_with_panel):
    window = window_with_panel
    window.state.add_focus_lines()
    window.state.add_flow_lines()
    window.state.remove_flow_lines()
    assert panel(window).focus_lines is not None
    assert panel(window).flow_lines is None


def test_複製すれば写しにも乗る(window_with_flow):
    """保存形式を1往復させるので自動で写る（→ 6.15）。長さを1つも
    絶対値で持たないので、写した先で形も崩れない。
    """
    window = window_with_flow
    window.state.duplicate_selected()
    copies = [p for p in window.state.page.panels if p.flow_lines is not None]
    assert len(copies) == 2
    assert copies[0].flow_lines.seed == copies[1].flow_lines.seed


# -- 本数・太さ・長さ・振り直し ----------------------------------------------


def test_本数を増減できる(window_with_flow):
    state = window_with_flow.state
    before = panel(window_with_flow).flow_lines.count
    assert state.step_flow_count(1) is True
    assert panel(window_with_flow).flow_lines.count == before + FL.COUNT_STEP
    assert state.step_flow_count(-1) is True
    assert panel(window_with_flow).flow_lines.count == before


def test_太さを増減できる(window_with_flow):
    state = window_with_flow.state
    before = panel(window_with_flow).flow_lines.width
    assert state.step_flow_width(1) is True
    assert panel(window_with_flow).flow_lines.width == pytest.approx(
        before + FL.WIDTH_STEP
    )


def test_長さを増減できる(window_with_flow):
    """集中線には無い項目。端が見える以上、長さは見た目の主要素になる
    （→ 6.26）。
    """
    state = window_with_flow.state
    before = panel(window_with_flow).flow_lines.length
    assert state.step_flow_length(1) is True
    assert panel(window_with_flow).flow_lines.length == pytest.approx(
        before + FL.LENGTH_STEP
    )
    assert state.step_flow_length(-1) is True
    assert panel(window_with_flow).flow_lines.length == pytest.approx(before)


def test_端まで来たら履歴に積まない(window_with_flow):
    """押しても何も変わらない操作で Undo の一手を使わせない
    （→ `_step_focus` と同じ流儀）。
    """
    window = window_with_flow
    with window.state.edit("端へ") as project:
        project.pages[0].panels[0].flow_lines.count = FL.COUNT_MAX
    depth = len(window.state.history._undo)
    assert window.state.step_flow_count(1) is False
    assert len(window.state.history._undo) == depth


def test_形を振り直せる(window_with_flow):
    window = window_with_flow
    before = panel(window).flow_lines
    angle, count, width, length = (
        before.angle,
        before.count,
        before.width,
        before.length,
    )
    assert window.state.reseed_flow() is True
    after = panel(window).flow_lines
    assert (after.angle, after.count, after.width, after.length) == (
        angle,
        count,
        width,
        length,
    )


def test_振り直しは1手で戻る(window_with_flow):
    window = window_with_flow
    before = panel(window).flow_lines.seed
    window.state.reseed_flow()
    window.state.undo()
    assert panel(window).flow_lines.seed == before


def test_流線の入っていないコマでは何も起きない(window_with_panel):
    state = window_with_panel.state
    assert state.step_flow_count(1) is False
    assert state.step_flow_width(1) is False
    assert state.step_flow_length(1) is False
    assert state.reseed_flow() is False
    assert state.toggle_flow_color() is False


# -- 色 ----------------------------------------------------------------------


def test_既定は黒(window_with_flow):
    assert panel(window_with_flow).flow_lines.white is False


def test_白に切り替えられる(window_with_flow):
    state = window_with_flow.state
    assert state.toggle_flow_color() is True
    assert panel(window_with_flow).flow_lines.white is True
    assert state.toggle_flow_color() is True
    assert panel(window_with_flow).flow_lines.white is False


def test_色の切り替えは形に触らない(window_with_flow):
    before = panel(window_with_flow).flow_lines
    values = (before.angle, before.count, before.width, before.length, before.seed)
    window_with_flow.state.toggle_flow_color()
    after = panel(window_with_flow).flow_lines
    assert (after.angle, after.count, after.width, after.length, after.seed) == values


def test_メニューの文言が白にする_黒に戻すで入れ替わる(window_with_flow):
    window = window_with_flow
    assert window.flow_menu.color_action.text() == "白にする"
    window.toggle_flow_color()
    assert window.flow_menu.color_action.text() == "黒に戻す"
    window.toggle_flow_color()
    assert window.flow_menu.color_action.text() == "白にする"


# -- つまみ ------------------------------------------------------------------


def test_つまみを引くと向きが変わる(window_with_flow):
    window = window_with_flow
    view = window.view
    start = view._scene.flow_angle_handle()
    cx, cy = PANEL_CENTER
    # 中心の真上へ引く＝ -90 度
    drag(view, start[0], start[1], cx, cy - 200.0)
    assert panel(window).flow_lines.angle == pytest.approx(-90.0, abs=1.0)


def test_向きを変えるのは1手(window_with_flow):
    window = window_with_flow
    view = window.view
    start = view._scene.flow_angle_handle()
    cx, cy = PANEL_CENTER
    drag(view, start[0], start[1], cx, cy - 200.0)
    window.state.undo()
    assert panel(window).flow_lines.angle == 0.0


def test_ドラッグ中はモデルに触らない(window_with_flow):
    """離すまで下見を描くだけ（集中線・斜めの境界と同じ流儀）。"""
    window = window_with_flow
    view = window.view
    start = view._scene.flow_angle_handle()
    cx, cy = PANEL_CENTER

    press(view, start[0], start[1])
    move_to(view, cx, cy - 200.0)
    assert panel(window).flow_lines.angle == 0.0
    assert view._scene.flow_preview is not None
    release(view, cx, cy - 200.0)
    assert panel(window).flow_lines.angle != 0.0
    assert view._scene.flow_preview is None


def test_Shiftで15度刻みになる(window_with_flow):
    """刻み方も刻み幅も画像の回転と同じ（→ 6.3、6.26）。"""
    window = window_with_flow
    view = window.view
    start = view._scene.flow_angle_handle()
    cx, cy = PANEL_CENTER
    # 中心から見て約 20 度の方向。刻みが効けば 15 度に寄る
    shift_drag(view, start[0], start[1], cx + 200.0, cy + 73.0)
    assert panel(window).flow_lines.angle == pytest.approx(15.0)


def test_つまみはコマの移動より先に拾う(window_with_flow):
    """つまみはコマの内側に出るので、後に見ると掴んだつもりがコマの
    移動になる（集中線と同じ → 6.26）。
    """
    window = window_with_flow
    view = window.view
    start = view._scene.flow_angle_handle()
    drag(view, start[0], start[1], start[0], start[1] - 40.0)
    assert panel(window).shape.bounds() == PANEL


def test_集中線のつまみのほうが先に拾われる(window_with_flow):
    """**重なったときに逃げられるのは集中線の側だけ**なので、あちらを
    先に見る（→ 6.26）。中心を流線のつまみの位置へ重ねて確かめる。
    """
    window = window_with_flow
    view = window.view
    window.state.add_focus_lines()
    handle = view._scene.flow_angle_handle()
    # 集中線の中心を、流線のつまみと同じ場所へ持っていく
    with window.state.edit("中心を重ねる") as project:
        project.pages[0].panels[0].focus_lines.center = F_center_ratio(handle)

    before = panel(window).flow_lines.angle
    drag(view, handle[0], handle[1], handle[0], handle[1] - 80.0)
    # 動いたのは集中線の中心のほうで、流線の向きは変わっていない
    assert panel(window).flow_lines.angle == before
    assert F.center_point(panel(window).focus_lines, PANEL)[1] == pytest.approx(
        handle[1] - 80.0, abs=1.0
    )


def F_center_ratio(point) -> tuple[float, float]:
    """ページ座標を、コマに対する割合へ直す（集中線の `center` の形）。"""
    return ((point[0] - PANEL.x) / PANEL.w, (point[1] - PANEL.y) / PANEL.h)


def test_角のつまみはつまみに邪魔されない(window_with_flow):
    """つまみを見る順が角・辺 → 流線であることの確認。"""
    window = window_with_flow
    view = window.view
    drag(view, PANEL.x, PANEL.y, PANEL.x - 40.0, PANEL.y - 40.0)
    assert panel(window).shape.bounds().x == pytest.approx(PANEL.x - 40.0)


def test_流線が無いコマではつまみが出ない(window_with_panel):
    assert window_with_panel.view._scene.flow_angle_handle() is None


def test_コマを選んでいなければつまみが出ない(window_with_flow):
    window_with_flow.state.select(None)
    assert window_with_flow.view._scene.flow_angle_handle() is None


def test_つまみを描いても落ちない(window_with_flow):
    """つまみは掴む側だけテストしても、描く側が落ちれば画面が出ない。
    集中線と同居させた状態（両方のつまみが出る）で1回通す。
    """
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    window = window_with_flow
    window.state.add_focus_lines()
    page = window.state.page
    target = QImage(int(page.size.w), int(page.size.h), QImage.Format.Format_ARGB32)
    target.fill(0)
    painter = QPainter(target)
    window.view._scene.render(
        painter, QRectF(target.rect()), QRectF(0.0, 0.0, page.size.w, page.size.h)
    )
    painter.end()


def test_つまみの無い場所を押せばこれまでどおり選べる(window_with_flow):
    window = window_with_flow
    window.state.select(None)
    click(window.view, PANEL.x + 20.0, PANEL.y + 20.0)
    assert window.state.selected_panel is not None


# -- 右クリック --------------------------------------------------------------


def submenus(window):
    """コマの右クリックメニューに畳んである部分メニューの一覧。"""
    menu = window.context_menu.build(*PANEL_CENTER)
    found = {}
    for action in menu.actions():
        sub = action.menu()
        if sub is not None:
            found[action.text()] = [
                item for item in sub.actions() if not item.isSeparator()
            ]
    return found


def test_右クリックにも同じ項目が畳んで出る(window_with_flow):
    """**メニューバーと同じ実体**を並べる（→ 6.12）。"""
    window = window_with_flow
    items = submenus(window)["流線"]
    assert window.flow_menu.toggle_action in items
    assert all(action in items for action in window.flow_menu.actions)


def test_集中線と流線が別々に畳まれる(window_with_flow):
    """1つにまとめない。決まるものが違う（→ 6.26）。"""
    found = submenus(window_with_flow)
    assert "集中線" in found
    assert "流線" in found
    assert found["集中線"] != found["流線"]


def test_文言の書き換えが右クリック側にも出る(window_with_panel):
    window = window_with_panel
    assert submenus(window)["流線"][0].text() == "入れる"
    window.toggle_flow_lines()
    assert submenus(window)["流線"][0].text() == "消す"
