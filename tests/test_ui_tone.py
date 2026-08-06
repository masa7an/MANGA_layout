"""トーンの操作まわりの検証（画面なし。要件定義 10.1）。

置き換えの中身と保存形式は tests/test_tone.py。ここで見るのは
「メニュー → モデルの変更 → 履歴に積む」と、**道具の出入り**。

集中線・流線（tests/test_ui_focus.py、tests/test_ui_flow.py）と作りは
同じだが、**持ち主がコマではなく画像**で、**つまみを道具に逃がしてある**
のがここの違い。

画面に出る文字の扱いは tests/test_ui_balloon.py の冒頭と同じ方針。
"""

from __future__ import annotations

import pytest
from test_ui_balloon import drag, move_to, press, release
from test_ui_context_menu import folded_labels, labels

from manga_layout import Rect, tone as TN
from manga_layout.model import (
    TONE_KIND_GRAY as KIND_GRAY,
    TONE_KIND_STRIPES as KIND_STRIPES,
    TONE_KIND_WHITE as KIND_WHITE,
)
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import (
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_ROUGH,
    TOOL_SELECT,
    TOOL_TONE_AREA,
)

# 座標は px（要件定義 3章）
PANEL = Rect(120.0, 120.0, 720.0, 540.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def dark_png(qapp) -> bytes:
    """半分が黒ベタの画像。

    **試験用の画像に黒が要る。** 手持ちの `rgb_opaque.png` には
    しきい値より暗い画素が1つも無く、トーンを入れても結果が1画素も
    変わらないため、置き換えが効いたかどうかを見分けられない
    （これで一度テストを取り違えた）。
    """
    from PySide6.QtGui import QColor, QImage, QPainter

    from manga_layout.images import to_png_bytes

    image = QImage(120, 120, QImage.Format.Format_ARGB32)
    image.fill(QColor("#FFFFFF"))
    painter = QPainter(image)
    painter.fillRect(10, 10, 100, 50, QColor("#000000"))
    painter.end()
    return to_png_bytes(image)


@pytest.fixture
def window_with_image(window, dark_png):
    """コマに画像を1枚貼り、その画像が選ばれている状態。"""
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    panel_id = window.state.page.panels[0].id
    window.state.place_image(panel_id, dark_png)
    window.state.set_tool(TOOL_SELECT)
    return window


@pytest.fixture
def window_with_tone(window_with_image):
    """トーンを入れた画像が選ばれている状態。"""
    window_with_image.state.add_tone()
    return window_with_image


def image(window):
    return window.state.page.panels[0].children[0]


# -- 入れる・消す -----------------------------------------------------------


def test_画像を選んで入れられる(window_with_image):
    assert window_with_image.state.add_tone() is True
    assert image(window_with_image).tone is not None


def test_2つは入らない(window_with_tone):
    assert window_with_tone.state.add_tone() is False


def test_絵の入っていないコマでは入らない(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(window.state.page.panels[0].id)
    assert window.state.add_tone() is False


def test_コマを選んだだけでも入れられる(window_with_image):
    """**絵を選び直さなくてよい。**

    トーンの持ち主は画像だが、絵を選ぶにはコマをダブルクリックして中へ
    入る必要がある。そこを要求すると、集中線・流線と同じつもりでコマを
    選んだ利用者にはただグレーの項目に見える（本人談 2026-08-06）。
    """
    state = window_with_image.state
    state.select(state.page.panels[0].id)
    assert state.selected_image is None
    assert state.add_tone() is True
    assert image(window_with_image).tone is not None


def test_コマを選んだ状態でも項目が押せる(window_with_image):
    state = window_with_image.state
    state.select(state.page.panels[0].id)
    window_with_image._refresh()
    assert window_with_image.tone_menu.toggle_action.isEnabled()


def test_絵が複数あるコマでは断る(window_with_image, dark_png):
    """どちらに掛けるかは黙って選べない。背景の上にキャラを重ねる使い方がある。"""
    state = window_with_image.state
    panel_id = state.page.panels[0].id
    state.place_image(panel_id, dark_png)  # 2枚目
    state.select(panel_id)

    assert state.tone_ambiguous is True
    assert state.tone_image is None
    assert state.add_tone() is False
    assert all(c.tone is None for c in state.page.panels[0].children)


def test_絵が複数でも項目はグレーにしない(window_with_image, dark_png):
    """グレーだと「使えない」は伝わるが理由が伝わらない（→ 6.15 と同じ線引き）。"""
    state = window_with_image.state
    panel_id = state.page.panels[0].id
    state.place_image(panel_id, dark_png)
    state.select(panel_id)
    window_with_image._refresh()
    assert window_with_image.tone_menu.toggle_action.isEnabled()


def test_入れた時点では絞らない(window_with_tone):
    """まず全体に掛けて様子を見るほうが手数が少ない（→ 要件定義 10.1）。"""
    assert image(window_with_tone).tone.area is None


def test_消せる(window_with_tone):
    assert window_with_tone.state.remove_tone() is True
    assert image(window_with_tone).tone is None


def test_右クリックに並ぶ_絵を選んでいるとき(window_with_tone):
    """集中線・流線と同じように畳んで出す（本人の要望 2026-08-06）。"""
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    assert "トーン" in labels(menu)


def test_右クリックに並ぶ_コマを選んでいるとき(window_with_tone):
    """コマを右クリックした人にも行き先が要る（集中線・流線と同じ場所）。"""
    state = window_with_tone.state
    state.select(state.page.panels[0].id)
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    names = labels(menu)
    assert "トーン" in names
    assert names.index("トーン") == names.index("流線") + 1, "集中線・流線の隣"


def test_右クリックの畳みに中身が入っている(window_with_tone):
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    # 増減する項目は「濃く（6/17）」の形で段数が付く（→ 6.27）
    assert any(name.startswith("濃く") for name in folded_labels(menu, "トーン"))


def test_範囲の項目が入れるのすぐ下に並ぶ(window_with_tone):
    """入れた直後に遠くのメニューへ移らせない（本人談 2026-08-06）。"""
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    names = folded_labels(menu, "トーン")
    assert names[:3] == ["消す", TOOL_LABELS[TOOL_TONE_AREA], "範囲を全体に戻す"]


def test_範囲を調整する道具は右クリックにも写る(window_with_tone):
    """道具は普通は写さないが、この道具には「ここに〜」の相手がいない。"""
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    assert TOOL_LABELS[TOOL_TONE_AREA] in folded_labels(menu, "トーン")


def test_他の道具は右クリックに写らない(window_with_tone):
    """写すのはトーン範囲の道具だけ。他まで並ぶと「ここに〜」と2通りになる。"""
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    names = folded_labels(menu, "トーン")
    assert TOOL_LABELS[TOOL_ROUGH] not in names


def test_トーンが無いと道具の項目も押せない(window_with_image):
    """掴めるものが無い道具へ持ち替えさせない（ラフと同じ → 6.23）。"""
    window_with_image._refresh()
    assert not window_with_image.tone_menu.area_tool_action.isEnabled()
    window_with_image.state.add_tone()
    window_with_image._refresh()
    assert window_with_image.tone_menu.area_tool_action.isEnabled()


def test_絵の無いコマでは右クリックに出さない(window):
    """押しても何も起きない項目を並べない（→ 6.12）。"""
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(window.state.page.panels[0].id)
    menu = window.context_menu.build(0.0, 0.0)
    assert "トーン" not in labels(menu)


def test_メニューの項目が入れる_消すで入れ替わる(window_with_image):
    menu = window_with_image.image_menu.tone
    window_with_image._refresh()
    assert menu.toggle_action.text() == "入れる"
    window_with_image.toggle_tone()
    window_with_image._refresh()
    assert menu.toggle_action.text() == "消す"


def test_入れていない画像では調整の項目が押せない(window_with_image):
    menu = window_with_image.image_menu.tone
    window_with_image._refresh()
    assert all(not action.isEnabled() for action in menu.actions)


# -- 何段目かを項目名に出す（→ 6.27）----------------------------------------


def level_text(window, name: str) -> str:
    """トーンのメニューから、その項目の今の文言を1つ取り出す。"""
    for action, base, _field, _key in window.image_menu.tone.level_actions:
        if base == name:
            return action.text()
    raise AssertionError(f"{name} という項目が無い")


def test_項目名に今の段数が出る(window_with_tone):
    """一回きりに見える文言だと、重ねてよいことが伝わらない（本人談 2026-08-06）。"""
    window_with_tone._refresh()
    assert level_text(window_with_tone, "細い線を残す") == "細い線を残す（2/20）"


def test_押すたびに数字が動く(window_with_tone):
    """**動くことが「もう一度押してよい」の合図。**"""
    window_with_tone.state.step_tone_thin(1)
    window_with_tone._refresh()
    assert level_text(window_with_tone, "細い線を残す") == "細い線を残す（3/20）"
    # 対になる項目にも同じ数字を出す。片方だけだと、2つが同じ1つの値を
    # 上げ下げしていることが伝わらない
    assert level_text(window_with_tone, "細い線も塗る") == "細い線も塗る（3/20）"


def test_キーの付く項目は括弧を重ねない(window_with_tone):
    """「拾う黒を増やす（Shift+]）（3/20）」は括弧が2つ続いて読みにくい。"""
    window_with_tone._refresh()
    assert level_text(window_with_tone, "拾う黒を増やす") == "拾う黒を増やす 3/20（Shift+]）"


def test_トーンが無いときは数字を出さない(window_with_image):
    """出す値が無い。押せない状態でもあるので、名前だけに戻す。"""
    window_with_image._refresh()
    assert level_text(window_with_image, "細い線を残す") == "細い線を残す"
    assert level_text(window_with_image, "拾う黒を増やす") == "拾う黒を増やす（Shift+]）"


def test_向きにだけ数字を付けない(window_with_tone):
    """ぐるぐる回るだけで端が無く、「あと何回」に意味が無い。"""
    window_with_tone._refresh()
    names = [base for _a, base, _f, _k in window_with_tone.image_menu.tone.level_actions]
    assert "15度回す" not in names


def test_キーで動かしても数字が付いてくる(window_with_tone):
    """`Shift+]` はメニューを開かずに押す。次に開いたとき合っていること。"""
    from PySide6.QtCore import Qt

    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    window_with_tone._refresh()
    assert level_text(window_with_tone, "拾う黒を増やす") == "拾う黒を増やす 4/20（Shift+]）"


def test_Undoで戻すと数字も戻る(window_with_tone):
    """**押した回数を数えていない**ので、戻せば数字も一緒に戻る（→ 6.27）。"""
    window_with_tone.state.step_tone_thin(1)
    window_with_tone.state.undo()
    window_with_tone._refresh()
    assert level_text(window_with_tone, "細い線を残す") == "細い線を残す（2/20）"


def test_絞っていなければ範囲を戻す項目は押せない(window_with_tone):
    menu = window_with_tone.image_menu.tone
    window_with_tone._refresh()
    assert not menu.clear_area_action.isEnabled()

    window_with_tone.state.set_tone_area(image(window_with_tone).id, Rect(0, 0, 0.5, 0.5))
    window_with_tone._refresh()
    assert menu.clear_area_action.isEnabled()


# -- 種類（斜線・灰色・白抜き → 6.27）---------------------------------------


def kind_action(window, kind: str):
    for action, k in window.image_menu.tone.kind_actions:
        if k == kind:
            return action
    raise AssertionError(f"{kind} の項目が無い")


def test_入れた直後は斜線(window_with_tone):
    """灰色・白抜きはクリスタで貼り直す前提の絵なので、出発点にしない。"""
    assert image(window_with_tone).tone.kind == KIND_STRIPES


def test_種類を切り替えられる(window_with_tone):
    assert window_with_tone.state.set_tone_kind(KIND_GRAY) is True
    assert image(window_with_tone).tone.kind == KIND_GRAY


def test_同じ種類をもう一度押しても何も起きない(window_with_tone):
    """履歴に積まない（端まで来た増減と同じ扱い）。"""
    assert window_with_tone.state.set_tone_kind(KIND_STRIPES) is False


def test_種類を戻せる(window_with_tone):
    window_with_tone.state.set_tone_kind(KIND_WHITE)
    window_with_tone.state.undo()
    assert image(window_with_tone).tone.kind == KIND_STRIPES


def test_今の種類にレ点が付く(window_with_tone):
    """3つあるので、名前の書き換え（→ 流線の色）では今どれかが読めない。"""
    window_with_tone._refresh()
    assert kind_action(window_with_tone, KIND_STRIPES).isChecked()
    assert not kind_action(window_with_tone, KIND_GRAY).isChecked()

    window_with_tone.state.set_tone_kind(KIND_GRAY)
    window_with_tone._refresh()
    assert kind_action(window_with_tone, KIND_GRAY).isChecked()
    assert not kind_action(window_with_tone, KIND_STRIPES).isChecked()


def test_トーンが無ければどれにもレ点が付かない(window_with_image):
    window_with_image._refresh()
    assert all(not a.isChecked() for a, _k in window_with_image.image_menu.tone.kind_actions)


def test_灰色では斜線だけの項目が押せない(window_with_tone):
    """押しても絵が変わらない項目を押せるままにしない（→ 6.12）。"""
    menu = window_with_tone.image_menu.tone
    window_with_tone.state.set_tone_kind(KIND_GRAY)
    window_with_tone._refresh()
    assert all(not a.isEnabled() for a in menu.stripes_actions), "間隔と向き"
    assert all(a.isEnabled() for a in menu.density_actions), "濃さは灰色でも効く"


def test_白抜きでは濃さも押せない(window_with_tone):
    menu = window_with_tone.image_menu.tone
    window_with_tone.state.set_tone_kind(KIND_WHITE)
    window_with_tone._refresh()
    assert all(not a.isEnabled() for a in menu.density_actions)
    assert all(not a.isEnabled() for a in menu.stripes_actions)


def test_斜線に戻すと効かなかった項目が戻る(window_with_tone):
    """**値を消していない**ので、調整もそのまま返る。"""
    menu = window_with_tone.image_menu.tone
    window_with_tone.state.step_tone_pitch(1)
    before = image(window_with_tone).tone.pitch
    window_with_tone.state.set_tone_kind(KIND_WHITE)
    window_with_tone.state.set_tone_kind(KIND_STRIPES)
    window_with_tone._refresh()
    assert all(a.isEnabled() for a in menu.stripes_actions)
    assert image(window_with_tone).tone.pitch == pytest.approx(before)


def test_種類は右クリックにも並ぶ(window_with_tone):
    """写しているのは同じ QAction なので、レ点も一緒に出る（→ 6.27）。"""
    window_with_tone.state.set_tone_kind(KIND_GRAY)
    window_with_tone._refresh()
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    assert {"斜線", "灰色", "白抜き"} <= set(folded_labels(menu, "トーン"))


def test_トーンの無い画像では種類も押せない(window_with_image):
    window_with_image._refresh()
    assert all(not a.isEnabled() for a, _k in window_with_image.image_menu.tone.kind_actions)


# -- Undo -------------------------------------------------------------------


def test_入れたのを戻せる(window_with_tone):
    window_with_tone.state.undo()
    assert image(window_with_tone).tone is None


def test_調整を戻せる(window_with_tone):
    before = image(window_with_tone).tone.density
    window_with_tone.state.step_tone_density(1)
    assert image(window_with_tone).tone.density != before
    window_with_tone.state.undo()
    assert image(window_with_tone).tone.density == before


# -- 増減 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "step, field",
    [
        ("step_tone_density", "density"),
        ("step_tone_pitch", "pitch"),
        ("step_tone_threshold", "threshold"),
        ("step_tone_thin", "thin"),
    ],
)
def test_増やして減らすと元に戻る(window_with_tone, step, field):
    before = getattr(image(window_with_tone).tone, field)
    getattr(window_with_tone.state, step)(1)
    getattr(window_with_tone.state, step)(-1)
    assert getattr(image(window_with_tone).tone, field) == pytest.approx(before)


def test_端まで来たら履歴に積まない(window_with_tone):
    """押し続けても Undo の回数だけが増えていく、という状態を作らない。"""
    state = window_with_tone.state
    for _ in range(40):
        state.step_tone_density(1)
    assert image(window_with_tone).tone.density == pytest.approx(TN.DENSITY_MAX)
    assert state.step_tone_density(1) is False


def test_向きは15度ずつ回る(window_with_tone):
    before = image(window_with_tone).tone.angle
    window_with_tone.state.step_tone_angle(1)
    assert image(window_with_tone).tone.angle == pytest.approx(before + TN.ANGLE_STEP)


def test_向きは畳んで持つ(window_with_tone):
    """-180〜180 に収める（画像の傾き・流線の向きと同じ）。"""
    state = window_with_tone.state
    for _ in range(24):
        state.step_tone_angle(1)
    assert -180.0 <= image(window_with_tone).tone.angle <= 180.0


def test_入れていない画像では増減しない(window_with_image):
    assert window_with_image.state.step_tone_density(1) is False


# -- 道具の出入り -----------------------------------------------------------


def test_絵の入っていないコマを選ぶと道具が外れる(window_with_tone):
    """掴めるものが1つも無い道具を持ったまま残さない（ラフと同じ → 6.23）。"""
    state = window_with_tone.state
    with state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], Rect(120.0, 700.0, 300.0, 200.0))
    empty = state.page.panels[1].id

    state.set_tool(TOOL_TONE_AREA)
    state.select(empty)
    assert state.tool == TOOL_SELECT


def test_絵を持つコマを選んでも道具は残る(window_with_tone):
    """コマ経由でも効くので、外す理由が無い（→ `EditorState.tone_image`）。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    state.select(state.page.panels[0].id)
    assert state.tool == TOOL_TONE_AREA


def test_トーンを消すと道具が外れる(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    state.remove_tone()
    state.undo()  # 消したのを戻すと今度は残る
    state.set_tool(TOOL_TONE_AREA)
    state.remove_tone()
    state._leave_tone_tool_if_gone()
    assert state.tool == TOOL_SELECT


def test_同じ項目をもう一度押すと調整をやめる(window_with_tone):
    """**入るのと出るのが同じ場所。** 持ち替えた覚えのないまま入っても戻れる
    （本人の指摘 2026-08-06）。
    """
    action = window_with_tone.tone_menu.area_tool_action
    action.trigger()
    assert window_with_tone.state.tool == TOOL_TONE_AREA

    action.trigger()
    assert window_with_tone.state.tool == TOOL_SELECT
    assert not action.isChecked(), "レ点も外れる"


def test_調整をやめても範囲は残る(window_with_tone):
    """やめるのは道具の持ち替えだけ。囲った範囲まで戻したら操作にならない。"""
    state = window_with_tone.state
    state.set_tone_area(image(window_with_tone).id, Rect(0.2, 0.2, 0.3, 0.3))
    action = window_with_tone.tone_menu.area_tool_action
    action.trigger()
    action.trigger()
    assert image(window_with_tone).tone.area is not None


def test_作る道具は2回押しても解除されない(window_with_tone):
    """コマやフキダシは押すたびに1つ作る。2回目が「やめる」に化けると困る。"""
    action = window_with_tone._tool_actions[TOOL_PANEL]
    action.trigger()
    action.trigger()
    assert window_with_tone.state.tool == TOOL_PANEL


def test_道具を持ち替えただけで名乗りが変わる(window_with_tone):
    """**`_refresh` を自分で呼ばない。** 道具の持ち替えは選択も編集も伴わない
    ので、`tool_changed` を繋いでいないと次に何かを選ぶまで前の文言が残る
    （実際に漏れていた → 6.27）。
    """
    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    assert window_with_tone.hint_label.text().startswith("トーン範囲を調整中")
    assert window_with_tone.image_menu.tone.menu.title() == "トーン調整中"


def test_調整中は畳んだ親の名前が変わる(window_with_tone):
    """**レ点は畳みを開いた先にある。** 閉じたままでも分かるように
    （本人の要望 2026-08-06）。
    """
    menu = window_with_tone.image_menu.tone
    window_with_tone._refresh()
    assert menu.menu.title() == "トーン"

    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    window_with_tone._refresh()
    assert menu.menu.title() == "トーン調整中"

    window_with_tone.state.set_tool(TOOL_SELECT)
    window_with_tone._refresh()
    assert menu.menu.title() == "トーン"


def test_右クリックの畳みも調整中を名乗る(window_with_tone):
    """右クリックは組み直しなので、メニューバーとは別の経路（→ 6.27）。"""
    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    assert "トーン調整中" in labels(menu)


def test_コマの上の右クリックでも名乗る(window_with_tone):
    """絵の上とコマの上で、トーンの畳みを出す所が2か所ある。"""
    state = window_with_tone.state
    state.select(state.page.panels[0].id)
    state.set_tool(TOOL_TONE_AREA)
    menu = window_with_tone.context_menu.build(0.0, 0.0)
    assert "トーン調整中" in labels(menu)


def test_調整中は状態表示で名乗る(window_with_tone):
    """**「クリックしても選べない」としか見えないのが躓きの中身**（→ 6.27）。"""
    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    window_with_tone._refresh()
    hint = window_with_tone.hint_label.text()
    assert hint.startswith("トーン範囲を調整中")
    assert "もう一度" in hint, "出口も一緒に名乗る"


def test_調整をやめたら状態表示も戻る(window_with_tone):
    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    window_with_tone.state.set_tool(TOOL_SELECT)
    window_with_tone._refresh()
    assert "調整中" not in window_with_tone.hint_label.text()


def test_Undoでトーンが消えても道具が外れる(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    state.undo()  # 入れたのを取り消す
    assert state.tool == TOOL_SELECT


# -- 矩形を引く -------------------------------------------------------------


def test_絞っていない画像の内側から引ける(window_with_tone):
    """**この機能の一番よく使う経路。**

    絞る前の枠は画像いっぱいなので、「内側を押したら移動」を素直に書くと
    どこを押しても移動になり、新しく囲い直せなくなる（実際にそうなった）。
    """
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect

    drag(
        window_with_tone.view,
        box.x + box.w * 0.25,
        box.y + box.h * 0.25,
        box.x + box.w * 0.75,
        box.y + box.h * 0.75,
    )
    area = image(window_with_tone).tone.area
    assert area.w == pytest.approx(0.50, abs=0.02), "移動ではなく囲い直しになる"


def test_絞ったあとは内側を押すと動く(window_with_tone):
    state = window_with_tone.state
    box = image(window_with_tone).rect
    state.set_tone_area(image(window_with_tone).id, Rect(0.2, 0.2, 0.3, 0.3))
    state.set_tool(TOOL_TONE_AREA)

    # 範囲の中（0.35 あたり）を掴んで右へ引く
    drag(
        window_with_tone.view,
        box.x + box.w * 0.35,
        box.y + box.h * 0.35,
        box.x + box.w * 0.45,
        box.y + box.h * 0.35,
    )
    area = image(window_with_tone).tone.area
    assert area.x == pytest.approx(0.30, abs=0.02), "動いた"
    assert area.w == pytest.approx(0.30, abs=0.02), "大きさは変わらない"


def test_引いた矩形が割合で入る(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect

    drag(
        window_with_tone.view,
        box.x + box.w * 0.25,
        box.y + box.h * 0.25,
        box.x + box.w * 0.75,
        box.y + box.h * 0.75,
    )

    area = image(window_with_tone).tone.area
    assert area is not None
    assert area.x == pytest.approx(0.25, abs=0.02)
    assert area.w == pytest.approx(0.50, abs=0.02)


# -- 離れたコマとの行き来（→ 6.27）------------------------------------------


@pytest.fixture
def two_panels(window_with_tone, dark_png):
    """上下2コマ。上のコマの絵にトーンが入っていて、それが選ばれている状態。

    **上下を行き来しながらトーンを合わせる使い方**（本人の要望 2026-08-06）。
    この道具を持っている間は選び直せないので、下のコマを触ったつもりの
    ドラッグが上の絵に効いてしまう経路がある。
    """
    state = window_with_tone.state
    with state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], Rect(120.0, 800.0, 720.0, 540.0))
    state.place_image(state.page.panels[1].id, dark_png)
    # 上のコマの絵を選び直す（`place_image` で下の絵が選ばれている）
    state.select(image(window_with_tone).id)
    return window_with_tone


def lower_panel(window) -> Rect:
    return window.state.page.panels[1].bounds()


def test_離れたコマの上で引いても効かない(two_panels):
    """**弾かないと「上のコマのトーンが消えた」という見え方になる。**

    範囲が絵の外へ飛ぶだけなので、絵は元のまま・トーンだけが消える。
    原因の見当が付かない種類の事故（→ 6.27）。
    """
    state = two_panels.state
    state.set_tool(TOOL_TONE_AREA)
    box = lower_panel(two_panels)
    before = len(state.history._undo)

    drag(two_panels.view, box.x + 50, box.y + 50, box.x + 250, box.y + 250)

    assert image(two_panels).tone.area is None, "上の絵の範囲は動かない"
    assert len(state.history._undo) == before, "履歴にも積まない"


def test_離れたコマの上で引いたら理由を伝える(two_panels):
    """黙って何も起きないと、道具が壊れていると受け取られる（→ 6.15）。"""
    state = two_panels.state
    state.set_tool(TOOL_TONE_AREA)
    box = lower_panel(two_panels)
    said = []
    state.message.connect(said.append)

    drag(two_panels.view, box.x + 50, box.y + 50, box.x + 250, box.y + 250)

    assert said and "選び直して" in said[-1]


def test_はみ出す矩形は今までどおり入る(two_panels):
    """**弾くのは重なりが1つも無いときだけ。** はみ出しは画像の縁で切れる。"""
    state = two_panels.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(two_panels).rect

    # 絵の真ん中から、下のコマまで突き抜けるように引く
    drag(
        two_panels.view,
        box.x + box.w * 0.5,
        box.y + box.h * 0.5,
        box.x + box.w * 0.5 + 300,
        box.bottom + 400,
    )

    area = image(two_panels).tone.area
    assert area is not None
    assert area.y + area.h > 1.0, "下へはみ出したまま入る"


def test_引くと1手だけ積む(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect
    before = len(state.history._undo)

    drag(
        window_with_tone.view,
        box.x + 10,
        box.y + 10,
        box.x + box.w - 10,
        box.y + box.h - 10,
    )
    assert len(state.history._undo) == before + 1


def test_押しただけでは何も起きない(window_with_tone):
    """潰れた矩形が入ると、トーンが丸ごと消えたように見えて戻し方が分からない。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect

    press(window_with_tone.view, box.x + 100, box.y + 100)
    release(window_with_tone.view, box.x + 100, box.y + 100)
    assert image(window_with_tone).tone.area is None


def test_隅のつまみで広げ直せる(window_with_tone):
    state = window_with_tone.state
    box = image(window_with_tone).rect
    state.set_tone_area(image(window_with_tone).id, Rect(0.25, 0.25, 0.5, 0.5))
    state.set_tool(TOOL_TONE_AREA)

    # 右下の隅（se）を掴んで、さらに右下へ引く
    drag(
        window_with_tone.view,
        box.x + box.w * 0.75,
        box.y + box.h * 0.75,
        box.x + box.w * 0.90,
        box.y + box.h * 0.90,
    )
    area = image(window_with_tone).tone.area
    assert area.w == pytest.approx(0.65, abs=0.03)


# -- キーで拾う黒を合わせる ---------------------------------------------------


def press_key(view, key) -> None:
    """キーを1回押す。**メニューのショートカットは offscreen では鳴らない**
    ので、画面側の受け口を直に呼ぶ（→ 要件定義 7章）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.ShiftModifier)
    )


def test_キーで拾う黒を増やせる(window_with_tone):
    from PySide6.QtCore import Qt

    before = image(window_with_tone).tone.threshold
    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert image(window_with_tone).tone.threshold > before


def test_キーで拾う黒を減らせる(window_with_tone):
    from PySide6.QtCore import Qt

    before = image(window_with_tone).tone.threshold
    press_key(window_with_tone.view, Qt.Key.Key_BraceLeft)
    assert image(window_with_tone).tone.threshold < before


def test_角括弧そのものでも効く(window_with_tone):
    """Shift を押した `[` `]` は配列によって `{` `}` にならないことがある。"""
    from PySide6.QtCore import Qt

    before = image(window_with_tone).tone.threshold
    press_key(window_with_tone.view, Qt.Key.Key_BracketRight)
    assert image(window_with_tone).tone.threshold > before


def test_コマを選んだ状態でも効く(window_with_tone):
    """トーンの入り口を広げたのと揃える（→ `EditorState.tone_image`）。"""
    from PySide6.QtCore import Qt

    state = window_with_tone.state
    state.select(state.page.panels[0].id)
    before = image(window_with_tone).tone.threshold
    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert image(window_with_tone).tone.threshold > before


def test_連打しても履歴は1手(window_with_tone):
    """1回ずつ積むと、20回押した調整を戻すのに Undo を20回押すことになる。"""
    from PySide6.QtCore import Qt

    state = window_with_tone.state
    before_value = image(window_with_tone).tone.threshold
    before_steps = len(state.history._undo)

    for _ in range(8):
        press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert image(window_with_tone).tone.threshold > before_value
    assert len(state.history._undo) == before_steps + 1

    state.undo()
    assert image(window_with_tone).tone.threshold == before_value


def test_別の項目を触ると連打が区切れる(window_with_tone):
    """鍵に項目の名前を混ぜてあるので、濃さとしきい値は別の手になる。"""
    from PySide6.QtCore import Qt

    state = window_with_tone.state
    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    state.step_tone_density(1)
    before = len(state.history._undo)
    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert len(state.history._undo) == before + 1


def test_端まで来たらキーも効かない(window_with_tone):
    from PySide6.QtCore import Qt

    state = window_with_tone.state
    for _ in range(60):
        press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert image(window_with_tone).tone.threshold == TN.THRESHOLD_MAX
    assert state.step_tone_threshold(1) is False


def test_トーンが無ければ何も起きない(window_with_image):
    """素通りさせるだけ。他の操作を塞がない。"""
    from PySide6.QtCore import Qt

    press_key(window_with_image.view, Qt.Key.Key_BraceRight)
    assert image(window_with_image).tone is None


def test_拡大縮小のキーは奪わない(window_with_tone):
    """拾い具合を見るときこそ拡大したい（→ 要件定義 7章）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = window_with_tone.view
    before = view.view_scale
    view.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Plus,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert view.view_scale > before


def test_セリフを打っている間は横取りしない(window_with_tone):
    """メニューのショートカットにすると、Qt が入力欄より先に取ってしまう。

    画面側で拾い、入力中は 1 つも横取りしないという規則に合わせてある
    （→ 要件定義 7章の `+` / `-` と同じ扱い）。
    """
    from PySide6.QtCore import Qt

    state = window_with_tone.state
    text = state.add_text(Rect(200.0, 200.0, 200.0, 100.0))
    window_with_tone.view.begin_text_edit(text.id)
    assert window_with_tone.view.is_editing_text

    before = image(window_with_tone).tone.threshold
    press_key(window_with_tone.view, Qt.Key.Key_BraceRight)
    assert image(window_with_tone).tone.threshold == before


def test_矩形を画像全体に戻せる(window_with_tone):
    state = window_with_tone.state
    state.set_tone_area(image(window_with_tone).id, Rect(0.25, 0.25, 0.5, 0.5))
    assert state.clear_tone_area() is True
    assert image(window_with_tone).tone.area is None
    assert state.clear_tone_area() is False, "戻す先が無ければ何もしない"


def test_はみ出した矩形も直さない(window_with_tone):
    """0〜1 の外は絵が無いだけで、画像の縁で自然に切れる（→ 要件定義 10.1）。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect

    drag(
        window_with_tone.view,
        box.x - 200,
        box.y - 200,
        box.x + box.w * 0.5,
        box.y + box.h * 0.5,
    )
    assert image(window_with_tone).tone.area.x < 0.0


# -- 道具の間は他のものを触らない -------------------------------------------


def test_道具の間は選び直さない(window_with_tone):
    """選択枠を出していないので、裏で選ばれると見えないまま次の操作に効く。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    selected = state.selected_id

    box = image(window_with_tone).rect
    drag(window_with_tone.view, box.x + 10, box.y + 10, box.x + 100, box.y + 100)
    assert state.selected_id == selected


def test_道具の間はつまみが出ない(window_with_tone):
    """この道具を作った理由そのもの。残すとつまみが9個並ぶ（→ 要件定義 10.1）。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect
    # 画像の大きさを変えるつまみ（左上）を掴んでも、画像は動かない
    before = image(window_with_tone).rect
    drag(window_with_tone.view, box.x, box.y, box.x - 50, box.y - 50)
    assert image(window_with_tone).rect == before


def test_ラフの道具とは別物(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    assert state.tool != TOOL_ROUGH


# -- 描き出しへの反映 --------------------------------------------------------


def test_書き出しにも出る(window_with_tone):
    """作品の内容そのもの。`ImageCache` を通るのでサムネイルにも自動で出る。"""
    from manga_layout.ui.export import FullImages

    images = FullImages(window_with_tone.state)
    plain = images(image(window_with_tone))
    assert plain is not None

    # トーンを消した同じ画像と見比べる
    window_with_tone.state.remove_tone()
    bare = images(image(window_with_tone))
    assert bare.image.constBits() != plain.image.constBits()


def test_サムネイルの指紋に載る(window_with_tone):
    """`page.to_dict()` から作っているので、絞り直しただけでも描き直される。"""
    page = window_with_tone.state.page
    before = page.to_dict()
    window_with_tone.state.step_tone_density(1)
    assert window_with_tone.state.page.to_dict() != before


# -- 複製 -------------------------------------------------------------------


def test_複製すると写る(window_with_tone):
    """保存形式を1往復させるので自動で写る（→ 要件定義 6.15）。"""
    state = window_with_tone.state
    state.set_tone_area(image(window_with_tone).id, Rect(0.1, 0.2, 0.3, 0.4))
    state.duplicate_selected()

    children = state.page.panels[0].children
    assert len(children) == 2
    assert children[1].tone == children[0].tone


# -- 画面が落ちないこと ------------------------------------------------------


def test_道具を持っても描ける(window_with_tone):
    """×のつまみを描く経路を通す。落ちないことだけを見る。"""
    from PySide6.QtGui import QImage, QPainter

    window_with_tone.state.set_tool(TOOL_TONE_AREA)
    canvas = QImage(400, 300, QImage.Format.Format_ARGB32)
    painter = QPainter(canvas)
    window_with_tone.view.scene().render(painter)
    painter.end()


def test_絞っていなくても枠が出る(window_with_tone):
    """何も出さないと、道具を持ったのに掴めるものが無いように見える。"""
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    frame = window_with_tone.view.tone_area_rect(image(window_with_tone))
    assert frame == image(window_with_tone).rect


def test_道具の間もカーソルが変わる(window_with_tone):
    state = window_with_tone.state
    state.set_tool(TOOL_TONE_AREA)
    box = image(window_with_tone).rect
    move_to(window_with_tone.view, box.x + box.w / 2, box.y + box.h / 2)
