"""メインウィンドウ。メニュー・道具箱・ページ送り・ファイル操作。"""

from __future__ import annotations

import pathlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QFontDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QToolBar,
)

from ..errors import MangaLayoutError
from ..images import to_png_bytes
from ..layout import attach_target, cover_rect_in, full_page_rect, image_at
from ..model import (
    PT_TO_PX,
    TAIL_SHAPE_BUBBLES,
    TAIL_SHAPE_TRIANGLE,
    ImageObject,
    Panel,
)
from ..recent_project import load_recent_project
from ..settings import ensure_settings_file, load_settings, settings_path
from ..storage import PROJECT_FILENAME, prune_unused_assets
from .canvas import IMAGE_FILE_FILTER, PageView
from .export import (
    DEFAULT_SCALE,
    EXPORT_DIRNAME,
    ExportDialog,
    existing_paths,
    export_dir_of,
    export_pages,
    missing_assets_in,
    page_px,
    planned_paths,
    scale_label,
)
from .pages import PageJumpBar, PageListPanel, PageSizeDialog
from .project_io import ProjectIO
from .state import (
    BALLOON_STYLE_LABELS,
    BALLOON_TOOLS,
    STICKER_KIND_LABELS,
    STICKER_TOOLS,
    TOOL_BALLOON,
    TOOL_BALLOON_CLOUD,
    TOOL_BALLOON_JAGGED,
    TOOL_BALLOON_RECT,
    TOOL_BALLOON_WAVY,
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_STICKER_EXCLAIM,
    TOOL_STICKER_EXCLAIM_QUESTION,
    TOOL_TEXT,
    EditorState,
    object_label,
)

APP_TITLE = "漫画レイアウタ"

# 表示メニューに出す、ページ一覧の開け閉め項目の名前。
# 一覧の見出し（「ページ 1/9」）とは別に持つ（理由は `_refresh`）
PAGES_MENU_LABEL = "ページ一覧"

TEXT_ALIGN_LABELS = {"left": "左寄せ", "center": "中央寄せ", "right": "右寄せ"}

# 縦書きのときの、同じ値の呼び名。`align` は横書き用に作った項目を
# 読み替えて使っているので（→ `manga_layout.vertical`）、表示だけ言い換える。
# 「行の始まりに寄せる」が left で、縦書きの列は上から始まるため上寄せになる
TEXT_ALIGN_LABELS_VERTICAL = {"left": "上寄せ", "center": "中央寄せ", "right": "下寄せ"}


def align_label(align: str, direction: str) -> str:
    labels = (
        TEXT_ALIGN_LABELS_VERTICAL if direction == "vertical" else TEXT_ALIGN_LABELS
    )
    return labels.get(align, align)


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
# ので、書き分けないよう1箇所に持つ（→ `_build_balloon_style_menu`）
BALLOON_STYLE_MENU_LABEL = "種類を変える"

# しっぽの形を切り替える項目の文言。**「どちらに変わるか」を名前に出す。**
# 今の形を書いても、押した結果が分からない（「入れる／消す」と同じ形 → 6.19）
TAIL_SHAPE_LABELS = {
    TAIL_SHAPE_TRIANGLE: "しっぽを三角にする",
    TAIL_SHAPE_BUBBLES: "しっぽを丸くする",
}

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
    （→ `MainWindow._show_tips_in_status_bar`）。
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

# 文字の大きさを1段階変える幅と、行き過ぎを止める範囲。
# 数値を打ち込ませるより、押して確かめるほうが速い。
#
# **1段階はポイントで決める。** 状態表示もフォント設定の窓もポイントで
# 喋るので、px で決めると 1 段階が半端な数になる（以前の 2px は
# 約 0.96pt で、押しても表示が 1pt 動いたり動かなかったりした）。
TEXT_SIZE_STEP_PT = 2.0
TEXT_SIZE_STEP_PX = TEXT_SIZE_STEP_PT * PT_TO_PX
TEXT_SIZE_MIN_PX = 9.0
TEXT_SIZE_MAX_PX = 180.0

# 起動時の希望サイズ。画面に入らなければ後述の作業領域に合わせて縮める
WINDOW_SIZE = (1100, 860)

# 画面下端との間に必ず空ける余白（px）。
# ここを 0 にすると、下端いっぱいのときにステータス表示がタスクバーと
# 接して読みにくくなる
BOTTOM_GAP_PX = 20

# タイトルバーと枠のぶんの見込み（px）。
# 表示前は実寸（frameGeometry）が取れないため固定値で確保する。
# これが無いと、画面いっぱいの高さにしたときタイトルバーが画面外に出て
# ウィンドウを掴めなくなる
FRAME_ALLOWANCE_PX = 48

# 上書きの確認に名前を並べる件数の上限。
# 30 ページの作品でも確認欄が画面を埋めないようにする
OVERWRITE_LIST_LIMIT = 5


class MainWindow(QMainWindow):
    def __init__(self, state: EditorState | None = None):
        super().__init__()
        self.state = state or EditorState()
        self.view = PageView(self.state)
        self.setCentralWidget(self.view)
        self._apply_initial_geometry()

        # 書き出す画像サイズは作品ではなく好みなので、project.json には
        # 入れない。ただし1回の作業中は同じ値を使い続けるのが普通なので覚えておく
        self._export_scale = DEFAULT_SCALE

        # settings.json は手で書き換える前提のファイル。実物が無いと
        # 「どこに何を書けばいいのか」が分からないので、起動時に雛形を置く。
        # 読めなくても既定値で進む（設定は好みで、無くても作業はできる）。
        #
        # 場所を持っておくのは、テストで本物の設定を読み書きしないため
        self.settings_file = settings_path()
        ensure_settings_file(self.settings_file)
        self.settings = load_settings(self.settings_file)

        # ファイル入出力の部品。メニューがスロットとして参照するので、
        # メニューの組み立てより先に作る
        self.files = ProjectIO(self)

        self._tool_actions: dict[str, QAction] = {}
        self._build_pages_dock()
        self._build_menus()
        self._build_toolbar()
        self._build_status_bar()

        self.state.changed.connect(self._refresh)
        self.state.selection_changed.connect(self._refresh)
        self.state.page_changed.connect(self._refresh)
        self.state.tool_changed.connect(self._sync_tool_actions)
        self.state.message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.view.context_menu_requested.connect(self._show_context_menu)

        # 前回のセッションで開いていた作品名を「前回のファイルを開く」に出す
        self._sync_recent_project_action()

        self._refresh()

    # -- 組み立て ----------------------------------------------------------

    def _apply_initial_geometry(self) -> None:
        """タスクバーに隠れないよう、画面の作業領域に収めて配置する。

        availableGeometry はタスクバーを除いた領域を返す。そこから
        下に BOTTOM_GAP_PX、上に FRAME_ALLOWANCE_PX を残した範囲に
        中央寄せする。画面が希望サイズより小さければ縮める。
        """
        width, height = WINDOW_SIZE
        screen = self.screen()
        if screen is None:  # 表示装置が無いとき（offscreen 等）
            self.resize(width, height)
            return

        area = screen.availableGeometry().adjusted(
            0, FRAME_ALLOWANCE_PX, 0, -BOTTOM_GAP_PX
        )
        width = min(width, area.width())
        height = min(height, area.height())
        self.setGeometry(
            area.x() + (area.width() - width) // 2,
            area.y() + (area.height() - height) // 2,
            width,
            height,
        )

    def _build_pages_dock(self) -> None:
        """ページ一覧。左に置く（要件定義 6.1）。

        右に置くと、右綴じ（`reading_direction` が `rtl`）で読む向きと
        ページ送りの向きが画面の中でぶつかる。作品の並びは一覧の縦方向で
        表しているので、道具の置き場所として素直な左に寄せる。
        """
        self.pages_panel = PageListPanel(self.state)
        self.pages_dock = QDockWidget("ページ", self)
        self.pages_dock.setObjectName("pages")
        self.pages_dock.setWidget(self.pages_panel)
        self.pages_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        # 見出しは自前の部品にする。番号を押すと入力欄になり、そのページへ
        # 飛べる（要件定義 6.1）。既定の題名では入力欄を置けない
        self.pages_title = PageJumpBar(self.state, self.pages_dock)
        self.pages_dock.setTitleBarWidget(self.pages_title)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.pages_dock)

    def _act(self, text: str, slot, shortcut: str | None = None, tip: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _build_tool_actions(self) -> None:
        """道具の切り替え。メニューより先に作る。

        道具メニューと吹き出しメニューの両方から同じ項目を出すため。
        別々の項目にすると、選ばれている印がどちらか片方にしか付かない。
        """
        group = QActionGroup(self)
        group.setExclusive(True)
        # ショートカットが None の道具もある。**1文字キーを全部の道具に
        # 割り当てない。** 覚えられる数を超えると、余ったキーが誤爆の元に
        # なるだけになる。よく使うほうにだけ付ける（→ 6.14）
        for tool, shortcut in (
            (TOOL_SELECT, "V"),
            (TOOL_PANEL, "P"),
            (TOOL_SPLIT_H, "H"),
            (TOOL_SPLIT_V, "J"),
            (TOOL_SPLIT_SLANT, "K"),
            (TOOL_BALLOON, "B"),
            (TOOL_BALLOON_JAGGED, "G"),
            (TOOL_BALLOON_WAVY, "W"),
            # 雲と四角にキーは割り当てない。**キーを足すこと自体に値打ちは
            # 無く、元から通っていたものを塞ぐ副作用は必ず付く**
            # （→ 要件定義 7章）。ビックリはてなマークと同じ扱いで、
            # メニューと右クリックから出す
            (TOOL_BALLOON_CLOUD, None),
            (TOOL_BALLOON_RECT, None),
            (TOOL_STICKER_EXCLAIM, "M"),
            (TOOL_STICKER_EXCLAIM_QUESTION, None),
            (TOOL_TEXT, "T"),
        ):
            label = TOOL_LABELS[tool]
            action = QAction(f"{label} ({shortcut})" if shortcut else label, self)
            action.setCheckable(True)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, t=tool: self.state.set_tool(t))
            group.addAction(action)
            self.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[TOOL_SELECT].setChecked(True)

    def _build_sticker_menu(self) -> None:
        """マークのメニュー（要件定義 6.14）。

        アクセスキーは `&K`。`&M` はコマのメニューが使っている。

        先頭に「作る」を置くのは、フキダシ・セリフと同じ理由（1つも
        選んでいない間にメニュー全体がグレーにならないようにする）。
        """
        menu = self.menuBar().addMenu("マーク(&K)")
        for tool in STICKER_TOOLS:
            menu.addAction(self._tool_actions[tool])
        menu.addSeparator()

        # ここから下は選択中のマークに対する操作
        self.sticker_actions: list[QAction] = []
        self.sticker_attach_action = self._act(
            "コマへの紐づけを解除", self.toggle_sticker_attachment
        )
        menu.addAction(self.sticker_attach_action)
        self.sticker_actions.append(self.sticker_attach_action)

        # 右クリックのメニューが写して使う（→ `_items_to_copy`）
        self.sticker_copy_items = self._items_to_copy(menu)

    def _build_focus_menu(self) -> QMenu:
        """集中線のメニュー（要件定義 6.16）。**コマのメニューの下に畳む。**

        7項目あるので、コマのメニューへ並べると縦に伸びて読む字数が増える。
        入れていないコマでは「入れる」1つしか使えないので、畳んでおくほうが
        目に入る量が少ない。

        **キーは1つも割り当てない。** この道具はマウスで使うもので、キーを
        足すこと自体に値打ちは無い（→ 要件定義 7章）。
        """
        menu = QMenu("集中線", self)
        # 「入れる」と「消す」は1項目の文言を入れ替える（しっぽと同じ）。
        # 2つ並べると、片方は必ず押せない状態で場所だけ取る
        self.focus_toggle_action = self._act(
            "入れる",
            self.toggle_focus_lines,
            None,
            "選んだコマに放射状の線を引く。中心はあとから動かせる",
        )
        menu.addAction(self.focus_toggle_action)
        menu.addSeparator()

        # ここから下は集中線の入ったコマにだけ効く
        self.focus_actions: list[QAction] = []

        def add(label: str, slot, tip: str = "") -> QAction:
            action = self._act(label, slot, None, tip)
            menu.addAction(action)
            self.focus_actions.append(action)
            return action

        add("線を増やす", lambda: self.state.step_focus_count(1))
        add("線を減らす", lambda: self.state.step_focus_count(-1))
        menu.addSeparator()
        add("線を太く", lambda: self.state.step_focus_width(1))
        add("線を細く", lambda: self.state.step_focus_width(-1))
        menu.addSeparator()
        # 「白にする／黒に戻す」も入れる／消すと同じく1項目の文言を入れ替える
        # （要件定義 6.19。単純な色違いなので、色を選ぶメニューにはしない）
        self.focus_color_action = add(
            "白にする",
            self.toggle_focus_color,
            "線の色を黒と白で切り替える。暗いコマの上で使う",
        )
        menu.addSeparator()
        add(
            "形を振り直す",
            self.state.reseed_focus,
            "中心・本数・太さはそのままで、線のばらつきだけを作り直す",
        )

        # 右クリックのメニューが写して使う（→ `_items_to_copy`）。
        # **このメソッドは1度しか呼ばない。** 呼ぶたびに QAction を作り直す
        # ので、2度呼ぶとメニューバー側が古い項目を持ったまま取り残される
        self.focus_copy_items = self._items_to_copy(menu)
        return menu

    def _build_balloon_style_menu(self) -> QMenu:
        """種類を変えるメニュー（要件定義 10.1）。**フキダシのメニューに畳む。**

        種類が増えるぶん「◯◯にする」がそのまま縦に伸びる。今の種類へ
        変える項目は必ず1つ混ざっているので、**並べても常に1行は無駄に
        場所を取る**。集中線と同じ扱いで畳んでおく（→ 6.12、6.16）。

        一覧は `BALLOON_STYLE_LABELS` から作る。書き並べると、種類を
        足したときにここへ足し忘れて相互に変えられなくなる。

        **このメソッドは1度しか呼ばない。** 呼ぶたびに QAction を作り直す
        ので、2度呼ぶとメニューバー側が古い項目を持ったまま取り残される
        （→ `_build_focus_menu`）。
        """
        menu = QMenu(BALLOON_STYLE_MENU_LABEL, self)
        for style, name in BALLOON_STYLE_LABELS.items():
            action = self._act(
                f"{name}にする", lambda _=False, s=style: self.set_balloon_style(s)
            )
            menu.addAction(action)
            self.balloon_actions.append(action)

        # 右クリックのメニューが写して使う（→ `_items_to_copy`）
        self.balloon_style_copy_items = self._items_to_copy(menu)
        return menu

    def _build_text_menu(self) -> None:
        """セリフのメニュー。

        先頭に「作る」を置く。ここが選択中のセリフへの操作だけだと、
        1つも選んでいない間はメニュー全体がグレーになり、
        どこから作るのか分からなくなる（吹き出しで一度やった失敗）。
        """
        menu = self.menuBar().addMenu("セリフ(&X)")
        menu.addAction(self._tool_actions[TOOL_TEXT])
        menu.addSeparator()

        self.text_actions: list[QAction] = []

        def add(label: str, slot, shortcut: str | None = None) -> QAction:
            action = self._act(label, slot, shortcut)
            menu.addAction(action)
            self.text_actions.append(action)
            return action

        add("文字を入力...", self.edit_text, "F2")
        menu.addSeparator()

        self.vertical_action = add("縦書き", self.toggle_vertical, "F7")
        self.vertical_action.setCheckable(True)
        menu.addSeparator()

        for label, align in (("左寄せ", "left"), ("中央寄せ", "center"), ("右寄せ", "right")):
            add(label, lambda _=False, a=align: self.set_text_align(a))
        menu.addSeparator()

        add("大きく", lambda: self.step_text_size(1), "Ctrl+]")
        add("小さく", lambda: self.step_text_size(-1), "Ctrl+[")
        self.bold_action = add("太字", self.toggle_bold, "Ctrl+B")
        self.bold_action.setCheckable(True)
        menu.addSeparator()
        add("フォントを選ぶ...", self.choose_font)

        # 右クリックのメニューが写して使う（→ `_items_to_copy`）
        self.text_copy_items = self._items_to_copy(menu)

    def _build_menus(self) -> None:
        self._build_tool_actions()

        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction(self._act("新規作成", self.files.new_project, "Ctrl+N"))
        file_menu.addAction(
            self._act(
                "開く...",
                self.files.open_project,
                "Ctrl+O",
                f"作品フォルダの中の {PROJECT_FILENAME} を選ぶ",
            )
        )
        self.recent_project_action = self._act(
            "前回のファイルを開く",
            self.files.open_recent_project,
            None,
            "前回開いた・保存した作品を、選ぶ手間なしで開く",
        )
        file_menu.addAction(self.recent_project_action)
        file_menu.addSeparator()
        file_menu.addAction(self._act("保存", self.files.save_project, "Ctrl+S"))
        file_menu.addAction(
            self._act("名前を付けて保存...", self.files.save_project_as, "Ctrl+Shift+S")
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self._act(
                "PNG で書き出し...",
                self.export_png,
                "Ctrl+E",
                f"作品フォルダの {EXPORT_DIRNAME}/ に書き出す",
            )
        )
        file_menu.addSeparator()
        file_menu.addAction(self._act("終了", self.close, "Ctrl+Q"))

        edit_menu = self.menuBar().addMenu("編集(&E)")
        self.undo_action = self._act("元に戻す", self.state.undo, "Ctrl+Z")
        self.redo_action = self._act("やり直す", self.state.redo, "Ctrl+Y")
        # Ctrl+Shift+Z も「やり直す」に割り当てる。ソフトによって Ctrl+Y と
        # Ctrl+Shift+Z のどちらが「やり直す」かが割れているため、両方通す
        self.redo_action.setShortcuts(
            [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")]
        )
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        # **キーは割り当てない**（→ 要件定義 7章）。`Ctrl+D` は他のソフトで
        # 「選択を解除」に当たっていることがあり、意味が食い違う
        self.duplicate_action = self._act(
            "複製",
            self.state.duplicate_selected,
            None,
            "選んでいるものを写して、右下へ少しずらして置く",
        )
        edit_menu.addAction(self.duplicate_action)
        self.delete_action = self._act("削除", self.delete_selected, "Delete")
        edit_menu.addAction(self.delete_action)
        self.full_page_action = self._act(
            "ページ全面にコマを作る", self.add_full_page_panel, "Ctrl+Shift+A"
        )
        edit_menu.addAction(self.full_page_action)

        panel_menu = self.menuBar().addMenu("コマ(&M)")
        # 「作る」を先頭に置く。ここが選択中のコマへの操作だけだと、
        # 何も選んでいない間はメニュー全体がグレーになる（吹き出しでの失敗）
        panel_menu.addAction(self._tool_actions[TOOL_PANEL])
        panel_menu.addSeparator()
        for tool in (TOOL_SPLIT_H, TOOL_SPLIT_V, TOOL_SPLIT_SLANT):
            panel_menu.addAction(self._tool_actions[tool])
        panel_menu.addSeparator()
        self.slant_flip_action = self._act(
            "斜めの向きを反転",
            self.flip_slant,
            None,
            "斜めに割った2枚の傾きを / と \\ で入れ替える",
        )
        panel_menu.addAction(self.slant_flip_action)
        panel_menu.addSeparator()
        # 「ロック」と「ロックを解除」は1項目の文言を入れ替える
        # （しっぽの「消す／出す」と同じ → `_refresh`）
        self.lock_toggle_action = self._act(
            "ロック",
            self.toggle_panel_lock,
            None,
            "選んだコマを動かせなくする。中の画像やフキダシは今まで通り触れる",
        )
        panel_menu.addAction(self.lock_toggle_action)
        self.lock_all_action = self._act(
            "このページのコマをすべてロック", self.lock_all_panels
        )
        panel_menu.addAction(self.lock_all_action)
        self.unlock_all_action = self._act(
            "このページのコマのロックをすべて解除", self.unlock_all_panels
        )
        panel_menu.addAction(self.unlock_all_action)
        panel_menu.addSeparator()
        panel_menu.addMenu(self._build_focus_menu())

        image_menu = self.menuBar().addMenu("画像(&I)")
        self.paste_action = self._act(
            "貼り付け", self.paste_image, "Ctrl+V", "クリップボードの画像を置く"
        )
        self.open_image_action = self._act("ファイルから読み込み...", self.open_image_file)
        image_menu.addAction(self.paste_action)
        image_menu.addAction(self.open_image_action)
        image_menu.addSeparator()
        self.fit_action = self._act(
            "コマにフィット", self.fit_image, "Ctrl+Shift+F", "選択中の画像でコマを埋める"
        )
        image_menu.addAction(self.fit_action)
        # 回すのはつまみ（→ 6.3）。ここに出すのは戻す側だけで、キーは足さない。
        # 傾けるのは絵を見ながら合わせる操作なので、数値やキーでは代えられない
        self.reset_rotation_action = self._act(
            "回転をリセット",
            self.reset_image_rotation,
            None,
            "選択中の画像の傾きを 0 に戻す",
        )
        image_menu.addAction(self.reset_rotation_action)
        image_menu.addSeparator()
        image_menu.addAction(self._act("未使用ファイルを整理...", self.prune_assets))

        balloon_menu = self.menuBar().addMenu("フキダシ(&B)")
        # まず「作る」を置く。これが無いと、吹き出しを1つも選んでいない間は
        # メニュー全体がグレーになり、どこから作るのか分からなくなる
        for tool in BALLOON_TOOLS:
            balloon_menu.addAction(self._tool_actions[tool])
        balloon_menu.addSeparator()

        # ここから下は選択中の吹き出しに対する操作
        self.balloon_actions: list[QAction] = []
        # 種類を変える項目は畳む（→ `_build_balloon_style_menu`）。
        #
        # **畳んだ側の見出しは `balloon_actions` に入れない。** この QAction は
        # 下位の QMenu が持っているもので、誰かが `QAction.menu()` を呼んだ
        # 時点で QMenu ごと引き取られ、実体が消える。持ってよいのは `_act` で
        # 作った QAction（親がこのウィンドウ）だけ（→ `_items_to_copy`）。
        # 見出しを持たせたところ、メニューバーを辿ったあとの `_refresh` が
        # `Internal C++ object already deleted` で落ちた（2026-08-05 に実測）。
        #
        # 見出しは使えるままで、中身だけがグレーになる。集中線と同じ形
        style_menu_action = balloon_menu.addMenu(self._build_balloon_style_menu())
        balloon_menu.addSeparator()

        self.tail_action = self._act("しっぽを消す", self.toggle_tail)
        balloon_menu.addAction(self.tail_action)
        self.balloon_actions.append(self.tail_action)

        # 「入れる／消す」と同じく1項目の文言を入れ替える。2つ並べると、
        # 片方は必ず押しても何も変わらない状態で場所だけ取る
        self.tail_shape_action = self._act(
            TAIL_SHAPE_LABELS[TAIL_SHAPE_BUBBLES],
            self.toggle_tail_shape,
            tip="心の声・独り言に使う、円が並んだしっぽに変える",
        )
        balloon_menu.addAction(self.tail_shape_action)
        self.balloon_actions.append(self.tail_shape_action)

        # しっぽの向きを変える4つは右クリックには出さない。選択中の
        # フキダシだけでも右クリックのメニューは項目数が多く（実測13、
        # 外して10）、これ以上増やすと選びにくくなる（相談 2026-08-05）
        tail_turn_actions: list[QAction] = []
        for where, direction in TAIL_TURN_ITEMS:
            action = self._act(
                f"しっぽを{where}へ",
                lambda _=False, d=direction: self.turn_tail(d),
                tip=f"しっぽの向きを{where}に変えます。先端も一緒に回ります",
            )
            balloon_menu.addAction(action)
            self.balloon_actions.append(action)
            tail_turn_actions.append(action)

        self.attach_action = self._act("コマへの紐づけを解除", self.toggle_attachment)
        balloon_menu.addAction(self.attach_action)
        self.balloon_actions.append(self.attach_action)

        # 右クリックのメニューが写して使う（→ `_items_to_copy`）。
        # **畳んだ「種類を変える」は写せない**（QMenu を持ち帰れないため）。
        # 右クリック側では同じ控え（`balloon_style_copy_items`）から
        # 組み直す（→ `_context_menu`、集中線と同じ形）
        self.balloon_copy_items = self._items_to_copy(
            balloon_menu,
            extra_exclude=(style_menu_action, *tail_turn_actions),
        )

        self._build_sticker_menu()
        self._build_text_menu()

        tool_menu = self.menuBar().addMenu("道具(&T)")
        for action in self._tool_actions.values():
            tool_menu.addAction(action)

        page_menu = self.menuBar().addMenu("ページ(&P)")
        # 「追加」は必ず末尾、「挿入」は表示中のページの前。行き先の決まった
        # ほうを別の項目にしてある（要件定義 6.1）
        page_menu.addAction(
            self._act("ページを追加", self.add_page, "Ctrl+Shift+N", "末尾に1枚足す")
        )
        page_menu.addAction(
            self._act(
                "ページを挿入",
                self.insert_page,
                "Ctrl+Shift+I",
                "表示中のページの前に1枚差し込む",
            )
        )
        self.delete_page_action = self._act("ページを削除...", self.delete_page)
        page_menu.addAction(self.delete_page_action)
        page_menu.addSeparator()

        self.move_page_up_action = self._act(
            "ページを前へ移動", lambda: self.move_page_by(-1), "Ctrl+Shift+PgUp"
        )
        self.move_page_down_action = self._act(
            "ページを後ろへ移動", lambda: self.move_page_by(1), "Ctrl+Shift+PgDown"
        )
        page_menu.addAction(self.move_page_up_action)
        page_menu.addAction(self.move_page_down_action)
        page_menu.addSeparator()

        page_menu.addAction(
            self._act("ページサイズ...", self.change_page_size, None, "A4 相当 / B5 相当 / px 指定")
        )
        page_menu.addSeparator()
        page_menu.addAction(self._act("前のページ", self.prev_page, "PgUp"))
        page_menu.addAction(self._act("次のページ", self.next_page, "PgDown"))

        view_menu = self.menuBar().addMenu("表示(&V)")
        self.pages_toggle_action = self.pages_dock.toggleViewAction()
        self.pages_toggle_action.setText(PAGES_MENU_LABEL)
        view_menu.addAction(self.pages_toggle_action)
        view_menu.addSeparator()
        # 素の + / - とホイールでも拡大縮小できる（PageView 側で拾う）。
        # メニューには修飾キー付きのほうを出す。素のキーを割り当てると
        # 文字入力中に横取りしてしまうため
        view_menu.addAction(
            self._act("拡大", self.view.zoom_in, "Ctrl++", "+ キー / ホイール上でも拡大")
        )
        view_menu.addAction(
            self._act("縮小", self.view.zoom_out, "Ctrl+-", "- キー / ホイール下でも縮小")
        )
        view_menu.addAction(self._act("原寸で表示", self.zoom_actual, "Ctrl+1"))
        view_menu.addAction(self._act("ページ全体を表示", self.view.fit_page, "Ctrl+0"))

    def _build_toolbar(self) -> None:
        """道具箱。**一覧は `_tool_actions` から取る。**

        道具の名前を並べ直すと、道具メニュー（`_build_menus` の
        `tool_menu`）と2か所を直すことになる。道具を増やしたときに
        片方だけ直し忘れると、道具箱にだけ出ない項目ができてしまう。
        """
        bar = QToolBar("道具", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for action in self._tool_actions.values():
            bar.addAction(action)
        bar.addSeparator()
        bar.addAction(self._act("← 前ページ", self.prev_page))
        bar.addAction(self._act("次ページ →", self.next_page))

    def _build_status_bar(self) -> None:
        self.page_label = QLabel()
        self.hint_label = QLabel()
        self.statusBar().addPermanentWidget(self.hint_label)
        self.statusBar().addPermanentWidget(self.page_label)

    # -- 右クリックのメニュー ------------------------------------------------

    def _show_context_menu(self, x: float, y: float, global_pos) -> None:
        """画面を右クリックされた。押した場所に応じたメニューを出す。

        押した場所のものは `PageView` 側で既に選び直されている。項目の
        有効・無効はその選択を見て `_refresh` が決めているので、ここでは
        並べて出すだけでよい。
        """
        menu = self._context_menu(x, y)
        menu.exec(global_pos)
        # 押した場所を覚えている項目があるので使い回せない。毎回捨てる
        menu.deleteLater()

    def _context_menu(self, x: float, y: float) -> QMenu:
        """右クリックのメニューを組む。`x`, `y` は押した場所（シーンの px）。

        **項目は既にある QAction を写して並べる。作り直さない。**
        有効・無効の切り替えと文言の書き換え（「しっぽを消す／出す」など）は
        `_refresh` が1か所でやっている。ここで別の QAction を立てると同じ
        処理をもう1組書くことになり、片方だけ直し忘れる。

        例外は「ここに〜」「ここで〜」の項目だけ。押した場所を持てるのは
        右クリックだけで、メニューバー側には対応する項目が無い（場所が
        決まらないため、道具に持ち替える形になっている）。
        """
        menu = QMenu(self)
        # 区切り線が薄くて目立たないとの指摘（2026-08-05）を受けて明るくする。
        # このメニューはここでしか作らないので、メニューバー側の見た目には響かない。
        menu.setStyleSheet(
            "QMenu::separator { height: 1px; background: #cccccc; margin: 4px 8px; }"
        )
        self._show_tips_in_status_bar(menu)
        state = self.state

        if state.selected_text is not None:
            self._copy_actions(menu, self.text_copy_items)

        elif state.selected_sticker is not None:
            self._copy_actions(menu, self.sticker_copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("text",))

        elif state.selected_balloon is not None:
            # **メニューバーと同じものを畳んで出す**（集中線と同じ形 → 下）。
            # 種類を写せないのは QMenu を持ち帰れないため（→ `_items_to_copy`）
            self._copy_actions(
                menu.addMenu(BALLOON_STYLE_MENU_LABEL), self.balloon_style_copy_items
            )
            self._copy_actions(menu, self.balloon_copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("sticker", "text"))

        elif state.selected_image is not None:
            menu.addAction(self.fit_action)
            # 傾いていないときは出さない。押しても何も起きない項目が
            # 並ぶと、メニューを読む手間だけが増える（→ 6.12）
            if state.selected_image.rotation != 0.0:
                menu.addAction(self.reset_rotation_action)
            menu.addSeparator()
            # 踏み込んで画像を選んだ状態でも、差し替えと読み込みは要る。
            # ここに無いと、いったん Esc でコマへ戻る手数が挟まる
            self._menu_act(
                menu,
                REPLACE_IMAGE_LABEL,
                lambda _checked=False, i=state.selected_image.id: (
                    self.replace_image_file(i)
                ),
                tip=REPLACE_IMAGE_TIP,
            )
            menu.addAction(self.open_image_action)

        elif state.selected_panel is not None:
            self._add_split_here(menu, x, y)
            menu.addAction(self.slant_flip_action)
            menu.addAction(self.lock_toggle_action)
            # **メニューバーと同じものを畳んで出す。** 並べるのは同じ
            # QAction なので、有効・無効と「入れる／消す」の文言は
            # `_refresh` が1か所で面倒を見たままになる（→ 6.12）。
            # ここで作り直すと、メニューバー側が古い項目を持ったまま残る
            self._copy_actions(menu.addMenu("集中線"), self.focus_copy_items)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("balloon", "sticker", "text"))
            menu.addSeparator()
            menu.addAction(self.paste_action)
            menu.addAction(self.open_image_action)
            self._add_image_here(menu, state.selected_panel, x, y)

        else:
            # 何も無いところ。選択に効く項目はどれも使えないので出さない。
            # 代わりに、ここでしか呼べない「元に戻す」を添える
            self._add_place_here(menu, x, y, ("panel", "balloon", "sticker", "text"))
            menu.addAction(self.full_page_action)
            menu.addSeparator()
            menu.addAction(self.undo_action)
            menu.addAction(self.redo_action)
            return menu

        menu.addSeparator()
        # 複製と削除は「選んでいるものへの操作」で、どの品書きにも要る。
        # 分岐の外に置いて、種類を足したときに片方だけ抜けないようにする
        menu.addAction(self.duplicate_action)
        menu.addAction(self.delete_action)
        return menu

    def _show_tips_in_status_bar(self, menu: QMenu) -> None:
        """カーソルを乗せた項目の説明をステータスバーに出す。

        **宛先はこのウィンドウだと明示する。** メニューバーから開いた
        メニューには「呼び出し元のウィジェット」があり、Qt はそこへ説明を
        送るのでステータスバーまで届く。右クリックのメニューには呼び出し元が
        無いため、Qt は QMenu 自身へ送る。**説明は親へ伝わらないので、
        そのままでは誰も受け取らない**（2026-08-05 に実測。メニューへ送ると
        空、ウィンドウへ送ると出る）。

        `hovered` はキーボードの上下でも鳴るので、マウスを使わない場合も
        同じように出る。閉じたら消すのは、選び終わったあとに古い説明が
        残らないようにするため。
        """
        menu.hovered.connect(lambda action: action.showStatusText(self))
        menu.aboutToHide.connect(self.statusBar().clearMessage)

    def _items_to_copy(
        self, source: QMenu, extra_exclude: tuple[QAction, ...] = ()
    ) -> list[QAction | None]:
        """メニューバーのメニューから、右クリック側へ写す項目を控えておく。

        **QMenu も、そのメニューが持つ区切り線も持ち帰らない。**
        持てるのは `_act` で作った QAction（親がこのウィンドウ）だけで、
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

        **道具の切り替え（「フキダシを追加」など）は外す。** 右クリック側は
        押した場所が分かっているので「ここに〜」を別に出しており、道具に
        持ち替える項目まで並べると、同じことが2通り並ぶ。

        `extra_exclude` は道具以外で個別に外したい項目（→ しっぽの
        付け根を細かく選ぶ3項目、`_build_menus`）。
        """
        tools = set(self._tool_actions.values()) | set(extra_exclude)
        items: list[QAction | None] = []
        for action in source.actions():
            if action in tools:
                continue
            items.append(None if action.isSeparator() else action)
        return items

    def _copy_actions(self, menu: QMenu, items: list[QAction | None]) -> None:
        """控えておいた項目を右クリックのメニューへ並べる。

        QAction は写しても実体は1つなので、有効・無効も文言も
        メニューバー側と自動で揃う。`None` は区切り線の印
        （→ `_items_to_copy`）。
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
        items = (
            ("panel", "コマ", lambda: self.view.add_panel_at(x, y)),
            # `slot` が None ＝ 種類を畳んで下に出す印（→ 下の分岐）
            ("balloon", BALLOON_PLACE_HERE_NAME, None),
            *(
                (
                    "sticker",
                    name,
                    lambda _=False, k=kind: self.view.add_sticker_at(x, y, k),
                )
                for kind, name in STICKER_KIND_LABELS.items()
            ),
            ("text", "セリフ", lambda: self.view.add_text_at(x, y)),
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
        sub = menu.addMenu(label)
        sub.menuAction().setStatusTip(tip)
        self._show_tips_in_status_bar(sub)
        for style, name in BALLOON_STYLE_LABELS.items():
            self._menu_act(
                sub,
                name,
                lambda _=False, s=style: self.view.add_balloon_at(x, y, s),
                tip=place_here_label(name, first=True),
            )

    def _add_image_here(self, menu: QMenu, panel: Panel, x: float, y: float) -> None:
        """カーソルの下に画像があれば、その画像に効く項目を出す。

        **コマを選んだままでも画像を差し替え・削除できるようにする。**
        画像を選ぶにはダブルクリックで一段踏み込む必要があり（要件定義 6.3）、
        右クリックしただけではコマが選ばれる。削除の項目が無かった頃は、
        メニューに並ぶのが「コマを削除」だけになり、**画像を消すつもりで
        コマを消す**取り違えが実際に起きた。差し替えも同じ理由でここに出す。
        """
        image = image_at(panel, x, y)
        if image is None:
            return
        self._menu_act(
            menu,
            REPLACE_IMAGE_LABEL,
            lambda _checked=False, i=image.id: self.replace_image_file(i),
            tip=REPLACE_IMAGE_TIP,
        )
        self._menu_act(
            menu,
            "この画像を削除",
            lambda _checked=False, i=image.id: self.delete_image(i),
        )

    def _add_split_here(self, menu: QMenu, x: float, y: float) -> None:
        """押した場所で1回きり割る項目。

        メニューバー側は道具の切り替え（選んでから、割る場所を押す）だが、
        右クリックは押した場所が既に分かっているので、その場で割る。

        **前置きを出すのは1つめだけ**（→ `split_here_label`）。3つとも
        必ず一緒に出るので、1つめは常に「横」になる。
        """
        for index, (name, tool) in enumerate(
            (
                ("横に割る", TOOL_SPLIT_H),
                ("縦に割る", TOOL_SPLIT_V),
                ("斜めに割る", TOOL_SPLIT_SLANT),
            )
        ):
            self._menu_act(
                menu,
                split_here_label(name, first=index == 0),
                # 既定値で受けているのは、triggered が渡す checked を
                # tool の位置で受け取らないようにするため
                lambda _checked=False, t=tool: self.view.split_at(x, y, t),
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

    def zoom_actual(self) -> None:
        """紙に刷ったときと同じ大きさで表示する。

        px で作る道具なので、原寸（1シーンpx＝1画面px）で一度確かめられると
        文字の詰まり具合や余白の判断がしやすい。
        """
        percent = self.view.zoom_percent()
        if percent <= 0:
            return
        self.view.zoom_by(100.0 / percent, at_mouse=False)

    # -- 表示の更新 --------------------------------------------------------

    def _refresh(self) -> None:
        self.setWindowTitle(self._title())
        size = self.state.page.size
        self.page_label.setText(
            f"ページ {self.state.page_index + 1} / {self.state.page_count}"
            f"（{size.w:.0f} × {size.h:.0f} px）"
        )

        self.hint_label.setText(self._hint())

        # 「ページ [n]/総数」の見出しは PageJumpBar が自分で追いかける
        index, count = self.state.page_index, self.state.page_count
        self.delete_page_action.setEnabled(count > 1)
        self.move_page_up_action.setEnabled(index > 0)
        self.move_page_down_action.setEnabled(index < count - 1)

        history = self.state.history
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
        target = self.delete_target()
        self.delete_action.setEnabled(target is not None)
        self.delete_action.setText("削除" if target is None else f"{target[0]}を削除")
        # 複製も対象を名前に出す（→ 6.15）。**斜めに割ったコマでも押せるまま**に
        # する。グレーにすると「写せない」ことは伝わっても理由が伝わらず、
        # 出所が斜めであることに気づけない（断りは `duplicate_selected` が出す）
        name = object_label(self.state.selected_object)
        self.duplicate_action.setEnabled(bool(name))
        self.duplicate_action.setText(f"{name}を複製" if name else "複製")
        image = self.state.selected_image
        self.fit_action.setEnabled(image is not None)
        self.reset_rotation_action.setEnabled(image is not None and image.rotation != 0.0)
        self.slant_flip_action.setEnabled(self.state.selected_slant_pair is not None)

        # ロック（→ 6.17）。「ロック／ロックを解除」は集中線の「入れる／消す」
        # と同じく1項目の文言を入れ替える。一括の2項目は、押しても何も
        # 変わらない側をグレーにする（→ `lock_all_panels` の無変化ガード）
        self.lock_toggle_action.setEnabled(self.state.selected_panel is not None)
        self.lock_toggle_action.setText(
            "ロックを解除" if self.state.is_locked_selection else "ロック"
        )
        page_panels = self.state.page.panels
        self.lock_all_action.setEnabled(
            bool(page_panels) and not all(p.locked for p in page_panels)
        )
        self.unlock_all_action.setEnabled(any(p.locked for p in page_panels))

        # 集中線。入れる／消すはコマを選んでいれば押せ、調整の5項目は
        # 入っているときだけ（→ 6.16）
        panel = self.state.selected_panel
        focus = self.state.selected_focus
        self.focus_toggle_action.setEnabled(panel is not None)
        self.focus_toggle_action.setText("消す" if focus is not None else "入れる")
        for action in self.focus_actions:
            action.setEnabled(focus is not None)
        if focus is not None:
            self.focus_color_action.setText("黒に戻す" if focus.white else "白にする")

        text = self.state.selected_text
        for action in self.text_actions:
            action.setEnabled(text is not None)
        self.bold_action.setChecked(text is not None and text.font.bold)
        self.vertical_action.setChecked(
            text is not None and text.direction == "vertical"
        )

        balloon = self.state.selected_balloon
        for action in self.balloon_actions:
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

        sticker = self.state.selected_sticker
        for action in self.sticker_actions:
            action.setEnabled(sticker is not None)
        if sticker is not None:
            self.sticker_attach_action.setText(
                "コマへの紐づけを解除"
                if sticker.attached_panel_id
                else "重なっているコマに紐づける"
            )

    def _hint(self) -> str:
        """いま何を選んでいるかを状態表示に出す。

        コマと画像は見た目が似ているので、文字でも示さないと
        どちらを動かしているのか分からなくなる。
        """
        image = self.state.selected_image
        if image is not None:
            r = image.rect
            w, h = image.src_px
            return f"画像を選択中: {r.w:.0f} × {r.h:.0f} px（元 {w}×{h} px）"

        text = self.state.selected_text
        if text is not None:
            font = text.font
            weight = " 太字" if font.bold else ""
            tied = "フキダシに紐づけ" if text.attached_balloon_id else "紐づけなし"
            lines = text.content.count("\n") + 1 if text.content else 0
            body = f"{lines} 行" if lines else "（未入力）"
            lay = "縦書き" if text.direction == "vertical" else "横書き"
            return (
                f"セリフを選択中: {body} / {font.family} {self._size_label(font.size_px)}{weight}"
                f" / {lay} / {align_label(text.align, text.direction)} / {tied}"
            )

        sticker = self.state.selected_sticker
        if sticker is not None:
            r = sticker.rect
            w, h = sticker.src_px
            tied = "コマに紐づけ" if sticker.attached_panel_id else "紐づけなし"
            return (
                f"{object_label(sticker)}を選択中: "
                f"{r.w:.0f} × {r.h:.0f} px（元 {w}×{h} px）/ {tied}"
            )

        balloon = self.state.selected_balloon
        if balloon is not None:
            r = balloon.rect
            kind = BALLOON_STYLE_LABELS.get(balloon.style, balloon.style)
            tied = "コマに紐づけ" if balloon.attached_panel_id else "紐づけなし"
            return f"フキダシを選択中: {kind} / {r.w:.0f} × {r.h:.0f} px / {tied}"

        panel = self.state.selected_panel
        if panel is not None:
            b = panel.shape.bounds()
            count = len(panel.children)
            inside = f" / 画像 {count} 枚" if count else ""
            # 集中線はつまみ以外に見分ける手がかりが無いので、本数を添える
            lines = (
                f" / 集中線 {panel.focus_lines.count} 本"
                if panel.focus_lines is not None
                else ""
            )
            # ロック中は見た目を変えないので、気づける手がかりはここだけ
            # （つまみを出さないのと合わせて → 要件定義 6.17）
            locked = " / ロック中" if self.state.is_locked_selection else ""
            return f"コマを選択中: {b.w:.0f} × {b.h:.0f} px{inside}{lines}{locked}"

        return "コマ未選択"

    def _sync_tool_actions(self) -> None:
        self._tool_actions[self.state.tool].setChecked(True)

    def _title(self) -> str:
        name = self.state.project_dir.name if self.state.project_dir else "無題"
        mark = " *" if self.state.is_dirty else ""
        return f"{name}{mark} - {APP_TITLE}"

    # -- 編集 --------------------------------------------------------------

    def delete_target(self) -> tuple[str, object] | None:
        """いま「削除」で消えるもの（呼び名と、消す処理）。何も選んでいなければ None。

        **項目名と、実際に消えるものを1か所で決める。** 別々に持つと
        「削除」と書いてあるのに違うものが消える状態を作れてしまう。

        実際、画像を消すつもりでコマを消した、という取り違えが起きている。
        コマの中の画像を選ぶにはダブルクリックで一段踏み込む必要があり
        （要件定義 6.3）、右クリックしただけではコマが選ばれるため。
        呼び名を項目に出せば、押す前にカーソルの下で気づける。
        """
        if self.state.selected_image is not None:
            return "画像", self.delete_image
        if self.state.selected_text is not None:
            return "セリフ", self.delete_text
        if self.state.selected_sticker is not None:
            return object_label(self.state.selected_sticker), self.delete_sticker
        if self.state.selected_balloon is not None:
            return "フキダシ", self.delete_balloon
        if self.state.selected_panel is not None:
            # ロックしたコマは消せない（→ 要件定義 6.17）。ここで None にすると
            # 「削除」がグレーになり、押す前に気づける（ロック中はステータス
            # 表示にも出る → `_hint`）
            if self.state.is_locked_selection:
                return None
            return "コマ", self.delete_panel
        return None

    def delete_selected(self) -> None:
        """Delete キー。選んでいるものに応じて消し分ける。"""
        target = self.delete_target()
        if target is not None:
            target[1]()

    def delete_balloon(self) -> None:
        """フキダシを消す。上に乗っていたセリフは残り、紐づけだけ外れる。"""
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        balloon_id = balloon.id
        with self.state.edit_page("フキダシの削除") as page:
            page.remove_floating(balloon_id)
        self.state.select(None)
        self.state.message.emit("フキダシを削除しました")

    def delete_sticker(self) -> None:
        """マークを消す。紐づいていたコマはそのまま残る。"""
        sticker = self.state.selected_sticker
        if sticker is None:
            return
        label = object_label(sticker)
        sticker_id = sticker.id
        with self.state.edit_page(f"{label}の削除") as page:
            page.remove_floating(sticker_id)
        self.state.select(None)
        self.state.message.emit(f"{label}を削除しました")

    def toggle_sticker_attachment(self) -> None:
        """マークのコマへの紐づけを付け外しする。

        フキダシと同じ扱い（→ `toggle_attachment`）。紐づけておくと、
        コマを動かしたときにマークが付いて回る。
        """
        sticker = self.state.selected_sticker
        if sticker is None:
            return
        if sticker.attached_panel_id is not None:
            self.state.set_sticker_attachment(sticker.id, None)
            self.state.message.emit("コマへの紐づけを解除しました")
            return

        panel_id = attach_target(self.state.page, sticker.rect)
        if panel_id is None:
            self.state.message.emit("重なっているコマがありません")
            return
        self.state.set_sticker_attachment(sticker.id, panel_id)
        self.state.message.emit("コマに紐づけました")

    def delete_panel(self) -> None:
        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id
        with self.state.edit_page("コマの削除") as page:
            page.remove_panel(panel_id)
        self.state.select(None)
        self.state.message.emit("コマを削除しました")

    def flip_slant(self) -> None:
        """斜めに割った2枚の傾きを入れ替える。

        外側の矩形は変わらないので、隣のコマとの位置関係は動かない。
        """
        if self.state.flip_slant():
            self.state.message.emit("斜めの向きを反転しました")

    def toggle_panel_lock(self) -> None:
        """選んだコマのロックを付け外しする（要件定義 6.17）。

        1項目の文言を入れ替える形にしてある（集中線の「入れる／消す」と
        同じ）。斜めの組は `state.set_panel_locked` の側で両方に効かせる。
        """
        locked_before = self.state.is_locked_selection
        if self.state.toggle_panel_lock():
            self.state.message.emit(
                "コマのロックを解除しました" if locked_before else "コマをロックしました"
            )

    def lock_all_panels(self) -> None:
        if self.state.lock_all_panels():
            self.state.message.emit("このページのコマをすべてロックしました")

    def unlock_all_panels(self) -> None:
        if self.state.unlock_all_panels():
            self.state.message.emit("このページのコマのロックをすべて解除しました")

    def toggle_focus_lines(self) -> None:
        """選んだコマの集中線を入れる／消す（要件定義 6.16）。

        1項目の文言を入れ替える形にしてある（しっぽの「消す／出す」と
        同じ）。どちらが押せるかは選んだコマで決まるので、2つ並べると
        片方は必ずグレーで場所だけ取る。
        """
        if self.state.selected_focus is None:
            self.state.add_focus_lines()
        elif self.state.remove_focus_lines():
            self.state.message.emit("集中線を消しました")

    def toggle_focus_color(self) -> None:
        """選んだコマの集中線の色を黒⇄白で切り替える（要件定義 6.19）。"""
        if self.state.toggle_focus_color():
            focus = self.state.selected_focus
            color = "白" if focus is not None and focus.white else "黒"
            self.state.message.emit(f"集中線の色: {color}")

    def delete_image(self, image_id: str | None = None) -> None:
        """画像だけ消す。入っていたコマは残り、そのコマを選び直す。

        画像の実体（assets/）はここでは消さない。Undo で戻せなくなるため。
        余った実体は「未使用ファイルを整理」で片付ける（要件定義 5章）。

        `image_id` を渡さなければ選択中の画像。右クリックのメニューからは
        **カーソルの下の画像を名指しで渡す。** コマを選んだままでも消せる
        ようにするため（ダブルクリックで踏み込まないと画像は選べない）。
        """
        if image_id is None:
            image = self.state.selected_image
            if image is None:
                return
            image_id = image.id
        panel = self.state.page.panel_of_image(image_id)
        panel_id = panel.id if panel is not None else None

        with self.state.edit_page("画像の削除") as page:
            target = page.panel_of_image(image_id)
            if target is not None:
                target.children = [c for c in target.children if c.id != image_id]

        self.state.select(panel_id)
        self.state.message.emit("画像を削除しました")

    # -- 画像 --------------------------------------------------------------

    def _target_panel(self) -> Panel | None:
        """画像を入れるコマ。画像を選んでいれば、それが入っているコマ。"""
        panel = self.state.selected_panel
        if panel is None:
            image = self.state.selected_image
            if image is not None:
                panel = self.state.page.panel_of_image(image.id)
        if panel is None:
            self.state.message.emit("先にコマを選んでください")
        return panel

    def _place_image(self, panel_id: str, data: bytes, source: str) -> bool:
        try:
            image = self.state.place_image(panel_id, data)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "画像を置けません", str(e))
            return False
        self._image_message(image, source, "置きました")
        return True

    def _image_message(self, image: ImageObject, source: str, what: str) -> None:
        """置いた・差し替えたときの状態表示。

        埋めたければ Ctrl+Shift+F、という案内をどちらにも同じ形で出す
        （置いた直後は「収める」大きさで始まる → 要件定義 6.3）。
        """
        w, h = image.src_px
        self.state.message.emit(
            f"画像を{what}（{source} / {w}×{h} px）。コマを埋めるなら Ctrl+Shift+F"
        )

    def paste_image(self) -> None:
        panel = self._target_panel()
        if panel is None:
            return
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            self.state.message.emit("クリップボードに画像がありません")
            return
        try:
            data = to_png_bytes(image)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "画像を置けません", str(e))
            return
        self._place_image(panel.id, data, "貼り付け")

    def _choose_image_file(self) -> tuple[bytes, str] | None:
        """画像ファイルを選ばせて、中身と名前を返す。やめた・読めなければ None。

        **始まる場所は保存・作品を開くと揃える。** 下書きは作品の隣に
        置かれることが多いので、そこから始めれば辿り直さずに済む。
        空文字を渡すと**アプリを起動したフォルダ**から始まり、
        毎回どこか分からない場所を辿ることになる。
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "画像を選ぶ", self.files.dialog_start_dir(), IMAGE_FILE_FILTER
        )
        if not path:
            return None
        file = pathlib.Path(path)
        try:
            data = file.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "画像を読めません", f"{file}\n{e}")
            return None
        return data, file.name

    def open_image_file(self) -> None:
        """コマに入れる画像を選ぶ。**既にある画像は消さず、上に重ねる。**

        背景の絵の上にキャラの絵を置く使い方があるので、1コマに何枚でも
        入れられる。入れ替えたいときは「この画像を差し替え」を使う
        （→ `replace_image_file`）。
        """
        panel = self._target_panel()
        if panel is None:
            return
        chosen = self._choose_image_file()
        if chosen is None:
            return
        data, name = chosen
        self._place_image(panel.id, data, name)

    def replace_image_file(self, image_id: str | None = None) -> None:
        """画像を1枚、選んだファイルの絵に差し替える。

        `image_id` を渡さなければ選択中の画像。右クリックのメニューからは
        **カーソルの下の画像を名指しで渡す**（コマを選んだままでも差し替え
        られるように → `_add_image_here`）。

        重なり順は元の画像を引き継ぐので、背景だけ差し替えても手前の絵は
        手前のまま（→ `EditorState.replace_image`）。
        """
        if image_id is None:
            image = self.state.selected_image
            if image is None:
                return
            image_id = image.id
        chosen = self._choose_image_file()
        if chosen is None:
            return
        data, name = chosen
        try:
            placed = self.state.replace_image(image_id, data)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "画像を置けません", str(e))
            return
        if placed is None:
            return
        self._image_message(placed, name, "差し替えました")

    def fit_image(self) -> None:
        """選択中の画像でコマを埋める。はみ出た分はコマの形で切り抜かれる。"""
        image = self.state.selected_image
        if image is None:
            self.state.message.emit("先に画像を選んでください（コマの中をダブルクリック）")
            return
        panel = self.state.page.panel_of_image(image.id)
        if panel is None:
            return

        # 傾けてあれば、傾いたまま覆う大きさにする。ここで 0 に戻すと、
        # 合わせた傾きが黙って消える（→ 要件定義 6.3）
        rect = cover_rect_in(panel.shape.bounds(), image.src_px, image.rotation)
        if rect == image.rect:
            return
        image_id = image.id
        with self.state.edit_page("コマにフィット") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.rect = rect
        self.state.message.emit(f"コマを埋めました（{rect.w:.0f} × {rect.h:.0f} px）")

    def reset_image_rotation(self) -> None:
        """選択中の画像の傾きを 0 に戻す。

        回しすぎて戻せなくなる状態を作らないための逃げ道。つまみで 0 ちょうどに
        合わせるのは難しく、Undo は回した直後にしか使えない。
        """
        image = self.state.selected_image
        if image is None or image.rotation == 0.0:
            return
        image_id = image.id
        with self.state.edit_page("回転をリセット") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.rotation = 0.0
        self.state.message.emit("傾きを戻しました")

    # -- 吹き出し ----------------------------------------------------------

    def set_balloon_style(self, style: str) -> None:
        balloon = self.state.selected_balloon
        if balloon is None or balloon.style == style:
            return
        self.state.set_balloon_style(balloon.id, style)
        self.state.message.emit(f"{BALLOON_STYLE_LABELS.get(style, style)}にしました")

    def toggle_tail(self) -> None:
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        enabled = not balloon.tail.enabled
        self.state.set_tail_enabled(balloon.id, enabled)
        self.state.message.emit("しっぽを出しました" if enabled else "しっぽを消しました")

    def toggle_tail_shape(self) -> None:
        """三角のしっぽ ⇄ 丸い飛びしっぽ（→ 要件定義 10.1）。"""
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        shape = (
            TAIL_SHAPE_TRIANGLE
            if balloon.tail.shape == TAIL_SHAPE_BUBBLES
            else TAIL_SHAPE_BUBBLES
        )
        self.state.set_tail_shape(balloon.id, shape)
        self.state.message.emit(
            "しっぽを丸くしました（心の声・独り言）"
            if shape == TAIL_SHAPE_BUBBLES
            else "しっぽを三角に戻しました"
        )

    def turn_tail(self, direction: str) -> None:
        """しっぽの向きを変える。**先端も一緒に回る**（→ 6.4）。

        付け根だけを動かすと、先端と反対側では本体に隠れて針に痩せる。
        付け根の細かい寄せはひし形の印のドラッグが受け持つ。
        """
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        if not balloon.tail.enabled:
            self.state.message.emit("しっぽが出ていません")
            return
        self.state.turn_tail(balloon.id, direction)
        self.state.message.emit(f"しっぽを{TAIL_TURN_LABELS[direction]}へ向けました")

    def toggle_attachment(self) -> None:
        """コマへの紐づけを付けたり外したり（要件定義 6.4「手動で解除可」）。

        紐づいていれば外す。外れていれば、いま重なっているコマに付け直す。
        """
        balloon = self.state.selected_balloon
        if balloon is None:
            return

        if balloon.attached_panel_id is not None:
            self.state.set_attachment(balloon.id, None)
            self.state.message.emit("紐づけを解除しました。コマを動かしても付いてきません")
            return

        panel_id = attach_target(self.state.page, balloon.rect)
        if panel_id is None:
            self.state.message.emit("重なっているコマがありません")
            return
        self.state.set_attachment(balloon.id, panel_id)
        self.state.message.emit("コマに紐づけました。コマを動かすと付いてきます")

    # -- セリフ ------------------------------------------------------------

    def edit_text(self) -> None:
        text = self.state.selected_text
        if text is None:
            self.state.message.emit("先にセリフを選んでください")
            return
        self.view.begin_text_edit(text.id)

    def set_text_align(self, align: str) -> None:
        text = self.state.selected_text
        if text is None or text.align == align:
            return
        self.state.set_text_align(text.id, align)
        self.state.message.emit(f"整列: {align_label(align, text.direction)}")

    def toggle_vertical(self) -> None:
        """縦書きと横書きを入れ替える。

        セリフごとに持つ。1 ページの中に縦書きのセリフと横書きの効果音が
        混ざるのが普通なので、作品全体の設定にはしない。
        """
        text = self.state.selected_text
        if text is None:
            self.state.message.emit("先にセリフを選んでください")
            return
        to_vertical = text.direction != "vertical"
        self.state.set_text_direction(
            text.id, "vertical" if to_vertical else "horizontal"
        )
        self.state.message.emit(
            "縦書きにしました" if to_vertical else "横書きにしました"
        )

    def step_text_size(self, direction: int) -> None:
        """文字を1段階だけ大きく／小さくする。

        数値を打ち込ませるより、押して確かめるほうが速い。
        """
        text = self.state.selected_text
        if text is None:
            return
        size = round(text.font.size_px + direction * TEXT_SIZE_STEP_PX, 2)
        size = min(max(size, TEXT_SIZE_MIN_PX), TEXT_SIZE_MAX_PX)
        if size == text.font.size_px:
            return
        self.state.set_text_font(text.id, size_px=size)
        self.state.message.emit(f"文字の大きさ: {self._size_label(size)}")

    def toggle_bold(self) -> None:
        text = self.state.selected_text
        if text is None:
            return
        bold = not text.font.bold
        self.state.set_text_font(text.id, bold=bold)
        self.state.message.emit("太字にしました" if bold else "太字をやめました")

    def choose_font(self) -> None:
        """種類・大きさ・太さをまとめて選ぶ。

        **今の書式を窓に持っていき、選んだ大きさを持ち帰る。** 以前は
        `QFont(family)` だけを渡していたため、窓には Qt の既定である
        12pt が出ていた。**選択中のセリフとは無関係な数字**で、しかも
        窓で大きさを変えても捨てていたので、そこから文字の大きさを
        変えることができなかった（2026-08-03 に直した）。

        窓はポイントでしか喋らないので、`PT_TO_PX` で換算して渡す。
        画面の解像度で Qt に換算させると、紙の上での大きさと合わない。
        """
        text = self.state.selected_text
        if text is None:
            self.state.message.emit("先にセリフを選んでください")
            return
        current = QFont(text.font.family)
        current.setPointSizeF(text.font.size_px / PT_TO_PX)
        current.setBold(text.font.bold)

        chosen, ok = QFontDialog.getFont(current, self, "フォントを選ぶ")
        if not ok:
            return

        size = self._size_px_of(chosen, fallback=text.font.size_px)
        self.state.set_text_font(
            text.id, family=chosen.family(), size_px=size, bold=chosen.bold()
        )
        self.state.message.emit(f"フォント: {chosen.family()} {self._size_label(size)}")

    @staticmethod
    def _size_px_of(font: QFont, fallback: float) -> float:
        """窓が返した大きさをページの px に直す。

        `pointSizeF()` は、大きさが px で指定された QFont では -1 を返す。
        窓には pt で渡しているので普通は起きないが、**戻り値を信じて
        負の大きさを保存すると描画が消える**ので、その場合は元の値を使う。
        """
        points = font.pointSizeF()
        if points <= 0:
            return fallback
        size = round(points * PT_TO_PX, 2)
        return min(max(size, TEXT_SIZE_MIN_PX), TEXT_SIZE_MAX_PX)

    @staticmethod
    def _size_label(size_px: float) -> str:
        """px とポイントを併記する。

        px だけだと**画面の点の数と取り違える**。実際にはページの座標
        （150dpi 換算）なので、20px は紙の上では約 9.6pt にしかならない。
        フォント設定の窓もポイントで喋るので、そちらとも突き合わせられる。
        """
        return f"{size_px:.0f}px（約 {size_px / PT_TO_PX:.0f}pt）"

    def delete_text(self) -> None:
        text = self.state.selected_text
        if text is None:
            return
        text_id = text.id
        with self.state.edit_page("セリフの削除") as page:
            page.remove_floating(text_id)
        self.state.select(None)
        self.state.message.emit("セリフを削除しました")

    def prune_assets(self) -> None:
        """どこからも使われていない画像を assets/_unused/ へ移す。

        保存時に自動で行わない理由は要件定義 5章。Undo で戻した画像の
        実体が消えてしまうため、利用者が選んだときだけ動かす。
        """
        if self.state.project_dir is None:
            self.state.message.emit("先に作品を保存してください")
            return
        if self.state.is_dirty:
            QMessageBox.information(
                self,
                "先に保存してください",
                "保存していない変更があります。\n"
                "保存前に整理すると、まだ保存されていない画像まで未使用と判定されます。",
            )
            return

        moved = prune_unused_assets(self.state.project, self.state.project_dir)
        if not moved:
            self.state.message.emit("使われていない画像はありませんでした")
            return
        QMessageBox.information(
            self,
            "整理しました",
            f"{len(moved)} 件を assets/_unused/ へ移しました。\n"
            "削除はしていないので、戻したいときはフォルダから取り出せます。",
        )
        self.state.message.emit(f"{len(moved)} 件を _unused/ へ移しました")

    def add_full_page_panel(self) -> None:
        rect = full_page_rect(self.state.page, self.state.settings)
        with self.state.edit("コマの追加") as project:
            panel = project.add_panel(project.pages[self.state.page_index], rect)
        self.state.select(panel.id)

    # -- ページ ------------------------------------------------------------

    def add_page(self) -> None:
        """**末尾**に1枚足して、そこへ移る。

        以前は「表示中のページの次」に足していたが、一覧のカーソルが
        どこにあるかを常に意識しているわけではないので、**思っていない
        場所にページができる**ことがあった。行き先の決まった「追加」と、
        位置を狙う「挿入」に分けてある（要件定義 6.1）。
        """
        at = self.state.add_page()
        self.state.message.emit(f"末尾に {at + 1} ページ目を追加しました")

    def insert_page(self) -> None:
        """表示中のページの**前**に1枚差し込んで、そこへ移る。

        差し込んだページがその番号を引き継ぎ、それまでのページは1つ
        後ろへ下がる（表計算の「行の挿入」と同じ向き）。
        """
        at = self.state.insert_page()
        self.state.message.emit(
            f"表示中のページの前に差し込みました（{at + 1} ページ目）"
        )

    def delete_page(self) -> None:
        """ページを消す。**必ず確認する**（要件定義 6.1）。

        コマ1つの削除と違い、ページ1枚には積み上げた作業がまるごと乗る。
        Delete キーには割り当てず、メニューからだけにしてあるのも同じ理由。
        """
        if self.state.page_count <= 1:
            self.state.message.emit("最後の1ページは削除できません")
            return

        page = self.state.page
        count = len(page.panels) + len(page.floating)
        placed = f"コマ・フキダシ・セリフが {count} 個置かれています。\n" if count else ""
        answer = QMessageBox.question(
            self,
            "ページを削除しますか",
            f"{self.state.page_index + 1} ページ目を削除します。\n"
            f"{placed}元に戻す（Ctrl+Z）で戻せます。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.state.delete_page():
            self.state.message.emit("ページを削除しました")

    def move_page_by(self, offset: int) -> None:
        """表示中のページを前後へ1枚ぶん動かす。

        一覧のドラッグと同じことをキーからも行えるようにしてある。
        並べ替えのために一度マウスへ持ち替えなくて済む。
        """
        index = self.state.page_index
        if not self.state.move_page(index, index + offset):
            self.state.message.emit("これ以上動かせません")
            return
        self.state.message.emit(
            f"{index + 1} ページ目を {index + offset + 1} ページ目へ移しました"
        )

    def change_page_size(self) -> None:
        """ページの大きさを変える（A4 / B5 / カスタム）。"""
        dialog = PageSizeDialog(self.state.page.size, self.state.page_count, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        size = dialog.chosen_size()
        all_pages = dialog.apply_to_all()
        outside = self.state.set_page_size(size, all_pages=all_pages)
        self.view.fit_page()

        where = "すべてのページ" if all_pages else f"{self.state.page_index + 1} ページ目"
        message = f"{where}を {size.w:.0f} × {size.h:.0f} px にしました"
        if outside:
            # 勝手に動かさない。どう直すかは場面ごとに違う（要件定義 6.1）
            message += f"。{len(outside)} 個が用紙からはみ出しています"
        self.state.message.emit(message)

    def prev_page(self) -> None:
        self.state.set_page_index(self.state.page_index - 1)

    def next_page(self) -> None:
        self.state.set_page_index(self.state.page_index + 1)

    # -- ファイル ----------------------------------------------------------
    # 実処理は `ProjectIO`（→ `project_io.py`）。ここに残るのは
    # メニュー項目の表示合わせだけ

    def _sync_recent_project_action(self, path: pathlib.Path | None = None) -> None:
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

    # -- 書き出し ----------------------------------------------------------

    def export_png(self) -> bool:
        """PNG で書き出す（要件定義 6.7）。書き出したら True。

        断る場所を3つ設けてある。**どれも書き始める前に出す。**
        書いたあとで知らせても、上書きしてしまったものは戻らない。

        1. 保存前の作品（書き出し先が決まらない）
        2. 実体が見つからない画像がある（その場所が白く抜ける）
        3. 同じ名前のファイルがすでにある（上書きになる）
        """
        dest = self._export_dest()
        if dest is None:
            return False

        dialog = ExportDialog(
            dest,
            self.state.page_index,
            self.state.page_count,
            self,
            self.state.page.size,
            self._export_scale,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self._export_scale = dialog.chosen_scale()
        indexes = (
            list(range(self.state.page_count))
            if dialog.wants_all_pages()
            else [self.state.page_index]
        )

        if not self._confirm_missing(indexes):
            return False
        if not self._confirm_overwrite(dest, indexes):
            return False
        return self._run_export(dest, indexes)

    def _export_dest(self) -> pathlib.Path | None:
        """書き出し先。保存前なら、先に保存してもらう。

        預かって後で書く（画像の貼り付けの `PendingAssets`）形にはしない。
        書き出しは「いま欲しいファイルを作る」操作なので、後回しにすると
        何のために押したのか分からなくなる。
        """
        try:
            return export_dir_of(self.state)
        except MangaLayoutError as e:
            answer = QMessageBox.question(
                self,
                "先に保存が必要です",
                f"{e}\n\n今すぐ保存しますか。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer != QMessageBox.StandardButton.Save or not self.files.save_project():
                return None
        return export_dir_of(self.state)

    def _confirm_missing(self, indexes: list[int]) -> bool:
        """実体の無い画像があれば知らせる。続けてよければ True。

        画面では×印が出ているが、書き出しには目印を描かない（作品ではない
        ため）。黙って白く抜けるので、ここで必ず言う。
        """
        count = missing_assets_in(self.state, indexes)
        if count == 0:
            return True
        answer = QMessageBox.warning(
            self,
            "画像が見つかりません",
            f"実体の見つからない画像が {count} 個あります。\n"
            "その場所は白いまま書き出されます。続けますか。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _confirm_overwrite(self, dest: pathlib.Path, indexes: list[int]) -> bool:
        """すでにあるファイルを上書きしてよいか聞く。よければ True。

        名前を並べる件数を絞るのは、30 ページの作品で確認欄が画面を
        埋め尽くすのを避けるため。件数は必ず先に出す。
        """
        found = existing_paths(planned_paths(dest, indexes, self.state.page_count))
        if not found:
            return True

        shown = [p.name for p in found[:OVERWRITE_LIST_LIMIT]]
        if len(found) > OVERWRITE_LIST_LIMIT:
            shown.append(f"ほか {len(found) - OVERWRITE_LIST_LIMIT} 件")
        answer = QMessageBox.question(
            self,
            "上書きしますか",
            f"{dest} に同じ名前のファイルが {len(found)} 件あります。\n"
            + "、".join(shown)
            + "\n\n上書きしますか。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _run_export(self, dest: pathlib.Path, indexes: list[int]) -> bool:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            written = export_pages(self.state, indexes, dest, self._export_scale)
        except (MangaLayoutError, OSError) as e:
            QMessageBox.critical(self, "書き出せません", str(e))
            return False
        finally:
            QGuiApplication.restoreOverrideCursor()

        where = written[0].name if len(written) == 1 else f"{len(written)} 枚"
        px = page_px(self.state.page.size, self._export_scale)
        self.state.message.emit(
            f"{dest} に {where} を書き出しました"
            f"（{scale_label(self._export_scale)}・{px[0]:,} × {px[1]:,} 画素）"
        )
        return True

    # -- 終了時 ------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self.files.confirm_discard():
            event.accept()
        else:
            event.ignore()
