"""メニューの項目を言葉で探す窓（要件定義 6.30）。

**出すのは「どこにあるか」だけで、押しても実行しない。** 辿り着いた先で
メニューを開き直してもらう。ここから直に実行できるようにすると、
選んでいるものによって押せない項目をこの窓でももう一度説明することになる
（メニュー側は灰色で示している → 6.12）。

**項目名だけでなく、項目に添えた説明も探す。** 項目名は読む字数を減らす側に
倒してあり（→ 6.12）、言葉で辿り着ける情報は説明のほう（カーソルを乗せた
間だけ画面下に出るあの文）に寄っている。

**言い換えは、実際に出てこなかった言葉だけを足す**（→ `SYNONYMS`）。
先回りで表を作ると、使われない言い換えまで抱え込んだうえ、項目を増やす
たびに足す手間が残る。表に無い言葉は今も単純な文字の一致だけで探す。

**一覧が0件になる問いには、一覧の外から答える**（→ `guide_notes`）。
絵を描くソフトなら必ずある言葉（「ペン」「消しゴム」など）は**無いと答え**
（→ `MISSING_FEATURES`）、右クリックからしか出ないもの（「付箋」）は
**行き先を答える**（→ `NOT_IN_MENU`）。どちらも一覧では0件になるが、
0件とだけ返すと**無いのか、呼び名が違うだけなのかが分からない**まま
探し続けることになる。

押した項目については、**そのメニューを画面上端で四角く囲む**
（→ `MainWindow.highlight_menu`）。実行はしないまま、最初に開く場所だけ
名指しする。
"""

from __future__ import annotations

import re
import unicodedata
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

# 打ち込まれた言葉の言い換え（左）を、アプリが使っている表記（右）に読み替える。
#
# **最初は「実際に打って出てこなかった言葉だけ」に絞っていたが、
# 先回りする側へ改めた**（本人の判断 2026-08-07）。時間を無駄にしないための
# アプリなのに、**定番の呼び名で0件になる時間**がいちばん無駄になる。
# 外しても実害は「使われない行が1つ残る」だけで、当たれば数分が浮く。
#
# ただし**闇雲には増やさない。** 足すのは、他のソフトで通っている呼び名で
# 打たれる見込みがあり、かつ**今このアプリで0件になる**言葉に限る。
#
# **アプリ側の表記は増やさない。** 表に出る「フキダシ」は
# `BALLOON_STYLE_LABELS` と `BALLOON_PLACE_HERE_NAME` の2箇所だけという
# 決まりがある（→ 6.12）。ここは**探すときだけの読み替え**で、
# 画面に出る文言には触らない。
#
# **読み替えた先は、途中まででよい。** 「びっくり」を「ビックリマーク」まで
# 伸ばすと、「びっくりマーク」と打たれたときに「ビックリマークマーク」になって
# 外れる。項目名に含まれるところまで（「ビックリ」）に留めれば、どちらの
# 打ち方でも当たり、「ビックリはてなマーク」にも一緒に届く。
#
# 語の一部としても効く（「ふきだしを追加」→「フキダシを追加」）
SYNONYMS = {
    "吹き出し": "フキダシ",
    "吹きだし": "フキダシ",
    "ふきだし": "フキダシ",
    "テキスト": "セリフ",
    "びっくり": "ビックリ",
    # 「コマ」だけなら元から当たるが、日本語IMEは「コマ割り」までを
    # ひとまとまりで確定するので、送り仮名が付いた形が打ち込まれる
    # （本人の要望 2026-08-07）。**送り仮名を落とすだけの読み替え**
    "コマ割り": "コマ",
    "コマ割": "コマ",
    "コマわり": "コマ",
    # 書き出し。**拡張子で打たれる**（「pngにしたい」）ので、そのまま行き先へ。
    # `read_as` は casefold 済みの言葉を受けるので、鍵は小文字で書く
    "png": "書き出し",
    "jpg": "書き出し",
    "jpeg": "書き出し",
    "psd": "書き出し",
    "エクスポート": "書き出し",
    "出力": "書き出し",
    # 他のソフトの呼び名（このアプリは全部やまとことばで書いてある）
    "アンドゥ": "元に戻す",
    "取り消し": "元に戻す",
    "リドゥ": "やり直す",
    "セーブ": "保存",
    "ズーム": "拡大",
    "表示倍率": "拡大",
    "書体": "フォント",
    "検索": "探す",
    # このアプリに `Ctrl+C` は無く、同じ用が足りるのは「複製」
    "コピー": "複製",
    "複写": "複製",
    # キーの一覧（→ `shortcuts.py`）。「ショートカット」「キー」は項目名に
    # 入っているので元から当たる。当たらない呼び名だけを足す
    "ホットキー": "ショートカット",
    "キーボード": "ショートカット",
    "アクセラレータ": "ショートカット",
    # マンガの用語のゆれ
    "スピード線": "流線",
    "尻尾": "しっぽ",
}


# 無い機能を尋ねられたときの答え。**言い換え表とは別に持つ。**
#
# `SYNONYMS` は「同じものの別の呼び名」を結ぶ表で、こちらは**結ぶ先が無い**
# ことを答える表。混ぜると、読み替えた結果がたまたま当たらなかっただけの
# 場合と、そもそも機能が無い場合を区別できなくなる。
#
# **並ぶのは、README を読まずに触った人が最初に打つ言葉**（本人の要望
# 2026-08-07）。このアプリはコマ割りとフキダシの配置だけを受け持ち、絵は
# 描かないが、**そう書いてあるのは README のほう**で、探した人には届かない。
# 0件とだけ返すと「無いのか、呼び名が違うだけなのか」が分からず探し続ける。
#
# **当たっても項目の一覧は消さない。** 案内は上に足すだけで、文字が
# たまたま一致した項目はそのまま並べる（「PSD」は書き出しの説明に出てくる）。
PAINT_SOFT = "ペイントソフト側で行ってください"
_NO_FEATURE = f"その機能はありません。{PAINT_SOFT}"

# **「ペイントソフトで」と言えないものもある。** 置き場所の補助（定規・
# グリッド）や一括処理は、向こうでやっても解決しない。行き先を書けない
# ときは、余計な誘導を付けずに無いとだけ言う
_NO_FEATURE_PLAIN = "その機能はありません"

# 印刷向け。README の「ウェブ漫画用で、印刷は考慮していません」に対応する
_PRINT = "印刷向けの機能はありません（ウェブ漫画用です）"

# セリフの書式は `セリフ` メニューに出ている範囲だけ
_TEXT_FORMAT = "セリフの書式は、大きさ・太字・寄せ・縦横だけです"

MISSING_FEATURES = {
    "ペン": f"ペン入れ機能はありません。{PAINT_SOFT}",
    "消しゴム": f"消しゴム機能はありません。{PAINT_SOFT}",
    # 呼び名が4つあるが、答えは1つ（同じ文面は1回しか出さない → `guide_notes`）
    "擬音": f"擬音機能はありません。{PAINT_SOFT}",
    "オノマトペ": f"擬音機能はありません。{PAINT_SOFT}",
    "描き文字": f"擬音機能はありません。{PAINT_SOFT}",
    "効果音": f"擬音機能はありません。{PAINT_SOFT}",
    "ブラシ": _NO_FEATURE,
    "レイヤー効果": _NO_FEATURE,
    "レイヤー結合": _NO_FEATURE,
    "AI生成": _NO_FEATURE,
    "ベクター": _NO_FEATURE,
    "フィルター": _NO_FEATURE,
    "色調補正": _NO_FEATURE,
    "印刷": _NO_FEATURE,
    "グラデーション": _NO_FEATURE,
    "図形": _NO_FEATURE,
    "ぼかし": _NO_FEATURE,
    "シャープ": _NO_FEATURE,
    # PSD は**片道だけある**ので、無いとは言わずにどちら向きかを答える。
    # 大文字・全角は下で潰すので、この1件で「PSD」「ｐｓｄ」まで届く
    "psd": "PSD 機能は、【書き出し】のみとなっています",
    # 「レイヤー」だけは**ありませんで済ませない。** 編集中には無いが、
    # PSD には分けて書き出す（→ 10.1）。無いとだけ答えると、
    # クリスタへ渡す道があること自体を知らないまま帰らせる
    "レイヤー": "編集中にレイヤーはありません。PSD で書き出すとレイヤーに分かれます",
    # -- 印刷・製本（README「ウェブ漫画用で、印刷は考慮していません」） ----
    "トンボ": _PRINT,
    "裁ち落とし": _PRINT,
    "タチキリ": _PRINT,
    "ノンブル": _PRINT,
    "ページ番号": _PRINT,
    "見開き": _PRINT,
    "表紙": _PRINT,
    "入稿": _PRINT,
    "dpi": _PRINT,
    "解像度": _PRINT,
    # -- 文字組み -------------------------------------------------------------
    "ルビ": _TEXT_FORMAT,
    "縦中横": _TEXT_FORMAT,
    "行間": _TEXT_FORMAT,
    "字間": _TEXT_FORMAT,
    "禁則": _TEXT_FORMAT,
    # -- 描く・加工する（ペイントソフト側でできる） ---------------------------
    "塗りつぶし": _NO_FEATURE,
    "不透明度": _NO_FEATURE,
    "縁取り": _NO_FEATURE,
    "白フチ": _NO_FEATURE,
    "手ぶれ補正": _NO_FEATURE,
    "パース定規": _NO_FEATURE,
    "マスク": _NO_FEATURE,
    "切り抜き": _NO_FEATURE,
    "トリミング": _NO_FEATURE,
    "左右反転": _NO_FEATURE,
    # -- 置き場所の補助（向こうでやっても解決しない） -------------------------
    "グリッド": _NO_FEATURE_PLAIN,
    "スナップ": _NO_FEATURE_PLAIN,
    "ガイド": _NO_FEATURE_PLAIN,
    "定規": _NO_FEATURE_PLAIN,
    "整列": _NO_FEATURE_PLAIN,
    "等間隔": _NO_FEATURE_PLAIN,
    # -- その他 ---------------------------------------------------------------
    "テンプレート": _NO_FEATURE_PLAIN,
    "素材": _NO_FEATURE_PLAIN,
    "スタンプ": _NO_FEATURE_PLAIN,
    "置換": _NO_FEATURE_PLAIN,
    "プラグイン": _NO_FEATURE_PLAIN,
    "マクロ": _NO_FEATURE_PLAIN,
    "一括": _NO_FEATURE_PLAIN,
    "クラウド": _NO_FEATURE_PLAIN,
    "同期": _NO_FEATURE_PLAIN,
    "動画": _NO_FEATURE_PLAIN,
    "gif": _NO_FEATURE_PLAIN,
}

# **あるが、メニューには無い**機能の行き先（本人の要望 2026-08-07）。
#
# この窓はメニューバーを辿って一覧を作るので（→ `collect_menu_entries`）、
# 右クリックからしか出ないものは**どう探しても0件になる**。無い機能と
# 同じ見え方をするのに、答えは正反対（「ありません」ではなく「そこにある」）
# なので、表も分ける。
#
# **`MISSING_FEATURES` に混ぜてはいけない。** あちらは「メニューにも
# 実在しないこと」を歯止めのテストで確かめている表で、こちらは実在する。
_PAN = "画面の移動は、スペースキーを押しながらドラッグ（中ボタンのドラッグでも動きます）"

NOT_IN_MENU = {
    "付箋": "付箋は、サムネイルを右クリックしてください",
    "ふせん": "付箋は、サムネイルを右クリックしてください",
    # ホイールは拡大・縮小に取ってあるので、上下に動かす手が別にある
    # （→ `MangaCanvas.wheelEvent`）。メニューには1つも出ていない
    "スクロール": _PAN,
    "手のひら": _PAN,
    "画面移動": _PAN,
    "パン": _PAN,
    # 設定の窓はメニューから出さず、`settings.bat` に分けてある（→ 6.28）
    "設定": "設定は settings.bat から変えられます",
    "オプション": "設定は settings.bat から変えられます",
    # このアプリは右クリックが本体（→ README の冒頭）。メニューバーだけを
    # 見ている人に、いちばん短い近道を教える
    "右クリック": "多くの操作は、対象を右クリックすると出ます",
}


def guide_notes(query: str) -> list[str]:
    """打った言葉に対する案内（→ `NOT_IN_MENU`、`MISSING_FEATURES`）。

    **「ある」ほうを先に出す。** 行き先のある答えのほうが、探している人が
    次にやることに直に繋がる。

    **文の中に混じっていても拾う。**「psdを入力するには？」のように、
    探す言葉ではなく質問の形で打たれることを見込んでいる。

    大文字小文字と全角半角は区別しない（「PSD」「ｐｓｄ」も同じ）。
    同じ答えが2つ並ばないよう、**文面**で重複を落とす。
    """
    asked = _fold(query)
    notes: list[str] = []
    for word, note in (*NOT_IN_MENU.items(), *MISSING_FEATURES.items()):
        if _fold(word) in asked and note not in notes:
            notes.append(note)
    return notes


def _fold(text: str) -> str:
    """大文字小文字・全角半角の違いを潰す。"""
    return unicodedata.normalize("NFKC", text).casefold()


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
    # 同じ項目に通してある2本目以降のキー（「やり直す」の Ctrl+Shift+Z、
    # 「メニューを探す」の Ctrl+F → 7章）。**探すときには使わないが、
    # ショートカットの一覧には全部出す**（→ `shortcuts.py`）。
    # `shortcut` だけだと、2本通してあることが一覧に出ない
    alt_shortcuts: tuple[str, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        """この項目に通してあるキー全部。無ければ空。"""
        return (self.shortcut, *self.alt_shortcuts) if self.shortcut else ()

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
        # **`shortcut()` ではなく `shortcuts()` を読む。** 前者は1本目しか
        # 返さないので、2本通してある項目（→ `MenuEntry.alt_shortcuts`）の
        # 片方が落ちる
        keys = [
            key.toString(QKeySequence.SequenceFormat.NativeText)
            for key in action.shortcuts()
        ]
        entries.append(
            MenuEntry(
                path=path,
                text=label,
                tip=action.statusTip(),
                shortcut=keys[0] if keys else "",
                alt_shortcuts=tuple(keys[1:]),
            )
        )


def read_as(word: str) -> str:
    """言い換えをアプリの表記に読み替える（→ `SYNONYMS`）。表に無ければそのまま。

    **casefold 済みの言葉を受け取る**（→ `search`）。「PNG」「Png」でも
    同じ行に当てるためで、`SYNONYMS` の鍵は小文字で書く決まりになる。

    **長い言い換えから先に当てる。** 短いほうを先に当てると、長いほうの
    前半だけが持っていかれる（「コマ割り」に「コマ割」が先に当たると
    「コマり」になって、どこにも当たらなくなる）。表に書いた順に
    引きずられないよう、ここで並べ替える。
    """
    for written in sorted(SYNONYMS, key=len, reverse=True):
        word = word.replace(written, SYNONYMS[written])
    return word


def search(entries: list[MenuEntry], query: str) -> list[MenuEntry]:
    """打ち込んだ言葉を含む項目を返す。**表に載せた言い換え以外は文字の一致だけ。**

    空白で区切ると、全部を含むものに絞る（「フキダシ しっぽ」）。
    全角の空白も区切りとして扱う——日本語を打っている最中に半角へ
    切り替える手間を、絞り込みのために課さない。

    空のときは全部返す。**この窓をメニューの一覧としても使えるように
    するため**で、打ち始める前に何があるかを眺められる。
    """
    words = [read_as(word.casefold()) for word in query.replace("　", " ").split()]
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

        # 一覧では答えられないことへの案内（→ `guide_notes`）。**一覧の上に置く。**
        # 下に置くと、0件の一覧を見た時点で窓を閉じられて読まれない。
        # **灰色にしない**（下の `note` と違い、これは読ませたい答え）
        self._guide = QLabel()
        self._guide.setWordWrap(True)
        self._guide.hide()

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
        layout.addWidget(self._guide)
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
        query = self._field.text()
        self._hits = search(self._entries, query)
        self._list.clear()
        for entry in self._hits:
            self._list.addItem(item_text(entry))

        notes = guide_notes(query)
        self._guide.setText("\n".join(notes))
        self._guide.setVisible(bool(notes))

        if self._hits:
            self._count.setText(f"{len(self._hits)} 件")
        else:
            # **無いと答えたときは「別の言葉で探して」と言わない。**
            # 言い直しても出てこないものを、探し続けさせることになる
            self._count.setText(
                "見つかりません" if notes else "見つかりません（別の言葉で探してください）"
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
