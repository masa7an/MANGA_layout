"""Qt が用意しているボタン・窓の文字を日本語にする。

`QMessageBox` や `QDialogButtonBox` の「はい・いいえ」に当たるボタンは、
**こちらが文字を書いていない。** 押した意味（保存する／捨てる／やめる）だけを
Qt に渡し、そこに出る言葉は Qt が決める。既定は英語なので、何もしなければ
`Save` `Discard` `Cancel` と出る。

日本語にする道は2つあり、両方を重ねて使う。

1. **Qt に付いてくる日本語訳を読み込む**（`qtbase_ja.qm`）。ボタンだけでなく、
   フォントを選ぶ窓の見出しや、文字を打つ欄の右クリックまで一度に日本語になる
2. **そのうえで、この作品での言い方に差し替える**（`BUTTON_WORDS`）。Qt の訳は
   `Cancel` を「キャンセル」、`Discard` を「変更を破棄」と当てるが、
   書き出しの進み具合の窓では既に「中止」と書いてある（→ `project_io`）。
   **同じ意味のボタンが窓によって違う言葉で出るのは、読む側の負担になる**

差し替えは `QTranslator` を1つ被せて行う。当てはまらない言葉には `None` を返し、
下に敷いた Qt の日本語訳へ落とす。**空文字を返してはいけない**——それは
「訳は空である」という意味になり、見出しの文字が消える（2026-08-27 に実測）。
"""

from __future__ import annotations

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

# Qt がボタンの文字を引くときの見出し。ここ以外は触らない
THEME_CONTEXT = "QPlatformTheme"

# 差し替える言葉。左は Qt が使っている元の文字で、`&` は押下キーの目印。
# **ここに無いものは Qt の日本語訳のまま**（保存・閉じる・はい・いいえ など）
BUTTON_WORDS = {
    # 「キャンセル」だと、書き出しの窓の「中止」と食い違う
    "Cancel": "中止",
    # 「変更を破棄」では、何が失われるのか分かりにくい。
    # 保存の確認で「保存」と並ぶので、対にして読めるようにする
    "Discard": "保存しない",
}

# 読み込んだ訳は、こちらで持っていないと消える。Qt 側は預かってくれない
# （参照が切れると訳が当たらなくなる。2026-08-27 に実測）
_installed: list[QTranslator] = []


class ButtonWords(QTranslator):
    """ボタンの言葉だけを差し替える訳。当てはまらなければ下へ落とす。"""

    def translate(self, context, source, disambiguation=None, n=-1):  # noqa: N802
        if context == THEME_CONTEXT:
            return BUTTON_WORDS.get(source)
        return None


def install(app: QApplication) -> None:
    """日本語の訳を入れる。`QApplication` を作った直後に1回だけ呼ぶ。

    Qt の日本語訳が見つからなくても止めない。その場合はボタンの一部が
    英語のまま出るだけで、**窓が開かなくなるよりはるかにましである。**
    """
    base = QTranslator()
    if base.load(
        QLocale("ja_JP"),
        "qtbase",
        "_",
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath),
    ):
        app.installTranslator(base)
        _installed.append(base)

    # 後から入れたものが先に効く。差し替えは Qt の訳より後に入れる
    words = ButtonWords()
    app.installTranslator(words)
    _installed.append(words)
