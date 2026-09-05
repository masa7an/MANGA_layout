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
from manga_layout.model import SLANT_RIGHT, BalloonObject, SlantPair
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


class TestReplacementRemoval:
    """差し替えでコマを消すときの作法（→ `Page.remove_panel`）。

    **一覧を直に書き替えない。** `remove_panel` は、紐づいたフキダシ・セリフの
    紐づけを外し、斜めの組を解いてから消す。直に書き替えるとそこを飛ばす。

    **今は振る舞いに出ない。** 消すのは直前に自分が置いたコマだけで、紐づけも
    斜めの組も持たないため。**だから重要度は低い。それでも作法を1つにする**
    ——ここだけ違う形が残っていると、次に条件が変わったとき静かに壊れる
    （2026-09-05 に揃えた）。ここで見るのは「**差し替えがページの他の部分を
    荒らさないこと**」。

    差し替えたコマ自身に紐づけや斜めの組を持たせた材料は作れない。付けるには
    編集が要り、編集を挟むとまとめ扱いが切れて差し替えが起きなくなるため。
    """

    def test_差し替えても他のコマの斜めの組は残る(self, qapp):
        editor = editor_with(*band(0.06, 0.28))
        with editor.edit("準備") as project:
            page = project.pages[0]
            page.slant_pairs.append(
                SlantPair(left_id=page.panels[1].id, right_id=page.panels[0].id,
                          ratio=0.5, angle=10.0, direction=SLANT_RIGHT)
            )
        before = [p.members() for p in editor.page.slant_pairs]
        assert before
        editor.suggest_next_panel()
        editor.suggest_next_panel()          # 差し替え
        assert [p.members() for p in editor.page.slant_pairs] == before

    def test_差し替えても他のコマへの紐づけは残る(self, qapp):
        editor = editor_with(*band(0.06, 0.28))
        with editor.edit("準備") as project:
            page = project.pages[0]
            page.floating.append(
                BalloonObject(id="balloon_x",
                              rect=Rect(200.0, 200.0, 100.0, 80.0),
                              attached_panel_id=page.panels[0].id)
            )
        target = editor.page.panels[0].id
        editor.suggest_next_panel()
        editor.suggest_next_panel()          # 差し替え
        balloon = next(f for f in editor.page.floating if f.id == "balloon_x")
        assert balloon.attached_panel_id == target

    def test_何回押しても数は増えない(self, editor):
        editor.suggest_next_panel()
        count = len(editor.page.panels)
        for _ in range(4):
            editor.suggest_next_panel()
        assert len(editor.page.panels) == count


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

    def test_コマを選んだだけでも足す側に戻る(self, editor):
        # 選ぶのは編集ではないが、**まとめ扱いは打ち切られる**（`break_merge`）。
        # 差し替えたつもりで新しい1手が積まれると、Undo 1回では戻らなくなる
        editor.suggest_next_panel()
        count = len(editor.page.panels)
        editor.select(editor.page.panels[0].id)
        editor.suggest_next_panel()
        assert len(editor.page.panels) > count

    def test_置いた数だけ元に戻せる(self, editor):
        # **1回の提案＝履歴1手。** 差し替えと履歴のまとめが同じ鍵で決まるので、
        # 「差し替えたのに履歴は別の1手」が起きない
        before = shapes(editor.page)
        editor.suggest_next_panel()
        after_first = shapes(editor.page)
        editor.select(editor.page.panels[0].id)
        editor.suggest_next_panel()

        editor.undo()
        assert shapes(editor.page) == after_first
        editor.undo()
        assert shapes(editor.page) == before

    def test_ページを移ったら足す側に戻る(self, editor):
        editor.suggest_next_panel()
        count = len(editor.page.panels)
        editor.add_page()
        editor.set_page_index(0)
        editor.suggest_next_panel()
        assert len(editor.page.panels) > count

    def test_元に戻したあとは足す側から始める(self, editor):
        # 元に戻すとまとめ鍵も消える。**置いた覚えの無いコマを差し替えない**
        before = shapes(editor.page)
        editor.suggest_next_panel()
        editor.undo()
        assert shapes(editor.page) == before
        editor.suggest_next_panel()
        assert len(editor.page.panels) > len(before)


class TestShortcut:
    def test_名前にキーを入れて道具と体裁を揃える(self, qapp):
        # 道具は「コマ追加 (P)」の形。**こちらだけ名前にキーが無いと、
        # 「メニューを探す」窓で「次のコマを提案　［N］」と体裁が割れる**
        from manga_layout.ui.menu_search import collect_menu_entries, item_text

        window = MainWindow()
        try:
            rows = {
                e.text: item_text(e).splitlines()[0]
                for e in collect_menu_entries(window)
            }
            assert rows["次のコマを提案 (N)"].endswith("次のコマを提案 (N)")
            assert "［" not in rows["次のコマを提案 (N)"]
        finally:
            window.close()

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
    def refuse(self, editor, *points):
        editor.page.panels[0].shape = Polygon(
            tuple((float(x), float(y)) for x, y in points)
        )
        seen = []
        editor.message.connect(seen.append)
        assert not editor.suggest_next_panel()
        return seen[-1]

    def test_斜めのコマがあるページでは断る(self, editor):
        text = self.refuse(editor, (100, 100), (400, 140), (400, 500), (100, 460))
        assert "四角" in text

    def test_断る相手は斜めだけではない(self, editor):
        """**文言に「斜め」と書かない。** 判定を `split_panel` と同じ道具に
        1本化した結果、断る対象が広がった（2026-09-05）。面積の無いコマで
        「斜めのコマがあります」と出すと、**探しても見つからない。**
        """
        text = self.refuse(editor, (100, 100), (400, 100), (400, 100), (100, 100))
        assert "四角" in text
        assert "斜め" not in text

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
