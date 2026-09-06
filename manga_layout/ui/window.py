"""メインウィンドウ。メニュー・道具箱・ページ送り・ファイル操作。"""

from __future__ import annotations

import pathlib
from functools import partial

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QRubberBand,
    QToolBar,
)

from ..check import headline, inspect_project, marked_page_ids
from ..errors import MangaLayoutError
from ..images import to_png_bytes
from ..layout import attach_target, cover_rect_in, full_page_rect
from ..model import (
    PT_TO_PX,
    TAIL_SHAPE_BUBBLES,
    TAIL_SHAPE_TRIANGLE,
    TEXT_SIZE_MAX_PX,
    TEXT_SIZE_MIN_PX,
    ImageObject,
    Panel,
)
from ..settings import ensure_settings_file, load_settings, settings_path
from ..storage import prune_unused_assets
from .canvas import IMAGE_FILE_FILTER, PageView, font_size_label
from .check_view import CheckResultDialog
from .context_menu import ContextMenu
from .font_dialog import FONT_DIALOG_SIZE, FontChooser
from .menu_search import (
    HIGHLIGHT_SECONDS,
    MENU_SEARCH_HINT,
    MenuSearchDialog,
    collect_menu_entries,
    plain_label,
)
from .menus import (
    TAIL_TURN_LABELS,
    BalloonMenu,
    EditMenu,
    FileMenu,
    ImageMenu,
    PageMenu,
    PanelMenu,
    StickerMenu,
    TextMenu,
)
from .pages import PageJumpBar, PageListPanel, PageSizeDialog
from .project_io import ProjectIO
from .shortcuts import SHORTCUTS_HINT, ShortcutsDialog, collect_groups
from .state import (
    ADJUST_TOOLS,
    BALLOON_STYLE_LABELS,
    TOOL_BALLOON,
    TOOL_BALLOON_CLOUD,
    TOOL_BALLOON_JAGGED,
    TOOL_BALLOON_RECT,
    TOOL_BALLOON_SPIKY,
    TOOL_BALLOON_WAVY,
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_ROUGH,
    TOOL_SELECT,
    TOOL_SHORT_LABELS,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_STICKER_EXCLAIM,
    TOOL_STICKER_EXCLAIM_QUESTION,
    TOOL_TEXT,
    TOOL_TONE_AREA,
    TOOL_WAND,
    EditorState,
    object_label,
)

APP_TITLE = "漫画レイアウタ"

# 表示メニューに出す、ページ一覧の開け閉め項目の名前。
# 一覧の見出し（「ページ 1/9」）とは別に持つ（理由は `_refresh`）
PAGES_MENU_LABEL = "ページ一覧"

def adjust_tool_exit(tool: str) -> str:
    """調整の道具（→ `ADJUST_TOOLS`）を持っている間、状態表示の末尾に出す出口。

    **出口は必ず名乗る。** これらは持ち替えた覚えのないまま入ってしまうことが
    あり、そのとき「クリックしても何も選べない」としか見えない（→ 6.27）。

    **どの項目を押せばよいかまで書く**（本人の指示 2026-08-27）。以前は
    「同じ項目をもう一度押すと戻る」だったが、**どの項目のことかを書いて
    いなかった**——入り口を覚えていない人は、その「同じ項目」を探せない。

    名前は `TOOL_LABELS` から引く。ここへ書き写すと、道具を改名したときに
    案内だけ古い名前で残る（→ `BALLOON_STYLE_LABELS` の注記）。
    """
    return f"もう一度、メニュー「{TOOL_LABELS[tool]}」を押すと解除"

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


# 文字の大きさを1段階変える幅。
# 数値を打ち込ませるより、押して確かめるほうが速い。
#
# **1段階はポイントで決める。** 状態表示もフォント設定の窓もポイントで
# 喋るので、px で決めると 1 段階が半端な数になる（以前の 2px は
# 約 0.96pt で、押しても表示が 1pt 動いたり動かなかったりした）。
#
# **行き過ぎを止める範囲（`TEXT_SIZE_MIN_PX` / `TEXT_SIZE_MAX_PX`）は
# `model.py` にある。** 四隅のドラッグ（`canvas.TextScaleDrag`）も同じ
# 範囲を見るが、あちらはここを読めない（循環参照になる）
# 道具箱のボタンに出す書体名の長さ（文字数）。**超えたぶんは「…」にする。**
#
# 書体名は「UD デジタル 教科書体 N」のように長い。3つ並べると道具箱の幅を
# 押し出し、**入り切らないボタンは送りのボタン（»）の中へ隠れる。**
# 1回押しのための機能が、押すまでに2手かかることになる。
#
# **形は名前より速く見分けられる**ので、詰めたぶんは書体そのもので描いて
# 補う（→ `MainWindow._build_font_actions`）。全部の名前はホバー中の
# 状態表示に出る
FONT_BUTTON_MAX_CHARS = 8

# よく使う書体が1つも登録されていないときの案内。**どこで登録するかまで言う。**
# 「登録されていません」だけだと、設定の窓に辿り着けない
FAVORITE_FONT_EMPTY_HINT = (
    "よく使う書体が登録されていません（settings.bat の「よく使う書体」で登録できます）"
)


def _font_button_label(family: str) -> str:
    """道具箱とメニューに出す書体名。長ければ末尾を「…」で詰める。"""
    if len(family) <= FONT_BUTTON_MAX_CHARS:
        return family
    return family[:FONT_BUTTON_MAX_CHARS] + "…"


TEXT_SIZE_STEP_PT = 2.0
TEXT_SIZE_STEP_PX = TEXT_SIZE_STEP_PT * PT_TO_PX

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



# メニューバーの右端に出す案内。**`ファイル(F)` の括弧が何なのかは、初めて見た人には
# 分からない。** Alt と一緒に押す、という説明を、その括弧が並んでいる場所に置く
ALT_HINT = "＋ Alt キー"


class MainWindow(QMainWindow):
    def __init__(self, state: EditorState | None = None):
        super().__init__()
        self.state = state or EditorState()
        self.view = PageView(self.state)
        self.setCentralWidget(self.view)
        self._apply_initial_geometry()

        # settings.json は手で書き換える前提のファイル。実物が無いと
        # 「どこに何を書けばいいのか」が分からないので、起動時に雛形を置く。
        # 読めなくても既定値で進む（設定は好みで、無くても作業はできる）。
        #
        # 場所を持っておくのは、テストで本物の設定を読み書きしないため
        self.settings_file = settings_path()
        ensure_settings_file(self.settings_file)
        self.settings = load_settings(self.settings_file)
        # ラフの濃さは描く側（`PageRenderer`）が要るので、状態に渡しておく
        # （→ 6.23）。ラフを読み込む直前にも入れ直す（→ `load_rough`）
        self.state.rough_opacity = self.settings.rough_opacity

        # ファイル入出力の部品。メニューがスロットとして参照するので、
        # メニューの組み立てより先に作る
        self.files = ProjectIO(self)

        # 点検の結果の窓（→ 10.1）。**押されるまで作らない。**
        # 起動のたびに窓を1つ作るのは、一度も使わない場合に無駄が出る
        self._check_dialog: CheckResultDialog | None = None
        # メニューを探す窓（→ 6.30）。こちらも押されるまで作らない。
        # 押された項目のメニューを囲む枠と、それを消すタイマーも同じ扱い
        self._menu_search_dialog: MenuSearchDialog | None = None
        self._menu_highlight: QRubberBand | None = None
        self._menu_highlight_timer: QTimer | None = None
        # ショートカットキーの一覧（→ 7章）。こちらも押されるまで作らない
        self._shortcuts_dialog: ShortcutsDialog | None = None

        self._tool_actions: dict[str, QAction] = {}
        self._build_pages_dock()
        self._build_tool_actions()  # 各メニューが道具の項目を参照するので先に作る
        # よく使う書体も、セリフのメニューと道具箱の両方が参照する（→ 6.5）
        self._build_font_actions()

        # メニューバー。**生成順＝画面での並び順。** 各部品は生成時に自分の
        # QAction を作り切るので、作り忘れ・順序の入れ替えはここで露見する
        self.file_menu = FileMenu(self)
        self.edit_menu = EditMenu(self)
        self.panel_menu = PanelMenu(self)
        # 平らな別名。右クリックとテストが「集中線」「流線」を直に指すときに使う
        self.focus_menu = self.panel_menu.focus
        self.flow_menu = self.panel_menu.flow
        self.image_menu = ImageMenu(self)
        # 平らな別名。右クリックとテストが「トーン」を直に指すときに使う
        # （集中線・流線と同じ扱い）
        self.tone_menu = self.image_menu.tone
        self.balloon_menu = BalloonMenu(self)
        self.sticker_menu = StickerMenu(self)
        self.text_menu = TextMenu(self)
        self.page_menu = PageMenu(self)
        self._build_view_menu()
        # ヘルプは一番右。**最後に組む**（生成順＝並び順）
        self._build_help_menu()

        # `refresh()` を配って回る部品の一覧（→ `_refresh`）。
        # 部品を足したらここにも足す。足し忘れると、その部品のメニューが
        # 選択に追従しなくなる
        self._menus = [
            self.file_menu,  # ラフの項目のぶん（→ 6.23）
            self.edit_menu,
            self.panel_menu,  # 集中線・流線（focus_menu / flow_menu）のぶんも面倒を見る
            self.image_menu,
            self.balloon_menu,
            self.sticker_menu,
            self.text_menu,
            self.page_menu,
        ]
        self._build_toolbar()
        self._build_status_bar()
        # 右クリックのメニュー。各メニュー部品の QAction を写して出すので、
        # 部品が全部そろってから作る（→ `context_menu.py`）
        self.context_menu = ContextMenu(self)

        self.state.changed.connect(self._refresh)
        self.state.selection_changed.connect(self._refresh)
        self.state.page_changed.connect(self._refresh)
        self.state.tool_changed.connect(self._sync_tool_actions)
        # **道具の持ち替えでも文言を組み直す。** 調整中は状態表示と畳んだ
        # 親の名前がそれを名乗るので（→ 6.27）、ここを繋がないと、次に何かを
        # 選ぶまで前の文言が残る（2026-08-06。名乗りを足したとき配線が漏れていた）
        self.state.tool_changed.connect(self._refresh)
        self.state.message.connect(lambda text: self.statusBar().showMessage(text, 6000))
        self.view.context_menu_requested.connect(self.context_menu.show)

        # 前回のセッションで開いていた作品名を「前回のファイルを開く」に出す
        self.file_menu.sync_recent_project()

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
        action.triggered.connect(lambda *_, slot=slot: self.run_action(slot))
        self.addAction(action)
        return action

    def run_action(self, slot) -> None:
        """項目を1つ実行する。**その前に、開いている入力欄を確定する。**

        `PageView.keyPressEvent` の「入力中は1つも横取りしない」は、キーが
        画面まで届いた場合にしか効かない。**メニューのショートカットは
        それより手前で解決される**ので、入力欄を開いたまま素通りして発火する
        （Qt が入力欄に譲るのは Ctrl+Z・Delete など標準の編集キーだけ）。

        塞がずにいると、打った文字が黙って消えることまで起きる。入力中に
        ページを移す項目（`PgDown` など）が通ると、**入力欄は開いたまま
        ページだけが変わる**。確定時の書き戻しは今のページからセリフを
        探すので見つからず、打った内容が捨てられて、元のページには中身の
        無い枠が残る（2026-08-08 に発見）。書式の項目（`F7`・`Ctrl+B`）が
        通った場合も、履歴に1手挟まって「置いたばかりのセリフを Esc で
        追加ごと取り消す」（→ `PageView.finish_text_edit`）が効かなくなる。

        **先に確定してから項目を実行する**、という形に揃えた。画面を触った
        ときと右クリックのときが既にこの形で（→ `PageView.contextMenuEvent`）、
        メニューを押した場合も焦点が外れて同じ道を通っている。**残るのは
        ショートカットだけ**だったので、項目の実行を1箇所に集めて塞ぐ。

        トーンの連打キー（`Shift+]` など）が `setShortcut` を避けて画面側で
        拾っているのは、この穴を1つずつ避けていた形（→ `menus.ToneMenu`）。
        """
        self.view.finish_text_edit(commit=True)
        slot()

    def _build_tool_actions(self) -> None:
        """道具の切り替え。メニューより先に作る。

        **1つの項目を、担当のメニューと道具箱と右クリックが共有する。**
        別々の項目にすると、選ばれている印がどれか1つにしか付かない。

        **道具だけを集めたメニューは持たない**（2026-09-06 に畳んだ → 6.33）。
        道具は「コマ」「フキダシ」「マーク」「セリフ」「画像」「ファイル」の
        担当のメニューに1つずつ出る。選択の道具だけは担当が無いので編集へ
        置いた（→ `menus.EditMenu`）。
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
            # フキダシの並びは `BALLOON_TOOLS` と揃える（使う頻度の順
            # → `BALLOON_STYLE_LABELS`）。ここだけ別の順にすると、道具箱と
            # フキダシメニューで並びが食い違う
            (TOOL_BALLOON, "B"),
            # `W` は雲に付ける。**足したのではなく、ふわふわから付け替えた**
            # （本人の指示 2026-08-07）。並びを使う頻度の順にしたとき
            # （→ `BALLOON_STYLE_LABELS`）雲が2番目に上がったのに、キーだけ
            # 前の頻度のまま残っていた。**キーの本数は増やさない**——足すこと
            # 自体に値打ちは無く、塞ぐ副作用は必ず付く（→ 要件定義 7章）
            (TOOL_BALLOON_CLOUD, "W"),
            # トゲトゲ（ギザギザの曲線版 → 6.32）にキーは割り当てない。
            # ギザギザと二者択一で、作風を決めたあとは片方しか使わない
            (TOOL_BALLOON_SPIKY, None),
            # ふわふわと四角にキーは割り当てない。ビックリはてなマークと
            # 同じ扱いで、メニューと右クリックから出す
            (TOOL_BALLOON_WAVY, None),
            (TOOL_BALLOON_JAGGED, "G"),
            (TOOL_BALLOON_RECT, None),
            (TOOL_STICKER_EXCLAIM, "M"),
            (TOOL_STICKER_EXCLAIM_QUESTION, None),
            (TOOL_TEXT, "T"),
            # ラフの調整（→ 6.23）にもキーは割り当てない。敷いたあとに
            # 何度も出入りするものではないので、メニューと右クリックで足りる
            (TOOL_ROUGH, None),
            # トーンの範囲（→ 10.1）も同じ。しきい値と細さで足りなかった
            # ときの手当てなので、頻繁に出入りするものではない
            (TOOL_TONE_AREA, None),
            # 切り抜き（→ 10.3）にもキーは割り当てない。**キーの本数は
            # 増やさない**（→ 上の雲の注記）。使うのは絵を貼った直後だけで、
            # メニューと右クリックで足りる
            (TOOL_WAND, None),
        ):
            label = TOOL_LABELS[tool]
            action = QAction(f"{label} ({shortcut})" if shortcut else label, self)
            # **道具箱のボタンにだけ短い名前を出す**（→ 6.33）。`setIconText` は
            # 道具箱側しか見ないので、上で作った名前（メニュー・右クリック・
            # 「メニューを探す」窓・ホバーの吹き出し）は変わらない
            action.setIconText(TOOL_SHORT_LABELS[tool])
            action.setCheckable(True)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            # 道具の項目も `_act` と同じ道を通す（→ `run_action`）。
            # 1文字キーは入力欄が先に受け取るので今のところ素通りしないが、
            # **項目ごとに効く・効かないを覚えるほうが危うい**
            action.triggered.connect(
                lambda *_, t=tool: self.run_action(lambda: self._pick_tool(t))
            )
            group.addAction(action)
            self.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[TOOL_SELECT].setChecked(True)
        # **選択にだけ説明を添える。** 他の道具は「〜を追加」「〜を調整」と
        # 名前で何が起きるか分かるが、選択は**戻る先**であることが名前に
        # 出ない。編集メニューでは取り消しの隣に並ぶので、なおさら
        # （→ `menus.EditMenu`）。ホバー中の状態表示で名乗らせる
        self._tool_actions[TOOL_SELECT].setStatusTip(
            "選ぶ・動かす道具に戻る。作る道具を持ったまま気が変わったときに押す"
        )

    def _pick_tool(self, tool: str) -> None:
        """道具の項目が押された。

        **調整の道具は、同じ項目をもう一度押すと解除する**（→ 要件定義 6.27）。
        レ点が外れて選択の道具へ戻る。**入るのと出るのが同じ場所**になるので、
        持ち替えた覚えのないまま入ってしまった人も、押した項目を辿れば出られる
        （本人の指摘 2026-08-06。トーンの範囲を調整中だと気づけなかった）。

        **作る側の道具（コマ・フキダシ・マーク）には広げない。** あちらは
        押すたびに1つ作るので、2回目が「やめる」に化けると意味が変わる。
        """
        if tool in ADJUST_TOOLS and self.state.tool == tool:
            self.state.set_tool(TOOL_SELECT)
            return
        self.state.set_tool(tool)

    def _build_view_menu(self) -> None:
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

    def _build_help_menu(self) -> None:
        """ヘルプ（→ 6.30、7章）。「メニューを探す」と「ショートカットキーの一覧」。

        「メニューを探す」のキーは F1 と Ctrl+F の両方を通す。**探しに来る
        人が押すキーはどちらか片方に決まらない**——ヘルプの慣習は F1、
        探すの慣習は Ctrl+F で、どちらもこのアプリでは空いている
        （「やり直す」に Ctrl+Y と Ctrl+Shift+Z の両方を通しているのと
        同じ形 → 7章）。

        **一覧のほうにキーは付けない。** キーを覚えていない人が開く窓に
        キーで入る道を作っても使われないし、キーを足せばそのぶん元から
        通っていたものを塞ぐ（→ 7章）。
        """
        menu = self.menuBar().addMenu("ヘルプ(&H)")
        action = self._act("メニューを探す...", self.search_menu, "F1", MENU_SEARCH_HINT)
        action.setShortcuts([QKeySequence("F1"), QKeySequence("Ctrl+F")])
        menu.addAction(action)
        menu.addAction(
            self._act(
                "ショートカットキーの一覧...", self.show_shortcuts, None, SHORTCUTS_HINT
            )
        )
        self._add_alt_hint()

    def _add_alt_hint(self) -> None:
        """メニューバーの右端に「＋ Alt キー」と灰色で出す。

        **`ファイル(F)` の括弧が何なのかは、初めて見た人には分からない。**
        Alt と一緒に押す、という説明を、その括弧が並んでいる場所そのものに置く。

        **項目ではなく、メニューバーの隅に置く部品にする**（`setCornerWidget`）。
        以前は「押せない項目」として置いていたが、**押せない項目を飛ばすかどうかは
        見た目（スタイル）任せ**で、`SH_Menu_AllowActiveAndDisabled` が 1 の
        `windowsvista` と `windows` では **Alt → → と進んだときに反転表示で止まる**
        （↓ を押しても何も開かない。抜けられはするが行き止まりに見える）。
        このPCの既定 `windows11` と `fusion` では起きないので、**手元では見えない**
        （2026-09-05 に4つのスタイルで実測して確認）。

        部品にすれば**どのスタイルでも項目ではなくなる**ので、この違いごと無くなる。
        「メニューを探す」窓にも、そもそも項目でないので出ない。
        """
        hint = QLabel(ALT_HINT)
        hint.setEnabled(False)                 # 灰色にするため（押せる部品ではない）
        # **メニューの文字の大きさに揃える。** 既定のままだと本文の大きさになり、
        # 隣の `ヘルプ(H)` より大きく見える（利用者の設定は大きめ）
        hint.setFont(self.menuBar().font())
        hint.setContentsMargins(0, 0, 8, 0)    # 右端に貼り付かないよう少しだけ空ける
        self.menuBar().setCornerWidget(hint, Qt.Corner.TopRightCorner)
        self._alt_hint_widget = hint

    def _build_font_actions(self) -> None:
        """よく使う書体の項目（→ 要件定義 6.5）。

        **セリフのメニューと道具箱が同じものを使う**（`_tool_actions` と
        同じ作り → `_build_toolbar`）。並べ直すと2か所を直すことになり、
        片方だけ直し忘れると道具箱にだけ出ない項目ができる。

        **登録されている書体のぶんだけ作る。** 空の枠にボタンを出すと、
        押しても何も起きない当たりが道具箱に並ぶ。

        **書体名をその書体で描く**（`setFont`）。名前は詰めて出すので
        （→ `_font_button_label`）、形が見えないと見分けが付かない。
        """
        self._font_actions: list[QAction] = []
        for family in self.settings.favorite_fonts:
            if not family:
                continue
            action = self._act(
                _font_button_label(family),
                partial(self.apply_font_family, family),
                tip=f"セリフの書体を「{family}」にします（F3 で順に切り替え）",
            )
            action.setCheckable(True)
            action.setFont(QFont(family))
            # 詰めた名前ではなく、書体名そのものを持たせる。
            # 今どれが効いているかの照合に使う（→ `_sync_font_actions`）
            action.setData(family)
            self._font_actions.append(action)

    def _build_toolbar(self) -> None:
        """道具箱。**一覧は `_tool_actions` から取る。**

        道具の名前をここへ書き写すと、担当のメニュー（`menus.py`）と
        2か所を直すことになる。道具を増やしたときに片方だけ直し忘れると、
        道具箱にだけ出ない項目ができてしまう。

        **ボタンに出るのは短い名前**（→ `TOOL_SHORT_LABELS`、6.33）。
        メニューの名前をそのまま出すと 20項目のうち8つしか入らない。
        """
        bar = self.tool_bar = QToolBar("道具", self)
        bar.setMovable(False)
        self.addToolBar(bar)
        for action in self._tool_actions.values():
            bar.addAction(action)
        bar.addSeparator()
        # ページ送りも道具と同じ扱いで、ボタンには矢印だけを出す（→ 6.33）。
        # 何ページ目へ動くのかはホバーの吹き出しと状態表示の左に出る
        for text, short, slot in (
            ("← 前ページ", "←", self.prev_page),
            ("次ページ →", "→", self.next_page),
        ):
            action = self._act(text, slot)
            action.setIconText(short)
            bar.addAction(action)
        self._build_font_toolbar()

    def _build_font_toolbar(self) -> None:
        """よく使う書体のボタン（→ 要件定義 6.5）。**道具箱とは別の段に置く。**

        **道具の隣には置けない。** 足した当時、道具箱は入り切っておらず、
        はみ出したぶんは送りのボタン（»）の中へ隠れていた。**既定の窓
        （1100px）では20項目のうち8つしか見えていなかった**（2026-09-06 に
        実機で計測。同じ日に offscreen で数えたときは7つだったが、あちらは
        書体が1本も無く字幅が出ない）。末尾に足した書体のボタンは、
        **必ずその隠れる側に入っていた。**

        **道具箱そのものは 6.33 で直したが、この段は戻さない。** 短い名前に
        できたのは道具の側だけで、書体名は「UD デジタル 教科書体 N」のように
        長いまま出す必要がある（→ `_font_button_label`。詰めすぎると見分けが
        付かない）。3つ並べれば再び道具箱を押し出す。

        段を増やすぶん用紙は狭くなるが、**押せないボタンを置くことに意味は
        無い。** 1回押しで切り替えるための機能なので、送りのボタンを開いて
        から押す形になった時点で、キー（`F3`）だけあれば足りることになる。

        **1つも登録されていなければ、段そのものを作らない**（→ 6.5）。
        """
        if not self._font_actions:
            return
        self.addToolBarBreak()
        self.font_bar = QToolBar("書体", self)
        self.font_bar.setMovable(False)
        self.addToolBar(self.font_bar)
        for action in self._font_actions:
            self.font_bar.addAction(action)

    def _build_status_bar(self) -> None:
        self.page_label = QLabel()
        self.hint_label = QLabel()
        self.statusBar().addPermanentWidget(self.hint_label)
        self.statusBar().addPermanentWidget(self.page_label)

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

        # よく使う書体の押し込み（→ `_sync_font_actions`）。選び直すたびに
        # 変わるので、道具の印（`_sync_tool_actions`）と同じくここでも回す
        self._sync_font_actions()

        # メニューの有効・無効と文言は、各部品が自分のぶんを面倒見る
        # （→ `menus.py`。どの部品が回るかは `_menus` で決まる）
        for menu in self._menus:
            menu.refresh()

    def _hint(self) -> str:
        """状態表示の右側に出す1行。**道具が先、選択があと。**

        道具そのものが名乗る案内（`_tool_hint`）があればそれを出し、
        無ければ選んでいるものの案内（`_selection_hint`）を出す。

        **2つは混ざらない。** ラフやトーンの範囲を調整している間はそもそも
        何も選べないので、選択の話をしても出るものが無い（→ 6.23、6.27）。
        """
        tool = self._tool_hint()
        return self._selection_hint() if tool is None else tool

    def _tool_hint(self) -> str | None:
        """道具そのものが名乗る案内。**名乗らない道具では None。**

        ラフの調整中は選択の話をしない（→ 6.23）。この道具では何も選べず、
        掴めるのはラフだけなので、そちらの寸法を出す。**トーンの範囲も同じ**
        （→ 6.27）。どちらも**調整中であること自体をここで名乗る**——
        持ち替えたことに気づかないまま「クリックしても選べない」に見えるのが、
        この2つの道具のいちばんの躓き（本人の指摘 2026-08-06）。
        """
        if self.state.tool == TOOL_ROUGH:
            rough = self.state.page.rough
            if rough is None:
                # **対象が無いときこそ出口を出す**（2026-08-27 に追加）。
                # 掴めるものが1つも無いこの状態は、「持ち替えた覚えがないのに
                # 何も選べない」と見分けが付かない——出口がいちばん要る場面
                # だったのに、ここだけ出していなかった
                return (
                    "ラフ調整中: このページにはラフがありません"
                    f" / {adjust_tool_exit(TOOL_ROUGH)}"
                )
            tint = "青く淡く" if rough.faded else "元の色"
            return (
                f"ラフ調整中: {rough.rect.w:.0f} × {rough.rect.h:.0f} px / {tint}"
                f" / {adjust_tool_exit(TOOL_ROUGH)}"
            )

        if self.state.tool == TOOL_TONE_AREA:
            tone = self.state.selected_tone
            image = self.state.tone_image
            if tone is None or image is None:
                # 対象が無いときも出口を出す（→ 上のラフと同じ理由）
                return (
                    "トーン範囲を調整中: 対象の絵がありません"
                    f" / {adjust_tool_exit(TOOL_TONE_AREA)}"
                )
            area = tone.area
            where = (
                "今は画像全体"
                if area is None
                else f"{area.w * image.rect.w:.0f} × {area.h * image.rect.h:.0f} px"
            )
            return f"トーン範囲を調整中: {where} / {adjust_tool_exit(TOOL_TONE_AREA)}"

        # **切り抜きでは、押すと何が起きるかをここに出す**（→ 10.3）。
        # 項目名は「切り抜き」の4文字しかなく、押した所が消えることも、
        # Shift で裏返ることも、名前からは分からない。**道具の名前を短く
        # 保てるのは、持っている間ずっとここに出ているから**
        if self.state.tool == TOOL_WAND:
            return (
                f"切り抜き中: 絵を押すとその区画が消える"
                f" / Shift+押すとそこだけ残す / 許容差 {self.state.wand_tolerance}"
                f" / {adjust_tool_exit(TOOL_WAND)}"
            )

        # **セリフを置く道具では、置く前に書式を名乗る。** 書式は最後に
        # 指定したものを引き継ぐので（→ `EditorState.next_text_font`）、
        # 何で置かれるのかは置いてみるまで分からない。置いてから直すのは
        # 選び直しと同じ手数になる（本人の指摘 2026-08-07）
        if self.state.tool == TOOL_TEXT:
            return f"セリフを追加: {self._next_font_label()}"

        return None

    def _selection_hint(self) -> str:
        """いま何を選んでいるかを出す。**何も選んでいなくても1行返す。**

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
            # 集中線・流線はつまみ以外に見分ける手がかりが無いので、本数を
            # 添える。流線は向きも出す（つまみの位置だけでは角度が読めない）
            lines = (
                f" / 集中線 {panel.focus_lines.count} 本"
                if panel.focus_lines is not None
                else ""
            )
            if panel.flow_lines is not None:
                lines += f" / 流線 {panel.flow_lines.count} 本 {panel.flow_lines.angle:.0f}°"
            # ロック中は見た目を変えないので、気づける手がかりはここだけ
            # （つまみを出さないのと合わせて → 要件定義 6.17）
            locked = " / ロック中" if self.state.is_locked_selection else ""
            return f"コマを選択中: {b.w:.0f} × {b.h:.0f} px{inside}{lines}{locked}"

        return f"コマ未選択 / 次のセリフ: {self._next_font_label()}"

    def _next_font_label(self) -> str:
        """次に作るセリフの書式と向き（→ `EditorState.next_text_font`）。

        **向きも出す。** 向きは書式と同じく引き継ぐので（→
        `EditorState.next_text_direction`）、何で置かれるのかは置いてみる
        まで分からない。書体だけ名乗って向きを黙っていると、横書きのつもりで
        置いたものが縦書きで出る事故がそのまま残る（本人の指摘 2026-08-07）。
        """
        font = self.state.next_text_font
        weight = " 太字" if font.bold else ""
        lay = "縦" if self.state.next_text_direction == "vertical" else "横"
        return f"{font.family} {self._size_label(font.size_px)}{weight} {lay}書き"

    def _refresh_hint(self) -> None:
        """状態表示の右側を出し直す。

        **`changed` の飛ばない変化を映すための入口。** 道具の切り替えと、
        次に作るセリフの書式（→ `choose_font`）はどちらも作品を変えないので、
        `_refresh` は呼ばれない。
        """
        self.hint_label.setText(self._hint())

    def _sync_tool_actions(self) -> None:
        self._tool_actions[self.state.tool].setChecked(True)
        # 状態表示は道具でも変わる（ラフの調整中は選択の話をしない → 6.23）
        self._refresh_hint()

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

    def suggest_next_panel(self) -> None:
        """次に置くコマを提案する（要件定義 10.5）。

        **押すたびに、直前の提案を次の案へ差し替える。** 気に入ったら別の操作へ移れば
        そのまま残り、要らなければ Undo 1回で消える。

        お知らせは `state` の側で出している。**何番目の案かはここからは分からない。**
        """
        self.state.suggest_next_panel()

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

    def raise_panel(self) -> None:
        """選んだコマを最前面へ（重なっているコマの手前に出す）。"""
        panel = self.state.selected_panel
        if panel is not None and self.state.raise_panel(panel.id):
            self.state.message.emit("コマを手前に出しました")

    def lower_panel(self) -> None:
        """選んだコマを最背面へ。"""
        panel = self.state.selected_panel
        if panel is not None and self.state.lower_panel(panel.id):
            self.state.message.emit("コマを奥に送りました")

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

    def toggle_flow_lines(self) -> None:
        """選んだコマの流線を入れる／消す（要件定義 6.26）。

        集中線と同じ形。**両方入れても構わない**（別の項目なので、
        止めるほうがコードが増える → 6.26）。
        """
        if self.state.selected_flow is None:
            self.state.add_flow_lines()
        elif self.state.remove_flow_lines():
            self.state.message.emit("流線を消しました")

    def toggle_tone(self) -> None:
        """選んだ画像のトーンを入れる／消す（要件定義 10.1）。

        集中線・流線と同じ形。**持ち主が画像**なので、コマではなく中の絵を
        選んでから使う。
        """
        if self.state.selected_tone is None:
            self.state.add_tone()
        elif self.state.remove_tone():
            self.state.message.emit("トーンを消しました")

    def toggle_flow_color(self) -> None:
        """選んだコマの流線の色を黒⇄白で切り替える（要件定義 6.26）。"""
        if self.state.toggle_flow_color():
            flow = self.state.selected_flow
            color = "白" if flow is not None and flow.white else "黒"
            self.state.message.emit(f"流線の色: {color}")

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
        ref = next((c.asset for c in panel.children if c.id == image_id), None) if panel else None

        with self.state.edit_page("画像の削除") as page:
            target = page.panel_of_image(image_id)
            if target is not None:
                target.children = [c for c in target.children if c.id != image_id]

        self.state.select(panel_id)
        self.state.message.emit("画像を削除しました")
        if ref is not None:
            self.state.forget_if_unused(ref)

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

    def clear_image_mask(self) -> None:
        """選択中の画像から切り抜きを外す（→ 要件定義 10.3）。

        **実体（assets/）は消さない。** Undo で戻せる操作なので、消すと
        戻したときに切り抜きだけが失われる（→ `EditorState.clear_image_mask`）。
        """
        image = self.state.selected_image
        if image is None or not image.mask_asset:
            return
        if self.state.clear_image_mask(image.id):
            self.state.message.emit("切り抜きを外しました")

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
        if not balloon.tail.enabled:
            self.state.message.emit("しっぽが出ていません")
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

    def current_font_family(self) -> str:
        """いま効いている書体。**選んでいなければ「次に作るセリフ」のもの。**

        書体を変える操作がどちらへ効くかと同じ分け方（→ `choose_font`）。
        道具箱のどのボタンを押し込むかも、`F3` がどこから数え始めるかも、
        ここ1か所で決まる。
        """
        text = self.state.selected_text
        return (text.font if text is not None else self.state.next_text_font).family

    def apply_font_family(self, family: str) -> None:
        """書体だけを変える。大きさと太さはそのまま。

        **既にその書体なら何もしない。** `set_text_font` は毎回1手を積むので、
        同じボタンを2回押しただけで、見た目の変わらない手が履歴に増える。

        セリフを選んでいなければ「次に作るセリフ」の書体を決める
        （→ `choose_font` と同じ。選んでいるときしか変えられないと、
        次の書式を決めるためだけに要らないセリフを1つ置くことになる）。
        """
        if family == self.current_font_family():
            self.state.message.emit(f"書体は既に「{family}」です")
            self._sync_font_actions()
            return

        text = self.state.selected_text
        if text is None:
            self.state.set_next_text_font(family=family)
            # 作品は変わらないので `changed` は飛ばない。自分で出し直す
            self._refresh_hint()
            self.state.message.emit(f"次に作るセリフの書体: {family}")
        else:
            self.state.set_text_font(text.id, family=family)
            self.state.message.emit(f"書体: {family}")
        self._sync_font_actions()

    def cycle_favorite_font(self) -> None:
        """よく使う書体を順に切り替える（`F3`。→ 要件定義 6.5）。

        **キーは1つで、押すたびに次の書体へ回る。** 3枠あるので、3つ登録
        してあれば最大2回で目的の書体に届く。

        **今の書体が登録されていなければ、1つめから始める。** フォントを
        選ぶ窓で登録外の書体にしたあと `F3` を押したときに、押した回数で
        行き先が変わらない。
        """
        families = self.settings.registered_fonts
        if not families:
            self.state.message.emit(FAVORITE_FONT_EMPTY_HINT)
            return
        current = self.current_font_family()
        index = families.index(current) if current in families else -1
        self.apply_font_family(families[(index + 1) % len(families)])

    def _sync_font_actions(self) -> None:
        """いま効いている書体のボタンを押し込む（`_sync_tool_actions` と同じ役）。

        **1つも当たらないことがある。** 登録していない書体を選んでいる
        ときで、そのときはどれも押し込まない（嘘の表示を出さない）。
        """
        current = self.current_font_family()
        for action in self._font_actions:
            action.setChecked(action.data() == current)

    def choose_font(self) -> None:
        """種類・大きさ・太さをまとめて選ぶ。

        **今の書式を窓に持っていき、選んだ大きさを持ち帰る。** 以前は
        `QFont(family)` だけを渡していたため、窓には Qt の既定である
        12pt が出ていた。**選択中のセリフとは無関係な数字**で、しかも
        窓で大きさを変えても捨てていたので、そこから文字の大きさを
        変えることができなかった（2026-08-03 に直した）。

        窓はポイントでしか喋らないので、`PT_TO_PX` で換算して渡す。
        画面の解像度で Qt に換算させると、紙の上での大きさと合わない。

        **セリフを選んでいなければ「次に作るセリフ」の書式を決める**
        （→ `EditorState.next_text_font`）。以前はここで断っていたため、
        次の書式を決めるためだけに要らないセリフを1つ置く必要があった
        （本人の指摘 2026-08-07）。
        """
        text = self.state.selected_text
        font = text.font if text is not None else self.state.next_text_font

        current = QFont(font.family)
        current.setPointSizeF(font.size_px / PT_TO_PX)
        current.setBold(font.bold)

        chosen, ok = self._ask_font(current)
        if not ok:
            return

        size = self._size_px_of(chosen, fallback=font.size_px)
        label = f"{chosen.family()} {self._size_label(size)}"
        if text is None:
            self.state.set_next_text_font(
                family=chosen.family(), size_px=size, bold=chosen.bold()
            )
            # 作品は変わらないので `changed` は飛ばない。状態表示だけ出し直す
            self._refresh_hint()
            self.state.message.emit(f"次に作るセリフの書式: {label}")
            return

        self.state.set_text_font(
            text.id, family=chosen.family(), size_px=size, bold=chosen.bold()
        )
        self.state.message.emit(f"フォント: {label}")

    def _ask_font(self, current: QFont) -> tuple[QFont, bool]:
        """フォントを選ぶ窓を出す。選ばれた書式と、決めたかどうかを返す。

        **`QFontDialog.getFont`（1行で済む静的な呼び方）は使えない。**
        あちらは窓の大きさを指定できず、Qt の既定のまま開く。書体の一覧が
        数行しか見えないので目当ての書体まで延々と送ることになり、しかも
        **隅を引けば広がること自体に気づけない**（本人の指摘 2026-08-07）。
        自分で組み立てて `resize` してから開く（→ `FONT_DIALOG_SIZE`）。

        画面より大きくしない。作業領域（タスクバーを除いた範囲）に収める
        のは起動時の窓と同じ扱い（→ `_apply_initial_geometry`）。

        窓には**書体を打ち込んで絞り込む欄**を足してある（→ `FontChooser`）。
        広げても一覧に見えるのは十数件で、200件近い書体を送る手間は
        窓の大きさだけでは解けない（本人の要望 2026-08-07）。
        """
        dialog = FontChooser(current, self)
        dialog.setWindowTitle("フォントを選ぶ")

        width, height = FONT_DIALOG_SIZE
        screen = self.screen()
        if screen is not None:  # 表示装置が無いとき（offscreen 等）は素通り
            area = screen.availableGeometry()
            width = min(width, area.width())
            height = min(height, area.height())
        dialog.resize(width, height)

        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.selectedFont(), accepted

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
        """px とポイントを併記する（→ `canvas.font_size_label`）。

        **書き方はあちらに置いてある。** 四隅のドラッグでも同じ値を出すが、
        あちらからここは読めない（循環参照になる）。同じ値が2通りの書き方で
        出ると、同じものを触っている実感が切れる
        """
        return font_size_label(size_px)

    def delete_text(self) -> None:
        text = self.state.selected_text
        if text is None:
            return
        text_id = text.id
        with self.state.edit_page("セリフの削除") as page:
            page.remove_floating(text_id)
        self.state.select(None)
        self.state.message.emit("セリフを削除しました")

    # -- ラフ（下敷き → 要件定義 6.23） ------------------------------------

    def load_rough(self) -> None:
        """紙に描いたラフを、表示中のページの一番下に敷く。

        **画像を選ぶ窓は「画像を置く」と同じもの**（`_choose_image_file`）。
        始まる場所も同じで、ラフは作品の隣に置かれることが多いので辿り直さずに済む。

        **設定を読み直してから敷く。** 濃さ（`rough_opacity`）は手で書き換える
        前提のファイルにあるので、起動したままでも効かせたい。読み込む瞬間は、
        その値が初めて画面に出る瞬間でもある（→ `ProjectIO.default_parent`
        と同じ扱い）。
        """
        chosen = self._choose_image_file()
        if chosen is None:
            return
        data, name = chosen
        self.settings = load_settings(self.settings_file)
        self.state.rough_opacity = self.settings.rough_opacity
        try:
            self.state.place_rough(data)
        except MangaLayoutError as e:
            QMessageBox.warning(self, "ラフを敷けません", str(e))
            return
        self.state.message.emit(
            f"ラフを敷きました（{name}）。位置と大きさは「ファイル → ラフ → "
            f"{TOOL_LABELS[TOOL_ROUGH]}」から直せます"
        )

    def remove_rough(self) -> None:
        """表示中のページのラフを外す。

        **調整の道具を持っていたら選択へ戻す。** ラフが無いとこの道具は
        何もできず、しかも項目が押せなくなるので、持ち替える手段が
        メニューからしか残らない。
        """
        if self.state.page.rough is None:
            return
        self.state.remove_rough()
        if self.state.tool == TOOL_ROUGH:
            self.state.set_tool(TOOL_SELECT)
        self.state.message.emit("ラフを外しました")

    def toggle_rough_faded(self) -> None:
        """ラフを青く淡くする／写真のままの色に戻す（1項目の文言を入れ替え）。"""
        rough = self.state.page.rough
        if rough is None:
            return
        self.state.set_rough_faded(not rough.faded)
        self.state.message.emit(
            "ラフを青く淡くしました" if not rough.faded else "ラフを元の色に戻しました"
        )

    def prune_assets(self) -> None:
        """どこからも使われていない画像を assets/_unused/ へ移す。

        保存時に自動で行わない理由は要件定義 5章。Undo で戻した画像の
        実体が消えてしまうため、利用者が選んだときだけ動かす。

        **移した後は Undo の記録も空にする**（2026-08-08 発見）。整理は
        「今のプロジェクトから参照が無い」画像だけを移すが、Undo の記録は
        保存をまたいで積み上がっているので、整理より古い手まで戻ると、
        そちらで参照していた画像が既に移動済みで参照が壊れる——保存時に
        自動実行しない理由（Undo で戻した画像の実体が消える）と同じ壊れ方が、
        手動整理の後にも起こり得た。
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

        try:
            moved = prune_unused_assets(self.state.project, self.state.project_dir)
        except OSError as e:
            # 対象の画像が他アプリ（ビューアなど）で開かれたままだと、
            # 移動しようとした os.replace / unlink が失敗する。ここが
            # 無防備だと、Qt のスロットの中で例外が漏れるだけになる
            # （2026-08-08 に発見）
            QMessageBox.critical(self, "整理できません", str(e))
            return
        if not moved:
            self.state.message.emit("使われていない画像はありませんでした")
            return
        # 整理より古い Undo の記録が、移した画像を参照したままだと
        # 実体と食い違う（2026-08-08 発見）。整理の直後に記録を空にして、
        # その組み合わせ自体を起こらなくする
        self.state.history.forget_undo_history()
        # **記録を捨てたら画面も揃える。** `History` は通知を出さないので、
        # `state.changed` などを引き金にしている `refresh` は走らない。
        # 呼ばないと編集メニューが「元に戻す: コマの移動」と出たまま押せて、
        # 押すと「これ以上戻せません」と返る——直後にダイアログで伝えた
        # 内容と食い違う（2026-08-09 に発見）
        self._refresh()
        QMessageBox.information(
            self,
            "整理しました",
            f"{len(moved)} 件を assets/_unused/ へ移しました。\n"
            "削除はしていないので、戻したいときはフォルダから取り出せます。\n"
            "この操作より前へは「元に戻す」で戻れなくなりました。",
        )
        self.state.message.emit(f"{len(moved)} 件を _unused/ へ移しました")

    def run_check(self) -> None:
        """抜けチェック（点検 → 要件定義 10.1）。**何も直さない。**

        結果は2か所に出す。**何が問題か**は窓に文で（閉じるまで残る）、
        **どのページか**は一覧の紫の印で。状態表示の1行には件数だけを出す
        ——6秒で消えるので、明細を置く場所には使えない。

        **作品には一切書かない。** 印は画面の状態（`state.check_marks`）
        だけに持つので、Undo にも保存形式にもサムネイルの指紋にも乗らない。
        """
        findings = inspect_project(
            self.state.project, self.state.has_asset, self.state.asset_px
        )
        self.state.set_check_marks(marked_page_ids(findings))

        if self._check_dialog is None:
            self._check_dialog = CheckResultDialog(self)
        self._check_dialog.show_result(findings)
        self.state.message.emit(headline(findings))

    def search_menu(self) -> None:
        """メニューを探す窓を出す（→ 要件定義 6.30）。

        **開くたびに一覧を取り直す。** メニューの文言は状態で変わるので
        （調整中の名前、開いた作品のファイル名 → 6.27、6.6）、作り置きを
        使い回すと古い名前で出る。数十項目を読むだけなので、毎回作っても
        待たされない。
        """
        if self._menu_search_dialog is None:
            self._menu_search_dialog = MenuSearchDialog(self)
            self._menu_search_dialog.menu_chosen.connect(self.highlight_menu)
        self._menu_search_dialog.show_entries(collect_menu_entries(self))

    def show_shortcuts(self) -> None:
        """ショートカットキーの一覧を出す（→ 要件定義 7章）。

        **開くたびに作り直す。** 一覧はメニューから作っているので
        （→ `shortcuts.collect_groups`）、探す窓と同じく作り置きにすると
        状態で変わる文言が古いまま残る。
        """
        if self._shortcuts_dialog is None:
            self._shortcuts_dialog = ShortcutsDialog(self)
        self._shortcuts_dialog.show_groups(collect_groups(self))

    def highlight_menu(self, name: str) -> None:
        """メニューバーの見出し1つを四角く囲む（→ 要件定義 6.30）。

        **開いて見せるのではなく、場所を指すだけ。** メニューを外から
        開かせる操作は環境で挙動が割れやすいうえ、開いたメニューは押せて
        しまう——「この窓から実行はしない」という線引きの裏口になる。

        枠は `QRubberBand`（範囲を示すためだけの部品）を借りる。自前で
        描くと、メニューバーの上に重ねる順番と再描画の面倒を見ることになる。

        知らない名前が来ても黙って何もしない。メニューの名前を変えたときに
        探す窓のほうが古い名前を持っている、という食い違いは起こりうるが、
        そこで画面を止めるほどのことではない。
        """
        bar = self.menuBar()
        action = next(
            (a for a in bar.actions() if plain_label(a.text()) == name), None
        )
        if action is None:
            return
        if self._menu_highlight is None:
            # 親をメニューバーにする。座標をそのまま使えて、窓を動かしても付いて回る
            self._menu_highlight = QRubberBand(QRubberBand.Shape.Rectangle, bar)
            self._menu_highlight_timer = QTimer(self)
            self._menu_highlight_timer.setSingleShot(True)
            self._menu_highlight_timer.timeout.connect(self._menu_highlight.hide)
        self._menu_highlight.setGeometry(bar.actionGeometry(action))
        self._menu_highlight.show()
        # 同じ項目を押し直したときは、消えるまでの時間を数え直す
        self._menu_highlight_timer.start(int(HIGHLIGHT_SECONDS * 1000))

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

    # -- 終了時 ------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self.files.confirm_discard():
            event.accept()
        else:
            event.ignore()
