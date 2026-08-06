"""ショートカットキーの一覧の検証（要件定義 7章）。

押さえたいのは4つ。

1. **メニューから作れていること。** 手で書いた表を出しているなら、この
   機能はキーを1つ変えた日から嘘をつき始める（→ `shortcuts.py` の冒頭）
2. **2本通してあるキーが両方出ること。** 「やり直す」（Ctrl+Y /
   Ctrl+Shift+Z）と「メニューを探す」（F1 / Ctrl+F）は、片方だけ出すと
   もう片方を知らないまま帰らせる
3. **同じキーが二度出ないこと。** 道具の項目は複数のメニューに置いてある
   （同じ QAction の使い回し → `_build_tool_actions`）
4. **手で書いた行がメニュー側と食い違わないこと。** `EXTRA_GROUPS` は
   このアプリで唯一の書き写しなので、ここだけ見張りを付ける
"""

from __future__ import annotations

import pytest

from manga_layout.ui import EditorState, MainWindow
from manga_layout.ui.menu_search import MenuEntry
from manga_layout.ui.shortcuts import (
    EXTRA_GROUPS,
    ShortcutsDialog,
    action_label,
    collect_groups,
    menu_groups,
)


@pytest.fixture
def window(qapp):
    win = MainWindow(EditorState())
    yield win
    win.state.history.mark_saved()
    win.close()


@pytest.fixture
def groups(window):
    return collect_groups(window)


def rows_of(groups, title: str):
    return next(group.rows for group in groups if group.title == title)


def pairs(groups) -> list[tuple[str, str]]:
    return [(row.keys, row.action) for group in groups for row in group.rows]


class Test一覧を作る:
    def test_メニューのキーを拾う(self, groups):
        assert ("Ctrl+S", "保存") in pairs(groups)

    def test_キーの無い項目は並べない(self, window):
        """「抜けチェック...」はキーを付けていない（→ 6.29）。"""
        actions = [action for _, action in pairs(menu_groups(window))]
        assert "抜けチェック..." not in actions

    def test_見出しはメニュー名(self, groups):
        titles = [group.title for group in groups]
        assert "ファイル" in titles
        assert titles.index("ファイル") < titles.index("編集")

    def test_2本通してあるキーは両方出す(self, groups):
        assert ("Ctrl+Y / Ctrl+Shift+Z", "やり直す") in pairs(groups)
        assert ("F1 / Ctrl+F", "メニューを探す...") in pairs(groups)

    def test_同じキーが二度出ない(self, window):
        """道具の項目は道具メニューと各メニューの両方に置いてある。"""
        found = pairs(menu_groups(window))
        assert len(found) == len(set(found))

    def test_道具のキーも並ぶ(self, window):
        assert ("P", "コマ追加") in pairs(menu_groups(window))

    def test_名前に入っているキーは消す(self):
        """道具は「コマ追加 (P)」の形。左の列と合わせて2度出さない。"""
        entry = MenuEntry(("道具",), "コマ追加 (P)", "", "P")
        assert action_label(entry) == "コマ追加"

    def test_いちばん上のメニュー名は見出しへ逃がす(self):
        entry = MenuEntry(("ファイル", "ラフ"), "読み込む...", "", "Ctrl+R")
        assert action_label(entry) == "ラフ → 読み込む..."


class Testメニューに無いキー:
    def test_後ろに足す(self, groups):
        assert [group.title for group in groups][-3:] == [
            group.title for group in EXTRA_GROUPS
        ]

    def test_画面側で拾うキーが並ぶ(self, groups):
        keys = [row.keys for row in rows_of(groups, "画面（メニューに無いキー）")]
        assert "+ / -" in keys
        assert "Shift+] / Shift+[" in keys

    def test_マウスの操作も並ぶ(self, groups):
        rows = rows_of(groups, "マウス")
        assert any(row.keys == "右クリック" for row in rows)
        assert any("画面を動かす" in row.action for row in rows)

    def test_メニューにあるキーを書き写していない(self, window):
        """**この一覧で唯一の書き写しなので、ここだけ見張る。**

        メニューに項目があるものを手で書き足すと、キーを変えたときに
        片方だけ古くなる（→ `EXTRA_GROUPS`）。
        """
        from_menu = {key for key, _ in pairs(menu_groups(window))}
        by_hand = {row.keys for group in EXTRA_GROUPS for row in group.rows}
        assert not (from_menu & by_hand)


class Test窓:
    def test_押すまで作らない(self, window):
        """一度も使わない人のぶんの窓を、起動のたびに作らない。"""
        assert window._shortcuts_dialog is None

    def test_開くと一覧が入る(self, window):
        window.show_shortcuts()
        tree = window._shortcuts_dialog._tree
        assert tree.topLevelItemCount() == len(collect_groups(window))
        assert tree.topLevelItem(0).childCount() > 0

    def test_二度開いても窓は1つ(self, window):
        window.show_shortcuts()
        first = window._shortcuts_dialog
        window.show_shortcuts()
        assert window._shortcuts_dialog is first
        # 行が二重にならない（開くたびに入れ替える）
        assert first._tree.topLevelItemCount() == len(collect_groups(window))

    def test_相手を止めない窓(self, window):
        """出したままメニューへ手が届く必要がある。"""
        window.show_shortcuts()
        assert not window._shortcuts_dialog.isModal()

    def test_窓は単体でも組める(self, qapp):
        ShortcutsDialog()


class Testメニューから辿れる:
    def test_ヘルプに項目がある(self, window):
        from manga_layout.ui.menu_search import collect_menu_entries, search

        entries = collect_menu_entries(window)
        found = search(entries, "ショートカット")
        assert any(e.trail == "ヘルプ → ショートカットキーの一覧..." for e in found)

    def test_ホットキーでも当たる(self, window):
        from manga_layout.ui.menu_search import collect_menu_entries, search

        found = search(collect_menu_entries(window), "ホットキー")
        assert any("ショートカットキーの一覧..." == e.text for e in found)
