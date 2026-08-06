"""ショートカットキーの一覧を出す窓（要件定義 7章）。

**一覧は手で書かない。メニューを辿って作る**（→ `menu_groups`）。キーは
メニュー項目に付けてあるので（→ `MainWindow._act`）、ここに表を持つと
**キーを変えた日から一覧が嘘をつき始める**。道具箱を `_tool_actions` から
作っているのと同じ理由（→ `MainWindow._build_toolbar`）。

**メニューに出ないキーだけを手で書く**（→ `EXTRA_GROUPS`）。素の `+` / `-`
や `Shift+]` は、セリフの入力中に横取りしないため画面側（`PageView`）で
拾う決まりで、QAction が無い（→ 7章）。**ここだけは書き写しなので、
`test_ui_shortcuts.py` でメニュー側と重複していないことを見張る。**

**押しても実行しない。** 「メニューを探す」窓と同じ線引きで（→ 6.30）、
読むためだけの窓にする。実行できるようにすると、選んでいるものによって
押せる／押せないをこの窓でももう一度説明することになる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .menu_search import PATH_SEPARATOR, MenuEntry, collect_menu_entries

if TYPE_CHECKING:
    from .window import MainWindow

# 窓の初期の大きさ（画面ピクセル）。メニュー由来だけで40行ほど並ぶので、
# 「メニューを探す」窓（480）より縦に長くしてある
DIALOG_SIZE = (520, 600)

# 1つの項目に2本通してあるキーの区切り（「Ctrl+Y / Ctrl+Shift+Z」）
KEY_SEPARATOR = " / "

# 左の列（キー）の幅。「Ctrl+Shift+PgDown / …」まで折り返さずに収まる幅
KEY_COLUMN_PX = 190


@dataclass(frozen=True)
class ShortcutRow:
    """一覧の1行。キーと、それが何をするか。"""

    keys: str  # "Ctrl+Shift+S"
    action: str  # "名前を付けて保存..."


@dataclass(frozen=True)
class ShortcutGroup:
    """見出し1つぶん。メニュー名（「ファイル」）か、手書きの分類名。"""

    title: str
    rows: tuple[ShortcutRow, ...]


# メニューに出ないキー（→ 7章）。**メニュー由来の一覧の後ろに足す。**
#
# ここに書くのは「QAction が無いもの」だけ。メニューに項目があるものを
# 親切のつもりで書き足すと、キーを変えたときに片方だけ古くなる。
EXTRA_GROUPS = (
    ShortcutGroup(
        "画面（メニューに無いキー）",
        (
            ShortcutRow("+ / -", "拡大 / 縮小（画面の中心が軸）"),
            ShortcutRow("Shift+] / Shift+[", "トーンで拾う黒を増やす / 減らす"),
            ShortcutRow("Enter", "選んでいるセリフを打ち始める"),
            ShortcutRow("Esc", "画像からコマへ戻る / 選択を解除する"),
        ),
    ),
    ShortcutGroup(
        "セリフを打っている間",
        (
            ShortcutRow("Enter", "改行する"),
            ShortcutRow("Ctrl+Enter", "確定する"),
            ShortcutRow("Esc", "取り消す"),
        ),
    ),
    ShortcutGroup(
        "マウス",
        (
            ShortcutRow("ホイール", "拡大 / 縮小（マウスの位置が軸）"),
            ShortcutRow("スペース+ドラッグ", "画面を動かす（中ボタンのドラッグでも同じ）"),
            ShortcutRow("ダブルクリック", "押した場所のものを順に選び直す"),
            ShortcutRow("右クリック", "押した場所のものを選び、その場のメニューを出す"),
            ShortcutRow("Shift+ドラッグ", "縦横の比を保って大きさを変える / 15度ずつ回す"),
        ),
    ),
)


def collect_groups(window: MainWindow) -> list[ShortcutGroup]:
    """窓に出す一覧ぜんぶ。メニュー由来が先、手書きが後（→ `EXTRA_GROUPS`）。"""
    return [*menu_groups(window), *EXTRA_GROUPS]


def menu_groups(window: MainWindow) -> list[ShortcutGroup]:
    """メニューを辿って、キーの付いている項目だけを拾う。

    見出しはいちばん上のメニュー名で、並びはメニューバーの並びのまま。
    **探すときの順（あいうえお順など）に組み替えない。** キーを確かめる人は
    画面上端の並びを覚えているので、そこと同じ順のほうが目で追える。

    **同じ項目が2度出ないようにする。** 道具の項目（「コマ追加 (P)」など）は
    道具メニューと各メニューの両方に置いてあり（同じ QAction を使い回す →
    `_build_tool_actions`）、素直に並べると同じキーが二度出る。**先に出た
    ほうを残す**ので、道具のキーは「道具」ではなく、その道具が属する
    メニュー（「コマ」「フキダシ」…）の側に並ぶ。
    """
    groups: dict[str, list[ShortcutRow]] = {}
    seen: set[ShortcutRow] = set()
    for entry in collect_menu_entries(window):
        if not entry.keys:
            continue
        row = ShortcutRow(KEY_SEPARATOR.join(entry.keys), action_label(entry))
        if row in seen:
            continue
        seen.add(row)
        groups.setdefault(entry.path[0], []).append(row)
    return [ShortcutGroup(title, tuple(rows)) for title, rows in groups.items()]


def action_label(entry: MenuEntry) -> str:
    """一覧の右の列に出す名前。

    いちばん上のメニュー名は見出しに出ているので落とし、畳んだ親（「ラフ」）
    が残っていれば道順として付ける（「ラフ → 読み込む...」）。

    **名前に入っているキーは消す。** 道具の項目は「コマ追加 (P)」の形なので
    （→ `_build_tool_actions`）、そのまま出すと左の列と合わせて2度出る。
    """
    text = entry.text
    for key in entry.keys:
        text = text.replace(f" ({key})", "")
    return PATH_SEPARATOR.join((*entry.path[1:], text))


class ShortcutsDialog(QDialog):
    """ショートカットキーの一覧。**作りっぱなしで使い回す**（→ `CheckResultDialog`）。

    **相手を止めない窓にする。** キーを確かめながら実際に押せるように、
    出したまま作品の側へ手が届く必要がある。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("ショートカットキーの一覧")
        self.setModal(False)
        self.resize(*DIALOG_SIZE)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["キー", "動作"])
        # 見出し（メニュー名）は開いた状態で出し、開閉の三角は出さない。
        # 畳めるようにしても、畳んだぶんだけ探す手数が増えるだけ
        self._tree.setRootIsDecorated(False)
        # **押しても何も起きない窓なので、選択の見た目も出さない。**
        # 行が青くなると、押せば何かできそうに見える
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)

        note = QLabel(
            "セリフを打っている間は、文字の入力が優先されます"
            "（打ち込んだキーが操作に化けません）。"
        )
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tree)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def show_groups(self, groups: list[ShortcutGroup]) -> None:
        """一覧を入れ替えて前に出す。開いていなければ開く。"""
        self._tree.clear()
        for group in groups:
            head = QTreeWidgetItem(self._tree, [group.title, ""])
            # 見出しは太字にする。キーの行と同じ見た目だと、どこで
            # メニューが変わったのかを目で追えない
            font = head.font(0)
            font.setBold(True)
            head.setFont(0, font)
            head.setFirstColumnSpanned(True)
            for row in group.rows:
                QTreeWidgetItem(head, [row.keys, row.action])
        self._tree.expandAll()
        self._tree.setColumnWidth(0, KEY_COLUMN_PX)
        self.show()
        self.raise_()
        self.activateWindow()


# メニューに添える説明（ホバー中の状態表示に出る → 7章）
SHORTCUTS_HINT = (
    "キーでできる操作を、メニューごとに並べる"
    "（メニューに出ないキーとマウスの操作も最後に付ける）"
)
