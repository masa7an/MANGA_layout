"""プロジェクトのデータモデル。

構造は要件定義 4章のとおり:

    Project
     ├ Page[]
          ├ Panel[]                コマ（クリッピングの主体）
          │    └ Image[]           コマ枠で切り抜かれる
          └ Floating[]             ページ直下に置かれ、切り抜かれない
               ├ Balloon           吹き出し
               ├ Sticker           マーク（！など。要件定義 6.14）
               └ Text              セリフ

吹き出しをコマの「子」にしていないのが要点。漫画の吹き出しはコマ枠から
はみ出すのが常態なので、子にすると切り抜きに巻き込まれて切られてしまう。
代わりに `attached_panel_id` で紐づけ、**移動は追随するが切り抜かれない**
という狙った挙動だけを取っている。マークも同じ理由でここに置く。

保存形式との対応は 1 対 1 で、`to_dict()` / `from_dict()` を往復しても
情報が落ちない。この性質が Undo/Redo（要件定義 6.8、スナップショット方式）
の土台になる。
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import validation as v
from .errors import ProjectFormatError, UnsupportedVersionError
from .geometry import Polygon, Rect, Size, normalize_angle

# 2 で単位を mm から px に変えた（要件定義 3章）。version 1 の作品は
# 読み込み時に換算する（`_scale_lengths`）
FORMAT_VERSION = 2
APP_NAME = "MANGA_layout"

# version 1（mm）を px に直すときの倍率。150dpi 換算。
# **この値を後から変えてはいけない。** 変えると、まだ変換していない
# 古い作品だけが違う大きさで開く
MM_TO_PX = 150.0 / 25.4

# 文字の大きさをポイントで言い直すときの倍率。
#
# **フォント設定の窓はポイントでしか喋らない。** こちらは px で持って
# いるが、その px は画面の点ではなく**ページの座標**（150dpi 換算）なので、
# Qt が画面の解像度で勝手に換算すると別の大きさになる。紙の上での
# 大きさで揃うよう、ここでも 150dpi で換算する。
#
# 1pt = 1/72 インチ。20px は約 9.6pt（約 3.4mm）にあたる
PT_TO_PX = 150.0 / 72.0

# よく使うページ寸法（px）。紙の寸法を 150dpi で換算した値で、
# 印刷はしないので「A4 相当」という目安でしかない（要件定義 1章）
PAGE_SIZES: dict[str, Size] = {
    "A4": Size(1240.0, 1754.0),
    "B5": Size(1075.0, 1518.0),
}
DEFAULT_PAGE_SIZE = PAGE_SIZES["A4"]

# ID の接頭辞。JSON を人が読んだときに種別が分かるようにしている
ID_PREFIX_PAGE = "page"
ID_PREFIX_PANEL = "panel"
ID_PREFIX_IMAGE = "img"
ID_PREFIX_BALLOON = "bal"
ID_PREFIX_TEXT = "txt"
ID_PREFIX_STICKER = "stk"

_ID_RE = re.compile(r"^[a-z]+_(\d+)$")

# 吹き出しの種類。`wavy`（波形）は**不安・動揺・弱った声**を表す。
# ギザギザで代用すると叫びに読めてしまい、意味が逆になる。
#
# `rect`（四角）は**ナレーション・地の文**で、誰かのセリフではない。
# 他の3種と違って輪郭が楕円から作れず、当たり判定も矩形で行う
# （要件定義 6.20）。
#
# `cloud`（雲）は**心の声・回想**。丸い飛びしっぽ（→ `TAIL_SHAPE_BUBBLES`）と
# 組にして使う（要件定義 6.22）。
#
# `spiky`（トゲトゲ）は**ギザギザと同じ叫び**で、山と谷を曲線でつないだ版。
# 意味は同じで見た目だけが違うので、どちらを使うかは作風で選ぶ（要件定義 6.32）
BALLOON_STYLES = ("ellipse", "jagged", "spiky", "wavy", "rect", "cloud")

# 作った時点でしっぽを消しておく種類。四角はナレーションに使うもので、
# 指す相手がいない（要件定義 10.1）。
#
# **禁止ではなく、置いたときの既定の話。** あとから「しっぽを出す」で
# 出せる。種類を変えたときにも触らない——既にあるフキダシのしっぽを
# 黙って消すことになるため
BALLOON_STYLES_WITHOUT_TAIL = ("rect",)

# しっぽの形。`bubbles`（丸い飛びしっぽ）は**心の声・独り言**を表す。
#
# **フキダシの種類とは別に持つ。** 種類の側に焼き込むと、丸い・ふわふわ・雲の
# それぞれに「飛びしっぽ版」が要って種類が倍に増える（要件定義 10.1）。
#
# 組み合わせは制限しない。意味の薄い組み合わせ（ギザギザや四角）は選ばない
# 限り出てこないので、禁じてメニューの分岐と読み込みの検証を増やすより軽い
TAIL_SHAPE_TRIANGLE = "triangle"
TAIL_SHAPE_BUBBLES = "bubbles"
TAIL_SHAPES = (TAIL_SHAPE_TRIANGLE, TAIL_SHAPE_BUBBLES)

# 置いた時点でしっぽを丸い飛びしっぽにする種類。雲は心の声を表すので、
# 三角のしっぽと組み合わせることがまず無い（要件定義 6.22）。
#
# **禁止ではなく、置いたときの既定の話**（`BALLOON_STYLES_WITHOUT_TAIL` と
# 同じ線引き）。あとから「しっぽを三角にする」で戻せる
BALLOON_STYLES_WITH_BUBBLE_TAIL = ("cloud",)
TEXT_ALIGNS = ("left", "center", "right")
TEXT_DIRECTIONS = ("horizontal", "vertical")

# **新しく作るセリフの向き。** マンガのセリフは縦書きが普通なので、
# 横書きのほうを選ぶ形にする（要件定義 6.11）。
#
# これは「作るとき」の既定であって、**読み込むときの既定ではない**。
# 保存形式に `direction` が無いファイルは横書きとして読む（`TextObject.from_dict`）。
DEFAULT_TEXT_DIRECTION = "vertical"

# **新しく作るセリフの書体。** 読みやすさを狙って作られた書体で、
# Windows 10 以降に標準で入っている。
#
# **名前のスペースを省けない。** Qt に渡すのは `UD デジタル 教科書体 N`
# （3か所にスペース）で、`UDデジタル教科書体N` と詰めて書くと一致せず、
# **黙って Tahoma に化ける**（2026-08-03 実測）。代わりの書体で描かれる
# だけなので、エラーにならず気づけない。
#
# 末尾の `N` は等幅の一族（`NK` は仮名が詰まり、`NP` は全体が詰まる）。
# 太さの `-R` / `-B` は付けない。Qt では同じ一族の中の style 扱いで、
# 太字は `Font.bold` から切り替わる。
DEFAULT_FONT_FAMILY = "UD デジタル 教科書体 N"

# 新しく作るセリフの文字の大きさ（px）。**紙の上で 20pt 相当**。
#
# px はページの座標（150dpi 換算）なので、数字の見た目より小さい。
# 20px では紙の上で約 9.6pt しかなく、ネームの下書きとして読みにくかった
# （2026-08-03、実機で確認して 42px に決めた）。
DEFAULT_FONT_SIZE_PX = 42.0

# 上の2つは `DEFAULT_TEXT_DIRECTION` と同じく「作るとき」の既定であって、
# **読み込むときの既定ではない**。項目の欠けた `project.json` は、書かれて
# いた頃の既定（`Yu Gothic UI` / 21px）で読む（`Font.from_dict`）。
# 既にある作品の見た目を、アプリの更新で変えないため
LEGACY_FONT_FAMILY = "Yu Gothic UI"
LEGACY_FONT_SIZE_PX = 21.0

# 斜め割りの境界が傾く向き。上へ行くほど右が "/"、上へ行くほど左が "\"
SLANT_RIGHT = "/"
SLANT_LEFT = "\\"
SLANT_DIRECTIONS = (SLANT_RIGHT, SLANT_LEFT)
READING_DIRECTIONS = ("rtl", "ltr")

# 吹き出しとセリフはコマより手前に置く。既存が無いときの開始値
FLOATING_BASE_Z = 10


# --------------------------------------------------------------------------
# 部品
# --------------------------------------------------------------------------


@dataclass
class Border:
    """枠線。コマ枠と吹き出しの輪郭に共通で使う。太さは px。"""

    width: float = 3.5
    color: str = "#000000"
    visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "color": self.color, "visible": self.visible}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Border:
        d = v.req_mapping(data, where)
        return cls(
            width=v.number(d, "width", where, 3.5),
            color=v.color(d, "color", where, "#000000"),
            visible=v.flag(d, "visible", where, True),
        )


@dataclass
class Font:
    """セリフの書式。サイズは px で持つ（ポイントでも mm でもない）。

    座標系が px に統一されているので、Qt へそのまま渡せる。**mm だった頃は
    3.5 のような小さな値になり、Qt が整数の画素数しか受け取らないせいで
    3 か 4 に丸められていた。** それを避けるための倍率合わせ（旧
    `TEXT_FONT_SCALE`）は、px にしたことで要らなくなった。
    """

    family: str = DEFAULT_FONT_FAMILY
    size_px: float = DEFAULT_FONT_SIZE_PX
    bold: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.family, "size_px": self.size_px, "bold": self.bold}

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Font:
        """`size_px` を読む。**version 1 の `size_mm` も受ける。**

        値の換算は `Project.from_dict` がまとめて行う。ここは
        「どの名前で入っているか」だけを吸収する。

        **項目が欠けているときは昔の既定で埋める**（`LEGACY_FONT_*`）。
        新しく作るときの既定（`DEFAULT_FONT_*`）を使うと、アプリを更新
        しただけで既にある作品の見た目が変わってしまう。
        """
        d = v.req_mapping(data, where)
        legacy = "size_px" not in d and "size_mm" in d
        return cls(
            family=v.text(d, "family", where, LEGACY_FONT_FAMILY),
            size_px=v.positive(
                d, "size_mm" if legacy else "size_px", where, LEGACY_FONT_SIZE_PX
            ),
            bold=v.flag(d, "bold", where, False),
        )


@dataclass
class Tail:
    """吹き出しのしっぽ。

    `tip`（先端）は吹き出しからの相対ではなく**ページ座標**で持つ。
    しゃべっている人物を指すものなので、吹き出しを動かしたときに
    先端が付いて回らないほうが自然に扱える。

    `root_y` は付け根の縦位置を、吹き出しの高さに対する割合で持つ
    （-1.0 が上端、0.0 が中央、+1.0 が下端）。**長さではなく割合**なのは、
    吹き出しの大きさを変えても付け根が同じ場所に残るようにするため。
    `None` は自動＝先端の向きに合わせる。

    `shape` は三角か飛びしっぽか（→ `TAIL_SHAPES`）。

    **`width` は三角のときだけ効く。** 飛びしっぽの円の大きさは、付け根から
    先端までの長さから決める。決め打ちにすると、長いしっぽで隙間だけが
    広がって点が散らばった絵になり、先端を引いても引いた量が絵に出ない
    （→ `layout.tail_bubbles`）。
    """

    enabled: bool = True
    tip: tuple[float, float] = (0.0, 0.0)
    width: float = 35.0
    root_y: float | None = None
    shape: str = TAIL_SHAPE_TRIANGLE

    def translated(self, dx: float, dy: float) -> Tail:
        return Tail(
            self.enabled,
            (self.tip[0] + dx, self.tip[1] + dy),
            self.width,
            self.root_y,
            self.shape,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "enabled": self.enabled,
            "tip": [self.tip[0], self.tip[1]],
            "width": self.width,
            "root_y": self.root_y,
        }
        # 三角のときは項目ごと省く。**飛びしっぽを足す前に保存した作品と、
        # 三角のままの作品の project.json が一字一句同じになる**
        # （`locked` → 6.17、`white` → 6.19 と同じ形）
        if self.shape != TAIL_SHAPE_TRIANGLE:
            data["shape"] = self.shape
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Tail:
        d = v.req_mapping(data, where)
        tip = v.point(d["tip"], f"{where}.tip") if "tip" in d else (0.0, 0.0)
        return cls(
            enabled=v.flag(d, "enabled", where, True),
            tip=tip,
            width=v.positive(d, "width", where, 35.0),
            # 項目が無い作品は自動として読む。root_y を足す前に保存した
            # ファイルでも、それまでと同じ形で開ける
            root_y=v.opt_ratio(d, "root_y", where),
            shape=v.choice(d, "shape", where, TAIL_SHAPES, TAIL_SHAPE_TRIANGLE),
        )


# 本数として読んでよい範囲。**`focus.py` ではなくここに置く。**
# 読み込みの検証は保存形式の話で、`focus` を読み込まなくても効く必要がある
# （`focus` は `model` を読む側なので、逆向きには参照できない）
FOCUS_COUNT_MIN = 4
FOCUS_COUNT_MAX = 400


@dataclass
class FocusLines:
    """集中線（要件定義 6.16）。**コマの属性として1つだけ持つ。**

    独立したオブジェクトにしていないので、`id` も `z` も持たない。矩形も
    持たず、**常にコマいっぱいに広がる**。大きさを別に持たせると、コマを
    縮めたときに集中線だけ取り残される状態を作れてしまう。

    **長さは1つも持たない。** `center` はコマの外接矩形に対する割合、
    `hole` と `width` は短辺に対する割合。絶対座標で持つと、コマを縮めた
    ときに中心がコマの外へ飛び出す（`SlantPair.ratio` と同じ理由）。

    `center` の**範囲は制限しない。** 画面の外から集中させるために中心を
    コマの外へ置くことがあり、その場合も線は正しく作れる。

    `seed` は形のばらつきを決める数字。**保存しないと開くたびに形が変わり**、
    書き出した PNG と画面が一致しなくなる。形を作る側は `random` を使わず
    自前の擬似乱数を回す（→ `manga_layout.focus`）。

    **既定値を持たせていない。** 出発点の値は `FocusSettings` にあり、
    2か所に持つと片方だけ古くなる。作るときは `focus.default_focus()` を通す。

    `white` は線の色。**黒地に白い線にするだけの単純な色違い**（要件定義
    6.19）。既定は False（黒）で、入れていない（False）作品では項目ごと
    省く。`locked` と同じ線引き。
    """

    center: tuple[float, float]
    hole: float
    count: int
    width: float
    seed: int
    white: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "center": [self.center[0], self.center[1]],
            "hole": self.hole,
            "count": self.count,
            "width": self.width,
            "seed": self.seed,
        }
        if self.white:
            data["white"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> FocusLines:
        """**範囲外は切り詰めずに弾く。**

        黙って直すと、書き出した値と読み戻した値が食い違い、保存のたびに
        形が変わる（`Tail.root_y` と同じ → 要件定義 5章）。

        ここで見るのは「読んでよい値か」だけで、掴んで動かせる範囲
        （`focus.HOLE_MAX` など）はこれより狭い。操作の都合と保存形式の
        正しさは別のものとして持つ。
        """
        d = v.req_mapping(data, where)
        hole = v.number(d, "hole", where)
        width = v.number(d, "width", where)
        count = v.integer(d, "count", where)
        seed = v.integer(d, "seed", where)
        if not 0.0 <= hole <= 1.0:
            raise ProjectFormatError(f"{where}.hole: 0.0〜1.0 の範囲外です（{hole}）")
        if not 0.0 <= width <= 1.0:
            raise ProjectFormatError(f"{where}.width: 0.0〜1.0 の範囲外です（{width}）")
        if not FOCUS_COUNT_MIN <= count <= FOCUS_COUNT_MAX:
            raise ProjectFormatError(
                f"{where}.count: {FOCUS_COUNT_MIN}〜{FOCUS_COUNT_MAX} の範囲外です（{count}）"
            )
        if seed < 0:
            raise ProjectFormatError(f"{where}.seed: 0 以上が必要です（{seed}）")
        return cls(
            center=v.point(d["center"], f"{where}.center")
            if "center" in d
            else (0.5, 0.5),
            hole=hole,
            count=count,
            width=width,
            seed=seed,
            white=v.flag(d, "white", where, False),
        )


# 本数として読んでよい範囲。集中線と同じ値だが**定数は別に持つ**。
# 片方の上限を変えたときに、もう片方まで巻き添えで変わらないようにする
FLOW_COUNT_MIN = 4
FLOW_COUNT_MAX = 400


@dataclass
class FlowLines:
    """流線（要件定義 6.26）。**コマの属性として1つだけ持つ。**

    `FocusLines`（集中線）とは**別の項目**で、混ぜない。集中線にしか無い
    `center` / `hole` と、流線にしか無い `angle` / `length` が1つの型に
    同居すると、**どの組み合わせが正しいのかを型が語れなくなる**。

    集中線と同じく `id` も `z` も矩形も持たず、**常にコマいっぱいに
    広がる**。長さも1つも絶対値で持たない（`width` は短辺、`length` は
    対角線に対する割合）。

    `angle` は線の向き（度）。0 が水平・右向きで、`-180`〜`180` に畳んで
    持つ（画像の `rotation` と同じ → `geometry.normalize_angle`）。

    `seed` は形のばらつきを決める数字。**保存しないと開くたびに形が変わり**、
    書き出した PNG と画面が食い違う。形を作る側は `random` を使わず自前の
    擬似乱数を回す（→ `manga_layout.noise`）。

    **既定値を持たせていない。** 出発点の値は `FlowSettings` にあり、
    2か所に持つと片方だけ古くなる。作るときは `flow.default_flow()` を通す。
    """

    angle: float
    count: int
    width: float
    length: float
    seed: int
    white: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "angle": self.angle,
            "count": self.count,
            "width": self.width,
            "length": self.length,
            "seed": self.seed,
        }
        if self.white:
            data["white"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> FlowLines:
        """**範囲外は切り詰めずに弾く。** 理由は `FocusLines` と同じ。

        ただし `angle` だけは弾かずに畳む。角度は周期的な量なので、
        範囲外を弾くと -10 度のような正しい値まで読めなくなる。畳む操作は
        何度かけても結果が変わらないので、書き出した値と読み戻した値が
        食い違うこともない（要件定義 6.26）。
        """
        d = v.req_mapping(data, where)
        width = v.number(d, "width", where)
        length = v.number(d, "length", where)
        count = v.integer(d, "count", where)
        seed = v.integer(d, "seed", where)
        if not 0.0 <= width <= 1.0:
            raise ProjectFormatError(f"{where}.width: 0.0〜1.0 の範囲外です（{width}）")
        if not 0.0 <= length <= 1.0:
            raise ProjectFormatError(
                f"{where}.length: 0.0〜1.0 の範囲外です（{length}）"
            )
        if not FLOW_COUNT_MIN <= count <= FLOW_COUNT_MAX:
            raise ProjectFormatError(
                f"{where}.count: {FLOW_COUNT_MIN}〜{FLOW_COUNT_MAX} の範囲外です（{count}）"
            )
        if seed < 0:
            raise ProjectFormatError(f"{where}.seed: 0 以上が必要です（{seed}）")
        return cls(
            angle=normalize_angle(v.number(d, "angle", where, 0.0)),
            count=count,
            width=width,
            length=length,
            seed=seed,
            white=v.flag(d, "white", where, False),
        )


# トーンの見た目（要件定義 6.27）。**置き換えた先に何を描くか**だけが違い、
# どこを置き換えるか（しきい値・細さ・矩形）は3つとも同じ。
#
# `gray`（均一な灰色）と `white`（白抜き）は、**クリスタで本物のトーンを
# 貼る前提の絵**でもある。斜線を敷いたままだと、網点の隙間から下の斜線が
# 透ける（→ 6.27「マスクをそのまま出す」）。
#
# 網点はここに並べない。粒が細かく、画面の縮小表示でも書き出しの縮小でも
# モアレが出る（→ 6.27）。
TONE_KIND_STRIPES = "stripes"
TONE_KIND_GRAY = "gray"
TONE_KIND_WHITE = "white"
TONE_KINDS = (TONE_KIND_STRIPES, TONE_KIND_GRAY, TONE_KIND_WHITE)


@dataclass
class Tone:
    """画像の暗い所を斜線・灰色・白に置き換える設定（要件定義 6.27）。

    **画像の属性として1つだけ持つ。** コマではなく画像に付けるのは、
    「この絵だけ黒ベタのまま残したい」が普通にあるため。

    **長さを1つも絶対値で持たない。** `pitch` と `thin` は画像の短辺に
    対する割合、`area` は画像に対する割合。px で持つと、画面用の縮小版
    （長辺 1,600px）と書き出しの原寸（2,048px 程度）で見た目が変わる
    （集中線・流線が長さを持たないのと同じ理由 → 6.16、6.26）。

    `area` が `None` なら画像全体。矩形で絞るのは、しきい値と太さで
    拾いきれないもの——服の模様・目の黒・小物のベタ——を位置で外すため。

    **既定値を持たせていない。** 出発点の値は `ToneSettings` にあり、
    2か所に持つと片方だけ古くなる。作るときは `tone.default_tone()` を通す。
    """

    threshold: int
    angle: float
    pitch: float
    density: float
    thin: float
    area: Rect | None = None
    # 見た目（`TONE_KINDS`）。**斜線以外では `angle` と `pitch` が効かないが、
    # 値は捨てずに持ち続ける。** 捨てると、斜線に戻したときに調整が消える
    kind: str = TONE_KIND_STRIPES

    def key(self) -> tuple:
        """焼いた1枚を覚えておくときの鍵。**同じ設定なら同じ鍵になる。**

        `area` は `Rect` が dataclass なので、そのままでは辞書の鍵に
        できない。組にして畳んでおく。

        **`kind` も混ぜる。** 混ぜずにいると、種類を切り替えても前の1枚が
        引かれて絵が変わらない。効かない値（灰色のときの `angle`）まで
        鍵に残るが、覚え直しが1枚増えるだけで害は無い。
        """
        area = None if self.area is None else (
            self.area.x, self.area.y, self.area.w, self.area.h
        )
        return (
            self.threshold, self.angle, self.pitch, self.density, self.thin, area,
            self.kind,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "threshold": self.threshold,
            "angle": self.angle,
            "pitch": self.pitch,
            "density": self.density,
            "thin": self.thin,
            # **省かない。** 省いてよいのは「無い」に意味がある `area` だけで、
            # 種類は必ず1つ決まっている
            "kind": self.kind,
        }
        if self.area is not None:
            data["area"] = self.area.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Tone:
        """**範囲外は切り詰めずに弾く。** 理由は `FocusLines` と同じ。

        `angle` だけは弾かずに畳む（周期的な量なので、範囲外を弾くと
        -45 度のような正しい値まで読めなくなる → `FlowLines`）。

        `area` は**はみ出していても弾かない**。0〜1 の外は絵が無いだけで
        破綻せず、画像の縁で自然に切れる（要件定義 10.1）。

        `kind` を省いたファイルは**斜線**として読む。種類を足す前に保存した
        作品がそのまま開ける（セリフの `direction` と同じ扱い）。
        """
        d = v.req_mapping(data, where)
        threshold = v.integer(d, "threshold", where)
        if not 0 <= threshold <= 255:
            raise ProjectFormatError(
                f"{where}.threshold: 0〜255 の範囲外です（{threshold}）"
            )
        for name in ("pitch", "density", "thin"):
            value = v.number(d, name, where)
            if not 0.0 <= value <= 1.0:
                raise ProjectFormatError(
                    f"{where}.{name}: 0.0〜1.0 の範囲外です（{value}）"
                )
        area = d.get("area")
        return cls(
            threshold=threshold,
            angle=normalize_angle(v.number(d, "angle", where, 0.0)),
            pitch=v.number(d, "pitch", where),
            density=v.number(d, "density", where),
            thin=v.number(d, "thin", where),
            area=None if area is None else Rect.from_dict(area, f"{where}.area"),
            kind=v.choice(d, "kind", where, TONE_KINDS, TONE_KIND_STRIPES),
        )


# --------------------------------------------------------------------------
# 配置オブジェクト
# --------------------------------------------------------------------------


@dataclass
class SceneObject:
    """ページに置かれるものの共通部分。"""

    id: str
    z: int = 0


@dataclass
class ImageObject(SceneObject):
    """コマに貼られた画像。

    大きさは倍率ではなく `rect` そのもので表す。倍率と矩形の二重管理をやめて、
    どちらが正なのか分からなくなる状態を作らないための選択。

    `rotation` は**この型だけが実際に使う**（マークは常に 0 → `StickerObject`）。
    """

    asset: str = ""
    rect: Rect = field(default_factory=lambda: Rect(0.0, 0.0, 0.0, 0.0))
    # 元画像のピクセル寸法。縦横比の維持だけに使う。
    # **単位の換算では触らない。** 最初から px なので、掛けると比が狂う
    src_px: tuple[int, int] = (0, 0)
    # `rect` の中心まわりの傾き（度、時計回りが正、-180〜180）。
    # `rect` 自体は傾けない。回すのは描画・当たり判定・つまみの3か所だけで、
    # ここを 0 にすれば今までと同じ経路を通る（要件定義 6.3）
    rotation: float = 0.0
    opacity: float = 1.0
    # 暗い所を斜線に置き換える設定（→ `Tone`、要件定義 10.1）。
    # **None なら項目ごと書かない**ので、使っていない作品の project.json は
    # この機能を足す前と1文字も変わらない（集中線・流線・付箋と同じ線引き）
    tone: Tone | None = None
    # 切り抜きのマスク（→ 要件定義 10.3）。`assets/` の中の8bitグレースケールPNGで、
    # **元画像と同じピクセル寸法**。掛けていなければ空文字。
    #
    # 絵そのもの（`asset`）は書き換えない。外せば元の絵に戻る、が切り抜きの前提
    mask_asset: str = ""

    TYPE = "image"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.TYPE,
            "asset": self.asset,
            "rect": self.rect.to_dict(),
            "src_px": [self.src_px[0], self.src_px[1]],
            "rotation": self.rotation,
            "opacity": self.opacity,
            "z": self.z,
        }
        if self.tone is not None:
            data["tone"] = self.tone.to_dict()
        # **切り抜いていない画像では項目ごと省く**（トーン・集中線と同じ線引き）。
        # 切り抜きを使っていない作品の project.json が、この機能の前と1文字も
        # 変わらない
        if self.mask_asset:
            data["mask_asset"] = self.mask_asset
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> ImageObject:
        d = v.req_mapping(data, where)
        src_px = v.pixel_size(d.get("src_px", [0, 0]), f"{where}.src_px")
        opacity = v.number(d, "opacity", where, 1.0)
        if not 0.0 <= opacity <= 1.0:
            raise ProjectFormatError(f"{where}.opacity: 0.0〜1.0 の範囲外です（{opacity}）")
        tone = d.get("tone")
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            asset=v.text(d, "asset", where),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            src_px=src_px,
            # 保存は -180〜180 に畳んでいる（→ 上のフィールドの注記）が、
            # 読み込みでは畳んでいなかったため、手編集した 3600 のような
            # 値がそのまま残っていた（2026-08-08 に発見。`FocusLines.angle`
            # など他の角度は読み込みでも畳んでいる）
            rotation=normalize_angle(v.number(d, "rotation", where, 0.0)),
            opacity=opacity,
            tone=None if tone is None else Tone.from_dict(tone, f"{where}.tone"),
            # 古い作品にはこの項目が無い。無ければ切り抜き無しとして読む
            mask_asset=v.text(d, "mask_asset", where, ""),
        )


@dataclass
class Panel(SceneObject):
    """コマ。中の画像を自分の形に切り抜く。"""

    shape: Polygon = field(default_factory=lambda: Polygon.from_rect(Rect(0.0, 0.0, 10.0, 10.0)))
    border: Border = field(default_factory=Border)
    children: list[ImageObject] = field(default_factory=list)
    # 集中線（→ 6.16）。**コマに1つだけ。** 入れていなければ None
    focus_lines: FocusLines | None = None
    # 流線（→ 6.26）。集中線と**別の項目**。こちらもコマに1つだけ
    flow_lines: FlowLines | None = None
    # 位置ロック（→ 6.17）。誤って動かさないためのもの。既定は False
    locked: bool = False

    TYPE = "panel"

    def bounds(self) -> Rect:
        return self.shape.bounds()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.TYPE,
            "shape": {"kind": "polygon", "points": self.shape.to_list()},
            "border": self.border.to_dict(),
            "z": self.z,
            "children": [c.to_dict() for c in self.children],
        }
        # **入れていないコマでは項目ごと省く。** 集中線を使っていない作品の
        # project.json が、この機能の追加前と同じ内容のままになる
        if self.focus_lines is not None:
            data["focus_lines"] = self.focus_lines.to_dict()
        # 流線も同じ。**集中線とは別の項目**にしてある（→ 6.26）
        if self.flow_lines is not None:
            data["flow_lines"] = self.flow_lines.to_dict()
        # ロックしていない（False）コマでは項目ごと省く。理由は集中線と同じ
        if self.locked:
            data["locked"] = True
        return data

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Panel:
        d = v.req_mapping(data, where)
        shape = v.req_mapping(d.get("shape"), f"{where}.shape")
        kind = v.choice(shape, "kind", f"{where}.shape", ("polygon",))
        assert kind == "polygon"  # 現状 polygon のみ。増えたら分岐する
        children_raw = v.req_list(d.get("children", []), f"{where}.children")
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            shape=Polygon.from_list(shape.get("points"), f"{where}.shape.points"),
            border=Border.from_dict(d.get("border", {}), f"{where}.border"),
            children=[
                ImageObject.from_dict(c, f"{where}.children[{i}]")
                for i, c in enumerate(children_raw)
            ],
            # 項目が無い＝集中線なし。この機能より前の作品がそのまま開ける
            focus_lines=(
                FocusLines.from_dict(d["focus_lines"], f"{where}.focus_lines")
                if d.get("focus_lines") is not None
                else None
            ),
            # 項目が無い＝流線なし。この機能より前の作品がそのまま開ける
            flow_lines=(
                FlowLines.from_dict(d["flow_lines"], f"{where}.flow_lines")
                if d.get("flow_lines") is not None
                else None
            ),
            # 項目が無い＝ロックなし。この機能より前の作品がそのまま開ける
            locked=v.flag(d, "locked", where, False),
        )


@dataclass
class BalloonObject(SceneObject):
    """吹き出し。ページ直下に置かれ、コマ枠で切り抜かれない。"""

    style: str = "ellipse"
    rect: Rect = field(default_factory=lambda: Rect(0.0, 0.0, 0.0, 0.0))
    fill: str = "#FFFFFF"
    border: Border = field(default_factory=lambda: Border(width=2.5))
    tail: Tail = field(default_factory=Tail)
    attached_panel_id: str | None = None

    TYPE = "balloon"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.TYPE,
            "style": self.style,
            "rect": self.rect.to_dict(),
            "fill": self.fill,
            "border": self.border.to_dict(),
            "tail": self.tail.to_dict(),
            "attached_panel_id": self.attached_panel_id,
            "z": self.z,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> BalloonObject:
        d = v.req_mapping(data, where)
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            style=v.choice(d, "style", where, BALLOON_STYLES, "ellipse"),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            fill=v.color(d, "fill", where, "#FFFFFF"),
            border=Border.from_dict(d.get("border", {"width": 2.5}), f"{where}.border"),
            tail=Tail.from_dict(d.get("tail", {}), f"{where}.tail"),
            attached_panel_id=v.opt_text(d, "attached_panel_id", where),
        )


@dataclass
class TextObject(SceneObject):
    """セリフ。横書き・縦書きの両方に対応する（要件定義 6.5、6.11）。"""

    content: str = ""
    rect: Rect = field(default_factory=lambda: Rect(0.0, 0.0, 0.0, 0.0))
    font: Font = field(default_factory=Font)
    align: str = "center"
    direction: str = DEFAULT_TEXT_DIRECTION
    attached_panel_id: str | None = None
    # 吹き出しの上に置いたセリフは、その吹き出しに付いて回る（要件定義 6.5）。
    # コマへの紐づけとは別に持つ。吹き出しはコマの中で単独に動かせるので、
    # コマだけを見ていると吹き出しを動かしたときにセリフが取り残される
    attached_balloon_id: str | None = None

    TYPE = "text"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.TYPE,
            "content": self.content,
            "rect": self.rect.to_dict(),
            "font": self.font.to_dict(),
            "align": self.align,
            "direction": self.direction,
            "attached_panel_id": self.attached_panel_id,
            "attached_balloon_id": self.attached_balloon_id,
            "z": self.z,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> TextObject:
        d = v.req_mapping(data, where)
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            content=v.text(d, "content", where, ""),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            font=Font.from_dict(d.get("font", {}), f"{where}.font"),
            align=v.choice(d, "align", where, TEXT_ALIGNS, "center"),
            # **既定は "horizontal" のまま。`DEFAULT_TEXT_DIRECTION` に
            # 追随させてはいけない。** この項目が無いファイルは、縦書きが
            # まだ無かった頃に書かれたもの。縦書きとして読むと、既にある
            # 原稿の見た目が開いた瞬間に変わる
            direction=v.choice(d, "direction", where, TEXT_DIRECTIONS, "horizontal"),
            attached_panel_id=v.opt_text(d, "attached_panel_id", where),
            attached_balloon_id=v.opt_text(d, "attached_balloon_id", where),
        )


@dataclass
class StickerObject(SceneObject):
    """マーク（！など）。ページ直下に置かれ、コマ枠で切り抜かれない。

    中身は画像なので `ImageObject` と同じ項目を持つ。**違うのは置き場所**で、
    コマの子ではないため切り抜かれない。マークはコマからはみ出して置くのが
    普通なので、切り抜かれる置き方では要求そのものを満たせない（要件定義 6.14）。

    `rotation` は常に 0。**傾きは素材の PNG に焼き込む。** 角度違いが要る
    ときは素材を1枚足す（要件定義 6.14）。`ImageObject` は 2026-08-05 に
    回転できるようにしたが、**マークはこの扱いのまま**。利用者が持ち込む
    画像と違い、素材はこちらで差し替えられる。
    """

    # どの組み込み素材から作ったか。**描画には使わない**（描くのは `asset`）。
    # 画面の呼び名を引くためだけに持つ
    kind: str = ""
    asset: str = ""
    rect: Rect = field(default_factory=lambda: Rect(0.0, 0.0, 0.0, 0.0))
    src_px: tuple[int, int] = (0, 0)
    rotation: float = 0.0
    opacity: float = 1.0
    attached_panel_id: str | None = None

    TYPE = "sticker"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.TYPE,
            "kind": self.kind,
            "asset": self.asset,
            "rect": self.rect.to_dict(),
            "src_px": [self.src_px[0], self.src_px[1]],
            "rotation": self.rotation,
            "opacity": self.opacity,
            "attached_panel_id": self.attached_panel_id,
            "z": self.z,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> StickerObject:
        d = v.req_mapping(data, where)
        src_px = v.pixel_size(d.get("src_px", [0, 0]), f"{where}.src_px")
        opacity = v.number(d, "opacity", where, 1.0)
        if not 0.0 <= opacity <= 1.0:
            raise ProjectFormatError(f"{where}.opacity: 0.0〜1.0 の範囲外です（{opacity}）")
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            # **選択肢を固定しない。** 素材が増えたあとの作品を古いアプリで
            # 開いたとき、知らない値で読み込みごと断るのは重すぎる。
            # 呼び名の表に無い値は「マーク」と呼ぶだけにする（→ 5章）
            kind=v.text(d, "kind", where, ""),
            asset=v.text(d, "asset", where),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            src_px=src_px,
            # ImageObject と同じく読み込みでも畳む（→ そちらの注記）。
            # マークは常に 0 のはずだが、手編集ファイルへの備えは揃える
            rotation=normalize_angle(v.number(d, "rotation", where, 0.0)),
            opacity=opacity,
            attached_panel_id=v.opt_text(d, "attached_panel_id", where),
        )


# ページ直下に置けるもの。将来 EffectObject などを足すときは、
# **ここへの登録だけでは足りない。** 触る場所は他に6ファイル・約15箇所ある
# （2026-08-05、マークを足した実績から数えた）。次に足す人（＝半年後の自分）が
# ここだけ直して安心してしまわないよう、一覧をここに置く。
#
#     manga_layout/model.py
#         - 型そのもの（dataclass。BalloonObject などに倣う）
#         - `_FLOATING_TYPES`（このすぐ下）
#         - `floating_layer`（重なりの段。どの高さに挟むかを決める → 下の「表示レイヤー」）
#         - `_ID_PREFIXES`（→ `Project.duplicate` の複製で使う）
#         - `Project.add_〇〇`（生成口。id の採番をここに絞るため）
#         - `Project.scale_lengths`（version 1 の mm→px 換算。長さを持つ項目があれば要る）
#     manga_layout/layout.py
#         - `〇〇_at(page, x, y)`（当たり判定。描く順と同じ順で拾う → `_pick_at`）
#     manga_layout/ui/state.py
#         - `selected_〇〇` プロパティ
#         - `object_label`（削除・複製で出す呼び名）
#     manga_layout/ui/canvas.py
#         - `_pick_at`（クリックで拾う優先順位）
#         - `_apply_move` / `_apply_resize`（確定時の型ごとの分岐）
#         - `mousePressEvent` の道具分岐（クリックで置ける道具にする場合）
#     manga_layout/ui/window.py
#         - `_hint`（状態表示）
#         - `delete_target`（「削除」で消える対象と呼び名）
#     manga_layout/ui/menus.py
#         - メニュー部品（`StickerMenu` に倣ってクラスを1つ足し、
#           `MainWindow.__init__` で生成して `_menus` に登録する）
#     manga_layout/ui/context_menu.py
#         - `ContextMenu.build`（右クリックの出し分け）
#         - `_add_place_here`（「ここに〜を追加」）
#     manga_layout/ui/render.py
#         - `draw_floating` の分岐（描画）
#
# **全部を1つのテストで確かめる術は無い。** 足したあと、新しい型を1つ置いて
# 「選べる・動かせる・消せる・保存して開き直せる」を実機かテストで一通り触ること。
FloatingObject = BalloonObject | StickerObject | TextObject
_FLOATING_TYPES: dict[str, Any] = {
    BalloonObject.TYPE: BalloonObject,
    StickerObject.TYPE: StickerObject,
    TextObject.TYPE: TextObject,
}


def _floating_from_dict(data: Any, where: str) -> FloatingObject:
    d = v.req_mapping(data, where)
    kind = v.text(d, "type", where)
    factory = _FLOATING_TYPES.get(kind)
    if factory is None:
        known = " / ".join(sorted(_FLOATING_TYPES))
        raise ProjectFormatError(
            f"{where}.type: 知らない種別です（{kind!r}）。扱えるのは {known}"
        )
    return factory.from_dict(d, where)


# --------------------------------------------------------------------------
# 表示レイヤー
# --------------------------------------------------------------------------

# 重なりの順は**種類で決まる**。奥から手前へ
#
#     用紙 → コマ（と中の画像）→ 吹き出し → マーク → セリフ
#
# 用紙とコマは描く場所そのものが分かれているので、ここで段を持つのは
# ページ直下のもの（吹き出し・マーク・セリフ）だけ。
#
# **z ではこの順を覆せない。** 以前は z だけで重ねていたため、セリフを
# 書いたあとに吹き出しを載せると、後から作った吹き出しのほうが z が大きく、
# 白い塗りが文字を塗り潰して**セリフが消えた**。吹き出しは後から足すのが
# 普通の手順なので、作った順で見た目が変わらないようにする。
#
# マークは吹き出しの上・**セリフの下**（要件定義 6.14）。上に置くと、
# 位置を誤ったときにセリフが読めなくなる。上と同じ形の事故なので、
# 段の順で最初から起こらないようにする。
#
# **段は保存しない。** 種類から毎回ここで求めるので、段を1つ増やしても
# 既にある作品には影響しない。
#
# z は同じ段の中の前後（吹き出しどうし・マークどうし）にだけ効く。
LAYER_BALLOON = 0
LAYER_STICKER = 1
LAYER_TEXT = 2


def floating_layer(obj: FloatingObject) -> int:
    """そのものが載る段。大きいほど手前。"""
    if isinstance(obj, TextObject):
        return LAYER_TEXT
    if isinstance(obj, StickerObject):
        return LAYER_STICKER
    return LAYER_BALLOON


def floating_order(obj: FloatingObject) -> tuple[int, int]:
    """奥から手前へ並べるときの鍵。`sorted(page.floating, key=...)` に使う。

    段が先、z が後。当たり判定（`layout.text_at` → `layout.sticker_at` →
    `layout.balloon_at` の順に見る）もこの順に合わせてある。描く順と拾う順が
    ずれると、見えているものを掴めなくなる。
    """
    return (floating_layer(obj), obj.z)


# --------------------------------------------------------------------------
# 斜めに割ったコマの組
# --------------------------------------------------------------------------


@dataclass
class SlantPair:
    """1つの矩形を斜めに割って生まれた、コマ2枚の関係。

    2枚は普通の `Panel` として `Page.panels` に並んでおり、切り抜きも
    画像の所属も吹き出しの紐づけも通常どおり動く。ここが持つのは
    **形を決めるパラメータだけ**。

    パラメータをコマ側に持たせず1箇所に集めているのは、2枚で値が
    食い違う経路を無くすため（`attached_panel_id` と同じ考え方）。

    `ratio` を絶対座標ではなく割合にしてあるのが要。外側の矩形を縮めても
    境界が外へ飛び出さず、拡大縮小に素直に追従する。
    """

    left_id: str
    right_id: str
    ratio: float
    angle: float
    direction: str

    def members(self) -> tuple[str, str]:
        return (self.left_id, self.right_id)

    def flipped(self) -> SlantPair:
        """傾きの向きを反転した組を返す。"""
        other = SLANT_LEFT if self.direction == SLANT_RIGHT else SLANT_RIGHT
        return dataclasses.replace(self, direction=other)

    def to_dict(self) -> dict[str, Any]:
        return {
            "left": self.left_id,
            "right": self.right_id,
            "ratio": self.ratio,
            "angle": self.angle,
            "direction": self.direction,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> SlantPair:
        d = v.req_mapping(data, where)
        ratio = v.number(d, "ratio", where)
        if not 0.0 < ratio < 1.0:
            raise ProjectFormatError(
                f"{where}.ratio: 0 と 1 の間である必要があります（{ratio}）"
            )
        angle = v.positive(d, "angle", where)
        if angle >= 90.0:
            raise ProjectFormatError(
                f"{where}.angle: 90 度未満である必要があります（{angle}）"
            )
        return cls(
            left_id=v.text(d, "left", where),
            right_id=v.text(d, "right", where),
            ratio=ratio,
            angle=angle,
            direction=v.choice(d, "direction", where, SLANT_DIRECTIONS),
        )


# --------------------------------------------------------------------------
# ページ
# --------------------------------------------------------------------------

# 付箋の色（要件定義 6.18）。**意味は割り当てない**。3色は識別のためだけに使い、
# 合う意味はそのつど一行メモに書く。増やすと「どの色にしたか」を思い出す手間が
# 色を見て分かる利得を上回るため、3色で止める。
NOTE_COLORS = ("yellow", "pink", "blue")


@dataclass
class PageNote:
    """付箋（要件定義 6.18）。サムネイル一覧でだけ見える作業用の覚え書き。

    用紙の絵には出ない（本画面にも書き出しにも出さない）ので、`PageRenderer`
    は素通りする。一覧の描画側（`ui/pages.py`）だけがこれを読む。
    """

    color: str  # NOTE_COLORS のいずれか
    text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"color": self.color}
        if self.text:
            out["text"] = self.text
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> PageNote:
        d = v.req_mapping(data, where)
        return cls(
            color=v.choice(d, "color", where, NOTE_COLORS),
            text=v.opt_text(d, "text", where),
        )


@dataclass
class PageRough:
    """ラフ（下敷き → 要件定義 6.23）。紙に描いた下書きをページの一番下に敷く。

    **画像でもマークでもなく、ページの属性として持つ。** どちらかにすると
    「選べる・掴める・複製できる」が付いて回るが、ラフはなぞる相手であって
    作品の中身ではない。掴めるのは調整の道具に持ち替えている間だけでよい。

    `faded` は**青く淡くしているか**（青鉛筆の下書きに寄せた見え方）。
    透明度そのものは持たない。作品ではなく **この PC での見やすさ**の話なので、
    `settings.json` の `rough_opacity` から取る（→ `settings.AppSettings`）。
    ここに焼き込むと、同じ作品を別の PC で開いたときに相手の好みを上書きする。
    """

    asset: str
    rect: Rect
    src_px: tuple[int, int]
    faded: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "rect": self.rect.to_dict(),
            "src_px": [self.src_px[0], self.src_px[1]],
            # **省かない。** 2値しか無いうえ `rough` 自体が省ける項目なので、
            # 「書いてあれば読む」で足りる。省くと、既定がどちらだったかを
            # JSON を読む人が思い出さないと分からなくなる
            "faded": self.faded,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> PageRough:
        d = v.req_mapping(data, where)
        src_px = v.pixel_size(d.get("src_px", [0, 0]), f"{where}.src_px")
        return cls(
            asset=v.text(d, "asset", where),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            src_px=src_px,
            faded=v.flag(d, "faded", where, True),
        )


@dataclass
class Page:
    id: str
    size: Size = DEFAULT_PAGE_SIZE
    panels: list[Panel] = field(default_factory=list)
    floating: list[FloatingObject] = field(default_factory=list)
    slant_pairs: list[SlantPair] = field(default_factory=list)
    # 付箋（→ 6.18）。**ページに1つだけ。** 入れていなければ None
    note: PageNote | None = None
    # ラフ（→ 6.23）。**ページに1枚だけ。** 敷いていなければ None
    rough: PageRough | None = None

    # -- 検索 --------------------------------------------------------------

    def panel(self, panel_id: str) -> Panel:
        for p in self.panels:
            if p.id == panel_id:
                return p
        raise KeyError(f"コマが見つかりません: {panel_id}")

    def find(self, object_id: str) -> SceneObject | None:
        """ページ内のどこかにある1件を id で探す（コマの中の画像も含む）。"""
        for p in self.panels:
            if p.id == object_id:
                return p
            for c in p.children:
                if c.id == object_id:
                    return c
        for f in self.floating:
            if f.id == object_id:
                return f
        return None

    def panel_of_image(self, image_id: str) -> Panel | None:
        """その画像が入っているコマ。画像はコマの子なので必ず1つに属する。"""
        for p in self.panels:
            for c in p.children:
                if c.id == image_id:
                    return p
        return None

    def attached_to(self, panel_id: str) -> list[FloatingObject]:
        """そのコマに紐づいた吹き出し・マーク・セリフ。"""
        return [f for f in self.floating if f.attached_panel_id == panel_id]

    def slant_pair_of(self, panel_id: str) -> SlantPair | None:
        """そのコマが属する斜めの組。属していなければ None。

        1枚のコマが入れる組は必ず1つまで。この決まりがあるおかげで、
        移動もリサイズも「相方を1枚探して一緒に動かす」で済んでいる。
        （代わりに、左右とも斜めの平行四辺形のコマは作れない）
        """
        for pair in self.slant_pairs:
            if panel_id in pair.members():
                return pair
        return None

    def slant_bounds(self, pair: SlantPair) -> Rect:
        """斜めの組の外側の矩形。

        2枚の外接矩形を合わせると元の矩形が厳密に戻るので、保存せず
        毎回ここで求めている。持たなければ食い違いようがない。
        """
        a = self.panel(pair.left_id).shape.bounds()
        b = self.panel(pair.right_id).shape.bounds()
        x, y = min(a.x, b.x), min(a.y, b.y)
        return Rect(x, y, max(a.right, b.right) - x, max(a.bottom, b.bottom) - y)

    def iter_objects(self) -> Iterator[SceneObject]:
        for p in self.panels:
            yield p
            yield from p.children
        yield from self.floating

    # -- 編集 --------------------------------------------------------------

    def texts_on_balloon(self, balloon_id: str) -> list[TextObject]:
        """その吹き出しの上に置かれたセリフ。"""
        return [
            f
            for f in self.floating
            if isinstance(f, TextObject) and f.attached_balloon_id == balloon_id
        ]

    def move_balloon(self, balloon_id: str, dx: float, dy: float) -> None:
        """吹き出しを動かす。上に乗ったセリフも一緒に動く。

        **しっぽの先端は動かさない。** 先端はしゃべっている人物を指す
        ページ座標なので、吹き出しの置き場所を変えても指す相手は変わらない
        （要件定義 4章）。
        """
        balloon = self.find(balloon_id)
        if not isinstance(balloon, BalloonObject):
            raise KeyError(f"吹き出しが見つかりません: {balloon_id}")
        balloon.rect = balloon.rect.translated(dx, dy)
        for text in self.texts_on_balloon(balloon_id):
            text.rect = text.rect.translated(dx, dy)

    def move_panel(self, panel_id: str, dx: float, dy: float) -> None:
        """コマを動かす。中の画像と、紐づいた吹き出し・セリフも一緒に動く。

        斜めの組に入っているコマは、**相方も同じだけ動く**。片方だけ動かすと
        噛み合っていた斜めの辺が離れ、組の意味が無くなる。
        """
        pair = self.slant_pair_of(panel_id)
        # **組の2枚を通じて1つの控え。** 別々に持つと、「コマAに紐づく
        # 吹き出しの上のセリフ」が「コマBにも直接紐づいている」ような
        # 手編集ファイルで、Aの回・Bの回それぞれから動かされて二重に
        # ずれる（2026-08-08 に発見）
        moved: set[str] = set()
        for target in (pair.members() if pair is not None else (panel_id,)):
            self._move_panel_only(target, dx, dy, moved)

    def _move_panel_only(
        self, panel_id: str, dx: float, dy: float, moved: set[str]
    ) -> None:
        """コマ1枚とその持ち物だけを動かす。

        要件定義 4章で狙った挙動そのもの。吹き出しは親子関係ではなく
        `attached_panel_id` の紐づけなので、追随はするが切り抜かれない。

        吹き出しの上に乗ったセリフも連れていく。**同じものを2回動かさない**
        よう、動かした id を `moved` に控えながら進む。コマにもその上の
        吹き出しにも紐づいたセリフがあると、二重に動いて位置がずれる。
        `moved` は呼び出し元（`move_panel`）が持ち、斜めの組の2回の呼び出し
        を通じて共有する。
        """
        panel = self.panel(panel_id)
        panel.shape = panel.shape.translated(dx, dy)
        for child in panel.children:
            child.rect = child.rect.translated(dx, dy)

        for obj in self.attached_to(panel_id):
            if obj.id in moved:
                continue
            obj.rect = obj.rect.translated(dx, dy)
            moved.add(obj.id)
            if isinstance(obj, BalloonObject):
                # しっぽの先端はページ座標なので、これも動かさないと形が崩れる
                obj.tail = obj.tail.translated(dx, dy)
                for text in self.texts_on_balloon(obj.id):
                    if text.id in moved:
                        continue
                    text.rect = text.rect.translated(dx, dy)
                    moved.add(text.id)

    def remove_floating(self, object_id: str) -> FloatingObject:
        """ページ直下のもの（吹き出し・マーク・セリフ）を消す。

        吹き出しを消しても、上に乗っていたセリフは消さず紐づけだけ外す。
        コマ削除と同じ考え方で、手のかかっているセリフを巻き添えにしない。
        """
        for obj in self.floating:
            if obj.id == object_id:
                self.floating.remove(obj)
                if isinstance(obj, BalloonObject):
                    for text in self.texts_on_balloon(object_id):
                        text.attached_balloon_id = None
                return obj
        raise KeyError(f"見つかりません: {object_id}")

    def remove_panel(self, panel_id: str) -> Panel:
        """コマを消す。中の画像も一緒に消える。

        紐づいていた吹き出し・セリフは消さず、紐づけだけ外してページに残す。
        セリフはコマより手間がかかっているので、巻き添えで消さない。

        斜めの組の片方だった場合は、組の関係だけを解く。**残ったコマは
        斜めの辺を持ったまま**その場に残る。外側の矩形に戻すと、消した
        覚えのない方向へコマが伸びて驚きが大きい。
        """
        panel = self.panel(panel_id)
        self.panels.remove(panel)
        pair = self.slant_pair_of(panel_id)
        if pair is not None:
            self.slant_pairs.remove(pair)
        for obj in self.attached_to(panel_id):
            obj.attached_panel_id = None
        return panel

    # -- 変換 --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "size": self.size.to_dict(),
            "panels": [p.to_dict() for p in self.panels],
            "floating": [f.to_dict() for f in self.floating],
        }
        # 斜めの組が無いページには項目ごと書かない。斜めを使っていない
        # 作品の project.json が、この機能の追加前と同じ内容のままになる
        if self.slant_pairs:
            out["slant_pairs"] = [s.to_dict() for s in self.slant_pairs]
        # 付箋の無いページでは項目ごと省く。理由は slant_pairs と同じ
        if self.note is not None:
            out["note"] = self.note.to_dict()
        # ラフも同じ（→ 6.23）。敷いていない作品の project.json は
        # この機能の追加前と一字一句変わらない
        if self.rough is not None:
            out["rough"] = self.rough.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> Page:
        d = v.req_mapping(data, where)
        panels_raw = v.req_list(d.get("panels", []), f"{where}.panels")
        floating_raw = v.req_list(d.get("floating", []), f"{where}.floating")
        slant_raw = v.req_list(d.get("slant_pairs", []), f"{where}.slant_pairs")
        return cls(
            id=v.text(d, "id", where),
            size=Size.from_dict(d.get("size", DEFAULT_PAGE_SIZE.to_dict()), f"{where}.size"),
            panels=[Panel.from_dict(p, f"{where}.panels[{i}]") for i, p in enumerate(panels_raw)],
            floating=[
                _floating_from_dict(f, f"{where}.floating[{i}]")
                for i, f in enumerate(floating_raw)
            ],
            slant_pairs=[
                SlantPair.from_dict(s, f"{where}.slant_pairs[{i}]")
                for i, s in enumerate(slant_raw)
            ],
            # 項目が無い＝付箋なし。この機能より前の作品がそのまま開ける
            note=(
                PageNote.from_dict(d["note"], f"{where}.note")
                if d.get("note") is not None
                else None
            ),
            # 項目が無い＝ラフなし（→ 6.23）。付箋と同じ読み方
            rough=(
                PageRough.from_dict(d["rough"], f"{where}.rough")
                if d.get("rough") is not None
                else None
            ),
        )


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------

# 型ごとの ID の接頭辞。**複製で使う**（→ `Project.duplicate`）。
# `add_*` は種類ごとに分かれているので接頭辞を直に書けるが、複製は
# 選んだものの型で分かれるため、1箇所から引く形にする
_ID_PREFIXES: dict[type, str] = {
    Panel: ID_PREFIX_PANEL,
    ImageObject: ID_PREFIX_IMAGE,
    BalloonObject: ID_PREFIX_BALLOON,
    StickerObject: ID_PREFIX_STICKER,
    TextObject: ID_PREFIX_TEXT,
}


def _detached_copy(obj: SceneObject) -> Any:
    """保存形式を往復させた深い写し。**id は元のまま**なので、呼んだ側が振り直す。

    `Project.copy()` と同じ理屈（→ 要件定義 6.8）。項目を手で書き写す形に
    すると、あとから項目を足したときに写し忘れても静かに通ってしまう。
    ここを通しておけば、`to_dict()` への追加を忘れた項目は Undo と同じく
    複製でも落ちるので、抜けが1か所に集まる。
    """
    return type(obj).from_dict(obj.to_dict(), f"{obj.id} の複製")


def _fill_legacy_length_defaults(pages_raw: list) -> None:
    """v1（mm 時代）ファイルの生の辞書に、欠けた長さの既定値を先回りで埋める。

    `Panel.from_dict` などが「項目が無いときの既定値」に使うのは**今の
    px 基準の値**（例: `Border().width == 3.5`）。version 1 のファイルは
    `Project.from_dict` が組み立て後に丸ごと `scale_lengths(MM_TO_PX)`
    （≈5.9倍）で換算するため、この既定値がそのまま換算対象に入ると
    二重に掛かる（文字サイズ 42px の既定が 42 × 5.9 ≈ 248px になる、
    枠線なら 3.5 → 20.7px。2026-08-08 に発見）。

    ここでは v1 ファイルに限り、**値が丸ごと欠けている項目にだけ**
    「今の既定値 ÷ MM_TO_PX」を先に埋める。あとの `scale_lengths` が
    掛け戻すので、結果は今の既定値と一致する（＝手で壊れた箇所は
    「今の新規作成と同じ見た目」で開ける）。**値がある項目には一切
    触らない**——本物の mm 値まで書き換えると、正しく保存されていた
    ファイルまで壊してしまう。

    アプリが書いた v1 ファイルは `to_dict()` が全項目を書くため、
    実際に効くのは手で編集して項目を削ったファイルだけ。
    """
    panel_width = Border().width
    balloon_width = BalloonObject(id="_").border.width
    tail_width = Tail().width
    font_size = Font().size_px

    for page in pages_raw:
        if not isinstance(page, dict):
            continue
        for panel in page.get("panels", None) or []:
            if not isinstance(panel, dict):
                continue
            _fill_missing_length(panel, "border", ("width",), panel_width)
        for obj in page.get("floating", None) or []:
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "balloon":
                _fill_missing_length(obj, "border", ("width",), balloon_width)
                _fill_missing_length(obj, "tail", ("width",), tail_width)
            elif obj.get("type") == "text":
                _fill_missing_length(obj, "font", ("size_px", "size_mm"), font_size)


def _fill_missing_length(
    obj: dict, key: str, names: tuple[str, ...], default_px: float
) -> None:
    """`obj[key]` の中に `names` のどれも無ければ、換算前の既定値を差し込む。

    埋めるのは `names` の先頭（px 側の名前）。`size_mm` のような mm 時代の
    別名を併せて渡すのは、**どちらか一方でも書いてあれば「値がある」**と
    見なすため。

    **`obj[key]` が辞書でないときは何もしない。** 書いてあるのが文字列や
    数値だった場合にここで埋めると、`{**"thick"}` のような**生の
    `TypeError`** になり、開く側（`ui.project_io`）が拾えずアプリごと落ちる。
    型の間違いを「どこが」付きで報告するのは `Border.from_dict` などの
    検証層の役目なので、素通しでそちらへ渡す（2026-08-09 に発見。
    `src_px` を `v.pixel_size` へ通したのと同じ線引き）。**`null` も同じ**
    ——欠けているのではなく「型の違う値が書いてある」ので、直さず報告する。
    """
    sub = obj.get(key)
    if key in obj and not isinstance(sub, dict):
        return
    if isinstance(sub, dict) and any(name in sub for name in names):
        return
    obj[key] = {**(sub or {}), names[0]: default_px / MM_TO_PX}


@dataclass
class Project:
    """作品ひとつ分。project.json と 1 対 1 に対応する。

    オブジェクトの生成は必ずこのクラスの `add_*` を通す。
    ID の採番口をひとつに絞ることで、重複した ID が生まれる経路を無くしている
    （重複すると `attached_panel_id` の解決先が狂う）。
    """

    title: str = ""
    default_page_size: Size = DEFAULT_PAGE_SIZE
    reading_direction: str = "rtl"
    pages: list[Page] = field(default_factory=list)
    next_id: int = 1

    # 読み込み時に見つかった軽微な問題。保存対象ではない
    load_warnings: list[str] = field(default_factory=list, compare=False, repr=False)

    # -- 採番 --------------------------------------------------------------

    def _new_id(self, prefix: str) -> str:
        value = self.next_id
        self.next_id += 1
        return f"{prefix}_{value:04d}"

    # -- 生成 --------------------------------------------------------------

    def add_page(self, index: int | None = None, size: Size | None = None) -> Page:
        page = Page(id=self._new_id(ID_PREFIX_PAGE), size=size or self.default_page_size)
        if index is None:
            self.pages.append(page)
        else:
            self.pages.insert(index, page)
        return page

    def add_panel(self, page: Page, rect: Rect) -> Panel:
        z = max((p.z for p in page.panels), default=-1) + 1
        panel = Panel(id=self._new_id(ID_PREFIX_PANEL), z=z, shape=Polygon.from_rect(rect))
        page.panels.append(panel)
        return panel

    def add_image(self, panel: Panel, asset: str, rect: Rect, src_px: tuple[int, int]) -> ImageObject:
        z = max((c.z for c in panel.children), default=-1) + 1
        image = ImageObject(
            id=self._new_id(ID_PREFIX_IMAGE), z=z, asset=asset, rect=rect, src_px=src_px
        )
        panel.children.append(image)
        return image

    def _next_floating_z(self, page: Page) -> int:
        return max((f.z for f in page.floating), default=FLOATING_BASE_Z - 1) + 1

    def add_balloon(
        self, page: Page, rect: Rect, style: str = "ellipse", attached_panel_id: str | None = None
    ) -> BalloonObject:
        if style not in BALLOON_STYLES:
            raise ValueError(f"知らない吹き出しの種類です: {style!r}")
        balloon = BalloonObject(
            id=self._new_id(ID_PREFIX_BALLOON),
            z=self._next_floating_z(page),
            style=style,
            rect=rect,
            attached_panel_id=attached_panel_id,
        )
        page.floating.append(balloon)
        return balloon

    def add_sticker(
        self,
        page: Page,
        kind: str,
        asset: str,
        rect: Rect,
        src_px: tuple[int, int],
        attached_panel_id: str | None = None,
    ) -> StickerObject:
        sticker = StickerObject(
            id=self._new_id(ID_PREFIX_STICKER),
            z=self._next_floating_z(page),
            kind=kind,
            asset=asset,
            rect=rect,
            src_px=src_px,
            attached_panel_id=attached_panel_id,
        )
        page.floating.append(sticker)
        return sticker

    def add_text(
        self, page: Page, content: str, rect: Rect, attached_panel_id: str | None = None
    ) -> TextObject:
        text_obj = TextObject(
            id=self._new_id(ID_PREFIX_TEXT),
            z=self._next_floating_z(page),
            content=content,
            rect=rect,
            attached_panel_id=attached_panel_id,
        )
        page.floating.append(text_obj)
        return text_obj

    # -- 複製（要件定義 6.15） ------------------------------------------------

    def duplicate(self, page: Page, object_id: str, dx: float, dy: float) -> SceneObject:
        """ページの中の1つを写して、(dx, dy) だけずらして置く。写しを返す。

        写るのは、選んだもの1つと**それに巻き込まれるもの**だけ。

        | 選んだもの | 写るもの |
        |---|---|
        | コマ | コマと中の画像。画像の実体は増えない（同じ `asset` を指す） |
        | 画像 | 画像だけ。同じコマの中に増える |
        | フキダシ | フキダシと、上に乗っているセリフ |
        | マーク・セリフ | それだけ |

        **id は振り直す。** 写しは別のものなので、元と同じ id を持たせない
        （持たせると `attached_panel_id` の解決先が狂う → 6章）。

        **コマへの紐づけは元のまま引き継ぐ。** ただし**フキダシと一緒に
        写したセリフは、写したフキダシに紐づけ直す**。元のフキダシを指した
        ままにすると、元を動かしたときに写したセリフだけが飛んでいく（→ 6.5）。

        **斜めに割ったコマは写せない**（`ValueError`）。2枚1組で形が決まって
        いるので、片方だけ写すと相方のいない平行四辺形が1枚できる（→ 6.10）。
        呼ぶ側が `Page.slant_pair_of()` で先に断ること。
        """
        obj = page.find(object_id)
        if obj is None:
            raise KeyError(f"見つかりません: {object_id}")

        if isinstance(obj, Panel):
            if page.slant_pair_of(object_id) is not None:
                raise ValueError(f"斜めに割ったコマは複製できません: {object_id}")
            return self._duplicate_panel(page, obj, dx, dy)
        if isinstance(obj, ImageObject):
            return self._duplicate_image(page, obj, dx, dy)

        copy = self._duplicate_floating(page, obj, dx, dy)
        if isinstance(copy, BalloonObject):
            # **しっぽの先端も動かす。** 先端はページ座標なので、置いていくと
            # 写しだけしっぽが伸びて別の形になる。ここは「同じ形のフキダシを
            # 並べる」操作なので、指す相手を据え置く `move_balloon`（→ 6.4）
            # ではなく、形をそのまま写すほうを採る
            copy.tail = copy.tail.translated(dx, dy)
            for text in page.texts_on_balloon(object_id):
                text_copy = self._duplicate_floating(page, text, dx, dy)
                text_copy.attached_balloon_id = copy.id
        return copy

    def _duplicate_panel(self, page: Page, panel: Panel, dx: float, dy: float) -> Panel:
        """コマを写す。中の画像も一緒に写り、集中線・流線もそのまま乗る。

        どちらも長さを1つも持たない（集中線は中心も空きも割合 → 6.16、
        流線は向きと割合だけ → 6.26）ので、ずらしても直すところが無い。

        **写しはロックしない**（→ 6.17 の「新しく作ったコマはロックしない」）。
        置き場所を決めるために作ったものなので、いきなり動かせないと
        写すたびに解除の手間が挟まる。
        """
        copy = _detached_copy(panel)
        copy.id = self._new_id(ID_PREFIX_PANEL)
        copy.z = max((p.z for p in page.panels), default=-1) + 1
        copy.shape = copy.shape.translated(dx, dy)
        copy.locked = False
        for child in copy.children:
            child.id = self._new_id(ID_PREFIX_IMAGE)
            child.rect = child.rect.translated(dx, dy)
        page.panels.append(copy)
        return copy

    def _duplicate_image(
        self, page: Page, image: ImageObject, dx: float, dy: float
    ) -> ImageObject:
        """画像を写す。**同じコマの中に増える。**"""
        panel = page.panel_of_image(image.id)
        # `page.find()` が画像として返した以上、必ずどれかのコマに属している
        assert panel is not None
        copy = _detached_copy(image)
        copy.id = self._new_id(ID_PREFIX_IMAGE)
        copy.z = max((c.z for c in panel.children), default=-1) + 1
        copy.rect = copy.rect.translated(dx, dy)
        panel.children.append(copy)
        return copy

    def _duplicate_floating(
        self, page: Page, obj: FloatingObject, dx: float, dy: float
    ) -> FloatingObject:
        """ページ直下のものを1つ写す。**同じ段の手前に置く**（`add_*` と同じ）。"""
        copy = _detached_copy(obj)
        copy.id = self._new_id(_ID_PREFIXES[type(obj)])
        copy.z = self._next_floating_z(page)
        copy.rect = copy.rect.translated(dx, dy)
        page.floating.append(copy)
        return copy

    # -- ページ操作 --------------------------------------------------------

    def page(self, page_id: str) -> Page:
        for p in self.pages:
            if p.id == page_id:
                return p
        raise KeyError(f"ページが見つかりません: {page_id}")

    def remove_page(self, page_id: str) -> Page:
        page = self.page(page_id)
        self.pages.remove(page)
        return page

    def move_page(self, from_index: int, to_index: int) -> None:
        """ページの並びを変える。`to_index` は移動後の位置。"""
        if not 0 <= from_index < len(self.pages):
            raise IndexError(f"移動元のページ番号が範囲外です: {from_index}")
        if not 0 <= to_index < len(self.pages):
            raise IndexError(f"移動先のページ番号が範囲外です: {to_index}")
        page = self.pages.pop(from_index)
        self.pages.insert(to_index, page)

    # -- 走査 --------------------------------------------------------------

    def iter_objects(self) -> Iterator[tuple[Page, SceneObject]]:
        for page in self.pages:
            for obj in page.iter_objects():
                yield page, obj

    def iter_images(self) -> Iterator[ImageObject]:
        for _, obj in self.iter_objects():
            if isinstance(obj, ImageObject):
                yield obj

    def iter_stickers(self) -> Iterator[StickerObject]:
        for _, obj in self.iter_objects():
            if isinstance(obj, StickerObject):
                yield obj

    def iter_roughs(self) -> Iterator[PageRough]:
        """敷いてあるラフ（→ 6.23）。1ページに1枚まで。"""
        for page in self.pages:
            if page.rough is not None:
                yield page.rough

    def referenced_assets(self) -> set[str]:
        """どこかが参照している assets/ のパス一覧。

        **マークとラフも数える。** 数え漏らすと「未使用ファイルを整理」が
        実体を `_unused/` へ移し、次に開いたときに×印だけが残る（ラフは
        ×印すら出ないので、黙って消えたように見える → 6.23）。
        """
        used = {img.asset for img in self.iter_images() if img.asset}
        # **切り抜きのマスクも数える**（→ 要件定義 10.3）。数え漏らすと整理が
        # マスクを `_unused/` へ移し、次に開いたときに切り抜きだけが外れた絵に
        # なる。×印も出ないので、黙って戻ったように見える
        used |= {img.mask_asset for img in self.iter_images() if img.mask_asset}
        used |= {s.asset for s in self.iter_stickers() if s.asset}
        used |= {r.asset for r in self.iter_roughs() if r.asset}
        return used

    # -- 変換 --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "app": APP_NAME,
            "title": self.title,
            "default_page_size": self.default_page_size.to_dict(),
            "reading_direction": self.reading_direction,
            "next_id": self.next_id,
            "pages": [p.to_dict() for p in self.pages],
        }

    @classmethod
    def from_dict(cls, data: Any, where: str = "project.json") -> Project:
        """保存形式の辞書からプロジェクトを組み立てる。

        `where` はエラー文言の先頭に出す名前。**既定は本体
        `project.json` のもの**だが、`backup/` の世代を読むときは呼ぶ側
        （`storage.read_project_file`）が実ファイル名を渡す。渡さないと、
        壊れているのが世代のファイルでも「project.json」を名指しして
        しまい、本体が壊れていると誤解させる（2026-08-08 に発見）。
        """
        d = v.req_mapping(data, where)

        version = v.integer(d, "format_version", where, 1)
        if version > FORMAT_VERSION:
            raise UnsupportedVersionError(
                f"このプロジェクトは新しい形式（version {version}）で保存されています。"
                f"このアプリが扱えるのは version {FORMAT_VERSION} までです。"
                "アプリを更新してください。"
            )

        pages_raw = v.req_list(d.get("pages", []), f"{where}.pages")
        if version < 2:
            # v1 ファイルで文字・枠線・しっぽ幅が丸ごと欠けている場合の
            # 備え。詳しくは `_fill_legacy_length_defaults` を参照
            _fill_legacy_length_defaults(pages_raw)
        project = cls(
            title=v.text(d, "title", where, ""),
            default_page_size=Size.from_dict(
                d.get("default_page_size", DEFAULT_PAGE_SIZE.to_dict()),
                f"{where}.default_page_size",
            ),
            reading_direction=v.choice(
                d, "reading_direction", where, READING_DIRECTIONS, "rtl"
            ),
            pages=[Page.from_dict(p, f"{where}.pages[{i}]") for i, p in enumerate(pages_raw)],
            next_id=v.integer(d, "next_id", where, 1),
        )
        if version < 2:
            # version 1 は mm。組み立て終えた**型のあるオブジェクトを走査**して
            # 換算する。読み込む前の辞書を書き換える手もあるが、入れ子の
            # どの数値が長さなのかを名前で見分けることになり、1つ取りこぼすと
            # そこだけ 1/5.9 の大きさで残る
            project.scale_lengths(MM_TO_PX)
            project.load_warnings.append(
                "古い形式（mm）の作品を px に換算して開きました。"
                "上書き保存すると新しい形式になります"
            )
        project._repair_after_load()
        return project

    # -- 単位の換算 --------------------------------------------------------

    def scale_lengths(self, factor: float) -> None:
        """長さを持つ値をまとめて `factor` 倍する。

        **割合と角度には触らない。** `SlantPair.ratio`（0〜1）、
        `Tail.root_y`（-1〜1）、`angle`（度）、`opacity` は単位を持たないので、
        掛けると意味が壊れる。`src_px` も元画像の実寸なので対象外。
        **`FocusLines` も `FlowLines` も1つも長さを持たない**ので、まるごと
        対象外（→ 6.16、6.26 で長さを持たせなかったことが、ここでも効いている）。
        """
        self.default_page_size = self.default_page_size.scaled(factor)
        for page in self.pages:
            page.size = page.size.scaled(factor)
            # ラフは version 1（mm）の頃には無いので実際には通らないが、
            # 「長さを持つものはここに並ぶ」を崩さないために足しておく。
            # 抜けを見つけるのは、寸法が10倍ずれた作品を開いたときになる
            if page.rough is not None:
                page.rough.rect = page.rough.rect.scaled(factor)
            for panel in page.panels:
                panel.shape = panel.shape.scaled(factor)
                panel.border.width *= factor
                for image in panel.children:
                    image.rect = image.rect.scaled(factor)
            for obj in page.floating:
                obj.rect = obj.rect.scaled(factor)
                if isinstance(obj, BalloonObject):
                    obj.border.width *= factor
                    obj.tail = dataclasses.replace(
                        obj.tail,
                        tip=(obj.tail.tip[0] * factor, obj.tail.tip[1] * factor),
                        width=obj.tail.width * factor,
                    )
                elif isinstance(obj, TextObject):
                    obj.font = dataclasses.replace(
                        obj.font, size_px=obj.font.size_px * factor
                    )

    def copy(self) -> Project:
        """完全な複製を作る。

        Undo/Redo（要件定義 6.8）のスナップショットはこれを使う。
        保存形式を往復させることで、**保存に載らない状態は複製にも載らない**
        ことが保証される。項目を増やしたとき `to_dict()` への追加を忘れると
        Undo のテストでも落ちるので、抜けに気づける。
        """
        return Project.from_dict(self.to_dict())

    # -- 読み込み後の修復 --------------------------------------------------

    def _repair_after_load(self) -> None:
        """手で書き換えられた JSON や、古い不具合が残したデータを整える。

        ここで落とさず直しているのは、いずれも「開けなくする」ほどの
        問題ではないため。直した内容は `load_warnings` に残す。
        """
        # **ページが1枚も無いと、表示するものがそもそも無い。** 画面側は
        # 「必ず1枚はある」を前提に `pages[page_index]` を引く（→
        # `EditorState.page`）ので、空のまま通すと開いた先で `IndexError`
        # になる。しかもそれが `changed` などのシグナルの中で起きるため
        # PySide6 が握り潰し、**エラーの窓すら出ないまま窓だけが固まる**
        # （2026-08-09 に発見）。
        #
        # アプリ自身は 0 枚の作品を書かない（最後の1枚は消せない →
        # `EditorState.delete_page`）ので、ここへ来るのは手で書き換えた
        # ファイルか、途中で切れたファイルだけ。**空のページを1枚足しても
        # 失うものが何も無い**ので、断らずに直して開く（ID の重複と違い、
        # 意味が狂う相手がいない）。
        #
        # **他の直しより先に行う。** 足したページは以降の走査に乗るので、
        # id の重複も `next_id` の繰り上げも通常どおり効く
        if not self.pages:
            self.add_page()
            self.load_warnings.append(
                "ページが1枚もなかったので、空のページを1枚足しました"
            )

        seen: set[str] = set()
        max_serial = 0

        for _, obj in self.iter_objects():
            if obj.id in seen:
                raise ProjectFormatError(
                    f"ID が重複しています: {obj.id}。"
                    "紐づけの解決先が狂うため、このまま開くことはできません。"
                )
            seen.add(obj.id)

        for page in self.pages:
            if page.id in seen:
                raise ProjectFormatError(f"ID が重複しています: {page.id}")
            seen.add(page.id)

        for object_id in seen:
            m = _ID_RE.match(object_id)
            if m:
                max_serial = max(max_serial, int(m.group(1)))

        # next_id が実際の使用済み番号より小さいと、次の採番で ID が衝突する
        if self.next_id <= max_serial:
            self.load_warnings.append(
                f"next_id を {self.next_id} から {max_serial + 1} に直しました"
                "（このままでは ID が重複します）"
            )
            self.next_id = max_serial + 1

        # 存在しないコマ・吹き出しを指した紐づけを外す。
        # 残しておくと移動時に追随せず、しかも原因が見えない
        for page in self.pages:
            panel_ids = {p.id for p in page.panels}
            balloon_ids = {f.id for f in page.floating if isinstance(f, BalloonObject)}

            # 斜めの組の整合を取る。組が壊れていても、コマ自体は正しい形を
            # 持っているので開ける。関係だけ解いて普通のコマとして扱う
            paired: set[str] = set()
            for pair in list(page.slant_pairs):
                missing = [i for i in pair.members() if i not in panel_ids]
                if missing:
                    self.load_warnings.append(
                        f"斜めの組が存在しないコマ {' / '.join(missing)} を指していたため、"
                        "組を解きました（コマの形はそのままです）"
                    )
                    page.slant_pairs.remove(pair)
                    continue
                if pair.left_id == pair.right_id:
                    self.load_warnings.append(
                        f"斜めの組が同じコマ {pair.left_id} を左右に指していたため、組を解きました"
                    )
                    page.slant_pairs.remove(pair)
                    continue
                # 1枚が2つの組に入ると、どちらに合わせて動かすかが決まらない
                overlap = paired & set(pair.members())
                if overlap:
                    self.load_warnings.append(
                        f"コマ {' / '.join(sorted(overlap))} が複数の斜めの組に入っていたため、"
                        "後ろの組を解きました"
                    )
                    page.slant_pairs.remove(pair)
                    continue
                paired.update(pair.members())

            for obj in page.floating:
                if obj.attached_panel_id is not None and obj.attached_panel_id not in panel_ids:
                    self.load_warnings.append(
                        f"{obj.id} が存在しないコマ {obj.attached_panel_id} を指していたため、"
                        "紐づけを外しました"
                    )
                    obj.attached_panel_id = None

                if not isinstance(obj, TextObject) or obj.attached_balloon_id is None:
                    continue
                if obj.attached_balloon_id not in balloon_ids:
                    self.load_warnings.append(
                        f"{obj.id} が存在しないフキダシ {obj.attached_balloon_id} を"
                        "指していたため、紐づけを外しました"
                    )
                    obj.attached_balloon_id = None


def new_project(title: str = "", size: Size | None = None) -> Project:
    """空のプロジェクトを 1 ページだけ持たせて作る。"""
    project = Project(title=title, default_page_size=size or DEFAULT_PAGE_SIZE)
    project.add_page()
    return project
