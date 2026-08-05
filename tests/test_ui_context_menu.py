"""右クリックのメニューの検証（画面なし）。

**`exec()` は呼べない。** 応答待ちで止まるので、テストでは
「右クリックを受け取って選び直すところ」と「メニューを組むところ」を
別々に通す。本番はこの2つを `_show_context_menu` がつないでいる。

ここで押さえたいのは主に2つ。押した場所のものが選び直されること
（選ばずに出すと、直前に選んでいた別のものへ操作が効く）と、項目が
メニューバーの QAction の**写し**であること（作り直すと、有効・無効の
切り替えを2か所で書くことになる）。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QMenu

from manga_layout import Rect
from manga_layout.model import BalloonObject, Panel, TextObject
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.menus import BALLOON_STYLE_MENU_LABEL
from manga_layout.ui.state import BALLOON_STYLE_LABELS, TOOL_SELECT
from manga_layout.ui.window import (
    BALLOON_PLACE_HERE_NAME,
    PLACE_HERE_PREFIX,
    REPLACE_IMAGE_LABEL,
    SPLIT_HERE_PREFIX,
    place_here_label,
    split_here_label,
)

# 呼び名は1箇所（`BALLOON_STYLE_LABELS`）から取る。書き写すと、
# 改名したときにテストだけが古い名前を通してしまう
JAGGED = BALLOON_STYLE_LABELS["jagged"]


def place_first(name: str) -> str:
    """並びの1つめ（前置きが付く形）の名前。"""
    return place_here_label(name, first=True)


def place_rest(name: str) -> str:
    """2つめ以降（前置きを空白に落とした形）の名前。"""
    return place_here_label(name, first=False)


def split_first(name: str) -> str:
    """割る項目の1つめ（前置きが付く形）の名前。"""
    return split_here_label(name, first=True)


def split_rest(name: str) -> str:
    """割る項目の2つめ以降（前置きを空白に落とした形）の名前。"""
    return split_here_label(name, first=False)

# 座標は px（要件定義 3章）。既定の吹き出し・セリフが中に収まる大きさ
PANEL = Rect(120.0, 120.0, 720.0, 540.0)
# 用紙（A4 相当 1240×1754）の中で、上のコマから充分に離れた場所
EMPTY = (1000.0, 1400.0)

# セリフ1つぶん。中身は改行なしの3文字なので、既定の縦書きでは1列になる
TEXT_RECT = Rect(250.0, 250.0, 200.0, 150.0)
TEXT_CONTENT = "セリフ"
# その列の上の点。**枠の中ならどこでもよいわけではない。**
# セリフは字の並んでいる帯だけを拾うので（→ `layout.text_ink_bands`）、
# 枠の左寄り（x=300）では字から外れ、下のフキダシやコマが選ばれる。
# 1列のときの列の中心は枠の横中央にくる（→ `vertical.layout`）
ON_TEXT = (350.0, 300.0)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    # メニューを出す側は外す。つないだままだと exec() で止まる
    win.view.context_menu_requested.disconnect(win._show_context_menu)
    yield win
    win.view.finish_text_edit(commit=False)
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def window_with_panel(window):
    with window.state.edit("コマの追加") as project:
        project.add_panel(project.pages[0], PANEL)
    window.state.select(None)
    return window


def right_click(window: MainWindow, x: float, y: float) -> QMenu:
    """シーンの (x, y) を右クリックし、出るはずのメニューを返す。

    本番と同じ順（選び直してから組む）で通す。選択が先に済んでいないと、
    メニューの中身も項目の有効・無効も1つ前の選択で決まってしまう。
    """
    view = window.view
    pos = view.mapFromScene(QPointF(x, y))
    view.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, pos, view.viewport().mapToGlobal(pos)
        )
    )
    return window._context_menu(x, y)


def labels(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def find(menu: QMenu, label: str):
    """名前で項目を探す。無ければテストを落とす。"""
    for action in menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"{label} がメニューに無い: {labels(menu)}")


def _open_folded(menu: QMenu, label: str):
    """畳んだメニューを名前で開き、**見出しの QAction ごと**返す。

    `QAction.menu()` はその QMenu を呼び出し側の QAction に引き取らせる
    （→ `menus.items_to_copy`）。**見出しを使い捨てにすると、返って
    きた QMenu はその場で消える。** `find(menu, label).menu()` と1行で書いて
    実際に `Internal C++ object already deleted` になった（2026-08-05）。

    そのため見出しを呼び出し側へ一緒に返し、中身を読み終わるまで
    生かしておく。**畳んだメニューを覗くのはこの関数を通してだけにする。**
    """
    action = find(menu, label)
    sub = action.menu()
    assert sub is not None, f"{label} は畳んだメニューではない: {labels(menu)}"
    return action, sub


def folded_labels(menu: QMenu, label: str) -> list[str]:
    """畳んだメニューの中身（項目名）。"""
    action, sub = _open_folded(menu, label)
    found = labels(sub)
    del action  # 読み終わるまで生かしておくためだけに持っていた
    return found


def trigger_folded(menu: QMenu, label: str, item: str) -> None:
    """畳んだメニューの中の項目を押す。"""
    action, sub = _open_folded(menu, label)
    find(sub, item).trigger()
    del action


def only(page, kind):
    found = [f for f in page.floating if isinstance(f, kind)]
    assert len(found) == 1, f"{kind.__name__} が {len(found)} 個ある"
    return found[0]


class TestSelection:
    """押した場所のものを選び直す（左クリックと同じ判定を使う）。"""

    def test_コマの上ならそのコマが選ばれる(self, window_with_panel):
        right_click(window_with_panel, 400.0, 300.0)
        panel = window_with_panel.state.selected_panel
        assert panel is not None
        assert panel.shape.as_rect() == PANEL

    def test_何も無いところなら選択が外れる(self, window_with_panel):
        right_click(window_with_panel, 400.0, 300.0)
        assert window_with_panel.state.selected_id is not None

        right_click(window_with_panel, *EMPTY)
        assert window_with_panel.state.selected_id is None

    def test_別のものを選んでいても押した場所へ移る(self, window_with_panel):
        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        balloon_id = state.selected_id

        # 吹き出しを選んだまま、外れたコマの上を右クリックする
        right_click(window_with_panel, 700.0, 600.0)

        assert state.selected_id != balloon_id
        assert state.selected_panel is not None

    def test_セリフは吹き出しより先に拾う(self, window_with_panel):
        """左クリックと同じ順序。ここが食い違うと、右クリックで選ばれた
        ものと、そのまま引いたときに動くものが別になる。
        """
        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 400.0, 300.0))
        state.add_text(TEXT_RECT, TEXT_CONTENT)
        state.select(None)

        right_click(window_with_panel, *ON_TEXT)

        assert state.selected_text is not None

    def test_入力中に右クリックすると確定する(self, window_with_panel):
        """左クリックと同じ扱い（「画面を触ったら確定」）。

        Qt 標準の入力欄メニューに任せてはいけない。メニューへ焦点が移った
        時点で focusOut が走り、メニューを開いたまま入力欄がシーンから外れる。
        """
        state = window_with_panel.state
        text = state.add_text(Rect(250.0, 250.0, 200.0, 150.0))
        window_with_panel.view.begin_text_edit(text.id)
        assert window_with_panel.view.is_editing_text

        right_click(window_with_panel, *EMPTY)

        assert not window_with_panel.view.is_editing_text

    def test_画面移動の最中は出さない(self, window_with_panel):
        """掴んだままメニューが割り込むと、離しても移動が終わらない。"""
        view = window_with_panel.view
        view._space_held = True
        seen = []
        view.context_menu_requested.connect(lambda *args: seen.append(args))

        pos = view.mapFromScene(QPointF(400.0, 300.0))
        view.contextMenuEvent(
            QContextMenuEvent(
                QContextMenuEvent.Reason.Mouse, pos, view.viewport().mapToGlobal(pos)
            )
        )
        view._space_held = False

        assert seen == []


class TestContents:
    def test_何も無いところの品書き(self, window_with_panel):
        menu = right_click(window_with_panel, *EMPTY)
        found = labels(menu)
        assert place_first("コマ") in found
        assert "ページ全面にコマを作る" in found
        # 選んでいるものが無いので、選択に効く項目は出さない
        assert not any(label.endswith("を削除") for label in found)

    def test_コマの品書き(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        found = labels(menu)
        # 3つとも必ず一緒に出るので、前置きが付くのは常に「横」
        assert split_first("横に割る") in found
        for name in ("縦に割る", "斜めに割る"):
            assert split_rest(name) in found
        # コマを選ぶと「コマ」が外れるので、フキダシが並びの1つめになる。
        # 種類は畳んだ下にある（→ 要件定義 10.1）
        assert place_first(BALLOON_PLACE_HERE_NAME) in found
        styles = folded_labels(menu, place_first(BALLOON_PLACE_HERE_NAME))
        assert BALLOON_STYLE_LABELS["ellipse"] in styles
        assert "貼り付け" in found
        # 何が消えるかを名前に出す（→ MainWindow.delete_target）
        assert "コマを削除" in found
        # コマの上に重ねてコマを作る道は用意しない（割るほうが素直）
        assert not any("コマ" in label and "追加" in label for label in found)

    def test_フキダシの品書き(self, window_with_panel):
        window_with_panel.state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, 300.0, 300.0)
        found = labels(menu)
        # 種類を変える項目は畳んだ下にある（→ 要件定義 10.1）
        assert f"{JAGGED}にする" in folded_labels(menu, BALLOON_STYLE_MENU_LABEL)
        assert "しっぽを消す" in found
        # フキダシを選ぶとマークが1つめになるので、セリフは前置きが落ちる
        assert place_rest("セリフ") in found
        assert "フキダシを削除" in found

    def test_セリフの品書き(self, window_with_panel):
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, *ON_TEXT)
        found = labels(menu)
        assert "文字を入力..." in found
        assert "縦書き" in found
        assert "フォントを選ぶ..." in found
        assert "セリフを削除" in found

    def test_道具に持ち替える項目は出さない(self, window_with_panel):
        """「ここに〜」と役割が重なるため外している（→ `_copy_actions`）。"""
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, *ON_TEXT)

        for action in window_with_panel._tool_actions.values():
            assert action not in menu.actions()

    def test_ここにの前置きは1つめだけに付く(self, window_with_panel):
        """並んだ2つめからは前置きを空白に落とす（→ `place_here_label`）。

        読む字数を減らすための省略なので、**前置きが2つ以上出ていたら
        効いていない。** 場面によって1つめの種類が変わるため、名前を
        決め打ちせず「前置きの付いた項目がちょうど1つ」で見る。
        """
        menu = right_click(window_with_panel, *EMPTY)
        found = labels(menu)

        prefixed = [label for label in found if label.startswith(PLACE_HERE_PREFIX)]
        assert len(prefixed) == 1, prefixed
        # 残りは同じ幅の空白で始まる（名前の頭が1つめと縦に揃う）
        pad = "　" * len(PLACE_HERE_PREFIX)
        assert len([label for label in found if label.startswith(pad)]) > 1

    def test_ここでの前置きも1つめだけに付く(self, window_with_panel):
        """割る項目にも同じ省略を当てている（→ `split_here_label`）。"""
        found = labels(right_click(window_with_panel, 400.0, 300.0))

        prefixed = [label for label in found if label.startswith(SPLIT_HERE_PREFIX)]
        assert len(prefixed) == 1, prefixed

    def test_ここにとここでは同じ列に揃う(self, window_with_panel):
        """コマを選ぶと2つの組が同じメニューに並ぶ。

        **頭の位置は前置きの字数から出している。** 決め打ちの空白にすると、
        前置きの長さが違ったときに片方だけずれる。
        """
        found = labels(right_click(window_with_panel, 400.0, 300.0))

        indented = [label for label in found if label.startswith("　")]
        # 割る2つ（縦・斜め）と、置く3つ（マーク2・セリフ）。フキダシは
        # 畳んで1行になり、それが置く組の1つめなので前置きが付く
        assert len(indented) == 5
        # 空白の数が1種類なら、どちらの組も同じ列から名前が始まる
        assert len({len(label) - len(label.lstrip("　")) for label in indented}) == 1

    def test_区切り線が先頭や連続で並ばない(self, window_with_panel):
        """道具の項目を外した跡に区切り線だけが残らないこと。"""
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        actions = right_click(window_with_panel, *ON_TEXT).actions()

        assert not actions[0].isSeparator()
        assert not actions[-1].isSeparator()
        # 隣り合う組を作るためのずらしで、長さが1つ違うのは意図どおり
        assert not any(
            a.isSeparator() and b.isSeparator()
            for a, b in zip(actions, actions[1:], strict=False)
        )


class TestStatusTips:
    """カーソルを乗せた項目の説明（→ `_show_tips_in_status_bar`）。

    名前から落とした「ここに」「を追加」はここで補っている。
    **繋ぎ忘れても例外は出ず、黙って何も出なくなるだけ**なので、
    画面を見ないと気づけない。テストで押さえておく。
    """

    def hover(self, window, menu, label):
        """本番のカーソル移動と同じ経路（`hovered`）で項目を光らせる。"""
        menu.show()
        window.statusBar().clearMessage()
        menu.setActiveAction(find(menu, label))
        return window.statusBar().currentMessage()

    def test_省略した前置きが説明に出る(self, window_with_panel):
        menu = right_click(window_with_panel, *EMPTY)

        message = self.hover(window_with_panel, menu, place_rest("セリフ"))

        # 名前は短いまま、説明のほうが完全な文になる
        assert message == place_first("セリフ")
        menu.hide()

    def test_1つめも同じ説明を持つ(self, window_with_panel):
        """名前が既に完全な文でも、説明が空だと項目ごとに挙動が変わる。"""
        menu = right_click(window_with_panel, *EMPTY)

        message = self.hover(window_with_panel, menu, place_first("コマ"))

        assert message == place_first("コマ")
        menu.hide()

    def test_閉じると説明は消える(self, window_with_panel):
        menu = right_click(window_with_panel, *EMPTY)
        self.hover(window_with_panel, menu, place_rest("セリフ"))

        menu.hide()

        assert window_with_panel.statusBar().currentMessage() == ""


class TestSharedActions:
    """項目はメニューバーと同じ実体。作り直さない（→ `_context_menu`）。"""

    def test_同じ項目を並べている(self, window_with_panel):
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, *ON_TEXT)

        assert window_with_panel.text_menu.bold_action in menu.actions()
        assert window_with_panel.edit_menu.delete_action in menu.actions()

    def test_文言の書き換えが右クリック側にも出る(self, window_with_panel):
        """「しっぽを消す／出す」は `_refresh` が1か所で切り替えている。"""
        state = window_with_panel.state
        balloon = state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)

        assert "しっぽを消す" in labels(right_click(window_with_panel, 300.0, 300.0))

        state.set_tail_enabled(balloon.id, False)
        assert "しっぽを出す" in labels(right_click(window_with_panel, 300.0, 300.0))

    def test_選んでいないものへの項目は無効のまま(self, window_with_panel):
        """画像を選んでいるときの「コマにフィット」は使えるが、
        コマを選んでいるだけの状態では出さない。
        """
        menu = right_click(window_with_panel, 400.0, 300.0)
        assert window_with_panel.fit_action not in menu.actions()

    def test_メニューバーを辿られても組める(self, window_with_panel):
        """`QAction.menu()` を呼ばれた後でも右クリックのメニューが出ること。

        PySide6 では `QAction.menu()` がその QMenu を呼び出し側の QAction に
        引き取らせる。QAction を使い捨てにすると、片付いた時点で QMenu の
        Python 側の参照が無効になる（→ `menus.items_to_copy`）。
        以前は写す元として QMenu そのものを持っていたため、これで
        `RuntimeError: Internal C++ object already deleted` になった。
        """
        import gc

        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)

        # 使い捨ての QAction からメニューを取る。テストや外部の道具が
        # メニューバーを辿るときの典型的な書き方
        for action in window_with_panel.menuBar().actions():
            action.menu()
        gc.collect()

        menu = right_click(window_with_panel, 300.0, 300.0)
        assert "しっぽを消す" in labels(menu)
        # 畳んだ側も同じように組み直せること。**ここが本題。**
        # 種類の項目は畳んだ QMenu に並ぶので、辿られて実体が消えていると
        # ここで空になる（→ `MainWindow._build_balloon_style_menu`）
        assert f"{JAGGED}にする" in folded_labels(menu, BALLOON_STYLE_MENU_LABEL)


class TestActions:
    """メニューを押した結果。押した場所が効いていることを確かめる。"""

    def test_ここにコマを追加(self, window):
        menu = right_click(window, *EMPTY)
        find(menu, place_first("コマ")).trigger()

        panels = window.state.page.panels
        assert len(panels) == 1
        rect = panels[0].shape.as_rect()
        # 押した場所が中心。用紙からはみ出さない範囲で寄る
        assert rect.contains(*EMPTY)
        assert window.state.selected_id == panels[0].id

    def test_ここに吹き出しを追加(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        trigger_folded(menu, place_first(BALLOON_PLACE_HERE_NAME), JAGGED)

        balloon = only(window_with_panel.state.page, BalloonObject)
        assert balloon.style == "jagged"
        assert balloon.rect.contains(400.0, 300.0)
        # 重なっているコマに紐づく（要件定義 6.4）
        assert balloon.attached_panel_id is not None

    def test_ここにセリフを追加(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, place_rest("セリフ")).trigger()

        text = only(window_with_panel.state.page, TextObject)
        assert text.rect.contains(400.0, 300.0)
        # 作っただけでは空の枠が残るので、そのまま打てる状態にする
        assert window_with_panel.view.is_editing_text

    def test_ここで横に割る(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, split_first("横に割る")).trigger()

        panels = window_with_panel.state.page.panels
        assert len(panels) == 2
        # 押した高さで切れている。溝（gutter 35px）はその位置を挟んで空く
        upper, lower = sorted(panels, key=lambda p: p.shape.bounds().y)
        assert upper.shape.bounds().bottom == pytest.approx(300.0 - 17.5)
        assert lower.shape.bounds().y == pytest.approx(300.0 + 17.5)

    def test_割っても道具は持ち替えない(self, window_with_panel):
        """メニューバー側は道具の切り替えだが、右クリックはその場で割る。
        持ち替えると、次に画面を押したときに割るつもりのない場所が割れる。
        """
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, split_rest("縦に割る")).trigger()

        assert window_with_panel.state.tool == TOOL_SELECT

    def test_削除は選んでいるものに効く(self, window_with_panel):
        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)

        menu = right_click(window_with_panel, 300.0, 300.0)
        find(menu, "フキダシを削除").trigger()

        assert [f for f in state.page.floating if isinstance(f, BalloonObject)] == []
        # コマは残る
        assert len(state.page.panels) == 1
        assert isinstance(state.page.panels[0], Panel)


class TestDeleteTarget:
    """何が消えるかを名前に出す。

    画像を消すつもりでコマを消した、という取り違えが実際に起きた。
    コマの中の画像を選ぶにはダブルクリックで一段踏み込む必要があり
    （要件定義 6.3）、右クリックしただけではコマが選ばれるため。
    """

    def test_選んでいるものが名前に出る(self, window_with_panel):
        state = window_with_panel.state
        action = window_with_panel.edit_menu.delete_action

        right_click(window_with_panel, 400.0, 300.0)
        assert action.text() == "コマを削除"

        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)
        right_click(window_with_panel, 300.0, 300.0)
        assert action.text() == "フキダシを削除"

        state.add_text(TEXT_RECT, TEXT_CONTENT)
        state.select(None)
        right_click(window_with_panel, *ON_TEXT)
        assert action.text() == "セリフを削除"

    def test_何も選んでいなければ無効(self, window_with_panel):
        right_click(window_with_panel, *EMPTY)
        assert window_with_panel.edit_menu.delete_action.text() == "削除"
        assert not window_with_panel.edit_menu.delete_action.isEnabled()

    def test_名前と実際に消えるものが揃っている(self, window_with_panel):
        """`delete_target` が両方の出所。別々に持つと食い違わせられる。"""
        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)
        right_click(window_with_panel, 300.0, 300.0)

        assert window_with_panel.edit_menu.delete_action.text() == "フキダシを削除"
        window_with_panel.delete_selected()

        assert [f for f in state.page.floating if isinstance(f, BalloonObject)] == []
        assert len(state.page.panels) == 1


@pytest.fixture
def window_with_image(window_with_panel, png_bytes):
    """コマに画像を1枚入れ、コマを選んだ状態に戻したところ。"""
    panel = window_with_panel.state.page.panels[0]
    window_with_panel.state.place_image(panel.id, png_bytes)
    # 置いた直後は画像が選ばれている。コマを選んだ状態に戻す
    window_with_panel.state.select(panel.id)
    return window_with_panel


class TestDeleteImageHere:
    """コマを選んだままでも、カーソルの下の画像を消せる。

    この項目が無いと、メニューに並ぶのは「コマを削除」だけになり、
    画像を消すつもりでコマが消える（→ `_add_image_here`）。
    """

    def test_画像の上でだけ出る(self, window_with_image):
        panel = window_with_image.state.page.panels[0]
        image = panel.children[0]
        cx, cy = image.rect.center

        assert "この画像を削除" in labels(right_click(window_with_image, cx, cy))

        # コマの中でも画像から外れていれば出さない
        corner = (PANEL.x + 5.0, PANEL.y + 5.0)
        assert not image.rect.contains(*corner)
        assert "この画像を削除" not in labels(right_click(window_with_image, *corner))

    def test_画像だけ消えてコマは残る(self, window_with_image):
        state = window_with_image.state
        panel = state.page.panels[0]
        cx, cy = panel.children[0].rect.center

        menu = right_click(window_with_image, cx, cy)
        find(menu, "この画像を削除").trigger()

        assert len(state.page.panels) == 1
        assert state.page.panels[0].children == []

    def test_コマを選んだままでも消せる(self, window_with_image):
        """踏み込まずに消せることがこの項目の目的。"""
        state = window_with_image.state
        panel = state.page.panels[0]
        cx, cy = panel.children[0].rect.center

        menu = right_click(window_with_image, cx, cy)
        # 選ばれているのはコマのまま（「コマを削除」も並んでいる）
        assert state.selected_panel is not None
        assert "コマを削除" in labels(menu)

        find(menu, "この画像を削除").trigger()
        assert state.page.panels[0].children == []


class TestReplaceImageHere:
    """画像の差し替えと、絵があるままの読み込み（→ `_add_image_here`）。

    ファイル選択の窓は開けない（応答待ちで止まる）ので、ここで見るのは
    「どの項目が並ぶか」まで。実際に入れ替わることは
    `test_ui.py::TestImageReplace` が見ている。
    """

    def test_画像の上でだけ出る(self, window_with_image):
        panel = window_with_image.state.page.panels[0]
        image = panel.children[0]
        cx, cy = image.rect.center

        assert REPLACE_IMAGE_LABEL in labels(right_click(window_with_image, cx, cy))

        corner = (PANEL.x + 5.0, PANEL.y + 5.0)
        assert not image.rect.contains(*corner)
        found = labels(right_click(window_with_image, *corner))
        assert REPLACE_IMAGE_LABEL not in found
        # 絵から外れていても、コマへ読み込む道は開いている
        assert "ファイルから読み込み..." in found

    def test_絵があっても読み込みは出る(self, window_with_image):
        """背景の上にキャラを重ねる使い方があるので、塞がない。"""
        image = window_with_image.state.page.panels[0].children[0]
        cx, cy = image.rect.center

        found = labels(right_click(window_with_image, cx, cy))

        assert "ファイルから読み込み..." in found
        assert REPLACE_IMAGE_LABEL in found

    def test_画像を選んでいるときも両方出る(self, window_with_image):
        """踏み込んで画像を選んだ状態。Esc でコマへ戻る手数を挟ませない。"""
        state = window_with_image.state
        image = state.page.panels[0].children[0]
        state.select(image.id)
        cx, cy = image.rect.center

        found = labels(right_click(window_with_image, cx, cy))

        assert state.selected_image is not None
        assert REPLACE_IMAGE_LABEL in found
        assert "ファイルから読み込み..." in found
        # 消す側は「画像を削除」1つで足りる（選んでいるものに効く）
        assert "画像を削除" in found
