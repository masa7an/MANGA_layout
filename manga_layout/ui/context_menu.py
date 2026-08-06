"""右クリックのメニューの組み立て。

`MainWindow` から切り出した部品。**項目は既にある QAction を写して並べる。
作り直さない。** 有効・無効の切り替えと文言の書き換えは各メニュー部品の
`refresh()` が1か所でやっている（→ `menus.py`）。ここで別の QAction を
立てると同じ処理をもう1組書くことになり、片方だけ直し忘れる。

例外は「ここに〜」「ここで〜」の項目だけ。押した場所を持てるのは
右クリックだけで、メニューバー側には対応する項目が無い（場所が
決まらないため、道具に持ち替える形になっている）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from ..layout import image_at
from .menus import BALLOON_STYLE_MENU_LABEL
from .state import (
    BALLOON_STYLE_LABELS,
    STICKER_KIND_LABELS,
    TOOL_ROUGH,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
)

if TYPE_CHECKING:
    from ..model import Panel
    from .window import MainWindow

# 右クリックの「ここに フキダシ を追加」で、種類を下に畳むときの名前。
# **表に出る「フキダシ」はここと `BALLOON_STYLE_LABELS` だけ**
BALLOON_PLACE_HERE_NAME = "フキダシ"


# 右クリックの「ここに ●● を追加」「ここで ●●」の前置きと後置き。
# 分けて持つのは、2つめから落とすため（→ `here_label`）
PLACE_HERE_PREFIX = "ここに"
PLACE_HERE_SUFFIX = "を追加"
SPLIT_HERE_PREFIX = "ここで"


def here_label(name: str, *, prefix: str, suffix: str = "", first: bool) -> str:
    """押した場所に効く項目の名前。**2つめからは前後を落として名前だけにする。**

    右クリックのメニューは項目が多く（吹き出しを選んだだけで10前後）、
    同じ前置きが縦に並ぶと、選ぶために読む字数がそのまま増える。
    違うのは真ん中の名前だけなので、そこだけ残す。

    先頭を空白で埋めるのは、名前の頭を1つめと縦に揃えるため。
    詰めてしまうと、前置きの掛かっていない別の項目に見える。
    「ここに」も「ここで」も全角3字なので、全角の空白3つで幅が揃う。
    **2つの組が同じメニューに並んでも頭が揃う**のは、幅を前置きの
    字数から出しているため。決め打ちの空白にすると、片方だけずれる。

    落とした言葉は捨てず、カーソルを乗せた間だけステータスバーに出す
    （→ `ContextMenu._show_tips_in_status_bar`）。
    """
    if first:
        return f"{prefix} {name} {suffix}" if suffix else f"{prefix} {name}"
    return f"{'　' * len(prefix)} {name}"


def place_here_label(name: str, *, first: bool) -> str:
    """「ここに ●● を追加」。"""
    return here_label(
        name, prefix=PLACE_HERE_PREFIX, suffix=PLACE_HERE_SUFFIX, first=first
    )


def split_here_label(name: str, *, first: bool) -> str:
    """「ここで ●●に割る」。

    後置きを持たないのは、変わるのが向き（横・縦・斜め）だけで、
    「に割る」まで名前に含めたほうが1行で意味が通るため。
    ここを「ここで 横 に割る」と切ると、残るのが1字になって読みにくい。
    """
    return here_label(name, prefix=SPLIT_HERE_PREFIX, first=first)


# 右クリックの「この画像を差し替え」。名前は隣に並ぶ「この画像を削除」に
# 揃える（どちらもカーソルの下の1枚に効く）。何をするのかは名前を伸ばさず、
# カーソルを乗せた間の説明に逃がす（→ `_menu_act` の `tip`）
REPLACE_IMAGE_LABEL = "この画像を差し替え..."
REPLACE_IMAGE_TIP = "ファイルを選び、この画像と入れ替える（前の絵は消える）"


class ContextMenu:
    """右クリックのメニュー。窓の属性としては `window.context_menu`。"""

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        self._state = window.state
        self._view = window.view

    def show(self, x: float, y: float, global_pos) -> None:
        """画面を右クリックされた。押した場所に応じたメニューを出す。

        押した場所のものは `PageView` 側で既に選び直されている。項目の
        有効・無効はその選択を見て `_refresh` が決めているので、ここでは
        並べて出すだけでよい。
        """
        menu = self.build(x, y)
        menu.exec(global_pos)
        # 押した場所を覚えている項目があるので使い回せない。毎回捨てる
        menu.deleteLater()

    def build(self, x: float, y: float) -> QMenu:
        """右クリックのメニューを組む。`x`, `y` は押した場所（シーンの px）。"""
        window = self._window
        menu = QMenu(window)
        # 区切り線が薄くて目立たないとの指摘（2026-08-05）を受けて明るくする。
        # このメニューはここでしか作らないので、メニューバー側の見た目には響かない。
        menu.setStyleSheet(
            "QMenu::separator { height: 1px; background: #cccccc; margin: 4px 8px; }"
        )
        self._show_tips_in_status_bar(menu)
        state = self._state

        # ラフの調整中は、ラフの項目だけ出す（→ 要件定義 6.23）。この道具では
        # 何も選べないので、選択に効く項目はどれも押せない
        if state.tool == TOOL_ROUGH:
            menu.addAction(window.file_menu.rough_faded_action)
            menu.addAction(window.file_menu.rough_remove_action)
            menu.addSeparator()
            # ここに出しておかないと、道具を戻すのにメニューバーへ
            # 戻ることになる（この道具は道具箱の一番端にある）
            self._menu_act(
                menu,
                "調整をやめる",
                lambda _checked=False: state.set_tool(TOOL_SELECT),
                tip="選択の道具に戻る。ラフはそのまま残る",
            )
            return menu

        if state.selected_text is not None:
            self._copy_actions(menu, window.text_menu.copy_items)

        elif state.selected_sticker is not None:
            self._copy_actions(menu, window.sticker_menu.copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("text",))

        elif state.selected_balloon is not None:
            # **メニューバーと同じものを畳んで出す**（集中線と同じ形 → 下）。
            # 種類を写せないのは QMenu を持ち帰れないため（→ `items_to_copy`）
            self._copy_actions(
                menu.addMenu(BALLOON_STYLE_MENU_LABEL),
                window.balloon_menu.style_copy_items,
            )
            self._copy_actions(menu, window.balloon_menu.copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("sticker", "text"))

        elif state.selected_image is not None:
            menu.addAction(window.image_menu.fit_action)
            # 傾いていないときは出さない。押しても何も起きない項目が
            # 並ぶと、メニューを読む手間だけが増える（→ 6.12）
            if state.selected_image.rotation != 0.0:
                menu.addAction(window.image_menu.reset_rotation_action)
            menu.addSeparator()
            # 踏み込んで画像を選んだ状態でも、差し替えと読み込みは要る。
            # ここに無いと、いったん Esc でコマへ戻る手数が挟まる
            self._menu_act(
                menu,
                REPLACE_IMAGE_LABEL,
                lambda _checked=False, i=state.selected_image.id: (
                    window.replace_image_file(i)
                ),
                tip=REPLACE_IMAGE_TIP,
            )
            menu.addAction(window.image_menu.open_image_action)

        elif state.selected_panel is not None:
            self._add_split_here(menu, x, y)
            menu.addAction(window.panel_menu.slant_flip_action)
            # 重なり順。**押せるときだけ出す。** コマが重なっていないページ
            # では常にグレーで、右クリックのたびに読み飛ばす2行になる
            # （メニューバー側は場所が動かないほうがよいのでグレーで残す）
            for action in (window.panel_menu.raise_action, window.panel_menu.lower_action):
                if action.isEnabled():
                    menu.addAction(action)
            menu.addAction(window.panel_menu.lock_toggle_action)
            # **メニューバーと同じものを畳んで出す。** 並べるのは同じ
            # QAction なので、有効・無効と「入れる／消す」の文言は
            # 各部品の `refresh()` が1か所で面倒を見たままになる（→ 6.12）。
            # ここで作り直すと、メニューバー側が古い項目を持ったまま残る
            self._copy_actions(menu.addMenu("集中線"), window.focus_menu.copy_items)
            self._copy_actions(menu.addMenu("流線"), window.flow_menu.copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("balloon", "sticker", "text"))
            menu.addSeparator()
            menu.addAction(window.image_menu.paste_action)
            menu.addAction(window.image_menu.open_image_action)
            self._add_image_here(menu, state.selected_panel, x, y)

        else:
            # 何も無いところ。選択に効く項目はどれも使えないので出さない。
            # 代わりに、ここでしか呼べない「元に戻す」を添える
            self._add_place_here(menu, x, y, ("panel", "balloon", "sticker", "text"))
            menu.addAction(window.edit_menu.full_page_action)
            # ラフ（→ 6.23）は敷いてあるときだけ。押した場所は関係ない操作
            # なので、コマの上ではなく「何も無いところ」に置く
            if self._state.page.rough is not None:
                menu.addSeparator()
                self._add_rough_actions(menu)
            menu.addSeparator()
            menu.addAction(window.edit_menu.undo_action)
            menu.addAction(window.edit_menu.redo_action)
            return menu

        menu.addSeparator()
        # 複製と削除は「選んでいるものへの操作」で、どの品書きにも要る。
        # 分岐の外に置いて、種類を足したときに片方だけ抜けないようにする
        menu.addAction(window.edit_menu.duplicate_action)
        menu.addAction(window.edit_menu.delete_action)
        return menu

    def _show_tips_in_status_bar(self, menu: QMenu) -> None:
        """カーソルを乗せた項目の説明をステータスバーに出す。

        **宛先はウィンドウだと明示する。** メニューバーから開いた
        メニューには「呼び出し元のウィジェット」があり、Qt はそこへ説明を
        送るのでステータスバーまで届く。右クリックのメニューには呼び出し元が
        無いため、Qt は QMenu 自身へ送る。**説明は親へ伝わらないので、
        そのままでは誰も受け取らない**（2026-08-05 に実測。メニューへ送ると
        空、ウィンドウへ送ると出る）。

        `hovered` はキーボードの上下でも鳴るので、マウスを使わない場合も
        同じように出る。閉じたら消すのは、選び終わったあとに古い説明が
        残らないようにするため。
        """
        window = self._window
        menu.hovered.connect(lambda action: action.showStatusText(window))
        menu.aboutToHide.connect(window.statusBar().clearMessage)

    def _copy_actions(self, menu: QMenu, items: list[QAction | None]) -> None:
        """控えておいた項目を右クリックのメニューへ並べる。

        QAction は写しても実体は1つなので、有効・無効も文言も
        メニューバー側と自動で揃う。`None` は区切り線の印
        （→ `items_to_copy`）。
        """
        for item in items:
            last = menu.actions()[-1] if menu.actions() else None
            if item is None:
                # 道具を外した跡に区切り線だけが残る。先頭と連続は出さない
                if last is not None and not last.isSeparator():
                    menu.addSeparator()
                continue
            menu.addAction(item)

    def _add_place_here(
        self, menu: QMenu, x: float, y: float, kinds: tuple[str, ...]
    ) -> None:
        """押した場所に1つ置く項目。`kinds` に挙げた種類だけ出す。

        名前を「ここに」で始めるのは、メニューバー側の「〜を追加」
        （道具に持ち替えて、次に押した場所に置く）と区別するため。
        こちらは道具を持ち替えず、その場で置いて終わる。

        **前置きを出すのは、実際に並んだ1つめだけ**（→ `place_here_label`）。
        `kinds` で絞ったあとの並び順で決める。ここに書いた順ではないので、
        「コマ」が外れる場面では次の種類が1つめになる。

        **フキダシだけは種類を下に畳む**（要件定義 10.1）。種類が増えるほど
        「ここに」の組だけで縦に伸び、コマ・マーク・セリフが下へ押し出される。
        畳めば、フキダシを置かない場面で読む字数が1行に収まる。
        """
        view = self._view
        items = (
            ("panel", "コマ", lambda: view.add_panel_at(x, y)),
            # `slot` が None ＝ 種類を畳んで下に出す印（→ 下の分岐）
            ("balloon", BALLOON_PLACE_HERE_NAME, None),
            *(
                (
                    "sticker",
                    name,
                    lambda _=False, k=kind: view.add_sticker_at(x, y, k),
                )
                for kind, name in STICKER_KIND_LABELS.items()
            ),
            ("text", "セリフ", lambda: view.add_text_at(x, y)),
        )
        shown = 0
        for kind, name, slot in items:
            if kind not in kinds:
                continue
            # 名前から落とした「ここに」「を追加」は、カーソルを乗せた
            # 間だけステータスバーに出す。名前に戻すと縦に並んだときの
            # 字数が増え、省略した意味が無くなる
            label = place_here_label(name, first=shown == 0)
            tip = place_here_label(name, first=True)
            if slot is None:
                self._add_balloon_styles_here(menu, x, y, label, tip)
            else:
                self._menu_act(menu, label, slot, tip=tip)
            shown += 1

    def _add_balloon_styles_here(
        self, menu: QMenu, x: float, y: float, label: str, tip: str
    ) -> None:
        """「ここに フキダシ を追加」の下に、種類を並べる。

        **下位のメニューにも説明の出し方を配り直す。** `hovered` は開いた
        メニューごとに鳴るので、親につないだだけでは畳んだ中の説明が
        ステータスバーに出ない（→ `_show_tips_in_status_bar`）。
        """
        view = self._view
        sub = menu.addMenu(label)
        sub.menuAction().setStatusTip(tip)
        self._show_tips_in_status_bar(sub)
        for style, name in BALLOON_STYLE_LABELS.items():
            self._menu_act(
                sub,
                name,
                lambda _=False, s=style: view.add_balloon_at(x, y, s),
                tip=place_here_label(name, first=True),
            )

    def _add_rough_actions(self, menu: QMenu) -> None:
        """ラフの色の切り替えと、位置・大きさの調整（→ 要件定義 6.23）。

        **メニューバーと同じ実体を並べる。** 文言の入れ替え（「ラフを青くする」
        ↔「ラフの色を戻す」）と有効・無効は `FileMenu.refresh` が1か所で
        面倒を見たままになる（集中線・ロックと同じ形 → 6.12）。

        畳まずに直に並べるのは、ここに出るのが2項目しかないため。畳むと
        開く操作が1つ増えるだけになる（ファイルのメニューでは4項目なので畳む）。
        """
        window = self._window
        menu.addAction(window.file_menu.rough_faded_action)
        menu.addAction(window.file_menu.rough_tool_action)

    def _add_image_here(self, menu: QMenu, panel: Panel, x: float, y: float) -> None:
        """カーソルの下に画像があれば、その画像に効く項目を出す。

        **コマを選んだままでも画像を差し替え・削除できるようにする。**
        画像を選ぶにはダブルクリックで一段踏み込む必要があり（要件定義 6.3）、
        右クリックしただけではコマが選ばれる。削除の項目が無かった頃は、
        メニューに並ぶのが「コマを削除」だけになり、**画像を消すつもりで
        コマを消す**取り違えが実際に起きた。差し替えも同じ理由でここに出す。
        """
        window = self._window
        image = image_at(panel, x, y)
        if image is None:
            return
        self._menu_act(
            menu,
            REPLACE_IMAGE_LABEL,
            lambda _checked=False, i=image.id: window.replace_image_file(i),
            tip=REPLACE_IMAGE_TIP,
        )
        self._menu_act(
            menu,
            "この画像を削除",
            lambda _checked=False, i=image.id: window.delete_image(i),
        )

    def _add_split_here(self, menu: QMenu, x: float, y: float) -> None:
        """押した場所で1回きり割る項目。

        メニューバー側は道具の切り替え（選んでから、割る場所を押す）だが、
        右クリックは押した場所が既に分かっているので、その場で割る。

        **前置きを出すのは1つめだけ**（→ `split_here_label`）。3つとも
        必ず一緒に出るので、1つめは常に「横」になる。
        """
        view = self._view
        for index, (name, tool) in enumerate(
            (
                ("コマを横に割る", TOOL_SPLIT_H),
                ("コマを縦に割る", TOOL_SPLIT_V),
                ("コマを斜めに割る", TOOL_SPLIT_SLANT),
            )
        ):
            self._menu_act(
                menu,
                split_here_label(name, first=index == 0),
                # 既定値で受けているのは、triggered が渡す checked を
                # tool の位置で受け取らないようにするため
                lambda _checked=False, t=tool: view.split_at(x, y, t),
                tip=split_here_label(name, first=True),
            )

    @staticmethod
    def _menu_act(menu: QMenu, label: str, slot, tip: str = "") -> QAction:
        """そのメニュー限りの項目。

        親をメニューにしてあるので、メニューを捨てれば一緒に消える。
        ウィンドウに持たせると、右クリックのたびに溜まっていく。

        `tip` はカーソルを乗せた間だけステータスバーに出る説明
        （メニューバー側の `_act` と同じ仕組み）。**名前を短く保ったまま
        言葉を補いたいときに使う。** 名前のほうを長くすると、項目が
        縦に並んだときに読む字数がそのまま増える。
        """
        action = QAction(label, menu)
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action
