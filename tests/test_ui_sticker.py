"""マークの操作まわりの検証（画面なし。要件定義 6.14）。

保存形式と置き場所は tests/test_sticker.py。ここで見るのは
「道具 → 置く → 選ぶ → 動かす」がつながっているかと、
**フキダシ・セリフとの選択の取り合い**。

画面に出る文字の扱いは tests/test_ui_balloon.py の冒頭と同じ方針
（探すためには使わない／表示そのものが仕様のところは決め打ち）。
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import StickerObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.state import (
    STICKER_KIND_LABELS,
    TOOL_SELECT,
    TOOL_STICKER_EXCLAIM,
    TOOL_STICKER_EXCLAIM_QUESTION,
)
from manga_layout.stickers import STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION

from test_ui_balloon import click, drag

# 既定のマーク（長辺 240px）が余裕をもって収まる大きさ
PANEL = Rect(120.0, 120.0, 720.0, 540.0)
CENTER = (480.0, 390.0)


@pytest.fixture
def window(qapp, tmp_path):
    """保存先を持たせておく。素材は `assets/` へ入る経路を通る。"""
    win = MainWindow(EditorState())
    win.state.save(tmp_path / "作品")
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_panel(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(None)
    return window


@pytest.fixture
def window_with_sticker(window_with_panel):
    """コマの中にマークを1つ置いた状態。マークが選ばれている。"""
    window_with_panel.state.add_sticker(STICKER_EXCLAIM, *CENTER)
    return window_with_panel


def stickers(window) -> list[StickerObject]:
    return [f for f in window.state.page.floating if isinstance(f, StickerObject)]


class Test置く:
    def test_道具を選んでクリックすると置ける(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM)
        click(window_with_panel.view, *CENTER)
        assert len(stickers(window_with_panel)) == 1

    def test_置いたら選択の道具に戻る(self, window_with_panel):
        """コマ・フキダシと同じ「1回きり」（→ 6.9）。"""
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM)
        click(window_with_panel.view, *CENTER)
        assert window_with_panel.state.tool == TOOL_SELECT

    def test_置いたものが選ばれる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM)
        click(window_with_panel.view, *CENTER)
        assert window_with_panel.state.selected_sticker is stickers(window_with_panel)[0]

    def test_道具ごとに種類が変わる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM_QUESTION)
        click(window_with_panel.view, *CENTER)
        assert stickers(window_with_panel)[0].kind == STICKER_EXCLAIM_QUESTION

    def test_重なっているコマに紐づく(self, window_with_sticker):
        panel = window_with_sticker.state.page.panels[0]
        assert stickers(window_with_sticker)[0].attached_panel_id == panel.id

    def test_コマの外なら紐づかない(self, window_with_panel):
        sticker = window_with_panel.state.add_sticker(STICKER_EXCLAIM, 60.0, 60.0)
        assert sticker.attached_panel_id is None

    def test_素材の実体が取り込まれる(self, window_with_sticker):
        """置いた時点で assets/ に入る。作品は素材フォルダに依存しない。"""
        sticker = stickers(window_with_sticker)[0]
        assert sticker.asset
        assert window_with_sticker.state.read_asset(sticker.asset)

    def test_元の寸法を覚える(self, window_with_sticker):
        w, h = stickers(window_with_sticker)[0].src_px
        assert w > 0 and h > 0

    def test_同じ素材を2回置いても実体は1つ(self, window_with_panel):
        """内容ハッシュが名前なので重複しない（→ 5章）。"""
        a = window_with_panel.state.add_sticker(STICKER_EXCLAIM, *CENTER)
        b = window_with_panel.state.add_sticker(STICKER_EXCLAIM, 300.0, 300.0)
        assert a.asset == b.asset
        assert a.id != b.id

    def test_ドラッグで囲うとその中に収まる(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM)
        drag(window_with_panel.view, 200.0, 200.0, 500.0, 600.0)
        rect = stickers(window_with_panel)[0].rect
        box = Rect(200.0, 200.0, 300.0, 400.0)
        assert rect.x >= box.x - 0.5 and rect.right <= box.right + 0.5
        assert rect.y >= box.y - 0.5 and rect.bottom <= box.bottom + 0.5

    def test_ドラッグでも縦横比は崩れない(self, window_with_panel):
        window_with_panel.state.set_tool(TOOL_STICKER_EXCLAIM)
        drag(window_with_panel.view, 200.0, 200.0, 500.0, 600.0)
        sticker = stickers(window_with_panel)[0]
        w, h = sticker.src_px
        assert sticker.rect.w / sticker.rect.h == pytest.approx(w / h, rel=1e-3)


class Test選択の取り合い:
    """重なりの順（フキダシ → マーク → セリフ）どおりに拾えること。

    描く順と拾う順がずれると、**見えているものを掴めなくなる**。

    順が合っていても、**上のものが描いていない場所まで拾う**と同じことが
    起きる。セリフを字の範囲だけで判定しているのはそのため（→ 下）。
    """

    def test_フキダシより先にマークを拾う(self, window_with_panel):
        state = window_with_panel.state
        state.add_balloon(Rect(300.0, 300.0, 300.0, 200.0))
        sticker = state.add_sticker(STICKER_EXCLAIM, 450.0, 400.0)
        state.select(None)

        click(window_with_panel.view, 450.0, 400.0)
        assert state.selected_id == sticker.id

    def test_マークより先にセリフを拾う(self, window_with_panel):
        state = window_with_panel.state
        state.add_sticker(STICKER_EXCLAIM, 450.0, 400.0)
        text = state.add_text(Rect(400.0, 350.0, 100.0, 100.0), "あ")
        state.select(None)

        click(window_with_panel.view, 450.0, 400.0)
        assert state.selected_id == text.id

    def test_セリフの字から外れた所ではマークを拾う(self, window_with_panel):
        """**フキダシの中に置いたマークが掴めること**（2026-08-04 の不具合）。

        セリフ枠はフキダシの内側をほぼ埋める大きさなので、枠全体を拾って
        いた頃は**字が1つも無い場所でもセリフに取られて**、マークを
        選べなかった。字の並んでいる範囲だけ拾う形に直してある
        （→ `layout.text_ink_bands`、要件定義 6.5）。

        押す点はフキダシの内側でもあるので、マークがフキダシより先に
        拾われることも同時に見ている。
        """
        state = window_with_panel.state
        state.add_balloon(Rect(300.0, 200.0, 333.0, 400.0))
        state.add_text(Rect(350.0, 240.0, 230.0, 330.0), "あ")
        sticker = state.add_sticker(STICKER_EXCLAIM, 400.0, 300.0)
        state.select(None)

        click(window_with_panel.view, 400.0, 300.0)
        assert state.selected_id == sticker.id

    def test_マークの外ではコマを拾う(self, window_with_sticker):
        state = window_with_sticker.state
        state.select(None)
        click(window_with_sticker.view, 150.0, 150.0)
        assert state.selected_panel is state.page.panels[0]


class Test動かす:
    def test_ドラッグで動く(self, window_with_sticker):
        state = window_with_sticker.state
        before = state.selected_sticker.rect
        drag(window_with_sticker.view, *CENTER, CENTER[0] + 100.0, CENTER[1] + 60.0)
        after = state.selected_sticker.rect
        assert (after.x - before.x, after.y - before.y) != (0.0, 0.0)

    def test_コマを動かすと付いてくる(self, window_with_sticker):
        state = window_with_sticker.state
        sticker_id = state.selected_sticker.id
        before = state.selected_sticker.rect
        with state.edit("コマの移動") as project:
            project.pages[0].move_panel(state.page.panels[0].id, 40.0, 25.0)
        after = state.page.find(sticker_id).rect
        assert after == before.translated(40.0, 25.0)

    def test_履歴に積まれる(self, window_with_sticker):
        state = window_with_sticker.state
        before = state.selected_sticker.rect
        drag(window_with_sticker.view, *CENTER, CENTER[0] + 100.0, CENTER[1] + 60.0)
        state.undo()
        assert state.page.floating[0].rect == before


class Test大きさを変える:
    def test_Shiftなしでも縦横比を保つ(self, window_with_sticker):
        """記号そのものなので、比を崩すと形が壊れるだけ（→ 6.14）。"""

        class _NoModifier:
            @staticmethod
            def modifiers():
                from PySide6.QtCore import Qt

                return Qt.KeyboardModifier.NoModifier

        view = window_with_sticker.view
        sticker = window_with_sticker.state.selected_sticker
        w, h = sticker.src_px
        assert view._locked_aspect(_NoModifier()) == pytest.approx(w / h)

    def test_コマの中の画像はShiftのときだけ(self, window_with_panel, fixture_dir):
        """マークで常時にしたことが、画像側の扱いを変えていないこと。"""

        class _NoModifier:
            @staticmethod
            def modifiers():
                from PySide6.QtCore import Qt

                return Qt.KeyboardModifier.NoModifier

        state = window_with_panel.state
        panel = state.page.panels[0]
        data = (fixture_dir / "rgb_opaque.png").read_bytes()
        image = state.place_image(panel.id, data)
        state.select(image.id)
        assert window_with_panel.view._locked_aspect(_NoModifier()) == 0.0


class Test消す:
    def test_Deleteで消える(self, window_with_sticker):
        window_with_sticker.delete_selected()
        assert stickers(window_with_sticker) == []

    def test_項目名に種類が出る(self, window_with_sticker):
        """押す前にカーソルの下で気づけるようにする（→ 6.12）。"""
        target = window_with_sticker.delete_target()
        assert target is not None
        assert target[0] == STICKER_KIND_LABELS[STICKER_EXCLAIM]

    def test_コマは巻き添えにならない(self, window_with_sticker):
        window_with_sticker.delete_selected()
        assert len(window_with_sticker.state.page.panels) == 1

    def test_Undoで戻る(self, window_with_sticker):
        window_with_sticker.delete_selected()
        window_with_sticker.state.undo()
        assert len(stickers(window_with_sticker)) == 1


class Test紐づけの付け外し:
    def test_外せる(self, window_with_sticker):
        window_with_sticker.toggle_sticker_attachment()
        assert stickers(window_with_sticker)[0].attached_panel_id is None

    def test_付け直せる(self, window_with_sticker):
        panel_id = window_with_sticker.state.page.panels[0].id
        window_with_sticker.toggle_sticker_attachment()
        window_with_sticker.toggle_sticker_attachment()
        assert stickers(window_with_sticker)[0].attached_panel_id == panel_id


def sticker_menu_items(window):
    """マークのメニューの項目（区切り線を除く）。

    名前ではなく中身で探す（tests/test_ui_balloon.py と同じ理由）。
    目印には「紐づけ」の項目を使う。道具の項目は道具メニューにも並ぶので
    目印にならない。
    """
    marker = window.sticker_actions[0]
    for action in window.menuBar().actions():
        menu = action.menu()
        if menu is not None and marker in menu.actions():
            return [a for a in menu.actions() if not a.isSeparator()]
    raise AssertionError("マークのメニューが見つかりません")


class Testメニュー:
    def test_何も選んでいなくても作れる項目がある(self, window):
        usable = [a for a in sticker_menu_items(window) if a.isEnabled()]
        assert usable, "マークのメニューが全部グレーになっている"

    def test_追加の項目が先頭にある(self, window):
        items = sticker_menu_items(window)
        assert items[0] is window._tool_actions[TOOL_STICKER_EXCLAIM]
        assert items[1] is window._tool_actions[TOOL_STICKER_EXCLAIM_QUESTION]

    def test_追加の項目から道具に切り替わる(self, window):
        items = sticker_menu_items(window)
        items[0].trigger()
        assert window.state.tool == TOOL_STICKER_EXCLAIM
        items[1].trigger()
        assert window.state.tool == TOOL_STICKER_EXCLAIM_QUESTION

    def test_選択中だけ使える項目もある(self, window_with_sticker):
        assert all(a.isEnabled() for a in sticker_menu_items(window_with_sticker))

    def test_選択を外すと編集の項目は戻る(self, window_with_sticker):
        window_with_sticker.state.select(None)
        assert not window_with_sticker.sticker_attach_action.isEnabled()

    def test_ショートカットはMだけ(self, window):
        """1文字キーを全部の道具に割り当てない（→ 6.14）。"""
        exclaim = window._tool_actions[TOOL_STICKER_EXCLAIM]
        question = window._tool_actions[TOOL_STICKER_EXCLAIM_QUESTION]
        assert exclaim.shortcut().toString() == "M"
        assert question.shortcut().isEmpty()


class Test右クリックのメニュー:
    def test_コマの上でマークを置ける(self, window_with_panel):
        window_with_panel.state.select(window_with_panel.state.page.panels[0].id)
        menu = window_with_panel._context_menu(*CENTER)
        labels = [a.text() for a in menu.actions()]
        assert f"ここに{STICKER_KIND_LABELS[STICKER_EXCLAIM]}を追加" in labels
        menu.deleteLater()

    def test_何も無いところでも置ける(self, window):
        window.state.select(None)
        menu = window._context_menu(60.0, 60.0)
        labels = [a.text() for a in menu.actions()]
        assert f"ここに{STICKER_KIND_LABELS[STICKER_EXCLAIM]}を追加" in labels
        menu.deleteLater()

    def test_その場で置ける_道具は変わらない(self, window_with_panel):
        window_with_panel.view.add_sticker_at(*CENTER, STICKER_EXCLAIM)
        assert len(stickers(window_with_panel)) == 1
        assert window_with_panel.state.tool == TOOL_SELECT

    def test_マークを選んでいるときは編集の項目が出る(self, window_with_sticker):
        menu = window_with_sticker._context_menu(*CENTER)
        assert window_with_sticker.sticker_attach_action in menu.actions()
        menu.deleteLater()


class Test状態表示:
    def test_種類と大きさが出る(self, window_with_sticker):
        hint = window_with_sticker._hint()
        assert STICKER_KIND_LABELS[STICKER_EXCLAIM] in hint
        assert "コマに紐づけ" in hint

    def test_知らない種類はマークと呼ぶ(self, window_with_sticker):
        """素材が増えたあとの作品を古いアプリで開く場面（→ 5章）。"""
        sticker = window_with_sticker.state.selected_sticker
        sticker.kind = "まだ無い種類"
        assert "マークを選択中" in window_with_sticker._hint()
