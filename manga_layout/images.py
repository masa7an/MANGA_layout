"""画像の展開と、画面用の縮小版の保持。

**ここが Qt に依存する唯一の非 UI 層。** `assets.py` はバイト列しか扱わず
「画像として展開できるか」を判定できないため、その検証をここが受け持つ。
取り込みは必ずこの層を通し、展開に成功したものだけを `assets/` へ渡す。
壊れたデータを入れてしまうと、内容ハッシュが名前なので、あとから
人が「どれが壊れているか」を見分けられなくなる。

画面には**縮小版だけ**を使う（要件定義 9章）。クリスタからの貼り付けは
実測で 2048×2048（419万画素）あり、原寸のまま毎回描くと拡大縮小や
スクロールがそのぶん重くなる。原寸が要るのは PNG 書き出しのときだけなので、
そちらは必要になった時点で読み直す。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from .errors import BrokenImageError

# 画面用の縮小版で許す長辺（ピクセル）。
# A4 を 600dpi で描いても長辺 7016px なので、画面で見るぶんには十分足りる。
# 上げるほど拡大したときに綺麗になり、そのぶんメモリと描画時間が増える
PREVIEW_MAX_PX = 1600

# ラフ（下敷き → 要件定義 6.23）を染める青。**下書きに使う青鉛筆の色。**
#
# 黒いままだと、上に置いたコマ枠・フキダシの線と見分けが付かない。色を
# 変えてしまえば、濃さを上げても「これは下敷き」と一目で分かる。灰色に
# 落とす手もあるが、鉛筆で描いたラフはもともと灰色なので差が出ない
ROUGH_BLUE = QColor("#4A7FC1")


def decode(data: bytes) -> QImage:
    """バイト列を画像として展開する。できなければ例外。

    取り込み経路（貼り付け・ドロップ・ファイル読み込み）は必ずここを通す。
    """
    image = QImage()
    if not image.loadFromData(data) or image.isNull():
        raise BrokenImageError(
            f"画像として展開できませんでした（{len(data):,} バイト）。"
            "ファイルが壊れている可能性があります"
        )
    return image


def size_px(image: QImage) -> tuple[int, int]:
    return (image.width(), image.height())


def to_png_bytes(image: QImage) -> bytes:
    """`QImage` を PNG のバイト列にする。

    クリップボードから来る画像はファイルではなく展開済みの1枚なので、
    `assets/` に置くにはどこかで符号化が要る。PNG にするのは可逆で
    透明度を保てるため（要件定義 9章の検証どおり、クリスタからの
    貼り付けはアルファが完全に残っている）。
    """
    if image.isNull():
        raise BrokenImageError("空の画像は取り込めません")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise BrokenImageError("画像を PNG に変換できませんでした")
    return bytes(buffer.data())


def make_preview(image: QImage) -> QImage:
    """画面用の縮小版。小さい画像はそのまま返す。"""
    longest = max(image.width(), image.height())
    if longest <= PREVIEW_MAX_PX:
        return image
    return image.scaled(
        PREVIEW_MAX_PX,
        PREVIEW_MAX_PX,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def to_blue_pencil(image: QImage) -> QImage:
    """青鉛筆の下書きに見えるよう染めた1枚（要件定義 6.23）。

    手順は2つだけ。**まず色を捨てて**（`Format_Grayscale8`）、**その上から
    青を Screen で乗せる**。Screen は「白はそのまま、黒は乗せた色になる」
    合成なので、紙の白は白のまま残り、鉛筆の線だけが青くなる。線の濃淡も
    そのまま階調として残る。

    元の色を先に捨てるのは、色付きのラフ（青ボールペン・色鉛筆）でも
    同じ濃さに揃えるため。捨てずに青を乗せると、赤い線だけが黒く沈む。

    **透明な部分は白として扱われる**（`Format_Grayscale8` がアルファを
    落とすため）。ラフは紙を写したものなので透明は普通は無く、あった
    ときも「白い紙」として見えるだけで破綻はしない。

    濃さ（透明度）はここでは焼き込まない。描くときに `setOpacity` で
    掛ける（→ `settings.rough_opacity`）。焼き込むと、設定を書き換えた
    ときに画像を展開し直すことになる。
    """
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    out = gray.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
    painter.fillRect(out.rect(), ROUGH_BLUE)
    painter.end()
    return out


@dataclass(frozen=True)
class Preview:
    """画面に描くための1枚。"""

    image: QImage
    # 縮小前＝原寸のピクセル寸法。縦横比の計算はこちらを使う
    source_px: tuple[int, int]

    @property
    def is_reduced(self) -> bool:
        return size_px(self.image) != self.source_px


def preview_from_bytes(data: bytes) -> Preview:
    full = decode(data)
    return Preview(image=make_preview(full), source_px=size_px(full))


def rough_preview_from_bytes(data: bytes) -> Preview:
    """ラフ用の1枚。画面用に縮めてから青く染める（要件定義 6.23）。

    **染めるのは縮めたあと。** 先に染めると、縮小のたびに全画素ぶんの
    合成をやり直すことになる。ラフは長辺 2,000px を超える写真が普通なので、
    順番を逆にすると読み込みで待たされる。

    書き出しには出ない（→ 6.23）ので、原寸を返す経路は要らない。
    """
    full = decode(data)
    return Preview(image=to_blue_pencil(make_preview(full)), source_px=size_px(full))


def full_from_bytes(data: bytes) -> Preview:
    """縮小しない1枚。**PNG 書き出しのときだけ使う。**

    画面用の縮小版（長辺 `PREVIEW_MAX_PX`）を 600dpi へ引き伸ばすと、
    書き出したものだけがぼやける。画面で確かめても気づけないので、
    ここで経路を分けてある。
    """
    full = decode(data)
    return Preview(image=full, source_px=size_px(full))


class ImageCache:
    """参照文字列から画面用の1枚を引く。展開は1回だけ。

    画面はコマを毎回描き直す作りなので、ここで持たないと1フレームごとに
    PNG を展開することになる。

    **展開に失敗したことも覚える。** 覚えずにいると、壊れた画像が1枚あるだけで
    描き直しのたびに展開を試して画面が固まる。

    `make` を差し替えると原寸を持つ入れ物になる（書き出し用）。既定は
    画面用の縮小版。**同じ入れ物に縮小版と原寸を混ぜてはいけない。**
    どちらが入っているかを引く側が判断できず、書き出しが静かにぼやける。
    """

    def __init__(self, make: Callable[[bytes], Preview] = preview_from_bytes) -> None:
        self._make = make
        self._items: dict[str, Preview | None] = {}

    def __len__(self) -> int:
        return len(self._items)

    def get(self, ref: str, read_bytes: Callable[[], bytes | None]) -> Preview | None:
        """`ref` の1枚。まだ無ければ `read_bytes()` で取り寄せて展開する。

        実体が無い・壊れているときは None。描く側は「その場所に何も描かない」
        で進める。1枚欠けただけで作品が開けないのは割に合わない。
        """
        if ref in self._items:
            return self._items[ref]

        preview: Preview | None = None
        try:
            data = read_bytes()
            if data:
                preview = self._make(data)
        except (BrokenImageError, OSError):
            preview = None

        self._items[ref] = preview
        return preview

    def put(self, ref: str, preview: Preview) -> None:
        """展開済みの1枚を覚えさせる。

        取り込み直後に使う。取り込み側は展開を確かめるために一度展開して
        いるので、それを渡せば描画の1回目で展開し直さずに済む。
        """
        self._items[ref] = preview

    def forget(self, ref: str) -> None:
        self._items.pop(ref, None)

    def clear(self) -> None:
        """別の作品に入れ替えるとき。前の作品の画像を抱えたままにしない。"""
        self._items.clear()
