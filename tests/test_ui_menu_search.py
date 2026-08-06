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
    MenuEntry,
    collect_menu_entries,
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

    def test_言い換えの表は持たない(self, entries):
        """**わざと当たらない**（→ `menu_search` の説明）。

        表を持たない決まりを、後から黙って崩さないための歯止め。
        崩すなら、この1件を落としてからにする。
        """
        assert search(entries, "ふきだし") == []


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
