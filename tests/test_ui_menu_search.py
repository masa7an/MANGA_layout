"""メニューを探す窓の検証（要件定義 6.30）。

押さえたいのは4つ。

1. **道順が付くこと。** 「読み込む...」だけ出しても、どこから開けば
   いいのか分からない。この機能の値打ちは道順のほうにある
2. **説明からも当たること。** 項目名は短く保つ決まりなので（→ 6.12）、
   言葉で辿り着ける情報は説明に寄っている
3. **メニューを壊さないこと。** 一覧を作るのにメニューバーを辿るため、
   `QAction.menu()` の罠を踏むと、無関係な右クリックのメニューや
   `_refresh` が後から落ちる（→ `PySide6の落とし穴.md` の 1）
4. **開くたびに取り直すこと。** 文言は状態で変わる（→ 6.27）
"""

from __future__ import annotations

import gc

import pytest

from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.menu_search import (
    HIGHLIGHT_SECONDS,
    MISSING_FEATURES,
    NOT_IN_MENU,
    MenuEntry,
    collect_menu_entries,
    guide_notes,
    item_text,
    plain_label,
    search,
)
from manga_layout.ui.menus import ADJUSTING_MENU_LABELS, ROUGH_MENU_LABEL
from manga_layout.ui.state import TOOL_ROUGH


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def entries(window) -> list[MenuEntry]:
    return collect_menu_entries(window)


def trails(entries: list[MenuEntry]) -> list[str]:
    return [entry.trail for entry in entries]


class Test一覧を作る:
    def test_アクセスキーの印は落とす(self):
        assert plain_label("ファイル(&F)") == "ファイル"
        assert plain_label("開く...") == "開く..."

    def test_道順が付く(self, entries):
        assert "ファイル → 新規作成" in trails(entries)

    def test_畳んだ先の項目にも道順が付く(self, entries):
        """ラフは「ファイル」の下にもう1段畳んである（→ 6.23）。"""
        assert "ファイル → ラフ → 読み込む..." in trails(entries)

    def test_畳んだ見出しそのものは項目にしない(self, entries):
        """押しても開くだけなので、行き先として並べない。"""
        assert "ファイル → ラフ" not in trails(entries)

    def test_区切り線は項目にしない(self, entries):
        assert all(entry.text for entry in entries)

    def test_説明とショートカットも控える(self, entries):
        found = next(e for e in entries if e.trail == "ファイル → 開く...")
        assert "project.json" in found.tip
        assert found.shortcut == "Ctrl+O"

    def test_全部のメニューから拾う(self, entries):
        tops = {entry.path[0] for entry in entries}
        assert {"ファイル", "編集", "コマ", "画像", "ページ", "フキダシ"} <= tops

    def test_開くたびに文言を取り直す(self, window):
        """調整中は畳んだ親の名前が変わる（→ 6.27）。

        作り置きを使い回すと、ここが古い名前のままになる。
        """
        window.state.set_tool(TOOL_ROUGH)
        adjusting = ADJUSTING_MENU_LABELS[ROUGH_MENU_LABEL]
        assert f"ファイル → {adjusting} → 読み込む..." in trails(
            collect_menu_entries(window)
        )


class Test探す:
    def test_項目名で当たる(self, entries):
        assert "ファイル → 抜けチェック..." in trails(search(entries, "抜け"))

    def test_説明で当たる(self, entries):
        """「バックアップから復元...」の説明にだけ `backup/` が出てくる。"""
        assert "ファイル → バックアップから復元..." in trails(
            search(entries, "backup")
        )

    def test_道順で当たる(self, entries):
        """項目名にも説明にも無い言葉でも、親メニューの名前で辿り着ける。"""
        found = next(e for e in search(entries, "表示") if e.text == "拡大")
        assert "表示" not in f"{found.text}{found.tip}"

    def test_大文字小文字を区別しない(self, entries):
        assert trails(search(entries, "BACKUP")) == trails(search(entries, "backup"))

    def test_全角英数でも当たる(self, entries):
        """`casefold()` だけでは全角半角の違いを潰せない（2026-08-08 発見）。

        `_fold`（NFKC正規化＋casefold）を通す必要がある。
        """
        assert trails(search(entries, "ｐｎｇ")) == trails(search(entries, "png"))
        assert trails(search(entries, "ＢＡＣＫＵＰ")) == trails(
            search(entries, "backup")
        )

    def test_空白で区切ると全部を含むものに絞る(self, entries):
        both = search(entries, "ファイル 書き出し")
        assert both
        assert all(e.path[0] == "ファイル" for e in both)
        assert len(both) < len(search(entries, "書き出し")) + len(
            search(entries, "ファイル")
        )

    def test_全角の空白も区切りとして扱う(self, entries):
        assert trails(search(entries, "ファイル　書き出し")) == trails(
            search(entries, "ファイル 書き出し")
        )

    def test_空のときは全部返す(self, entries):
        """打ち始める前に、メニューの一覧として眺められる。"""
        assert trails(search(entries, "")) == trails(entries)
        assert trails(search(entries, "   ")) == trails(entries)

    def test_見つからないときは空(self, entries):
        assert search(entries, "そんな項目は無い") == []

    @pytest.mark.parametrize("written", ["吹き出し", "吹きだし", "ふきだし"])
    def test_フキダシの言い換えで当たる(self, entries, written):
        """本人が実際に打つ表記（→ `SYNONYMS`、本人の要望 2026-08-06）。"""
        hits = trails(search(entries, written))
        assert "フキダシ → 丸い_フキダシを追加 (B)" in hits  # 空同士の一致で通さない
        assert hits == trails(search(entries, "フキダシ"))

    def test_セリフの言い換えで当たる(self, entries):
        """「テキスト」で探しても「セリフ」に届く（本人の要望 2026-08-07）。"""
        hits = trails(search(entries, "テキスト"))
        assert "セリフ → セリフを追加 (T)" in hits
        assert hits == trails(search(entries, "セリフ"))

    @pytest.mark.parametrize("written", ["びっくり", "びっくりマーク"])
    def test_ビックリマークの言い換えで当たる(self, entries, written):
        """「びっくり」だけでも、「マーク」まで続けても当たる。

        読み替えた先を「ビックリマーク」まで伸ばすと、後者が
        「ビックリマークマーク」になって外れる（→ `SYNONYMS`）。
        """
        assert "マーク → ビックリマークを追加 (M)" in trails(search(entries, written))

    @pytest.mark.parametrize("written", ["コマ割", "コマ割り", "コマわり"])
    def test_コマの送り仮名付きでも当たる(self, entries, written):
        """日本語IMEが「コマ割り」までを一括で確定するため（本人の要望 2026-08-07）。

        長い言い換えを先に当てないと、「コマ割り」が「コマり」になって
        どこにも当たらない（→ `read_as`）。
        """
        assert trails(search(entries, written)) == trails(search(entries, "コマ"))
        assert "コマ → コマ追加 (P)" in trails(search(entries, written))

    @pytest.mark.parametrize("written", ["png", "PNG", "jpg", "psd", "エクスポート"])
    def test_拡張子や他のソフトの言い方で書き出しに届く(self, entries, written):
        """**大文字でも当たる。** 拡張子は大文字で打たれることが多い。

        `read_as` は casefold 済みの言葉を受けるので、表の鍵は小文字。
        """
        assert "ファイル → 画像で書き出し..." in trails(search(entries, written))

    @pytest.mark.parametrize(
        ("written", "trail"),
        [
            ("アンドゥ", "編集 → 元に戻す"),
            ("リドゥ", "編集 → やり直す"),
            ("コピー", "編集 → 複製"),
            ("ズーム", "表示 → 拡大"),
            ("書体", "セリフ → フォントを選ぶ..."),
            ("スピード線", "コマ → 流線 → 入れる"),
            ("尻尾", "フキダシ → しっぽを消す"),
        ],
    )
    def test_他のソフトの呼び名でも当たる(self, entries, written, trail):
        """このアプリの項目名はやまとことばに寄せてある（→ 6.12）。

        **先回りして足す側へ改めた**（本人の判断 2026-08-07）。定番の
        呼び名で0件になる時間がいちばん無駄になる。
        """
        assert trail in trails(search(entries, written))

    def test_言い換えは語の一部でも効く(self, entries):
        """「ふきだしを追加」のように、続けて打たれても読み替える。"""
        assert trails(search(entries, "ふきだしを追加")) == trails(
            search(entries, "フキダシを追加")
        )

    def test_表に無い言葉は読み替えない(self, entries):
        """**先回りで網羅しない**（→ `menu_search` の説明）。

        表を膨らませない決まりを、後から黙って崩さないための歯止め。
        崩すなら、この1件を落としてからにする。
        """
        assert search(entries, "ふきだしのしっぽ") == []
        assert search(entries, "こま") == []


class Test無い機能に無いと答える:
    """絵を描くソフトの定番機能で探した人に、0件とだけ返さない（→ 6.30）。"""

    @pytest.mark.parametrize(
        ("asked", "expected"),
        [
            ("ペン", "ペン入れ機能はありません。ペイントソフト側で行ってください"),
            ("消しゴム", "消しゴム機能はありません。ペイントソフト側で行ってください"),
            ("オノマトペ", "擬音機能はありません。ペイントソフト側で行ってください"),
            ("ぼかし", "その機能はありません。ペイントソフト側で行ってください"),
        ],
    )
    def test_定番の機能には無いと答える(self, asked, expected):
        assert guide_notes(asked) == [expected]

    @pytest.mark.parametrize("asked", ["擬音", "オノマトペ", "描き文字", "効果音"])
    def test_呼び名が違っても同じ答えに行き着く(self, asked):
        """4つとも「擬音機能はありません」に集める。"""
        assert guide_notes(asked) == guide_notes("擬音")

    def test_同じ答えは1回しか出さない(self):
        """「効果音のオノマトペ」でも、同じ文が2行並ばない。"""
        assert len(guide_notes("効果音のオノマトペ")) == 1

    def test_別の答えは並べて出す(self):
        assert len(guide_notes("ペンと消しゴム")) == 2

    @pytest.mark.parametrize(
        "asked",
        [
            "psd入力",
            "PSD読み込み",
            "psd読み込ませる",
            "PSDを入力するには？",
            "ｐｓｄを入力するには？",
        ],
    )
    def test_PSDは片道だけあると答える(self, asked):
        """大文字・全角の違いは潰す。**無いとは言わず、どちら向きかを答える。**"""
        assert guide_notes(asked) == ["PSD 機能は、【書き出し】のみとなっています"]

    @pytest.mark.parametrize(
        ("asked", "expected"),
        [
            ("トンボ", "印刷向けの機能はありません（ウェブ漫画用です）"),
            ("ノンブル", "印刷向けの機能はありません（ウェブ漫画用です）"),
            ("ルビ", "セリフの書式は、大きさ・太字・寄せ・縦横だけです"),
            ("行間", "セリフの書式は、大きさ・太字・寄せ・縦横だけです"),
            ("定規", "その機能はありません"),
        ],
    )
    def test_無い理由まで書ける群には専用の答えを返す(self, asked, expected):
        """**「ペイントソフトで」と言えないものがある。**

        定規やグリッドは向こうでやっても解決しないので、行き先を書けない
        ときは余計な誘導を付けずに無いとだけ言う。
        """
        assert guide_notes(asked) == [expected]

    def test_レイヤーは無いで済ませない(self):
        """編集中には無いが、PSD では分かれる（→ 10.1）。

        無いとだけ答えると、**クリスタへ渡す道があること自体**を
        知らないまま帰らせることになる。
        """
        assert guide_notes("レイヤー") == [
            "編集中にレイヤーはありません。PSD で書き出すとレイヤーに分かれます"
        ]

    def test_探す言葉でなく文で打たれても拾う(self):
        assert guide_notes("ペンはどこですか") == guide_notes("ペン")

    def test_表に無い言葉には何も答えない(self):
        """**言い換え表と同じ歯止め**。先回りで機能名を並べない。"""
        assert guide_notes("コマ") == []
        assert guide_notes("フキダシ") == []
        assert guide_notes("") == []

    def test_あるものを無いと言わない(self, entries):
        """表の言葉がメニューに実在したら、案内と矛盾する（→ 6.30）。

        トーンのように**実際にある機能**を後から表へ入れてしまうと、
        窓が嘘をつく。足すときはここで気づける。
        """
        for word in MISSING_FEATURES:
            found = [e for e in search(entries, word) if word in e.trail]
            assert not found, f"「{word}」はメニューに実在する: {trails(found)}"


class Testメニューに無いが在るものは行き先を答える:
    """右クリックからしか出ないものは、どう探しても0件になる（→ 6.30）。"""

    @pytest.mark.parametrize("asked", ["付箋", "ふせん", "付箋を貼りたい"])
    def test_付箋はサムネイルの右クリックへ案内する(self, asked):
        assert guide_notes(asked) == ["付箋は、サムネイルを右クリックしてください"]

    @pytest.mark.parametrize("asked", ["スクロール", "手のひら", "画面移動"])
    def test_画面の動かし方を答える(self, asked):
        """ホイールは拡大・縮小に取ってあるので、上下に動かす手が別にある。"""
        assert "スペースキー" in guide_notes(asked)[0]

    def test_設定の在り処を答える(self):
        assert guide_notes("環境設定") == ["設定は settings.bat から変えられます"]

    def test_右クリックが本体だと教える(self):
        assert guide_notes("右クリック") == ["多くの操作は、対象を右クリックすると出ます"]

    def test_一覧では見つからないから案内が要る(self, entries):
        """メニューバーを辿るだけでは届かないことが、この表の前提。"""
        for word in NOT_IN_MENU:
            assert search(entries, word) == []

    def test_あるほうを先に出す(self):
        """行き先のある答えのほうが、次にやることに直に繋がる。"""
        notes = guide_notes("付箋とペン")
        assert len(notes) == 2
        assert notes[0].startswith("付箋")

    def test_無い機能の表とは別に持つ(self):
        """混ぜると、実在しないことを確かめる歯止め（上）が付箋で落ちる。"""
        assert not set(NOT_IN_MENU) & set(MISSING_FEATURES)


class Test出しかた:
    def test_道順と説明を2段に分ける(self):
        entry = MenuEntry(("ファイル",), "開く...", "説明", "Ctrl+O")
        head, tip = item_text(entry).split("\n")
        assert head == "ファイル → 開く...　［Ctrl+O］"
        assert tip.strip() == "説明"

    def test_名前にキーが入っている項目には足さない(self):
        """道具の項目は名前が「コマ追加 (P)」の形（→ 6.14）。"""
        entry = MenuEntry(("道具",), "コマ追加 (P)", "", "P")
        assert item_text(entry) == "道具 → コマ追加 (P)"

    def test_説明が無ければ1行(self):
        entry = MenuEntry(("表示",), "拡大", "", "")
        assert item_text(entry) == "表示 → 拡大"

    def test_窓は一覧を入れ替えて開ける(self, window, entries):
        window.search_menu()
        dialog = window._menu_search_dialog
        assert dialog is not None
        assert dialog._list.count() == len(entries)

        # 押し直しても窓は増やさない（→ `CheckResultDialog` と同じ）
        window.search_menu()
        assert window._menu_search_dialog is dialog
        dialog.close()

    def test_打ち込むと絞られる(self, window):
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog._field.setText("抜け")
        assert dialog._list.count() == 1
        assert "抜けチェック..." in dialog._list.item(0).text()
        dialog.close()

    def test_無い機能は案内を出し別の言葉で探せと言わない(self, window):
        """0件のまま「別の言葉で探して」と返すと、無いものを探し続けさせる。"""
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog._field.setText("消しゴム")
        assert dialog._list.count() == 0
        assert "消しゴム機能はありません" in dialog._guide.text()
        assert "別の言葉" not in dialog._count.text()
        dialog.close()

    def test_案内は打ち直すと消える(self, window):
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog._field.setText("ペン")
        assert dialog._guide.text()
        dialog._field.setText("コマ")
        assert dialog._guide.text() == ""
        assert dialog._list.count() > 0
        dialog.close()

    def test_案内を出しても一覧は消さない(self, window):
        """「PSD」は書き出しの説明に出てくる。案内は上に足すだけ。"""
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog._field.setText("PSD")
        assert "【書き出し】" in dialog._guide.text()
        assert dialog._list.count() > 0
        dialog.close()


class Test押した項目のメニューを囲む:
    """押すと、そのメニューの見出しが画面上端で四角く囲まれる（→ 6.30）。

    **枠の見た目は自動では確かめられない。** ここで押さえるのは、
    どこを囲むかの座標と、押してから枠が出るまでの配線。
    """

    def test_見出しの位置を囲む(self, window):
        window.highlight_menu("画像")
        bar = window.menuBar()
        action = next(a for a in bar.actions() if plain_label(a.text()) == "画像")
        # `isVisible` ではなく `isHidden` で見る。窓自体を表示していない
        # テストでは、出したはずの子まで「見えていない」になる
        assert not window._menu_highlight.isHidden()
        assert window._menu_highlight.geometry() == bar.actionGeometry(action)

    def test_別のメニューを押すと囲み直す(self, window):
        window.highlight_menu("画像")
        first = window._menu_highlight.geometry()
        window.highlight_menu("ページ")
        assert window._menu_highlight.geometry() != first

    def test_知らない名前は黙って何もしない(self, window):
        window.highlight_menu("そんなメニューは無い")
        assert window._menu_highlight is None

    def test_しばらくすると消える(self, window):
        """消し方はタイマー任せ。**出しっぱなしにしない**ことだけ確かめる。"""
        window.highlight_menu("画像")
        timer = window._menu_highlight_timer
        assert timer.isSingleShot()
        assert timer.isActive()
        assert timer.interval() == int(HIGHLIGHT_SECONDS * 1000)
        timer.timeout.emit()  # 時間切れの代わり
        assert window._menu_highlight.isHidden()

    def test_一覧を押すと囲むところまで繋がっている(self, window):
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog._field.setText("トーン範囲を調整")
        item = dialog._list.item(0)
        assert "画像 → トーン" in item.text()

        dialog._list.itemClicked.emit(item)
        bar = window.menuBar()
        action = next(a for a in bar.actions() if plain_label(a.text()) == "画像")
        assert window._menu_highlight.geometry() == bar.actionGeometry(action)
        dialog.close()

    def test_窓はメニューバーに重ならない位置に出る(self, window):
        """囲んだ枠が窓の下に隠れては意味が無い（→ `_place_once`）。"""
        window.show()
        window.search_menu()
        dialog = window._menu_search_dialog
        bar = window.menuBar()
        bar_bottom = bar.mapToGlobal(bar.rect().bottomLeft()).y()
        assert dialog.frameGeometry().top() > bar_bottom
        dialog.close()

    def test_2回目からは置き直した場所を動かさない(self, window):
        window.search_menu()
        dialog = window._menu_search_dialog
        dialog.move(0, 0)  # 使う人が動かしたつもり
        window.search_menu()
        assert dialog.pos().x() == 0 and dialog.pos().y() == 0
        dialog.close()


class Testメニューを壊さない:
    """一覧を作った**あと**で、メニューが今まで通り使えること。

    `QAction.menu()` でメニューを辿ると、辿った側の後片付けに巻き込まれて
    アプリ側の参照が無効になる（→ `PySide6の落とし穴.md` の 1）。
    症状が出るのは辿った場所ではなく、無関係な別の場所。
    """

    def test_辿ったあとも画面の更新が通る(self, window):
        collect_menu_entries(window)
        gc.collect()
        window._refresh()  # 畳んだメニューの名前を書き換える（→ 6.27）

    def test_辿ったあとも右クリックのメニューが組める(self, window):
        collect_menu_entries(window)
        gc.collect()
        menu = window.context_menu.build(100.0, 100.0)
        assert menu.actions()
