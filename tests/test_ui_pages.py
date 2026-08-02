"""ページ管理（要件定義 6.1）の検証。

ここで押さえたいのは3つ。

1. **並び順の出所が `Project.pages` ひとつであること。** 一覧側にも並びを
   持たせると、Undo で戻したのに一覧だけ動いたまま、という食い違いが作れる
2. **表示中のページを見失わないこと。** 削除・並べ替えで番号はずれる。
   番号のまま留まると、操作した直後に別のページが出る
3. **消す前に必ず確認すること。** ページ1枚には積み上げた作業がまるごと乗る
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QMessageBox

from manga_layout import PAGE_SIZES, Rect, Size
from manga_layout.layout import outside_page
from manga_layout.storage import load_project
from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.pages import PageSizeDialog, render_thumbnail, reorder_target


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def three_pages(window):
    """3ページ。1ページ目にだけ全面コマを置いて、見分けが付くようにする。"""
    window.add_full_page_panel()
    window.add_page()
    window.add_page()
    window.state.set_page_index(0)
    return window


def page_ids(window) -> list[str]:
    return [p.id for p in window.state.project.pages]


class TestAddPage:
    """「追加」は必ず末尾。どこを見ていても行き先が変わらない。"""

    def test_末尾に入る(self, three_pages):
        window = three_pages
        window.state.set_page_index(0)  # 先頭を見ている
        before = page_ids(window)

        window.add_page()

        assert page_ids(window)[:3] == before
        assert window.state.page_count == 4

    def test_追加したページへ移る(self, three_pages):
        three_pages.state.set_page_index(0)
        three_pages.add_page()
        assert three_pages.state.page_index == 3
        assert three_pages.state.page.panels == []

    def test_選択が外れる(self, window):
        window.add_full_page_panel()
        window.add_page()
        assert window.state.selected_id is None

    def test_元に戻せる(self, window):
        window.add_page()
        window.state.undo()
        assert window.state.page_count == 1
        assert window.state.page_index == 0

    def test_既定の大きさで入る(self, window):
        window.state.set_page_size(PAGE_SIZES["B5"])
        window.add_page()
        assert window.state.page.size == PAGE_SIZES["B5"]

    def test_どこにできたか知らせる(self, three_pages):
        seen = []
        three_pages.state.message.connect(seen.append)
        three_pages.add_page()
        assert any("末尾に 4 ページ目" in m for m in seen)


class TestInsertPage:
    """「挿入」は表示中のページの**前**。位置を狙うときだけこちらを使う。

    以前は「追加」1つで表示中のページの次に入れていたが、一覧のカーソルが
    どこにあるか常に意識しているわけではないので、思っていない場所に
    ページができることがあった（要件定義 6.1）。
    """

    def test_表示中のページの前に入る(self, three_pages):
        window = three_pages
        window.state.set_page_index(1)
        first, second, third = page_ids(window)

        window.insert_page()

        ids = page_ids(window)
        assert ids[0] == first
        assert ids[2] == second and ids[3] == third
        assert window.state.page_count == 4

    def test_差し込んだページへ移る(self, three_pages):
        """番号は変わらないが、中身は新しい空のページになる。"""
        window = three_pages
        window.state.set_page_index(1)
        before = window.state.page.id

        window.insert_page()

        assert window.state.page_index == 1
        assert window.state.page.id != before
        assert window.state.page.panels == []

    def test_それまでのページは1つ後ろへ下がる(self, three_pages):
        window = three_pages
        window.state.set_page_index(1)
        pushed = window.state.page.id

        window.insert_page()

        assert page_ids(window)[2] == pushed

    def test_先頭を見ていれば先頭に入る(self, three_pages):
        window = three_pages
        window.state.set_page_index(0)
        head = window.state.page.id

        window.insert_page()

        assert window.state.page_index == 0
        assert page_ids(window)[1] == head

    def test_末尾を見ていても末尾には足されない(self, three_pages):
        """「追加」との違いがはっきり出る場面。"""
        window = three_pages
        window.state.set_page_index(2)
        last = window.state.page.id

        window.insert_page()

        assert window.state.page_index == 2
        assert page_ids(window)[3] == last

    def test_元に戻せる(self, three_pages):
        window = three_pages
        before = page_ids(window)
        window.state.set_page_index(0)
        window.insert_page()
        window.state.undo()
        assert page_ids(window) == before

    def test_履歴の名前が追加と分かれている(self, three_pages):
        """Undo の表示で、どちらの操作を戻すのか分かるようにする。"""
        three_pages.insert_page()
        assert three_pages.state.history.undo_label == "ページの挿入"
        three_pages.add_page()
        assert three_pages.state.history.undo_label == "ページの追加"


class TestDeletePage:
    def test_最後の1ページは消せない(self, window):
        seen = []
        window.state.message.connect(seen.append)

        assert window.state.delete_page() is False

        assert window.state.page_count == 1
        assert any("最後の1ページ" in m for m in seen)

    def test_繰り上がったページを表示する(self, three_pages):
        window = three_pages
        window.state.set_page_index(1)
        third = page_ids(window)[2]

        window.state.delete_page()

        assert window.state.page_count == 2
        assert window.state.page_index == 1
        assert window.state.page.id == third

    def test_末尾を消すと1つ前へ戻る(self, three_pages):
        window = three_pages
        window.state.set_page_index(2)

        window.state.delete_page()

        assert window.state.page_index == 1
        assert window.state.page_count == 2

    def test_元に戻せる(self, three_pages):
        window = three_pages
        before = page_ids(window)
        window.state.set_page_index(1)
        window.state.delete_page()

        window.state.undo()

        assert page_ids(window) == before

    def test_中身ごと消える(self, three_pages):
        """1ページ目にはコマがある。消したあと Undo で戻ること。"""
        window = three_pages
        window.state.delete_page(0)
        assert window.state.project.pages[0].panels == []
        window.state.undo()
        assert len(window.state.project.pages[0].panels) == 1


class TestDeleteConfirmation:
    """確認なしで消えないこと。ここが黙って通ると被害が大きい。"""

    def _answer(self, monkeypatch, button):
        asked = []

        class 決まった答えを返す確認:
            StandardButton = QMessageBox.StandardButton

            @classmethod
            def question(cls, *args, **kwargs):
                asked.append(args)
                return button

        monkeypatch.setattr("manga_layout.ui.window.QMessageBox", 決まった答えを返す確認)
        return asked

    def test_はいなら消える(self, three_pages, monkeypatch):
        asked = self._answer(monkeypatch, QMessageBox.StandardButton.Yes)
        three_pages.delete_page()
        assert len(asked) == 1
        assert three_pages.state.page_count == 2

    def test_いいえなら消えない(self, three_pages, monkeypatch):
        asked = self._answer(monkeypatch, QMessageBox.StandardButton.No)
        three_pages.delete_page()
        assert len(asked) == 1
        assert three_pages.state.page_count == 3

    def test_最後の1ページなら確認もしない(self, window, monkeypatch):
        asked = self._answer(monkeypatch, QMessageBox.StandardButton.Yes)
        window.delete_page()
        assert asked == []
        assert window.state.page_count == 1


class TestMovePage:
    def test_後ろへ動かせる(self, three_pages):
        window = three_pages
        first, second, third = page_ids(window)

        assert window.state.move_page(0, 1) is True

        assert page_ids(window) == [second, first, third]

    def test_前へ動かせる(self, three_pages):
        window = three_pages
        first, second, third = page_ids(window)

        window.state.move_page(2, 0)

        assert page_ids(window) == [third, first, second]

    def test_表示中のページを追いかける(self, three_pages):
        """番号のまま留まると、動かした直後に別のページが出る。"""
        window = three_pages
        window.state.set_page_index(0)
        showing = window.state.page.id

        window.state.move_page(0, 2)

        assert window.state.page_index == 2
        assert window.state.page.id == showing

    def test_動かしていないページを見ていれば番号がずれる(self, three_pages):
        window = three_pages
        window.state.set_page_index(2)
        showing = window.state.page.id

        window.state.move_page(0, 1)  # 見ているページより手前での入れ替え

        assert window.state.page.id == showing
        assert window.state.page_index == 2

    def test_端では動かない(self, three_pages):
        window = three_pages
        before = page_ids(window)
        assert window.state.move_page(0, -1) is False
        assert window.state.move_page(2, 3) is False
        assert page_ids(window) == before

    def test_同じ位置なら履歴に積まない(self, three_pages):
        window = three_pages
        depth = window.state.history.depth
        assert window.state.move_page(1, 1) is False
        assert window.state.history.depth == depth

    def test_元に戻せる(self, three_pages):
        window = three_pages
        before = page_ids(window)
        window.state.move_page(0, 2)
        window.state.undo()
        assert page_ids(window) == before

    def test_メニューからも動かせる(self, three_pages):
        window = three_pages
        window.state.set_page_index(0)
        showing = window.state.page.id

        window.move_page_by(1)

        assert page_ids(window)[1] == showing
        assert window.move_page_up_action.isEnabled()

    def test_端では項目が選べない(self, three_pages):
        window = three_pages
        window.state.set_page_index(0)
        assert not window.move_page_up_action.isEnabled()
        window.state.set_page_index(2)
        assert not window.move_page_down_action.isEnabled()


class TestPageSize:
    def test_表示中のページだけ変わる(self, three_pages):
        window = three_pages
        window.state.set_page_index(1)

        window.state.set_page_size(PAGE_SIZES["B5"])

        pages = window.state.project.pages
        assert pages[1].size == PAGE_SIZES["B5"]
        assert pages[0].size == PAGE_SIZES["A4"]

    def test_すべてのページに適用できる(self, three_pages):
        three_pages.state.set_page_size(PAGE_SIZES["B5"], all_pages=True)
        assert all(p.size == PAGE_SIZES["B5"] for p in three_pages.state.project.pages)

    def test_次に足すページも同じ大きさになる(self, window):
        """1枚だけ前の大きさ、という食い違いを作らない。"""
        window.state.set_page_size(Size(150.0, 200.0))
        window.add_page()
        assert window.state.page.size == Size(150.0, 200.0)

    def test_カスタムの寸法も使える(self, window):
        window.state.set_page_size(Size(120.5, 180.5))
        assert window.state.page.size == Size(120.5, 180.5)

    def test_元に戻せる(self, window):
        window.state.set_page_size(PAGE_SIZES["B5"])
        window.state.undo()
        assert window.state.page.size == PAGE_SIZES["A4"]

    def test_保存して開き直しても残る(self, window, tmp_path):
        window.state.set_page_size(Size(120.0, 180.0))
        window.state.save(tmp_path)

        restored = load_project(tmp_path)

        assert restored.pages[0].size == Size(120.0, 180.0)
        assert restored.default_page_size == Size(120.0, 180.0)

    def test_はみ出したものを知らせる(self, window):
        """置いたものは動かさない。数えて伝えるだけ（要件定義 6.1）。"""
        window.add_full_page_panel()
        panel = window.state.selected_panel
        before = panel.shape.bounds()

        outside = window.state.set_page_size(Size(100.0, 120.0))

        assert [o.id for o in outside] == [panel.id]
        assert window.state.page.panels[0].shape.bounds() == before

    def test_全ページに適用したら全ページ見て数える(self, three_pages):
        """見えていないページのはみ出しを黙って通さない。"""
        window = three_pages
        window.state.set_page_index(1)  # コマがあるのは1ページ目

        outside = window.state.set_page_size(Size(100.0, 120.0), all_pages=True)

        assert len(outside) == 1

    def test_収まっていれば何も報告しない(self, window):
        window.add_full_page_panel()
        assert window.state.set_page_size(PAGE_SIZES["A4"]) == []

    def test_吹き出しも数える(self, window):
        window.state.add_balloon(Rect(160.0, 20.0, 40.0, 26.0))
        outside = window.state.set_page_size(Size(150.0, 200.0))
        assert len(outside) == 1

    def test_状態表示に大きさが出る(self, window):
        window.state.set_page_size(PAGE_SIZES["B5"])
        window._refresh()
        assert "182 × 257 mm" in window.page_label.text()


class TestOutsidePage:
    def test_紙の中なら空(self, window):
        window.add_full_page_panel()
        assert outside_page(window.state.page) == []

    def test_はみ出したコマを拾う(self, window):
        with window.state.edit("コマの追加") as project:
            project.add_panel(project.pages[0], Rect(-10.0, 10.0, 50.0, 50.0))
        assert len(outside_page(window.state.page)) == 1


class TestPageSizeDialog:
    def test_A4なら用紙が選ばれ寸法は触れない(self, qapp):
        dialog = PageSizeDialog(PAGE_SIZES["A4"], page_count=1)
        try:
            assert dialog.preset.currentData() == "A4"
            assert not dialog.width_mm.isEnabled()
            assert dialog.chosen_size() == PAGE_SIZES["A4"]
        finally:
            dialog.deleteLater()

    def test_合う用紙が無ければカスタムになる(self, qapp):
        dialog = PageSizeDialog(Size(120.0, 180.0), page_count=1)
        try:
            assert dialog.preset.currentData() is None
            assert dialog.width_mm.isEnabled()
            assert dialog.chosen_size() == Size(120.0, 180.0)
        finally:
            dialog.deleteLater()

    def test_用紙を選ぶと寸法が入る(self, qapp):
        dialog = PageSizeDialog(Size(120.0, 180.0), page_count=1)
        try:
            dialog.preset.setCurrentIndex(dialog.preset.findData("B5"))
            assert dialog.chosen_size() == PAGE_SIZES["B5"]
            assert not dialog.height_mm.isEnabled()
        finally:
            dialog.deleteLater()

    def test_1ページなら全ページ適用は選べない(self, qapp):
        dialog = PageSizeDialog(PAGE_SIZES["A4"], page_count=1)
        try:
            assert not dialog.all_pages.isEnabled()
        finally:
            dialog.deleteLater()

    def test_複数ページなら選べる(self, qapp):
        dialog = PageSizeDialog(PAGE_SIZES["A4"], page_count=3)
        try:
            assert dialog.all_pages.isEnabled()
            assert not dialog.apply_to_all()
        finally:
            dialog.deleteLater()


class TestPageSizeMenu:
    """メニューからダイアログを経てモデルまで届くこと。"""

    def _dialog(self, monkeypatch, accepted: bool, all_pages: bool = False):
        from PySide6.QtWidgets import QDialog

        class 決まった答えを返すダイアログ:
            def __init__(self, current, page_count, parent=None):
                self.current = current

            def exec(self):
                return (
                    QDialog.DialogCode.Accepted
                    if accepted
                    else QDialog.DialogCode.Rejected
                )

            def chosen_size(self):
                return PAGE_SIZES["B5"]

            def apply_to_all(self):
                return all_pages

        monkeypatch.setattr(
            "manga_layout.ui.window.PageSizeDialog", 決まった答えを返すダイアログ
        )

    def test_選ぶと変わる(self, three_pages, monkeypatch):
        self._dialog(monkeypatch, accepted=True)
        seen = []
        three_pages.state.message.connect(seen.append)

        three_pages.change_page_size()

        assert three_pages.state.page.size == PAGE_SIZES["B5"]
        assert any("182 × 257 mm" in m for m in seen)

    def test_取り消すと変わらない(self, three_pages, monkeypatch):
        self._dialog(monkeypatch, accepted=False)
        depth = three_pages.state.history.depth

        three_pages.change_page_size()

        assert three_pages.state.page.size == PAGE_SIZES["A4"]
        assert three_pages.state.history.depth == depth

    def test_全ページに適用できる(self, three_pages, monkeypatch):
        self._dialog(monkeypatch, accepted=True, all_pages=True)
        three_pages.change_page_size()
        assert all(p.size == PAGE_SIZES["B5"] for p in three_pages.state.project.pages)

    def test_はみ出しを知らせる(self, three_pages, monkeypatch):
        """1ページ目には A4 全面のコマがある。B5 にすると収まらない。"""
        self._dialog(monkeypatch, accepted=True)
        seen = []
        three_pages.state.message.connect(seen.append)

        three_pages.change_page_size()

        assert any("はみ出しています" in m for m in seen)


class TestPageList:
    def test_枚数が一致する(self, three_pages):
        assert three_pages.pages_panel.count() == 3

    def test_番号が並ぶ(self, three_pages):
        panel = three_pages.pages_panel
        assert [panel.item(i).text() for i in range(3)] == ["1", "2", "3"]

    def test_行を選ぶとページが変わる(self, three_pages):
        three_pages.pages_panel.setCurrentRow(2)
        assert three_pages.state.page_index == 2

    def test_ページを変えると行が動く(self, three_pages):
        three_pages.state.set_page_index(1)
        assert three_pages.pages_panel.currentRow() == 1

    def test_追加と削除に追いつく(self, three_pages):
        window = three_pages
        window.add_page()
        assert window.pages_panel.count() == 4
        window.state.delete_page()
        assert window.pages_panel.count() == 3

    def test_元に戻すにも追いつく(self, three_pages):
        window = three_pages
        window.state.delete_page(0)
        window.state.undo()
        assert window.pages_panel.count() == 3

    def test_並べ替えると番号が振り直される(self, three_pages):
        window = three_pages
        window.state.move_page(0, 2)
        panel = window.pages_panel
        assert [panel.item(i).text() for i in range(3)] == ["1", "2", "3"]
        # 1ページ目のサムネイルは、動かしたページ（コマ入り）ではなくなる
        assert panel.item(2).toolTip().endswith("コマ 1")

    def test_中身を変えるとサムネイルを描き直す(self, window):
        panel = window.pages_panel
        before = panel.item(0).data(Qt.ItemDataRole.UserRole)

        window.add_full_page_panel()

        assert panel.item(0).data(Qt.ItemDataRole.UserRole) != before

    def test_サムネイルが描かれる(self, three_pages):
        item = three_pages.pages_panel.item(0)
        assert not item.icon().isNull()

    def test_行に縮小画像が実際に出る(self, three_pages):
        """項目に持たせるだけでなく、行の絵として出ていること。

        番号を左へ移すために描画を自前にしてある。項目を持っているのに
        1枚も描いていない、という壊れ方をここで止める。
        """
        from manga_layout.ui.pages import ITEM_GAP, ITEM_PADDING, NUMBER_WIDTH

        panel = three_pages.pages_panel
        image = panel.grab().toImage()
        rect = panel.visualItemRect(panel.item(0))
        left = rect.x() + ITEM_PADDING + NUMBER_WIDTH + ITEM_GAP

        # 用紙は白。選択中の行でも、縮小画像が乗っていれば白い画素が残る
        white = 0
        for x in range(left + 4, min(left + 40, image.width())):
            for y in range(rect.y() + 8, min(rect.y() + 40, image.height())):
                c = image.pixelColor(x, y)
                if c.red() > 240 and c.green() > 240 and c.blue() > 240:
                    white += 1
        assert white > 0, "行に縮小画像が描かれていない"

    def test_一覧は中身のぶんだけ占める(self, three_pages):
        """本画面をなるべく広く取る。余らせるとその幅ぶん狭くなり続ける。"""
        from PySide6.QtWidgets import QStyle

        from manga_layout.ui.pages import ITEM_WIDTH

        panel = three_pages.pages_panel
        allowance = (
            panel.spacing() * 2
            + panel.frameWidth() * 2
            + panel.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        )
        assert panel.width() == ITEM_WIDTH + allowance

    def test_一覧は広げられない(self, three_pages):
        """ドックを引っぱっても広がらない。"""
        panel = three_pages.pages_panel
        assert panel.minimumWidth() == panel.maximumWidth() == panel.width()

    def test_番号は縮小画像より左に出る(self, three_pages):
        from manga_layout.ui.pages import ITEM_PADDING, NUMBER_WIDTH

        panel = three_pages.pages_panel
        rect = panel.visualItemRect(panel.item(0))
        # 番号の欄は行の左端。ここが画像の幅に食い込んでいないこと
        assert ITEM_PADDING + NUMBER_WIDTH < rect.width() - panel.iconSize().width()


class TestPageJumpBar:
    """見出しの「ページ [n]/総数」。番号を押すと入力欄になる（要件定義 6.1）。"""

    def test_何枚中の何枚目かが出る(self, three_pages):
        window = three_pages
        window.state.set_page_index(1)
        assert window.pages_title.number.text() == "2"
        assert window.pages_title.total.text() == "/3"

    def test_ページが増えると総数も変わる(self, three_pages):
        three_pages.add_page()
        assert three_pages.pages_title.total.text() == "/4"
        assert three_pages.pages_title.number.text() == "4"

    def test_メニューの項目名は一覧のまま(self, three_pages):
        from manga_layout.ui.window import PAGES_MENU_LABEL

        three_pages.state.set_page_index(2)
        assert three_pages.pages_toggle_action.text() == PAGES_MENU_LABEL

    def test_番号を押すと入力欄になる(self, three_pages):
        bar = three_pages.pages_title
        three_pages.state.set_page_index(1)

        bar.number.clicked.emit()

        assert bar.field.currentWidget() is bar.edit
        # いまの番号が入っていて、打ち直せば置き換わる状態
        assert bar.edit.text() == "2"
        assert bar.edit.selectedText() == "2"

    def test_数字を決定するとそのページへ移る(self, three_pages):
        bar = three_pages.pages_title
        bar.number.clicked.emit()
        bar.edit.setText("3")

        bar.edit.returnPressed.emit()

        assert three_pages.state.page_index == 2
        # 入力欄は表示へ戻り、新しい番号が出ている
        assert bar.field.currentWidget() is bar.number
        assert bar.number.text() == "3"

    def test_範囲外なら移動せず知らせる(self, three_pages):
        bar = three_pages.pages_title
        seen = []
        three_pages.state.message.connect(seen.append)

        bar.number.clicked.emit()
        bar.edit.setText("9")
        bar.edit.returnPressed.emit()

        assert three_pages.state.page_index == 0
        assert any("1 〜 3" in m for m in seen)

    def test_数字以外は打てない(self, three_pages):
        from PySide6.QtGui import QValidator

        bar = three_pages.pages_title
        rejected, _, _ = bar.edit.validator().validate("a", 1)
        assert rejected == QValidator.State.Invalid

    def test_範囲外の数字は打てるようにしておく(self, three_pages):
        """上限をページ数に絞ると「入力途中」扱いで Enter が効かなくなる。

        打てないのではなく、決定したときに言葉で知らせるほうが、
        なぜ動かないのか分かる。
        """
        from PySide6.QtGui import QValidator

        bar = three_pages.pages_title
        accepted, _, _ = bar.edit.validator().validate("9", 1)
        assert accepted == QValidator.State.Acceptable

    def test_取り消すと移動しない(self, three_pages):
        bar = three_pages.pages_title
        bar.number.clicked.emit()
        bar.edit.setText("3")

        bar.cancelled_by_escape = bar.edit.cancelled.emit()

        assert three_pages.state.page_index == 0
        assert bar.field.currentWidget() is bar.number
        assert bar.number.text() == "1"

    def test_空のまま決定しても移動しない(self, three_pages):
        bar = three_pages.pages_title
        bar.number.clicked.emit()
        bar.edit.clear()

        bar.edit.returnPressed.emit()

        assert three_pages.state.page_index == 0
        assert bar.field.currentWidget() is bar.number

    def test_押して打って決定するまで通しで動く(self, three_pages):
        """本物のクリックとキー入力で確かめる。

        入力欄の受け付ける範囲を絞ると、Qt は Enter を「入力途中」と見て
        何も起こさない。信号を直接出す検証だけではそこを見逃す。
        """
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest

        bar = three_pages.pages_title
        point = QPointF(bar.number.rect().center())
        bar.number.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                point,
                bar.number.mapToGlobal(point),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        QTest.keyClicks(bar.edit, "3")
        QTest.keyClick(bar.edit, Qt.Key.Key_Return)

        assert three_pages.state.page_index == 2
        assert bar.number.text() == "3"

    def test_確定したあと二重に戻らない(self, three_pages):
        """Enter のあと欄が焦点を失って、もう一度後始末に来る。"""
        bar = three_pages.pages_title
        bar.number.clicked.emit()
        bar.edit.setText("2")
        bar.edit.returnPressed.emit()

        bar.edit.cancelled.emit()  # 焦点が外れたときの分

        assert three_pages.state.page_index == 1
        assert bar.number.text() == "2"


class TestThumbnail:
    """本画面と同じ経路で描けていること。"""

    def test_用紙が白く出る(self, window):
        from manga_layout.ui.pages import thumbnail_box

        page = window.state.page
        box = thumbnail_box([page])
        image = render_thumbnail(window.state, page, box).toImage()

        center = image.pixelColor(box.width() // 2, box.height() // 2)
        assert center.red() > 240 and center.green() > 240 and center.blue() > 240

    def test_コマが描かれる(self, window):
        from manga_layout.ui.pages import thumbnail_box

        window.add_full_page_panel()
        page = window.state.page
        box = thumbnail_box([page])
        image = render_thumbnail(window.state, page, box).toImage()

        # コマの下地は #F4F4F4。真っ白な用紙と区別できる
        center = image.pixelColor(box.width() // 2, box.height() // 2)
        assert (center.red(), center.green(), center.blue()) == (244, 244, 244)

    def test_大きさの違うページも枠に収まる(self, window):
        from manga_layout.ui.pages import thumbnail_box

        window.state.set_page_size(Size(300.0, 100.0))  # 横長
        page = window.state.page
        box = thumbnail_box([page])
        pixmap = render_thumbnail(window.state, page, box)

        assert pixmap.size() == box


class TestReorderTarget:
    """挿入位置 → 並べ替え後の番号。ここを間違えると1つ隣へ動かせない。"""

    @pytest.mark.parametrize(
        "source, insert_at, expected",
        [
            (0, 2, 1),  # 後ろへ運ぶと、自分が抜けたぶん1つ手前になる
            (0, 3, 2),  # 末尾の後ろへ
            (2, 0, 0),  # 前へ運ぶときはそのまま
            (2, 1, 1),
            (1, 5, 2),  # 行き過ぎても最後で止める
            (1, -3, 0),
        ],
    )
    def test_換算(self, source, insert_at, expected):
        assert reorder_target(source, insert_at, 3) == expected


class TestDropReorder:
    """一覧へ落としたときに、モデル側だけが並びを持つこと。"""

    def _drop(self, window, source: int, at_row: int) -> None:
        panel = window.pages_panel
        panel._drag_row = source
        rect = panel.visualItemRect(panel.item(at_row))
        event = QDropEvent(
            QPointF(rect.center()),
            Qt.DropAction.MoveAction,
            QMimeData(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        panel.dropEvent(event)

    def test_落とすと並びが変わる(self, three_pages):
        window = three_pages
        first = page_ids(window)[0]

        self._drop(window, 0, 2)

        assert page_ids(window)[1] == first

    def test_項目が増えも減りもしない(self, three_pages):
        """Qt にも動かさせると、行が重複したり消えたりする。"""
        window = three_pages
        self._drop(window, 0, 2)
        assert window.pages_panel.count() == window.state.page_count == 3

    def test_落としたあと元に戻せる(self, three_pages):
        window = three_pages
        before = page_ids(window)
        self._drop(window, 0, 2)
        window.state.undo()
        assert page_ids(window) == before
        assert window.pages_panel.count() == 3
