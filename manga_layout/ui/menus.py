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

from ..model import TAIL_SHAPE_BUBBLES, TAIL_SHAPE_TRIANGLE
from .state import BALLOON_STYLE_LABELS, BALLOON_TOOLS, STICKER_TOOLS, TOOL_TEXT

if TYPE_CHECKING:
    from .window import MainWindow


# しっぽの向きを変える項目（→ `TAIL_DIRECTIONS`）。
#
# **右と左を分ける。** まとめて「横へ」にすると、どちら側になるかが
# 先端の現在位置任せになる。左右に2人が向かい合うコマでは、指す相手を
# 選べないと使えない（相談 2026-08-05）
#
# **メニューの文言と操作後の案内が同じ表を見る。** 書き分けると、
# 改名したときに片方だけ古いまま残る（→ `BALLOON_STYLE_LABELS` と同じ線引き）
TAIL_TURN_ITEMS = (("上", "up"), ("右", "right"), ("左", "left"), ("下", "down"))
TAIL_TURN_LABELS = {direction: where for where, direction in TAIL_TURN_ITEMS}

# 畳んだメニューの見出し。メニューバーと右クリックの2か所が同じ名前を出す
# ので、書き分けないよう1箇所に持つ（→ `BalloonMenu`）
BALLOON_STYLE_MENU_LABEL = "種類を変える"

# しっぽの形を切り替える項目の文言。**「どちらに変わるか」を名前に出す。**
# 今の形を書いても、押した結果が分からない（「入れる／消す」と同じ形 → 6.19）
TAIL_SHAPE_LABELS = {
    TAIL_SHAPE_TRIANGLE: "しっぽを三角にする",
    TAIL_SHAPE_BUBBLES: "しっぽを丸くする",
}


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


class BalloonMenu:
    """フキダシのメニュー。

    まず「作る」を置く。これが無いと、吹き出しを1つも選んでいない間は
    メニュー全体がグレーになり、どこから作るのか分からなくなる。
    """

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("フキダシ(&B)")
        for tool in BALLOON_TOOLS:
            menu.addAction(window._tool_actions[tool])
        menu.addSeparator()

        # ここから下は選択中の吹き出しに対する操作
        self.actions: list[QAction] = []
        # 種類を変える項目は畳む（→ `_build_style_menu`）。
        #
        # **畳んだ側の見出しは `actions` に入れない。** この QAction は
        # 下位の QMenu が持っているもので、誰かが `QAction.menu()` を呼んだ
        # 時点で QMenu ごと引き取られ、実体が消える。持ってよいのは `_act` で
        # 作った QAction（親がウィンドウ）だけ（→ `items_to_copy`）。
        # 見出しを持たせたところ、メニューバーを辿ったあとの `_refresh` が
        # `Internal C++ object already deleted` で落ちた（2026-08-05 に実測）。
        #
        # 見出しは使えるままで、中身だけがグレーになる。集中線と同じ形
        style_menu_action = menu.addMenu(self._build_style_menu(window))
        menu.addSeparator()

        self.tail_action = window._act("しっぽを消す", window.toggle_tail)
        menu.addAction(self.tail_action)
        self.actions.append(self.tail_action)

        # 「入れる／消す」と同じく1項目の文言を入れ替える。2つ並べると、
        # 片方は必ず押しても何も変わらない状態で場所だけ取る
        self.tail_shape_action = window._act(
            TAIL_SHAPE_LABELS[TAIL_SHAPE_BUBBLES],
            window.toggle_tail_shape,
            tip="心の声・独り言に使う、円が並んだしっぽに変える",
        )
        menu.addAction(self.tail_shape_action)
        self.actions.append(self.tail_shape_action)

        # しっぽの向きを変える4つは右クリックには出さない。選択中の
        # フキダシだけでも右クリックのメニューは項目数が多く（実測13、
        # 外して10）、これ以上増やすと選びにくくなる（相談 2026-08-05）
        tail_turn_actions: list[QAction] = []
        for where, direction in TAIL_TURN_ITEMS:
            action = window._act(
                f"しっぽを{where}へ",
                lambda _=False, d=direction: window.turn_tail(d),
                tip=f"しっぽの向きを{where}に変えます。先端も一緒に回ります",
            )
            menu.addAction(action)
            self.actions.append(action)
            tail_turn_actions.append(action)

        self.attach_action = window._act(
            "コマへの紐づけを解除", window.toggle_attachment
        )
        menu.addAction(self.attach_action)
        self.actions.append(self.attach_action)

        # 右クリックのメニューが写して使う（→ `items_to_copy`）。
        # **畳んだ「種類を変える」は写せない**（QMenu を持ち帰れないため）。
        # 右クリック側では同じ控え（`style_copy_items`）から
        # 組み直す（→ `_context_menu`、集中線と同じ形）
        self.copy_items = items_to_copy(
            menu,
            (*window._tool_actions.values(), style_menu_action, *tail_turn_actions),
        )

    def _build_style_menu(self, window: MainWindow) -> QMenu:
        """種類を変えるメニュー（要件定義 10.1）。**フキダシのメニューに畳む。**

        種類が増えるぶん「◯◯にする」がそのまま縦に伸びる。今の種類へ
        変える項目は必ず1つ混ざっているので、**並べても常に1行は無駄に
        場所を取る**。集中線と同じ扱いで畳んでおく（→ 6.12、6.16）。

        一覧は `BALLOON_STYLE_LABELS` から作る。書き並べると、種類を
        足したときにここへ足し忘れて相互に変えられなくなる。
        """
        menu = QMenu(BALLOON_STYLE_MENU_LABEL, window)
        for style, name in BALLOON_STYLE_LABELS.items():
            action = window._act(
                f"{name}にする", lambda _=False, s=style: window.set_balloon_style(s)
            )
            menu.addAction(action)
            self.actions.append(action)

        # 右クリックのメニューが写して使う（→ `items_to_copy`）
        self.style_copy_items = items_to_copy(menu, window._tool_actions.values())
        return menu

    def refresh(self) -> None:
        balloon = self._state.selected_balloon
        for action in self.actions:
            action.setEnabled(balloon is not None)
        if balloon is not None:
            self.tail_action.setText(
                "しっぽを消す" if balloon.tail.enabled else "しっぽを出す"
            )
            # 押した先の形を名前に出す（→ `TAIL_SHAPE_LABELS`）
            self.tail_shape_action.setText(
                TAIL_SHAPE_LABELS[
                    TAIL_SHAPE_TRIANGLE
                    if balloon.tail.shape == TAIL_SHAPE_BUBBLES
                    else TAIL_SHAPE_BUBBLES
                ]
            )
            self.attach_action.setText(
                "コマへの紐づけを解除"
                if balloon.attached_panel_id
                else "重なっているコマに紐づける"
            )


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
