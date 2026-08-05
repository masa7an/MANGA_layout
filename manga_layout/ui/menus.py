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

import pathlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu

from ..model import TAIL_SHAPE_BUBBLES, TAIL_SHAPE_TRIANGLE
from ..recent_project import load_recent_project
from ..storage import PROJECT_FILENAME
from .export import EXPORT_DIRNAME
from .state import (
    BALLOON_STYLE_LABELS,
    BALLOON_TOOLS,
    STICKER_TOOLS,
    TOOL_PANEL,
    TOOL_ROUGH,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_TEXT,
    object_label,
)

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

# ラフ（下敷き → 6.23）を畳んだメニューの見出しと、その中の項目の文言。
#
# **畳むのは、ファイルのメニューが4項目ぶん伸びるのを避けるため。** ラフは
# 1つの作品で最初に1回敷いたら、あとは調整と色の切り替えしか使わない。
# 開く・保存・書き出しと同じ高さに常時並べる頻度ではない（集中線・
# フキダシの種類と同じ畳み方 → 6.12）
ROUGH_MENU_LABEL = "ラフ"

# 色の切り替えは**1項目の文言を入れ替える**（集中線の白 → 6.19 と同じ形）。
# 「どちらに変わるか」を名前に出す。今の状態を書いても、押した結果が分からない
ROUGH_FADED_LABELS = {
    True: "青く淡くする",
    False: "元の色に戻す",
}

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


class FileMenu:
    """ファイルのメニュー。実処理は `ProjectIO`（→ `project_io.py`）。

    ラフ（→ 6.23）だけは `MainWindow` 側に実処理がある。作品まるごとの
    出し入れではなく、表示中のページに1枚敷くだけの操作なので、
    `ProjectIO` の受け持ち（新規・開く・保存・書き出し）とは筋が違う。
    """

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("ファイル(&F)")
        menu.addAction(window._act("新規作成", window.files.new_project, "Ctrl+N"))
        menu.addAction(
            window._act(
                "開く...",
                window.files.open_project,
                "Ctrl+O",
                f"作品フォルダの中の {PROJECT_FILENAME} を選ぶ",
            )
        )
        self.recent_project_action = window._act(
            "前回のファイルを開く",
            window.files.open_recent_project,
            None,
            "前回開いた・保存した作品を、選ぶ手間なしで開く",
        )
        menu.addAction(self.recent_project_action)
        menu.addSeparator()
        menu.addAction(window._act("保存", window.files.save_project, "Ctrl+S"))
        menu.addAction(
            window._act(
                "名前を付けて保存...", window.files.save_project_as, "Ctrl+Shift+S"
            )
        )
        menu.addSeparator()
        self._build_rough_menu(window, menu)
        menu.addSeparator()
        menu.addAction(
            window._act(
                "画像で書き出し...",
                window.files.export_image,
                "Ctrl+E",
                f"作品フォルダの {EXPORT_DIRNAME}/ に書き出す",
            )
        )
        menu.addSeparator()
        menu.addAction(
            window._act(
                "閉じる",
                window.files.close_project,
                "Ctrl+W",
                "保存を確認したうえで、開いている作品を閉じて空の状態に戻す（アプリは終了しない）",
            )
        )
        menu.addAction(window._act("終了", window.close, "Ctrl+Q"))

    def _build_rough_menu(self, window: MainWindow, parent_menu: QMenu) -> None:
        """ラフ（下敷き → 6.23）を畳んで出す。

        **畳んだ見出しの QAction を控えない。** この QAction は下位の QMenu が
        持っているもので、控えて有効・無効を触ると実体ごと消える
        （→ `items_to_copy`、PySide6の落とし穴.md の 1）。
        """
        menu = QMenu(ROUGH_MENU_LABEL, parent_menu)
        menu.addAction(
            window._act(
                "読み込む...",
                window.load_rough,
                None,
                "紙に描いたラフを、このページの一番下に敷く（書き出しには出ない）",
            )
        )
        # 色の切り替えと調整の道具は、右クリックからも同じ実体を出す
        # （→ `context_menu.py`）。作り直すと有効・無効が片方だけ古くなる
        self.rough_faded_action = window._act(
            ROUGH_FADED_LABELS[True],
            window.toggle_rough_faded,
            None,
            "青鉛筆の下書きのように淡くする／写真のままの色に戻す",
        )
        menu.addAction(self.rough_faded_action)
        self.rough_tool_action = window._tool_actions[TOOL_ROUGH]
        menu.addAction(self.rough_tool_action)
        self.rough_remove_action = window._act(
            "外す",
            window.remove_rough,
            None,
            "このページのラフを外す（画像そのものは消えない）",
        )
        menu.addAction(self.rough_remove_action)
        parent_menu.addMenu(menu)

    def refresh(self) -> None:
        """ラフの項目を、いまのページの状態に合わせる。

        ラフが無いページでは、読み込む以外の3つを押せなくする。押しても
        何も起きない項目が並ぶより、押せないことが先に分かるほうがよい
        （→ 6.12）。
        """
        rough = self._state.page.rough
        # 「どちらに変わるか」を出す。いま淡いなら「元の色に戻す」
        self.rough_faded_action.setText(
            ROUGH_FADED_LABELS[not (rough is not None and rough.faded)]
        )
        self.rough_faded_action.setEnabled(rough is not None)
        self.rough_tool_action.setEnabled(rough is not None)
        self.rough_remove_action.setEnabled(rough is not None)

    def sync_recent_project(self, path: pathlib.Path | None = None) -> None:
        """『前回のファイルを開く』の表示を、わかっている行き先に合わせる。

        `path` を渡さなければ記録から読み直す（起動直後の初期表示用）。
        """
        if path is None:
            path = load_recent_project()
        if path is None:
            self.recent_project_action.setText("前回のファイルを開く")
            self.recent_project_action.setEnabled(False)
            return
        self.recent_project_action.setText(f"前回のファイルを開く（{path.name}）")
        self.recent_project_action.setEnabled(True)


class EditMenu:
    """編集のメニュー。元に戻す・やり直す・複製・削除・全面コマ。"""

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        self._state = window.state
        menu = window.menuBar().addMenu("編集(&E)")
        self.undo_action = window._act("元に戻す", window.state.undo, "Ctrl+Z")
        self.redo_action = window._act("やり直す", window.state.redo, "Ctrl+Y")
        # Ctrl+Shift+Z も「やり直す」に割り当てる。ソフトによって Ctrl+Y と
        # Ctrl+Shift+Z のどちらが「やり直す」かが割れているため、両方通す
        self.redo_action.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        menu.addAction(self.undo_action)
        menu.addAction(self.redo_action)
        menu.addSeparator()
        # **キーは割り当てない**（→ 要件定義 7章）。`Ctrl+D` は他のソフトで
        # 「選択を解除」に当たっていることがあり、意味が食い違う
        self.duplicate_action = window._act(
            "複製",
            window.state.duplicate_selected,
            None,
            "選んでいるものを写して、右下へ少しずらして置く",
        )
        menu.addAction(self.duplicate_action)
        self.delete_action = window._act("削除", window.delete_selected, "Delete")
        menu.addAction(self.delete_action)
        self.full_page_action = window._act(
            "ページ全面にコマを作る", window.add_full_page_panel, "Ctrl+Shift+A"
        )
        menu.addAction(self.full_page_action)

    def refresh(self) -> None:
        history = self._state.history
        self.undo_action.setEnabled(history.can_undo)
        self.redo_action.setEnabled(history.can_redo)
        self.undo_action.setText(
            f"元に戻す: {history.undo_label}" if history.can_undo else "元に戻す"
        )
        self.redo_action.setText(
            f"やり直す: {history.redo_label}" if history.can_redo else "やり直す"
        )
        # 「削除」ではなく「コマを削除」「画像を削除」と、対象を名前に出す。
        # 押す前にカーソルの下で気づけるようにするため（→ `delete_target`）
        target = self._window.delete_target()
        self.delete_action.setEnabled(target is not None)
        self.delete_action.setText("削除" if target is None else f"{target[0]}を削除")
        # 複製も対象を名前に出す（→ 6.15）。**斜めに割ったコマでも押せるまま**に
        # する。グレーにすると「写せない」ことは伝わっても理由が伝わらず、
        # 出所が斜めであることに気づけない（断りは `duplicate_selected` が出す）
        name = object_label(self._state.selected_object)
        self.duplicate_action.setEnabled(bool(name))
        self.duplicate_action.setText(f"{name}を複製" if name else "複製")


class PanelMenu:
    """コマのメニュー。割る道具・斜めの反転・ロック・集中線（畳み）。"""

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("コマ(&M)")
        # 「作る」を先頭に置く。ここが選択中のコマへの操作だけだと、
        # 何も選んでいない間はメニュー全体がグレーになる（吹き出しでの失敗）
        menu.addAction(window._tool_actions[TOOL_PANEL])
        menu.addSeparator()
        for tool in (TOOL_SPLIT_H, TOOL_SPLIT_V, TOOL_SPLIT_SLANT):
            menu.addAction(window._tool_actions[tool])
        menu.addSeparator()
        self.slant_flip_action = window._act(
            "斜めの向きを反転",
            window.flip_slant,
            None,
            "斜めに割った2枚の傾きを / と \\ で入れ替える",
        )
        menu.addAction(self.slant_flip_action)
        menu.addSeparator()
        # 「ロック」と「ロックを解除」は1項目の文言を入れ替える
        # （しっぽの「消す／出す」と同じ → `refresh`）
        self.lock_toggle_action = window._act(
            "ロック",
            window.toggle_panel_lock,
            None,
            "選んだコマを動かせなくする。中の画像やフキダシは今まで通り触れる",
        )
        menu.addAction(self.lock_toggle_action)
        self.lock_all_action = window._act(
            "このページのコマをすべてロック", window.lock_all_panels
        )
        menu.addAction(self.lock_all_action)
        self.unlock_all_action = window._act(
            "このページのコマのロックをすべて解除", window.unlock_all_panels
        )
        menu.addAction(self.unlock_all_action)
        menu.addSeparator()
        self.focus = FocusMenu(window, menu)

    def refresh(self) -> None:
        self.slant_flip_action.setEnabled(self._state.selected_slant_pair is not None)

        # ロック（→ 6.17）。「ロック／ロックを解除」は集中線の「入れる／消す」
        # と同じく1項目の文言を入れ替える。一括の2項目は、押しても何も
        # 変わらない側をグレーにする（→ `lock_all_panels` の無変化ガード）
        self.lock_toggle_action.setEnabled(self._state.selected_panel is not None)
        self.lock_toggle_action.setText(
            "ロックを解除" if self._state.is_locked_selection else "ロック"
        )
        page_panels = self._state.page.panels
        self.lock_all_action.setEnabled(
            bool(page_panels) and not all(p.locked for p in page_panels)
        )
        self.unlock_all_action.setEnabled(any(p.locked for p in page_panels))

        self.focus.refresh()


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


class ImageMenu:
    """画像のメニュー。貼り付け・読み込み・フィット・回転リセット・整理。"""

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("画像(&I)")
        self.paste_action = window._act(
            "クリップボードから画像を貼り付け", window.paste_image, "Ctrl+V", "クリップボードの画像を置く"
        )
        self.open_image_action = window._act(
            "ファイル画像 読み込み...", window.open_image_file
        )
        menu.addAction(self.paste_action)
        menu.addAction(self.open_image_action)
        menu.addSeparator()
        self.fit_action = window._act(
            "コマにフィット", window.fit_image, "Ctrl+Shift+F", "選択中の画像でコマを埋める"
        )
        menu.addAction(self.fit_action)
        # 回すのはつまみ（→ 6.3）。ここに出すのは戻す側だけで、キーは足さない。
        # 傾けるのは絵を見ながら合わせる操作なので、数値やキーでは代えられない
        self.reset_rotation_action = window._act(
            "回転をリセット",
            window.reset_image_rotation,
            None,
            "選択中の画像の傾きを 0 に戻す",
        )
        menu.addAction(self.reset_rotation_action)
        menu.addSeparator()
        menu.addAction(window._act("未使用ファイルを整理...", window.prune_assets))

    def refresh(self) -> None:
        image = self._state.selected_image
        self.fit_action.setEnabled(image is not None)
        self.reset_rotation_action.setEnabled(
            image is not None and image.rotation != 0.0
        )


class PageMenu:
    """ページのメニュー。追加・挿入・削除・並べ替え・サイズ・ページ送り。"""

    def __init__(self, window: MainWindow) -> None:
        self._state = window.state
        menu = window.menuBar().addMenu("ページ(&P)")
        # 「追加」は必ず末尾、「挿入」は表示中のページの前。行き先の決まった
        # ほうを別の項目にしてある（要件定義 6.1）
        menu.addAction(
            window._act("ページを追加", window.add_page, "Ctrl+Shift+N", "末尾に1枚足す")
        )
        menu.addAction(
            window._act(
                "ページを挿入",
                window.insert_page,
                "Ctrl+Shift+I",
                "表示中のページの前に1枚差し込む",
            )
        )
        self.delete_page_action = window._act("ページを削除...", window.delete_page)
        menu.addAction(self.delete_page_action)
        menu.addSeparator()

        self.move_up_action = window._act(
            "ページを前へ移動", lambda: window.move_page_by(-1), "Ctrl+Shift+PgUp"
        )
        self.move_down_action = window._act(
            "ページを後ろへ移動", lambda: window.move_page_by(1), "Ctrl+Shift+PgDown"
        )
        menu.addAction(self.move_up_action)
        menu.addAction(self.move_down_action)
        menu.addSeparator()

        menu.addAction(
            window._act(
                "ページサイズ...", window.change_page_size, None, "A4 相当 / B5 相当 / px 指定"
            )
        )
        menu.addSeparator()
        menu.addAction(window._act("前のページ", window.prev_page, "PgUp"))
        menu.addAction(window._act("次のページ", window.next_page, "PgDown"))

    def refresh(self) -> None:
        # 「ページ [n]/総数」の見出しは PageJumpBar が自分で追いかける
        index, count = self._state.page_index, self._state.page_count
        self.delete_page_action.setEnabled(count > 1)
        self.move_up_action.setEnabled(index > 0)
        self.move_down_action.setEnabled(index < count - 1)


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
        # 組み直す（→ `ContextMenu.build`、集中線と同じ形）
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
