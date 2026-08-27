"""Qt が出すボタンが日本語で出ることを確かめる。

**終了時の確認は、押し間違えると作業が消える。** 3つ並ぶボタンの言葉が
英語に戻っていないかを、ここで見張る（本人の要望 2026-08-27）。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from manga_layout.ui import install_japanese


@pytest.fixture
def 日本語のqapp(qapp):
    """訳を入れた状態にする。他のテストへ持ち越さないよう、最後に外す。"""
    from manga_layout.ui import i18n

    before = list(i18n._installed)
    install_japanese(qapp)
    yield qapp
    for translator in i18n._installed[len(before) :]:
        qapp.removeTranslator(translator)
    del i18n._installed[len(before) :]


class Test終了時の確認:
    def test_保存_保存しない_中止と出る(self, 日本語のqapp):
        SB = QMessageBox.StandardButton
        box = QMessageBox()
        box.setStandardButtons(SB.Save | SB.Discard | SB.Cancel)
        labels = {box.button(b).text() for b in (SB.Save, SB.Discard, SB.Cancel)}
        assert labels == {"保存", "保存しない", "中止"}


class Testそのほかのボタン:
    def test_はい_いいえ_閉じるも日本語(self, 日本語のqapp):
        SB = QMessageBox.StandardButton
        box = QMessageBox()
        box.setStandardButtons(SB.Yes | SB.No | SB.Close)
        # 押下キーの目印（&Y など）は Qt の訳が付けるので、取り除いて比べる
        texts = [box.button(b).text().replace("&", "") for b in (SB.Yes, SB.No, SB.Close)]
        assert texts == ["はい(Y)", "いいえ(N)", "閉じる"]

    def test_窓の下のボタンも日本語(self, 日本語のqapp):
        """`QDialogButtonBox` は `QMessageBox` と別の部品だが、言葉は共通。"""
        SB = QDialogButtonBox.StandardButton
        buttons = QDialogButtonBox(SB.Ok | SB.Cancel)
        assert buttons.button(SB.Cancel).text() == "中止"
