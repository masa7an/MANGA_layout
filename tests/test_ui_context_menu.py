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
from manga_layout.ui.state import BALLOON_STYLE_LABELS, TOOL_SELECT

# 呼び名は1箇所（`BALLOON_STYLE_LABELS`）から取る。書き写すと、
# 改名したときにテストだけが古い名前を通してしまう
JAGGED = BALLOON_STYLE_LABELS["jagged"]

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
        assert "ここにコマを追加" in found
        assert "ページ全面にコマを作る" in found
        # 選んでいるものが無いので、選択に効く項目は出さない
        assert not any(label.endswith("を削除") for label in found)

    def test_コマの品書き(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        found = labels(menu)
        for label in ("ここで横に割る", "ここで縦に割る", "ここで斜めに割る"):
            assert label in found
        assert f"ここに{BALLOON_STYLE_LABELS['ellipse']}を追加" in found
        assert "貼り付け" in found
        # 何が消えるかを名前に出す（→ MainWindow.delete_target）
        assert "コマを削除" in found
        # コマの上に重ねてコマを作る道は用意しない（割るほうが素直）
        assert "ここにコマを追加" not in found

    def test_フキダシの品書き(self, window_with_panel):
        window_with_panel.state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, 300.0, 300.0)
        found = labels(menu)
        assert f"{JAGGED}にする" in found
        assert "しっぽを消す" in found
        assert "ここにセリフを追加" in found
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

    def test_区切り線が先頭や連続で並ばない(self, window_with_panel):
        """道具の項目を外した跡に区切り線だけが残らないこと。"""
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        actions = right_click(window_with_panel, *ON_TEXT).actions()

        assert not actions[0].isSeparator()
        assert not actions[-1].isSeparator()
        assert not any(
            a.isSeparator() and b.isSeparator() for a, b in zip(actions, actions[1:])
        )


class TestSharedActions:
    """項目はメニューバーと同じ実体。作り直さない（→ `_context_menu`）。"""

    def test_同じ項目を並べている(self, window_with_panel):
        window_with_panel.state.add_text(TEXT_RECT, TEXT_CONTENT)
        window_with_panel.state.select(None)

        menu = right_click(window_with_panel, *ON_TEXT)

        assert window_with_panel.bold_action in menu.actions()
        assert window_with_panel.delete_action in menu.actions()

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
        Python 側の参照が無効になる（→ `MainWindow._items_to_copy`）。
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

        found = labels(right_click(window_with_panel, 300.0, 300.0))
        assert "しっぽを消す" in found
        assert f"{JAGGED}にする" in found


class TestActions:
    """メニューを押した結果。押した場所が効いていることを確かめる。"""

    def test_ここにコマを追加(self, window):
        menu = right_click(window, *EMPTY)
        find(menu, "ここにコマを追加").trigger()

        panels = window.state.page.panels
        assert len(panels) == 1
        rect = panels[0].shape.as_rect()
        # 押した場所が中心。用紙からはみ出さない範囲で寄る
        assert rect.contains(*EMPTY)
        assert window.state.selected_id == panels[0].id

    def test_ここに吹き出しを追加(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, f"ここに{JAGGED}を追加").trigger()

        balloon = only(window_with_panel.state.page, BalloonObject)
        assert balloon.style == "jagged"
        assert balloon.rect.contains(400.0, 300.0)
        # 重なっているコマに紐づく（要件定義 6.4）
        assert balloon.attached_panel_id is not None

    def test_ここにセリフを追加(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, "ここにセリフを追加").trigger()

        text = only(window_with_panel.state.page, TextObject)
        assert text.rect.contains(400.0, 300.0)
        # 作っただけでは空の枠が残るので、そのまま打てる状態にする
        assert window_with_panel.view.is_editing_text

    def test_ここで横に割る(self, window_with_panel):
        menu = right_click(window_with_panel, 400.0, 300.0)
        find(menu, "ここで横に割る").trigger()

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
        find(menu, "ここで縦に割る").trigger()

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
        action = window_with_panel.delete_action

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
        assert window_with_panel.delete_action.text() == "削除"
        assert not window_with_panel.delete_action.isEnabled()

    def test_名前と実際に消えるものが揃っている(self, window_with_panel):
        """`delete_target` が両方の出所。別々に持つと食い違わせられる。"""
        state = window_with_panel.state
        state.add_balloon(Rect(200.0, 200.0, 300.0, 200.0))
        state.select(None)
        right_click(window_with_panel, 300.0, 300.0)

        assert window_with_panel.delete_action.text() == "フキダシを削除"
        window_with_panel.delete_selected()

        assert [f for f in state.page.floating if isinstance(f, BalloonObject)] == []
        assert len(state.page.panels) == 1


class TestDeleteImageHere:
    """コマを選んだままでも、カーソルの下の画像を消せる。

    この項目が無いと、メニューに並ぶのは「コマを削除」だけになり、
    画像を消すつもりでコマが消える（→ `_add_delete_image_here`）。
    """

    @pytest.fixture
    def window_with_image(self, window_with_panel, png_bytes):
        panel = window_with_panel.state.page.panels[0]
        window_with_panel.state.place_image(panel.id, png_bytes)
        # 置いた直後は画像が選ばれている。コマを選んだ状態に戻す
        window_with_panel.state.select(panel.id)
        return window_with_panel

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
