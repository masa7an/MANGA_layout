"""編集中の状態。画面の各部品が共通で見る一箇所。

`History` がプロジェクトの唯一の持ち主なので、ここでも `Project` を
直接抱えない。Undo で実体が差し替わったときに、古い `Page` や `Panel` を
掴んだままの画面が出ないようにするため（要件定義 6.8）。
"""

from __future__ import annotations

import contextlib
import dataclasses
import pathlib
from collections.abc import Iterator

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .. import next_panel
from ..assets import AssetStore, PendingAssets
from ..errors import AssetError, MaskSizeError
from ..flow import (
    DEFAULT_FLOW_SETTINGS,
    default_flow,
    stepped_count as flow_stepped_count,
    stepped_length as flow_stepped_length,
    stepped_width as flow_stepped_width,
)
from ..focus import (
    DEFAULT_FOCUS_SETTINGS,
    default_focus,
    new_seed,
    stepped_count,
    stepped_width,
)
from ..geometry import Rect, Size, normalize_angle
from ..history import History
from ..image_masks import decode_mask, safe_masked_preview
from ..images import (
    BakedCache,
    ImageCache,
    Preview,
    bake_key,
    decode,
    preview_from_bytes,
    readable_file,
    rough_preview_from_bytes,
    size_px,
    to_png_bytes,
    toned,
)
from ..layout import (
    BalloonSettings,
    LayoutSettings,
    attach_target,
    balloon_at,
    contain_rect_in,
    default_sticker_rect,
    default_tail_tip,
    image_orphaned_at,
    outside_page,
    tail_tip_turned_to,
    text_frame,
)
from ..model import (
    BALLOON_STYLES_WITH_BUBBLE_TAIL,
    BALLOON_STYLES_WITHOUT_TAIL,
    DEFAULT_TEXT_DIRECTION,
    NOTE_COLORS,
    TAIL_SHAPE_BUBBLES,
    TAIL_SHAPE_TRIANGLE,
    BalloonObject,
    FlowLines,
    FocusLines,
    Font,
    ImageObject,
    Page,
    PageNote,
    PageRough,
    Panel,
    Project,
    SceneObject,
    SlantPair,
    StickerObject,
    Tail,
    TextObject,
    Tone,
    new_project,
)
from ..settings import ROUGH_OPACITY_DEFAULT
from ..slant import flip_slant_pair, set_slant_pair_rect, slide_slant_pair
from ..stickers import STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION, read_sticker
from ..storage import (
    BackupEntry,
    list_backups,
    load_backup,
    load_project,
    save_project,
    write_autosave,
)
from ..tone import (
    ANGLE_STEP as TONE_ANGLE_STEP,
    KIND_LABELS as TONE_KIND_LABELS,
    default_tone,
    level_label as tone_level_label,
    stepped_density as tone_stepped_density,
    stepped_pitch as tone_stepped_pitch,
    stepped_thin as tone_stepped_thin,
    stepped_threshold as tone_stepped_threshold,
)
from ..wand import (
    DEFAULT_TOLERANCE as WAND_TOLERANCE_DEFAULT,
    intersected,
    removed,
    select_at,
)

# 次のコマの提案（→ 要件定義 10.5）。**履歴のまとめ鍵と同じ文字列を使う。**
# 「直前の1手が提案か」を `history.undo_label` で見るので、名前がずれると差し替えが
# 効かなくなる（増えていくだけになる）
SUGGEST_LABEL = "次のコマを提案"

# 道具（ツール）
TOOL_SELECT = "select"
TOOL_PANEL = "panel"
TOOL_SPLIT_H = "split_h"
TOOL_SPLIT_V = "split_v"
TOOL_SPLIT_SLANT = "split_slant"
TOOL_BALLOON = "balloon"
TOOL_BALLOON_JAGGED = "balloon_jagged"
TOOL_BALLOON_SPIKY = "balloon_spiky"
TOOL_BALLOON_WAVY = "balloon_wavy"
TOOL_BALLOON_CLOUD = "balloon_cloud"
TOOL_BALLOON_RECT = "balloon_rect"
TOOL_TEXT = "text"
TOOL_STICKER_EXCLAIM = "sticker_exclaim"
TOOL_STICKER_EXCLAIM_QUESTION = "sticker_exclaim_question"
# ラフ（下敷き）の位置と大きさを直す道具（→ 要件定義 6.23）。
#
# **道具にしたのは、ラフが一番下にあるため。** コマの下に潜っているものを
# 普段の選択で掴めるようにすると、「コマを選んだつもりでラフが動く」経路が
# できる。持ち替えている間だけ掴める形なら、その取り違えが起こらない
TOOL_ROUGH = "rough"
# トーンを掛ける範囲（矩形）を直す道具（→ 要件定義 10.1）。
#
# **道具にしたのは、つまみの記号が尽きているため。** 四角＝大きさ・丸＝回転・
# ひし形＝しっぽの付け根・十字＝集中線の中心で在庫が無く、矩形の4隅にもう
# 1種類足すと画像を選んだだけでつまみが9個並ぶ。持ち替えている間だけ出す形に
# すれば、その間は他のつまみを全部消せる（ラフと同じ切り分け）
TOOL_TONE_AREA = "tone_area"
# 絵の一部を消す道具（自動領域選択 → 要件定義 10.3）。
#
# **道具にしたのは、普段のクリックと意味が正面から衝突するため。** 選択の道具で
# 絵を押せば「その絵を選ぶ」で、ここでは「押した所を消す」。同じ操作に2つの意味を
# 持たせず、持ち替えている間だけ消える形にする（ラフ・トーン範囲と同じ切り分け）
TOOL_WAND = "wand"

# **既にあるものを調整するだけの道具。** 何も作らず、持っている間は選び直せない
# （→ 要件定義 6.23、6.27）。この3つだけ、**もう一度選ぶと選択の道具へ戻る**
# （→ `MainWindow._pick_tool`）。作る側の道具（コマ・フキダシ・マーク）は
# 押すたびに1つ作るので、同じ扱いにすると2回目が「作る」ではなく「やめる」に化ける
ADJUST_TOOLS = (TOOL_ROUGH, TOOL_TONE_AREA, TOOL_WAND)

# どの道具がどの種類の吹き出しを作るか
BALLOON_TOOLS = {
    TOOL_BALLOON: "ellipse",
    TOOL_BALLOON_CLOUD: "cloud",
    TOOL_BALLOON_SPIKY: "spiky",
    TOOL_BALLOON_WAVY: "wavy",
    TOOL_BALLOON_JAGGED: "jagged",
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
# **並び順は使う頻度で決める**（本人の指示 2026-08-07）。道具箱・道具メニュー・
# フキダシメニュー・種類を変えるメニューがすべてこの順で並ぶので、よく使うものが
# 先頭に来ていないと、毎回よく使う項目まで目で降りることになる。
#
# ギザギザとトゲトゲは同じ叫びの直線版・曲線版で、**作風を決めたあとは片方しか
# 使わない**（→ 6.32）。使うほう（トゲトゲ）を前に残し、使わないほう（ギザギザ）は
# 後ろへ下げてある。「見比べられるよう隣に並べる」は選ぶときだけの都合で、
# 選び終わったあとは毎日の頻度のほうが効く
BALLOON_STYLE_LABELS = {
    "ellipse": "丸い_フキダシ",
    "cloud": "雲_フキダシ",
    "spiky": "トゲトゲ_フキダシ",
    "wavy": "ふわふわ_フキダシ",
    "jagged": "ギザギザ_フキダシ",
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
    TOOL_ROUGH: "ラフを調整",
    TOOL_TONE_AREA: "トーン範囲を調整",
    # **短く置く。** 何が起きるかは、持っている間ずっと状態表示の右側に出る
    # （→ `MainWindow._hint`）。項目名で説明しようとすると、道具の並びで
    # ここだけ長くなって読む字数が増える
    TOOL_WAND: "切り抜き",
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

# 切り抜き（自動領域選択）の許容差で動かせる範囲と、1回ぶん。
#
# **上限を 64 で止める。** 実測では、陰影のある絵を 64 で押すと画面の半分が
# 選ばれた（→ `data/` の検討メモ）。そこから先は「区画を選ぶ」ではなくなる
WAND_TOLERANCE_MIN = 0
WAND_TOLERANCE_MAX = 64
WAND_TOLERANCE_STEP = 4


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


def _font_changes(
    family: str | None, size_px: float | None, bold: bool | None
) -> dict[str, object]:
    """指定された項目だけを集める（`dataclasses.replace` へ渡す形）。

    セリフの書式と「次に作るセリフの書式」の両方が同じ形で受け取るので、
    組み立ては1か所にしてある（→ `EditorState.set_text_font`）。
    """
    return {
        key: value
        for key, value in (("family", family), ("size_px", size_px), ("bold", bold))
        if value is not None
    }


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
    # 点検の印が変わった（→ 要件定義 10.1）。**`changed` とは別にする。**
    # あちらは「モデルが変わった＝描き直しが要る」の合図で、点検の印は
    # 作品を1文字も変えないので、混ぜると印を付けただけで未保存になる
    check_changed = Signal()

    def __init__(self, project: Project | None = None, project_dir: pathlib.Path | None = None):
        super().__init__()
        self.history = History(project if project is not None else new_project())
        self.project_dir = project_dir
        self.settings = LayoutSettings()
        self.balloon_settings = BalloonSettings()
        self.focus_settings = DEFAULT_FOCUS_SETTINGS
        self.flow_settings = DEFAULT_FLOW_SETTINGS
        self._page_index = 0
        # 直前の提案で置いたコマの id と、その案の番号（→ `suggest_next_panel`）
        self._suggested: tuple[str, ...] = ()
        self._suggest_index = 0
        self._tool = TOOL_SELECT
        self._selected_id: str | None = None
        # 保存先が決まる前に貼り付けた画像の預かり所。保存時に書き出す
        self.pending_assets = PendingAssets()
        self.image_cache = ImageCache()
        # ラフ用の入れ物（→ 6.23）。**画像と混ぜない。** `ImageCache` は
        # 「同じ入れ物に別の作り方のものを入れてはいけない」決まりで動いて
        # おり、青く染めた1枚を混ぜると、引く側がどちらか判断できなくなる
        self.rough_cache = ImageCache(make=rough_preview_from_bytes)
        # トーン（→ 10.1）と切り抜き（→ 10.3）を焼いた1枚の入れ物。
        # こちらも画像と混ぜない。鍵に設定が入るぶん増え続けるので、
        # あちらと違って上限を持つ
        self.baked_cache = BakedCache()
        # 切り抜きの許容差（→ 10.3）。**作品ではなく操作の設定**なので
        # `project.json` には入れない（道具の選択と同じ扱い）
        self.wand_tolerance = WAND_TOLERANCE_DEFAULT
        # ラフの濃さ（→ `settings.rough_opacity`）。作品ではなく好みなので
        # `project.json` ではなく `settings.json` から来る。窓が起動時と
        # ラフを読み込む直前に入れ直す（→ `MainWindow.load_rough`）
        self.rough_opacity = ROUGH_OPACITY_DEFAULT
        # 次に作るセリフの書式（→ 要件定義 6.5）。**書式を指定した操作が
        # ここへ写り、以後の「セリフの追加」がこれを使う。** 選んだ書式が
        # 選択中の1つにしか効かず、次に置いたセリフが毎回既定へ戻るのが
        # 分かりにくかった（本人の指摘 2026-08-07）。
        #
        # **作品ではなく好みなので `project.json` には載せない。** 別の
        # 作品を開いても持ち越す（`reset` で戻さない）のは `rough_opacity`
        # と同じ扱い。アプリを閉じると既定へ戻る
        self.next_text_font = Font()
        # 次に作るセリフの向き（→ 要件定義 6.5、6.11）。**書式と同じ扱いで
        # 引き継ぐ。** 既定は縦書きだが、横書きの箇条書きを作っている最中は
        # 1つ置くたびに縦書きへ戻り、そのつど F7 を押し直すことになる
        # （本人の指摘 2026-08-07）。書式だけ引き継いで向きが戻るのは、
        # 利用者から見れば「勝手に縦にされる」としか映らない
        self.next_text_direction = DEFAULT_TEXT_DIRECTION
        # 点検で見つかったページの id（→ 要件定義 10.1）。**保存形式には
        # 載せない。** 作品ではなく「いま押した結果」なので、`Page` に
        # 持たせると Undo・サムネイルの指紋・project.json の全部に絡む
        self._check_marks: set[str] = set()

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

        セリフは**枠ではなく字の並びの外接矩形**を返す（→ `layout.text_frame`）。
        セリフだけは掴める範囲が枠と別で、枠を返すと押しても掴めない場所まで
        つまみが伸びる。移動・大きさ変更もここを起点にするので、**見えている
        枠を掴んで動かす**という関係が全部の操作で揃う。
        """
        obj = self.selected_object
        if isinstance(obj, Panel):
            pair = self.page.slant_pair_of(obj.id)
            return obj.shape.bounds() if pair is None else self.page.slant_bounds(pair)
        if isinstance(obj, TextObject):
            return text_frame(obj)
        if isinstance(obj, (ImageObject, BalloonObject, StickerObject)):
            return obj.rect
        return None

    # -- 操作 --------------------------------------------------------------

    def set_tool(self, tool: str) -> None:
        if tool == self._tool:
            return
        self._tool = tool
        # 連打でまとめている1手をここで区切る（→ `_step_tone`）。
        # 区切らないと、道具を持ち替えて戻ってきたあとの連打が、前の連打と
        # 同じ1手に吸い込まれる
        self.history.break_merge()
        self.tool_changed.emit()

    def select(self, panel_id: str | None) -> None:
        if panel_id == self._selected_id:
            return
        self._selected_id = panel_id
        # 連打でまとめている1手をここで区切る（→ `_step_tone`）。道具を
        # `TOOL_TONE_AREA` から持ち替えていない場合でも、選び直した時点で
        # 次の連打は別の手として積む（2026-08-08 発見。以前は道具を
        # 持ち替えたときにしか区切られず、選び直しただけの連打が
        # 前の画像の調整へ吸い込まれていた）
        self.history.break_merge()
        # トーンの範囲を直す道具は、その画像を選んでいる間だけのもの。
        # 別のものへ移った時点で外す（→ `_leave_tone_tool_if_gone`）
        self._leave_tone_tool_if_gone()
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
        # ページを跨いだら連打のまとめ扱いを打ち切る（→ `select` と同じ理由。
        # 2026-08-08 発見）。跨がないと、別ページへ移って戻ってきた同じ
        # 画像への調整が、移る前の連打と1手にまとまってしまう
        self.history.break_merge()
        self._leave_rough_tool_if_gone()
        self.page_changed.emit()
        self.selection_changed.emit()
        self.changed.emit()

    def _leave_rough_tool_if_gone(self) -> None:
        """ラフの無いページへ移ったら、調整の道具から選択へ戻す（→ 6.23）。

        掴めるものが1つも無い道具を持ったまま残ると、押しても何も起きず、
        しかもメニューの項目まで押せなくなって戻る手段が分かりにくくなる。
        ページを移ったときと、Undo でラフが消えたときの両方から呼ぶ。
        """
        if self._tool == TOOL_ROUGH and self.page.rough is None:
            self.set_tool(TOOL_SELECT)

    def _leave_tone_tool_if_gone(self) -> None:
        """トーンの入った画像を選んでいなければ、調整の道具から選択へ戻す。

        理由はラフと同じ（→ `_leave_rough_tool_if_gone`）。掴めるものが
        1つも無い道具を持ったまま残ると、押しても何も起きない。
        **選択が変わったときと、Undo でトーンが消えたときの両方から呼ぶ。**
        """
        if self._tool == TOOL_TONE_AREA and self.selected_tone is None:
            self.set_tool(TOOL_SELECT)

    # -- 編集 --------------------------------------------------------------

    @contextlib.contextmanager
    def edit(self, label: str, *, merge_key: str | None = None) -> Iterator[Project]:
        """1手ぶんの編集。抜けたところで履歴に積み、画面を描き直す。"""
        with self.history.edit(label, merge_key=merge_key) as project:
            yield project
        self.changed.emit()

    @contextlib.contextmanager
    def edit_page(self, label: str, *, merge_key: str | None = None) -> Iterator[Page]:
        """1手ぶんの編集。表示中のページを直接返す。

        `edit()` に続けて `project.pages[self._page_index]` と書く形が
        呼び出し側の大半を占めていたための省略形（型は違うが `page`
        プロパティと対になる書き方）。

        **`Project` そのものが要る操作には使えない。** `project.add_balloon`
        のように `Project` のメソッドを呼ぶ側は、今まで通り `edit()` を使うこと
        """
        with self.edit(label, merge_key=merge_key) as project:
            yield project.pages[self._page_index]

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

    def discard_last_edit(self, label: str) -> bool:
        """直前の1手を、やり直せる形を残さず取り消す（→ `History.discard_last`）。

        取り消した1手で作られたものは消えるので、**選択も外す**。id は
        残っていても引き当たらないだけだが、状態表示が「セリフを選択中」の
        まま残る（`selected_text` が None を返すので中身は空）。
        """
        if not self.history.discard_last(label):
            return False
        self.select(None)
        self.changed.emit()
        return True

    def _after_history_move(self, message: str) -> None:
        # ページが減っていた場合に備えて番号を丸める
        self._page_index = max(0, min(self._page_index, self.page_count - 1))
        # ラフやトーンが消えていることがある（→ `_leave_rough_tool_if_gone`）
        self._leave_rough_tool_if_gone()
        self._leave_tone_tool_if_gone()
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

        **画像は、写しがコマの外へ完全に出るときも同じく断る。** コマの縁に
        寄せた画像をずらす量ぶん複製すると、写しがコマと1pxも重ならず、
        二度と選べない「見えない孤児」になることがある（→ `layout.
        image_orphaned_at`。2026-08-08 に発見。移動側の同じ穴と対）。
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

        if isinstance(obj, ImageObject):
            panel = self.page.panel_of_image(obj.id)
            moved = obj.rect.translated(offset, offset)
            if panel is not None and image_orphaned_at(panel, moved, obj.rotation):
                self.message.emit(
                    "コマの外まで出て選べなくなるので、この画像は複製できません"
                )
                return None
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

    def has_asset(self, ref: str) -> bool:
        """その画像が使えるか（実体があり、画像として開ける形か）。

        **展開はしない**（→ `preview` との違い）。全ページ分を巡回する
        処理から呼ぶので、まだ画面に出しておらずキャッシュに乗っていない
        画像があると、`preview()` 経由では展開待ちで固まる（2026-08-08 発見）。
        ヘッダーだけを見る `images.readable_file` で足りる。

        **「無い」と「開けない」を分けない。** 書き出した結果はどちらも
        同じ——そこが白く抜ける——で、利用者に伝えたいこともそこだから。
        分けていた頃は、点検（`check.inspect_project`）が実体の有無だけを
        見て「問題なし」と答える一方、書き出し前の警告
        （`ui.export.missing_assets_in`）は展開できるかを見ていたため、
        **壊れた画像1枚で2つの機能が違う答えを返していた**
        （2026-08-09 に発見。両方をここへ集約した）。
        """
        if ref in self.pending_assets:
            # 預かり分は取り込み時に `images.decode` を通っている（→ 5章）。
            # 開けることが確認済みなので、読み直さない
            return True
        if self.project_dir is None:
            return False
        store = AssetStore(self.project_dir)
        try:
            path = store.resolve(ref)
        except AssetError:
            # 参照の形自体が壊れている（`assets/` の外を指すなど）
            return False
        return path.is_file() and readable_file(path)

    def preview(self, ref: str) -> Preview | None:
        """画面に描くための1枚。無い・壊れているときは None。

        **トーンは掛かっていない。** ラフのように「画像そのもの」が要る
        経路が使う。コマの中の画像を描くときは `image_preview` を通す。
        """
        return self.image_cache.get(ref, lambda: self.read_asset(ref))

    def image_preview(self, image) -> Preview | None:
        """画面に描くための1枚。**トーンと切り抜きが入っていれば焼いたほうを返す。**

        受け取るのが参照文字列ではなく画像そのものなのは、同じ `asset` でも
        トーンの設定や切り抜き次第で別の絵になるため（→ 要件定義 10.1・10.3）。
        マーク（`StickerObject`）もここを通るが、あちらはどちらも持たない。

        **切り抜きのある画像だけ、原寸から作り直す。** マスクは元画像の
        ピクセル座標に結び付いているので、`image_cache` が持っている縮小版へは
        掛けられない（→ `image_masks.masked_preview`）。無い画像は今までどおり
        縮小版から焼くので、この機能を使っていない作品では何も変わらない。
        """
        tone = getattr(image, "tone", None)
        mask_ref = getattr(image, "mask_asset", "")
        if tone is None and not mask_ref:
            return self.preview(image.asset)
        key = bake_key(image)
        if not mask_ref:
            return self.baked_cache.get(
                key, lambda: toned(self.preview(image.asset), tone)
            )
        return self.baked_cache.get(key, lambda: self._masked_preview(image, tone))

    def _masked_preview(self, image, tone) -> Preview | None:
        """切り抜きを焼いた画面用の1枚。実体が欠けていれば None。

        **マスクだけが欠けているときは、切り抜き無しの絵を返す**（→ 計画 段階1〜3）。
        開けなくする・何も描かない、はしない——欠けた絵1枚で作品が読めなく
        なるのは割に合わない、という `ImageCache` の考え方と揃えてある。
        マスクの欠けは点検（`check.KIND_MISSING_MASK`）が拾う。
        """
        baked = safe_masked_preview(
            self.read_asset(image.asset),
            self.read_asset(image.mask_asset),
            tone,
            reduced=True,
        )
        if baked is not None:
            return baked
        plain = self.preview(image.asset)
        return plain if tone is None else toned(plain, tone)

    def forget_if_unused(self, ref: str) -> None:
        """その参照が作品のどこからも使われていなければ、覚えを手放す。

        削除・差し替えで使われなくなった絵の縮小版・トーン焼き版が
        キャッシュに残り続けていた（`ImageCache.forget` /
        `ToneCache.forget` 自体はあったが、呼ぶ場所が無かった。
        2026-08-08 に発見）。**まだ使われている参照は手放さない。**
        同じ絵を複数箇所で使い回している場合に、片方を消しただけで
        もう片方まで展開し直しになるのを避ける。

        実体（assets/）は消さない削除・差し替え（→ `window.delete_image`）
        と同じ考え方で、ここも Undo で戻ったときに困らないよう**キャッシュ
        だけ**を対象にする。手放しても、次に描くときに読み直すだけで
        正しい絵に戻る。
        """
        if ref in self.project.referenced_assets():
            return
        self.image_cache.forget(ref)
        self.baked_cache.forget(ref)

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
        old = next(c for c in panel.children if c.id == image_id)
        old_z, old_ref = old.z, old.asset

        ref, px = self.import_bytes(data)
        rect = contain_rect_in(panel.shape.bounds(), px)

        with self.edit("画像の差し替え") as project:
            target = project.pages[self._page_index].panel(panel_id)
            image = project.add_image(target, ref, rect, px)
            image.z = old_z
            target.children = [c for c in target.children if c.id != image_id]
        self.select(image.id)
        self.forget_if_unused(old_ref)
        return image

    # -- 切り抜き（AI で作ったマスク → 要件定義 10.3） ---------------------
    #
    # **作品が変わるのは適用の1回だけ。** 候補を見比べている間は何も変えない
    # ので、推論の失敗や見比べが未保存の変更や Undo の履歴を汚さない
    # （→ `SAM3実装計画.md` 段階5）。

    def import_mask_bytes(self, data: bytes) -> str:
        """マスクを取り込んで参照を返す。展開できなければ例外。

        **画像用の入れ物（`image_cache`）には入れない。** マスクは描く相手
        ではなく、絵に掛ける材料。混ぜると、引く側が「これは絵か、マスクか」を
        判断できなくなる（→ `ImageCache` の注記）。
        """
        decode_mask(data)  # 壊れていればここで例外
        if self.project_dir is None:
            return self.pending_assets.add(data)
        return AssetStore(self.project_dir).add_bytes(data)

    def apply_image_mask(
        self, image_id: str, data: bytes, *, label: str = "切り抜きの適用"
    ) -> bool:
        """表示中のページの画像に切り抜きを掛ける。掛けたら True。

        `label` は履歴に積む名前。**押した所を消す操作（→ `erase_region_at`）は
        別の名前で積む**——Undo の一覧で「切り抜きの適用」が並ぶだけだと、
        どれがどの操作か分からない。

        **寸法が合わなければ断る**（`MaskSizeError`）。縮めて合わせることは
        しない——合わせると、ずれた組み合わせが「輪郭がわずかにずれた絵」に
        なるだけで人が気づけない（→ `image_masks.apply_mask`）。

        既に切り抜いてある画像へ掛け直すのも同じ1手。前のマスクの実体は
        消さず、使われなくなった実体は「未使用ファイルを整理」が拾う
        （→ 要件定義 5章。差し替え・削除と同じ扱い）。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject):
            return False

        mask_px = size_px(decode_mask(data))
        if mask_px != image.src_px:
            raise MaskSizeError(
                f"切り抜きの大きさが画像と違います"
                f"（画像 {image.src_px[0]:,} × {image.src_px[1]:,} 画素、"
                f"切り抜き {mask_px[0]:,} × {mask_px[1]:,} 画素）"
            )

        old_ref = image.mask_asset
        ref = self.import_mask_bytes(data)
        with self.edit_page(label) as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.mask_asset = ref
        if old_ref:
            self.forget_if_unused(old_ref)
        return True

    def clear_image_mask(self, image_id: str) -> bool:
        """切り抜きを外して元の絵に戻す。外したら True。

        **実体（assets/）は消さない。** Undo で戻せる操作なので、ここで
        消すと戻したときに切り抜きだけが失われる（ラフを外すのと同じ扱い）。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject) or not image.mask_asset:
            return False

        old_ref = image.mask_asset
        with self.edit_page("切り抜きを外す") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.mask_asset = ""
        self.forget_if_unused(old_ref)
        return True

    def region_mask_at(self, image: ImageObject, seed: tuple[int, int]):
        """その画像の、指した1点を含む区画（→ `manga_layout.wand`）。

        **原寸に対して選ぶ。** マスクは元画像と同じ寸法でなければ適用できない
        ので、画面用の縮小版（`image_cache`）は使えない。
        """
        data = self.read_asset(image.asset)
        if data is None:
            return None
        return select_at(decode(data), seed, tolerance=self.wand_tolerance)

    def erase_region_at(
        self, image_id: str, seed: tuple[int, int], *, keep_only: bool = False
    ) -> bool:
        """指した区画を消す。消したら True（→ 要件定義 10.3）。

        **1回押すと1手。** 選んでから確かめて確定する、という段取りを置かない。
        違えば Undo で戻し、続けて押せば足せる——**戻せる操作なら、確認より
        やり直しのほうが手数が少ない**（本人の判断 2026-08-27）。

        `keep_only` なら逆に「そこだけ残す」。背景が何区画にも割れている絵で、
        消す側を何度も押すより速い。

        既に切り抜いてある絵では、**今のマスクから引く**（掛け直しではない）。
        押すたびに前回の結果が消えると、区画ごとに消していけない。
        """
        image = self.page.find(image_id)
        if not isinstance(image, ImageObject):
            return False

        chosen = self.region_mask_at(image, seed)
        if chosen is None:
            self.message.emit("絵の実体が見つかりません")
            return False
        if chosen.empty:
            return False

        current = self.image_mask_or_full(image)
        if current is None:
            return False
        updated = (
            intersected(current, chosen.mask)
            if keep_only
            else removed(current, chosen.mask)
        )

        label = "押した所だけ残す" if keep_only else "押した所を消す"
        if not self.apply_image_mask(image_id, to_png_bytes(updated), label=label):
            return False

        if chosen.leaked and not keep_only:
            self.message.emit(
                f"{label}ました（絵の {chosen.ratio:.0%}）。"
                "線に隙間があるかもしれません"
            )
        else:
            self.message.emit(f"{label}ました（押した区画は絵の {chosen.ratio:.0%}）")
        return True

    def image_mask_or_full(self, image: ImageObject):
        """その画像の今のマスク。掛かっていなければ**全面が残った**マスク。

        **無い状態を「全部残す」に読み替える。** こうすると、1枚目を押すときと
        2枚目以降を押すときで処理が分かれない。
        """
        px = image.src_px
        if image.mask_asset:
            data = self.read_asset(image.mask_asset)
            if data is not None:
                try:
                    return decode_mask(data)
                except AssetError:
                    pass  # 壊れている。全面から引き直す（描画と同じ考え方）
        full = QImage(px[0], px[1], QImage.Format.Format_Grayscale8)
        if full.isNull():
            return None
        full.fill(255)
        return full

    def step_wand_tolerance(self, steps: int) -> bool:
        """許容差を増減する。**端で止める。** 変わったら True。

        トーンの増減（→ `_step_tone`）と同じ流儀。数字そのものは覚えなくて
        よいように、状態表示に段を出す。
        """
        value = max(
            WAND_TOLERANCE_MIN,
            min(WAND_TOLERANCE_MAX, self.wand_tolerance + steps * WAND_TOLERANCE_STEP),
        )
        if value == self.wand_tolerance:
            return False
        self.wand_tolerance = value
        self.message.emit(f"切り抜きの許容差: {value}")
        return True

    # -- ラフ（下敷き） ----------------------------------------------------

    def rough_preview(self, ref: str, faded: bool) -> Preview | None:
        """ラフを描くための1枚。無い・壊れているときは None。

        **青く染めるかどうかで入れ物を分ける。** 染めていないほうは普通の
        画像と同じものなので、画像用の入れ物をそのまま使い回せる。
        """
        cache = self.rough_cache if faded else self.image_cache
        return cache.get(ref, lambda: self.read_asset(ref))

    def place_rough(self, data: bytes) -> PageRough:
        """表示中のページにラフを敷く（→ 要件定義 6.23）。

        最初は**ページに収まる大きさ**（全体が見える）で置く。写真の縦横比が
        用紙と違っても切れないので、まず全体を見てから調整できる。
        既に敷いてあれば置き換える（1ページに1枚）。
        """
        ref, px = self.import_bytes(data)
        rect = contain_rect_in(Rect(0.0, 0.0, self.page.size.w, self.page.size.h), px)
        rough = PageRough(asset=ref, rect=rect, src_px=px)
        with self.edit_page("ラフの読み込み") as page:
            page.rough = rough
        return rough

    def remove_rough(self) -> None:
        """表示中のページのラフを外す。**実体（assets/）は消さない。**

        Undo で戻せる操作なので、ここで実体まで消すと戻したときに×印だけが
        残る。使われなくなった実体は「未使用ファイルを整理」が拾う（→ 5章）。
        """
        with self.edit_page("ラフを外す") as page:
            page.rough = None

    def set_rough_faded(self, faded: bool) -> None:
        """ラフを青く淡くするか、元の絵のまま出すかを切り替える。"""
        label = "ラフを青く淡く" if faded else "ラフを元の色に"
        with self.edit_page(label) as page:
            if page.rough is not None:
                page.rough = dataclasses.replace(page.rough, faded=faded)

    def set_rough_rect(self, rect: Rect, label: str) -> None:
        """ラフの位置・大きさを差し替える。1回のドラッグで1手。"""
        with self.edit_page(label) as page:
            if page.rough is not None:
                page.rough = dataclasses.replace(page.rough, rect=rect)

    # -- 斜めのコマ --------------------------------------------------------
    #
    # 組を作り直す操作は3つ（向きの反転・境界の移動・大きさ変更）あり、
    # **どれも最後は `slant.rebuild_slant_pair` → `check_slant` を通る**。
    # 割れない大きさなら `ValueError` で断られるので、受け方も1か所に
    # まとめてある（→ `_edit_slant`）。

    def _edit_slant(self, label: str, apply) -> bool:
        """斜めの組を作り直す1手。作り直せたら True。

        `apply(page)` の中で `check_slant` が断ったら、**理由を状態表示に
        出して False を返す**（コマの分割と同じ扱い → `PageView._apply_split`）。

        **3つの操作で受け方を書き分けない。** 書き分けていた頃は大きさ変更
        にしか受けが無く、反転と境界の移動では `ValueError` が Qt の
        スロットを突き抜けていた。突き抜けても PySide6 は印字して続けるので、
        **画面には何も出ないまま操作だけが効かない**（2026-08-09 に発見）。

        普段はここへ来ない。**ドラッグの下見が先に押し戻す**ので
        （→ `slant.clamp_slant_rect` / `clamp_slant_ratio`）、断りが要るのは
        手で書き換えた project.json のように、元から割れない大きさの組だけ。
        """
        try:
            with self.edit_page(label) as page:
                apply(page)
        except ValueError as e:
            self.message.emit(str(e))
            return False
        return True

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
        return self._edit_slant(
            "斜めの向きを反転",
            lambda page: flip_slant_pair(
                page, page.slant_pair_of(panel_id), self.settings
            ),
        )

    def slide_slant(self, panel_id: str, ratio: float) -> bool:
        """斜めの境界を左右にずらす。ずらせたら True。

        1回のドラッグで1手。ドラッグ中は画面側が下見を描くだけで、
        ここへは離した時点で1度だけ来る（しっぽの付け根と同じ流儀）。
        """

        def apply(page: Page) -> None:
            pair = page.slant_pair_of(panel_id)
            if pair is not None:
                slide_slant_pair(page, pair, ratio, self.settings)

        return self._edit_slant("斜めの境界を移動", apply)

    def set_slant_rect(self, panel_id: str, rect: Rect) -> bool:
        """斜めの組の外側の矩形を差し替える（大きさ変更）。変えられたら True。

        1枚ずつ変形せず組ごと作り直す。片方ずつ動かすと、傾きと隙間が
        左右で食い違う（→ `slant.set_slant_pair_rect`）。
        """

        def apply(page: Page) -> None:
            pair = page.slant_pair_of(panel_id)
            if pair is not None:
                set_slant_pair_rect(page, pair, rect, self.settings)

        return self._edit_slant("斜めのコマの大きさ変更", apply)

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
        with self.edit_page(label) as page:
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
        with self.edit_page("このページのコマをすべてロック") as page:
            for panel in page.panels:
                panel.locked = True
        return True

    def unlock_all_panels(self) -> bool:
        """このページのコマのロックをすべて解除する。変わったら True。"""
        if not any(p.locked for p in self.page.panels):
            return False
        with self.edit_page("このページのコマのロックをすべて解除") as page:
            for panel in page.panels:
                panel.locked = False
        return True

    # -- 次のコマの提案 ------------------------------------------------------
    #
    # 描きかけのページを見て、次に置くコマを提案する（→ 要件定義 10.5）。
    # 幾何の計算は `next_panel.py` にあり、ここは**押されたときの振る舞い**だけを持つ。
    #
    # **押すたびに、直前の提案を次の案へ差し替える。** 増やしていくのではない。
    # 案どうしは重なるので、並べて置くと読めない絵になる。
    #
    # **履歴は `merge_key` で1手にまとめる。** 5回押しても Undo 1回で消える。
    # 「気に入らなければ元に戻す」を、押した回数だけ繰り返させない。

    def suggest_next_panel(self) -> bool:
        """次のコマを提案し、そのとおりに置く。置けたら True。

        **成功のお知らせもここで出す。** 何番目の案かは呼ぶ側からは分からないため
        （他の操作は窓の側でお知らせを出しているが、ここだけ例外にしてある）。
        """
        page = self.page
        if not next_panel.supported(page):
            self.message.emit("斜めのコマがあるページでは提案できません")
            return False

        # 直前の1手が提案で、そのとき置いたコマが今もあるなら「差し替え」。
        # 途中で別の操作をしたら、その提案は**確定したもの**として扱い、次は新しく足す
        ids = {p.id for p in page.panels}
        replacing = (
            self.history.undo_label == SUGGEST_LABEL
            and bool(self._suggested)
            and ids.issuperset(self._suggested)
        )
        ignore = self._suggested if replacing else ()

        found = next_panel.suggestions(
            self.project, page, ignore, self.settings.margin, self.settings.gutter
        )
        if not found:
            self._suggested = ()
            self.message.emit("提案できる形が見つかりません")
            return False

        index = (self._suggest_index + 1) % len(found) if replacing else 0
        with self.edit(SUGGEST_LABEL, merge_key=SUGGEST_LABEL) as project:
            edited = project.pages[self._page_index]
            if replacing:
                edited.panels[:] = [p for p in edited.panels if p.id not in ignore]
            added = next_panel.add_suggestion(project, edited, found[index])
        self._suggested = tuple(p.id for p in added)
        self._suggest_index = index
        self.message.emit(
            f"提案 {index + 1}/{len(found)}: {found[index].text()}"
            "（もう一度押すと次の案）"
        )
        return True

    # -- コマの重なり順 ------------------------------------------------------
    #
    # コマ同士が重なったとき、どちらを手前に描くかを `z` で決める。描く順
    # （`render.draw_page`）とクリック判定（`layout.panel_at`）はどちらも
    # 元から `z` を見ているので、ここで書き換えるだけで両方に効く。
    #
    # **「1つ手前へ」ではなく「最前面へ」にしてある。** 重なっていない
    # コマを1つ跨いでも見た目が変わらず、押しても何も起きないように
    # 見える。段階を数えさせないほうが確実に届く。

    @staticmethod
    def _panel_group(page: Page, panel_id: str) -> tuple[str, ...]:
        """一緒に動かすコマ。斜めの組なら**両方**、でなければ自分だけ。

        ロック（→ `set_panel_locked`）と同じ扱い。片割れだけ手前に出すと、
        1枚を割って作ったはずの2枚の間に別のコマが挟まる。
        """
        pair = page.slant_pair_of(panel_id)
        return pair.members() if pair is not None else (panel_id,)

    def _panel_group_at_edge(self, panel_id: str, *, front: bool) -> bool:
        """そのコマ（と相方）が既に一番手前／一番奥に居るか。"""
        page = self.page
        group = set(self._panel_group(page, panel_id))
        others = [p.z for p in page.panels if p.id not in group]
        if not others:
            return True
        mine = [page.panel(i).z for i in group]
        return min(mine) > max(others) if front else max(mine) < min(others)

    def can_raise_panel(self, panel_id: str) -> bool:
        return not self._panel_group_at_edge(panel_id, front=True)

    def can_lower_panel(self, panel_id: str) -> bool:
        return not self._panel_group_at_edge(panel_id, front=False)

    def raise_panel(self, panel_id: str) -> bool:
        """コマを最前面へ。変わったら True。

        既に一番手前なら**何もしない**。押しても変化の無い操作で Undo の
        一手を使わせない（→ `lock_all_panels` と同じ流儀）。
        """
        if not self.can_raise_panel(panel_id):
            return False
        with self.edit_page("コマを手前へ") as page:
            top = max(p.z for p in page.panels)
            for offset, target_id in enumerate(self._ordered_group(page, panel_id), 1):
                page.panel(target_id).z = top + offset
        return True

    def lower_panel(self, panel_id: str) -> bool:
        """コマを最背面へ。変わったら True。"""
        if not self.can_lower_panel(panel_id):
            return False
        with self.edit_page("コマを奥へ") as page:
            bottom = min(p.z for p in page.panels)
            group = self._ordered_group(page, panel_id)
            for offset, target_id in enumerate(reversed(group), 1):
                page.panel(target_id).z = bottom - offset
        return True

    @classmethod
    def _ordered_group(cls, page: Page, panel_id: str) -> list[str]:
        """一緒に動かすコマを、今の重なり順のまま並べたもの。

        斜めの組は互いに重ならないので見た目には出ないが、順を崩さずに
        運べば、あとで組に別の意味が付いたときに巻き込まれない。
        """
        return sorted(cls._panel_group(page, panel_id), key=lambda i: page.panel(i).z)

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
            with self.edit_page(label) as page:
                target = page.find(panel_id)
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
        with self.edit_page("集中線を入れる") as page:
            page.panel(panel_id).focus_lines = default_focus(self.focus_settings)
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
        with self.edit_page("集中線を消す") as page:
            page.panel(panel_id).focus_lines = None
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

    # -- 流線 --------------------------------------------------------------
    #
    # 集中線と同じく、独立したオブジェクトではなくコマの属性
    # （→ 要件定義 6.26）。選択・削除・複製の経路には出てこない。
    #
    # **集中線の操作と1つにまとめていない。** 値の名前だけでなく、決まる
    # ものが違う（中心と空き ⇄ 向きと長さ）ので、まとめると分岐だらけの
    # 1本になる。`_step_flow` だけは形が同じなので、そちらでまとめてある。

    @property
    def selected_flow(self) -> FlowLines | None:
        """選択中のコマに入っている流線。無ければ None。"""
        panel = self.selected_panel
        return None if panel is None else panel.flow_lines

    def _edit_flow(self, panel_id: str, label: str):
        """id で引き直してから触るための小さな入れ物（`_edit_focus` と同じ）。"""

        @contextlib.contextmanager
        def scope():
            with self.edit_page(label) as page:
                target = page.find(panel_id)
                if not isinstance(target, Panel) or target.flow_lines is None:
                    raise KeyError(f"流線の入ったコマが見つかりません: {panel_id}")
                yield target

        return scope()

    def add_flow_lines(self) -> bool:
        """選択中のコマに流線を入れる。入れたら True。

        **1コマに1つまで。** 集中線が入っていても構わない（別の項目なので
        両方持てる → 要件定義 6.26）。
        """
        panel = self.selected_panel
        if panel is None:
            self.message.emit("流線を入れるコマを選んでください")
            return False
        if panel.flow_lines is not None:
            return False

        panel_id = panel.id
        with self.edit_page("流線を入れる") as page:
            page.panel(panel_id).flow_lines = default_flow(self.flow_settings)
        self.message.emit("流線を入れました。丸のつまみをドラッグすると向きが変わります")
        return True

    def remove_flow_lines(self) -> bool:
        """選択中のコマから流線を消す。消したら True。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self.edit_page("流線を消す") as page:
            page.panel(panel_id).flow_lines = None
        return True

    def set_flow_angle(self, panel_id: str, angle: float) -> None:
        """向きを差し替える。1回のドラッグで1手（集中線の中心と同じ流儀）。"""
        with self._edit_flow(panel_id, "流線の向き") as panel:
            panel.flow_lines.angle = angle

    def step_flow_count(self, steps: int) -> bool:
        """線の本数を増減する。変わったら True。"""
        return self._step_flow(
            "count", flow_stepped_count, steps, lambda n: f"流線: {n} 本"
        )

    def step_flow_width(self, steps: int) -> bool:
        """線の太さを増減する。変わったら True。"""
        return self._step_flow(
            "width",
            flow_stepped_width,
            steps,
            lambda w: f"流線の太さ: {w * 100:.1f}%（コマの短辺に対する割合）",
        )

    def step_flow_length(self, steps: int) -> bool:
        """線の長さを増減する。変わったら True。"""
        return self._step_flow(
            "length",
            flow_stepped_length,
            steps,
            lambda v: f"流線の長さ: コマの対角線の {v * 100:.0f}%",
        )

    def _step_flow(self, field: str, step, steps: int, label) -> bool:
        """本数・太さ・長さの増減は、値の名前と刻み方だけが違う
        （→ `_step_focus`）。
        """
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False
        value = step(getattr(panel.flow_lines, field), steps)
        if value == getattr(panel.flow_lines, field):
            # 端まで来ている。**履歴に積まない**（→ `_step_focus`）
            return False

        panel_id = panel.id
        with self._edit_flow(panel_id, "流線の調整") as target:
            setattr(target.flow_lines, field, value)
        self.message.emit(label(value))
        return True

    def reseed_flow(self) -> bool:
        """ばらつきだけを作り直す。向き・本数・太さ・長さは変えない。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self._edit_flow(panel_id, "流線の形を振り直す") as target:
            target.flow_lines.seed = new_seed()
        return True

    def toggle_flow_color(self) -> bool:
        """線の色を黒⇄白で切り替える（集中線と同じ形 → 6.19）。"""
        panel = self.selected_panel
        if panel is None or panel.flow_lines is None:
            return False

        panel_id = panel.id
        with self._edit_flow(panel_id, "流線の色") as target:
            target.flow_lines.white = not target.flow_lines.white
        return True

    # -- トーン（画像の黒の置き換え → 要件定義 10.1） ------------------------

    @property
    def tone_image(self) -> ImageObject | None:
        """トーンの操作が効く画像。無ければ None。

        **絵を選んでいなくても、コマを選んでいれば効く。** トーンの持ち主は
        画像だが（→ 要件定義 6.27）、絵を選ぶにはコマをダブルクリックして
        中へ入る必要がある。そこを要求すると、集中線・流線と同じつもりで
        コマを選んだ利用者には**ただグレーの項目**に見える（本人談 2026-08-06）。

        **絵が2枚以上あるコマでは決まらない**ので None を返す。背景の上に
        キャラを重ねる使い方があり、どちらに掛けるかは黙って選べない
        （→ `tone_ambiguous`）。
        """
        image = self.selected_image
        if image is not None:
            return image
        images = self.panel_images
        return images[0] if len(images) == 1 else None

    @property
    def panel_images(self) -> list[ImageObject]:
        """選択中のコマに入っている絵。コマを選んでいなければ空。"""
        panel = self.selected_panel
        if panel is None:
            return []
        return [c for c in panel.children if isinstance(c, ImageObject)]

    @property
    def tone_ambiguous(self) -> bool:
        """コマを選んでいて、絵が2枚以上ある。どれに掛けるか決まらない。

        **項目はグレーにせず、押したら状態表示で断る。** グレーにすると
        「使えない」は伝わるが理由が伝わらない（斜めのコマを複製できない
        と伝えるときと同じ線引き → 6.15）。
        """
        return self.selected_image is None and len(self.panel_images) > 1

    @property
    def selected_tone(self) -> Tone | None:
        """操作が効く画像に入っているトーン。無ければ None。"""
        image = self.tone_image
        return None if image is None else image.tone

    def _edit_tone(self, image_id: str, label: str, *, merge_key: str | None = None):
        """id で引き直してから触るための小さな入れ物（`_edit_flow` と同じ）。

        `merge_key` を渡すと、連打ぶんが履歴の1手にまとまる（→ `History.commit`）。
        """

        @contextlib.contextmanager
        def scope():
            with self.edit_page(label, merge_key=merge_key) as page:
                target = page.find(image_id)
                if not isinstance(target, ImageObject) or target.tone is None:
                    raise KeyError(f"トーンの入った画像が見つかりません: {image_id}")
                yield target

        return scope()

    def add_tone(self) -> bool:
        """選択中の画像にトーンを入れる。入れたら True。

        **範囲は絞らない状態で入る。** 絞るのは足りなかったときの手当てで、
        まず全体に掛けて様子を見るほうが手数が少ない（→ 要件定義 10.1）。
        """
        if self.tone_ambiguous:
            self.message.emit(
                "このコマには絵が複数あります。"
                "ダブルクリックで絵を選んでから入れてください"
            )
            return False
        image = self.tone_image
        if image is None:
            self.message.emit("トーンを入れる絵かコマを選んでください")
            return False
        if image.tone is not None:
            return False

        image_id = image.id
        with self.edit_page("トーンを入れる") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.tone = default_tone()
        self.message.emit(
            "黒ベタをトーンにしました。範囲を絞るには道具の「トーン範囲を調整」へ"
        )
        return True

    def remove_tone(self) -> bool:
        """選択中の画像からトーンを消す。消したら True。"""
        image = self.tone_image
        if image is None or image.tone is None:
            return False

        image_id = image.id
        with self.edit_page("トーンを消す") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.tone = None
        return True

    # 押した直後の状態表示は、**割合ではなく「何段目か」で出す**
    # （→ 要件定義 6.27）。`0.3% 未満` の形だと「設定値を1つ決めた」ように
    # 読め、**同じ項目をもう一度押してよいことが伝わらない**（本人談
    # 2026-08-06。「細い線を残す」を3回押してちょうどよくなった）。
    # メニューの項目名にも同じ数字が出る（→ `ToneMenu`）。

    def step_tone_threshold(self, steps: int) -> bool:
        """どこまでを黒と見るかを増減する。変わったら True。"""
        return self._step_tone(
            "threshold",
            tone_stepped_threshold,
            steps,
            # ここだけ元の値も添える。**絵ごとに合わせる値**で、
            # 前に上手くいった絵と見比べられるようにしておく（→ 6.27）
            lambda n: f"拾う黒: {tone_level_label('threshold', n)}（明るさ {n} 以下）",
        )

    def step_tone_pitch(self, steps: int) -> bool:
        """斜線の間隔を増減する。変わったら True。"""
        return self._step_tone(
            "pitch",
            tone_stepped_pitch,
            steps,
            lambda v: f"斜線の間隔: {tone_level_label('pitch', v)}",
        )

    def step_tone_density(self, steps: int) -> bool:
        """濃さ（線の太さ）を増減する。変わったら True。"""
        return self._step_tone(
            "density",
            tone_stepped_density,
            steps,
            lambda v: f"トーンの濃さ: {tone_level_label('density', v)}",
        )

    def step_tone_thin(self, steps: int) -> bool:
        """どこまでを細いと見るかを増減する。変わったら True。"""
        return self._step_tone(
            "thin",
            tone_stepped_thin,
            steps,
            lambda v: (
                # 0 は「細さで選り分けない」という別の状態。段数だけだと
                # 端に着いたことしか分からないので、言葉でも断る
                f"細い線を残す: {tone_level_label('thin', v)}"
                + ("（細さで選り分けない）" if v <= 0.0 else "")
            ),
        )

    def set_tone_kind(self, kind: str) -> bool:
        """トーンの見た目（斜線・灰色・白抜き）を切り替える。変わったら True。

        **効かなくなる値を消さない。** 灰色にすると `angle` と `pitch` は
        絵に出なくなるが、持ったままにしておけば斜線へ戻したときに前の
        調整がそのまま返る（メニュー側はグレーにして「今は効かない」と
        示す → `ToneMenu.refresh`）。

        **連打をまとめない。** 3つを行き来して見比べる操作なので、1手ずつ
        積んで Undo で1つずつ戻れるほうがよい（増減の連打とは別 → `_step_tone`）。
        """
        image = self.tone_image
        if image is None or image.tone is None or image.tone.kind == kind:
            return False

        image_id = image.id
        with self._edit_tone(image_id, "トーンの種類") as target:
            target.tone.kind = kind
        self.message.emit(f"トーンの種類: {TONE_KIND_LABELS[kind]}")
        return True

    def step_tone_angle(self, steps: int) -> bool:
        """斜線の向きを 15 度ずつ回す。**つまみは作らない**（→ 要件定義 10.1）。"""
        image = self.tone_image
        if image is None or image.tone is None:
            return False

        angle = normalize_angle(image.tone.angle + steps * TONE_ANGLE_STEP)
        image_id = image.id
        with self._edit_tone(image_id, "斜線の向き") as target:
            target.tone.angle = angle
        self.message.emit(f"斜線の向き: {angle:.0f}°")
        return True

    def _step_tone(self, field: str, step, steps: int, label) -> bool:
        """しきい値・間隔・濃さ・細さの増減は、値の名前と刻み方だけが違う
        （→ `_step_flow`）。

        **連打ぶんは履歴の1手にまとめる**（`merge_key`）。1回ずつ積むと、
        20回押した調整を戻すのに Undo を20回押すことになる（セリフの入力と
        同じ扱い → `History.commit`）。**鍵に項目の名前を混ぜる**ので、
        濃さを触ってからしきい値を触っても別の手として積まれる。
        """
        image = self.tone_image
        if image is None or image.tone is None:
            return False
        value = step(getattr(image.tone, field), steps)
        if value == getattr(image.tone, field):
            # 端まで来ている。**履歴に積まない**（→ `_step_flow`）
            return False

        image_id = image.id
        with self._edit_tone(
            image_id, "トーンの調整", merge_key=f"tone:{image_id}:{field}"
        ) as target:
            setattr(target.tone, field, value)
        self.message.emit(label(value))
        return True

    def set_tone_area(self, image_id: str, area: Rect | None) -> None:
        """絞る矩形を差し替える。1回のドラッグで1手（流線の向きと同じ流儀）。

        `area` は**画像に対する割合**。`None` で絞らない状態に戻す。
        **はみ出していても直さない**——0〜1 の外は絵が無いだけで、画像の縁で
        自然に切れる（→ 要件定義 10.1）。
        """
        with self._edit_tone(image_id, "トーンの範囲") as target:
            target.tone.area = area

    def clear_tone_area(self) -> bool:
        """絞りを外して画像全体に戻す。戻したら True。"""
        image = self.tone_image
        if image is None or image.tone is None or image.tone.area is None:
            return False
        self.set_tone_area(image.id, None)
        self.message.emit("トーンの範囲を画像全体に戻しました")
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

        **雲は丸い飛びしっぽで置く**（`BALLOON_STYLES_WITH_BUBBLE_TAIL`）。
        どちらも心の声を表すので、三角と組み合わせることがまず無い。
        どちらも**置いたときの既定**で、あとから切り替えられる。
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
                shape=(
                    TAIL_SHAPE_BUBBLES
                    if style in BALLOON_STYLES_WITH_BUBBLE_TAIL
                    else TAIL_SHAPE_TRIANGLE
                ),
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
        with self.edit_page(label) as page:
            target = page.find(sticker_id)
            if not isinstance(target, StickerObject):
                raise KeyError(f"マークが見つかりません: {sticker_id}")
            target.attached_panel_id = panel_id

    # -- セリフ ------------------------------------------------------------

    def add_text(self, rect: Rect, content: str = "") -> TextObject:
        """セリフを1つ置く。置いたものを選択状態にする。

        **吹き出しの上なら、その吹き出しに紐づける**（要件定義 6.5）。
        吹き出しはコマの中で単独に動かせるので、コマだけに紐づけると
        吹き出しを動かしたときにセリフが取り残される。

        **書式と向きは `next_text_font` / `next_text_direction`（最後に
        指定したもの）を使う。** 作品の中でセリフの書体と大きさが揃って
        いるのが普通なので、1つ選ぶたびに既定へ戻ると、置くたびに選び直す
        ことになる。**向きも同じ**で、横書きの箇条書きを作っている最中に
        1つ置くたび縦書きへ戻ると、そのつど直す操作が要る。
        """
        page = self.page
        balloon = balloon_at(page, *rect.center)
        panel_id = None if balloon is not None else attach_target(page, rect)

        with self.edit("セリフの追加") as project:
            text = project.add_text(
                project.pages[self._page_index], content, rect, panel_id
            )
            text.font = dataclasses.replace(self.next_text_font)
            text.direction = self.next_text_direction
            text.attached_balloon_id = balloon.id if balloon is not None else None
        self.select(text.id)
        return text

    def _edit_text(self, text_id: str, label: str):
        @contextlib.contextmanager
        def scope():
            with self.edit_page(label) as page:
                target = page.find(text_id)
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
        """縦書き・横書きを切り替え、**次に作るセリフにも写す**。

        `align` は持ち替えない。横書きの左右の寄せが、縦書きでは上下の
        寄せとして読み替えられる（→ `manga_layout.vertical`）。別々に
        持つと、向きを往復したときにどちらの値を使うのか決められなくなる。

        写すのは書式（→ `set_text_font`）と同じ理由。向きだけ引き継がずに
        既定の縦書きへ戻ると、横書きの箇条書きを作っている最中は1つ置く
        たびに直すことになる。
        """
        with self._edit_text(text_id, "セリフの向き") as text:
            text.direction = direction
        self.set_next_text_direction(direction)

    def set_next_text_direction(self, direction: str) -> None:
        """**次に作るセリフの向きだけ**を決める。今あるセリフには触らない。

        作品を変えないので履歴には積まない（→ `set_next_text_font`）。
        """
        self.next_text_direction = direction

    def set_text_font(
        self,
        text_id: str,
        *,
        family: str | None = None,
        size_px: float | None = None,
        bold: bool | None = None,
    ) -> None:
        """選んでいるセリフの書式を変え、**次に作るセリフにも写す**。

        写すのをここでやるのは、書式を指定する操作（種類を選ぶ・大きさを
        1段階ずつ・太字）が全部ここを通るため。呼び出し側でやると、
        3か所のうち1つを直し忘れたときに「この操作だけ引き継がれない」
        という説明の付かない差ができる（→ `next_text_font`）。
        """
        with self._edit_text(text_id, "セリフの書式") as text:
            text.font = dataclasses.replace(
                text.font, **_font_changes(family, size_px, bold)
            )
        self.set_next_text_font(family=family, size_px=size_px, bold=bold)

    def set_next_text_font(
        self,
        *,
        family: str | None = None,
        size_px: float | None = None,
        bold: bool | None = None,
    ) -> None:
        """**次に作るセリフの書式だけ**を決める。今あるセリフには触らない。

        セリフを1つも選んでいないときにフォントの窓から使う（→
        `MainWindow.choose_font`）。選んでいるときしか書式を指定できないと、
        **次の書式を決めるためだけに、要らないセリフを1つ置く**ことになる。

        作品を変えないので履歴には積まない（→ `next_text_font`）。
        """
        self.next_text_font = dataclasses.replace(
            self.next_text_font, **_font_changes(family, size_px, bold)
        )

    def _edit_balloon(self, balloon_id: str, label: str):
        """id で引き直してから触るための小さな入れ物。

        Undo で `Project` の実体が差し替わるため、外で掴んだ吹き出しを
        そのまま書き換えてはいけない（要件定義 6.8）。
        """

        @contextlib.contextmanager
        def scope():
            with self.edit_page(label) as page:
                target = page.find(balloon_id)
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

    # -- 点検の印（要件定義 10.1） -------------------------------------------

    @property
    def check_marks(self) -> set[str]:
        """紫の印が付いているページの id。**作品には保存されない。**"""
        return set(self._check_marks)

    def set_check_marks(self, page_ids: set[str]) -> None:
        """点検の結果で印を付け直す。**前の結果は必ず捨てる。**

        足し込むと、直したものの印が残り続けて嘘になる。押すたびに数え直す
        （→ 要件定義 10.1）以上、印も毎回まっさらから付け直す。
        """
        if page_ids == self._check_marks:
            return
        self._check_marks = set(page_ids)
        self.check_changed.emit()

    def clear_check_marks(self) -> None:
        self.set_check_marks(set())

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
        self.rough_cache.clear()
        self.baked_cache.clear()
        # 点検の印は前の作品のもの。ページの id ごと別系列になるので、
        # 残しても付きようがないが、消さないと数だけ残る（→ 要件定義 10.1）
        self.clear_check_marks()
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
        store = AssetStore(target)
        self._carry_assets_to(store)
        self.pending_assets.flush_to(store)

        # **控えを手放すのは project.json の書き込みが終わってから。**
        # 実体は書けたのにここで例外が飛ぶと（ロック・ディスク満杯など）、
        # 保存は失敗として返る。そこで控えを先に空にしていると、続けて
        # 別の場所へ保存し直したときに実体が書かれず、参照だけが残った
        # project.json ができてしまう（2026-08-08 に発見）
        path = save_project(self.project, target)
        self.pending_assets.clear()
        self.project_dir = target
        self.history.mark_saved()
        return path

    def _carry_assets_to(self, store: AssetStore) -> None:
        """今の保存先にある画像の実体を、新しい保存先へ写す。

        「名前を付けて保存」で保存先が変わる経路のためにある。
        `pending_assets` が持っているのは**保存先が決まる前に貼った分**だけ
        なので、一度保存した作品を別名保存すると、それだけでは
        `assets/` が空のまま project.json だけが増える（＝参照が全部切れる）。

        写すのは **project.json から参照されている画像だけ。** 参照の無い
        ものまで運ぶと、「未使用ファイルを整理」で片付けたはずのものが
        別名保存のたびに戻ってくる。

        **1枚読めなくても保存は止めない。** 元の `assets/` が既に欠けている
        作品を別名保存したいことはある。そこで保存ごと失敗させると、
        欠けていない残りまで写せずに終わってしまう。欠けたままの参照は
        「ファイル → 抜けチェック」で見つけられる。
        """
        if self.project_dir is None:
            return

        source = AssetStore(self.project_dir)
        for ref in sorted(self.project.referenced_assets()):
            # 名前が中身のハッシュなので、同名が既にあれば中身も同じ。
            # 保存先が変わっていない普段の保存は、ここで全部素通りする
            if store.exists(ref) or ref in self.pending_assets:
                continue
            try:
                store.add_bytes(source.read(ref))
            except AssetError:
                # 読めない・画像として通らない1枚を飛ばすだけ。書き込みに
                # 失敗した場合（OSError）はここでは捕まえず、保存を止める。
                # 実体を書けないまま project.json を書くと参照が切れる
                continue

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

    # -- バックアップからの復元（要件定義 6.6） ------------------------------

    def backups(self) -> list[BackupEntry]:
        """戻せる世代の一覧。保存先が決まっていなければ空。"""
        if self.project_dir is None:
            return []
        return list_backups(self.project_dir)

    def restore_backup(self, path: pathlib.Path) -> list[str]:
        """`backup/` の世代1つを画面へ戻す。直した箇所があれば返す。

        **`project.json` は書き換えない。** 戻した結果は履歴の上に1手として
        乗るだけなので、Undo 1回で復元前の作業に戻れる。ディスクへ確定する
        のは利用者が保存を押したときで、そのとき今の `project.json` は
        自動で `backup/project.1.json` へ退避される（→ `_rotate_backups`）。

        **順序が要**。選んだ世代を**先に読み切ってから**、今の内容を退避する。
        逆にすると `write_autosave` が世代を1つずつ繰り下げるので、
        `autosave.2.json` を選んだつもりが別の中身になり、`autosave.3.json`
        に至っては最古として消えたあとを読むことになる。

        **`reset()` は通さない。** あちらは `History` を作り直すので、
        通した瞬間に「元に戻す」で戻れなくなる。
        """
        project = load_backup(path)
        warnings = list(project.load_warnings)

        # 今の内容を1つ退避しておく。戻す操作自体で今の作業を失わせない
        # （要件定義 10.1）。**読み終えた後**に行う（上の「順序が要」）
        with contextlib.suppress(OSError):
            self.autosave()

        if not self.history.replace(project, "バックアップから復元"):
            return warnings

        self._after_restore()
        return warnings

    def _after_restore(self) -> None:
        """復元で入れ替わった後の後始末。

        選択は**必ず解除する**。戻した作品の ID は今選んでいるものと
        別系列なので、残すと「選んでいるはずのものが見当たらない」状態に
        なる。ページ番号を丸めるのは Undo と同じ理由（→ `_after_history_move`）。

        **画像の覚え書き（`image_cache`）は捨てない。** `reset()` は捨てるが、
        あちらは別の作品へ移る操作。ここでは同じ作品フォルダの中に留まり、
        参照は中身から作った名前（SHA1）なので、同じ参照なら中身も必ず同じ。
        捨てると復元のたびに全部の画像を展開し直すことになる（Undo が
        捨てていないのと同じ理由）。
        """
        self._selected_id = None
        self._page_index = max(0, min(self._page_index, self.page_count - 1))
        self._leave_rough_tool_if_gone()
        # `_after_history_move` と同じ理由（→ そちら）。選択を必ず外すので
        # トーンの範囲を直す道具も持ったままにしない（2026-08-08 発見。
        # 以前はここだけ呼び忘れていた）
        self._leave_tone_tool_if_gone()
        self.changed.emit()
        self.selection_changed.emit()
        self.page_changed.emit()

    @property
    def is_dirty(self) -> bool:
        return self.history.is_dirty
