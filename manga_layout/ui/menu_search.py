"""メニューの項目を言葉で探す窓（要件定義 6.30）。

**出すのは「どこにあるか」だけで、押しても実行しない。** 辿り着いた先で
メニューを開き直してもらう。ここから直に実行できるようにすると、
選んでいるものによって押せない項目をこの窓でももう一度説明することになる
（メニュー側は灰色で示している → 6.12）。

**項目名だけでなく、項目に添えた説明も探す。** 項目名は読む字数を減らす側に
倒してあり（→ 6.12）、言葉で辿り着ける情報は説明のほう（カーソルを乗せた
間だけ画面下に出るあの文）に寄っている。

**言い換えの表は持たない。** 「ふきだし」と打っても「フキダシ」は出ない。
先回りで表を作ると、使われない言い換えまで抱え込んだうえ、項目を増やす
たびに足す手間が残る。実際に出てこなかった言葉が分かってから足す。

押した項目については、**そのメニューを画面上端で四角く囲む**
（→ `MainWindow.highlight_menu`）。実行はしないまま、最初に開く場所だけ
名指しする。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .window import MainWindow

# 窓の初期の大きさ（画面ピクセル）。説明が2行に折り返しても10件前後は
# スクロールせずに読める高さにしてある
DIALOG_SIZE = (520, 480)

# 道順のつなぎ目。メニューを上から順にたどる向きに読める矢印にする
PATH_SEPARATOR = " → "

# 押した項目のメニューを囲んでおく時間（秒）。
#
# **消えるようにする。** 出しっぱなしにすると、次に押すまで「今どれを
# 探していたか」と関係のない枠が残る。状態表示の1行（6秒）より短いのは、
# あちらが読ませる文なのに対し、こちらは目を向けさせるだけのため
HIGHLIGHT_SECONDS = 3.0

# 窓を最初に出す場所。親の窓の右下から、この幅だけ内側に置く。
#
# **上端に重ねない。** 押した項目のメニューを画面上端で囲むので
# （→ `MainWindow.highlight_menu`）、窓がそこに重なると肝心の枠が隠れる。
# `TOP_GAP` はメニューバーと道具箱の2段ぶんの見込み
FIRST_PLACE_MARGIN = 24
FIRST_PLACE_TOP_GAP = 120

# アクセスキーの印（「ファイル(&F)」の `(&F)` の部分）
_ACCESS_KEY = re.compile(r"\(&.\)")


def plain_label(text: str) -> str:
    """メニューの文言から、アクセスキーの印を落とす。

    そのまま出すと「ファイル(&F) → 開く...」になり、探す言葉に `&` が
    混じった扱いにもなる。表に出す形と探す対象の両方でこれを使う。
    """
    return _ACCESS_KEY.sub("", text).replace("&", "").strip()


@dataclass(frozen=True)
class MenuEntry:
    """メニューの項目1つぶんの控え。**Qt の部品を持たない。**

    窓を開くたびに作り直す（→ `collect_menu_entries`）ので、文字にして
    しまえば持ち越す必要が無い。QAction や QMenu を持つと、控えたほうが
    先に無効化される事故（→ `PySide6の落とし穴.md` の 1）を自分で
    増やすことになる。
    """

    path: tuple[str, ...]  # ("ファイル", "ラフ")
    text: str  # "読み込む..."
    tip: str
    shortcut: str

    @property
    def trail(self) -> str:
        """「ファイル → ラフ → 読み込む...」。"""
        return PATH_SEPARATOR.join((*self.path, self.text))

    @property
    def haystack(self) -> str:
        """探す対象。道順ごと見るので、「ファイル 書き出し」でも当たる。"""
        return f"{self.trail}\n{self.tip}"


def collect_menu_entries(window: MainWindow) -> list[MenuEntry]:
    """メニューバーを上から辿って、項目を平らに並べる。

    **窓を開くたびに呼ぶ。** 文言は状態で変わるので（畳んだ親が
    「トーン調整中」になる、「前回のファイルを開く（xxx.json）」に
    ファイル名が入る → 6.27、6.6）、作り置きすると古い名前で出る。
    """
    submenus = _submenus_by_action(window)
    entries: list[MenuEntry] = []
    _walk(window.menuBar().actions(), (), submenus, entries)
    return entries


def _submenus_by_action(window: MainWindow) -> dict[QAction, QMenu]:
    """「この項目を押すと開くメニュー」の対応表。

    **`QAction.menu()` を使わない**（→ `PySide6の落とし穴.md` の 1）。
    あれは呼んだ時点で、返ってきたメニューを呼び出し側の QAction の
    持ち物にしてしまう。ここで使い捨てにすると、その QAction が片付く
    ときにメニューを道連れにし、アプリが持っている `rough_menu` などが
    後から無効になる。**壊れるのは読んだ側ではなく、無関係な向こう側**。

    代わりにメニュー側から `menuAction()` で引く逆向きだけを使い、
    対応表を先に作っておく（この向きは安全だと実測されている）。
    """
    return {menu.menuAction(): menu for menu in window.findChildren(QMenu)}


def _walk(
    actions: list[QAction],
    path: tuple[str, ...],
    submenus: dict[QAction, QMenu],
    entries: list[MenuEntry],
) -> None:
    """メニュー1枚ぶんの項目を見て、下位のメニューへ降りる。

    畳んだメニューの見出し（「ラフ」「トーン」など）自体は項目にしない。
    押しても開くだけで何も起きず、名前は下の項目の道順に必ず出るため。
    """
    for action in actions:
        if action.isSeparator():
            continue
        label = plain_label(action.text())
        submenu = submenus.get(action)
        if submenu is not None:
            _walk(submenu.actions(), (*path, label), submenus, entries)
            continue
        if not label:
            continue
        entries.append(
            MenuEntry(
                path=path,
                text=label,
                tip=action.statusTip(),
                shortcut=action.shortcut().toString(
                    QKeySequence.SequenceFormat.NativeText
                ),
            )
        )


def search(entries: list[MenuEntry], query: str) -> list[MenuEntry]:
    """打ち込んだ言葉を含む項目を返す。**単純な文字の一致だけ。**

    空白で区切ると、全部を含むものに絞る（「フキダシ しっぽ」）。
    全角の空白も区切りとして扱う——日本語を打っている最中に半角へ
    切り替える手間を、絞り込みのために課さない。

    空のときは全部返す。**この窓をメニューの一覧としても使えるように
    するため**で、打ち始める前に何があるかを眺められる。
    """
    words = [word.casefold() for word in query.replace("　", " ").split()]
    if not words:
        return list(entries)
    return [
        entry
        for entry in entries
        if all(word in entry.haystack.casefold() for word in words)
    ]


def item_text(entry: MenuEntry) -> str:
    """一覧の1行。道順、ショートカット、説明の順。

    説明は下の段へ落として字下げする。1行に続けると道順が右へ流れて、
    縦に並べたときの読み比べができなくなる。

    **キーが名前に入っている項目には足さない。** 道具の項目は名前自体が
    「コマ追加 (P)」の形なので（→ `_build_tool_actions`）、そのまま足すと
    「コマ追加 (P)　［P］」と2度出る。
    """
    head = entry.trail
    if entry.shortcut and f"({entry.shortcut})" not in entry.text:
        head += f"　［{entry.shortcut}］"
    return f"{head}\n　{entry.tip}" if entry.tip else head


class MenuSearchDialog(QDialog):
    """メニューを探す窓。**作りっぱなしで使い回す**（→ `CheckResultDialog`）。

    **相手を止めない窓にする。** 探した項目をメニューから開くには、
    この窓が出たままでもメニューバーへ手が届く必要がある。
    """

    # 押された項目の、いちばん上のメニュー名（「画像」）。
    # **窓は囲む相手を知らない。** 画面上端のどこにあるかはメニューバーに
    # 聞かないと分からないので、名前だけ渡して `MainWindow` に任せる
    menu_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("メニューを探す")
        self.setModal(False)
        self.resize(*DIALOG_SIZE)

        self._entries: list[MenuEntry] = []
        self._hits: list[MenuEntry] = []
        self._placed = False

        self._field = QLineEdit()
        self._field.setPlaceholderText("探す言葉（例: ラフ、トーン、書き出し）")
        self._field.textChanged.connect(self._filter)

        self._count = QLabel()

        self._list = QListWidget()
        # 説明が長い項目があるので折り返す。横スクロールで読ませない
        self._list.setWordWrap(True)
        # **1回押しただけで囲む。** ダブルクリックや Enter だけにすると、
        # 押しても何も起きない当たり判定ができる（この一覧は押して実行する
        # ものではないので、二段構えにする理由が無い）。Enter でも通す
        self._list.itemClicked.connect(self._announce)
        self._list.itemActivated.connect(self._announce)

        note = QLabel(
            "項目名と、その説明の両方から探します。"
            "押すと、その項目があるメニューを画面の上端で四角く囲みます"
            "（実行はしません）。"
        )
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._field)
        layout.addWidget(self._count)
        layout.addWidget(self._list)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def show_entries(self, entries: list[MenuEntry]) -> None:
        """一覧を入れ替えて前に出す。開いていなければ開く。

        **前に打った言葉は残したまま、選択状態にする。** 同じ言葉で
        探し直すことも、そのまま打ち直すこともできる。
        """
        self._entries = entries
        self._filter()
        self._place_once()
        self._field.setFocus()
        self._field.selectAll()
        self.show()
        self.raise_()
        self.activateWindow()

    def _place_once(self) -> None:
        """初めて開くときだけ、親の窓の右下へ寄せる。

        **2回目からは動かさない。** 使う人が置き直した場所を、開くたびに
        引き戻すことになる。
        """
        parent = self.parentWidget()
        if self._placed or parent is None:
            return
        self._placed = True
        area = parent.frameGeometry()
        self.move(
            max(
                area.left() + FIRST_PLACE_MARGIN,
                area.right() - self.width() - FIRST_PLACE_MARGIN,
            ),
            max(
                area.top() + FIRST_PLACE_TOP_GAP,
                area.bottom() - self.height() - FIRST_PLACE_MARGIN,
            ),
        )

    def _filter(self) -> None:
        self._hits = search(self._entries, self._field.text())
        self._list.clear()
        for entry in self._hits:
            self._list.addItem(item_text(entry))
        self._count.setText(
            f"{len(self._hits)} 件"
            if self._hits
            else "見つかりません（別の言葉で探してください）"
        )

    def _announce(self, item: QListWidgetItem) -> None:
        """押された行の、いちばん上のメニュー名を知らせる。"""
        row = self._list.row(item)
        if 0 <= row < len(self._hits):
            self.menu_chosen.emit(self._hits[row].path[0])


# メニューに添える説明（ホバー中の状態表示に出る → 7章）
MENU_SEARCH_HINT = (
    "打ち込んだ言葉を含む項目を、メニューのどこにあるかと一緒に並べる"
    "（項目名だけでなく、この説明文からも探す）"
)
