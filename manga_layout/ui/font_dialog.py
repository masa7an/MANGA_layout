"""フォントを選ぶ窓。Qt の窓に「打ち込んで絞り込む」欄を足す。

書体は 199 件並ぶ（このPCでの実測）。窓を広げても（→ `FONT_DIALOG_SIZE`）
一度に見えるのは十数件で、**目当ての書体まで送る手間は残る**
（本人の要望 2026-08-07）。名前が分かっているときは、打ったほうが速い。

`QFontDialog` は Qt が組み立てた窓なので、**中の部品を後から差し替えて**
足している（→ `FontChooser._install`）。組み方が思っていた通りでなければ、
**絞り込みを付けずにそのまま開く。** Qt の版が上がって中身が変わったときに、
フォントを選ぶ手段ごと無くなるのがいちばん困るので、諦める側に倒す。
"""

from __future__ import annotations

import unicodedata

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFontDialog,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListView,
    QWidget,
)

# 絞り込みの欄に薄く出しておく文。**何を打てばいいかを例で示す。**
# 英語名でも日本語名でも当たることが、例を並べただけで分かる
FILTER_HINT = "打ち込むと絞り込み（例: メイリオ、gothic、UD）"

# 一覧の見出し。元は Qt の「Font」で、ここに件数を足して出す
HEADING = "書体"

# 窓の中の置き場所（`QGridLayout` の行・列）。Qt が組み立てた並びで、
# 0行目が見出し、1行目が今の書体名（読み取り専用の欄）、2行目が一覧
_HEADING_CELL = (0, 0)
_NAME_CELL = (1, 0)
_LIST_CELL = (2, 0)


def matches(family: str, query: str) -> bool:
    """打ち込んだ言葉を全部含む書体名か。空のときは全部当たる。

    空白で区切ると、全部を含むものに絞る（「ud 教科」）。全角の空白も
    区切りにする——日本語を打っている最中に半角へ切り替える手間を、
    絞り込みのために課さない（→ `menu_search.search` と同じ扱い）。

    大文字小文字と全角半角は区別しない。**全角を潰すのが効くのは
    「ＭＳ ゴシック」**で、あれは書体名自体が全角で登録されているため、
    そのままでは半角で打った「MS」に当たらない。

    **言い換えの表は持たない**（→ `menu_search.SYNONYMS`）。書体の一覧には
    同じ書体が英語名と日本語名の両方で並んでいる（「Yu Gothic」と
    「游ゴシック」、「Meiryo」と「メイリオ」）ので、どちらで打っても
    何かしらには当たる。
    """
    folded = _fold(family)
    return all(word in folded for word in _words(query))


def _words(query: str) -> list[str]:
    """打ち込んだ言葉を、区切って揃える。"""
    return [_fold(word) for word in query.replace("　", " ").split()]


def _fold(text: str) -> str:
    """大文字小文字・全角半角の違いを潰す（`menu_search._fold` と同じ扱い）。"""
    return unicodedata.normalize("NFKC", text).casefold()


class FontChooser(QFontDialog):
    """絞り込みの欄を足した、フォントを選ぶ窓。

    足すのは欄1つで、**書体の一覧そのものには手を触れない**
    （→ `_apply`）。窓の中身は一覧の並びを見て書体を決めているので、
    中身を作り直すと選んだ書体が食い違う。当たらない行を隠すだけにする。
    """

    def __init__(self, initial: QFont, parent: QWidget | None = None) -> None:
        super().__init__(initial, parent)
        self.filter_field = QLineEdit(self)
        self.filter_field.setPlaceholderText(FILTER_HINT)
        # 打った言葉を消すのに、後退キーを押し続けさせない
        self.filter_field.setClearButtonEnabled(True)

        self._family_list: QListView | None = None
        self._heading: QLabel | None = None
        self._install()

    def _install(self) -> None:
        """Qt が組み立てた窓に、絞り込みの欄を割り込ませる。

        置き場所は**今の書体名が出ていた欄**（読み取り専用）。一覧のすぐ上、
        「Font」の見出しの下という、打ちに行く場所として自然な位置が
        そこしかない。名前の欄が無くなっても、選んでいる書体は一覧の反転と
        見本の文字に出ているので分からなくならない。

        **元の欄は隠すだけで、片付けてはいけない。** 窓の中身はこの欄を
        持ったままで、書体が変わるたびに文字を書き込む。消すとそこで落ちる。

        思っていた組み方でなければ、何もせずに帰る（絞り込みが無いだけで、
        窓は今まで通り開く → このモジュールの冒頭）。
        """
        grid = self.layout()
        if not isinstance(grid, QGridLayout):
            return
        heading = _widget_at(grid, _HEADING_CELL)
        name_field = _widget_at(grid, _NAME_CELL)
        family_list = _widget_at(grid, _LIST_CELL)
        if not isinstance(name_field, QLineEdit):
            return
        if not isinstance(family_list, QListView):
            return

        grid.replaceWidget(name_field, self.filter_field)
        name_field.hide()

        self._family_list = family_list
        self._heading = heading if isinstance(heading, QLabel) else None

        self.filter_field.textChanged.connect(self._apply)
        self.filter_field.returnPressed.connect(self._take_first)

        model = family_list.model()
        if model is not None:
            # 書字系（Writing System）を変えると一覧が丸ごと入れ替わる。
            # **隠した行の番号はそのまま残る**ので、入れ替わったら当て直す
            model.modelReset.connect(self._apply)
        self._apply()

    def showEvent(self, event) -> None:  # noqa: N802  (Qt の決まった名前)
        """開いた直後は絞り込みの欄に手を置く。そのまま打てば絞り込める。"""
        super().showEvent(event)
        self.filter_field.setFocus()

    def visible_families(self) -> list[str]:
        """今、一覧に見えている書体の名前。"""
        model = self._model()
        if model is None or self._family_list is None:
            return []
        return [
            str(model.index(row, 0).data() or "")
            for row in range(model.rowCount())
            if not self._family_list.isRowHidden(row)
        ]

    def _apply(self) -> None:
        """打ち込んだ言葉に当たらない行を隠す。"""
        model = self._model()
        if model is None or self._family_list is None:
            return
        query = self.filter_field.text()
        shown = 0
        for row in range(model.rowCount()):
            hit = matches(str(model.index(row, 0).data() or ""), query)
            self._family_list.setRowHidden(row, not hit)
            shown += hit
        self._show_count(shown)

    def _show_count(self, shown: int) -> None:
        """見出しに件数を出す。

        **0件のときに何も言わないと、窓が壊れたように見える。** 一覧が
        白いまま残るだけで、打った言葉のせいだと結び付けられない。
        件数の置き場所は元からある見出しを使い、窓の作りは変えない。
        """
        if self._heading is None:
            return
        found = f"{shown}件" if shown else "見つかりません"
        self._heading.setText(f"{HEADING}（{found}）")

    def _take_first(self) -> None:
        """Enter で、絞り込んだ先頭の書体を選ぶ。

        **この後、窓はそのまま決定へ進む**（Enter は OK ボタンへ渡る）。
        ここで選び直しておかないと、**絞り込む前に選ばれていた書体のまま
        決まってしまう。** 打ち込んで Enter という運びで最も外しやすい。

        当たりが1つも無いときは何もしない（今選ばれている書体のまま）。
        """
        model = self._model()
        if model is None or self._family_list is None:
            return
        for row in range(model.rowCount()):
            if not self._family_list.isRowHidden(row):
                self._family_list.setCurrentIndex(model.index(row, 0))
                return

    def _model(self):
        """書体の一覧の中身。絞り込みを付けられなかったときは None。"""
        return None if self._family_list is None else self._family_list.model()


def _widget_at(grid: QGridLayout, cell: tuple[int, int]) -> QWidget | None:
    """升目に置かれている部品。空なら None。"""
    item = grid.itemAtPosition(*cell)
    return None if item is None else item.widget()
