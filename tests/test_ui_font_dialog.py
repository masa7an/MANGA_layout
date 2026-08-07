"""フォントを選ぶ窓の絞り込み（→ `manga_layout/ui/font_dialog.py`）。

**表示装置なしでは書体が1つも並ばない**ので、一覧の中身はテスト側から
差し込む（→ `_load`）。本物の書体名を使うのは、全角で登録された
「ＭＳ ゴシック」など、実際に打ち間違えようのある名前で確かめるため。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from manga_layout.ui.font_dialog import FontChooser, matches

# Windows 11 に入っている書体から抜き出した並び（実測 199 件のうち）
FAMILIES = [
    "Arial",
    "MS Gothic",
    "ＭＳ ゴシック",
    "Meiryo",
    "Meiryo UI",
    "Yu Gothic",
    "游ゴシック",
    "UD デジタル 教科書体 N",
]


def _load(chooser: FontChooser, families: list[str]) -> None:
    """一覧の中身を差し替える。"""
    chooser._family_list.model().setStringList(families)


@pytest.fixture
def chooser(qapp):
    win = FontChooser(QFont("Meiryo", 12))
    _load(win, FAMILIES)
    return win


class Test当たり判定:
    def test_途中の文字でも当たる(self):
        """先頭からの一致だけだと、Qt の一覧を打つのと変わらない。"""
        assert matches("Meiryo UI", "iryo")

    def test_大文字小文字は区別しない(self):
        assert matches("Meiryo", "MEIRYO")

    def test_全角の書体名を半角で打っても当たる(self):
        """「ＭＳ ゴシック」は**書体名自体が全角**で登録されている。"""
        assert matches("ＭＳ ゴシック", "ms")

    def test_空白で区切ると全部を含むものに絞る(self):
        assert matches("UD デジタル 教科書体 N", "ud 教科")
        assert not matches("UD デジタル 教科書体 N", "ud 明朝")

    def test_全角の空白も区切りにする(self):
        """日本語を打っている最中に半角へ切り替える手間を課さない。"""
        assert matches("UD デジタル 教科書体 N", "ud　教科")

    def test_空のときは全部当たる(self):
        assert matches("Arial", "")
        assert matches("Arial", "　 ")


class Test絞り込み:
    def test_打つ前は全部並ぶ(self, chooser):
        assert chooser.visible_families() == FAMILIES

    def test_打つと当たらない書体が消える(self, chooser):
        chooser.filter_field.setText("meiryo")

        assert chooser.visible_families() == ["Meiryo", "Meiryo UI"]

    def test_消せば元に戻る(self, chooser):
        chooser.filter_field.setText("meiryo")
        chooser.filter_field.setText("")

        assert chooser.visible_families() == FAMILIES

    def test_件数を見出しに出す(self, chooser):
        chooser.filter_field.setText("gothic")

        assert chooser._heading.text() == "書体（2件）"

    def test_0件のときはそう言う(self, chooser):
        """**一覧が白いまま残ると、窓が壊れたように見える。**"""
        chooser.filter_field.setText("zzzz")

        assert chooser.visible_families() == []
        assert "見つかりません" in chooser._heading.text()

    def test_一覧が入れ替わっても絞り込みは効いたまま(self, chooser):
        """書字系（Writing System）を変えると一覧が丸ごと入れ替わる。

        隠した行の**番号**はそのまま残るので、当て直さないと
        関係のない書体が消えたままになる。
        """
        chooser.filter_field.setText("ゴシック")
        _load(chooser, ["ＭＳ ゴシック", "游ゴシック", "游明朝"])

        assert chooser.visible_families() == ["ＭＳ ゴシック", "游ゴシック"]

    def test_一覧そのものは作り直さない(self, chooser):
        """窓の中身は一覧の**並び**を見て書体を決めている。

        当たらない行を隠すだけにしないと、選んだ書体が食い違う。
        """
        chooser.filter_field.setText("meiryo")

        model = chooser._family_list.model()
        assert [model.index(i, 0).data() for i in range(model.rowCount())] == FAMILIES


class TestEnter:
    def test_絞り込んだ先頭の書体で決まる(self, chooser):
        """**Enter は OK ボタンへ渡る。**

        絞り込んだ側を選び直しておかないと、打ち込んで Enter という運びで
        **絞り込む前に選ばれていた書体のまま**決まってしまう。

        当てる先は**一覧で選ばれている行**。`selectedFont()` は表示装置が
        無いと差し込んだ書体を実在しないものとして扱い、既定の
        `Sans Serif` に置き換えてしまう（実機では選んだ書体が返ることを
        2026-08-07 に確認済み）。
        """
        chooser.show()
        chooser.filter_field.setText("ゴシック")

        QTest.keyClick(chooser.filter_field, Qt.Key.Key_Return)

        assert chooser.result() == QDialog.DialogCode.Accepted
        assert chooser._family_list.currentIndex().data() == "ＭＳ ゴシック"

    def test_当たりが無ければ選び直さない(self, chooser):
        """今選ばれている書体のまま決まる（打ち間違いで書体が飛ばない）。"""
        before = chooser.currentFont().family()
        chooser.filter_field.setText("zzzz")

        chooser._take_first()

        assert chooser.currentFont().family() == before
