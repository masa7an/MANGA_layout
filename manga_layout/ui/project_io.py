"""作品のファイル入出力。新規・開く・保存・自動バックアップの段取り。

`MainWindow` から切り出した部品。ダイアログの親には常に窓を使い、
保存後の画面更新（`_refresh`）や表示合わせなど、窓側の都合は
窓のメソッドを呼び返す（`canvas.Drag` が `view` を呼び返すのと同じ向き）。
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..autosave_log import AutosaveLog
from ..errors import MangaLayoutError
from ..recent_project import load_recent_project, save_recent_project
from ..settings import load_settings
from ..storage import PROJECT_FILENAME, is_project_dir, project_dir_of
from . import saving
from .export import (
    DEFAULT_SCALE,
    ExportDialog,
    existing_paths,
    export_dir_of,
    export_pages,
    missing_assets_in,
    page_px,
    planned_paths,
    scale_label,
)
from .saving import SaveAsDialog

if TYPE_CHECKING:
    from .window import MainWindow

# 「開く」の窓に出す対象。作品フォルダそのものではなく、その中の
# project.json を選ばせる（理由は `open_project`）
PROJECT_FILE_FILTER = f"作品ファイル ({PROJECT_FILENAME});;すべてのファイル (*)"

# 自動バックアップで状態表示に出す文言と、記録に残す文言。
#
# **同じ表を見る。** 画面で見たものと `data/autosave.log` に残ったものが
# 食い違うと、報告を突き合わせられなくなる（→ `BALLOON_STYLE_LABELS` と
# 同じ線引き）
AUTOSAVE_SAVED = "自動バックアップしました"
AUTOSAVE_NO_DIR = "保存先が未定のため自動バックアップしません"
AUTOSAVE_NO_CHANGE = "変更が無いため自動バックアップしません"
AUTOSAVE_FAILED = "自動バックアップできません"

# 上書きの確認に名前を並べる件数の上限。
# 30 ページの作品でも確認欄が画面を埋めないようにする
OVERWRITE_LIST_LIMIT = 5


class ProjectIO:
    """ファイル入出力の段取り。窓の属性としては `window.files`。

    設定（`settings` / `settings_file`）は持たず、窓のものを読み書きする。
    起動時の読み込みは窓が行い、使う直前の読み直しは `default_parent` が行う。
    """

    def __init__(self, window: MainWindow) -> None:
        self._window = window
        self._state = window.state

        # 書き出す画像サイズは作品ではなく好みなので、project.json には
        # 入れない。ただし1回の作業中は同じ値を使い続けるのが普通なので覚えておく
        self.export_scale = DEFAULT_SCALE

        # 一定間隔で作業中の内容を backup/ へ退避する（要件定義 6.6）。
        # 本体（project.json）は触らないので、保存の確認とは食い違わない。
        #
        # **間隔は設定から取る。** 5分は待って確かめるには長いので、
        # 短くして動きを見られるようにしてある（→ `AppSettings`）
        self.autosave_log = AutosaveLog()
        interval_sec = window.settings.autosave_interval_sec
        # タイマーの親は窓に取り、窓と同じ寿命にする
        self.autosave_timer = QTimer(window)
        self.autosave_timer.setInterval(interval_sec * 1000)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()
        # 起動を1行残す。**記録が空なら、タイマーを積んだアプリが
        # そもそも動いていない**と分かる。回っているのに何もしない場合と
        # 区別が付かないのが、2026-08-05 の切り分けで困った点
        self.autosave_log.record(f"起動（{interval_sec}秒ごと）", repeat=True)

    def new_project(self) -> None:
        if not self.confirm_discard():
            return
        from ..model import new_project as make

        self._state.reset(make(), None)
        self._state.message.emit("新しい作品を作りました")

    def default_parent(self) -> pathlib.Path:
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
        self._window.settings = load_settings(self._window.settings_file)
        return saving.default_parent(
            self._state.project_dir, self._window.settings.default_parent_dir
        )

    def dialog_start_dir(self) -> str:
        """`QFileDialog` に渡す形にした `default_parent`。"""
        return str(self.default_parent())

    def open_project(self) -> None:
        """作品を開く。**`project.json` を選ばせる。**

        作品はフォルダ単位なので、内部で使うのはその親フォルダのほう。
        それでも「フォルダを選ぶ窓」にはしない。利用者から見れば
        「ファイルを開く」操作で、目当ての `project.json` が一覧に
        出てこないと、選べないのか場所を間違えたのかが分からない。
        """
        if not self.confirm_discard():
            return
        chosen, _ = QFileDialog.getOpenFileName(
            self._window, "作品を開く", self.dialog_start_dir(), PROJECT_FILE_FILTER
        )
        if not chosen:
            return
        self._open_project_dir(project_dir_of(pathlib.Path(chosen)))

    def open_recent_project(self) -> None:
        """『前回のファイルを開く』。窓を出さず、前回の行き先をそのまま開く。

        行き先は `data/recent_project.txt`（→ `recent_project.py`）。開く・
        保存するたびに黙って上書きしている記録で、`settings.json` とは別。
        """
        path = load_recent_project()
        if path is None:
            return
        if not self.confirm_discard():
            return
        self._open_project_dir(path)

    def _open_project_dir(self, path: pathlib.Path) -> None:
        if not is_project_dir(path):
            QMessageBox.warning(
                self._window,
                "開けません",
                f"作品として開けませんでした。\n{path}\n\n"
                f"作品フォルダの中にある {PROJECT_FILENAME} を選んでください。",
            )
            return
        try:
            warnings = self._state.load(path)
        except MangaLayoutError as e:
            QMessageBox.critical(self._window, "開けません", str(e))
            return

        self._window.view.fit_page()
        if warnings:
            QMessageBox.information(
                self._window,
                "読み込み時に直した箇所があります",
                "\n".join(f"・{w}" for w in warnings),
            )
        self._state.message.emit(f"開きました: {path}")
        self._remember_recent_project(path)

    def _remember_recent_project(self, path: pathlib.Path) -> None:
        """『前回のファイルを開く』の行き先を更新する。"""
        save_recent_project(path)
        self._window.file_menu.sync_recent_project(path)

    def save_project(self) -> bool:
        if self._state.project_dir is None:
            return self.save_project_as()
        return self.write(self._state.project_dir)

    def save_project_as(self) -> bool:
        """置き場所と作品名を決めて保存する。

        作品はフォルダなので、「既にあるフォルダを選ぶ」窓では名前を
        付けられない（選んだ瞬間にそこへ書き込まれる）。専用の窓で
        置き場所と名前を分けて受け取る（`saving.SaveAsDialog`）。
        """
        dialog = SaveAsDialog(
            self.default_parent(),
            self._state.project.title,
            self._window,
            self._window.settings_file,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        target = dialog.chosen_path()
        if dialog.overwrites_project() and not self._confirm_overwrite_project(target):
            return False
        return self.write(target)

    def _confirm_overwrite_project(self, path: pathlib.Path) -> bool:
        """既にある作品への上書きを確かめる。よければ True。

        窓の中でも赤字で伝えているが、上書きすると相手の作品の
        `project.json` が置き換わる。押し間違いで消せる場所ではない。
        """
        answer = QMessageBox.question(
            self._window,
            "上書きしますか",
            f"{path} には既に別の作品が入っています。\n"
            "この作品で上書きしますか。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def write(self, path: pathlib.Path) -> bool:
        try:
            self._state.save(path)
        except (MangaLayoutError, OSError) as e:
            QMessageBox.critical(self._window, "保存できません", str(e))
            return False
        self._window._refresh()
        self._state.message.emit(f"保存しました: {path}")
        self._remember_recent_project(path)
        return True

    def autosave(self) -> None:
        """タイマーからの自動バックアップ（要件定義 6.6）。

        **失敗しても作業は止めない。** 窓は出さず、状態表示に出すだけに
        する。利用者が押した操作ではないので、書けなかったからといって
        手を止めさせる理由がない。

        失敗しても印を進めないので、次の回にまた試す。保存先が外付け
        ドライブで抜かれている間は毎回知らせることになるが、**退避されて
        いない事実は伝わったほうがよい。**

        **何もしなかった回も記録には残す**（`data/autosave.log`）。
        状態表示には出さない——5分ごとに「何もしませんでした」と出ても
        邪魔なだけで、しかも見ていないうちに消える。記録は同じ内容が
        続く間 1 行にまとめるので、増え続けることはない。
        """
        try:
            path = self._state.autosave()
        except (MangaLayoutError, OSError) as e:
            self._state.message.emit(f"{AUTOSAVE_FAILED}: {e}")
            self.autosave_log.record(f"{AUTOSAVE_FAILED}: {e}")
            return

        if path is not None:
            self._state.message.emit(f"{AUTOSAVE_SAVED}: {path.name}")
            # 退避できた回は毎回残す。いつの時点の内容が backup/ に
            # 入っているかは、記録の目的そのもの
            self.autosave_log.record(f"{AUTOSAVE_SAVED}: {path}", repeat=True)
            return

        why = AUTOSAVE_NO_DIR if self._state.project_dir is None else AUTOSAVE_NO_CHANGE
        self.autosave_log.record(why)

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
            self._state.page_index,
            self._state.page_count,
            self._window,
            self._state.page.size,
            self.export_scale,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        self.export_scale = dialog.chosen_scale()
        indexes = (
            list(range(self._state.page_count))
            if dialog.wants_all_pages()
            else [self._state.page_index]
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
            return export_dir_of(self._state)
        except MangaLayoutError as e:
            answer = QMessageBox.question(
                self._window,
                "先に保存が必要です",
                f"{e}\n\n今すぐ保存しますか。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer != QMessageBox.StandardButton.Save or not self.save_project():
                return None
        return export_dir_of(self._state)

    def _confirm_missing(self, indexes: list[int]) -> bool:
        """実体の無い画像があれば知らせる。続けてよければ True。

        画面では×印が出ているが、書き出しには目印を描かない（作品ではない
        ため）。黙って白く抜けるので、ここで必ず言う。
        """
        count = missing_assets_in(self._state, indexes)
        if count == 0:
            return True
        answer = QMessageBox.warning(
            self._window,
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
        found = existing_paths(planned_paths(dest, indexes, self._state.page_count))
        if not found:
            return True

        shown = [p.name for p in found[:OVERWRITE_LIST_LIMIT]]
        if len(found) > OVERWRITE_LIST_LIMIT:
            shown.append(f"ほか {len(found) - OVERWRITE_LIST_LIMIT} 件")
        answer = QMessageBox.question(
            self._window,
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
            written = export_pages(self._state, indexes, dest, self.export_scale)
        except (MangaLayoutError, OSError) as e:
            QMessageBox.critical(self._window, "書き出せません", str(e))
            return False
        finally:
            QGuiApplication.restoreOverrideCursor()

        where = written[0].name if len(written) == 1 else f"{len(written)} 枚"
        px = page_px(self._state.page.size, self.export_scale)
        self._state.message.emit(
            f"{dest} に {where} を書き出しました"
            f"（{scale_label(self.export_scale)}・{px[0]:,} × {px[1]:,} 画素）"
        )
        return True

    # -- 終了時 ------------------------------------------------------------

    def confirm_discard(self) -> bool:
        """未保存の変更があれば確認する。続けてよければ True。"""
        if not self._state.is_dirty:
            return True
        answer = QMessageBox.question(
            self._window,
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
