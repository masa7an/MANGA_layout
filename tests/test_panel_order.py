"""コマ同士の重なり順（手前へ／奥へ）の検証。

コマは `z` の小さいほうから描かれ（`render.draw_page`）、クリックは大きい
ほうから当たる（`layout.panel_at`）。**この2つが同じ順を見ていること**が
この機能の土台なので、z を直接見るだけでなく `panel_at` でも確かめる。
順だけ変えて当たり判定が付いてこないと、「手前に出したのに掴めない」
という一番たちの悪い壊れ方になる。

押さえたいのは3点。

- **奥のコマを手前に出せること。** 重ねて置いたコマの上下を入れ替える
- **斜めの組は2枚一緒に動くこと**（→ 6.17 のロックと同じ扱い）。片割れ
  だけ動かすと、1枚を割って作った2枚の間に別のコマが挟まる
- **端に居るときは何もしないこと。** 押しても変化の無い操作で Undo の
  一手を使わせない
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.layout import panel_at
from manga_layout.slant import split_panel_slant
from manga_layout.ui import EditorState, MainWindow

# 重ねて置く2枚。右下と左上で半分ほど重なる
LOWER = Rect(100.0, 100.0, 300.0, 200.0)
UPPER = Rect(250.0, 200.0, 300.0, 200.0)
# 2枚が重なっている場所
OVERLAP = (300.0, 250.0)


@pytest.fixture
def state(qapp):
    """奥に LOWER、手前に UPPER を置いた状態。"""
    editor = EditorState()
    with editor.edit("準備") as project:
        page = project.pages[0]
        project.add_panel(page, LOWER)
        project.add_panel(page, UPPER)
    return editor


def panel_ids(editor) -> tuple[str, str]:
    """置いた順（＝重なり順）の id。奥, 手前。"""
    lower, upper = editor.page.panels
    return lower.id, upper.id


def hit(editor):
    """2枚が重なっている場所を押したときに当たるコマの id。"""
    found = panel_at(editor.page, *OVERLAP)
    return None if found is None else found.id


class Test手前へ:
    def test_奥のコマを手前に出せる(self, state):
        lower, upper = panel_ids(state)
        assert hit(state) == upper  # 出す前は後から置いたほうが当たる

        assert state.raise_panel(lower) is True
        assert state.page.panel(lower).z > state.page.panel(upper).z
        assert hit(state) == lower

    def test_既に手前なら何もしない(self, state):
        _, upper = panel_ids(state)
        assert state.can_raise_panel(upper) is False
        assert state.raise_panel(upper) is False

    def test_コマが1枚だけなら押せない(self, qapp):
        editor = EditorState()
        with editor.edit("準備") as project:
            panel = project.add_panel(project.pages[0], LOWER)
        assert editor.can_raise_panel(panel.id) is False
        assert editor.can_lower_panel(panel.id) is False


class Test奥へ:
    def test_手前のコマを奥に送れる(self, state):
        """奥のコマが完全に隠れていても、上を選んで送れば入れ替わる。"""
        lower, upper = panel_ids(state)
        assert state.lower_panel(upper) is True
        assert state.page.panel(upper).z < state.page.panel(lower).z
        assert hit(state) == lower

    def test_既に奥なら何もしない(self, state):
        lower, _ = panel_ids(state)
        assert state.can_lower_panel(lower) is False
        assert state.lower_panel(lower) is False


class Test斜めの組:
    @pytest.fixture
    def split(self, state):
        """奥のコマを斜めに割り、手前に1枚残した状態。"""
        lower, upper = panel_ids(state)
        with state.edit("斜めに割る") as project:
            page = project.pages[0]
            left, right = split_panel_slant(
                project, page, lower, position=LOWER.x + LOWER.w / 2
            )
        return left.id, right.id, upper

    def test_2枚一緒に手前へ出る(self, state, split):
        left, right, upper = split
        assert state.raise_panel(left) is True

        page = state.page
        assert page.panel(left).z > page.panel(upper).z
        assert page.panel(right).z > page.panel(upper).z

    def test_2枚一緒に奥へ送られる(self, state, split):
        left, right, upper = split
        assert state.lower_panel(upper) is True

        page = state.page
        assert page.panel(upper).z < page.panel(left).z
        assert page.panel(upper).z < page.panel(right).z

    def test_組の中の順は崩れない(self, state, split):
        """左右の前後関係は動かす前後で変わらない。"""
        left, right, _ = split
        before = state.page.panel(left).z < state.page.panel(right).z
        state.raise_panel(left)
        assert (state.page.panel(left).z < state.page.panel(right).z) is before


class Test元に戻す:
    def test_1手で戻る(self, state):
        lower, upper = panel_ids(state)
        state.raise_panel(lower)
        state.undo()
        assert hit(state) == upper

    def test_保存して開き直しても順が残る(self, state, tmp_path):
        lower, _ = panel_ids(state)
        state.raise_panel(lower)
        state.save(tmp_path / "作品")

        reopened = EditorState()
        reopened.load(tmp_path / "作品")
        assert hit(reopened) == lower


class Testメニュー:
    """押せる／押せないの出し分け（→ 6.12）。"""

    @pytest.fixture
    def window(self, state):
        win = MainWindow(state)
        yield win
        win.state.history.mark_saved()
        win.close()

    def test_奥のコマを選ぶと手前へだけ押せる(self, window):
        lower, _ = panel_ids(window.state)
        window.state.select(lower)
        assert window.panel_menu.raise_action.isEnabled() is True
        assert window.panel_menu.lower_action.isEnabled() is False

    def test_手前のコマを選ぶと奥へだけ押せる(self, window):
        _, upper = panel_ids(window.state)
        window.state.select(upper)
        assert window.panel_menu.raise_action.isEnabled() is False
        assert window.panel_menu.lower_action.isEnabled() is True

    def test_コマを選んでいなければどちらも押せない(self, window):
        window.state.select(None)
        assert window.panel_menu.raise_action.isEnabled() is False
        assert window.panel_menu.lower_action.isEnabled() is False

    def test_押したあとは反対側だけ押せる(self, window):
        lower, _ = panel_ids(window.state)
        window.state.select(lower)
        window.raise_panel()
        assert window.panel_menu.raise_action.isEnabled() is False
        assert window.panel_menu.lower_action.isEnabled() is True

    def test_2項目めは頭が揃う(self, window):
        """「コマを手前へ／　　　奥へ」。空白の数は「コマを」の字数と同じ。

        揃っていないと、2項目めが上の行の続きに見えず、何が奥へ行くのか
        分からない項目になる。字数を変えたら空白も直す必要があるので、
        目で見て気づけない崩れをここで止める。
        """
        raise_text = window.panel_menu.raise_action.text()
        lower_text = window.panel_menu.lower_action.text()
        assert raise_text == "コマを手前へ"
        assert lower_text.lstrip("　") == "奥へ"
        assert len(lower_text) - len("奥へ") == len("コマを")

    def test_右クリックには押せる側だけ出る(self, window):
        lower, _ = panel_ids(window.state)
        window.state.select(lower)
        actions = window.context_menu.build(*OVERLAP).actions()
        assert window.panel_menu.raise_action in actions
        assert window.panel_menu.lower_action not in actions
