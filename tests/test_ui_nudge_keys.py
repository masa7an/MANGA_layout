"""`Alt+矢印` で選んでいるものを1px ずつ動かす（要件定義 7章）。

ドラッグと同じ確定の処理を通すので、ここで見るのはキーの受け口と、
連打のまとめ方・ロック・素の矢印を取らないこと。
"""

from __future__ import annotations

import pytest
from test_ui_size_keys import (  # noqa: F401  （fixture を借りる）
    CENTER,
    PANEL,
    window,
    with_balloon,
    with_image,
    with_sticker,
)

from manga_layout import Rect

ARROWS = {
    "Left": (-1.0, 0.0),
    "Right": (1.0, 0.0),
    "Up": (0.0, -1.0),
    "Down": (0.0, 1.0),
}


def press(view, name: str, alt: bool = True) -> None:
    """矢印を1回押す。画面側の受け口を直に呼ぶ（→ test_ui_size_keys.alt_key）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    modifiers = Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier
    view.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, getattr(Qt.Key, f"Key_{name}"), modifiers)
    )


def bounds(window) -> Rect:
    return window.state.selected_bounds


@pytest.mark.parametrize("name", list(ARROWS))
@pytest.mark.parametrize("fixture", ["with_balloon", "with_image", "with_sticker"])
def test_1pxずつ動く(fixture, name, request):
    window = request.getfixturevalue(fixture)
    before = bounds(window)
    press(window.view, name)
    dx, dy = ARROWS[name]
    after = bounds(window)
    assert (after.x, after.y) == pytest.approx((before.x + dx, before.y + dy))
    assert (after.w, after.h) == pytest.approx((before.w, before.h))


def test_セリフも動く(window):
    window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
    before = window.state.selected_text.rect
    press(window.view, "Right")
    assert window.state.selected_text.rect == before.translated(1.0, 0.0)


def test_コマも動く(window):
    panel = window.state.page.panels[0]
    window.state.select(panel.id)
    before = bounds(window)
    press(window.view, "Down")
    assert bounds(window) == before.translated(0.0, 1.0)


def test_ロックしたコマは動かない(window):
    panel = window.state.page.panels[0]
    window.state.select(panel.id)
    window.state.set_panel_locked(panel.id, True)
    before = bounds(window)
    press(window.view, "Down")
    assert bounds(window) == before


def test_フキダシのしっぽの先端は動かず上のセリフは付いてくる(with_balloon):
    state = with_balloon.state
    balloon = state.selected_balloon
    tip = balloon.tail.tip
    text = state.add_text(Rect(340.0, 270.0, 100.0, 60.0))
    assert text in state.page.texts_on_balloon(balloon.id)
    text_before = text.rect
    state.select(balloon.id)
    press(with_balloon.view, "Left")
    assert state.selected_balloon.tail.tip == tip
    assert state.page.find(text.id).rect == text_before.translated(-1.0, 0.0)


def test_連打は元に戻す1回で戻る(with_sticker):
    before = bounds(with_sticker)
    for _ in range(5):
        press(with_sticker.view, "Right")
    assert bounds(with_sticker) == before.translated(5.0, 0.0)
    with_sticker.state.undo()
    assert bounds(with_sticker) == before


def test_向きを変えても1手にまとまる(with_sticker):
    """右へ行き過ぎて左へ戻す、は1回の位置合わせ。"""
    before = bounds(with_sticker)
    press(with_sticker.view, "Right")
    press(with_sticker.view, "Down")
    press(with_sticker.view, "Left")
    with_sticker.state.undo()
    assert bounds(with_sticker) == before


def test_選び直すと別の1手になる(with_sticker, png_bytes):
    state = with_sticker.state
    sticker_id = state.selected_id
    press(with_sticker.view, "Right")
    sticker_moved = state.page.find(sticker_id).rect
    state.place_image(state.page.panels[0].id, png_bytes)
    image_before = bounds(with_sticker)
    press(with_sticker.view, "Right")
    state.undo()
    # 戻るのは画像の1px だけで、マークの1px は残る
    assert bounds(with_sticker) == image_before
    assert state.page.find(sticker_id).rect == sticker_moved


def test_状態表示に今の位置を出す(with_sticker):
    messages: list[str] = []
    with_sticker.state.message.connect(messages.append)
    press(with_sticker.view, "Right")
    b = bounds(with_sticker)
    assert messages[-1] == f"位置: {b.x:.0f}, {b.y:.0f} px"


def test_素の矢印では動かない(with_sticker):
    """素の矢印は画面のスクロールに使われている。"""
    before = bounds(with_sticker)
    press(with_sticker.view, "Right", alt=False)
    assert bounds(with_sticker) == before


def test_セリフの入力中は横取りしない(window):
    text = window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
    window.view.begin_text_edit(text.id)
    assert window.view.is_editing_text
    before = window.state.selected_text.rect
    press(window.view, "Right")
    assert window.state.selected_text.rect == before


def test_何も選んでいなければ何もしない(window):
    window.state.select(None)
    label = window.state.history.undo_label
    press(window.view, "Right")
    assert window.state.history.undo_label == label
