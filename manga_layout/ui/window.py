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
from ..model import PT_TO_PX, ImageObject, Panel
from ..settings import ensure_settings_file, load_settings, settings_path
from ..storage import (
    PROJECT_FILENAME,
    is_project_dir,
    project_dir_of,
    prune_unused_assets,
)
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
from .saving import SaveAsDialog, default_parent
from .state import (
    TOOL_BALLOON,
    TOOL_BALLOON_JAGGED,
    TOOL_LABELS,
    TOOL_PANEL,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_TEXT,
    EditorState,
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

# 「開く」の窓に出す対象。作品フォルダそのものではなく、その中の
# project.json を選ばせる（理由は `open_project`）
PROJECT_FILE_FILTER = f"作品ファイル ({PROJECT_FILENAME});;すべてのファイル (*)"

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
        for tool, shortcut in (
            (TOOL_SELECT, "V"),
            (TOOL_PANEL, "P"),
            (TOOL_SPLIT_H, "H"),
            (TOOL_SPLIT_V, "J"),
            (TOOL_SPLIT_SLANT, "K"),
            (TOOL_BALLOON, "B"),
            (TOOL_BALLOON_JAGGED, "G"),
            (TOOL_TEXT, "T"),
        ):
            action = QAction(f"{TOOL_LABELS[tool]} ({shortcut})", self)
            action.setCheckable(True)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _checked=False, t=tool: self.state.set_tool(t))
            group.addAction(action)
            self.addAction(action)
            self._tool_actions[tool] = action
        self._tool_actions[TOOL_SELECT].setChecked(True)

    def _build_text_menu(self) -> None:
        """セリフのメニュー。

        先頭に「作る」を置く。ここが選択中のセリフへの操作だけだと、
        1つも選んでいない間はメニュー全体がグレーになり、
        どこから作るのか分からなくなる（吹き出しで一度やった失敗）。
        """
        menu = self.menuBar().addMenu("セリフ(&X)")
        # 右クリックのメニューが項目を写して使う（→ `_copy_actions`）
        self.text_menu = menu
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

        self.vertical_action = add("縦書き", self.toggle_vertical, "Ctrl+T")
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

    def _build_menus(self) -> None:
        self._build_tool_actions()

        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction(self._act("新規作成", self.new_project, "Ctrl+N"))
        file_menu.addAction(
            self._act(
                "開く...",
                self.open_project,
                "Ctrl+O",
                f"作品フォルダの中の {PROJECT_FILENAME} を選ぶ",
            )
        )
        file_menu.addSeparator()
        file_menu.addAction(self._act("保存", self.save_project, "Ctrl+S"))
        file_menu.addAction(
            self._act("名前を付けて保存...", self.save_project_as, "Ctrl+Shift+S")
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
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
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
        image_menu.addSeparator()
        image_menu.addAction(self._act("未使用ファイルを整理...", self.prune_assets))

        balloon_menu = self.menuBar().addMenu("吹き出し(&B)")
        # 右クリックのメニューが項目を写して使う（→ `_copy_actions`）
        self.balloon_menu = balloon_menu
        # まず「作る」を置く。これが無いと、吹き出しを1つも選んでいない間は
        # メニュー全体がグレーになり、どこから作るのか分からなくなる
        balloon_menu.addAction(self._tool_actions[TOOL_BALLOON])
        balloon_menu.addAction(self._tool_actions[TOOL_BALLOON_JAGGED])
        balloon_menu.addSeparator()

        # ここから下は選択中の吹き出しに対する操作
        self.balloon_actions: list[QAction] = []
        for label, slot in (
            ("楕円にする", lambda: self.set_balloon_style("ellipse")),
            ("ギザギザにする", lambda: self.set_balloon_style("jagged")),
        ):
            action = self._act(label, slot)
            balloon_menu.addAction(action)
            self.balloon_actions.append(action)
        balloon_menu.addSeparator()

        self.tail_action = self._act("しっぽを消す", self.toggle_tail)
        balloon_menu.addAction(self.tail_action)
        self.balloon_actions.append(self.tail_action)

        for label, ratio in (
            ("付け根を上端へ", -1.0),
            ("付け根を中央へ", 0.0),
            ("付け根を下端へ", 1.0),
            ("付け根を自動に戻す", None),
        ):
            action = self._act(label, lambda _=False, r=ratio: self.set_tail_root(r))
            balloon_menu.addAction(action)
            self.balloon_actions.append(action)

        self.attach_action = self._act("コマへの紐づけを解除", self.toggle_attachment)
        balloon_menu.addAction(self.attach_action)
        self.balloon_actions.append(self.attach_action)

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
        state = self.state

        if state.selected_text is not None:
            self._copy_actions(menu, self.text_menu)

        elif state.selected_balloon is not None:
            self._copy_actions(menu, self.balloon_menu)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("text",))

        elif state.selected_image is not None:
            menu.addAction(self.fit_action)

        elif state.selected_panel is not None:
            self._add_split_here(menu, x, y)
            menu.addAction(self.slant_flip_action)
            menu.addSeparator()
            self._add_place_here(menu, x, y, ("balloon", "text"))
            menu.addSeparator()
            menu.addAction(self.paste_action)
            menu.addAction(self.open_image_action)
            self._add_delete_image_here(menu, state.selected_panel, x, y)

        else:
            # 何も無いところ。選択に効く項目はどれも使えないので出さない。
            # 代わりに、ここでしか呼べない「元に戻す」を添える
            self._add_place_here(menu, x, y, ("panel", "balloon", "text"))
            menu.addAction(self.full_page_action)
            menu.addSeparator()
            menu.addAction(self.undo_action)
            menu.addAction(self.redo_action)
            return menu

        menu.addSeparator()
        menu.addAction(self.delete_action)
        return menu

    def _copy_actions(self, menu: QMenu, source: QMenu) -> None:
        """メニューバーのメニューから項目を写して並べる。

        QAction は写しても実体は1つなので、有効・無効も文言も
        メニューバー側と自動で揃う。

        **道具の切り替え（「楕円を追加」など）は外す。** 右クリック側は
        押した場所が分かっているので「ここに〜」を別に出しており、道具に
        持ち替える項目まで並べると、同じことが2通り並ぶ。
        """
        tools = set(self._tool_actions.values())
        for action in source.actions():
            if action in tools:
                continue
            # 道具を外した跡に区切り線だけが残る。先頭と連続は出さない
            last = menu.actions()[-1] if menu.actions() else None
            if action.isSeparator() and (last is None or last.isSeparator()):
                continue
            menu.addAction(action)

    def _add_place_here(
        self, menu: QMenu, x: float, y: float, kinds: tuple[str, ...]
    ) -> None:
        """押した場所に1つ置く項目。`kinds` に挙げた種類だけ出す。

        名前を「ここに」で始めるのは、メニューバー側の「〜を追加」
        （道具に持ち替えて、次に押した場所に置く）と区別するため。
        こちらは道具を持ち替えず、その場で置いて終わる。
        """
        items = (
            ("panel", "ここにコマを追加", lambda: self.view.add_panel_at(x, y)),
            (
                "balloon",
                "ここに吹き出しを追加",
                lambda: self.view.add_balloon_at(x, y, "ellipse"),
            ),
            (
                "balloon",
                "ここにギザギザを追加",
                lambda: self.view.add_balloon_at(x, y, "jagged"),
            ),
            ("text", "ここにセリフを追加", lambda: self.view.add_text_at(x, y)),
        )
        for kind, label, slot in items:
            if kind in kinds:
                self._menu_act(menu, label, slot)

    def _add_delete_image_here(
        self, menu: QMenu, panel: Panel, x: float, y: float
    ) -> None:
        """カーソルの下に画像があれば、その画像を消す項目を出す。

        **コマを選んだままでも画像を消せるようにする。** 画像を選ぶには
        ダブルクリックで一段踏み込む必要があり（要件定義 6.3）、右クリック
        しただけではコマが選ばれる。この項目が無いと、メニューに並ぶのは
        「コマを削除」だけになり、**画像を消すつもりでコマを消す**ことになる。
        実際にその取り違えが起きたので足した。
        """
        image = image_at(panel, x, y)
        if image is None:
            return
        self._menu_act(
            menu,
            "この画像を削除",
            lambda _checked=False, i=image.id: self.delete_image(i),
        )

    def _add_split_here(self, menu: QMenu, x: float, y: float) -> None:
        """押した場所で1回きり割る項目。

        メニューバー側は道具の切り替え（選んでから、割る場所を押す）だが、
        右クリックは押した場所が既に分かっているので、その場で割る。
        """
        for label, tool in (
            ("ここで横に割る", TOOL_SPLIT_H),
            ("ここで縦に割る", TOOL_SPLIT_V),
            ("ここで斜めに割る", TOOL_SPLIT_SLANT),
        ):
            self._menu_act(
                menu,
                label,
                # 既定値で受けているのは、triggered が渡す checked を
                # tool の位置で受け取らないようにするため
                lambda _checked=False, t=tool: self.view.split_at(x, y, t),
            )

    @staticmethod
    def _menu_act(menu: QMenu, label: str, slot) -> QAction:
        """そのメニュー限りの項目。

        親をメニューにしてあるので、メニューを捨てれば一緒に消える。
        ウィンドウに持たせると、右クリックのたびに溜まっていく。
        """
        action = QAction(label, menu)
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
        self.fit_action.setEnabled(self.state.selected_image is not None)
        self.slant_flip_action.setEnabled(self.state.selected_slant_pair is not None)

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
            self.attach_action.setText(
                "コマへの紐づけを解除"
                if balloon.attached_panel_id
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
            tied = "吹き出しに紐づけ" if text.attached_balloon_id else "紐づけなし"
            lines = text.content.count("\n") + 1 if text.content else 0
            body = f"{lines} 行" if lines else "（未入力）"
            lay = "縦書き" if text.direction == "vertical" else "横書き"
            return (
                f"セリフを選択中: {body} / {font.family} {self._size_label(font.size_px)}{weight}"
                f" / {lay} / {align_label(text.align, text.direction)} / {tied}"
            )

        balloon = self.state.selected_balloon
        if balloon is not None:
            r = balloon.rect
            kind = "ギザギザ" if balloon.style == "jagged" else "楕円"
            tied = "コマに紐づけ" if balloon.attached_panel_id else "紐づけなし"
            return f"吹き出しを選択中: {kind} / {r.w:.0f} × {r.h:.0f} px / {tied}"

        panel = self.state.selected_panel
        if panel is not None:
            b = panel.shape.bounds()
            count = len(panel.children)
            inside = f" / 画像 {count} 枚" if count else ""
            return f"コマを選択中: {b.w:.0f} × {b.h:.0f} px{inside}"

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
        if self.state.selected_balloon is not None:
            return "吹き出し", self.delete_balloon
        if self.state.selected_panel is not None:
            return "コマ", self.delete_panel
        return None

    def delete_selected(self) -> None:
        """Delete キー。選んでいるものに応じて消し分ける。"""
        target = self.delete_target()
        if target is not None:
            target[1]()

    def delete_balloon(self) -> None:
        """吹き出しを消す。上に乗っていたセリフは残り、紐づけだけ外れる。"""
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        balloon_id = balloon.id
        with self.state.edit("吹き出しの削除") as project:
            project.pages[self.state.page_index].remove_floating(balloon_id)
        self.state.select(None)
        self.state.message.emit("吹き出しを削除しました")

    def delete_panel(self) -> None:
        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id
        with self.state.edit("コマの削除") as project:
            project.pages[self.state.page_index].remove_panel(panel_id)
        self.state.select(None)
        self.state.message.emit("コマを削除しました")

    def flip_slant(self) -> None:
        """斜めに割った2枚の傾きを入れ替える。

        外側の矩形は変わらないので、隣のコマとの位置関係は動かない。
        """
        if self.state.flip_slant():
            self.state.message.emit("斜めの向きを反転しました")

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

        with self.state.edit("画像の削除") as project:
            target = project.pages[self.state.page_index].panel_of_image(image_id)
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
        w, h = image.src_px
        self.state.message.emit(
            f"画像を置きました（{source} / {w}×{h} px）。"
            "コマを埋めるなら Ctrl+Shift+F"
        )
        return True

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

    def open_image_file(self) -> None:
        """コマに入れる画像を選ぶ。

        **始まる場所は保存・作品を開くと揃える。** 下書きは作品の隣に
        置かれることが多いので、そこから始めれば辿り直さずに済む。
        空文字を渡すと**アプリを起動したフォルダ**から始まり、
        毎回どこか分からない場所を辿ることになる。
        """
        panel = self._target_panel()
        if panel is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "画像を選ぶ", self._dialog_start_dir(), IMAGE_FILE_FILTER
        )
        if not path:
            return
        file = pathlib.Path(path)
        try:
            data = file.read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "画像を読めません", f"{file}\n{e}")
            return
        self._place_image(panel.id, data, file.name)

    def fit_image(self) -> None:
        """選択中の画像でコマを埋める。はみ出た分はコマの形で切り抜かれる。"""
        image = self.state.selected_image
        if image is None:
            self.state.message.emit("先に画像を選んでください（コマの中をダブルクリック）")
            return
        panel = self.state.page.panel_of_image(image.id)
        if panel is None:
            return

        rect = cover_rect_in(panel.shape.bounds(), image.src_px)
        if rect == image.rect:
            return
        image_id = image.id
        with self.state.edit("コマにフィット") as project:
            target = project.pages[self.state.page_index].find(image_id)
            if isinstance(target, ImageObject):
                target.rect = rect
        self.state.message.emit(f"コマを埋めました（{rect.w:.0f} × {rect.h:.0f} px）")

    # -- 吹き出し ----------------------------------------------------------

    def set_balloon_style(self, style: str) -> None:
        balloon = self.state.selected_balloon
        if balloon is None or balloon.style == style:
            return
        self.state.set_balloon_style(balloon.id, style)
        self.state.message.emit(
            "ギザギザにしました" if style == "jagged" else "楕円にしました"
        )

    def toggle_tail(self) -> None:
        balloon = self.state.selected_balloon
        if balloon is None:
            return
        enabled = not balloon.tail.enabled
        self.state.set_tail_enabled(balloon.id, enabled)
        self.state.message.emit("しっぽを出しました" if enabled else "しっぽを消しました")

    def set_tail_root(self, root_y: float | None) -> None:
        """しっぽの付け根の縦位置。None は先端の向きに合わせる（自動）。"""
        balloon = self.state.selected_balloon
        if balloon is None or balloon.tail.root_y == root_y:
            return
        if not balloon.tail.enabled:
            self.state.message.emit("しっぽが出ていません")
            return
        self.state.set_tail_root(balloon.id, root_y)
        where = "自動" if root_y is None else PageView._root_label(root_y)
        self.state.message.emit(f"しっぽの付け根: {where}")

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
        with self.state.edit("セリフの削除") as project:
            project.pages[self.state.page_index].remove_floating(text_id)
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
        placed = f"コマ・吹き出し・セリフが {count} 個置かれています。\n" if count else ""
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

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        from ..model import new_project as make

        self.state.reset(make(), None)
        self.state.message.emit("新しい作品を作りました")

    def _default_parent(self) -> pathlib.Path:
        """ファイルの窓が始まる場所。

        **開く・保存・画像を選ぶで同じ場所を使う。** 別々にすると、
        同じ作業の途中なのに窓ごとに違う場所から始まり、そのたびに
        辿り直すことになる。決め方は `saving.default_parent`
        （開いている作品の隣 → `settings.json` → ドキュメント）。

        **設定は使う直前に読み直す。** `settings.json` は手で書き換える
        前提のファイルなのに、起動時に一度読むだけだと**書き換えても
        アプリを開き直すまで効かない**。しかも効かない理由が画面に出ない
        ので、設定の書き方を間違えたのかと疑うことになる（2026-08-03 に
        実際に起きた）。窓を開く瞬間に数百バイト読むだけなので、
        待たされることはない。
        """
        self.settings = load_settings(self.settings_file)
        return default_parent(self.state.project_dir, self.settings.default_parent_dir)

    def _dialog_start_dir(self) -> str:
        """`QFileDialog` に渡す形にした `_default_parent`。"""
        return str(self._default_parent())

    def open_project(self) -> None:
        """作品を開く。**`project.json` を選ばせる。**

        作品はフォルダ単位なので、内部で使うのはその親フォルダのほう。
        それでも「フォルダを選ぶ窓」にはしない。利用者から見れば
        「ファイルを開く」操作で、目当ての `project.json` が一覧に
        出てこないと、選べないのか場所を間違えたのかが分からない。
        """
        if not self._confirm_discard():
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self, "作品を開く", self._dialog_start_dir(), PROJECT_FILE_FILTER
        )
        if not chosen:
            return

        path = project_dir_of(pathlib.Path(chosen))
        if not is_project_dir(path):
            QMessageBox.warning(
                self,
                "開けません",
                f"作品として開けませんでした。\n{path}\n\n"
                f"作品フォルダの中にある {PROJECT_FILENAME} を選んでください。",
            )
            return
        try:
            warnings = self.state.load(path)
        except MangaLayoutError as e:
            QMessageBox.critical(self, "開けません", str(e))
            return

        self.view.fit_page()
        if warnings:
            QMessageBox.information(
                self,
                "読み込み時に直した箇所があります",
                "\n".join(f"・{w}" for w in warnings),
            )
        self.state.message.emit(f"開きました: {path}")

    def save_project(self) -> bool:
        if self.state.project_dir is None:
            return self.save_project_as()
        return self._write(self.state.project_dir)

    def save_project_as(self) -> bool:
        """置き場所と作品名を決めて保存する。

        作品はフォルダなので、「既にあるフォルダを選ぶ」窓では名前を
        付けられない（選んだ瞬間にそこへ書き込まれる）。専用の窓で
        置き場所と名前を分けて受け取る（`saving.SaveAsDialog`）。
        """
        dialog = SaveAsDialog(
            self._default_parent(),
            self.state.project.title,
            self,
            self.settings_file,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        target = dialog.chosen_path()
        if dialog.overwrites_project() and not self._confirm_overwrite_project(target):
            return False
        return self._write(target)

    def _confirm_overwrite_project(self, path: pathlib.Path) -> bool:
        """既にある作品への上書きを確かめる。よければ True。

        窓の中でも赤字で伝えているが、上書きすると相手の作品の
        `project.json` が置き換わる。押し間違いで消せる場所ではない。
        """
        answer = QMessageBox.question(
            self,
            "上書きしますか",
            f"{path} には既に別の作品が入っています。\n"
            "この作品で上書きしますか。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _write(self, path: pathlib.Path) -> bool:
        try:
            self.state.save(path)
        except (MangaLayoutError, OSError) as e:
            QMessageBox.critical(self, "保存できません", str(e))
            return False
        self._refresh()
        self.state.message.emit(f"保存しました: {path}")
        return True

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
            if answer != QMessageBox.StandardButton.Save or not self.save_project():
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

    def _confirm_discard(self) -> bool:
        """未保存の変更があれば確認する。続けてよければ True。"""
        if not self.state.is_dirty:
            return True
        answer = QMessageBox.question(
            self,
            "保存しますか",
            "保存していない変更があります。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save_project()
        return answer == QMessageBox.StandardButton.Discard

    def closeEvent(self, event) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
