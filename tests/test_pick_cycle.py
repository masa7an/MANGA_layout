"""同じ場所にあるものを、ダブルクリックで順に選び直す（要件定義 6.25）。

クリックは必ず手前のものに当たるので（`layout.panel_at`）、**大きなコマの
下に完全に隠れたコマは、それまで選ぶ手立てが無かった**。ここで確かめるのは
「隠れたものに届くこと」と、「届くようにした結果、今までの動きを壊して
いないこと」の2つ。

押さえたいのは4点。

- **1回目のダブルクリックは今までどおり画像を選ぶ**（→ 6.3）。巡る順で
  コマを画像より先に置いてあるのは、この1点のためだけと言ってよい
- **押下を挟んでも巡回が戻らないこと。** ダブルクリックの手前には必ず
  普通の押下が入り、そこで手前のコマに選び直される。押される前の選択を
  控えていないと、2つめと3つめを往復して隠れたコマに永久に届かない
- **完全に隠れたコマに届くこと。** この機能の目的そのもの
- **巡っても同じものに戻る場所では何もしないこと**（画像の無いコマ1枚など）
"""

from __future__ import annotations

import pytest
from mouse import double_click, press

from manga_layout import Rect
from manga_layout.layout import next_in_stack, pick_stack
from manga_layout.ui import EditorState, MainWindow

# 先に置く小さなコマ。あとから置く大きなコマに**すっぽり覆われる**
SMALL = Rect(150.0, 150.0, 120.0, 100.0)
# あとから置く大きなコマ。z が大きいので手前になる。4:3 にしてあるのは、
# 同じ比の画像を置いたときにコマを埋め、下の点まで届かせるため
BIG = Rect(100.0, 100.0, 400.0, 300.0)
# 2枚が重なっている場所（小さいコマの中心）
POINT = (210.0, 200.0)


def click_pair(view, x: float, y: float) -> None:
    """利用者から見た「ダブルクリック1回」。押下とダブルクリックの組。

    **Qt はダブルクリックの前に必ず押下を送る。** テストからダブルクリックの
    事象だけを直接呼ぶと、その押下による選び直しが再現されず、巡回が戻る
    不具合を素通りさせてしまう（実際にこの形でしか出ない）。
    """
    press(view, x, y)
    double_click(view, x, y)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def buried(window, fixture_dir):
    """大きなコマの下に小さなコマが完全に隠れ、大きなコマに絵が1枚ある状態。

    「大きさ的に埋もれているものを拾い上げる」場面そのもの。選択は
    外しておく（何も選んでいない所から始める）。
    """
    state = window.state
    with state.edit("準備") as project:
        page = project.pages[0]
        project.add_panel(page, SMALL)
        project.add_panel(page, BIG)
    small, big = state.page.panels
    state.place_image(big.id, (fixture_dir / "rgb_opaque.png").read_bytes())
    state.select(None)
    return window, small.id, big.id, big.children[0].id


class Test並び:
    """`pick_stack` が返す順。**z の順そのままではない**（→ 6.25）。"""

    def test_コマの次にその中の画像が来る(self, buried):
        window, small, big, image = buried

        assert pick_stack(window.state.page, *POINT) == [big, image, small]

    def test_隠れたコマも入る(self, buried):
        """見えていなくても並びに入る。選択枠は最前面に描かれるので、
        選んでしまえば必ず見える。"""
        window, small, _big, _image = buried

        assert small in pick_stack(window.state.page, *POINT)

    def test_画像の外ならコマだけ(self, buried):
        """絵はコマを埋めているので、コマの外に出れば何にも当たらない。"""
        window, _small, _big, _image = buried

        assert pick_stack(window.state.page, 10.0, 10.0) == []

    def test_どこにも当たらなければ空(self, window):
        assert pick_stack(window.state.page, *POINT) == []


class Test次を選ぶ:
    """`next_in_stack`。並びの上をどう歩くか。"""

    def test_次へ進む(self):
        assert next_in_stack(["a", "b", "c"], "a") == "b"

    def test_末尾なら先頭へ戻る(self):
        assert next_in_stack(["a", "b", "c"], "c") == "a"

    def test_並びに無いものは先頭の次(self):
        """フキダシの上でダブルクリックした場合。今までどおり下の画像が
        選ばれるように、先頭（コマ）ではなくその次を返す。"""
        assert next_in_stack(["a", "b", "c"], "z") == "b"

    def test_何も無ければ選べない(self):
        assert next_in_stack([], "a") is None


class Test巡回:
    """画面から見た動き。**押下とダブルクリックを組で送る**（→ `click_pair`）。"""

    def test_1回目は画像が選ばれる(self, buried):
        """6.3 のまま。ここが変わると、今までの操作を覚え直させることになる。"""
        window, _small, _big, image = buried

        click_pair(window.view, *POINT)

        assert window.state.selected_id == image
        assert window.state.selected_image is not None

    def test_2回目で隠れたコマに届く(self, buried):
        """この機能の目的。押下を挟んでも巡回が戻らないことの検証でもある。"""
        window, small, _big, _image = buried

        click_pair(window.view, *POINT)
        click_pair(window.view, *POINT)

        assert window.state.selected_id == small

    def test_3回目で手前のコマへ戻る(self, buried):
        window, _small, big, _image = buried

        for _ in range(3):
            click_pair(window.view, *POINT)

        assert window.state.selected_id == big

    def test_巡り続けても取りこぼさない(self, buried):
        """2周ぶん回して、3つすべてを2回ずつ通ることを確かめる。"""
        window, small, big, image = buried

        seen = []
        for _ in range(6):
            click_pair(window.view, *POINT)
            seen.append(window.state.selected_id)

        assert seen == [image, small, big, image, small, big]

    def test_重なり順は変わらない(self, buried):
        """選択が移るだけで、作品は変わらない（→ 6.25 の決め）。"""
        window, _small, _big, _image = buried
        before = [(p.id, p.z) for p in window.state.page.panels]
        label = window.state.history.undo_label

        for _ in range(3):
            click_pair(window.view, *POINT)

        assert [(p.id, p.z) for p in window.state.page.panels] == before
        assert window.state.history.undo_label == label

    def test_コマ1枚で画像が無ければ何も起きない(self, window):
        """巡っても同じものに戻る場所。選択が動いたように見せない。"""
        with window.state.edit("準備") as project:
            project.add_panel(project.pages[0], BIG)
        only = window.state.page.panels[0].id
        window.state.select(None)

        click_pair(window.view, *POINT)

        assert window.state.selected_id == only  # 押下で選ばれたまま
