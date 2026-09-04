"""【次のコマを提案】を押したときの振る舞いの検証（→ 要件定義 10.5）。

提案の中身は `test_joseki.py` と `test_next_panel.py` で見ている。ここで押さえるのは
**押したときに何が起きるか**の4点。

- **押すとコマが1枚以上増える。** 提案は下見ではなく、実際に置く
- **もう一度押すと、増えずに差し替わる。** 案どうしは重なるので、並べて置いてはいけない
- **何回押しても Undo 1回で消える**（履歴は `merge_key` で1手にまとめる）
- **間に別の操作を挟んだら、その提案は確定したものとして扱う。** 次は差し替えず、足す

出せないときは**押せなくするのではなく、理由をお知らせに出す。**
"""

from __future__ import annotations

import pytest

from manga_layout import Polygon, Rect, new_project
from manga_layout.ui import EditorState, MainWindow

PAGE_W, PAGE_H = 1240.0, 1754.0


def band(top, height):
    """左右2コマの段。"""
    return (
        Rect(0.52 * PAGE_W, top * PAGE_H, 0.42 * PAGE_W, height * PAGE_H),
        Rect(0.06 * PAGE_W, top * PAGE_H, 0.42 * PAGE_W, height * PAGE_H),
    )


def editor_with(*rects):
    editor = EditorState()
    with editor.edit("準備") as project:
        page = project.pages[0]
        for rect in rects:
            project.add_panel(page, rect)
    return editor


def shapes(page):
    return [tuple(panel.shape.points) for panel in page.panels]


@pytest.fixture
def editor(qapp):
    """1段目だけ描かれたページ。下がまるごと空いている"""
    return editor_with(*band(0.06, 0.28))


class TestFirstPress:
    def test_押すとコマが増える(self, editor):
        before = len(editor.page.panels)
        assert editor.suggest_next_panel()
        assert len(editor.page.panels) > before

    def test_お知らせに何番目の案かを出す(self, editor):
        seen = []
        editor.message.connect(seen.append)
        editor.suggest_next_panel()
        assert seen and seen[-1].startswith("提案 1/")

    def test_1手として履歴に積む(self, editor):
        depth = editor.history.depth
        editor.suggest_next_panel()
        assert editor.history.depth == depth + 1
        assert editor.history.undo_label == "次のコマを提案"


class TestRepeatedPress:
    def test_2回目は増えずに差し替わる(self, editor):
        editor.suggest_next_panel()
        count = len(editor.page.panels)
        first = shapes(editor.page)
        editor.suggest_next_panel()
        assert len(editor.page.panels) == count      # 増えない
        assert shapes(editor.page) != first          # 中身は変わる

    def test_何回押しても元に戻すのは1回(self, editor):
        before = shapes(editor.page)
        depth = editor.history.depth
        for _ in range(5):
            editor.suggest_next_panel()
        assert editor.history.depth == depth + 1
        editor.undo()
        assert shapes(editor.page) == before

    def test_案を一周したら先頭へ戻る(self, editor):
        seen = []
        editor.message.connect(seen.append)
        editor.suggest_next_panel()
        total = int(seen[-1].split("/")[1].split(":")[0])
        for _ in range(total):        # 一周ぶん押す
            editor.suggest_next_panel()
        assert seen[-1].startswith("提案 1/")

    def test_間に別の操作を挟んだら足す側に戻る(self, editor):
        editor.suggest_next_panel()
        count = len(editor.page.panels)
        # 別の1手を挟む。**直前の提案は確定したもの**として扱う
        editor.lock_all_panels()
        editor.suggest_next_panel()
        assert len(editor.page.panels) > count


class TestShortcut:
    def test_キーはN(self, qapp):
        # **押すたびに次の案へ切り替える操作**なので、1打で繰り返せるキーにしてある。
        # `Alt+P` は「ページ(&P)」メニューが使っていて割り当てられない
        window = MainWindow()
        try:
            keys = [
                s.toString()
                for s in window.panel_menu.suggest_action.shortcuts()
            ]
            assert keys == ["N"]
        finally:
            window.close()


class TestAltHint:
    """メニューバーの「＋ Alt キー」（→ `window._add_alt_hint`）。"""

    def test_ヘルプの右に灰色で出す(self, qapp):
        from manga_layout.ui.window import ALT_HINT

        window = MainWindow()
        try:
            texts = [a.text() for a in window.menuBar().actions()]
            assert texts[-1] == ALT_HINT          # いちばん右（ヘルプの次）
            assert not window._alt_hint_action.isEnabled()   # 押せない＝灰色
        finally:
            window.close()

    def test_メニューを探す窓には出さない(self, qapp):
        from manga_layout.ui.menu_search import collect_menu_entries
        from manga_layout.ui.window import ALT_HINT

        window = MainWindow()
        try:
            texts = [e.text for e in collect_menu_entries(window)]
            assert ALT_HINT not in texts
        finally:
            window.close()


class TestRefusal:
    def test_斜めのコマがあるページでは断る(self, editor):
        editor.page.panels[0].shape = Polygon(
            ((100.0, 100.0), (400.0, 140.0), (400.0, 500.0), (100.0, 460.0))
        )
        seen = []
        editor.message.connect(seen.append)
        assert not editor.suggest_next_panel()
        assert "斜め" in seen[-1]

    def test_埋まったページでは断る(self, qapp):
        editor = editor_with(
            *band(0.06, 0.28), *band(0.38, 0.22), *band(0.64, 0.30)
        )
        seen = []
        editor.message.connect(seen.append)
        assert not editor.suggest_next_panel()
        assert "見つかりません" in seen[-1]

    def test_断ったときは履歴を使わない(self, qapp):
        editor = editor_with(
            *band(0.06, 0.28), *band(0.38, 0.22), *band(0.64, 0.30)
        )
        depth = editor.history.depth
        editor.suggest_next_panel()
        assert editor.history.depth == depth      # 押しても何も起きない操作で1手を使わない


class TestBlankPage:
    def test_空白ページにも出せる(self, qapp):
        editor = EditorState(new_project())
        assert not editor.page.panels
        assert editor.suggest_next_panel()
        assert editor.page.panels

    def test_置いたコマは基本枠にぴったり付く(self, qapp):
        # **枠からはみ出さない。** 目安線の外に出たコマは、置き直す手間になる
        editor = EditorState(new_project())
        editor.suggest_next_panel()
        margin = editor.settings.margin
        box = editor.page.panels[0].bounds()
        assert box.y == pytest.approx(margin)
        assert box.right == pytest.approx(editor.page.size.w - margin)
        assert box.x >= margin
        assert box.bottom <= editor.page.size.h - margin + 0.5
