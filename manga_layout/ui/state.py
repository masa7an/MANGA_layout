"""編集中の状態。画面の各部品が共通で見る一箇所。

`History` がプロジェクトの唯一の持ち主なので、ここでも `Project` を
直接抱えない。Undo で実体が差し替わったときに、古い `Page` や `Panel` を
掴んだままの画面が出ないようにするため（要件定義 6.8）。

**`EditorState` の中身は5つのファイルに分けてある。** ここに残したのは
**どの操作からも使う土台**（参照・選択と道具・1手の編集・複製・ページ・
付箋）だけで、種類ごとの操作は別のファイルにある。

| ファイル | 何の操作か |
|---|---|
| `state_effects.py` | 集中線・流線・トーン |
| `state_image.py` | 絵の読み込み・切り抜き・ラフ |
| `state_text.py` | 吹き出し・マーク・セリフ |
| `state_panel.py` | 斜めのコマ・ロック・次の提案・重なり順 |
| `state_file.py` | 開く・保存・復元・点検の印 |

**分けたのは置き場所だけで、クラスは1つのまま**（mixin として混ぜている）。
呼ぶ側から見える顔ぶれは分ける前と同じなので、`state.EditorState` を
そのまま使えばよい。**定数と `object_label` はここに残してある**ので、
`from .state import TOOL_ROUGH` の形も変わっていない。
"""

from __future__ import annotations

import contextlib
import pathlib
from collections.abc import Iterator
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..assets import PendingAssets
from ..flow import DEFAULT_FLOW_SETTINGS
from ..focus import DEFAULT_FOCUS_SETTINGS
from ..geometry import Rect, Size
from ..history import History
from ..images import (
    BakedCache,
    ImageCache,
    rough_preview_from_bytes,
)
from ..layout import (
    BalloonSettings,
    LayoutSettings,
    image_orphaned_at,
    outside_page,
    text_frame,
)
from ..model import (
    DEFAULT_TEXT_DIRECTION,
    NOTE_COLORS,
    BalloonObject,
    Font,
    ImageObject,
    Page,
    PageNote,
    Panel,
    Project,
    SceneObject,
    SlantPair,
    StickerObject,
    TextObject,
    new_project,
)
from ..settings import ROUGH_OPACITY_DEFAULT
from ..stickers import STICKER_EXCLAIM, STICKER_EXCLAIM_QUESTION
from ..wand import DEFAULT_TOLERANCE as WAND_TOLERANCE_DEFAULT, GrayImage
from .state_effects import EffectsMixin
from .state_file import FileMixin
from .state_image import ImageMixin
from .state_panel import PanelMixin
from .state_text import STICKER_KIND_LABELS, TextMixin

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
# **並び順は使う頻度で決める**（本人の指示 2026-08-07）。道具箱・フキダシ
# メニュー・種類を変えるメニューがすべてこの順で並ぶので、よく使うものが
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
    # **「選択」だけでは足りない。** 道具メニューを畳んだあと、この項目は
    # 編集メニューの先頭（元に戻す・複製・削除の隣）に立つ（→ 6.33）。
    # そこに「選択」とだけ並ぶと、**選んでいるものへの操作**——「すべて選択」の
    # たぐいに読める。道具箱のボタンは短いままでよいので（`TOOL_SHORT_LABELS`）、
    # 長いほうにだけ「の道具」を付ける
    TOOL_SELECT: "選択の道具",
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
    # （→ `MainWindow._tool_hint`）。項目名で説明しようとすると、道具の並びで
    # ここだけ長くなって読む字数が増える
    TOOL_WAND: "切り抜き",
}

# 道具箱のボタンにだけ出す短い名前（→ 要件定義 6.33）。
#
# **メニューの名前（`TOOL_LABELS`）は変えない。** 道具箱は横1列なので、17個の
# 名前の長さがそのまま幅になる。既定の窓（1100px）で合計 2309px あり、
# **20項目のうち8つしか見えていなかった**（2026-09-06 実機で計測。offscreen だと
# 書体が1本も無く字幅が出ないので、実機でないと測れない）。短い名前を
# `QAction.setIconText` で持たせると**道具箱のボタンにだけ効き**、メニュー・
# 右クリック・「メニューを探す」窓・ホバーの吹き出しは元の名前のまま出る
# （→ `MainWindow._build_tool_actions`）。
#
# **キーは書かない。** メニュー側の名前が「コマ追加 (P)」の形で持っており
# （→ `_build_tool_actions`）、ホバーの吹き出しにもそちらが出る。道具箱でも
# 出すと同じキーが2度並ぶだけになる（→ `menu_search.item_text` と同じ線引き）。
#
# **フキダシとマークは呼び名から作る**（`TOOL_LABELS` と同じ理由）。短い名前を
# 手で書き写すと、改名したときにここだけ古いまま残る。末尾を落とすだけなので、
# 呼び名がその形をやめた日には**短い名前が長い名前と同じになるだけ**で、
# 食い違いにはならない。
TOOL_SHORT_LABELS = {
    TOOL_SELECT: "選択",
    TOOL_PANEL: "コマ",
    TOOL_SPLIT_H: "横割り",
    TOOL_SPLIT_V: "縦割り",
    TOOL_SPLIT_SLANT: "斜め割り",
    **{
        tool: BALLOON_STYLE_LABELS[style].removesuffix("_フキダシ")
        for tool, style in BALLOON_TOOLS.items()
    },
    **{
        tool: STICKER_KIND_LABELS[kind].removesuffix("マーク")
        for tool, kind in STICKER_TOOLS.items()
    },
    TOOL_TEXT: "セリフ",
    TOOL_ROUGH: "ラフ",
    TOOL_TONE_AREA: "トーン",
    # 元から短いので、そのまま（→ `TOOL_LABELS` の注記）
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


class EditorState(EffectsMixin, FileMixin, ImageMixin, PanelMixin, TextMixin, QObject):
    """開いている作品と、画面上の選択・道具。

    **種類ごとの操作は mixin で混ぜている**（→ このファイルの冒頭の表）。
    混ぜる順に意味は無い——**どの mixin も同じ名前のものを持っていない**ので、
    重なって上書きが起きることがない。並びは名前順にしてある。

    **`QObject` は最後に置く。** 合図（`Signal`）はここに書いたものだけが
    使え、mixin の側には持たせられない。混ぜる側が先、Qt が後。
    """

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
        # 切り抜きで押している絵の、濃淡に直した1枚（→ `wand_gray`）。
        # **入れ物ではなく1枚だけ。** 続けて押すたびに展開し直さないための
        # 覚えで、原寸ぶん（2048×2048 で 4MB）あるので抱え込まない
        self.wand_scan: tuple[str, GrayImage] | None = None
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
    def selected_rotatable(self) -> ImageObject | StickerObject | None:
        """選択中の、傾けられるもの。傾けられないものを選んでいれば None。

        **回転を扱う場所は、必ずここから取る。** 描く角度・つまみの位置・
        リサイズ・吸着・「回転をリセット」が同じ集合を見ることになるので、
        対象を1つ増やしたときに片方だけ古いまま、が起きない。

        画像だけだったものを 2026-09-06 にマークへ広げた。焼いたセリフを
        マークとして置き、それを回すため（→ 要件定義 6.14 の書き換え）。
        フキダシ・セリフ・コマは持たない（`rotation` の項目自体が無い）。
        """
        obj = self.selected_object
        return obj if isinstance(obj, (ImageObject, StickerObject)) else None

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

    @contextlib.contextmanager
    def _edit_found(
        self,
        object_id: str,
        label: str,
        kind: type,
        what: str,
        *,
        having: str | None = None,
        merge_key: str | None = None,
    ) -> Iterator[Any]:
        """id で引き直してから触るための入れ物。**1件を直す操作はここを通す。**

        Undo で `Project` の実体が差し替わるため、**外で掴んだオブジェクトを
        そのまま書き換えてはいけない**（要件定義 6.8）。id だけを渡し、編集の
        中で引き直す。

        **この約束を種類ごとに書き分けない。** 吹き出し・セリフ・集中線・
        流線・トーンで5回書いていたときは、6つめを足す人が引き直しごと
        写し忘れられる形だった。**間違いは「書き忘れ」で起き、書き忘れは
        テストに出ない**（引き直さなくても、Undo を挟まなければ動く）。

        `having` は「その属性が入っていること」も条件にするときの属性名。
        集中線・流線・トーンは**独立したオブジェクトではなくコマや画像の
        属性**なので、型が合っていても入っていなければ触れない。

        `merge_key` を渡すと、連打ぶんが履歴の1手にまとまる
        （→ `History.commit`）。

        呼ぶ側は種類ごとの小さな入れ物（`_edit_balloon` など）を使う。
        **そちらに残っているのは名前と型だけで、約束はここにしかない。**
        """
        with self.edit_page(label, merge_key=merge_key) as page:
            target = page.find(object_id)
            if not isinstance(target, kind) or (
                having is not None and getattr(target, having) is None
            ):
                raise KeyError(f"{what}が見つかりません: {object_id}")
            yield target

    def _step_value(
        self, holder: Any, field: str, step, steps: int, label, apply
    ) -> bool:
        """`holder` の持つ値を1段ずらす。ずらせたら True。

        **端まで来ていたら `apply` を呼ばずに False を返す。** 押しても何も
        変わらない操作で Undo の一手を使わせない。

        **この判断をここ1か所に置くのが、この入れ物の目的。** 集中線・流線・
        トーンで3回書いていたときは、4つめを足す人が落とせる形だった。
        落としても**画面の上では正しく動いて見える**——増えなくなった端で
        押し続けたぶんだけ、履歴に空の手が積まれるだけなので、気づくのは
        Undo を連打したときになる。

        `holder` が None なら何もしない（属性が入っていない）。`apply` は
        新しい値を受け取って**持ち主を id で引き直してから**書き込む
        （→ `_edit_found`）。ここで受け取った `holder` は値を読むためだけに
        使う。
        """
        if holder is None:
            return False
        current = getattr(holder, field)
        value = step(current, steps)
        if value == current:
            return False

        apply(value)
        self.message.emit(label(value))
        return True

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
