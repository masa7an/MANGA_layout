"""複製の検証（画面なし。要件定義 6.15）。

見るのは3つ。**写る範囲**（選んだもの1つと、それに巻き込まれるものだけ）、
**id と紐づけの張り替え**、そして**メニューの名前と実際に写るものが揃って
いること**。

id を振り直さないと `attached_panel_id` の解決先が狂い（→ 6章）、
セリフの紐づけを張り替えないと、元のフキダシを動かしたときに
写したセリフだけが飛んでいく（→ 6.5）。どちらも見た目には出ないので、
ここで固定しておかないと壊れたことに気づけない。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.layout import LayoutSettings
from manga_layout.model import (
    BalloonObject,
    ImageObject,
    Panel,
    StickerObject,
    TextObject,
    new_project,
)
from manga_layout.slant import split_panel_slant
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import TOOL_SELECT

# 座標は px（要件定義 3章）
PANEL = Rect(120.0, 120.0, 720.0, 540.0)
BALLOON = Rect(200.0, 200.0, 300.0, 200.0)
TEXT = Rect(250.0, 250.0, 200.0, 150.0)

# ずらす量は隙間1つ分。**書き写さず設定から取る**（既定を変えたときに
# テストだけが古い数字を通してしまわないように）
GUTTER = LayoutSettings().gutter


# -- モデルの側 ----------------------------------------------------------------


@pytest.fixture
def project():
    """1ページに、コマ1枚だけ置いたところ。"""
    p = new_project()
    p.add_panel(p.pages[0], PANEL)
    return p


def only(page, kind):
    found = [f for f in page.floating if isinstance(f, kind)]
    assert len(found) == 1, f"{kind.__name__} が {len(found)} 個ある"
    return found[0]


def test_コマは中の画像ごと写る(project):
    page = project.pages[0]
    panel = page.panels[0]
    project.add_image(panel, "assets/abc.png", Rect(130.0, 130.0, 200.0, 150.0), (400, 300))

    copy = project.duplicate(page, panel.id, GUTTER, GUTTER)

    assert isinstance(copy, Panel)
    assert len(page.panels) == 2
    assert copy.shape.as_rect() == PANEL.translated(GUTTER, GUTTER)
    assert len(copy.children) == 1
    assert copy.children[0].rect == Rect(130.0 + GUTTER, 130.0 + GUTTER, 200.0, 150.0)


def test_画像の実体は増えない(project):
    """同じ `asset` を指すだけ（→ 5章）。ここが増えると作品が重くなる。"""
    page = project.pages[0]
    panel = page.panels[0]
    project.add_image(panel, "assets/abc.png", Rect(130.0, 130.0, 200.0, 150.0), (400, 300))

    copy = project.duplicate(page, panel.id, GUTTER, GUTTER)

    assert copy.children[0].asset == "assets/abc.png"
    assert project.referenced_assets() == {"assets/abc.png"}


def test_写したコマも中の画像も新しいidを持つ(project):
    page = project.pages[0]
    panel = page.panels[0]
    project.add_image(panel, "assets/abc.png", Rect(130.0, 130.0, 200.0, 150.0), (400, 300))

    copy = project.duplicate(page, panel.id, GUTTER, GUTTER)

    ids = [obj.id for obj in page.iter_objects()]
    assert len(ids) == len(set(ids))
    assert copy.id != panel.id
    assert copy.children[0].id != panel.children[0].id


def test_写したコマはロックされない(project):
    """置き場所を決めるために作ったものなので、いきなり動かせないと困る。"""
    page = project.pages[0]
    page.panels[0].locked = True

    copy = project.duplicate(page, page.panels[0].id, GUTTER, GUTTER)

    assert copy.locked is False
    assert page.panels[0].locked is True


def test_集中線もそのまま乗る(project):
    """中心も空きも割合なので、ずらしても直すところが無い（→ 6.16）。"""
    from manga_layout.focus import default_focus

    page = project.pages[0]
    page.panels[0].focus_lines = default_focus()

    copy = project.duplicate(page, page.panels[0].id, GUTTER, GUTTER)

    assert copy.focus_lines == page.panels[0].focus_lines


def test_画像だけを写すと同じコマの中に増える(project):
    page = project.pages[0]
    panel = page.panels[0]
    image = project.add_image(
        panel, "assets/abc.png", Rect(130.0, 130.0, 200.0, 150.0), (400, 300)
    )

    copy = project.duplicate(page, image.id, GUTTER, GUTTER)

    assert isinstance(copy, ImageObject)
    assert len(page.panels) == 1
    assert [c.id for c in panel.children] == [image.id, copy.id]
    assert copy.z > image.z


def test_フキダシは上のセリフごと写る(project):
    page = project.pages[0]
    balloon = project.add_balloon(page, BALLOON)
    text = project.add_text(page, "セリフ", TEXT)
    text.attached_balloon_id = balloon.id

    copy = project.duplicate(page, balloon.id, GUTTER, GUTTER)

    balloons = [f for f in page.floating if isinstance(f, BalloonObject)]
    texts = [f for f in page.floating if isinstance(f, TextObject)]
    assert len(balloons) == 2 and len(texts) == 2
    assert copy.rect == BALLOON.translated(GUTTER, GUTTER)


def test_写したセリフは写したフキダシに紐づく(project):
    """元を指したままだと、元を動かしたときに写しだけが飛んでいく（→ 6.5）。"""
    page = project.pages[0]
    balloon = project.add_balloon(page, BALLOON)
    text = project.add_text(page, "セリフ", TEXT)
    text.attached_balloon_id = balloon.id

    copy = project.duplicate(page, balloon.id, GUTTER, GUTTER)

    copied_text = next(
        f
        for f in page.floating
        if isinstance(f, TextObject) and f.id != text.id
    )
    assert copied_text.attached_balloon_id == copy.id
    assert text.attached_balloon_id == balloon.id

    # 元を動かしても、写したセリフは付いてこない
    before = copied_text.rect
    page.move_balloon(balloon.id, 100.0, 0.0)
    assert copied_text.rect == before


def test_しっぽの先端も一緒にずれる(project):
    """置いていくと写しだけしっぽが伸び、同じ形にならない（→ 6.4）。"""
    page = project.pages[0]
    balloon = project.add_balloon(page, BALLOON)
    balloon.tail = balloon.tail.translated(400.0, 500.0)
    tip = balloon.tail.tip

    copy = project.duplicate(page, balloon.id, GUTTER, GUTTER)

    assert copy.tail.tip == (tip[0] + GUTTER, tip[1] + GUTTER)
    assert balloon.tail.tip == tip


def test_コマへの紐づけは元のまま引き継ぐ(project):
    page = project.pages[0]
    panel = page.panels[0]
    balloon = project.add_balloon(page, BALLOON, attached_panel_id=panel.id)

    copy = project.duplicate(page, balloon.id, GUTTER, GUTTER)

    assert copy.attached_panel_id == panel.id


def test_セリフだけを写してもフキダシは増えない(project):
    page = project.pages[0]
    text = project.add_text(page, "セリフ", TEXT)

    copy = project.duplicate(page, text.id, GUTTER, GUTTER)

    assert isinstance(copy, TextObject)
    assert copy.content == "セリフ"
    assert [f for f in page.floating if isinstance(f, BalloonObject)] == []


def test_マークは単独で写る(project):
    page = project.pages[0]
    sticker = project.add_sticker(
        page, "exclaim", "assets/ex.png", Rect(300.0, 300.0, 80.0, 160.0), (200, 400)
    )

    copy = project.duplicate(page, sticker.id, GUTTER, GUTTER)

    assert isinstance(copy, StickerObject)
    assert copy.kind == "exclaim"
    assert copy.rect == Rect(300.0 + GUTTER, 300.0 + GUTTER, 80.0, 160.0)


def test_斜めに割ったコマは断る(project):
    """片方だけ写すと、相方のいない平行四辺形が1枚できる（→ 6.10）。"""
    page = project.pages[0]
    left, _right = split_panel_slant(project, page, page.panels[0].id, position=480.0)

    with pytest.raises(ValueError):
        project.duplicate(page, left.id, GUTTER, GUTTER)
    assert len(page.panels) == 2


def test_元の作品を壊さない(project):
    """写しは深い写し。片方を触ってももう片方に響かない。"""
    page = project.pages[0]
    panel = page.panels[0]
    copy = project.duplicate(page, panel.id, GUTTER, GUTTER)

    copy.border.width = 99.0
    assert panel.border.width != 99.0


# -- 画面の側 ------------------------------------------------------------------


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


def test_写したほうが選ばれる(window_with_panel):
    """他の「追加」と同じ。続けて押すと階段状に増える。"""
    state = window_with_panel.state
    original = state.page.panels[0].id

    copy = state.duplicate_selected()

    assert copy is not None
    assert state.selected_id == copy.id != original


def test_続けて押すと同じ場所に重ならない(window_with_panel):
    state = window_with_panel.state
    state.duplicate_selected()
    state.duplicate_selected()

    rects = [p.shape.as_rect() for p in state.page.panels]
    assert len(rects) == 3
    assert len(set(rects)) == 3


def test_ページからはみ出しても直さない(window_with_panel):
    """位置は利用者が決めるもの（ページサイズ変更と同じ → 6.1）。"""
    state = window_with_panel.state
    page_w = state.page.size.w
    with state.edit("端へ寄せる") as project:
        panel = project.pages[0].panels[0]
        panel.shape = panel.shape.translated(page_w - PANEL.right, 0.0)

    copy = state.duplicate_selected()

    assert copy.shape.bounds().right > page_w


def test_Undoで1手で戻る(window_with_panel):
    state = window_with_panel.state
    state.duplicate_selected()
    assert state.history.undo_label == "コマの複製"

    state.undo()
    assert len(state.page.panels) == 1


def test_フキダシの複製もUndoは1手(window_with_panel):
    """セリフも一緒に写るが、戻すのは1回（→ 6.15）。"""
    state = window_with_panel.state
    balloon = state.add_balloon(BALLOON)
    text = state.add_text(TEXT, "セリフ")
    assert text.attached_balloon_id == balloon.id

    state.select(balloon.id)
    state.duplicate_selected()
    assert state.history.undo_label == "フキダシの複製"

    state.undo()
    assert len([f for f in state.page.floating if isinstance(f, BalloonObject)]) == 1
    assert len([f for f in state.page.floating if isinstance(f, TextObject)]) == 1


def test_何も選んでいなければ写さない(window_with_panel):
    state = window_with_panel.state
    state.select(None)

    assert state.duplicate_selected() is None
    assert len(state.page.panels) == 1


def test_斜めに割ったコマは押せるまま断る(window_with_panel):
    """グレーにすると「写せない」は伝わるが理由が伝わらない（→ 6.15）。"""
    state = window_with_panel.state
    with state.edit("斜めに割る") as project:
        page = project.pages[0]
        split_panel_slant(project, page, page.panels[0].id, position=480.0)
    state.select(state.page.panels[0].id)
    window_with_panel._refresh()

    assert window_with_panel.duplicate_action.isEnabled()
    assert state.duplicate_selected() is None
    assert len(state.page.panels) == 2


# -- メニューの名前 --------------------------------------------------------------


def test_名前に対象を出す(window_with_panel):
    """「複製」だけだと、コマと画像のどちらが写るのか押す前に分からない。"""
    state = window_with_panel.state
    action = window_with_panel.duplicate_action

    window_with_panel._refresh()
    assert action.text() == "コマを複製"

    state.add_balloon(BALLOON)
    window_with_panel._refresh()
    assert action.text() == "フキダシを複製"

    state.select(None)
    window_with_panel._refresh()
    assert action.text() == "複製"
    assert not action.isEnabled()


def test_名前と実際に写るものが揃っている(window_with_panel):
    """`object_label` が両方の出所。別々に持つと食い違わせられる。"""
    state = window_with_panel.state
    state.add_text(TEXT, "セリフ")
    window_with_panel._refresh()

    assert window_with_panel.duplicate_action.text() == "セリフを複製"
    copy = state.duplicate_selected()

    assert isinstance(copy, TextObject)
    assert state.history.undo_label == "セリフの複製"
