"""ページの表示と、コマの操作。

**シーンの座標をそのまま px として使う。** 拡大縮小は表示側の変換だけで行い、
モデルの値は一切触らない。おかげでどの倍率でも同じ計算が使え、
当たり判定も `manga_layout.layout`（Qt を使わない側）に任せられる。

コマを `QGraphicsItem` にはせず、その都度描いている。Undo でモデルの実体が
差し替わるため、部品を保持すると古い `Panel` を掴んだままになりやすい。
描き直しの費用は1ページぶんなので、素直に毎回描くほうが安全で速い。

用紙の中身そのものは `render.PageRenderer` が描く。ページ一覧のサムネイルと
**同じ経路**を通すため。ここに残っているのは選択枠・つまみ・下書きといった
「画面の道具」で、作品には出ない。
"""

from __future__ import annotations

import dataclasses
import math
import pathlib
from functools import partial

from PySide6.QtCore import QLineF, QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPen,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
)

from ..errors import MangaLayoutError
from ..fetch import display_name, fetch_bytes, is_fetchable
from ..flow import (
    angle_at as flow_angle_at,
    handle_point as flow_handle_point,
)
from ..focus import (
    center_at as focus_center_at,
    center_point as focus_center_point,
    hole_at as focus_hole_at,
    hole_point as focus_hole_point,
)
from ..geometry import (
    Rect,
    normalize_angle,
    rotate_point,
    rotated_rect_contains,
    unrotate_point,
)
from ..images import to_png_bytes
from ..layout import (
    aspect_of,
    balloon_pick_at,
    default_balloon_rect,
    default_panel_rect,
    handle_at,
    handle_positions,
    image_at,
    image_orphaned_at,
    image_pixel_at,
    keep_anchor,
    next_in_stack,
    panel_at,
    panel_rect_orphans,
    pick_stack,
    resize_rect,
    resize_rect_keep_aspect,
    root_y_at,
    set_panel_rect,
    snap_candidates,
    snap_moved_rect,
    snap_point,
    split_panel,
    sticker_at,
    tail_base_angle,
    tail_body_contains,
    tail_root_point,
    text_at,
)
from ..model import (
    SLANT_RIGHT,
    TONE_KIND_WHITE,
    BalloonObject,
    FlowLines,
    FocusLines,
    ImageObject,
    Panel,
    StickerObject,
    TextObject,
)
from ..slant import (
    clamp_slant_ratio,
    clamp_slant_rect,
    slant_boundary_x,
    slant_handle_point,
    slant_ratio_at,
    split_panel_slant,
)
from .render import (
    TEXT_ALIGN_FLAGS,
    DragPreview,
    PageRenderer,
    cosmetic_pen,
    polygon_of,
    qrect,
    text_font,
)
from .state import (
    BALLOON_TOOLS,
    DEFAULT_TEXT_SIZE,
    STICKER_KIND_LABELS,
    STICKER_TOOLS,
    TOOL_PANEL,
    TOOL_ROUGH,
    TOOL_SELECT,
    TOOL_SPLIT_H,
    TOOL_SPLIT_SLANT,
    TOOL_SPLIT_V,
    TOOL_TEXT,
    TOOL_TONE_AREA,
    TOOL_WAND,
    EditorState,
)

# 分割の道具。押した位置で1回きり切る、という扱いが共通している
SPLIT_TOOLS = (TOOL_SPLIT_H, TOOL_SPLIT_V, TOOL_SPLIT_SLANT)

CANVAS_BG = QColor("#3C3F41")
ACCENT = QColor("#1E88E5")
# 画像を選んでいるときの色。コマの選択（青）と見分けるために変える。
# 同じ色だと、いま動かすのがコマなのか中の絵なのか分からない
IMAGE_ACCENT = QColor("#FB8C00")
# 吹き出しを選んでいるときの色。コマ（青）・画像（橙）と重ならない色にする
BALLOON_ACCENT = QColor("#8E24AA")
# マークを選んでいるときの色。上の3色（青・橙・紫）と重ならない色にする。
# マークは吹き出しに重ねて置くので、吹き出しの紫と見分けが付くことが要る
STICKER_ACCENT = QColor("#00897B")
# セリフを選んでいるときの色。上の4色（青・橙・紫・緑）と重ならない色にする。
# セリフは吹き出しに重ねて置くので、吹き出しの紫と見分けが付くことが要る。
# 以前はここが漏れており、コマ（青）にフォールバックしていたため、
# セリフを選んでいるのかコマを選んでいるのか枠の色では区別できなかった
TEXT_ACCENT = QColor("#E53935")
# ラフを調整しているときの枠の色（→ 要件定義 6.23）。
# **ラフの青（`images.ROUGH_BLUE`）は使わない。** 下敷きそのものと同じ色で
# 枠を描くと、絵の中の線と枠の区別が付かない
ROUGH_ACCENT = QColor("#B08968")
# トーンの範囲を調整しているときの枠とつまみの色（→ 要件定義 10.1）。
#
# **赤系にしない。** 赤（`TEXT_ACCENT`）はセリフの選択色で、赤系
# （`render.MISSING_IMAGE`）は欠けた画像の目印——しかもあちらは**矩形に
# 対角線2本＝すでに「赤い×」**で、選択と無関係に用紙へ描かれる。トーンの
# 範囲も矩形なので、隅に赤い×を出すと「この画像は壊れている」と同じ語彙に
# なる。この道具の間は他のつまみが消えるので、他の色と見分ける必要は無く、
# 避けるのは**下の絵**（黒い斜線と白）のほうだけ
TONE_ACCENT = QColor("#00BCD4")

# 画面上での大きさ（画面ピクセル）。表示倍率で割ってシーンの px に直して使う
HANDLE_PX = 9.0
# コマを選んだときの選択枠の二重線の間隔（画面ピクセル）。
#
# コマのつまみは「枠」であることが伝わりにくいとの指摘（本人談 2026-08-06）を
# 受けて、コマの選択枠だけ二重線にする。画像・フキダシ等の一重線とは
# ここで見た目を分けている
PANEL_SELECTION_DOUBLE_GAP_PX = 15.0
# 斜めの境界を掴める範囲（ピクセル）。**描く印の大きさは `HANDLE_PX` のまま。**
# 印を大きく描くとコマの上に居座って絵の邪魔になるので、
# 「小さく描いて広く拾う」形にしてある
SLANT_HANDLE_PX = 50.0
# しっぽの先端（丸）を掴める範囲（ピクセル）。印は `HANDLE_PX` のまま。
#
# 丸は半径ぶんしか無く、狙って押しても外れやすい
# （本人談 2026-08-05・表示と操作のズレで戸惑うとの指摘）。
TAIL_TIP_HANDLE_PX = 16.0
# しっぽの付け根（ひし形）を掴める範囲（ピクセル）。ここも印は `HANDLE_PX` のまま。
#
# ひし形は同じ大きさの四角より**面積が半分**しかなく、狙って押しても
# 外れることが多かった（本人談 2026-08-05）。判定だけ 5px ぶん広げてある。
# 大きくしすぎると、小さいフキダシで角のつまみを覆う（付け根のほうが
# 先に判定されるため → `mousePressEvent`）ので、この程度で止める
TAIL_ROOT_HANDLE_PX = 14.0
# 集中線の中心（十字）を掴める範囲（ピクセル）。印は `HANDLE_PX` のまま。
#
# **コマの本体より先に判定する**ので、広げるほどコマを掴める場所が減る。
# 線が集まっていて狙いやすい場所でもあるので、控えめに取る
FOCUS_CENTER_HANDLE_PX = 18.0
# 集中線の内側の空き（四角）を掴める範囲（ピクセル）。
# 左右にしか動かないので、しっぽの付け根と同じくらい広く取る
FOCUS_HOLE_HANDLE_PX = 16.0
# 流線の向き（丸）を掴める範囲（ピクセル）。画像の回転つまみ
# （`ROTATE_HANDLE_HIT_PX`）と揃える。**同じ丸で同じ操作**なので、
# 掴み心地まで揃えておく（→ 要件定義 6.26）
FLOW_ANGLE_HANDLE_PX = 16.0

# これ以下の大きさで離した場合、ドラッグではなくクリックとみなして
# 既定の大きさのコマを置く
MIN_CREATE_PX = 6.0
# 吸着が効き始める距離（ピクセル）
SNAP_PX = 8.0

# ページ全体を表示するときに、用紙の外に残す余白（シーンの px）
FIT_MARGIN_PX = 30.0

# ホイール1目盛り／キー1回あたりの倍率。キーのほうが回数を稼ぎにくいので大きめ
WHEEL_ZOOM_STEP = 1.15
KEY_ZOOM_STEP = 1.25

# 表示倍率の下限・上限（画面のピクセル数 ÷ シーンの px）。
# 際限なく縮小・拡大できると、行き過ぎたときに戻ってこられなくなる。
# 1.0 が原寸（シーンの 1px が画面の 1px）。A4 相当は 1240×1754 なので、
# 1ページ全体を見るには 0.4 前後まで縮む必要がある
MIN_VIEW_SCALE = 0.05
MAX_VIEW_SCALE = 8.0

# 拡大・縮小のキー。`+` は配列によって Shift+= になるので `=` も拾う
ZOOM_IN_KEYS = (Qt.Key.Key_Plus, Qt.Key.Key_Equal)
ZOOM_OUT_KEYS = (Qt.Key.Key_Minus,)

# どこまでを黒と見るかを増減するキー（→ 要件定義 6.27）。**`Shift+]` / `Shift+[`。**
#
# **ここをキーにするのは、連打で合わせる値だから。** 目の細かさ・細さは
# 決め打ちで足りるが、拾う黒は絵ごとに違い、**行き過ぎたか足りないかを
# 見ながら往復する**（本人談 2026-08-06）。メニューだと1回ごとに開き直す。
#
# Shift を押した `[` `]` は配列によって `{` `}` として届くので、両方を拾う
# （`+` に対して `=` も拾っているのと同じ手当て）。
#
# **セリフの大きさ（`Ctrl+>` / `Ctrl+<`）と向きを揃える。** 右が増える側で、
# 左が減る側。修飾キーが違うので取り合いにはならない
TONE_THRESHOLD_UP_KEYS = (Qt.Key.Key_BraceRight, Qt.Key.Key_BracketRight)
TONE_THRESHOLD_DOWN_KEYS = (Qt.Key.Key_BraceLeft, Qt.Key.Key_BracketLeft)

# 濃さを増減するキー（本人の指示 2026-08-07）。**`Shift+.` / `Shift+,`。**
#
# 拾う黒と**同じ絵を見ながら往復する**組。どこまで拾うかを決めたあと、
# 敷いた斜線が濃すぎ／薄すぎに見えるのはその場で分かるので、メニューを
# 開き直さずに続けて詰められるほうがいい。
#
# 記号は `>` `<`。**右が増える側**という向きは拾う黒・セリフの大きさと
# 同じで、キーの位置も `]` `[` の隣の段に並ぶ。Shift を押した `.` `,` が
# 配列によって `>` `<` にならないことがあるので、素の側も拾う
# （角括弧と同じ手当て）
TONE_DENSITY_UP_KEYS = (Qt.Key.Key_Greater, Qt.Key.Key_Period)
TONE_DENSITY_DOWN_KEYS = (Qt.Key.Key_Less, Qt.Key.Key_Comma)

# ファイル選択ダイアログとドロップ受け入れで共通の対象。
# assets.sniff_format が見分けられる形式に合わせてある
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
IMAGE_FILE_FILTER = "画像 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;すべてのファイル (*)"

# 縦と横が同時に変わるつまみ。ここでだけ等比かどうかが問題になる
CORNER_HANDLES = ("nw", "ne", "se", "sw")
# **「維持」ではなく「元に戻す」。** 比べる相手は今の形（自由リサイズで
# 既に歪めていることがある）ではなく、`src_px`（元画像の実寸）。歪めた
# あとに Shift で掴むと、今の形を保つのではなく元の比へ跳ねて戻る
# （2026-08-08 に発見。挙動は要件定義 5章の記載どおりで安全側だが、
# 文言が「今の形を保つ」と誤読させていた）
ASPECT_HINT = "Shift キーを押しながらドラッグで元の縦横比に戻す"
ASPECT_HINT_HELD = "元の縦横比に戻しています（Shift）"

# 回転つまみ（丸）を上辺からどれだけ離すか（画面ピクセル）。
# 上辺の「n」のつまみ（一辺 `HANDLE_PX`）と重ならない距離にする。
# 近づけすぎると、大きさを変えるつもりで回してしまう。
#
# 22px では近すぎるとの指摘（本人談 2026-08-05）を受けて 40px にした。
# 判定の幅（`ROTATE_HANDLE_HIT_PX` = 16px）を足しても上辺のつまみとは
# 離れているので、掴み分けを誤らない
ROTATE_HANDLE_GAP_PX = 40.0
# 回転つまみを掴める範囲（画面ピクセル）。印は `HANDLE_PX` のまま。
# しっぽの先端（→ `TAIL_TIP_HANDLE_PX`）と同じく、丸は狙って押しても外れやすい
ROTATE_HANDLE_HIT_PX = 16.0
# Shift を押している間の角度の刻み（度）
ROTATE_STEP_DEG = 15.0
ROTATE_HINT = "ドラッグで回転。Shift キーで 15 度ずつ"

# その場編集に入ったときの案内。
_TEXT_EDIT_KEYS = "Enter で改行、Ctrl+Enter で確定、Esc で取り消し"
TEXT_EDIT_HINT = f"文字を入力してください。{_TEXT_EDIT_KEYS}"

# 案内文の長さの上限（文字数）。
#
# 状態表示のうち案内に使えるのは**約 560px しかない**。右側の常設表示
# （選択中の内容・ページ番号）が 696px を占めるため。実測は横書き用 445px /
# 縦書き用 473px で、どちらも収まる（2026-08-03、窓幅 1280px）。
#
# 一度この幅を超えて **`Ctrl+Enter で確定、Esc で取り消し` が切れて消えた。**
# 操作キーが読めなくなるほうが、説明が足りないより困る。
#
# **文字数は幅の代用でしかない。** 日本語は 1 文字約 13px、英数字は約 7px
# なので、同じ文字数でも中身次第で幅はほぼ倍まで変わる。この上限に収まって
# いても、日本語だけで埋めれば切れる。**案内を書き換えるときは実機で幅を
# 測り直すこと。** offscreen で動くテストでは幅を測れない（フォントが無い）
TEXT_EDIT_HINT_MAX_CHARS = 50

# **縦書きのセリフでも、入力欄は横書きで出る。** Qt に縦書きの入力欄が
# 無いため（`QTextOption` にあるのは左右の向きだけ）。ただし枠の中には
# 確定後の姿を実時間で出す（→ `render._draw_text_editing`）ので、
# 案内は「横書きで入る」の断りではなく**どこを見れば仕上がりが分かるか**
# を指す形にしてある。
#
# 「文字を入力してください」を**置き換える**形にしてあり、足していない。
# 状態表示のうち案内に使えるのは約 560px しかなく（右の常設表示が 696px を
# 占める）、横書き用の案内 445px に足すと **`Ctrl+Enter で確定` 以降が
# 切れて消える**（2026-08-03 に実測）。操作キーのほうが失えない。
# 入力に入った時点で「入力してください」は自明なので、そこを説明に使う
TEXT_EDIT_HINT_VERTICAL = f"縦書きの見た目は枠に出ます。{_TEXT_EDIT_KEYS}"

_HANDLE_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
}

# 移動・大きさ変更で「矩形をそのまま差し替えるだけ」で済む型。
# `PageView._apply_move` / `_apply_resize` が使う（→ そちらのコメントに詳細）。
#
# `getter` は `EditorState` を受けて選択中のその型のオブジェクトを返す関数。
# 型自身を渡さず呼び出し可能にしてあるのは、選ばれているかどうかの判定と
# 取り出しを1回で済ませるため。
#
# コマはどちらにも乗らない（斜めの組・最小サイズなど、大きさ変更・移動
# 特有の制約を持つため）
_MOVE_TARGETS = (
    (lambda s: s.selected_image, ImageObject, "画像"),
    (lambda s: s.selected_text, TextObject, "セリフ"),
    (lambda s: s.selected_sticker, StickerObject, "マーク"),
)
# 大きさ変更は、フキダシも矩形を差し替えるだけで済む
# （しっぽの先端は動かさないままでよい）ので、こちらにだけ足す。
# **移動には足さない。** 動かすときはしっぽの先端も一緒にずらす必要があり
# （`Page.move_balloon`）、単純な `rect = rect.translated(...)` では済まない
_RESIZE_TARGETS = (
    *_MOVE_TARGETS,
    (lambda s: s.selected_balloon, BalloonObject, "フキダシ"),
)


class Drag:
    """1つのドラッグ操作。`PageView` はこれ1つだけを覚えていればよい。

    `mousePressEvent` が `begin()` で作り、ドラッグのあいだ `mouseMoveEvent`
    が `update()` を、離した瞬間に `mouseReleaseEvent` が `commit()` を呼ぶ。
    **新しいドラッグ操作を1つ足すときは、このクラスを1つ足せば済む。**
    以前は press・move・release・下の6つの下見欄の4箇所を漏れなく揃える
    必要があり、1箇所忘れると「掴めるが動かない」ようなテストを書かないと
    気づけない壊れ方をした。

    下の6つは `PageScene` が描画のために直読みする下見欄。**使う種類だけ
    自分の値で上書きする。** ここが `PageScene` ではなく `Drag` にあるのは、
    「今どのドラッグが進行中か」に応じて自動で1つに絞られるようにするため。
    以前は6つとも `PageScene` 側のフィールドで、「同時に意味を持つのは
    1つだけ」を `_reset_drag` でまとめて消す**運用**によって守っていた。
    ここに置けば、`PageScene.active_drag` が1つしか持てない以上、
    2つの下見が同時に生き残ることは構造的に起こらない。
    """

    preview_rect: Rect | None = None
    tail_preview: tuple[str, tuple[float, float]] | None = None
    root_preview: tuple[str, float] | None = None
    slant_preview: tuple[str, float] | None = None
    rotate_preview: tuple[str, float] | None = None
    focus_preview: tuple[str, FocusLines] | None = None
    flow_preview: tuple[str, FlowLines] | None = None

    def update(self, view: PageView, x: float, y: float, event) -> None:
        raise NotImplementedError

    def commit(self, view: PageView) -> None:
        raise NotImplementedError


class CreatePanelDrag(Drag):
    """コマを1つ作る。**大きさは吸着する**（隣のコマの縦横の線に揃えやすく
    するため）。ドラッグと呼べない小さな動きはクリック扱いにして、
    既定の大きさで置く（→ `PageView._apply_create`）。
    """

    def __init__(self, press: tuple[float, float]):
        self.press = press
        self.preview_rect = Rect(press[0], press[1], 0.0, 0.0)

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> CreatePanelDrag:
        return cls((x, y))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        px, py = self.press
        rect = Rect(px, py, x - px, y - py)
        xs, ys = view._candidates(None)
        self.preview_rect = snap_moved_rect(
            rect.normalized(), xs, ys, view._snap_threshold()
        )

    def commit(self, view: PageView) -> None:
        view._apply_create(self.preview_rect, self.press)


class CreateFloatingDrag(Drag):
    """フキダシ・マーク・セリフを1つ作る。ページ直下に置くので、
    コマの上でもコマの外でも作れる（→ `mousePressEvent`）。

    大きさの吸着は**しない**。`CreatePanelDrag` と違い、隣のコマの辺に
    揃える意味が無い。
    """

    def __init__(self, kind: str, press: tuple[float, float]):
        self.kind = kind  # "balloon" / "sticker" / "text"
        self.press = press
        self.preview_rect = Rect(press[0], press[1], 0.0, 0.0)

    @classmethod
    def begin(
        cls, view: PageView, x: float, y: float, tool: str
    ) -> CreateFloatingDrag:
        if tool in BALLOON_TOOLS:
            kind = "balloon"
        elif tool in STICKER_TOOLS:
            kind = "sticker"
        else:
            kind = "text"
        return cls(kind, (x, y))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        px, py = self.press
        self.preview_rect = Rect(px, py, x - px, y - py).normalized()

    def commit(self, view: PageView) -> None:
        if self.kind == "balloon":
            view._apply_create_balloon(self.preview_rect, self.press)
        elif self.kind == "sticker":
            view._apply_create_sticker(self.preview_rect, self.press)
        else:
            view._apply_create_text(self.preview_rect, self.press)


class MoveDrag(Drag):
    """選んでいるものを動かす。型ごとの分かれ道は `PageView._apply_move`
    （画像・セリフ・マークは矩形をそのまま、フキダシ・コマは専用の処理）。
    """

    def __init__(self, object_id: str, origin_rect: Rect, grab: tuple[float, float]):
        self.object_id = object_id
        self.origin_rect = origin_rect
        self.grab = grab
        self.preview_rect = origin_rect

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> MoveDrag:
        # 掴む矩形は `selected_bounds` に任せる。斜めの組なら組の外側が
        # 返るので、片方だけ動く見た目にならない
        return cls(view.state.selected_id, view.state.selected_bounds, (x, y))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        gx, gy = self.grab
        moved = self.origin_rect.translated(x - gx, y - gy)
        xs, ys = view._candidates(view.state.selected_id)
        self.preview_rect = snap_moved_rect(
            moved, xs, ys, view._rect_snap_threshold()
        )

    def commit(self, view: PageView) -> None:
        view._apply_move(self.origin_rect, self.preview_rect)


class ResizeDrag(Drag):
    """8方向のつまみでの大きさ変更。傾き・等比ロック・斜めの組の制約を持つ。"""

    def __init__(self, handle: str, origin_rect: Rect):
        self.handle = handle
        self.origin_rect = origin_rect
        self.preview_rect = origin_rect

    @classmethod
    def begin(cls, view: PageView, handle: str, origin_rect: Rect) -> ResizeDrag:
        return cls(handle, origin_rect)

    def update(self, view: PageView, x: float, y: float, event) -> None:
        rotation = view._selected_rotation()
        xs, ys = view._candidates(view.state.selected_id)
        # 傾いていたら、まずマウスを「傾いていなかったときの位置」に
        # 戻す。こうすると以下のリサイズ計算は今までのままでよい
        px, py = (
            unrotate_point(x, y, self.origin_rect, rotation)
            if rotation != 0.0
            else (x, y)
        )
        sx, sy = snap_point(
            self.handle, px, py, xs, ys, view._rect_snap_threshold()
        )
        minimum = view.state.settings.min_panel_size
        aspect = view._locked_aspect(event)
        # ドラッグ中も出し直す。案内は数秒で消えるため、
        # ゆっくり合わせているうちに見えなくなってしまう
        view._update_aspect_hint(self.handle, view._shift_held(event))
        if aspect > 0.0:
            resized = resize_rect_keep_aspect(
                self.origin_rect, self.handle, sx, sy, minimum, aspect
            )
        else:
            resized = resize_rect(self.origin_rect, self.handle, sx, sy, minimum)
        # 傾いていると、幅を変えたぶんだけ中心が動く。掴んでいない側が
        # 動いて見えないよう、ここで戻す
        resized = keep_anchor(self.origin_rect, resized, self.handle, rotation)
        # 斜めの組は、細いほうが最小幅を割る手前で止める。下見のうちに
        # 押し戻しておけば、離した瞬間に形が飛ぶことがない。
        # **掴んでいるつまみを渡す。** 渡さないと、押し戻す軸と掴んでいる
        # 軸が食い違い、引いていない辺まで動く（→ `clamp_slant_rect`）
        pair = view.state.selected_slant_pair
        if pair is not None:
            resized = clamp_slant_rect(
                pair, resized, view.state.settings, self.handle
            )
        self.preview_rect = resized

    def commit(self, view: PageView) -> None:
        # 掴んだだけで動かさなかったら何もしない。他の型では
        # `_apply_resize` の「同じ矩形なら帰る」で弾かれていたが、セリフは
        # **起点が枠ではなく字の外接矩形**なので（→ `layout.text_frame`）
        # そこを素通りし、見た目が1px も変わらないまま履歴が1手増える
        if self.preview_rect == self.origin_rect:
            return
        view._apply_resize(self.preview_rect)


class RotateDrag(Drag):
    """画像の回転つまみ。Shift を押している間は15度刻み。"""

    def __init__(
        self, image_id: str, origin_rect: Rect, angle_offset: float, rotation: float
    ):
        self.image_id = image_id
        self.origin_rect = origin_rect
        # 掴んだ向きと今の傾きのずれ。つまみのどこを掴んでも押した瞬間に
        # 絵が飛ばないよう、動かすときはここを引く
        self.angle_offset = angle_offset
        self.rotate_preview = (image_id, rotation)

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> RotateDrag:
        image = view.state.selected_image
        offset = view._angle_at(image.rect, x, y) - image.rotation
        return cls(image.id, image.rect, offset, image.rotation)

    def update(self, view: PageView, x: float, y: float, event) -> None:
        angle = view._angle_at(self.origin_rect, x, y) - self.angle_offset
        if view._shift_held(event):
            angle = round(angle / ROTATE_STEP_DEG) * ROTATE_STEP_DEG
        self.rotate_preview = (self.image_id, normalize_angle(angle))

    def commit(self, view: PageView) -> None:
        _, angle = self.rotate_preview
        view._apply_rotate(self.image_id, angle)


class TailDrag(Drag):
    """しっぽの先端をドラッグする。先端そのものでも、見えているしっぽの
    内側のどこを掴んでも同じ扱い（→ `_tail_tip_at` / `_tail_body_at`）。

    押した点と先端がずれていても（＝しっぽの本体を掴んだ場合）、差分を
    覚えておいて先端がその分だけ付いてくるようにする。差分を覚えずに
    先端をマウス位置へ直接合わせると、本体の途中を押しただけで先端が
    瞬間移動して見える。
    """

    def __init__(
        self, balloon_id: str, grab: tuple[float, float], tip: tuple[float, float]
    ):
        self.balloon_id = balloon_id
        self.grab = grab
        self.tail_preview = (balloon_id, tip)

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> TailDrag:
        balloon = view.state.selected_balloon
        tip = balloon.tail.tip
        return cls(balloon.id, (x - tip[0], y - tip[1]), tip)

    def update(self, view: PageView, x: float, y: float, event) -> None:
        gx, gy = self.grab
        self.tail_preview = (self.balloon_id, (x - gx, y - gy))

    def commit(self, view: PageView) -> None:
        _, tip = self.tail_preview
        view._apply_tail(self.balloon_id, tip)


class TailRootDrag(Drag):
    """しっぽの付け根を上下にドラッグする。付け根は輪郭の上を滑るので、
    横位置は高さから決まる（→ `root_y_at`）。**縦だけ見る。**
    """

    def __init__(self, balloon_id: str, root_y: float):
        self.balloon_id = balloon_id
        self.root_preview = (balloon_id, root_y)

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> TailRootDrag:
        balloon = view.state.selected_balloon
        return cls(balloon.id, root_y_at(balloon.rect, y))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        balloon = view.state.selected_balloon
        if balloon is not None:
            self.root_preview = (self.balloon_id, root_y_at(balloon.rect, y))

    def commit(self, view: PageView) -> None:
        _, root_y = self.root_preview
        view._apply_tail_root(self.balloon_id, root_y)


class SlantDrag(Drag):
    """斜めに割ったコマの境界を左右にドラッグする。"""

    def __init__(self, panel_id: str, ratio: float):
        self.panel_id = panel_id
        self.slant_preview = (panel_id, ratio)

    @classmethod
    def begin(cls, view: PageView) -> SlantDrag:
        panel = view.state.selected_panel
        pair = view.state.page.slant_pair_of(panel.id)
        return cls(panel.id, pair.ratio)

    def update(self, view: PageView, x: float, y: float, event) -> None:
        pair = view.state.page.slant_pair_of(self.panel_id)
        if pair is None:
            return
        rect = view.state.page.slant_bounds(pair)
        ratio = clamp_slant_ratio(
            rect, pair.angle, slant_ratio_at(rect, x), view.state.settings
        )
        self.slant_preview = (self.panel_id, ratio)

    def commit(self, view: PageView) -> None:
        _, ratio = self.slant_preview
        view._apply_slant(self.panel_id, ratio)


class FocusDrag(Drag):
    """集中線の中心・内側の空きのつまみ。`kind` に `"focus_center"` /
    `"focus_hole"` のどちらのつまみかを持つ（元の `_mode` の値をそのまま
    引き継いでいる。呼び名としてそのまま通じるため）。
    """

    def __init__(self, kind: str, panel_id: str, focus: FocusLines):
        self.kind = kind
        self.panel_id = panel_id
        self.focus_preview = (panel_id, focus)

    @classmethod
    def begin(cls, view: PageView, kind: str) -> FocusDrag:
        panel, focus = view._scene.focus_of_selection()
        return cls(kind, panel.id, dataclasses.replace(focus))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        panel = view.state.selected_panel
        if panel is None:
            return
        bounds = panel.bounds()
        _, focus = self.focus_preview
        if self.kind == "focus_center":
            focus = dataclasses.replace(focus, center=focus_center_at(bounds, x, y))
        else:
            focus = dataclasses.replace(focus, hole=focus_hole_at(focus, bounds, x))
        self.focus_preview = (self.panel_id, focus)

    def commit(self, view: PageView) -> None:
        panel_id, focus = self.focus_preview
        view._apply_focus(panel_id, focus, self.kind)


class FlowDrag(Drag):
    """流線の向きのつまみ（丸）。**Shift を押している間は15度刻み。**

    刻み方も刻み幅も画像の回転（`RotateDrag`）と同じにしてある。同じ形の
    つまみで同じ操作なので、片方だけ違うと覚え直しが要る（→ 要件定義 6.26）。

    掴んだ点とつまみの位置がずれていても構わない。**距離は使わず向きだけ
    見る**ので、押した瞬間に線が飛ぶことがない。
    """

    def __init__(self, panel_id: str, flow: FlowLines):
        self.panel_id = panel_id
        self.flow_preview = (panel_id, flow)

    @classmethod
    def begin(cls, view: PageView) -> FlowDrag:
        panel, flow = view._scene.flow_of_selection()
        return cls(panel.id, dataclasses.replace(flow))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        panel = view.state.selected_panel
        if panel is None:
            return
        angle = flow_angle_at(panel.bounds(), x, y)
        if view._shift_held(event):
            angle = normalize_angle(round(angle / ROTATE_STEP_DEG) * ROTATE_STEP_DEG)
        _, flow = self.flow_preview
        self.flow_preview = (self.panel_id, dataclasses.replace(flow, angle=angle))

    def commit(self, view: PageView) -> None:
        panel_id, flow = self.flow_preview
        view._apply_flow_angle(panel_id, flow.angle)


class RoughMoveDrag(Drag):
    """ラフ（下敷き）を動かす（→ 要件定義 6.23）。**吸着しない。**

    吸着はコマの縦横の線に揃えるための仕組み。下敷きを合わせる相手は
    絵の中身であって、コマの辺ではない。効かせると、狙った場所の手前で
    勝手に止まる。
    """

    def __init__(self, origin_rect: Rect, grab: tuple[float, float]):
        self.origin_rect = origin_rect
        self.grab = grab
        self.preview_rect = origin_rect

    @classmethod
    def begin(cls, view: PageView, x: float, y: float) -> RoughMoveDrag:
        return cls(view.state.page.rough.rect, (x, y))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        gx, gy = self.grab
        self.preview_rect = self.origin_rect.translated(x - gx, y - gy)

    def commit(self, view: PageView) -> None:
        view._apply_rough_rect(self.origin_rect, self.preview_rect, "ラフの移動")


class RoughResizeDrag(Drag):
    """ラフの大きさを変える。**縦横比は常に保つ**（マークと同じ → 6.14）。

    下敷きは写真なので、比を崩すと元の絵が歪む。歪んだ下敷きをなぞっても
    使いものにならないので、Shift で外せる余地も作らない。写真に余白が
    写り込んでいる場合は、拡大して外へ追い出せば済む。
    """

    def __init__(self, handle: str, origin_rect: Rect, aspect: float):
        self.handle = handle
        self.origin_rect = origin_rect
        self.aspect = aspect
        self.preview_rect = origin_rect

    @classmethod
    def begin(cls, view: PageView, handle: str) -> RoughResizeDrag:
        rough = view.state.page.rough
        return cls(handle, rough.rect, aspect_of(rough.src_px))

    def update(self, view: PageView, x: float, y: float, event) -> None:
        minimum = view.state.settings.min_panel_size
        if self.aspect > 0.0:
            self.preview_rect = resize_rect_keep_aspect(
                self.origin_rect, self.handle, x, y, minimum, self.aspect
            )
        else:
            self.preview_rect = resize_rect(
                self.origin_rect, self.handle, x, y, minimum
            )

    def commit(self, view: PageView) -> None:
        view._apply_rough_rect(
            self.origin_rect, self.preview_rect, "ラフの大きさ変更"
        )


class ToneAreaDrag(Drag):
    """トーンを掛ける範囲（矩形）を引く（→ 要件定義 10.1）。

    **引くのは画像の傾きを外した座標**。矩形は画像に対する割合で持つので、
    傾いた画像でも「絵のどこを囲ったか」が変わらない。マウスの位置は
    `unrotate_point` で毎回戻す（回転を持ち込む境目 → 6.3）。

    **吸着しない。** 揃える相手はコマの辺ではなく絵の中身（ラフと同じ）。

    3つの引き方を1つの型にまとめてある。掴んだ場所で「隅を動かす／全体を
    動かす／新しく囲い直す」に分かれるだけで、離したときにやることは同じ。
    """

    def __init__(
        self,
        image_id: str,
        image_rect: Rect,
        rotation: float,
        frame: Rect,
        origin: Rect | None,
        handle: str | None,
        grab: tuple[float, float],
    ):
        self.image_id = image_id
        self.image_rect = image_rect
        self.rotation = rotation
        # 掴む前に見えていた枠。絞っていなければ画像いっぱいで、隅を掴んだ
        # ときの出発点になる
        self.frame = frame
        # 実際に持っている範囲。**絞っていなければ None。**
        # `frame` と分けてあるのが要で、同じものにすると「画像いっぱいの
        # 矩形を持っている」ことになり、内側を押すたびに移動へ入って
        # **新しく囲い直せなくなる**（実際にそうなった）
        self.origin = origin
        self.handle = handle
        self.grab = grab
        self.preview: Rect | None = origin

    @classmethod
    def begin(
        cls, view: PageView, x: float, y: float, handle: str | None
    ) -> ToneAreaDrag:
        image = view.state.tone_image
        local = unrotate_point(x, y, image.rect, image.rotation)
        return cls(
            image.id,
            image.rect,
            image.rotation,
            view.tone_area_rect(image),
            None if image.tone.area is None else view.tone_area_rect(image),
            handle,
            local,
        )

    def update(self, view: PageView, x: float, y: float, event) -> None:
        lx, ly = unrotate_point(x, y, self.image_rect, self.rotation)
        gx, gy = self.grab
        if self.handle is not None:
            # 隅を掴んでいる。**下限は 1px** ——絵の一部を囲うものなので、
            # コマの最小寸法（吸着の都合で決めた値）を持ち込む理由が無い
            self.preview = resize_rect(self.frame, self.handle, lx, ly, 1.0)
        elif self.origin is not None and self.origin.contains(gx, gy):
            self.preview = self.origin.translated(lx - gx, ly - gy)
        else:
            # 絞っていない画像の上か、範囲の外から引いた。新しく囲い直す
            self.preview = Rect(gx, gy, lx - gx, ly - gy).normalized()

    def commit(self, view: PageView) -> None:
        view._apply_tone_area(self.image_id, self.image_rect, self.origin, self.preview)


class PageScene(QGraphicsScene):
    """1ページぶんの描画。部品を持たず、その場で描く。"""

    def __init__(self, state: EditorState):
        super().__init__()
        self.state = state
        self.renderer = PageRenderer(state)
        # 操作中のドラッグ。下の6つのプロパティはここから読む（→ `Drag`）。
        # `PageView` が press で作り、move のたびに書き換え、release で
        # None に戻す
        self.active_drag: Drag | None = None
        # 分割線の下見。両端の座標で持つ。斜め・横・縦を同じ描き方で扱える。
        # `_mode` を経由しない別系統の操作なので、`active_drag` には乗らない
        self.split_preview: tuple[tuple[float, float], tuple[float, float]] | None = None
        # その場編集中のセリフ。編集中は下地を描かない
        self.editing_text_id: str | None = None
        # 入力欄にいま入っている文字列。縦書きの下見に使う
        # （→ `render.DragPreview.editing_text_content`）
        self.editing_text_content: str | None = None
        self.update_scene_rect()

    # -- ドラッグの下見 ------------------------------------------------------
    #
    # **どれも `active_drag` から読む薄い窓。** 実体を持たないので、
    # ここへ書き込むことはできない（`PageView` は `active_drag` そのものを
    # 差し替える）。使っていない種類のドラッグ中は必ず None になる
    # ——以前は6つのフィールドを別々に持ち、`_reset_drag` でまとめて
    # 消す**運用**で「同時に意味を持つのは1つだけ」を守っていたが、
    # こちらは `active_drag` が1つしか持てない以上、構造上そうなる。

    @property
    def preview_rect(self) -> Rect | None:
        return self.active_drag.preview_rect if self.active_drag else None

    @property
    def tail_preview(self) -> tuple[str, tuple[float, float]] | None:
        return self.active_drag.tail_preview if self.active_drag else None

    @property
    def root_preview(self) -> tuple[str, float] | None:
        return self.active_drag.root_preview if self.active_drag else None

    @property
    def slant_preview(self) -> tuple[str, float] | None:
        return self.active_drag.slant_preview if self.active_drag else None

    @property
    def rotate_preview(self) -> tuple[str, float] | None:
        return self.active_drag.rotate_preview if self.active_drag else None

    @property
    def focus_preview(self) -> tuple[str, FocusLines] | None:
        return self.active_drag.focus_preview if self.active_drag else None

    @property
    def flow_preview(self) -> tuple[str, FlowLines] | None:
        return self.active_drag.flow_preview if self.active_drag else None

    def update_scene_rect(self) -> None:
        size = self.state.page.size
        pad = max(size.w, size.h) * 0.25
        self.setSceneRect(-pad, -pad, size.w + pad * 2, size.h + pad * 2)

    # -- 用紙とコマ --------------------------------------------------------

    def drag_preview(self) -> DragPreview:
        """いまドラッグ中の下見を、描画側へ渡せる形にまとめる。"""
        return DragPreview(
            slant=self.slant_preview,
            tail=self.tail_preview,
            root=self.root_preview,
            rotate=self.rotate_preview,
            focus=self.focus_preview,
            flow=self.flow_preview,
            editing_text_id=self.editing_text_id,
            editing_text_content=self.editing_text_content,
        )

    def selection_rotation(self) -> float:
        """選択枠を描く角度。画像以外は 0。

        回している最中は下見の角度を使う。モデルには離すまで触らないので、
        ここを見ないと絵だけ回って枠が置いていかれる。
        """
        image = self.state.selected_image
        if image is None:
            return 0.0
        if self.rotate_preview is not None and self.rotate_preview[0] == image.id:
            return self.rotate_preview[1]
        return image.rotation

    @staticmethod
    def _apply_rotation(painter: QPainter, rect: Rect, rotation: float) -> None:
        """以降の描画を、`rect` の中心まわりに回す。

        呼ぶ側が `painter.save()` / `restore()` で挟むこと。
        """
        if rotation == 0.0:
            return
        cx, cy = rect.center
        painter.translate(cx, cy)
        painter.rotate(rotation)
        painter.translate(-cx, -cy)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.fillRect(rect, CANVAS_BG)
        self.renderer.draw(painter, self.state.page, self.drag_preview())

    # -- 選択と下書き ------------------------------------------------------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        scale = painter.transform().m11()
        if scale <= 0:
            return

        # ラフを調整している間は、ラフの枠だけを出す（→ 要件定義 6.23）。
        # **選択枠もつまみも出さない。** 掴めないものにつまみが付いていると、
        # 掴めるつもりで押して何も起きないことになる
        if self.state.tool == TOOL_ROUGH:
            self._draw_rough_frame(painter, scale)
            return

        # トーンの範囲を直している間も、その枠だけを出す（→ 要件定義 10.1）。
        # **他のつまみを全部消す**のがこの道具を作った理由そのもので、
        # 残すと画像を選んだだけでつまみが9個並ぶ
        if self.state.tool == TOOL_TONE_AREA:
            self._draw_tone_area_frame(painter, scale)
            return

        bounds = self.state.selected_bounds
        balloon = self.state.selected_balloon
        # 傾いた画像では、枠・つまみ・下書きの矩形をまとめて回す。
        # 絵だけ回して枠が水平のまま残ると、掴む場所と絵の角がズレる
        rotation = self.selection_rotation()
        if bounds is not None and self.preview_rect is None:
            painter.save()
            self._apply_rotation(painter, bounds, rotation)
            self._draw_selection(
                painter, bounds, scale, self._accent(), show_handles=not self.state.is_locked_selection
            )
            if self.state.selected_image is not None:
                self._draw_rotate_handle(painter, bounds, scale)
            if balloon is not None:
                self._draw_balloon_outline_highlight(painter, balloon)
            painter.restore()
            if self.rotate_preview is not None:
                self._draw_angle_hint(painter, bounds, rotation)
        if balloon is not None:
            self._draw_tail_handle(painter, balloon, scale)
        self._draw_slant_handle(painter, scale)
        # 流線のつまみは集中線より**先に**描く。掴む順は集中線が先なので、
        # 描く順は逆にしないと、上に見えているほうが掴めないことになる
        # （→ `mousePressEvent`、要件定義 6.26）
        self._draw_flow_handle(painter, scale)
        self._draw_focus_handles(painter, scale)

        if self.preview_rect is not None:
            painter.save()
            self._apply_rotation(painter, self.preview_rect, rotation)
            accent = self._accent()
            fill = QColor(accent)
            fill.setAlpha(30)
            painter.setPen(cosmetic_pen(accent, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QBrush(fill))
            painter.drawRect(qrect(self.preview_rect))
            self._draw_size_hint(painter, self.preview_rect)
            painter.restore()

        if self.split_preview is not None:
            (x1, y1), (x2, y2) = self.split_preview
            painter.setPen(cosmetic_pen(ACCENT, 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(QLineF(x1, y1, x2, y2))

    def _draw_rough_frame(self, painter: QPainter, scale: float) -> None:
        """ラフの枠とつまみ（要件定義 6.23）。ラフが無いページでは何も出さない。

        動かしている最中は下見の矩形を描く。モデルは離すまで触らないので、
        ここを見ないと絵が付いてこない（他のドラッグと同じ流儀）。
        """
        rough = self.state.page.rough
        if rough is None:
            return
        moving = self.preview_rect is not None
        bounds = self.preview_rect if moving else rough.rect
        self._draw_selection(painter, bounds, scale, ROUGH_ACCENT)
        if moving:
            self._draw_size_hint(painter, bounds)

    def _draw_tone_area_frame(self, painter: QPainter, scale: float) -> None:
        """トーンを掛ける範囲の枠と、×のつまみ（要件定義 10.1）。

        絞っていない画像では**画像の縁いっぱいを枠として出す**。何も出さないと
        「どこから引けばいいのか」が分からず、道具を持ったのに掴めるものが
        無いように見える。
        """
        view = self._page_view()
        image = self.state.tone_image
        if image is None or image.tone is None:
            return

        preview = view.tone_area_preview() if view is not None else None
        bounds = preview if preview is not None else view.tone_area_rect(image)

        painter.save()
        self._apply_rotation(painter, image.rect, image.rotation)
        # 絞っていないときは点線。「今は画像全体が対象」と、枠の見た目で分ける
        dashed = image.tone.area is None and preview is None
        style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        painter.setPen(cosmetic_pen(TONE_ACCENT, 1.5, style))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(qrect(bounds))
        self._draw_cross_handles(painter, bounds, scale)
        painter.restore()

    def _draw_cross_handles(
        self, painter: QPainter, bounds: Rect, scale: float
    ) -> None:
        """×のつまみ（要件定義 10.1）。

        **記号の在庫が尽きているので新しい形を1つ足した**（四角＝大きさ・
        丸＝回転・ひし形＝しっぽの付け根・十字＝集中線の中心）。集中線の
        十字とは 45 度違うだけだが、**同時に画面に出ない**——あちらはコマを
        選んでいるとき、こちらは道具を持っている間だけ。

        **当たり判定は×の線ではなく、今までどおりの正方形**（→ `handle_at`）。
        線そのものを判定にすると掴みにくい。ここで決めるのは見た目だけ。
        """
        size = HANDLE_PX / scale
        half = size / 2.0
        # 太字にするのは、線だけの記号は塗った四角より細く見えるため
        painter.setPen(cosmetic_pen(TONE_ACCENT, 2.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for name, (cx, cy) in handle_positions(bounds).items():
            if len(name) != 2:
                continue  # 隅の4つだけ。辺まで出すと×が8個並んで枠が読めない
            painter.drawLine(QLineF(cx - half, cy - half, cx + half, cy + half))
            painter.drawLine(QLineF(cx + half, cy - half, cx - half, cy + half))

    def _page_view(self):
        """このシーンを出している画面。下見の矩形を借りるためだけに使う。"""
        views = self.views()
        return views[0] if views else None

    def _accent(self) -> QColor:
        """選択枠の色。何を選んでいるかで変える。"""
        if self.state.selected_image is not None:
            return IMAGE_ACCENT
        if self.state.selected_balloon is not None:
            return BALLOON_ACCENT
        if self.state.selected_sticker is not None:
            return STICKER_ACCENT
        if self.state.selected_text is not None:
            return TEXT_ACCENT
        return ACCENT

    def _draw_balloon_outline_highlight(
        self, painter: QPainter, balloon: BalloonObject
    ) -> None:
        """掴んでいるフキダシの縁を、黒から紫へなぞり直す。

        外接矩形の選択枠（`_draw_selection`）だけでは、重なった図形の中で
        どれを掴んでいるのか分かりにくいとの指摘（本人談 2026-08-06）を
        受けて追加した。フキダシの実形そのものを、本体の縁と同じ太さで
        紫になぞることで、縁が紫に変わったように見せる。

        動かしている最中（`preview_rect` が立つ）は呼び出し側で外して
        あるので、ここでは常にモデルどおりの形を描いてよい。しっぽの
        先端・付け根だけを動かしている最中は下見に追随させる
        （`_draw_tail_handle` と同じ流儀）。
        """
        if not balloon.border.visible or balloon.border.width <= 0:
            return
        previewed = self.renderer.with_preview_tail(balloon, self.drag_preview())
        path = self.renderer.balloon_path(previewed)
        pen = QPen(BALLOON_ACCENT, balloon.border.width)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_tail_handle(
        self, painter: QPainter, balloon: BalloonObject, scale: float
    ) -> None:
        """しっぽの先端（丸）と付け根（ひし形）を掴む印。

        角を掴むつまみ（四角）と形を変える。同じ形だと、大きさを変える
        つもりで引っぱってしまう。先端と付け根も互いに別の形にして、
        どちらを動かすのか掴む前に分かるようにする。
        """
        balloon = self.renderer.with_preview_tail(balloon, self.drag_preview())
        if not balloon.tail.enabled:
            return

        size = HANDLE_PX / scale
        painter.setPen(cosmetic_pen(BALLOON_ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))

        tx, ty = balloon.tail.tip
        painter.drawEllipse(QPointF(tx, ty), size / 2.0, size / 2.0)

        root = tail_root_point(balloon, self.state.balloon_settings)
        if root is None:
            return
        half = size / 2.0
        painter.drawPolygon(
            polygon_of(
                (
                    (root[0], root[1] - half),
                    (root[0] + half, root[1]),
                    (root[0], root[1] + half),
                    (root[0] - half, root[1]),
                )
            )
        )

    def slant_handle(self) -> tuple[float, float] | None:
        """斜めの境界をつまむ位置。選んでいなければ None。

        描く側と掴む側で同じ答えが要るので、1箇所にまとめてある。
        ずらしている最中は下見の位置に付いてくる。

        **どちらかがロックされていれば None。** 境界のドラッグも本体の
        形を変える操作なので、移動・大きさ変更と同じ扱いで止める
        （→ 要件定義 6.17）。ここで None を返せば、描画と掴む判定の
        両方が一度に止まる。
        """
        pair = self.state.selected_slant_pair
        if pair is None or self.state.is_locked_selection:
            return None
        ratio = pair.ratio
        if self.slant_preview is not None and self.slant_preview[0] in pair.members():
            ratio = self.slant_preview[1]
        return slant_handle_point(self.state.page.slant_bounds(pair), ratio)

    def _draw_slant_handle(self, painter: QPainter, scale: float) -> None:
        """境界をずらすつまみ。左右向きの矢羽根で描く。

        角の四角つまみ・しっぽのひし形と形を変える。同じ形だと、
        大きさを変えるつもりで境界を引っぱってしまう。
        """
        point = self.slant_handle()
        if point is None:
            return
        size = HANDLE_PX / scale
        half = size / 2.0
        x, y = point
        painter.setPen(cosmetic_pen(ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawPolygon(
            polygon_of(
                (
                    (x - size, y),
                    (x - half, y - half),
                    (x + half, y - half),
                    (x + size, y),
                    (x + half, y + half),
                    (x - half, y + half),
                )
            )
        )

    def focus_of_selection(self) -> tuple[Panel, FocusLines] | None:
        """つまみを出す相手。選択中のコマと、そこに入っている集中線。

        動かしている最中は下見の値を返す。描く側と掴む側で同じ答えが要る
        ので、1箇所にまとめてある（`slant_handle` と同じ）。
        """
        panel = self.state.selected_panel
        if panel is None or panel.focus_lines is None:
            return None
        focus = panel.focus_lines
        if self.focus_preview is not None and self.focus_preview[0] == panel.id:
            focus = self.focus_preview[1]
        return (panel, focus)

    def flow_of_selection(self) -> tuple[Panel, FlowLines] | None:
        """つまみを出す相手。選択中のコマと、そこに入っている流線。

        動かしている最中は下見の値を返す（`focus_of_selection` と同じ）。
        """
        panel = self.state.selected_panel
        if panel is None or panel.flow_lines is None:
            return None
        flow = panel.flow_lines
        if self.flow_preview is not None and self.flow_preview[0] == panel.id:
            flow = self.flow_preview[1]
        return (panel, flow)

    def flow_angle_handle(self) -> tuple[float, float] | None:
        found = self.flow_of_selection()
        if found is None:
            return None
        panel, flow = found
        return flow_handle_point(flow, panel.bounds())

    def focus_center_handle(self) -> tuple[float, float] | None:
        found = self.focus_of_selection()
        if found is None:
            return None
        panel, focus = found
        return focus_center_point(focus, panel.bounds())

    def focus_hole_handle(self) -> tuple[float, float] | None:
        found = self.focus_of_selection()
        if found is None:
            return None
        panel, focus = found
        return focus_hole_point(focus, panel.bounds())

    def _draw_focus_handles(self, painter: QPainter, scale: float) -> None:
        """集中線の中心（十字）と、内側の空き（四角）のつまみ。

        **中心は十字。** 丸は画像の回転、ひし形はしっぽの付け根、四角は
        大きさ、矢羽根は斜めの境界で既に使っている。形を分けておかないと、
        掴む前にどれが動くのか分からない。

        空きのほうは**四角**にする。こちらは大きさを変える操作なので、
        既にある四角の意味と揃う。
        """
        found = self.focus_of_selection()
        if found is None:
            return
        panel, focus = found
        bounds = panel.bounds()
        size = HANDLE_PX / scale
        half = size / 2.0

        painter.setPen(cosmetic_pen(ACCENT, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = focus_center_point(focus, bounds)
        painter.drawLine(QLineF(cx - size, cy, cx + size, cy))
        painter.drawLine(QLineF(cx, cy - size, cx, cy + size))

        hx, hy = focus_hole_point(focus, bounds)
        painter.setPen(cosmetic_pen(ACCENT, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawRect(QRectF(hx - half, hy - half, size, size))

    def _draw_flow_handle(self, painter: QPainter, scale: float) -> None:
        """流線の向きのつまみ（丸）。中心から軸を1本引いて出す。

        **丸は画像の回転から借りた形**（要件定義 6.26）。記号の在庫が
        尽きていたが、どちらも「掴んで回す」なので意味は一致している。

        軸を添えるのも回転つまみと同じ理由で、線が無いと**どこを中心に
        回るのか**が分からない。流線では軸そのものが線の向きを表すので、
        つまみを掴む前に向きが読める。
        """
        found = self.flow_of_selection()
        if found is None:
            return
        panel, flow = found
        bounds = panel.bounds()
        cx, cy = bounds.center
        hx, hy = flow_handle_point(flow, bounds)
        size = HANDLE_PX / scale

        painter.setPen(cosmetic_pen(ACCENT, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QLineF(cx, cy, hx, hy))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QPointF(hx, hy), size / 2.0, size / 2.0)

    def _draw_selection(
        self,
        painter: QPainter,
        bounds: Rect,
        scale: float,
        color: QColor = ACCENT,
        *,
        show_handles: bool = True,
    ) -> None:
        painter.setPen(cosmetic_pen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(qrect(bounds))

        # コマの選択枠だけ、もう1本内側に添えて二重線にする。
        # 画像・フキダシ・ステッカー・テキスト・ラフは対象外（それぞれの
        # 色で一重線のまま）
        if color == ACCENT:
            gap = PANEL_SELECTION_DOUBLE_GAP_PX / scale
            inner = Rect(
                bounds.x + gap, bounds.y + gap, bounds.w - 2 * gap, bounds.h - 2 * gap
            )
            if inner.w > 0 and inner.h > 0:
                painter.drawRect(qrect(inner))

        # ロックしたコマではつまみを出さない（→ 要件定義 6.17）。
        # 出したまま効かないのが一番たちが悪く、掴めないことは見た目で
        # 先に分かるほうがよい
        if not show_handles:
            return

        size = HANDLE_PX / scale
        painter.setPen(cosmetic_pen(color, 1.2))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        for cx, cy in handle_positions(bounds).values():
            painter.drawRect(QRectF(cx - size / 2, cy - size / 2, size, size))

    def _draw_rotate_handle(
        self, painter: QPainter, bounds: Rect, scale: float
    ) -> None:
        """画像を回すつまみ（丸）。上辺の外に、軸を1本添えて出す。

        **形を四角にしない。** 8方向のつまみと同じ形だと、大きさを変える
        つもりで回してしまう。しっぽの先端（丸）と同じ理由（→ `_draw_tail_handle`）。

        呼ぶ側が painter を回してあるので、ここでは真上に置くだけでよい。
        傾いていれば絵と一緒に回った位置に出る。
        """
        size = HANDLE_PX / scale
        cx = bounds.center[0]
        top = bounds.y - ROTATE_HANDLE_GAP_PX / scale
        painter.setPen(cosmetic_pen(IMAGE_ACCENT, 1.2))
        # 軸。どの辺から生えているつまみなのかが、線が無いと分からない
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QLineF(cx, bounds.y, cx, top))
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QPointF(cx, top), size / 2.0, size / 2.0)

    def _draw_angle_hint(
        self, painter: QPainter, bounds: Rect, rotation: float
    ) -> None:
        """回している最中の角度を、その場に出す。

        文字は倍率の影響を受けないよう、変換を外してから描く（寸法表示と同じ）。
        何度傾いているか分からないまま確定するのを防ぐためのものなので、
        **回しているあいだだけ**出す。
        """
        cx, cy = bounds.center
        # 回した後の上辺の中央に添える。回す前の位置に出すと、大きく
        # 傾けたときに数字だけ絵から離れて残る
        hx, hy = rotate_point(cx, bounds.y, cx, cy, rotation)
        point = painter.transform().map(QPointF(hx, hy))
        painter.save()
        painter.resetTransform()
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(QPointF(point.x() + 8, point.y() - 8), f"{rotation:.0f}°")
        painter.restore()

    def _draw_size_hint(self, painter: QPainter, rect: Rect) -> None:
        """操作中のコマの寸法を、その場に px で出す。

        文字は表示倍率の影響を受けないよう、変換を外してから描く。
        位置だけは外す前の変換で求めておく。
        """
        corner = painter.transform().map(QPointF(rect.x, rect.y))
        painter.save()
        painter.resetTransform()
        painter.setPen(QPen(QColor("#FFFFFF")))
        painter.drawText(
            QPointF(corner.x() + 4, corner.y() - 6), f"{rect.w:.0f} × {rect.h:.0f} px"
        )
        painter.restore()


class ConfirmHintItem(QGraphicsItem):
    """入力欄のすぐ下に出す「確定」の目印。

    **Enter は改行なので、押しても入力から抜けられない。** 確定は Ctrl+Enter
    だが、それが分からないと「閉じる手段が無い」と感じてしまう。押し方を
    その場に出しておけば、状態表示まで目を動かさずに済む。

    押しても確定する。ただし**自分では受け取らない**
    （`setAcceptedMouseButtons(NoButton)`）。クリックは下の画面へ素通りし、
    「画面を触ったら確定」という既にある道（`PageView.mousePressEvent` →
    `finish_text_edit`）に乗る。自分で受け取ると、確定処理の途中で自分が
    シーンから外されることになって危うい。

    表示倍率を無視して常に同じ大きさで描く（`ItemIgnoresTransformations`）。
    これは作品の一部ではなく画面の道具なので、拡大しても太らないほうがよい。
    コマ枠に `cosmetic_pen` を使わないのと同じ線引き。
    """

    PADDING_X = 8.0
    PADDING_Y = 4.0
    # 入力欄の下端との間隔。表示倍率を無視して描くので、これは**画面上の
    # 画素数**であって作品の座標ではない（拡大しても間隔は変わらない）。
    # 近すぎて入力中の文字に被って見えたため 6 から広げた（2026-08-03）
    GAP = 26.0
    LABEL = "確定（Ctrl+Enter）"

    BG = QColor("#1E88E5")
    FG = QColor("#FFFFFF")

    def __init__(self, parent: QGraphicsItem):
        super().__init__(parent)
        self._font = QFont()
        self._font.setPixelSize(12)
        metrics = QFontMetricsF(self._font)
        self._w = metrics.horizontalAdvance(self.LABEL) + self.PADDING_X * 2
        self._h = metrics.height() + self.PADDING_Y * 2
        self._baseline = self.PADDING_Y + metrics.ascent()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1001)  # 入力欄（1000）より上

    def total_height(self) -> float:
        """目印が下に占める高さ。**手前の間隔を含む。**

        下へ続けて札を積むときの足し幅（→ `SizeKeysHintItem`）。
        """
        return self.GAP + self._h

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, self.GAP, self._w, self._h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        box = QRectF(0.0, self.GAP, self._w, self._h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.BG))
        painter.drawRoundedRect(box, 3.0, 3.0)

        painter.setFont(self._font)
        painter.setPen(QPen(self.FG))
        painter.drawText(
            QPointF(self.PADDING_X, self.GAP + self._baseline), self.LABEL
        )


class SizeKeysHintItem(QGraphicsItem):
    """確定の目印の下に積む、文字の大きさを変えるキーの看板。

    **キーの綴りは `menus.TextMenu` が持つものと同じでなければならない。**
    看板が食い違うと、読んだとおりに押しても何も起きない。テストで
    メニュー側の `QAction` と突き合わせてある。

    **`Ctrl+>` が大きく、`Ctrl+<` が小さく。** 右が増える側という並びの
    約束で、トーンの濃さと向きを揃えてある（→ 要件定義 6.27）。
    山括弧なのは、**向きが記号の形そのものに出ている**ため。角括弧 → 句読点
    → 山括弧と 2026-09-05 に2度動かしている（→ `menus.TextMenu`）。

    `ConfirmHintItem` の子にしてある。あちらは表示倍率を無視する
    （`ItemIgnoresTransformations`）ので、**その子は倍率の掛かっていない
    座標系に並ぶ**。同じ画面画素の単位で積めるので、間隔を px と画素で
    混ぜずに済む。倍率を無視するのはこれも同じ理由で、**キーの案内は
    作品の一部ではなく画面の道具**だから。

    押しても自分では受け取らない（`NoButton`）。クリックは下の画面へ
    素通りし、「画面を触ったら確定」という既にある道に乗る。
    """

    PADDING_X = 8.0
    PADDING_Y = 4.0
    # 確定の目印の下端との間隔（画面の画素）。目印と1組に見えるよう、
    # 入力欄との間（`ConfirmHintItem.GAP` ＝ 26）より詰める
    GAP = 6.0
    LABEL = "文字拡大 Ctrl+>　文字縮小 Ctrl+<"

    # 薄い橙。確定の目印（青）と役目が違うことを色で分ける——
    # あちらは「入力から抜ける」、こちらは「入力の外にある書式の操作」
    BG = QColor("#FFE0B2")
    BORDER = QColor("#FFB74D")
    FG = QColor("#E65100")

    def __init__(self, parent: ConfirmHintItem):
        super().__init__(parent)
        self._font = QFont()
        self._font.setPixelSize(12)
        metrics = QFontMetricsF(self._font)
        self._w = metrics.horizontalAdvance(self.LABEL) + self.PADDING_X * 2
        self._h = metrics.height() + self.PADDING_Y * 2
        self._baseline = self.PADDING_Y + metrics.ascent()

        # 親が倍率を無視するので、自分に付ける必要はない（付けても無害だが、
        # 「親の座標系に並んでいる」という意図が読めなくなる）
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1001)
        self.setPos(0.0, parent.total_height())

    def _box(self) -> QRectF:
        return QRectF(0.0, self.GAP, self._w, self._h)

    def boundingRect(self) -> QRectF:
        # 枠線が半分はみ出すぶんを見ておく
        return self._box().adjusted(-1.0, -1.0, 1.0, 1.0)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        box = self._box()
        painter.setPen(QPen(self.BORDER, 1.0))
        painter.setBrush(QBrush(self.BG))
        painter.drawRoundedRect(box, 3.0, 3.0)

        painter.setFont(self._font)
        painter.setPen(QPen(self.FG))
        painter.drawText(QPointF(self.PADDING_X, self.GAP + self._baseline), self.LABEL)


class ModeLabelItem(QGraphicsItem):
    """入力欄の**下**に添える【テキスト入力モード】の札。

    **これから打つ書体・大きさ・太さ、そのままで描く。** 札そのものが
    見本を兼ねる。空のセリフを打ち始めるとき、入力欄には1文字も無いので
    「どの書体の何 px で入るのか」が画面のどこにも出ていなかった。
    状態表示に書体名を文字で足す手もあるが、**名前を読んでも大きさは
    分からない**（本人の指摘 2026-09-05）。

    `ConfirmHintItem` と違い、**表示倍率を無視しない**（`ItemIgnores-
    Transformations` を付けない）。見本である以上、拡大すれば札も同じだけ
    大きくならないと「実際の大きさ」を示したことにならない。同じ理由で
    間隔も余白も px 固定にせず、字の高さに対する割合で決める。

    押しても自分では受け取らない（`NoButton`）。クリックは下の画面へ
    素通りし、「画面を触ったら確定」という既にある道に乗る——これは
    `ConfirmHintItem` と同じ線引き。

    **入力欄の上ではなく下に置く。** 上へ出すとフキダシの輪郭と重なり
    やすい（本人の指摘 2026-09-05）。セリフはフキダシの中に置くのが普通
    なので、入力欄の真上は輪郭が通っている確率が高い。
    """

    LABEL = "【テキスト入力モード】"

    # 字の高さに対する割合。px 固定にすると、大きい書体で札だけ詰まって見える
    PADDING_X_RATIO = 0.25
    PADDING_Y_RATIO = 0.12
    GAP_RATIO = 0.35

    BG = QColor("#E3F2FD")
    BORDER = QColor("#1E88E5")
    FG = QColor("#0D47A1")

    def __init__(self, parent: QGraphicsItem, font: QFont):
        super().__init__(parent)
        self._font = QFont(font)
        metrics = QFontMetricsF(self._font)

        line = metrics.height()
        pad_x = line * self.PADDING_X_RATIO
        pad_y = line * self.PADDING_Y_RATIO
        self._gap = line * self.GAP_RATIO
        self._w = metrics.horizontalAdvance(self.LABEL) + pad_x * 2
        self._h = line + pad_y * 2
        self._text_pos = QPointF(pad_x, pad_y + metrics.ascent())

        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(1001)  # 入力欄（1000）より上

    @property
    def gap(self) -> float:
        """札の前後に空ける間隔。**字の高さから決まる**ので定数では持てない。

        縦書きのときに入力欄を枠からどれだけ下げるかにも、この値を使う
        （→ `TextEditorItem._place_in`）。
        """
        return self._gap

    def total_height(self) -> float:
        """札が下に占める高さ。**手前の間隔を含む。**

        確定の目印をどこへ置くかは、この値で決まる
        （→ `TextEditorItem._place_hints`）。札の高さだけを返すと、呼ぶ側が
        間隔を足し忘れて札と目印がくっつく。
        """
        return self._gap + self._h

    def _box(self) -> QRectF:
        """入力欄の下端から、間隔ぶん離した位置。**下へ伸びる。**"""
        return QRectF(0.0, self._gap, self._w, self._h)

    def boundingRect(self) -> QRectF:
        # 枠線が半分はみ出すぶんを見ておく
        return self._box().adjusted(-1.0, -1.0, 1.0, 1.0)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        box = self._box()
        radius = self._h * 0.2
        painter.setPen(QPen(self.BORDER, 1.0))
        painter.setBrush(QBrush(self.BG))
        painter.drawRoundedRect(box, radius, radius)

        painter.setFont(self._font)
        painter.setPen(QPen(self.FG))
        painter.drawText(box.topLeft() + self._text_pos, self.LABEL)


class TextEditorItem(QGraphicsTextItem):
    """その場編集の入力欄。

    画面に重ねた別の部品ではなく、シーンに置いた項目にしてある。
    拡大縮小や画面移動に自動で付いてくるので、位置合わせを自分で
    やらずに済む（要件定義 6.5「画面上でその場編集」）。

    **縦書きのセリフでは、枠の中に確定後の姿が出ている**
    （→ `render._draw_text_editing`）。そこへ重ねると二重になって
    両方読めないので、入力欄のほうを枠の下へ逃がす（→ `_place_in`）。
    """

    def __init__(self, view: PageView, text: TextObject):
        super().__init__(text.content)
        self._view = view
        self._closing = False
        self._vertical = text.direction == "vertical"

        # 座標系が px なので、フォントの画素数をそのまま渡せる。
        # mm だった頃は、小さすぎて丸められるのを避けるために 20 倍で作って
        # 1/20 に縮めていた（旧 TEXT_FONT_SCALE）
        self.setFont(text_font(text.font))
        self.setDefaultTextColor(QColor("#000000"))

        option = self.document().defaultTextOption()
        option.setAlignment(
            TEXT_ALIGN_FLAGS.get(text.align, Qt.AlignmentFlag.AlignHCenter)
        )
        # 折り返さない。確定後の描画と食い違うと、入力中と結果で
        # 改行位置が変わって驚く（要件定義 9章: 手動改行のみ）
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        self.document().setDefaultTextOption(option)
        self.document().setDocumentMargin(0.0)
        self.setTextWidth(text.rect.w)

        self.setZValue(1000)
        # 枠から下げる幅に札の間隔を使うので、置く前に作る
        self._mode_label = ModeLabelItem(self, text_font(text.font))
        self._place_in(text.rect)

        self._confirm = ConfirmHintItem(self)
        # 目印の子。位置は目印の座標系で決まるので、置き直しは要らない
        self._size_keys = SizeKeysHintItem(self._confirm)
        self._place_hints()
        # 行が増えると入力欄の下端が下がる。札も目印も付いていく
        self.document().contentsChanged.connect(self._on_contents_changed)

        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)

    def _place_in(self, rect: Rect) -> None:
        """入力欄を置く。**縦書きのときだけ枠の外へ逃がす。**

        横書きは確定後の描画（上下中央）にそのまま重ねる。入力中と確定後で
        字が 1px も動かないのが一番よい（要件定義 6.5）。

        縦書きは枠の中に**確定後の姿**が出ているので、重ねられない。枠の
        下へ下ろす。空ける幅は札と同じ間隔にしてある——**字の大きさから
        決まる**ので、定数では決められない（大きい書体で詰まって見える）。
        """
        if self._vertical:
            top = rect.y + rect.h + self._mode_label.gap
        else:
            height = self.boundingRect().height()
            top = rect.y + max(0.0, (rect.h - height) / 2.0)
        self.setPos(rect.x, top)

    def _on_contents_changed(self) -> None:
        self._place_hints()
        self.publish_preview()

    def publish_preview(self) -> None:
        """打っている内容を、縦書きの下見として画面へ渡す。

        横書きでは渡さない。入力欄が枠に重なったまま同じ字を出すことに
        なり、二重に見えるだけになる（→ `render._draw_text_editing`）。
        """
        if self._vertical:
            self._view.update_text_preview(self.toPlainText())

    def _place_hints(self) -> None:
        """札と確定の目印を、入力欄の下端へ付け直す。

        **上から 入力欄 →【テキスト入力モード】の札 → 確定の目印 →
        文字の大きさのキー**の順。いちばん下の看板は目印の子なので、
        目印を動かせば付いてくる（ここで置き直すのは上の2つだけ）。
        札を入力欄の上に出すとフキダシの輪郭と重なりやすいので、両方とも
        下へ回した（本人の指摘 2026-09-05）。

        **2つは長さの単位が違う。** 札は書体と同じ px（表示倍率で伸び縮み
        する）、目印は画面の画素（倍率を無視する）。目印の位置に札の高さを
        足しているのは、**足す側が px だから**——目印は自分の内側で画面
        画素ぶんの間隔をさらに空ける。
        """
        box = self.boundingRect()
        self._mode_label.setPos(box.left(), box.bottom())
        self._confirm.setPos(
            box.left(), box.bottom() + self._mode_label.total_height()
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._view.finish_text_edit(commit=False)
            event.accept()
            return
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Enter だけなら改行。確定は Ctrl+Enter
            self._view.finish_text_edit(commit=True)
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # 取り外す最中の focusOut で呼び戻されないようにする
        if not self._closing:
            self._view.finish_text_edit(commit=True)

    def close_editor(self) -> str:
        """入力内容を返して、自分を畳む。"""
        self._closing = True
        return self.toPlainText()


class PageView(QGraphicsView):
    """マウスとキーの受け口。当たり判定はシーンの px 空間で行う。"""

    # 右クリックされた（シーンの x, y, 画面上の位置）。
    # メニューの中身は `MainWindow` が組む。項目の実体（QAction）は
    # メニューバーのものを使い回すので、持ち主のところで組むほうが早い
    context_menu_requested = Signal(float, float, QPoint)

    def __init__(self, state: EditorState):
        # Qt の初期化より先に属性を持たせない（基底の __init__ が済むまで代入できない）
        scene = PageScene(state)
        super().__init__(scene)
        self.state = state
        self._scene = scene

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        # ここで背景ブラシを設定してはいけない。設定すると Qt はビュー側で
        # 背景を塗って終わりにし、シーンの drawBackground を呼ばなくなる
        # （＝用紙もコマも描かれない）

        self._space_held = False
        self._pan_from: QPointF | None = None
        # 状態表示に出している案内。同じ文を出し続けないための控え
        self._hint_shown: str | None = None
        self._text_editor: TextEditorItem | None = None
        # 今開いている入力欄が「置いたばかりのセリフ」のものか。
        # 空のまま閉じた・Esc で取り消したときに、追加ごと取り消すか
        # どうかの判断に使う（→ `finish_text_edit`）
        self._text_editor_is_new = False
        # 押される直前に選ばれていたもの。ダブルクリックの巡回で使う
        # （→ `mouseDoubleClickEvent`、要件定義 6.25）
        self._selected_before_press: str | None = None

        state.changed.connect(self._on_model_changed)
        state.selection_changed.connect(self.viewport().update)
        state.tool_changed.connect(self._on_tool_changed)

        self.scale(2.2, 2.2)  # A4 が画面に収まる程度の初期倍率

    # -- 便利 --------------------------------------------------------------

    @property
    def _drag(self) -> Drag | None:
        """進行中のドラッグ。実体は `PageScene.active_drag` にある。

        描画に要るのはシーン側なので実体はそちらに置き、こちらは
        読み書きの窓だけを持つ。**2箇所に同じ値を持たせて食い違わせない**
        ための形で、`self._drag = ...` と書けるのは今まで通り
        """
        return self._scene.active_drag

    @_drag.setter
    def _drag(self, value: Drag | None) -> None:
        self._scene.active_drag = value

    @property
    def view_scale(self) -> float:
        return self.transform().m11()

    def _scene_px(self, event) -> tuple[float, float]:
        point = self.mapToScene(event.position().toPoint())
        return point.x(), point.y()

    def _snap_threshold(self) -> float:
        return SNAP_PX / self.view_scale

    def _selected_rotation(self) -> float:
        """選択中の画像の傾き。画像以外を選んでいるときは 0。"""
        image = self.state.selected_image
        return 0.0 if image is None else image.rotation

    def _rect_snap_threshold(self) -> float:
        """移動・リサイズで使う吸着の距離。**傾いた画像では 0**（吸着しない）。

        吸着は他のコマの縦横の線に吸い付く仕組みなので、傾いた絵に
        効かせても意味を成さない（→ 要件定義 6.3）。コマを作るときの
        吸着まで止めないよう、ここは移動とリサイズからだけ呼ぶ。
        """
        if self._selected_rotation() != 0.0:
            return 0.0
        return self._snap_threshold()

    def _candidates(self, exclude_id: str | None):
        return snap_candidates(self.state.page, exclude_id, self.state.settings)

    def _on_model_changed(self) -> None:
        # **ドラッグ中に Undo/Redo などでモデルが変わったら、そのドラッグは
        # 打ち切る。** マウスを掴んだまま Ctrl+Z を押すと、掴んでいた対象が
        # 消える（直前の手が「配置」だった場合など）ことがある。掴んだまま
        # 離すと、選び直された id で `page.panel()` などが KeyError を
        # 投げていた（2026-08-08 に発見）。通常のドラッグは確定まで
        # `state.changed` を発火しないので、ここが働くのは Undo/Redo など
        # 外からモデルが変わった場合だけ
        self._reset_drag()
        self._scene.update_scene_rect()
        self.viewport().update()

    def _on_tool_changed(self) -> None:
        self._scene.split_preview = None
        self._reset_drag()
        # 道具ごとに形が変わるので、次に動かすまで前の形が残らないようにする
        self.viewport().unsetCursor()
        self.viewport().update()

    def _reset_drag(self) -> None:
        self._drag = None
        self._hint_shown = None

    def fit_page(self) -> None:
        page = self.state.page
        self.fitInView(
            QRectF(
                -FIT_MARGIN_PX,
                -FIT_MARGIN_PX,
                page.size.w + FIT_MARGIN_PX * 2,
                page.size.h + FIT_MARGIN_PX * 2,
            ),
            Qt.AspectRatioMode.KeepAspectRatio,
        )

    # -- 拡大縮小・画面移動 ------------------------------------------------

    def zoom_percent(self) -> float:
        """いまの表示倍率（%）。**100% でシーンの 1px が画面の 1px。**

        画面の物理的な解像度は見ない。この道具の出力はウェブで読む絵で、
        紙に刷ったときの大きさを基準にしても意味がない（要件定義 1章）。
        「100% ＝ 書き出した PNG を等倍で見たときの見え方」になる。
        """
        return self.view_scale * 100.0

    def zoom_by(self, factor: float, *, at_mouse: bool = True) -> bool:
        """表示倍率を `factor` 倍する。上下限で止める。変わったら True。

        ホイールならマウスの位置を、キーなら画面の中心を動かさない。
        キーで押したときにカーソルの下を軸にすると、画面外へ飛んでいく。
        """
        target = self.view_scale * factor
        clamped = min(max(target, MIN_VIEW_SCALE), MAX_VIEW_SCALE)
        factor = clamped / self.view_scale
        if abs(factor - 1.0) < 1e-9:
            return False

        previous = self.transformationAnchor()
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
            if at_mouse
            else QGraphicsView.ViewportAnchor.AnchorViewCenter
        )
        self.scale(factor, factor)
        self.setTransformationAnchor(previous)
        self.state.message.emit(f"表示倍率 {self.zoom_percent():.0f}%")
        return True

    def zoom_in(self, *, at_mouse: bool = False) -> bool:
        return self.zoom_by(KEY_ZOOM_STEP, at_mouse=at_mouse)

    def zoom_out(self, *, at_mouse: bool = False) -> bool:
        return self.zoom_by(1.0 / KEY_ZOOM_STEP, at_mouse=at_mouse)

    def wheelEvent(self, event) -> None:
        """ホイールは拡大・縮小に割り当てる。

        画面の上下移動はスペース+ドラッグと中ボタン+ドラッグで足りる。
        文字の細部を見るために倍率を変える回数のほうが、ずっと多い。
        """
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        self.zoom_by(WHEEL_ZOOM_STEP if delta > 0 else 1.0 / WHEEL_ZOOM_STEP)
        event.accept()

    def keyPressEvent(self, event) -> None:
        # **入力中は1つも横取りしない。** キー入力はまずこの部品に届くので、
        # ここで拾うと入力欄まで下りず、文字として打てないキーができる。
        # Esc（取り消し）と Ctrl+Enter（確定）は入力欄自身が受け取る
        if self.is_editing_text:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._select_parent()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # 選択中のセリフを打ち始める。ダブルクリックより速い
            text = self.state.selected_text
            if text is not None:
                self.begin_text_edit(text.id)
                event.accept()
                return
        # どこまでを黒と見るかを連打で合わせる（→ 要件定義 6.27）。
        #
        # **`+` / `-` には割り当てない。** あちらは拡大縮小で、拾い具合を
        # 見るときこそ拡大したい。意味を差し替えると、いちばん要るときに
        # ズームが使えなくなる（→ 7章）。
        #
        # **トーンが入っていなければ素通りさせる。** そのときは何も起きない
        # キーとして下へ流れるだけで、他の操作を塞がない
        if key in TONE_THRESHOLD_UP_KEYS and self.state.step_tone_threshold(1):
            event.accept()
            return
        if key in TONE_THRESHOLD_DOWN_KEYS and self.state.step_tone_threshold(-1):
            event.accept()
            return

        # 濃さも同じ扱いで連打できる。**白抜きのときは素通りさせる**——
        # 白く塗るだけなので濃さは効かず、メニューでもグレーにしてある
        # （→ `ToneMenu.refresh`）。ここで値だけ動かすと、状態表示に段数が
        # 出るのに絵が変わらず、変わらない理由を探すことになる（→ 6.12）
        if self._tone_density_enabled:
            if key in TONE_DENSITY_UP_KEYS and self.state.step_tone_density(1):
                event.accept()
                return
            if key in TONE_DENSITY_DOWN_KEYS and self.state.step_tone_density(-1):
                event.accept()
                return

        if key in ZOOM_IN_KEYS:
            self.zoom_in()
            event.accept()
            return
        if key in ZOOM_OUT_KEYS:
            self.zoom_out()
            event.accept()
            return
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def _tone_density_enabled(self) -> bool:
        """濃さのキーが効くか。メニュー側のグレーと同じ条件（→ `ToneMenu.refresh`）。"""
        tone = self.state.selected_tone
        return tone is not None and tone.kind != TONE_KIND_WHITE

    def _select_parent(self) -> None:
        """Esc。画像を選んでいれば入っているコマへ、そうでなければ選択解除。

        踏み込んだぶんを1段ずつ戻す。いきなり選択が消えると、
        コマを選び直す操作が余計に要る。
        """
        self._reset_drag()
        image = self.state.selected_image
        if image is None:
            self.state.select(None)
            return
        panel = self.state.page.panel_of_image(image.id)
        self.state.select(panel.id if panel is not None else None)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # -- マウス ------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if self._space_held or event.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        # **押して選び直す前の選択を控える。** ダブルクリックの巡回は
        # 「今選ばれているものの次」で位置を決めるが、2回目のダブルクリックの
        # 手前には必ず普通の押下が挟まり、そこで手前のコマに選び直されて
        # しまう。控えが無いと巡回が2つめと3つめを往復する（→ 6.25）
        self._selected_before_press = self.state.selected_id

        x, y = self._scene_px(event)
        tool = self.state.tool

        # 入力中に画面を触ったら、そこで確定してから次の操作へ移る
        self.finish_text_edit(commit=True)

        if tool in SPLIT_TOOLS:
            self._apply_split(x, y)
            event.accept()
            return

        # ラフの調整（→ 要件定義 6.23）。**他の判定より先に、ここで打ち切る。**
        # 一番下に敷いてあるものなので、コマや吹き出しの判定を通すと、
        # ラフを掴むつもりが必ず上のものに取られる
        if tool == TOOL_ROUGH:
            self._begin_rough_drag(x, y)
            event.accept()
            return

        # トーンの範囲（→ 要件定義 10.1）。ここも他の判定より先に打ち切る。
        # 範囲はコマや画像の上に重なるので、下の判定を通すと必ず取られる
        if tool == TOOL_TONE_AREA:
            self._begin_tone_drag(x, y)
            event.accept()
            return

        # 切り抜き（→ 10.3）。**押した所を消すだけで、選択も掴みも起こさない。**
        # 下の判定を通すと、絵を選ぶ・動かすが先に効いてしまう
        if tool == TOOL_WAND:
            self._wand_click(x, y, event.modifiers())
            event.accept()
            return

        # 吹き出し・マーク・セリフはコマの上に置くものなので、下に何があっても
        # 作れる。コマ追加と違って「空白のときだけ」にすると、ほとんどの場所で
        # 作れない
        if tool in BALLOON_TOOLS or tool in STICKER_TOOLS or tool == TOOL_TEXT:
            self._drag = CreateFloatingDrag.begin(self, x, y, tool)
            event.accept()
            return

        handle = self._handle_at_point(x, y)
        rotating = self._rotate_handle_at(x, y)
        text = text_at(self.state.page, x, y)
        sticker = sticker_at(self.state.page, x, y)
        balloon = balloon_pick_at(self.state.page, x, y, self.state.balloon_settings)
        hit = panel_at(self.state.page, x, y)

        # コマ追加の道具でも、既にあるコマやそのつまみの上なら編集を優先する。
        # 何も無いところを押したときだけ新しいコマを作る
        if (
            tool == TOOL_PANEL
            and handle is None
            and not rotating
            and hit is None
            and balloon is None
            and sticker is None
            and text is None
        ):
            self._drag = CreatePanelDrag.begin(self, x, y)
            event.accept()
            return

        # しっぽの先端と付け根。つまみより先に見る。小さな吹き出しでは
        # これらと角のつまみが近づくため、狙って掴んだほうを優先する
        if self._tail_tip_at(x, y):
            self._drag = TailDrag.begin(self, x, y)
            event.accept()
            return

        if self._tail_root_at(x, y):
            self._drag = TailRootDrag.begin(self, x, y)
            self.state.message.emit("上下にドラッグすると、しっぽの付け根が動きます")
            event.accept()
            return

        # しっぽの三角形の内側。先端・付け根のつまみより後に見る
        # （ひし形は三角形の付け根と重なるため、先に見ると付け根を
        # 動かせなくなる）
        if self._tail_body_at(x, y):
            self._drag = TailDrag.begin(self, x, y)
            self.state.message.emit("ドラッグすると、しっぽの向きが変わります")
            event.accept()
            return

        selected_bounds = self.state.selected_bounds
        if handle is not None and selected_bounds is not None:
            self._drag = ResizeDrag.begin(self, handle, selected_bounds)
            # 掴んだ時点で出す。動かし始めてからでは遅い
            self._update_aspect_hint(handle, self._shift_held(event))
            event.accept()
            return

        # 回転つまみ。8方向のつまみより後に見る。上辺から離して置いてあるので
        # 普段は重ならないが、重なったときは大きさを変えるほうを優先する
        # （回転はやり直しやすく、意図せず回っても気づける）
        if rotating:
            self._drag = RotateDrag.begin(self, x, y)
            self.state.message.emit(ROTATE_HINT)
            event.accept()
            return

        # 斜めの境界のつまみ。**角と辺のつまみより後に見る。**
        # こちらは掴む範囲が広いので、先に見ると縮小したときや細いコマで
        # 左右のつまみを覆い隠し、大きさを変えられなくなる
        if self._slant_handle_at(x, y):
            self._drag = SlantDrag.begin(self)
            self.state.message.emit("左右にドラッグすると、斜めの境界が動きます")
            event.accept()
            return

        # 集中線のつまみ。**角と辺のつまみより後、コマ本体より先。**
        #
        # 角・辺より後にするのは、中心がコマの隅に寄っているときに
        # それらを覆い隠さないため（斜めの境界と同じ）。本体より先に
        # するのは、中心のつまみがコマの真ん中あたりに出るためで、
        # 後にすると掴んだつもりがコマの移動になる（→ 要件定義 6.16）
        if self._focus_hole_at(x, y):
            self._drag = FocusDrag.begin(self, "focus_hole")
            self.state.message.emit("左右にドラッグすると、集中線の内側の空きが変わります")
            event.accept()
            return

        if self._focus_center_at(x, y):
            self._drag = FocusDrag.begin(self, "focus_center")
            self.state.message.emit("ドラッグすると、集中線の中心が動きます")
            event.accept()
            return

        # 流線のつまみ。**集中線のつまみより後、コマ本体より先。**
        #
        # 集中線より後にするのは、あちらの中心が**動かせる＝どこにでも
        # 来る**ため。流線の丸は決まった場所にしか出ないので、重なった
        # ときに逃げられるのは集中線の側だけ（→ 要件定義 6.26）
        if self._flow_angle_at(x, y):
            self._drag = FlowDrag.begin(self)
            self.state.message.emit(
                "ドラッグすると、流線の向きが変わります（Shift で15度ずつ）"
            )
            event.accept()
            return

        self.state.select(self._pick_at(x, y))
        if self.state.selected_id is not None and self.state.is_locked_selection:
            # ロックしたコマは選べるが動かせない（→ 要件定義 6.17）。
            # 選択の入り口は塞がない——解除するにはメニューか右クリックが要る
            self.state.message.emit(
                "ロックされたコマです。動かすにはロックを解除してください"
            )
        elif self.state.selected_id is not None:
            self._drag = MoveDrag.begin(self, x, y)
            if self.state.selected_slant_pair is not None:
                self.state.message.emit("斜めに割った2枚は、まとめて動きます")
        event.accept()

    def _rough_handle_at(self, x: float, y: float) -> str | None:
        """その位置にあるラフのつまみ。無ければ None。

        傾きは持たないので角度は 0 で渡す。ラフに回転を入れていないのは、
        傾いた写真は撮り直せるため（マークの `rotation` と同じ線引き → 6.14）。
        """
        rough = self.state.page.rough
        if rough is None:
            return None
        return handle_at(rough.rect, x, y, HANDLE_PX / self.view_scale, 0.0)

    def _begin_rough_drag(self, x: float, y: float) -> None:
        """ラフ調整の道具で押された。つまみなら大きさ変更、内側なら移動。

        **ラフ以外には何も起きない。** 道具を持ち替えている間だけラフを
        掴める、という切り分けそのもの（→ 要件定義 6.23）。
        """
        rough = self.state.page.rough
        if rough is None:
            self.state.message.emit(
                "このページにはラフがありません（ファイル → ラフ → 読み込む）"
            )
            return

        handle = self._rough_handle_at(x, y)
        if handle is not None:
            self._drag = RoughResizeDrag.begin(self, handle)
            return
        if rough.rect.contains(x, y):
            self._drag = RoughMoveDrag.begin(self, x, y)

    def _apply_rough_rect(self, origin: Rect, final: Rect, label: str) -> None:
        """離した時点で1手として積む。**動いていなければ積まない。**"""
        if final == origin:
            return
        self.state.set_rough_rect(final, label)
        self.state.message.emit(f"ラフ: {final.w:.0f} × {final.h:.0f} px")

    # -- トーンの範囲（→ 要件定義 10.1） ------------------------------------

    @staticmethod
    def tone_area_rect(image: ImageObject) -> Rect:
        """絞る矩形を、**画像の傾きを外したページ座標**で返す。

        絞っていなければ画像いっぱい。保存は割合なので、画像を動かしても
        伸縮しても囲った場所が変わらない。
        """
        box = image.rect
        area = image.tone.area if image.tone is not None else None
        if area is None:
            return box
        return Rect(
            box.x + area.x * box.w,
            box.y + area.y * box.h,
            area.w * box.w,
            area.h * box.h,
        )

    @staticmethod
    def tone_area_ratio(image_rect: Rect, rect: Rect) -> Rect:
        """ページ座標の矩形を、画像に対する割合へ戻す。

        **はみ出しても丸めない。** 0〜1 の外は絵が無いだけで、画像の縁で
        自然に切れる（→ 要件定義 10.1）。
        """
        if image_rect.w <= 0 or image_rect.h <= 0:
            return Rect(0.0, 0.0, 1.0, 1.0)
        return Rect(
            (rect.x - image_rect.x) / image_rect.w,
            (rect.y - image_rect.y) / image_rect.h,
            rect.w / image_rect.w,
            rect.h / image_rect.h,
        )

    def tone_area_preview(self) -> Rect | None:
        """引いている最中の矩形。引いていなければ None。

        **モデルは離すまで触らない。** 触ると1動きごとにトーンを焼き直す
        ことになり、1枚 40ms がフレームごとに乗る（→ 要件定義 10.1）。
        """
        return getattr(self._drag, "preview", None) if isinstance(
            self._drag, ToneAreaDrag
        ) else None

    def _tone_handle_at(self, x: float, y: float) -> str | None:
        """その位置にあるトーン範囲のつまみ。無ければ None。

        **点を画像の中心まわりに戻してから見る。** `handle_at` の `rotation`
        は矩形自身の中心を軸にするので、そのまま渡すと傾いた画像で軸がずれる
        （範囲の矩形と画像とで中心が違う）。
        """
        image = self.state.tone_image
        if image is None or image.tone is None:
            return None
        lx, ly = unrotate_point(x, y, image.rect, image.rotation)
        return handle_at(
            self.tone_area_rect(image), lx, ly, HANDLE_PX / self.view_scale, 0.0
        )

    def _wand_click(self, x: float, y: float, modifiers) -> None:
        """切り抜きの道具で押された（→ 要件定義 10.3）。

        **押した1点を、その絵の元画像の画素へ翻訳して渡すだけ。** 何を消すかを
        決めるのは `manga_layout.wand` で、ここは座標の橋渡しに徹する。

        Shift を押しながらなら「そこだけ残す」。クリスタの選択と同じ指の形で、
        意味は裏返し（あちらは足す）だが、**この道具には足す相手が無い**——
        1回ごとに作品へ書き込むので、積んでおく選択そのものが存在しない。
        """
        panel = panel_at(self.state.page, x, y)
        image = image_at(panel, x, y) if panel is not None else None
        if image is None:
            self.state.message.emit("コマの中の絵を押してください")
            return

        seed = image_pixel_at(image, x, y)
        if seed is None:
            return
        keep_only = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        self.state.erase_region_at(image.id, seed, keep_only=keep_only)

    def _begin_tone_drag(self, x: float, y: float) -> None:
        """トーン範囲の道具で押された。つまみなら隅を動かし、それ以外は囲い直す。

        **他には何も起きない。** 道具を持ち替えている間だけ範囲を掴める、
        という切り分けそのもの（ラフと同じ → 6.23）。
        """
        image = self.state.tone_image
        if image is None or image.tone is None:
            self.state.message.emit(
                "トーンの入った絵かコマを選んでください（画像 → トーン → 入れる）"
            )
            return
        # 隅の4つだけを見る。辺のつまみは出していない（→ `_draw_cross_handles`）
        handle = self._tone_handle_at(x, y)
        if handle is not None and len(handle) != 2:
            handle = None
        self._drag = ToneAreaDrag.begin(self, x, y, handle)

    def _apply_tone_area(
        self, image_id: str, image_rect: Rect, origin: Rect | None, final: Rect | None
    ) -> None:
        """離した時点で1手として積む。**変わっていなければ積まない。**

        **潰れた矩形は捨てる。** 押しただけで動かさなかったときに幅 0 の
        範囲が入ると、トーンが丸ごと消えたように見えて戻し方が分からなくなる。

        **絵に1画素も掛からない矩形も捨てる**（→ 要件定義 6.27）。この道具を
        持っている間は選び直せないので、**離れたコマを触ったつもりのドラッグが、
        選んでいる絵の範囲を絵の外へ飛ばす**。結果は「そのコマのトーンが消えた」
        という見え方になり、原因の見当が付かない（本人の指摘 2026-08-06。
        上下のコマを行き来しながらトーンを合わせる使い方）。
        """
        if final is None or final.w < 1.0 or final.h < 1.0 or final == origin:
            return
        # はみ出し自体は今までどおり通す。**弾くのは重なりが無いときだけ**
        # （画像の縁で自然に切れるのが、はみ出しの扱い → `tone_area_ratio`）
        overlap_w = min(final.right, image_rect.right) - max(final.x, image_rect.x)
        overlap_h = min(final.bottom, image_rect.bottom) - max(final.y, image_rect.y)
        if overlap_w < 1.0 or overlap_h < 1.0:
            self.state.message.emit(
                "選んでいる絵から外れています。別の絵を絞るには、"
                "選択の道具（V）で選び直してから"
            )
            return
        self.state.set_tone_area(image_id, self.tone_area_ratio(image_rect, final))
        self.state.message.emit(f"トーンの範囲: {final.w:.0f} × {final.h:.0f} px")

    def _selected_image_hit(self, x: float, y: float) -> ImageObject | None:
        """選択中の画像を、切り抜かれた見た目の範囲内でだけ拾う。

        `layout.image_at` と同じ規則（コマの外にはみ出した部分は当たらない。
        切り抜かれて見えていないため）を、選択中の画像にも当てはめる。

        「コマにフィット」した画像はコマの縁を越えて広がるのが普通で、
        隙間を挟んだ隣のコマまで重なることもある。**その画像自身のコマの
        中かどうかを見ずに「どこかのコマの上」とだけ判定していた**ため、
        隣のコマを選ぼうとしたクリックが、見えない画像を掴んだまま
        動かしてしまっていた（2026-08-08 に発見）。
        """
        image = self.state.selected_image
        if image is None or not rotated_rect_contains(image.rect, x, y, image.rotation):
            return None
        panel = self.state.page.panel_of_image(image.id)
        if panel is None or not panel.shape.contains(x, y):
            return None
        return image

    def _pick_at(self, x: float, y: float) -> str | None:
        """その位置で選ぶものの id。何も無ければ None。

        **手前にあるものから順に見る。** セリフはマークの上に、マークは
        吹き出しの上に、吹き出しはコマの上に乗せるものなので、この順で
        拾わないと「文字を掴んだつもりで吹き出しが動く」ことになる。
        並び順の出所は `model.floating_order`（描く順と同じ）。

        **左クリックと右クリックで同じ判定を使う。** 別々に書くと、
        右クリックで選ばれるものと、そのまま引いたときに動くものが
        食い違う。
        """
        text = text_at(self.state.page, x, y)
        if text is not None:
            return text.id

        sticker = sticker_at(self.state.page, x, y)
        if sticker is not None:
            return sticker.id

        balloon = balloon_pick_at(self.state.page, x, y, self.state.balloon_settings)
        if balloon is not None:
            return balloon.id

        panel = panel_at(self.state.page, x, y)

        # 選択中の画像の上なら、コマに持ち替えずにその画像のまま。
        # ここで奪われると、選んだ絵をドラッグした瞬間にコマが動く
        image = self._selected_image_hit(x, y)
        if image is not None:
            return image.id

        return None if panel is None else panel.id

    def contextMenuEvent(self, event) -> None:
        """右クリック。**押した場所のものを選んでから**メニューを出す。

        選び直さずに出すと、「セリフを太字に」が直前に選んでいた別の
        セリフに効く。Windows の右クリックの慣例どおり、狙ったものが
        選ばれた状態でメニューが出る。

        画面移動の最中（スペース押し・中ボタン）は出さない。掴んだまま
        メニューが割り込むと、離しても移動が終わらない。
        """
        if self._space_held or self._pan_from is not None:
            return

        # 入力中に画面を触ったら、そこで確定してから次の操作へ移る
        # （左クリックと同じ扱い）。入力欄に Qt 標準の右クリックメニューを
        # 出させてはいけない。メニューに焦点を奪われた時点で focusOut が
        # 走り、メニューを開いたまま入力欄がシーンから外れる
        self.finish_text_edit(commit=True)
        self._reset_drag()

        point = self.mapToScene(event.pos())
        x, y = point.x(), point.y()
        # ラフ・トーン範囲の調整中は選び直さない（→ 6.23、10.1）。どちらの
        # 道具でも選択枠を出していないので、裏で選ばれても見えないまま次の
        # 操作に効いてしまう。トーンは特に、選び直した時点で道具そのものが
        # 外れる（→ `_leave_tone_tool_if_gone`）
        if self.state.tool not in (TOOL_ROUGH, TOOL_TONE_AREA):
            self.state.select(self._pick_at(x, y))
        self.viewport().update()
        self.context_menu_requested.emit(x, y, event.globalPos())
        event.accept()

    # -- セリフのその場編集 --------------------------------------------------

    @property
    def is_editing_text(self) -> bool:
        return self._text_editor is not None

    def update_text_preview(self, content: str) -> None:
        """入力中の内容を、縦書きの下見として画面へ渡す。

        モデルには確定するまで触らない。途中経過はしっぽや回転と同じく
        `DragPreview` に載せて渡す（→ `TextEditorItem.publish_preview`）。
        """
        self._scene.editing_text_content = content
        self.viewport().update()

    def begin_text_edit(self, text_id: str, *, is_new: bool = False) -> bool:
        """セリフの入力を始める。始められたら True。

        `is_new` は**置いたばかりのセリフを打ち始めるとき**だけ真にする
        （→ `_apply_create_text`）。空のまま閉じた・Esc で取り消したときに
        追加ごと取り消すのは、このときに限る（→ `finish_text_edit`）。
        """
        self.finish_text_edit(commit=True)

        text = self.state.page.find(text_id)
        if not isinstance(text, TextObject):
            return False

        self.state.select(text_id)
        self._reset_drag()
        editor = TextEditorItem(self, text)
        self._scene.addItem(editor)
        self._scene.editing_text_id = text_id
        self._scene.editing_text_content = text.content
        self._text_editor = editor
        self._text_editor_is_new = is_new

        editor.setFocus(Qt.FocusReason.MouseFocusReason)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)

        self.state.message.emit(
            TEXT_EDIT_HINT_VERTICAL
            if text.direction == "vertical"
            else TEXT_EDIT_HINT
        )
        self.viewport().update()
        return True

    def finish_text_edit(self, commit: bool = True) -> None:
        """入力を終える。`commit` が False なら書き戻さない。

        **置いたばかりのセリフが空のまま残るなら、追加ごと取り消す**
        （要件定義 6.5）。押し間違いで置いてしまっただけのときに、中身の
        無い枠が残る。画面に出るのは薄い点線だけで（`render._draw_text`）、
        書き出しでは何も描かれないので、**残っていること自体に気づけない**
        （本人の指摘 2026-08-07）。

        当てはまるのは次の2つ。**どちらも「残るのは中身の無い枠」で同じ**
        なので、片方だけ残しても覚え直しが要るだけになる。

        - 1文字も入れずに閉じた（確定・取り消しのどちら経由でも）
        - 打ってから **Esc で取り消した**（`commit=False`）。Esc は打った
          内容を捨てるので、書き戻さない以上そのセリフは空のまま残る。
          置く前へ戻すのが「取り消し」の意味に合う（2026-08-08）

        空白だけの入力も空として扱う（全角空白を含む）。描いても何も
        出ないので、枠だけが残るのと区別が付かない。
        """
        editor = self._text_editor
        if editor is None:
            return

        text_id = self._scene.editing_text_id
        content = editor.close_editor()
        is_new = self._text_editor_is_new

        self._text_editor = None
        self._text_editor_is_new = False
        self._scene.editing_text_id = None
        self._scene.editing_text_content = None
        self._scene.removeItem(editor)

        if is_new and (not commit or not content.strip()):
            # **確定していない中身は捨ててから消す。** 追加した時点の
            # 状態へ戻すので、書き戻す前でも結果は同じ。
            #
            # 直前の1手が「セリフの追加」でなければ `discard_last_edit` は
            # 何もしない（間に別の編集が挟まった場合の保険）。そのときは
            # 下へ抜けるが、`commit=False` なら書き戻しも起きないので
            # 空のセリフがそのまま残る——**消せないより、消し間違えない**
            if self.state.discard_last_edit("セリフの追加"):
                self.state.message.emit(
                    "取り消したので、セリフは作りませんでした"
                    if content.strip()
                    else "空のままだったので、セリフは作りませんでした"
                )
                self.viewport().update()
                return

        if commit and text_id is not None:
            current = self.state.page.find(text_id)
            if isinstance(current, TextObject):
                if current.content != content:
                    self.state.set_text_content(text_id, content)
            elif content.strip():
                # 書き戻す先が今のページに無い。**黙って捨てない。**
                #
                # 入力中にページが変わると起きる。項目の実行は必ず確定を
                # 挟むようにしたので（→ `MainWindow.run_action`）今は通れ
                # ないが、**通れたときに何も言わずに消えるのが元の壊れ方**
                # だった。打った内容が消えたことに気づけるようにしておく
                self.state.message.emit(
                    "書き戻す先のセリフが見つからず、打った内容を残せませんでした"
                )
        self.viewport().update()

    def _tail_tip_at(self, x: float, y: float) -> bool:
        """選択中の吹き出しの、しっぽの先端を掴んでいるか。

        描いてある丸より広く取る（→ `TAIL_TIP_HANDLE_PX`）。
        """
        balloon = self.state.selected_balloon
        if balloon is None or not balloon.tail.enabled:
            return False
        tx, ty = balloon.tail.tip
        half = TAIL_TIP_HANDLE_PX / self.view_scale / 2.0
        return abs(x - tx) <= half and abs(y - ty) <= half

    def _tail_body_at(self, x: float, y: float) -> bool:
        """選択中の吹き出しの、**見えているしっぽ**の内側を押しているか。

        先端の丸・付け根のひし形は小さく、そこだけしか掴めないと
        「しっぽの絵は見えているのに反応しない」というズレになる
        （本人談 2026-08-05）。見えている形の内側ならどこでも
        先端をつまんだのと同じ扱いにする。

        三角と飛びしっぽの違いは `tail_body_contains` が吸収する。
        ここで形ごとに分けると、片方を直し忘れて挙動が食い違う。
        """
        balloon = self.state.selected_balloon
        if balloon is None or not balloon.tail.enabled:
            return False
        return tail_body_contains(balloon, x, y, self.state.balloon_settings)

    def _slant_handle_at(self, x: float, y: float) -> bool:
        """斜めの境界のつまみを掴んでいるか。

        描いてある印より**ずっと広く**取る（`SLANT_HANDLE_PX`）。左右に
        しか動かないうえ、境界の周りは隙間で他に掴むものが無いため、
        狙いを外しても拾えるほうが扱いやすい。

        代わりに、角と辺のつまみより**後**に判定すること。先に見ると
        こちらが広いぶん、それらを覆い隠してしまう。
        """
        point = self._scene.slant_handle()
        if point is None:
            return False
        half = SLANT_HANDLE_PX / self.view_scale / 2.0
        return abs(x - point[0]) <= half and abs(y - point[1]) <= half

    def _focus_center_at(self, x: float, y: float) -> bool:
        """集中線の中心のつまみを掴んでいるか。"""
        return self._near(self._scene.focus_center_handle(), x, y, FOCUS_CENTER_HANDLE_PX)

    def _focus_hole_at(self, x: float, y: float) -> bool:
        """集中線の内側の空きのつまみを掴んでいるか。"""
        return self._near(self._scene.focus_hole_handle(), x, y, FOCUS_HOLE_HANDLE_PX)

    def _flow_angle_at(self, x: float, y: float) -> bool:
        """流線の向きのつまみを掴んでいるか。"""
        return self._near(self._scene.flow_angle_handle(), x, y, FLOW_ANGLE_HANDLE_PX)

    def _near(
        self, point: tuple[float, float] | None, x: float, y: float, size_px: float
    ) -> bool:
        """点を中心にした正方形の中か。掴む範囲は**画面の大きさ**で決める。

        表示倍率で割ってシーンの px に直すので、縮小しても掴みにくく
        ならない。
        """
        if point is None:
            return False
        half = size_px / self.view_scale / 2.0
        return abs(x - point[0]) <= half and abs(y - point[1]) <= half

    def _tail_root_at(self, x: float, y: float) -> bool:
        """選択中の吹き出しの、しっぽの付け根を掴んでいるか。

        描いてあるひし形より**少し広く**取る（→ `TAIL_ROOT_HANDLE_PX`）。
        """
        balloon = self.state.selected_balloon
        if balloon is None or not balloon.tail.enabled:
            return False
        root = tail_root_point(balloon, self.state.balloon_settings)
        if root is None:
            return False
        half = TAIL_ROOT_HANDLE_PX / self.view_scale / 2.0
        return abs(x - root[0]) <= half and abs(y - root[1]) <= half

    def mouseDoubleClickEvent(self, event) -> None:
        """**選んでいるセリフ**なら文字の入力へ。それ以外は、その場所にある
        ものを**押すたびに1つずつ選び直す**（→ 要件定義 6.25）。

        巡る順は「コマ → その中の画像 → 次のコマ → …」（→ `pick_stack`）
        なので、**1回目は必ずコマの中の画像**になる。1クリックで画像が
        選ばれるとコマを動かすつもりのドラッグが絵だけを動かすため、
        踏み込む操作を分けてあるのは今までどおりで（→ 6.3）、2回目から
        先が足した分。
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        # ラフの調整中は踏み込まない（→ 6.23）。掴めないものが選ばれるだけで、
        # しかも枠を出していないので選ばれたことに気づけない
        if self.state.tool in (TOOL_ROUGH, TOOL_TONE_AREA):
            event.accept()
            return

        x, y = self._scene_px(event)

        text = text_at(self.state.page, x, y)
        if text is not None:
            self._double_click_text(text.id)
            event.accept()
            return

        stack = pick_stack(self.state.page, x, y)
        # **押される前の選択から数える。** 直前の押下で手前のコマに
        # 選び直されているため、今の選択から数えると巡回が戻る（→ 6.25）
        target = next_in_stack(stack, self._selected_before_press)
        if target is None or target == self.state.selected_id:
            # 巡っても同じものに戻る場所（画像の無いコマ1枚など）。
            # 選択が動いたように見せない
            super().mouseDoubleClickEvent(event)
            return

        self._reset_drag()
        self.state.select(target)
        # 次の押下でも同じ位置から続けられるようにする。押下を挟まずに
        # ダブルクリックが続いた場合（テストや、素早い連打）への備え
        self._selected_before_press = target
        self._announce_pick(target, stack)
        event.accept()

    def _double_click_text(self, text_id: str) -> None:
        """セリフの上でダブルクリックされた。**選んでいたものだけ入力へ入る。**

        以前は1回目から入力に入っていたが、**隣り合ったセリフを選び分ける
        ときに、狙いを外したセリフの入力へ飛び込んでしまう**（本人談
        2026-08-07）。入力欄は縦書きのセリフでも横書きで開くので
        （→ `TextEditorItem`）、飛び込んだセリフはその場で横書きに化けて
        見える。**縦書きの設定は 1 度も変わっていないのに、変わったように
        見える**のがこの操作の質の悪いところだった。

        選ぶだけで済めば、外したときの損は「別のものが選ばれた」だけになる。
        入力へは**もう一度ダブルクリック**すれば入る（→ 要件定義 6.5）。

        **押される前の選択から数える**（`_selected_before_press`）。
        ダブルクリックの手前には必ず押下が挟まり、そこで既にこのセリフへ
        選び直されているので、今の選択を見ると**必ず「選んでいた」に
        なってしまう**。下の巡回が同じ理由で同じ控えを使っている（→ 6.25）。
        """
        if self._selected_before_press == text_id:
            self.begin_text_edit(text_id)
            return

        self._reset_drag()
        self.state.select(text_id)
        # 次のダブルクリックで入力へ入れるようにする。押下を挟まずに
        # ダブルクリックが続いた場合への備えで、下の巡回と同じ扱い
        self._selected_before_press = text_id
        self.state.message.emit(
            "セリフを選びました。もう一度ダブルクリック、または Enter で入力できます"
        )

    def _announce_pick(self, object_id: str, stack: list[str]) -> None:
        """ダブルクリックで選んだものを状態表示に出す。

        **画像のときは今までの文のまま。** 踏み込んだ直後に読みたいのは
        「これから何ができるか」で、そこに巡回の話を混ぜると長くなる。

        コマを選ぶのはこの巡回だけなので（1クリックで選ぶ経路とは別）、
        そちらには**何番目か**を添える。選んだコマが他のコマの下に
        隠れていると、枠が出ても「何が起きたのか」が伝わりにくい。
        """
        if self.state.selected_image is not None:
            self.state.message.emit(
                "画像を選びました。ドラッグで移動、つまみで拡大縮小"
                "（Shift で縦横比を保つ）。Esc でコマに戻ります"
            )
            return
        position = f"{stack.index(object_id) + 1} / {len(stack)}"
        self.state.message.emit(
            f"コマを選びました（{position}）。ダブルクリックで次のものへ"
        )

    def mouseMoveEvent(self, event) -> None:
        if self._pan_from is not None:
            delta = event.position() - self._pan_from
            self._pan_from = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return

        x, y = self._scene_px(event)

        if self.state.tool in SPLIT_TOOLS:
            self._update_split_preview(x, y)
            event.accept()
            return

        if self._drag is None:
            self._update_cursor(x, y)
            super().mouseMoveEvent(event)
            return

        self._drag.update(self, x, y, event)
        self.viewport().update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._pan_from is not None:
            self._pan_from = None
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if self._space_held else Qt.CursorShape.ArrowCursor
            )
            if not self._space_held:
                self.viewport().unsetCursor()
            event.accept()
            return

        if self._drag is None:
            super().mouseReleaseEvent(event)
            return

        drag = self._drag
        self._reset_drag()
        drag.commit(self)

        self.viewport().update()
        event.accept()

    def _apply_focus(self, panel_id: str, focus: FocusLines, mode: str) -> None:
        """離した時点で1手として積む。**変わっていなければ積まない。**"""
        panel = self.state.page.find(panel_id)
        if not isinstance(panel, Panel) or panel.focus_lines is None:
            return
        if mode == "focus_center":
            if panel.focus_lines.center == focus.center:
                return
            self.state.set_focus_shape(panel_id, center=focus.center)
            return
        if panel.focus_lines.hole == focus.hole:
            return
        self.state.set_focus_shape(panel_id, hole=focus.hole)
        self.state.message.emit(f"集中線の内側: コマの短辺の {focus.hole * 100:.0f}%")

    def _apply_flow_angle(self, panel_id: str, angle: float) -> None:
        """離した時点で1手として積む。**変わっていなければ積まない。**"""
        panel = self.state.page.find(panel_id)
        if not isinstance(panel, Panel) or panel.flow_lines is None:
            return
        if panel.flow_lines.angle == angle:
            return
        self.state.set_flow_angle(panel_id, angle)
        self.state.message.emit(f"流線の向き: {angle:.0f}°")

    def _handle_at_point(self, x: float, y: float) -> str | None:
        """その位置にある、選択中のもののつまみ。無ければ None。

        **ロックしたコマでは常に None。** 大きさ変更は本体の形を変える
        操作なので止める（→ 要件定義 6.17）。中の画像やフキダシは
        コマではないので、この判定に引っかからず今まで通り動く。
        """
        if self.state.is_locked_selection:
            return None
        bounds = self.state.selected_bounds
        if bounds is None:
            return None
        return handle_at(
            bounds, x, y, HANDLE_PX / self.view_scale, self._selected_rotation()
        )

    @staticmethod
    def _angle_at(rect: Rect, x: float, y: float) -> float:
        """矩形の中心から見た (x, y) の向き（度）。"""
        cx, cy = rect.center
        return math.degrees(math.atan2(y - cy, x - cx))

    def _rotate_handle_point(self) -> tuple[float, float] | None:
        """回転つまみ（丸）の位置。画像を選んでいないときは None。

        描く側（`PageScene._draw_rotate_handle`）と同じ場所を、painter を
        使わずに求める。片方だけ直すと、見えている印と掴める場所がズレる。
        """
        image = self.state.selected_image
        if image is None:
            return None
        rect = image.rect
        cx, cy = rect.center
        top = rect.y - ROTATE_HANDLE_GAP_PX / self.view_scale
        return rotate_point(cx, top, cx, cy, image.rotation)

    def _rotate_handle_at(self, x: float, y: float) -> bool:
        """回転つまみの上か。

        描いてある丸より広く取る（→ `ROTATE_HANDLE_HIT_PX`）。
        """
        point = self._rotate_handle_point()
        if point is None:
            return False
        half = ROTATE_HANDLE_HIT_PX / self.view_scale / 2.0
        return abs(x - point[0]) <= half and abs(y - point[1]) <= half

    def _update_aspect_hint(self, handle: str, shift_held: bool) -> None:
        """斜めのつまみで画像を伸縮しているあいだ、Shift の案内を出す。

        角のつまみは縦と横が同時に変わるので、ここでだけ等比かどうかが
        効いてくる。辺のつまみや、コマの伸縮では出さない（コマは絵では
        ないので等比に縛る意味がなく、案内が邪魔になる）。

        押している最中は文面を変える。効いているかどうかが分からないと、
        Shift を押したつもりで歪んだまま確定してしまう。
        """
        if self.state.selected_image is None or handle not in CORNER_HANDLES:
            return
        text = ASPECT_HINT_HELD if shift_held else ASPECT_HINT
        if text == self._hint_shown:
            return
        self._hint_shown = text
        self.state.message.emit(text)

    @staticmethod
    def _shift_held(event) -> bool:
        return bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

    def _locked_aspect(self, event) -> float:
        """リサイズ中に保つ縦横比。縛らないなら 0（自由に伸縮）。

        コマの中の画像は **Shift を押している間だけ**（→ 6.3）。コマは絵では
        ないので、等比に縛る意味がない。

        マークは**常に保つ**（→ 6.14）。記号そのものなので、比を崩すと形が
        壊れるだけで使い道がない。Shift で外せる余地も作らない。
        """
        sticker = self.state.selected_sticker
        if sticker is not None:
            return aspect_of(sticker.src_px)

        image = self.state.selected_image
        if image is None or not self._shift_held(event):
            return 0.0
        return aspect_of(image.src_px)

    def _update_cursor(self, x: float, y: float) -> None:
        # ラフの調整中（→ 要件定義 6.23）。**他のどの判定よりも先に見る。**
        # この道具ではラフしか掴めないので、コマや吹き出しの上で移動の形が
        # 出ると、掴めるつもりで押して何も起きないことになる。
        #
        # 形は Qt の標準の手（`OpenHandCursor`）。**専用の絵は持たない。**
        # 掴めるものが1種類しか無いモードなので、他のもの（コマ・セリフ）と
        # 取り違えようがなく、道具を示す印を手元に出す必要がない。
        # つまみの上だけは、伸びる向きを示す形のままにする
        if self.state.tool == TOOL_ROUGH:
            rough_handle = self._rough_handle_at(x, y)
            self.viewport().setCursor(
                _HANDLE_CURSORS[rough_handle]
                if rough_handle is not None
                else Qt.CursorShape.OpenHandCursor
            )
            return

        # トーンの範囲（→ 10.1）。ラフと同じ理由で、ここも先に打ち切る。
        # **既定は十字**——絞っていない状態からはどこを押しても囲い直せるので、
        # 「引いて囲う」に見える形にする（ラフの「掴んで動かす」とは違う）
        if self.state.tool == TOOL_TONE_AREA:
            tone_handle = self._tone_handle_at(x, y)
            self.viewport().setCursor(
                _HANDLE_CURSORS[tone_handle]
                if tone_handle is not None and len(tone_handle) == 2
                else Qt.CursorShape.CrossCursor
            )
            return

        handle = self._handle_at_point(x, y)
        if (
            self.state.tool in BALLOON_TOOLS
            or self.state.tool in STICKER_TOOLS
            or self.state.tool == TOOL_TEXT
        ):
            # どこを押しても作れるので、常に十字
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        elif text_at(self.state.page, x, y) is not None:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._tail_tip_at(x, y):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._tail_root_at(x, y):
            # 上下にしか動かないことを形で示す
            self.viewport().setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._tail_body_at(x, y):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif handle is not None:
            self.viewport().setCursor(_HANDLE_CURSORS[handle])
        elif self._rotate_handle_at(x, y):
            # 掴んで動かせるつまみであることだけ示す。回る向きを表す形は
            # Qt の標準カーソルに無い
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._slant_handle_at(x, y):
            # 左右にしか動かないことを形で示す。掴む順と同じく、
            # 角と辺のつまみより後に見る（先に見ると出る形が実際と食い違う）
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._focus_hole_at(x, y):
            # ここも左右だけ。**掴む順と同じ並びにする**
            self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._focus_center_at(x, y):
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self._flow_angle_at(x, y):
            # 回転つまみと同じ形にする。回る向きを表すカーソルは Qt の
            # 標準に無いので、掴めることだけを示す（→ `_rotate_handle_at`）
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._selected_image_hit(x, y) is not None:
            # 掴む側（`_pick_at`）と同じ判定を通す。**動かせる印が出た場所は
            # 必ず動かせる**（→ 下の吹き出しの分岐と同じ考え方）。以前は
            # コマの所属を見ていなかったため、見えない部分でも「動かせる」
            # 形が出ていた（→ `_selected_image_hit`）
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif sticker_at(self.state.page, x, y) is not None:
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif (
            balloon_pick_at(self.state.page, x, y, self.state.balloon_settings)
            is not None
        ):
            # しっぽの上でも出る。掴む側（`_pick_at`）と同じ判定を通すので、
            # 動かせる印が出た場所は必ず動かせる
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif panel_at(self.state.page, x, y) is not None:
            hovered = panel_at(self.state.page, x, y)
            # ロックしたコマでは動かせる印を出さない（→ 要件定義 6.17）
            if self.state.is_panel_locked(hovered.id):
                self.viewport().unsetCursor()
            else:
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.state.tool == TOOL_PANEL:
            # ここを押せばコマが作られる、と分かるようにする
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.viewport().unsetCursor()

    # -- 位置を指定して置く（右クリックのメニュー用） ------------------------
    #
    # どれも**ドラッグの無い経路**。大きさ0の矩形を渡して、クリックだけで
    # 置いたときと同じ道（`_apply_*`）を通している。既定の大きさ・案内文・
    # 道具の戻し方をここに書き写すと、2か所を直すことになる。

    def add_panel_at(self, x: float, y: float) -> None:
        """その位置に既定の大きさのコマを1つ置く。"""
        self._apply_create(Rect(x, y, 0.0, 0.0), (x, y))

    def add_balloon_at(self, x: float, y: float, style: str) -> None:
        """その位置に既定の大きさの吹き出しを1つ置く。"""
        self._apply_create_balloon(Rect(x, y, 0.0, 0.0), (x, y), style)

    def add_sticker_at(self, x: float, y: float, kind: str) -> None:
        """その位置に既定の大きさのマークを1つ置く。"""
        self._apply_create_sticker(Rect(x, y, 0.0, 0.0), (x, y), kind)

    def add_text_at(self, x: float, y: float) -> None:
        """その位置にセリフを1つ置き、そのまま入力を始める。"""
        self._apply_create_text(Rect(x, y, 0.0, 0.0), (x, y))

    def split_at(self, x: float, y: float, tool: str) -> None:
        """その位置でコマを割る。道具は持ち替えない。"""
        self._apply_split(x, y, tool)

    # -- 確定 --------------------------------------------------------------

    def _apply_create(self, rect: Rect, press: tuple[float, float]) -> None:
        """コマを1つ作り、選択して編集できる状態にする。

        作り終えたら選択の道具に戻す。追加のあとは位置と大きさを整える
        のが普通で、続けて追加することは少ない。道具を残しておくと、
        整えようとした操作が次のコマの追加になってしまう（要件定義 6.9）。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            # ドラッグと呼べない動き。クリックで置いたものとして扱う
            rect = default_panel_rect(self.state.page, press[0], press[1], self.state.settings)

        with self.state.edit("コマの追加") as project:
            panel = project.add_panel(self.state.page, rect)
        self.state.select(panel.id)
        self.state.set_tool(TOOL_SELECT)
        self.state.message.emit(
            f"コマを追加しました（{rect.w:.0f} × {rect.h:.0f} px）。"
            "位置と大きさを調整できます"
        )

    def _apply_create_balloon(
        self, rect: Rect, press: tuple[float, float], style: str | None = None
    ) -> None:
        """吹き出しを1つ作り、選択の道具に戻す。

        コマ追加と同じ「1回きり」の扱い（要件定義 6.9）。置いたあとは
        位置と大きさ、しっぽの向きを整えるほうが先に来る。

        `style` を渡さなければ今の道具から決める。右クリックのメニューは
        道具を持ち替えずに種類を指定するので、そこからは明示的に渡す。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            rect = default_balloon_rect(
                self.state.page, press[0], press[1], self.state.balloon_settings
            )

        if style is None:
            style = BALLOON_TOOLS.get(self.state.tool, "ellipse")
        balloon = self.state.add_balloon(rect, style)
        self.state.set_tool(TOOL_SELECT)

        where = "コマに紐づけました" if balloon.attached_panel_id else "コマの外です"
        # **しっぽの案内は、しっぽがあるときだけ出す。** 四角はしっぽを
        # 消して置くので（要件定義 10.1）、そのまま出すと画面に無い印を
        # 引けと言うことになる
        how = (
            "丸い印を引くとしっぽの向きが変わります"
            if balloon.tail.enabled
            else "しっぽはメニューの「しっぽを出す」から足せます"
        )
        self.state.message.emit(f"フキダシを追加しました（{where}）。{how}")

    def _apply_create_sticker(
        self, rect: Rect, press: tuple[float, float], kind: str | None = None
    ) -> None:
        """マークを1つ置き、選択の道具に戻す（要件定義 6.14）。

        コマ・フキダシと同じ「1回きり」の扱い（要件定義 6.9）。

        囲った範囲は**そのままの形では使わない**。画像なので縦横比を保った
        まま中へ収める（`state.add_sticker`）。範囲がドラッグと呼べない
        大きさなら、クリックで置いたものとして既定の大きさにする。

        `kind` を渡さなければ今の道具から決める。右クリックのメニューは
        道具を持ち替えずに種類を指定するので、そこからは明示的に渡す。
        """
        if kind is None:
            kind = STICKER_TOOLS.get(self.state.tool, "")
        minimum = MIN_CREATE_PX / self.view_scale
        box = None if rect.w < minimum or rect.h < minimum else rect

        try:
            sticker = self.state.add_sticker(kind, press[0], press[1], box)
        except MangaLayoutError as e:
            self.state.set_tool(TOOL_SELECT)
            self.state.message.emit(str(e))
            return
        self.state.set_tool(TOOL_SELECT)

        label = STICKER_KIND_LABELS.get(kind, "マーク")
        where = "コマに紐づけました" if sticker.attached_panel_id else "コマの外です"
        self.state.message.emit(
            f"{label}を追加しました（{where}）。角を引くと大きさが変わります"
        )

    def _apply_create_text(self, rect: Rect, press: tuple[float, float]) -> None:
        """セリフを1つ作り、そのまま入力を始める。

        作っただけでは空の枠が残るだけなので、続けて打てる状態にする。
        道具は選択に戻す（コマ・吹き出しと同じ「1回きり」）。

        **空のまま閉じるか Esc で取り消したら、この追加ごと無かったことに
        なる**（→ `finish_text_edit`）。ここで先に作って履歴へ積むのは、
        入力欄が `TextObject` の位置・大きさ・書式を見て開くため
        （作る前に開くと、どこへどの大きさで出すかが決められない）。
        """
        minimum = MIN_CREATE_PX / self.view_scale
        if rect.w < minimum or rect.h < minimum:
            w, h = DEFAULT_TEXT_SIZE
            page = self.state.page
            rect = Rect(
                min(max(press[0] - w / 2.0, 0.0), max(page.size.w - w, 0.0)),
                min(max(press[1] - h / 2.0, 0.0), max(page.size.h - h, 0.0)),
                w,
                h,
            )

        text = self.state.add_text(rect)
        self.state.set_tool(TOOL_SELECT)
        self.begin_text_edit(text.id, is_new=True)

    def _apply_tail(self, balloon_id: str, tip: tuple[float, float]) -> None:
        balloon = self.state.page.find(balloon_id)
        if not isinstance(balloon, BalloonObject) or balloon.tail.tip == tip:
            return
        self.state.set_tail_tip(balloon_id, tip)

    def _apply_tail_root(self, balloon_id: str, root_y: float) -> None:
        balloon = self.state.page.find(balloon_id)
        if not isinstance(balloon, BalloonObject) or balloon.tail.root_y == root_y:
            return
        self.state.set_tail_root(balloon_id, root_y)
        # 先端から離れすぎた指定はそこで止まる（→ `TAIL_ROOT_MAX_GAP`）。
        # 言われた値をそのまま出すと、印が動いていないのに「上端」と
        # 名乗ることになる。**実際に付いた高さ**を出す
        where = self._root_label(self._root_ratio(balloon_id, root_y))
        self.state.message.emit(f"しっぽの付け根: {where}")

    def _root_ratio(self, balloon_id: str, fallback: float) -> float:
        """いま付け根が付いている高さ（上端 -1、下端 +1）。"""
        balloon = self.state.page.find(balloon_id)
        if not isinstance(balloon, BalloonObject):
            return fallback
        angle = tail_base_angle(balloon)
        # 付け根の高さは中心から見た媒介変数の sin。上限で止められた
        # 場合もこの式で正しい高さが出る
        return fallback if angle is None else math.sin(angle)

    def _apply_slant(self, panel_id: str, ratio: float) -> None:
        pair = self.state.page.slant_pair_of(panel_id)
        if pair is None or pair.ratio == ratio:
            return
        # ずらせなかったときは断りが既に出ている（→ `EditorState._edit_slant`）。
        # 上書きすると、効かなかった操作が成功したように見える
        if self.state.slide_slant(panel_id, ratio):
            self.state.message.emit(f"斜めの境界: 左から {ratio * 100:.0f}%")

    @staticmethod
    def _root_label(root_y: float) -> str:
        """割合を言葉にする。数字だけでは上下どちらか分かりにくい。"""
        if root_y <= -0.66:
            return "上端"
        if root_y < -0.15:
            return "やや上"
        if root_y <= 0.15:
            return "中央"
        if root_y < 0.66:
            return "やや下"
        return "下端"

    def _orphan_rejected(self, image: ImageObject, rect: Rect, reason: str) -> bool:
        """その矩形にすると画像が自分のコマの外へ完全に出るか。出るなら断る。

        弾くのは重なりが無くなるときだけ（→ `_apply_tone_area` と同じ
        考え方）。コマの縁を大きく越えるはみ出し自体は今までどおり通す
        （→ `layout.image_orphaned_at`）。

        **断ったら理由を状態表示に出し、その場に留める。** 黙って引き戻すと、
        なぜ動かなかった（大きさが変わらなかった）のか分からない。
        `reason` だけを差し替えるのは、移動と大きさ変更で言い方が変わる
        ため——止めている理由は同じなので、前半は共通の文言にする。
        """
        panel = self.state.page.panel_of_image(image.id)
        if panel is None or not image_orphaned_at(panel, rect, image.rotation):
            return False
        self.state.message.emit(f"コマの外まで出ると選べなくなるので、{reason}")
        return True

    def _apply_move(self, origin: Rect, final: Rect) -> None:
        dx, dy = final.x - origin.x, final.y - origin.y
        if dx == 0.0 and dy == 0.0:
            return
        object_id = self.state.selected_id
        if object_id is None:
            return

        image = self.state.selected_image
        if image is not None and image.id == object_id:
            moved = image.rect.translated(dx, dy)
            if self._orphan_rejected(image, moved, "そこまでは動かせません"):
                return

        for getter, cls, name in _MOVE_TARGETS:
            if getter(self.state) is None:
                continue
            with self.state.edit_page(f"{name}の移動") as page:
                obj = page.find(object_id)
                if isinstance(obj, cls):
                    obj.rect = obj.rect.translated(dx, dy)
            return

        if self.state.selected_balloon is not None:
            # **しっぽの先端は動かさない。** 先端はしゃべっている人物を
            # 指すページ座標なので、吹き出しの置き場所を変えても
            # 指す相手は変わらない（要件定義 4章）。
            # 上に乗ったセリフは一緒に動く
            with self.state.edit_page("フキダシの移動") as page:
                page.move_balloon(object_id, dx, dy)
            return

        with self.state.edit_page("コマの移動") as page:
            page.move_panel(object_id, dx, dy)

    def _apply_rotate(self, image_id: str, angle: float) -> None:
        """回した結果を確定する。

        傾きは配置の一部なので、履歴には位置や大きさと同じ1手として積む
        （→ 要件定義 6.3）。
        """
        image = self.state.selected_image
        if image is None or image.id != image_id or image.rotation == angle:
            return
        with self.state.edit_page("画像の回転") as page:
            target = page.find(image_id)
            if isinstance(target, ImageObject):
                target.rotation = angle
        self.state.message.emit(f"{angle:.0f}° 傾けました")

    def _apply_resize(self, rect: Rect) -> None:
        for getter, cls, name in _RESIZE_TARGETS:
            obj = getter(self.state)
            if obj is None:
                continue
            if obj.rect == rect:
                return
            # 画像は大きさ変更でもコマの外へ完全に出せる。つまみを反対側へ
            # 大きく引くと矩形が向こう側へ回り込むので、移動と同じ穴が開く
            # （2026-08-09 に発見 → `_orphan_rejected`）
            if isinstance(obj, ImageObject) and self._orphan_rejected(
                obj, rect, "その大きさにはできません"
            ):
                return
            object_id = obj.id
            with self.state.edit_page(f"{name}の大きさ変更") as page:
                target = page.find(object_id)
                if isinstance(target, cls):
                    target.rect = rect
            self.state.message.emit(f"{rect.w:.0f} × {rect.h:.0f} px")
            return

        panel = self.state.selected_panel
        if panel is None:
            return
        panel_id = panel.id

        # 斜めの組は外側の矩形を差し替え、2枚を作り直す。1枚ずつ変形すると
        # 傾きと隙間が左右で食い違う
        pair = self.state.page.slant_pair_of(panel_id)
        if pair is not None:
            if self.state.page.slant_bounds(pair) == rect:
                return
            # **判定は組の外側の矩形で行う。** 作り直したあとの1枚ずつの形は
            # ここでは分からないので、2枚ぶんの画像をまとめて外側と見比べる。
            # 外側にも掛からない画像だけを弾くぶん甘いが、斜めのコマでは
            # もともと外接矩形で近似している（→ `layout.image_orphaned_in`）
            if any(
                panel_rect_orphans(self.state.page.panel(member), rect.normalized())
                for member in pair.members()
            ):
                self._reject_panel_resize()
                return
            # 割れない大きさなら断りが状態表示に出て False が返る
            # （反転・境界の移動と共通 → `EditorState._edit_slant`）
            if self.state.set_slant_rect(panel_id, rect):
                self.state.message.emit(f"{rect.w:.0f} × {rect.h:.0f} px")
            return

        if panel.shape.bounds() == rect:
            return
        # コマ側を動かしても、中の画像は付いて回らない（→ `set_panel_rect`）。
        # 縮めた先で1枚も掛からなくなると、その画像は二度と選べない
        # （2026-08-09 に発見 → `layout.panel_rect_orphans`）
        if panel_rect_orphans(panel, rect.normalized()):
            self._reject_panel_resize()
            return
        with self.state.edit_page("コマの大きさ変更") as page:
            set_panel_rect(page.panel(panel_id), rect)
        self.state.message.emit(f"{rect.w:.0f} × {rect.h:.0f} px")

    def _reject_panel_resize(self) -> None:
        """コマの大きさ変更を、中の画像が孤児になるので断る。

        言い方を画像側（`_orphan_rejected`）と分けているのは、**動かないのが
        押しているつまみの側ではない**ため。「画像が」と言わないと、コマが
        止まった理由に見当が付かない。
        """
        self.state.message.emit(
            "中の画像がコマの外へ出て選べなくなるので、そこまでは変えられません"
        )

    # -- 分割 --------------------------------------------------------------

    def _split_target(self, x: float, y: float):
        """そこで分割できるコマ。できないなら None。

        斜めに割ったコマは（矩形でなくなるため）どの分割にも出さない。
        分割は「軸並行の矩形を切る」操作として閉じている。
        """
        panel = panel_at(self.state.page, x, y)
        if panel is None or panel.shape.as_rect() is None:
            return None
        return panel

    def _split_line(self, panel: Panel, x: float, y: float):
        """分割線の両端。押した位置に合わせて引く。"""
        bounds = panel.shape.bounds()
        tool = self.state.tool
        if tool == TOOL_SPLIT_H:
            return ((bounds.x, y), (bounds.right, y))
        if tool == TOOL_SPLIT_V:
            return ((x, bounds.y), (x, bounds.bottom))

        # 斜めは、実際に割ったときと同じ計算で下見を引く。見えている線と
        # 出来上がる形がずれない
        settings = self.state.settings
        angle = settings.slant_angle
        ratio = (x - bounds.x) / bounds.w if bounds.w > 0.0 else 0.5
        top = slant_boundary_x(bounds, ratio, angle, SLANT_RIGHT, bounds.y)
        bottom = slant_boundary_x(
            bounds, ratio, angle, SLANT_RIGHT, bounds.bottom
        )
        return ((top, bounds.y), (bottom, bounds.bottom))

    def _update_split_preview(self, x: float, y: float) -> None:
        panel = self._split_target(x, y)
        self._scene.split_preview = (
            None if panel is None else self._split_line(panel, x, y)
        )
        self.viewport().update()

    def _apply_split(self, x: float, y: float, tool: str | None = None) -> None:
        """その位置でコマを割る。`tool` を渡さなければ今の道具で決める。"""
        panel = self._split_target(x, y)
        if panel is None:
            self.state.message.emit("コマの上でクリックしてください")
            return
        if self.state.is_panel_locked(panel.id):
            self.state.message.emit(
                "ロックされたコマです。割るにはロックを解除してください"
            )
            return

        if tool is None:
            tool = self.state.tool
        panel_id = panel.id
        try:
            with self.state.edit("コマの分割") as project:
                page = project.pages[self.state.page_index]
                if tool == TOOL_SPLIT_SLANT:
                    split_panel_slant(
                        project,
                        page,
                        panel_id,
                        position=x,
                        direction=SLANT_RIGHT,
                        settings=self.state.settings,
                    )
                else:
                    split_panel(
                        project,
                        page,
                        panel_id,
                        horizontal=tool == TOOL_SPLIT_H,
                        position=y if tool == TOOL_SPLIT_H else x,
                        settings=self.state.settings,
                    )
        except ValueError as e:
            self.state.message.emit(str(e))
            return

        self._scene.split_preview = None
        self.state.select(panel_id)
        if tool == TOOL_SPLIT_SLANT:
            self.state.message.emit(
                "斜めに割りました。2枚はまとめて動きます。"
                "向きは「コマ > 斜めの向きを反転」で変えられます"
            )
        else:
            self.state.message.emit("コマを分割しました")

    # -- ドラッグ&ドロップ --------------------------------------------------
    #
    # 落とされ方は3通りある。**手前のものほど確実で速いので、この順に見る。**
    #
    # 1. 手元のファイル（エクスプローラーから）
    # 2. 絵そのもの（画像を持たせて渡してくるアプリ）
    # 3. 住所だけ（ブラウザから。取りに行かないと絵が手に入らない）
    #
    # ブラウザは 2 と 3 の両方を渡してくることがある。2 を先に見れば、
    # 手元にある絵を捨ててわざわざ取りに行く、という無駄が起きない。

    def _dropped_images(self, mime) -> list[pathlib.Path]:
        """ドロップされたもののうち、画像として扱えるファイル。"""
        if not mime.hasUrls():
            return []
        files = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = pathlib.Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_SUFFIXES:
                files.append(path)
        return files

    def _dropped_image_data(self, mime) -> QImage | None:
        """ドロップに絵そのものが入っていれば、それ。"""
        if not mime.hasImage():
            return None
        data = mime.imageData()
        image = data if isinstance(data, QImage) else QImage(data)
        return None if image.isNull() else image

    def _dropped_urls(self, mime) -> list[str]:
        """取りに行ける住所。

        **拡張子では絞らない。** 配信元が住所に拡張子を含めないことは普通に
        あり（`.../photo?id=1`）、そこで弾くと落とせる絵のほうが少なくなる。
        画像かどうかは取ってきてから `images.decode` が見る
        """
        if not mime.hasUrls():
            return []
        return [
            url.toString()
            for url in mime.urls()
            if not url.isLocalFile() and is_fetchable(url.scheme())
        ]

    def _dropped_sources(self, mime) -> tuple[list[tuple[str, object]], bool]:
        """落とされたものを「名前 → 中身を作る手順」の並びと、通信の要否にする。

        **中身をまだ取り出さない。** 取りに行くのは落とし先が決まってから。
        先に取ってしまうと、コマの外に落として断られるたびに通信が走る
        """
        files = self._dropped_images(mime)
        if files:
            return ([(path.name, path.read_bytes) for path in files], False)

        image = self._dropped_image_data(mime)
        if image is not None:
            return ([("ドロップされた画像", lambda: to_png_bytes(image))], False)

        urls = self._dropped_urls(mime)
        return ([(display_name(u), partial(fetch_bytes, u)) for u in urls], bool(urls))

    def _droppable(self, mime) -> bool:
        """落とせる状態に見せてよいか。ドラッグ中に何度も呼ばれるので軽く。"""
        return bool(
            self._dropped_images(mime) or mime.hasImage() or self._dropped_urls(mime)
        )

    def dragEnterEvent(self, event) -> None:
        if self._droppable(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        # ここを受け取らないと、Windows では入った瞬間だけ許可されて
        # 動かした途端に拒否に変わり、落とせなくなる
        if self._droppable(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        sources, from_network = self._dropped_sources(event.mimeData())
        if not sources:
            if self._droppable(event.mimeData()):
                # 受けると見せておいて何も起きないのが一番困る。
                # `_droppable` は軽さを取って中身まで見ないので、ここで拾う
                self.state.message.emit("落とされたものから画像を取り出せませんでした")
                event.ignore()
                return
            super().dropEvent(event)
            return

        point = event.position() if hasattr(event, "position") else event.pos()
        scene_point = self.mapToScene(point.toPoint())
        panel = panel_at(self.state.page, scene_point.x(), scene_point.y())
        if panel is None:
            self.state.message.emit("コマの上に落としてください")
            event.ignore()
            return

        event.acceptProposedAction()
        if from_network:
            # 取り終わるまで画面が止まる。何も出さないと固まったように見える
            self.state.message.emit(f"{len(sources)} 枚を取り込んでいます…")
        placed = self._place_dropped(panel.id, sources, wait=from_network)

        if placed:
            self.state.message.emit(
                f"{placed} 枚を置きました。コマを埋めるなら Ctrl+Shift+F"
            )

    def _place_dropped(self, panel_id: str, sources, *, wait: bool) -> int:
        """落とされたものを順に置く。置けた枚数を返す。

        1つ失敗しても残りを続ける。まとめて落とすのが普通の使い方なので、
        1枚で全部止めると、どれが原因かも分からないまま何も置かれない
        """
        if wait:
            QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            placed = 0
            for name, load in sources:
                try:
                    self.state.place_image(panel_id, load())
                except (MangaLayoutError, OSError) as e:
                    self.state.message.emit(f"{name}: {e}")
                    continue
                placed += 1
            return placed
        finally:
            if wait:
                QGuiApplication.restoreOverrideCursor()

    def leaveEvent(self, event) -> None:
        if self._scene.split_preview is not None:
            self._scene.split_preview = None
            self.viewport().update()
        super().leaveEvent(event)
