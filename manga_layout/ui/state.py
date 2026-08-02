"""編集中の状態。画面の各部品が共通で見る一箇所。

`History` がプロジェクトの唯一の持ち主なので、ここでも `Project` を
直接抱えない。Undo で実体が差し替わったときに、古い `Page` や `Panel` を
掴んだままの画面が出ないようにするため（要件定義 6.8）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import pathlib
from typing import Iterator

from PySide6.QtCore import QObject, Signal

from ..assets import AssetStore, PendingAssets
from ..geometry import Rect
from ..history import History
from ..images import ImageCache, Preview, preview_from_bytes
from ..layout import (
    BalloonSettings,
    LayoutSettings,
    attach_target,
    contain_rect_in,
    default_tail_tip,
)
from ..model import (
    BalloonObject,
    ImageObject,
    Page,
    Panel,
    Project,
    SceneObject,
    Tail,
    TextObject,
    new_project,
)
from ..storage import load_project, save_project

# 道具（ツール）
TOOL_SELECT = "select"
TOOL_PANEL = "panel"
TOOL_SPLIT_H = "split_h"
TOOL_SPLIT_V = "split_v"
TOOL_BALLOON = "balloon"
TOOL_BALLOON_JAGGED = "balloon_jagged"

TOOL_LABELS = {
    TOOL_SELECT: "選択",
    TOOL_PANEL: "コマ追加",
    TOOL_SPLIT_H: "横に分割",
    TOOL_SPLIT_V: "縦に分割",
    TOOL_BALLOON: "吹き出し",
    TOOL_BALLOON_JAGGED: "吹き出し（ギザ）",
}

# どの道具がどの種類の吹き出しを作るか
BALLOON_TOOLS = {TOOL_BALLOON: "ellipse", TOOL_BALLOON_JAGGED: "jagged"}


class EditorState(QObject):
    """開いている作品と、画面上の選択・道具。"""

    # モデルが変わった（描き直しが要る）
    changed = Signal()
    # 選択が変わった
    selection_changed = Signal()
    # 道具が変わった
    tool_changed = Signal()
    # 表示中のページが変わった
    page_changed = Signal()
    # 状態表示に出すお知らせ
    message = Signal(str)

    def __init__(self, project: Project | None = None, project_dir: pathlib.Path | None = None):
        super().__init__()
        self.history = History(project if project is not None else new_project())
        self.project_dir = project_dir
        self.settings = LayoutSettings()
        self.balloon_settings = BalloonSettings()
        self._page_index = 0
        self._tool = TOOL_SELECT
        self._selected_id: str | None = None
        # 保存先が決まる前に貼り付けた画像の預かり所。保存時に書き出す
        self.pending_assets = PendingAssets()
        self.image_cache = ImageCache()

    # -- 参照 --------------------------------------------------------------

    @property
    def project(self) -> Project:
        return self.history.project

    @property
    def page(self) -> Page:
        return self.project.pages[self._page_index]

    @property
    def page_index(self) -> int:
        return self._page_index

    @property
    def page_count(self) -> int:
        return len(self.project.pages)

    @property
    def tool(self) -> str:
        return self._tool

    @property
    def selected_id(self) -> str | None:
        return self._selected_id

    @property
    def selected_object(self) -> SceneObject | None:
        """選択中のもの（コマ、またはコマの中の画像）。

        Undo で消えていれば None。id で毎回引き直すのは、Undo で
        `Project` の実体が差し替わるため（要件定義 6.8）。
        """
        if self._selected_id is None:
            return None
        return self.page.find(self._selected_id)

    @property
    def selected_panel(self) -> Panel | None:
        """選択中のコマ。画像を選んでいるときは None。"""
        obj = self.selected_object
        return obj if isinstance(obj, Panel) else None

    @property
    def selected_image(self) -> ImageObject | None:
        """選択中の画像。コマを選んでいるときは None。"""
        obj = self.selected_object
        return obj if isinstance(obj, ImageObject) else None

    @property
    def selected_balloon(self) -> BalloonObject | None:
        """選択中の吹き出し。"""
        obj = self.selected_object
        return obj if isinstance(obj, BalloonObject) else None

    @property
    def selected_bounds(self) -> Rect | None:
        """選択枠とつまみを描く矩形。"""
        obj = self.selected_object
        if isinstance(obj, Panel):
            return obj.shape.bounds()
        if isinstance(obj, (ImageObject, BalloonObject, TextObject)):
            return obj.rect
        return None

    # -- 操作 --------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        if tool == self._tool:
            return
        self._tool = tool
        self.tool_changed.emit()

    def select(self, panel_id: str | None) -> None:
        if panel_id == self._selected_id:
            return
        self._selected_id = panel_id
        self.selection_changed.emit()

    def set_page_index(self, index: int) -> None:
        index = max(0, min(index, self.page_count - 1))
        if index == self._page_index:
            return
        self._page_index = index
        self._selected_id = None
        self.page_changed.emit()
        self.selection_changed.emit()
        self.changed.emit()

    # -- 編集 --------------------------------------------------------------

    @contextlib.contextmanager
    def edit(self, label: str, *, merge_key: str | None = None) -> Iterator[Project]:
        """1手ぶんの編集。抜けたところで履歴に積み、画面を描き直す。"""
        with self.history.edit(label, merge_key=merge_key) as project:
            yield project
        self.changed.emit()

    def undo(self) -> None:
        label = self.history.undo()
        if label is None:
            self.message.emit("これ以上戻せません")
            return
        self._after_history_move(f"元に戻しました: {label}")

    def redo(self) -> None:
        label = self.history.redo()
        if label is None:
            self.message.emit("やり直せる操作がありません")
            return
        self._after_history_move(f"やり直しました: {label}")

    def _after_history_move(self, message: str) -> None:
        # ページが減っていた場合に備えて番号を丸める
        self._page_index = max(0, min(self._page_index, self.page_count - 1))
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()
        self.message.emit(message)

    # -- 画像 --------------------------------------------------------------

    def read_asset(self, ref: str) -> bytes | None:
        """画像の実体。まだ保存していないものは預かり所から取る。"""
        data = self.pending_assets.get(ref)
        if data is not None:
            return data
        if self.project_dir is None:
            return None
        store = AssetStore(self.project_dir)
        return store.read(ref) if store.exists(ref) else None

    def preview(self, ref: str) -> Preview | None:
        """画面に描くための1枚。無い・壊れているときは None。"""
        return self.image_cache.get(ref, lambda: self.read_asset(ref))

    def import_bytes(self, data: bytes) -> tuple[str, tuple[int, int]]:
        """画像を取り込み、参照と原寸のピクセル寸法を返す。

        **展開できるか先に確かめる。** `assets.py` はバイト列しか見ないため、
        署名だけ正しい壊れたファイルを通してしまう。内容ハッシュが名前なので、
        一度入れてしまうと人が見分けられなくなる。
        """
        preview = preview_from_bytes(data)  # 壊れていればここで例外
        if self.project_dir is None:
            ref = self.pending_assets.add(data)
        else:
            ref = AssetStore(self.project_dir).add_bytes(data)
        self.image_cache.put(ref, preview)
        return ref, preview.source_px

    def place_image(self, panel_id: str, data: bytes) -> ImageObject:
        """コマに画像を1枚置く。置いた画像を選択状態にする。

        最初は**コマに収まる大きさ**（全体が見える）にする。埋めたいときは
        「コマにフィット」を使う。いきなり埋めると、絵のどこが切れているのか
        分からないまま進んでしまう。
        """
        ref, px = self.import_bytes(data)
        page = self.page
        rect = contain_rect_in(page.panel(panel_id).shape.bounds(), px)

        with self.edit("画像の配置") as project:
            target = project.pages[self._page_index].panel(panel_id)
            image = project.add_image(target, ref, rect, px)
        self.select(image.id)
        return image

    # -- 吹き出し ----------------------------------------------------------

    def add_balloon(self, rect: Rect, style: str = "ellipse") -> BalloonObject:
        """吹き出しを1つ置く。置いたものを選択状態にする。

        重なっているコマに自動で紐づける（要件定義 6.4）。紐づけておくと、
        あとでコマを動かしたときに吹き出しが付いて回る。外したいときは
        あとから解除できる。
        """
        page = self.page
        attached = attach_target(page, rect)
        tip = default_tail_tip(rect)

        with self.edit("吹き出しの追加") as project:
            balloon = project.add_balloon(
                project.pages[self._page_index], rect, style, attached
            )
            balloon.tail = Tail(
                enabled=True, tip=tip, width=self.balloon_settings.tail_width
            )
        self.select(balloon.id)
        return balloon

    def _edit_balloon(self, balloon_id: str, label: str):
        """id で引き直してから触るための小さな入れ物。

        Undo で `Project` の実体が差し替わるため、外で掴んだ吹き出しを
        そのまま書き換えてはいけない（要件定義 6.8）。
        """

        @contextlib.contextmanager
        def scope():
            with self.edit(label) as project:
                target = project.pages[self._page_index].find(balloon_id)
                if not isinstance(target, BalloonObject):
                    raise KeyError(f"吹き出しが見つかりません: {balloon_id}")
                yield target

        return scope()

    def set_balloon_style(self, balloon_id: str, style: str) -> None:
        with self._edit_balloon(balloon_id, "吹き出しの種類変更") as balloon:
            balloon.style = style

    def set_tail_tip(self, balloon_id: str, tip: tuple[float, float]) -> None:
        with self._edit_balloon(balloon_id, "しっぽの向き") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, tip=tip, enabled=True)

    def set_tail_enabled(self, balloon_id: str, enabled: bool) -> None:
        label = "しっぽを出す" if enabled else "しっぽを消す"
        with self._edit_balloon(balloon_id, label) as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, enabled=enabled)

    def set_attachment(self, balloon_id: str, panel_id: str | None) -> None:
        """コマへの紐づけを付け替える。None で解除。"""
        label = "コマへの紐づけ" if panel_id else "紐づけの解除"
        with self._edit_balloon(balloon_id, label) as balloon:
            balloon.attached_panel_id = panel_id

    # -- ファイル ----------------------------------------------------------

    def reset(self, project: Project, project_dir: pathlib.Path | None) -> None:
        """別の作品に入れ替える。履歴も作り直す。"""
        self.history = History(project)
        self.project_dir = project_dir
        self._page_index = 0
        self._selected_id = None
        # 前の作品の画像を抱えたままにしない。参照が同じでも中身は別物
        self.pending_assets = PendingAssets()
        self.image_cache.clear()
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()

    def load(self, project_dir: pathlib.Path) -> list[str]:
        """作品を開く。読み込み時に直した内容があれば返す。"""
        project = load_project(project_dir)
        warnings = list(project.load_warnings)
        self.reset(project, project_dir)
        return warnings

    def save(self, project_dir: pathlib.Path | None = None) -> pathlib.Path:
        target = project_dir or self.project_dir
        if target is None:
            raise ValueError("保存先が決まっていません")

        # **画像の実体を先に書く。** 逆順だと、途中で落ちたときに
        # 実体の無い参照を持つ project.json が残る。この順なら、
        # 最悪でも参照されない画像が余るだけで済む
        self.pending_assets.flush_to(AssetStore(target))

        path = save_project(self.project, target)
        self.project_dir = target
        self.history.mark_saved()
        return path

    @property
    def is_dirty(self) -> bool:
        return self.history.is_dirty
