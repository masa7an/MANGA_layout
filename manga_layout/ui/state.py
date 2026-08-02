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
from ..geometry import Rect, Size
from ..history import History
from ..images import ImageCache, Preview, preview_from_bytes
from ..layout import (
    BalloonSettings,
    LayoutSettings,
    attach_target,
    balloon_at,
    contain_rect_in,
    default_tail_tip,
    flip_slant_pair,
    outside_page,
    slide_slant_pair,
)
from ..model import (
    BalloonObject,
    ImageObject,
    Page,
    Panel,
    Project,
    SceneObject,
    SlantPair,
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
TOOL_SPLIT_SLANT = "split_slant"
TOOL_BALLOON = "balloon"
TOOL_BALLOON_JAGGED = "balloon_jagged"
TOOL_TEXT = "text"

TOOL_LABELS = {
    TOOL_SELECT: "選択",
    TOOL_PANEL: "コマ追加",
    TOOL_SPLIT_H: "横に分割",
    TOOL_SPLIT_V: "縦に分割",
    TOOL_SPLIT_SLANT: "斜めに縦割り",
    # 「吹き出し」メニューの中にも並べるので、そこで
    # 「吹き出し > 吹き出し」と重ならない言い方にしてある
    TOOL_BALLOON: "楕円を追加",
    TOOL_BALLOON_JAGGED: "ギザギザを追加",
    TOOL_TEXT: "セリフを追加",
}

# クリックだけでセリフを置いたときの大きさ（mm）。
# 吹き出しの既定より一回り小さくして、中に収まるようにしてある
DEFAULT_TEXT_SIZE = (34.0, 18.0)

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
    def selected_text(self) -> TextObject | None:
        """選択中のセリフ。"""
        obj = self.selected_object
        return obj if isinstance(obj, TextObject) else None

    @property
    def selected_slant_pair(self) -> SlantPair | None:
        """選択中のコマが属する斜めの組。属していなければ None。"""
        panel = self.selected_panel
        return None if panel is None else self.page.slant_pair_of(panel.id)

    @property
    def selected_bounds(self) -> Rect | None:
        """選択枠とつまみを描く矩形。

        斜めの組に入っているコマは、**組の外側の矩形**を返す。組は必ず
        一緒に動くので、つまみも外側に付いていないと操作と結果が合わない。
        """
        obj = self.selected_object
        if isinstance(obj, Panel):
            pair = self.page.slant_pair_of(obj.id)
            return obj.shape.bounds() if pair is None else self.page.slant_bounds(pair)
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
        self._go_to_page(index)

    def _go_to_page(self, index: int) -> None:
        """表示するページを変える。**同じ番号でも必ず知らせる。**

        ページを消したり並べ替えたりすると、番号は同じでも中身が別の
        ページになる。`set_page_index` の「同じなら何もしない」を通すと、
        画面が前のページを描いたまま残る。
        """
        self._page_index = max(0, min(index, self.page_count - 1))
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

    # -- ページ ------------------------------------------------------------

    def add_page(self, index: int | None = None, size: Size | None = None) -> int:
        """ページを1枚足して、そこへ移る。足した位置を返す。

        `index` を渡さなければ**末尾**に足す。行き先が表示中のページに
        左右されないので、どこを見ていても結果が変わらない。途中へ
        差し込みたいときは `insert_page()`（要件定義 6.1）。
        """
        at = self.page_count if index is None else max(0, min(index, self.page_count))
        label = "ページの追加" if index is None else "ページの挿入"
        with self.edit(label) as project:
            project.add_page(index=at, size=size)
        self._go_to_page(at)
        return at

    def insert_page(self, size: Size | None = None) -> int:
        """表示中のページの**前**に1枚差し込んで、そこへ移る。

        差し込んだページが表示中のページの番号を引き継ぎ、それまでの
        ページは1つ後ろへ下がる。表計算の「行の挿入」と同じ向き。
        """
        return self.add_page(self._page_index, size)

    def delete_page(self, index: int | None = None) -> bool:
        """ページを1枚消す。消したら True。

        **最後の1ページは消さない。** ページが 0 枚になると表示するものが
        無くなり、コマも吹き出しも置き場所を失う。
        """
        at = self._page_index if index is None else index
        if not 0 <= at < self.page_count:
            return False
        if self.page_count <= 1:
            self.message.emit("最後の1ページは削除できません")
            return False

        page_id = self.project.pages[at].id
        with self.edit("ページの削除") as project:
            project.remove_page(page_id)
        # 消した位置に繰り上がってきたページを表示する。末尾を消したときだけ
        # 1つ前へ戻る（存在しない番号が残らないようにする）
        self._go_to_page(min(at, self.page_count - 1))
        return True

    def move_page(self, from_index: int, to_index: int) -> bool:
        """ページの並びを変える。動いたら True。

        **表示中のページは id で追いかける。** 並べ替えると番号がずれるので、
        番号のまま留まると、動かした直後に別のページが表示される。
        """
        count = self.page_count
        if not (0 <= from_index < count and 0 <= to_index < count):
            return False
        if from_index == to_index:
            return False

        showing = self.page.id
        with self.edit("ページの並べ替え") as project:
            project.move_page(from_index, to_index)
        moved = [p.id for p in self.project.pages].index(showing)
        self._go_to_page(moved)
        return True

    def set_page_size(self, size: Size, *, all_pages: bool = False) -> list[SceneObject]:
        """ページの大きさを変える。用紙からはみ出したものを返す。

        **次に追加するページも同じ大きさになる**（`default_page_size` を
        合わせる）。選んだ直後に足した1枚だけ前の大きさ、という食い違いを
        作らないため。

        はみ出したものは動かさない。位置は利用者が決めたもので、直し方も
        場面ごとに違う（要件定義 6.1）。数だけ知らせる。
        """
        label = "ページサイズの変更（全ページ）" if all_pages else "ページサイズの変更"
        with self.edit(label) as project:
            targets = project.pages if all_pages else [project.pages[self._page_index]]
            for page in targets:
                page.size = size
            project.default_page_size = size
        # 用紙の大きさが変わるとシーンの範囲も変わる
        self.page_changed.emit()

        # 全ページに適用したなら、全ページを見て数える。表示中のページだけ
        # 見ると、見えていないページのはみ出しを黙って通すことになる
        changed = self.project.pages if all_pages else [self.page]
        found: list[SceneObject] = []
        for page in changed:
            found.extend(outside_page(page))
        return found

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

    # -- 斜めのコマ --------------------------------------------------------

    def flip_slant(self) -> bool:
        """選択中の斜めの組の向きを反転する。反転できたら True。

        利用者がつつけるのはここだけ。位置と角度は分割したときに決まり、
        あとは外側の矩形にぶら下がって動く。
        """
        panel = self.selected_panel
        if panel is None or self.page.slant_pair_of(panel.id) is None:
            self.message.emit("斜めに割ったコマを選んでください")
            return False

        panel_id = panel.id
        with self.edit("斜めの向きを反転") as project:
            page = project.pages[self._page_index]
            flip_slant_pair(page, page.slant_pair_of(panel_id), self.settings)
        return True

    def slide_slant(self, panel_id: str, ratio: float) -> None:
        """斜めの境界を左右にずらす。

        1回のドラッグで1手。ドラッグ中は画面側が下見を描くだけで、
        ここへは離した時点で1度だけ来る（しっぽの付け根と同じ流儀）。
        """
        with self.edit("斜めの境界を移動") as project:
            page = project.pages[self._page_index]
            pair = page.slant_pair_of(panel_id)
            if pair is not None:
                slide_slant_pair(page, pair, ratio, self.settings)

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

    # -- セリフ ------------------------------------------------------------

    def add_text(self, rect: Rect, content: str = "") -> TextObject:
        """セリフを1つ置く。置いたものを選択状態にする。

        **吹き出しの上なら、その吹き出しに紐づける**（要件定義 6.5）。
        吹き出しはコマの中で単独に動かせるので、コマだけに紐づけると
        吹き出しを動かしたときにセリフが取り残される。
        """
        page = self.page
        balloon = balloon_at(page, *rect.center)
        panel_id = None if balloon is not None else attach_target(page, rect)

        with self.edit("セリフの追加") as project:
            text = project.add_text(
                project.pages[self._page_index], content, rect, panel_id
            )
            text.attached_balloon_id = balloon.id if balloon is not None else None
        self.select(text.id)
        return text

    def _edit_text(self, text_id: str, label: str):
        @contextlib.contextmanager
        def scope():
            with self.edit(label) as project:
                target = project.pages[self._page_index].find(text_id)
                if not isinstance(target, TextObject):
                    raise KeyError(f"セリフが見つかりません: {text_id}")
                yield target

        return scope()

    def set_text_content(self, text_id: str, content: str) -> None:
        with self._edit_text(text_id, "セリフの入力") as text:
            text.content = content

    def set_text_align(self, text_id: str, align: str) -> None:
        with self._edit_text(text_id, "セリフの整列") as text:
            text.align = align

    def set_text_font(
        self,
        text_id: str,
        *,
        family: str | None = None,
        size_mm: float | None = None,
        bold: bool | None = None,
    ) -> None:
        with self._edit_text(text_id, "セリフの書式") as text:
            text.font = dataclasses.replace(
                text.font,
                **{
                    key: value
                    for key, value in (
                        ("family", family),
                        ("size_mm", size_mm),
                        ("bold", bold),
                    )
                    if value is not None
                },
            )

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

    def set_tail_root(self, balloon_id: str, root_y: float | None) -> None:
        """しっぽの付け根の縦位置。None で先端の向きに合わせる（自動）。"""
        if root_y is not None:
            root_y = min(max(root_y, -1.0), 1.0)
        with self._edit_balloon(balloon_id, "しっぽの付け根") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, root_y=root_y)

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
