"""プロジェクトのデータモデル。

構造は要件定義 4章のとおり:

    Project
     ├ Page[]
          ├ Panel[]                コマ（クリッピングの主体）
          │    └ Image[]           コマ枠で切り抜かれる
          └ Floating[]             ページ直下に置かれ、切り抜かれない
               ├ Balloon           吹き出し
               └ Text              セリフ

吹き出しをコマの「子」にしていないのが要点。漫画の吹き出しはコマ枠から
はみ出すのが常態なので、子にすると切り抜きに巻き込まれて切られてしまう。
代わりに `attached_panel_id` で紐づけ、**移動は追随するが切り抜かれない**
という狙った挙動だけを取っている。

保存形式との対応は 1 対 1 で、`to_dict()` / `from_dict()` を往復しても
情報が落ちない。この性質が Undo/Redo（要件定義 6.8、スナップショット方式）
の土台になる。
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Iterator

from . import validation as v
from .errors import ProjectFormatError, UnsupportedVersionError
from .geometry import Polygon, Rect, Size

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

_ID_RE = re.compile(r"^[a-z]+_(\d+)$")

# 吹き出しの種類。`wavy`（波形）は**不安・動揺・弱った声**を表す。
# ギザギザで代用すると叫びに読めてしまい、意味が逆になる
BALLOON_STYLES = ("ellipse", "jagged", "wavy")
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
    def from_dict(cls, data: Any, where: str) -> "Border":
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
    def from_dict(cls, data: Any, where: str) -> "Font":
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
    """

    enabled: bool = True
    tip: tuple[float, float] = (0.0, 0.0)
    width: float = 35.0
    root_y: float | None = None

    def translated(self, dx: float, dy: float) -> "Tail":
        return Tail(
            self.enabled,
            (self.tip[0] + dx, self.tip[1] + dy),
            self.width,
            self.root_y,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "tip": [self.tip[0], self.tip[1]],
            "width": self.width,
            "root_y": self.root_y,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Tail":
        d = v.req_mapping(data, where)
        tip = v.point(d["tip"], f"{where}.tip") if "tip" in d else (0.0, 0.0)
        return cls(
            enabled=v.flag(d, "enabled", where, True),
            tip=tip,
            width=v.positive(d, "width", where, 35.0),
            # 項目が無い作品は自動として読む。root_y を足す前に保存した
            # ファイルでも、それまでと同じ形で開ける
            root_y=v.opt_ratio(d, "root_y", where),
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
    """

    asset: str = ""
    rect: Rect = field(default_factory=lambda: Rect(0.0, 0.0, 0.0, 0.0))
    # 元画像のピクセル寸法。縦横比の維持だけに使う。
    # **単位の換算では触らない。** 最初から px なので、掛けると比が狂う
    src_px: tuple[int, int] = (0, 0)
    # MVP では常に 0。将来の回転対応のために場所だけ確保している
    rotation: float = 0.0
    opacity: float = 1.0

    TYPE = "image"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.TYPE,
            "asset": self.asset,
            "rect": self.rect.to_dict(),
            "src_px": [self.src_px[0], self.src_px[1]],
            "rotation": self.rotation,
            "opacity": self.opacity,
            "z": self.z,
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "ImageObject":
        d = v.req_mapping(data, where)
        px = v.req_list(d.get("src_px", [0, 0]), f"{where}.src_px")
        if len(px) != 2:
            raise ProjectFormatError(f"{where}.src_px: [幅, 高さ] の 2 要素が必要です")
        opacity = v.number(d, "opacity", where, 1.0)
        if not 0.0 <= opacity <= 1.0:
            raise ProjectFormatError(f"{where}.opacity: 0.0〜1.0 の範囲外です（{opacity}）")
        return cls(
            id=v.text(d, "id", where),
            z=v.integer(d, "z", where, 0),
            asset=v.text(d, "asset", where),
            rect=Rect.from_dict(d.get("rect"), f"{where}.rect"),
            src_px=(int(px[0]), int(px[1])),
            rotation=v.number(d, "rotation", where, 0.0),
            opacity=opacity,
        )


@dataclass
class Panel(SceneObject):
    """コマ。中の画像を自分の形に切り抜く。"""

    shape: Polygon = field(default_factory=lambda: Polygon.from_rect(Rect(0.0, 0.0, 10.0, 10.0)))
    border: Border = field(default_factory=Border)
    children: list[ImageObject] = field(default_factory=list)

    TYPE = "panel"

    def bounds(self) -> Rect:
        return self.shape.bounds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.TYPE,
            "shape": {"kind": "polygon", "points": self.shape.to_list()},
            "border": self.border.to_dict(),
            "z": self.z,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Panel":
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
    def from_dict(cls, data: Any, where: str) -> "BalloonObject":
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
    def from_dict(cls, data: Any, where: str) -> "TextObject":
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


# ページ直下に置けるもの。将来 EffectObject などを足すときはここに登録する
FloatingObject = BalloonObject | TextObject
_FLOATING_TYPES: dict[str, Any] = {
    BalloonObject.TYPE: BalloonObject,
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
#     用紙 → コマ（と中の画像）→ 吹き出し → セリフ
#
# 用紙とコマは描く場所そのものが分かれているので、ここで段を持つのは
# ページ直下のもの（吹き出し・セリフ）だけ。
#
# **z ではこの順を覆せない。** 以前は z だけで重ねていたため、セリフを
# 書いたあとに吹き出しを載せると、後から作った吹き出しのほうが z が大きく、
# 白い塗りが文字を塗り潰して**セリフが消えた**。吹き出しは後から足すのが
# 普通の手順なので、作った順で見た目が変わらないようにする。
#
# z は同じ段の中の前後（吹き出しどうし・セリフどうし）にだけ効く。
LAYER_BALLOON = 0
LAYER_TEXT = 1


def floating_layer(obj: FloatingObject) -> int:
    """そのものが載る段。大きいほど手前。"""
    return LAYER_TEXT if isinstance(obj, TextObject) else LAYER_BALLOON


def floating_order(obj: FloatingObject) -> tuple[int, int]:
    """奥から手前へ並べるときの鍵。`sorted(page.floating, key=...)` に使う。

    段が先、z が後。当たり判定（`layout.text_at` を `layout.balloon_at` より
    先に見る）もこの順に合わせてある。描く順と拾う順がずれると、
    見えているものを掴めなくなる。
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

    def flipped(self) -> "SlantPair":
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
    def from_dict(cls, data: Any, where: str) -> "SlantPair":
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


@dataclass
class Page:
    id: str
    size: Size = DEFAULT_PAGE_SIZE
    panels: list[Panel] = field(default_factory=list)
    floating: list[FloatingObject] = field(default_factory=list)
    slant_pairs: list[SlantPair] = field(default_factory=list)

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
        """そのコマに紐づいた吹き出し・セリフ。"""
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

    def texts_on_balloon(self, balloon_id: str) -> list["TextObject"]:
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
        for target in (pair.members() if pair is not None else (panel_id,)):
            self._move_panel_only(target, dx, dy)

    def _move_panel_only(self, panel_id: str, dx: float, dy: float) -> None:
        """コマ1枚とその持ち物だけを動かす。

        要件定義 4章で狙った挙動そのもの。吹き出しは親子関係ではなく
        `attached_panel_id` の紐づけなので、追随はするが切り抜かれない。

        吹き出しの上に乗ったセリフも連れていく。**同じものを2回動かさない**
        よう、動かした id を控えながら進む。コマにもその上の吹き出しにも
        紐づいたセリフがあると、二重に動いて位置がずれる。
        """
        panel = self.panel(panel_id)
        panel.shape = panel.shape.translated(dx, dy)
        for child in panel.children:
            child.rect = child.rect.translated(dx, dy)

        moved: set[str] = set()
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

    def remove_floating(self, object_id: str) -> "FloatingObject":
        """ページ直下のもの（吹き出し・セリフ）を消す。

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
        return out

    @classmethod
    def from_dict(cls, data: Any, where: str) -> "Page":
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
        )


# --------------------------------------------------------------------------
# プロジェクト
# --------------------------------------------------------------------------


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

    def referenced_assets(self) -> set[str]:
        """どこかの画像が参照している assets/ のパス一覧。"""
        return {img.asset for img in self.iter_images() if img.asset}

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
    def from_dict(cls, data: Any) -> "Project":
        where = "project.json"
        d = v.req_mapping(data, where)

        version = v.integer(d, "format_version", where, 1)
        if version > FORMAT_VERSION:
            raise UnsupportedVersionError(
                f"このプロジェクトは新しい形式（version {version}）で保存されています。"
                f"このアプリが扱えるのは version {FORMAT_VERSION} までです。"
                "アプリを更新してください。"
            )

        pages_raw = v.req_list(d.get("pages", []), f"{where}.pages")
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
        """
        self.default_page_size = self.default_page_size.scaled(factor)
        for page in self.pages:
            page.size = page.size.scaled(factor)
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

    def copy(self) -> "Project":
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
