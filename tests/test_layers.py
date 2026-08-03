"""重ねる順（表示レイヤー）の検証。

奥から手前へ、用紙 → コマ（と中の画像）→ 吹き出し → セリフ。
**この順は種類で決まり、z では覆せない**（`model.floating_order`）。

押さえたいのは1点だけ。**セリフを書いたあとに吹き出しを載せても、
文字が消えないこと。** 以前は z だけで重ねていたため、後から作った
吹き出しのほうが z が大きく、白い塗りが文字を塗り潰していた。
吹き出しは後から足すのが普通の手順なので、これは日常的に踏む。
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from manga_layout import Rect
from manga_layout.model import LAYER_BALLOON, LAYER_TEXT, floating_order
from manga_layout.ui import EditorState
from manga_layout.ui.export import render_page

# 吹き出しと、その内側に収まるセリフ（px）
BALLOON = Rect(200.0, 200.0, 400.0, 300.0)
TEXT = Rect(340.0, 300.0, 120.0, 100.0)


@pytest.fixture
def state(qapp, tmp_path):
    editor = EditorState()
    editor.save(tmp_path / "作品")
    return editor


def rendered(editor) -> QImage:
    return render_page(editor, editor.page)


class Test並び順:
    def test_セリフは吹き出しより手前(self):
        assert LAYER_TEXT > LAYER_BALLOON

    def test_zでは覆せない(self, state):
        """吹き出しの z を上げても、セリフの下に入る。"""
        text = state.add_text(TEXT, "あ")
        balloon = state.add_balloon(BALLOON)
        assert balloon.z > text.z  # 後に作ったので z は大きい
        assert floating_order(text) > floating_order(balloon)

    def test_同じ種類どうしはzで決まる(self, state):
        first = state.add_text(TEXT, "あ")
        second = state.add_text(TEXT.translated(10.0, 10.0), "い")
        assert floating_order(second) > floating_order(first)


class Test描画:
    def test_後から載せた吹き出しがセリフを隠さない(self, state):
        """セリフ → 吹き出しの順に置いても、文字が残る。"""
        state.add_text(TEXT, "あ")
        state.add_balloon(BALLOON)
        with_text = rendered(state)

        # 同じ吹き出しだけを置いた1枚と見比べる。一致したら文字が
        # 塗り潰されている
        only_balloon = EditorState()
        only_balloon.save(state.project_dir.parent / "吹き出しだけ")
        only_balloon.add_balloon(BALLOON)
        assert with_text != rendered(only_balloon)

    def test_吹き出しはセリフの下に敷かれる(self, state):
        """順を逆に作っても同じ絵になる。作った順で見た目が変わらない。"""
        state.add_text(TEXT, "あ")
        state.add_balloon(BALLOON)

        reversed_order = EditorState()
        reversed_order.save(state.project_dir.parent / "逆順")
        reversed_order.add_balloon(BALLOON)
        reversed_order.add_text(TEXT, "あ")

        assert rendered(state) == rendered(reversed_order)
