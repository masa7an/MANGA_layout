"""メニューバーの各メニューの部品。1ドメイン＝1クラス。

各クラスが「QAction の生成」「メニューの組み立て」「有効・無効と文言の更新
（`refresh`）」「右クリック用の控え（`copy_items`）」を1つに持つ。作る側と
使う側が同じクラスに同居するので、作り忘れや組み立て順の入れ替えは部品の
生成時にその場で露見する。かつては `_build_*` と `_refresh` が離れていて、
最初の画面更新まで気づけなかった（→ `canvas.Drag` と同じ切り出し方）。

守ること2つ:

- **QAction は必ず `window._act(...)` で作る**（親＝窓＋`window.addAction()`）。
  部品自身を親にすると、メニューを開いていない間のショートカットが
  黙って効かなくなる。
- **QMenu の参照を持たない。** 組んだらメニューバーへ渡し切る
  （理由は `items_to_copy`）。持ってよいのは QAction と `copy_items` だけ。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from .state import STICKER_TOOLS, TOOL_TEXT

if TYPE_CHECKING:
    from .window import MainWindow


def items_to_copy(
    source: QMenu, exclude: Iterable[QAction] = ()
) -> list[QAction | None]:
    """メニューバーのメニューから、右クリック側へ写す項目を控えておく。

    **QMenu も、そのメニューが持つ区切り線も持ち帰らない。**
    持てるのは `_act` で作った QAction（親がウィンドウ）だけで、
    区切り線は `None` に置き換える。

    PySide6 では `QAction.menu()` を呼ぶと、その QMenu が呼び出し側の
    QAction に引き取られる。QAction を使い捨てにする書き方
    （`menuBar().actions()[0].menu()` のような1行）だと、それが
    片付いた時点で QMenu の Python 側の参照が無効になり、以後
    `RuntimeError: Internal C++ object already deleted` になる。
    C++ の実体はメニューバーの下で生きたままなので、画面のメニューは
    普通に開ける。**壊れるのは Python 側の参照だけ**で、区切り線の
    QAction（このメニューが親）も道連れになる。

    2026-08-04、テストがメニューバーを辿った直後に右クリックの
    メニューが RuntimeError で組めなくなることを実機で確認した。
    アプリ側は `QAction.menu()` を呼んでいないため表には出ていない。
    呼ばないという申し合わせで守るのは無理があるので、
    壊れようのないものだけを持つ形にした。

    `exclude` に渡すのは、写したくない項目。**道具の切り替え
    （「フキダシを追加」など）は必ず入れる。** 右クリック側は押した場所が
    分かっているので「ここに〜」を別に出しており、道具に持ち替える項目まで
    並べると、同じことが2通り並ぶ。
    """
    dropped = set(exclude)
    items: list[QAction | None] = []
    for action in source.actions():
        if action in dropped:
            continue
        items.append(None if action.isSeparator() else action)
    return items


class FocusMenu:
    """集中線のメニュー（要件定義 6.16）。**コマのメニューの下に畳む。**

    7項目あるので、コマのメニューへ並べると縦に伸びて読む字数が増える。
    入れていないコマでは「入れる」1つしか使えないので、畳んでおくほうが
    目に入る量が少ない。

    **キーは1つも割り当てない。** この道具はマウスで使うもので、キーを
    足すこと自体に値打ちは無い（→ 要件定義 7章）。
    """

    def __init__(self, window: MainWindow, parent_menu: QMenu) -> None:
        self._state = window.state
        menu = QMenu("集中線", window)
        # 「入れる」と「消す」は1項目の文言を入れ替える（しっぽと同じ）。
        # 2つ並べると、片方は必ず押せない状態で場所だけ取る
        self.toggle_action = window._act(
            "入れる",
            window.toggle_focus_lines,
            None,
            "選んだコマに放射状の線を引く。中心はあとから動かせる",
        )
        menu.addAction(self.toggle_action)
        menu.addSeparator()

        # ここから下は集中線の入ったコマにだけ効く
        self.actions: list[QAction] = []

        def add(label: str, slot, tip: str = "") -> QAction:
            action = window._act(label, slot, None, tip)
            menu.addAction(action)
            self.actions.append(action)
            return action

        add("線を増やす", lambda: window.state.step_focus_count(1))
        add("線を減らす", lambda: window.state.step_focus_count(-1))
        menu.addSeparator()
        add("線を太く", lambda: window.state.step_focus_width(1))
        add("線を細く", lambda: window.state.step_focus_width(-1))
        menu.addSeparator()
        # 「白にする／黒に戻す」も入れる／消すと同じく1項目の文言を入れ替える
        # （要件定義 6.19。単純な色違いなので、色を選ぶメニューにはしない）
        self.color_action = add(
            "白にする",
            window.toggle_focus_color,
            "線の色を黒と白で切り替える。暗いコマの上で使う",
        )
        menu.addSeparator()
        add(
            "形を振り直す",
            window.state.reseed_focus,
            "中心・本数・太さはそのままで、線のばらつきだけを作り直す",
        )

        # 右クリックのメニューが写して使う（→ `items_to_copy`）
        self.copy_items = items_to_copy(menu, window._tool_actions.values())
        parent_menu.addMenu(menu)

    def refresh(self) -> None:
        # 入れる／消すはコマを選んでいれば押せ、調整の5項目は
        # 入っているときだけ（→ 6.16）
        focus = self._state.selected_focus
        self.toggle_action.setEnabled(self._state.selected_panel is not None)
        self.toggle_action.setText("消す" if focus is not None else "入れる")
        for action in self.actions:
            action.setEnabled(focus is not None)
        if focus is not None:
            self.color_action.setText("黒に戻す" if focus.white else "白にする")


class TextMenu:
    """セリフのメニュー。

    先頭に「作る」を置く。ここが選択中のセリフへの操作だけだと、
    1つも選んでいない間はメニュー全体がグレーになり、
    どこから作るのか分からなくなる（吹き出しで一度やった失敗）。
    """

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("セリフ(&X)")
        menu.addAction(window._tool_actions[TOOL_TEXT])
        menu.addSeparator()

        self.actions: list[QAction] = []

        def add(label: str, slot, shortcut: str | None = None) -> QAction:
            action = window._act(label, slot, shortcut)
            menu.addAction(action)
            self.actions.append(action)
            return action

        add("文字を入力...", window.edit_text, "F2")
        menu.addSeparator()

        self.vertical_action = add("縦書き", window.toggle_vertical, "F7")
        self.vertical_action.setCheckable(True)
        menu.addSeparator()

        for label, align in (("左寄せ", "left"), ("中央寄せ", "center"), ("右寄せ", "right")):
            add(label, lambda _=False, a=align: window.set_text_align(a))
        menu.addSeparator()

        add("大きく", lambda: window.step_text_size(1), "Ctrl+]")
        add("小さく", lambda: window.step_text_size(-1), "Ctrl+[")
        self.bold_action = add("太字", window.toggle_bold, "Ctrl+B")
        self.bold_action.setCheckable(True)
        menu.addSeparator()
        add("フォントを選ぶ...", window.choose_font)

        # 右クリックのメニューが写して使う（→ `items_to_copy`）
        self.copy_items = items_to_copy(menu, window._tool_actions.values())

    def refresh(self) -> None:
        text = self._state.selected_text
        for action in self.actions:
            action.setEnabled(text is not None)
        self.bold_action.setChecked(text is not None and text.font.bold)
        self.vertical_action.setChecked(
            text is not None and text.direction == "vertical"
        )


class StickerMenu:
    """マークのメニュー（要件定義 6.14）。

    アクセスキーは `&K`。`&M` はコマのメニューが使っている。

    先頭に「作る」を置くのは、フキダシ・セリフと同じ理由（1つも
    選んでいない間にメニュー全体がグレーにならないようにする）。
    """

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("マーク(&K)")
        for tool in STICKER_TOOLS:
            menu.addAction(window._tool_actions[tool])
        menu.addSeparator()

        # ここから下は選択中のマークに対する操作
        self.actions: list[QAction] = []
        self.attach_action = window._act(
            "コマへの紐づけを解除", window.toggle_sticker_attachment
        )
        menu.addAction(self.attach_action)
        self.actions.append(self.attach_action)

        # 右クリックのメニューが写して使う（→ `items_to_copy`）
        self.copy_items = items_to_copy(menu, window._tool_actions.values())

    def refresh(self) -> None:
        sticker = self._state.selected_sticker
        for action in self.actions:
            action.setEnabled(sticker is not None)
        if sticker is not None:
            self.attach_action.setText(
                "コマへの紐づけを解除"
                if sticker.attached_panel_id
                else "重なっているコマに紐づける"
            )
