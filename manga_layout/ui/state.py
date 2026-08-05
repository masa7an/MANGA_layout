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
from ..focus import (
    DEFAULT_FOCUS_SETTINGS,
    default_focus,
    new_seed,
    stepped_count,
    stepped_width,
)
from ..geometry import Rect, Size
from ..history import History
from ..images import ImageCache, Preview, preview_from_bytes
from ..layout import (
    BalloonSettings,
    LayoutSettings,
    attach_target,
    balloon_at,
    contain_rect_in,
    default_sticker_rect,
    default_tail_tip,
    outside_page,
    tail_tip_turned_to,
)
from ..slant import flip_slant_pair, slide_slant_pair
from ..model import (
    BALLOON_STYLES_WITHOUT_TAIL,
    NOTE_COLORS,
    BalloonObject,
    FocusLines,
    ImageObject,
    Page,
    PageNote,
    Panel,
    Project,
    SceneObject,
    SlantPair,
    StickerObject,
    Tail,
    TextObject,
    new_project,
)
from ..stickers import STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION, read_sticker
from ..storage import load_project, save_project, write_autosave

# 道具（ツール）
TOOL_SELECT = "select"
TOOL_PANEL = "panel"
TOOL_SPLIT_H = "split_h"
TOOL_SPLIT_V = "split_v"
TOOL_SPLIT_SLANT = "split_slant"
TOOL_BALLOON = "balloon"
TOOL_BALLOON_JAGGED = "balloon_jagged"
TOOL_BALLOON_WAVY = "balloon_wavy"
TOOL_BALLOON_RECT = "balloon_rect"
TOOL_TEXT = "text"
TOOL_STICKER_EXCLAIM = "sticker_exclaim"
TOOL_STICKER_EXCLAIM_QUESTION = "sticker_exclaim_question"

# どの道具がどの種類の吹き出しを作るか
BALLOON_TOOLS = {
    TOOL_BALLOON: "ellipse",
    TOOL_BALLOON_JAGGED: "jagged",
    TOOL_BALLOON_WAVY: "wavy",
    TOOL_BALLOON_RECT: "rect",
}

# どの道具がどの種類のマークを置くか（要件定義 6.14）
STICKER_TOOLS = {
    TOOL_STICKER_EXCLAIM: STICKER_EXCLAIM,
    TOOL_STICKER_EXCLAIM_QUESTION: STICKER_EXCLAIM_QUESTION,
}

# マークの種類の呼び名。**フキダシと同じ形**（→ `BALLOON_STYLE_LABELS`）。
# 左側は保存形式に書かれる `kind` なので変えない。
#
# **表に無い値も来る。** `kind` は選択肢を固定せずに読むので（→ 5章）、
# 素材が増えたあとの作品を古いアプリで開くと知らない値が入る。
# 引くときは必ず既定を添える（`STICKER_KIND_LABELS.get(kind, "マーク")`）
STICKER_KIND_LABELS = {
    STICKER_EXCLAIM: "ビックリマーク",
    STICKER_EXCLAIM_QUESTION: "ビックリはてなマーク",
}

# 付箋の色の呼び名（要件定義 6.18）。**色に意味は割り当てない。** 見た目の
# 呼び名だけをここに置く（同じ形 → `STICKER_KIND_LABELS`）
NOTE_COLOR_LABELS = {
    "yellow": "黄",
    "pink": "桃",
    "blue": "青",
}

# 吹き出しの種類の呼び名。**道具・メニュー・状態表示・操作後の案内で共通に使う。**
#
# 左側の `ellipse` などは**保存形式に書かれる値なので変えない**。呼び名だけを
# ここで変える。値のほうを変えると、それまでに保存した作品が開けなくなる。
#
# 呼び名を1箇所に集めてあるのは、書き分けると片方だけ古いままになり、
# 「ふわふわ_フキダシにした」のに「丸い_フキダシを選択中」と出る
# 食い違いが作れるため（2026-08-04 の改名では、ここ1箇所で全部が変わった）
BALLOON_STYLE_LABELS = {
    "ellipse": "丸い_フキダシ",
    "jagged": "ギザギザ_フキダシ",
    "wavy": "ふわふわ_フキダシ",
    "rect": "四角_フキダシ",
}

TOOL_LABELS = {
    TOOL_SELECT: "選択",
    TOOL_PANEL: "コマ追加",
    TOOL_SPLIT_H: "横に分割",
    TOOL_SPLIT_V: "縦に分割",
    TOOL_SPLIT_SLANT: "斜めに縦割り",
    # 吹き出しの3種は呼び名から作る。ここに書き写すと、改名したときに
    # 道具の名前だけ古いまま残る
    **{
        tool: f"{BALLOON_STYLE_LABELS[style]}を追加"
        for tool, style in BALLOON_TOOLS.items()
    },
    # マークも同じ理由で呼び名から作る
    **{
        tool: f"{STICKER_KIND_LABELS[kind]}を追加"
        for tool, kind in STICKER_TOOLS.items()
    },
    TOOL_TEXT: "セリフを追加",
}

# クリックだけでセリフを置いたときの大きさ（px）。
# 吹き出しの既定より一回り小さくして、中に収まるようにしてある。
#
# **縦長にする。** 日本語のマンガは縦書きなので、セリフは少ない列数に
# 長く連なる。横長だと列が余って下だけが詰まり、置くたびに縦へ
# 伸ばし直すことになる。
#
# **文字の大きさと一緒に決まる。** 縦書きで 4 列 × 10 文字＝40 文字が
# 収まる寸法にしてある（`DEFAULT_FONT_SIZE_PX` が 42px のとき）。
# 文字だけ大きくすると枠に 6 文字しか入らず、置くたびに広げる操作が
# 要る状態になった（2026-08-03、20px から 42px へ変えたときに実測）
DEFAULT_TEXT_SIZE = (230.0, 422.0)


def object_label(obj: SceneObject | None) -> str:
    """選んでいるものの呼び名。何も無ければ空文字。

    **削除（→ 6.12）と複製（→ 6.15）で同じものを使う。** 書き分けると、
    「コマを複製」と出ているのに「画像の複製」で履歴に積まれる、といった
    食い違いを作れてしまう（削除で「項目名と実際に消えるものを1か所で
    決める」ことにしたのと同じ線引き）。

    マークだけは種類ごとに呼び名が違うので `STICKER_KIND_LABELS` を引く。
    **表に無い `kind` も来る**ので既定を添える（→ 5章）。
    """
    if isinstance(obj, ImageObject):
        return "画像"
    if isinstance(obj, TextObject):
        return "セリフ"
    if isinstance(obj, StickerObject):
        return STICKER_KIND_LABELS.get(obj.kind, "マーク")
    if isinstance(obj, BalloonObject):
        return "フキダシ"
    if isinstance(obj, Panel):
        return "コマ"
    return ""


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
        self.focus_settings = DEFAULT_FOCUS_SETTINGS
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
    def selected_sticker(self) -> StickerObject | None:
        """選択中のマーク。"""
        obj = self.selected_object
        return obj if isinstance(obj, StickerObject) else None

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

    def is_panel_locked(self, panel_id: str) -> bool:
        """そのコマがロックされていて動かせないか。

        斜めの組は片方でも動かせない（→ 要件定義 6.17）。組に属して
        いなければ、そのコマ自身の `locked` だけを見る。
        """
        panel = self.page.panel(panel_id)
        if panel.locked:
            return True
        pair = self.page.slant_pair_of(panel_id)
        if pair is None:
            return False
        other_id = pair.right_id if pair.left_id == panel_id else pair.left_id
        return self.page.panel(other_id).locked

    @property
    def is_locked_selection(self) -> bool:
        """選択中のコマが動かせない状態か。コマ以外を選んでいれば False。"""
        panel = self.selected_panel
        return False if panel is None else self.is_panel_locked(panel.id)

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
        if isinstance(obj, (ImageObject, BalloonObject, StickerObject, TextObject)):
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

    # -- 複製（要件定義 6.15） -----------------------------------------------

    def duplicate_selected(self) -> SceneObject | None:
        """選んでいるものを1つ写して、右下へずらして置く。写しを返す。

        **ずらす量は隙間1つ分**（`LayoutSettings.gutter`）。真上に重ねると、
        写せたのかどうかが見て分からない。

        **写したほうを選択状態にする**（他の「追加」と同じ）。続けて押すと
        階段状に増えるので、2回押して同じ場所に2枚重なることがない。

        写せなかったときは理由を状態表示に出して None を返す。**斜めに割った
        コマは押せる状態のまま断る**（→ 6.15）。グレーにすると理由を伝える
        先が無くなり、なぜ写せないのか分からないままになる。
        """
        obj = self.selected_object
        if obj is None:
            self.message.emit("複製するものを選んでください")
            return None
        if isinstance(obj, Panel) and self.page.slant_pair_of(obj.id) is not None:
            self.message.emit("斜めに割ったコマは複製できません")
            return None

        object_id = obj.id
        label = object_label(obj)
        offset = self.settings.gutter
        with self.edit(f"{label}の複製") as project:
            copy = project.duplicate(
                project.pages[self._page_index], object_id, offset, offset
            )
        self.select(copy.id)
        self.message.emit(f"{label}を複製しました")
        return copy

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

    # -- 付箋（要件定義 6.18） -----------------------------------------------
    #
    # 一覧の右クリックから呼ばれるので、**表示中のページとは限らない**。
    # `page_id` を毎回受け取り、`self._page_index` には触れない。

    def set_page_note_color(self, page_id: str, color: str) -> None:
        """付箋の色を変える（無ければ新しく貼る）。メモは変えずに残す。"""
        assert color in NOTE_COLORS
        with self.edit("付箋の色を変更") as project:
            page = project.page(page_id)
            text = page.note.text if page.note is not None else None
            page.note = PageNote(color=color, text=text)

    def set_page_note_text(self, page_id: str, text: str) -> None:
        """付箋のメモを書き換える。付箋が無いページでは何もしない。"""
        with self.edit("付箋のメモを変更") as project:
            page = project.page(page_id)
            if page.note is None:
                return
            page.note = PageNote(color=page.note.color, text=text or None)

    def remove_page_note(self, page_id: str) -> None:
        """付箋をはがす。"""
        with self.edit("付箋をはがす") as project:
            page = project.page(page_id)
            page.note = None

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

    def replace_image(self, image_id: str, data: bytes) -> ImageObject | None:
        """コマの中の画像を、別のファイルの絵に入れ替える。無い画像なら None。

        **重なり順（z）は元の画像から引き継ぐ。** 背景の上にキャラを重ねる
        使い方があり、ただ置き直す形（末尾に足す）にすると、背景を差し替え
        ただけで**キャラの手前へ出てしまう**。

        大きさは置いたときと同じ「コマに収まる」から始める（→ `place_image`）。
        元の矩形をそのまま使い回すと、縦横比の違う絵で歪む。

        取り込みが先で削除が後。壊れたファイルを選んだときは `import_bytes`
        が例外を出し、**元の画像が消えないまま**呼び出し側へ戻る。
        """
        panel = self.page.panel_of_image(image_id)
        if panel is None:
            return None
        panel_id = panel.id
        old_z = next(c for c in panel.children if c.id == image_id).z

        ref, px = self.import_bytes(data)
        rect = contain_rect_in(panel.shape.bounds(), px)

        with self.edit("画像の差し替え") as project:
            target = project.pages[self._page_index].panel(panel_id)
            image = project.add_image(target, ref, rect, px)
            image.z = old_z
            target.children = [c for c in target.children if c.id != image_id]
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

    # -- コマのロック --------------------------------------------------------
    #
    # 完成したコマを誤って動かさないためのもの（→ 要件定義 6.17）。
    # 止めるのは移動・大きさ変更・分割・削除だけで、中の画像や紐づいた
    # 吹き出し・セリフ・集中線には触らない。

    def set_panel_locked(self, panel_id: str, locked: bool) -> None:
        """1枚をロック／解除する。斜めの組なら**両方**に効かせる。

        片方だけ解いても動かせないままでは意味が無い（→ `is_panel_locked`
        が「片方でもロックなら組ごと動かせない」で見ているのと対）。
        """
        label = "コマをロック" if locked else "コマのロックを解除"
        with self.edit(label) as project:
            page = project.pages[self._page_index]
            pair = page.slant_pair_of(panel_id)
            targets = pair.members() if pair is not None else (panel_id,)
            for target_id in targets:
                page.panel(target_id).locked = locked

    def toggle_panel_lock(self) -> bool:
        """選択中のコマのロックを切り替える。変わったら True。"""
        panel = self.selected_panel
        if panel is None:
            return False
        self.set_panel_locked(panel.id, not self.is_locked_selection)
        return True

    def lock_all_panels(self) -> bool:
        """このページのコマをすべてロックする。変わったら True。

        既に全部ロック済みなら**何もしない**。押しても変化の無い操作で
        Undo の一手を使わせない（→ `_step_focus` と同じ流儀）。
        """
        page = self.page
        if not page.panels or all(p.locked for p in page.panels):
            return False
        with self.edit("このページのコマをすべてロック") as project:
            for panel in project.pages[self._page_index].panels:
                panel.locked = True
        return True

    def unlock_all_panels(self) -> bool:
        """このページのコマのロックをすべて解除する。変わったら True。"""
        if not any(p.locked for p in self.page.panels):
            return False
        with self.edit("このページのコマのロックをすべて解除") as project:
            for panel in project.pages[self._page_index].panels:
                panel.locked = False
        return True

    # -- 集中線 ------------------------------------------------------------
    #
    # 独立したオブジェクトではなくコマの属性なので（→ 要件定義 6.16）、
    # 選択・削除・複製の経路には出てこない。触れるのはここにある操作だけ。

    @property
    def selected_focus(self) -> FocusLines | None:
        """選択中のコマに入っている集中線。無ければ None。"""
        panel = self.selected_panel
        return None if panel is None else panel.focus_lines

    def _edit_focus(self, panel_id: str, label: str):
        """id で引き直してから触るための小さな入れ物。

        Undo で `Project` の実体が差し替わるため、外で掴んだコマを
        そのまま書き換えてはいけない（`_edit_balloon` と同じ）。
        """

        @contextlib.contextmanager
        def scope():
            with self.edit(label) as project:
                target = project.pages[self._page_index].find(panel_id)
                if not isinstance(target, Panel) or target.focus_lines is None:
                    raise KeyError(f"集中線の入ったコマが見つかりません: {panel_id}")
                yield target

        return scope()

    def add_focus_lines(self) -> bool:
        """選択中のコマに集中線を入れる。入れたら True。

        **1コマに1つまで。** 既に入っているコマでは何もしない
        （メニュー側は「消す」に変わっているので、ここへは来ない）。
        """
        panel = self.selected_panel
        if panel is None:
            self.message.emit("集中線を入れるコマを選んでください")
            return False
        if panel.focus_lines is not None:
            return False

        panel_id = panel.id
        with self.edit("集中線を入れる") as project:
            target = project.pages[self._page_index].panel(panel_id)
            target.focus_lines = default_focus(self.focus_settings)
        self.message.emit(
            "集中線を入れました。十字のつまみで中心、四角のつまみで内側の空きを変えられます"
        )
        return True

    def remove_focus_lines(self) -> bool:
        """選択中のコマから集中線を消す。消したら True。"""
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self.edit("集中線を消す") as project:
            project.pages[self._page_index].panel(panel_id).focus_lines = None
        return True

    def set_focus_shape(
        self,
        panel_id: str,
        *,
        center: tuple[float, float] | None = None,
        hole: float | None = None,
    ) -> None:
        """中心・内側の空きを差し替える。

        1回のドラッグで1手。ドラッグ中は画面側が下見を描くだけで、ここへは
        離した時点で1度だけ来る（斜めの境界と同じ流儀 → 要件定義 6.10）。
        """
        label = "集中線の中心" if center is not None else "集中線の内側"
        with self._edit_focus(panel_id, label) as panel:
            if center is not None:
                panel.focus_lines.center = center
            if hole is not None:
                panel.focus_lines.hole = hole

    def step_focus_count(self, steps: int) -> bool:
        """線の本数を増減する。変わったら True。"""
        return self._step_focus(
            "count", stepped_count, steps, lambda n: f"集中線: {n} 本"
        )

    def step_focus_width(self, steps: int) -> bool:
        """線の太さを増減する。変わったら True。"""
        return self._step_focus(
            "width",
            stepped_width,
            steps,
            lambda w: f"集中線の太さ: {w * 100:.1f}%（コマの短辺に対する割合）",
        )

    def _step_focus(self, field: str, step, steps: int, label) -> bool:
        """本数と太さの増減は、値の名前と刻み方だけが違う。

        書き分けると、端で止まったときの扱い（履歴に積まない）が
        片方だけ抜ける。
        """
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False
        value = step(getattr(panel.focus_lines, field), steps)
        if value == getattr(panel.focus_lines, field):
            # 端まで来ている。**履歴に積まない。** 押しても何も変わらない
            # 操作で Undo の一手を使わせない
            return False

        panel_id = panel.id
        with self._edit_focus(panel_id, "集中線の調整") as target:
            setattr(target.focus_lines, field, value)
        self.message.emit(label(value))
        return True

    def reseed_focus(self) -> bool:
        """ばらつきだけを作り直す。中心・本数・太さは変えない。

        **1手として積む。** Undo で前の形に戻せないと、気に入っていた形を
        振り直しで失うことになる（→ 要件定義 6.16）。
        """
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self._edit_focus(panel_id, "集中線の形を振り直す") as target:
            target.focus_lines.seed = new_seed()
        return True

    def toggle_focus_color(self) -> bool:
        """線の色を黒⇄白で切り替える。**単純な色違い**（要件定義 6.19）。

        形（本数・太さ・空き・中心）には触らない。押すたびに必ず変わる
        操作なので、`_step_focus` の端で止まるガードは要らない。
        """
        panel = self.selected_panel
        if panel is None or panel.focus_lines is None:
            return False

        panel_id = panel.id
        with self._edit_focus(panel_id, "集中線の色") as target:
            target.focus_lines.white = not target.focus_lines.white
        return True

    # -- 吹き出し ----------------------------------------------------------

    def add_balloon(self, rect: Rect, style: str = "ellipse") -> BalloonObject:
        """吹き出しを1つ置く。置いたものを選択状態にする。

        重なっているコマに自動で紐づける（要件定義 6.4）。紐づけておくと、
        あとでコマを動かしたときに吹き出しが付いて回る。外したいときは
        あとから解除できる。

        **四角はしっぽを消した状態で置く**（`BALLOON_STYLES_WITHOUT_TAIL`）。
        ナレーション・地の文に使うもので、指す相手がいない。先端の位置は
        他の種類と同じに入れておく——あとで「しっぽを出す」を選んだときに、
        向きの決まらないしっぽが出ないようにするため。
        """
        page = self.page
        attached = attach_target(page, rect)
        tip = default_tail_tip(rect)

        with self.edit("フキダシの追加") as project:
            balloon = project.add_balloon(
                project.pages[self._page_index], rect, style, attached
            )
            balloon.tail = Tail(
                enabled=style not in BALLOON_STYLES_WITHOUT_TAIL,
                tip=tip,
                width=self.balloon_settings.tail_width,
            )
        self.select(balloon.id)
        return balloon

    # -- マーク ------------------------------------------------------------

    def add_sticker(
        self, kind: str, x: float, y: float, box: Rect | None = None
    ) -> StickerObject:
        """マークを1つ置く。置いたものを選択状態にする（要件定義 6.14）。

        `box` はドラッグで囲った範囲。渡されたら**その中に縦横比を保って
        収める**。渡さなければ (x, y) を中心に既定の大きさで置く。

        重なっているコマに自動で紐づける（フキダシと同じ → 6.4）。
        紐づけておくと、あとでコマを動かしたときに付いて回る。**切り抜かれ
        ない**のはページ直下に置いているため。

        素材は組み込みだが、**実体は既存の経路で `assets/` に入る**。
        こうしておくと作品が自己完結し、あとで素材を差し替えても既にある
        作品の見た目は変わらない。
        """
        ref, px = self.import_bytes(read_sticker(kind))
        page = self.page
        rect = (
            contain_rect_in(box, px)
            if box is not None and box.w > 0.0 and box.h > 0.0
            else default_sticker_rect(page, x, y, px)
        )
        attached = attach_target(page, rect)

        label = STICKER_KIND_LABELS.get(kind, "マーク")
        with self.edit(f"{label}の追加") as project:
            sticker = project.add_sticker(
                project.pages[self._page_index], kind, ref, rect, px, attached
            )
        self.select(sticker.id)
        return sticker

    def set_sticker_attachment(self, sticker_id: str, panel_id: str | None) -> None:
        """コマへの紐づけを付け替える。None で解除。"""
        label = "コマへの紐づけ" if panel_id else "紐づけの解除"
        with self.edit(label) as project:
            target = project.pages[self._page_index].find(sticker_id)
            if not isinstance(target, StickerObject):
                raise KeyError(f"マークが見つかりません: {sticker_id}")
            target.attached_panel_id = panel_id

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

    def set_text_direction(self, text_id: str, direction: str) -> None:
        """縦書き・横書きを切り替える。

        `align` は持ち替えない。横書きの左右の寄せが、縦書きでは上下の
        寄せとして読み替えられる（→ `manga_layout.vertical`）。別々に
        持つと、向きを往復したときにどちらの値を使うのか決められなくなる。
        """
        with self._edit_text(text_id, "セリフの向き") as text:
            text.direction = direction

    def set_text_font(
        self,
        text_id: str,
        *,
        family: str | None = None,
        size_px: float | None = None,
        bold: bool | None = None,
    ) -> None:
        with self._edit_text(text_id, "セリフの書式") as text:
            text.font = dataclasses.replace(
                text.font,
                **{
                    key: value
                    for key, value in (
                        ("family", family),
                        ("size_px", size_px),
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
        with self._edit_balloon(balloon_id, "フキダシの種類変更") as balloon:
            balloon.style = style

    def set_tail_tip(self, balloon_id: str, tip: tuple[float, float]) -> None:
        with self._edit_balloon(balloon_id, "しっぽの向き") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, tip=tip, enabled=True)

    def set_tail_root(self, balloon_id: str, root_y: float | None) -> None:
        """しっぽの付け根の縦位置。None で先端の向きに合わせる（自動）。

        **先端は動かさない。** ここは付け根を左右へ寄せる微調整で、
        しゃべっている相手を指したまま生え際だけを変えるためのもの。
        大きく向きを変えるのは `turn_tail`（→ 6.4）。
        """
        if root_y is not None:
            root_y = min(max(root_y, -1.0), 1.0)
        with self._edit_balloon(balloon_id, "しっぽの付け根") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, root_y=root_y)

    def turn_tail(self, balloon_id: str, direction: str) -> None:
        """しっぽを `direction`（→ `TAIL_DIRECTIONS`）へ**先端ごと**回す。

        付け根だけを動かすと、先端と反対側では本体に隠れて針になる。
        メニューから「上へ」を選ぶのは向きを変えたいときなので、
        先端も連れて回すほうが指示どおりの絵になる（→ 6.4）。

        回した先では付け根の高さと先端の向きが一致するので、`root_y` は
        自動（None）へ戻す。ここに値を残すと、あとで先端だけ動かした
        ときに古い指定が効いて、また針に痩せる。
        """
        with self._edit_balloon(balloon_id, "しっぽの向き") as balloon:
            tip = tail_tip_turned_to(balloon, direction)
            if tip is None:
                return  # 先端が中心に重なっている。いまの向きが決まらない
            balloon.tail = dataclasses.replace(balloon.tail, tip=tip, root_y=None)

    def set_tail_enabled(self, balloon_id: str, enabled: bool) -> None:
        label = "しっぽを出す" if enabled else "しっぽを消す"
        with self._edit_balloon(balloon_id, label) as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, enabled=enabled)

    def set_tail_shape(self, balloon_id: str, shape: str) -> None:
        """しっぽを三角にするか、丸い飛びしっぽにするか（→ 要件定義 10.1）。

        **先端も付け根も動かさない。** 形だけを差し替えるので、指している
        相手と生え際はそのまま残る。
        """
        with self._edit_balloon(balloon_id, "しっぽの形") as balloon:
            balloon.tail = dataclasses.replace(balloon.tail, shape=shape)

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

    def autosave(self) -> pathlib.Path | None:
        """作業中の内容を `backup/` へ退避する。書いたらそのパスを返す。

        タイマーから一定間隔で呼ばれる（要件定義 6.6）。**次の2つの場合は
        何もせず None を返す。**

        **保存先が決まっていない**（一度も保存していない作品）。退避先の
        フォルダが無いうえ、その状態で貼った画像は**まだディスクに無い**
        （→ `import_bytes` の `pending_assets`）ため、JSON だけ書いても
        参照先の無い退避になる。保存先が決まっていれば画像は貼った時点で
        `assets/` に入るので、この問題は起きない。

        **前回の退避から変化が無い。** 判定は保存形式そのものの比較なので
        （→ `History.is_autosave_pending`）、「変えて元に戻した」場合も
        正しく何もしない。

        **保存した扱いにはしない。** 本体（project.json）を書き換えていない
        以上、未保存の印は利用者が保存するまで残す。
        """
        if self.project_dir is None:
            return None
        if not self.history.is_autosave_pending:
            return None

        path = write_autosave(self.project, self.project_dir)
        self.history.mark_autosaved()
        return path

    @property
    def is_dirty(self) -> bool:
        return self.history.is_dirty
