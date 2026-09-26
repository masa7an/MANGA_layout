"""`Alt+.` / `Alt+,` で選んでいるものを大きく／小さくする（要件定義 7章）。

フキダシ・画像・マークは中心を動かさずに1割ずつ、セリフは文字の大きさ、
コマは対象外。キーは画面側で拾うので、セリフの入力中は横取りしない。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import StickerObject
from manga_layout.stickers import STICKER_EXCLAIM
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.canvas import SIZE_STEP_FACTOR
from manga_layout.ui.state import TOOL_SELECT

PANEL = Rect(120.0, 120.0, 720.0, 540.0)
CENTER = (480.0, 390.0)


@pytest.fixture
def window(qapp, tmp_path):
    """保存先を持たせておく。マークの素材は `assets/` へ入る経路を通る。"""
    win = MainWindow(EditorState())
    win.state.save(tmp_path / "作品")
    with win.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    win.state.set_tool(TOOL_SELECT)
    yield win
    win.view.finish_text_edit(commit=False)
    win.state.history.mark_saved()
    win.close()


def alt_key(view, key) -> None:
    """Alt を押しながらキーを1回押す。画面側の受け口を直に呼ぶ
    （メニューのショートカットは offscreen では鳴らない → test_ui_tone.press_key）。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view.keyPressEvent(
        QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.AltModifier)
    )


def bigger(window) -> None:
    from PySide6.QtCore import Qt

    alt_key(window.view, Qt.Key.Key_Period)


def smaller(window) -> None:
    from PySide6.QtCore import Qt

    alt_key(window.view, Qt.Key.Key_Comma)


def selected(window):
    return window.state.selected_object


def assert_scaled(before: Rect, after: Rect, factor: float) -> None:
    """中心が動かず、縦横とも同じ倍率で変わったこと。"""
    assert after.w == pytest.approx(before.w * factor)
    assert after.h == pytest.approx(before.h * factor)
    assert after.center == pytest.approx(before.center)


@pytest.fixture
def with_balloon(window):
    window.state.add_balloon(Rect(300.0, 250.0, 200.0, 120.0))
    return window


@pytest.fixture
def with_image(window, png_bytes):
    window.state.place_image(window.state.page.panels[0].id, png_bytes)
    return window


@pytest.fixture
def with_sticker(window):
    window.state.add_sticker(STICKER_EXCLAIM, *CENTER)
    return window


@pytest.mark.parametrize("fixture", ["with_balloon", "with_image", "with_sticker"])
def test_中心を動かさずに1割大きくなる(fixture, request):
    window = request.getfixturevalue(fixture)
    before = selected(window).rect
    bigger(window)
    assert_scaled(before, selected(window).rect, SIZE_STEP_FACTOR)


@pytest.mark.parametrize("fixture", ["with_balloon", "with_image", "with_sticker"])
def test_小さくして大きくすると元に戻る(fixture, request):
    window = request.getfixturevalue(fixture)
    before = selected(window).rect
    smaller(window)
    assert selected(window).rect.w < before.w
    bigger(window)
    assert_scaled(before, selected(window).rect, 1.0)


def test_フキダシのしっぽの先端は動かない(with_balloon):
    before = selected(with_balloon).tail.tip
    bigger(with_balloon)
    assert selected(with_balloon).tail.tip == before


def test_傾いたマークも中心が軸(with_sticker):
    sticker = selected(with_sticker)
    with with_sticker.state.edit_page("回転") as page:
        target = page.find(sticker.id)
        assert isinstance(target, StickerObject)
        target.rotation = 30.0
    before = selected(with_sticker).rect
    bigger(with_sticker)
    after = selected(with_sticker)
    assert_scaled(before, after.rect, SIZE_STEP_FACTOR)
    assert after.rotation == 30.0


def test_セリフは文字が大きくなる(window):
    text = window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
    before_size = text.font.size_px
    before_rect = text.rect
    bigger(window)
    after = window.state.selected_text
    assert after.font.size_px > before_size
    assert after.rect == before_rect


def test_セリフはCtrlと同じだけ変わる(window):
    """`Ctrl+.` と同じ処理を通す（重複は本人が許容 2026-09-27）。"""
    window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
    start = window.state.selected_text.font.size_px
    window.step_text_size(1)
    by_ctrl = window.state.selected_text.font.size_px - start
    bigger(window)
    by_alt = window.state.selected_text.font.size_px - start - by_ctrl
    assert by_alt == pytest.approx(by_ctrl)


def test_セリフの入力中は横取りしない(window):
    text = window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
    window.view.begin_text_edit(text.id)
    assert window.view.is_editing_text
    before = window.state.selected_text.font.size_px
    bigger(window)
    assert window.state.selected_text.font.size_px == before


def test_コマは変わらない(window):
    panel = window.state.page.panels[0]
    window.state.select(panel.id)
    before = window.state.page.panels[0].shape.bounds()
    bigger(window)
    assert window.state.page.panels[0].shape.bounds() == before


def test_トーンの入った画像でも濃さではなく大きさが変わる(with_image):
    """濃さのキーは修飾キーを見ずに `.` を拾う。Alt 付きをそちらへ流さない。"""
    with_image.state.add_tone()
    image = selected(with_image)
    before_rect = image.rect
    before_density = image.tone.density
    bigger(with_image)
    after = selected(with_image)
    assert after.tone.density == before_density
    assert_scaled(before_rect, after.rect, SIZE_STEP_FACTOR)


def test_連打は元に戻す1回で戻る(with_balloon):
    before = selected(with_balloon).rect
    for _ in range(3):
        bigger(with_balloon)
    with_balloon.state.undo()
    assert selected(with_balloon).rect == before


def test_最小の大きさで止まる(with_balloon):
    minimum = with_balloon.state.settings.min_panel_size
    for _ in range(80):
        smaller(with_balloon)
    rect = selected(with_balloon).rect
    assert min(rect.w, rect.h) == pytest.approx(minimum)


class Test_メニュー:
    def test_編集メニューから大きくできる(self, with_balloon):
        before = selected(with_balloon).rect
        with_balloon._refresh()
        up, _down = with_balloon.edit_menu.size_actions
        assert up.isEnabled()
        up.trigger()
        assert_scaled(before, selected(with_balloon).rect, SIZE_STEP_FACTOR)

    def test_項目名にキーを出す(self, window):
        names = [a.text() for a in window.edit_menu.size_actions]
        assert names == ["大きくする（Alt+.）", "小さくする（Alt+,）"]

    def test_ショートカットにはしない(self, window):
        """割り当てるとセリフの入力中にも効いてしまう。"""
        assert all(a.shortcut().isEmpty() for a in window.edit_menu.size_actions)

    def test_コマを選んでいるときはグレー(self, window):
        window.state.select(window.state.page.panels[0].id)
        window._refresh()
        assert not any(a.isEnabled() for a in window.edit_menu.size_actions)

    def test_セリフを選んでいれば押せる(self, window):
        window.state.add_text(Rect(300.0, 250.0, 200.0, 120.0))
        window._refresh()
        assert all(a.isEnabled() for a in window.edit_menu.size_actions)
