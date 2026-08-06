"""抜けチェック（点検 → 要件定義 10.1）の、画面まわりの検証。

検査そのものは `test_check.py`。ここで押さえたいのは4つ。

1. **作品を1文字も変えないこと。** 押しただけで未保存になったり、Undo の
   履歴が伸びたりしない
2. **自分で貼った付箋を消さないこと。** 付箋はページに1つだけなので、
   同じ場所へ紫を入れると黄・桃・青が上書きで失われる（→ 6.18）
3. **押し直すと付け直すこと。** 直したのに紫が残ると嘘になる
4. **書き出しの隣から届くこと。** 押せない機能は無いのと同じ
"""

from __future__ import annotations

import pytest

from manga_layout import Rect
from manga_layout.model import PageNote
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.pages import (
    CHECK_COLOR,
    CHECK_ROLE,
    NOTE_COLOR_ROLE,
    NOTE_COLOR_SWATCH,
)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def dirty_pages(window):
    """2ページ。1ページ目だけに直し忘れ（空のコマ）を置く。"""
    with window.state.edit("下ごしらえ") as project:
        project.add_panel(project.pages[0], Rect(100.0, 100.0, 400.0, 400.0))
        project.add_page()
    window.state.history.mark_saved()
    return window


def page_ids(window) -> list[str]:
    return [p.id for p in window.state.project.pages]


def result_text(window) -> str:
    return window._check_dialog._body.toPlainText()


class Test動線:
    """**書き出しの隣**。書き出す直前に通る場所に置かないと点検を忘れる。"""

    def test_ファイルメニューの書き出しの手前にある(self, window):
        for action in window.menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            labels = [a.text() for a in menu.actions() if not a.isSeparator()]
            if "画像で書き出し..." not in labels:
                continue
            assert "抜けチェック..." in labels
            assert labels.index("抜けチェック...") == labels.index("画像で書き出し...") - 1
            return
        raise AssertionError("ファイルメニューが見つかりません")

    def test_ショートカットは足さない(self, window):
        """7章。キーは限られた資源なので、押す頻度に見合うものだけに割く。"""
        for action in window.menuBar().actions():
            menu = action.menu()
            if menu is None:
                continue
            for item in menu.actions():
                if item.text() == "抜けチェック...":
                    assert item.shortcut().isEmpty()
                    return
        raise AssertionError("抜けチェックが見つかりません")


class Test紫の印:
    def test_見つかったページに付く(self, dirty_pages):
        window = dirty_pages
        window.run_check()
        assert window.state.check_marks == {page_ids(window)[0]}

    def test_一覧の項目に伝わる(self, dirty_pages):
        window = dirty_pages
        window.run_check()
        pages = window.pages_panel
        assert pages.item(0).data(CHECK_ROLE) is True
        assert pages.item(1).data(CHECK_ROLE) is False

    def test_直して押し直すと消える(self, dirty_pages):
        """押すたびに数え直す。前の結果を足し込まない（→ 10.1）。"""
        window = dirty_pages
        window.run_check()
        assert window.state.check_marks

        with window.state.edit("空のコマを消す") as project:
            project.pages[0].panels.clear()
        window.run_check()

        assert window.state.check_marks == set()
        assert window.pages_panel.item(0).data(CHECK_ROLE) is False

    def test_編集しただけでは消えない(self, dirty_pages):
        """直したかどうかは点検し直して確かめる（→ 10.1）。

        編集のたびに消すと、印を付けたそばから消えていく。
        """
        window = dirty_pages
        window.run_check()
        with window.state.edit("ページの追加") as project:
            project.add_page()
        assert window.state.check_marks == {page_ids(window)[0]}

    def test_別の作品を開くと消える(self, dirty_pages, tmp_path):
        window = dirty_pages
        window.run_check()
        assert window.state.check_marks

        window.state.save(tmp_path / "作品")
        window.state.load(tmp_path / "作品")

        assert window.state.check_marks == set()


class Test付箋を壊さない:
    """付箋はページに1つだけ（→ 6.18）。紫を同じ場所へ入れてはいけない。"""

    def test_貼ってある付箋が残る(self, dirty_pages):
        window = dirty_pages
        with window.state.edit("付箋") as project:
            project.pages[0].note = PageNote(color="yellow", text="ここから")
        window.state.history.mark_saved()

        window.run_check()

        note = window.state.project.pages[0].note
        assert note is not None
        assert note.color == "yellow"
        assert note.text == "ここから"

    def test_一覧では別の場所に持つ(self, dirty_pages):
        window = dirty_pages
        with window.state.edit("付箋") as project:
            project.pages[0].note = PageNote(color="yellow")
        window.run_check()

        item = window.pages_panel.item(0)
        assert item.data(NOTE_COLOR_ROLE) == "yellow"
        assert item.data(CHECK_ROLE) is True

    def test_絵の上で重ならない(self, dirty_pages):
        """付箋は右上、点検の印は左上。**実際に描いて確かめる。**

        データを別に持っていても、同じ場所へ描けば下の色は見えなくなる。
        """
        window = dirty_pages
        with window.state.edit("付箋") as project:
            project.pages[0].note = PageNote(color="yellow")
        window.run_check()

        image = window.pages_panel.grab().toImage()
        spots = {}
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color == CHECK_COLOR:
                    spots.setdefault("check", x)
                elif color == NOTE_COLOR_SWATCH["yellow"]:
                    spots.setdefault("note", x)

        assert "check" in spots, "紫の印が描かれていない"
        assert "note" in spots, "付箋が紫に隠れている"
        assert spots["check"] < spots["note"]


class Test作品を変えない:
    def test_未保存にならない(self, dirty_pages):
        window = dirty_pages
        assert not window.state.is_dirty
        window.run_check()
        assert not window.state.is_dirty

    def test_元に戻すの履歴が伸びない(self, dirty_pages):
        window = dirty_pages
        before = window.state.history.undo_label
        window.run_check()
        assert window.state.history.undo_label == before

    def test_保存形式が変わらない(self, dirty_pages):
        window = dirty_pages
        before = window.state.project.to_dict()
        window.run_check()
        assert window.state.project.to_dict() == before


class Test結果の窓:
    def test_押すまで作らない(self, window):
        assert window._check_dialog is None

    def test_明細が読める(self, dirty_pages):
        window = dirty_pages
        window.run_check()
        assert "絵の入っていないコマ" in result_text(window)
        assert "1ページ" in result_text(window)

    def test_書き換えられない(self, dirty_pages):
        """読み返すためのもので、ここを直しても作品は変わらない。"""
        window = dirty_pages
        window.run_check()
        assert window._check_dialog._body.isReadOnly()

    def test_押し直しても窓は1つ(self, dirty_pages):
        window = dirty_pages
        window.run_check()
        first = window._check_dialog
        window.run_check()
        assert window._check_dialog is first

    def test_押し直すと中身が入れ替わる(self, dirty_pages):
        window = dirty_pages
        window.run_check()
        assert "絵の入っていないコマ" in result_text(window)

        with window.state.edit("空のコマを消す") as project:
            project.pages[0].panels.clear()
        window.run_check()

        assert "見つかりませんでした" in result_text(window)

    def test_窓を閉じても印は残る(self, dirty_pages):
        """窓を閉じたあとも一覧の印で残りを追える、というのが印の役目。"""
        window = dirty_pages
        window.run_check()
        window._check_dialog.close()
        assert window.state.check_marks == {page_ids(window)[0]}


class Test件数の知らせ:
    def test_状態表示に件数が出る(self, dirty_pages):
        window = dirty_pages
        seen: list[str] = []
        window.state.message.connect(seen.append)
        window.run_check()
        assert seen and "1 件" in seen[-1]

    def test_見つからなければそう言う(self, window):
        seen: list[str] = []
        window.state.message.connect(seen.append)
        window.run_check()
        assert seen and "見つかりませんでした" in seen[-1]
