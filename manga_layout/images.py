"""画像の展開と、画面用の縮小版の保持。

**Qt に依存する非 UI 層は、ここと `tone.py` の2つ。** `assets.py` は
バイト列しか扱わず「画像として展開できるか」を判定できないため、その検証を
ここが受け持つ。
取り込みは必ずこの層を通し、展開に成功したものだけを `assets/` へ渡す。
壊れたデータを入れてしまうと、内容ハッシュが名前なので、あとから
人が「どれが壊れているか」を見分けられなくなる。

画面には**縮小版だけ**を使う（要件定義 9章）。クリスタからの貼り付けは
実測で 2048×2048（419万画素）あり、原寸のまま毎回描くと拡大縮小や
スクロールがそのぶん重くなる。原寸が要るのは PNG 書き出しのときだけなので、
そちらは必要になった時点で読み直す。
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from .errors import AssetError, BrokenImageError
from .model import Tone
from .tone import apply_tone

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

    **展開する前に、寸法だけを先に確かめる。** `QImageReader.size()` は
    ヘッダーを読むだけで済み、画素の展開は行わない。Qt には確保上限
    （既定 `QImageReader.allocationLimit()` MB）があり、これを超える
    宣言サイズの画像は `loadFromData` がそのまま失敗する。以前はここを
    区別していなかったため、壊れてはいない大きすぎるだけの画像まで
    「ファイルが壊れている可能性があります」という誤った理由になって
    いた（2026-08-08 に発見）。壊れたファイルは寸法自体が読めない
    （`size().isValid()` が偽）ので、この判定に巻き込まれない。
    """
    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    size = QImageReader(buffer).size()
    limit_mb = QImageReader.allocationLimit()
    if size.isValid() and limit_mb > 0:
        # ARGB32 相当（1画素4バイト）で確保したときの見込み量
        estimated_mb = size.width() * size.height() * 4 / (1024 * 1024)
        if estimated_mb > limit_mb:
            raise BrokenImageError(
                f"画像が大きすぎます（{size.width():,} × {size.height():,} 画素）。"
                "縮小してから取り込んでください"
            )

    image = QImage()
    if not image.loadFromData(data) or image.isNull():
        raise BrokenImageError(
            f"画像として展開できませんでした（{len(data):,} バイト）。"
            "ファイルが壊れている可能性があります"
        )
    return image


def size_px(image: QImage) -> tuple[int, int]:
    return (image.width(), image.height())


def readable_file(path: pathlib.Path) -> bool:
    """そのファイルが画像として開ける形か。**画素は展開しない。**

    `QImageReader.size()` が読むのはヘッダーだけ（→ `decode` の注記）。
    ファイルの中身も先頭しか触らないので、実体をまるごと読む `decode` と
    違って**全ページ分を巡っても待たされない**。点検（`check.inspect_project`）
    と書き出し前の警告（`ui.export.missing_assets_in`）が、**同じ問い**
    ——「書き出すとそこが白く抜けるか」——にこれで答える。

    **展開まで試さないぶん、取りこぼす形が1つある。** ヘッダーは正しいのに
    画素の途中で切れているファイルは、ここでは「読める」と答えてしまう。
    そこまで見るには結局すべて展開するしかなく、それが固まる原因だった
    （2026-08-09 に集約したときの判断）。画面には従来どおり×印が出るので、
    見落としたまま気づけないわけではない。
    """
    return QImageReader(str(path)).size().isValid()


def file_px(path: pathlib.Path) -> tuple[int, int] | None:
    """そのファイルの画素寸法。開けない形なら None。**画素は展開しない。**

    `readable_file` と**同じ1回の読み**で分かることを、寸法まで返すだけ
    （→ そちらの注記）。点検（`check.inspect_project`）が、切り抜きの
    マスクの寸法が絵と合っているかを見るのに使う。展開する `decode` を
    呼ぶと、全ページ分を巡る処理が固まる（2026-08-09 の判断）。
    """
    size = QImageReader(str(path)).size()
    return (size.width(), size.height()) if size.isValid() else None


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

    PNG / JPG の書き出しには出ない（→ 6.23）。原寸を返す経路が要るのは
    PSD だけなので、そちらは別に持つ（→ `full_rough_from_bytes`）。
    """
    full = decode(data)
    return Preview(image=to_blue_pencil(make_preview(full)), source_px=size_px(full))


def full_rough_from_bytes(data: bytes) -> Preview:
    """ラフ用の1枚を、縮めずに青く染める。**PSD 書き出しのときだけ使う。**

    PSD のラフは非表示のレイヤーとして入れる（→ 要件定義 10.1）。
    **クリスタでなぞる相手になる**ので、画面用の縮小版（長辺 1,600px）を
    引き伸ばして入れると、そこに入れた意味が薄れる。

    染めるのが縮めたあとではなく先になるぶん、`rough_preview_from_bytes`
    より重い。書き出しの一度きりなので、待ち時間より細かさを取る。
    """
    full = decode(data)
    return Preview(image=to_blue_pencil(full), source_px=size_px(full))


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
        except (AssetError, OSError):
            # `AssetError` は `BrokenImageError`（展開の失敗）を含む親。
            # `read_bytes` の実体は `AssetStore.read` で、ファイルは
            # あるのに読めない場合（他アプリのロック・権限など）は OSError を
            # `AssetError` に包み直して投げる。以前はここで `BrokenImageError`
            # だけを見ていたため、その形の失敗だけ素通りしていた——**失敗が
            # 覚えられないまま描き直しのたびに例外を吐き続ける**、という
            # このキャッシュが本来防ぐはずの壊れ方そのものだった
            # （2026-08-08 に発見）
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


def toned(source: Preview | None, tone: Tone) -> Preview | None:
    """トーンを焼いた1枚を作る。**マスクの無い経路はこれだけで足りる。**

    渡された絵の大きさのまま焼く。画面用（縮小版）に焼くか、書き出し用
    （原寸）に焼くかは、渡す側が決める（→ `ImageCache` の注記）。

    マスクの掛かった画像はここを通らない。マスクは元画像の原寸座標に
    結び付いているので、**縮小版へは掛けられない**（→ `image_masks.masked_preview`）。
    """
    if source is None:
        return None
    return Preview(image=apply_tone(source.image, tone), source_px=source.source_px)


# 焼き込み済みの1枚を、いくつまで覚えておくか。
#
# **上限が要るのは `ImageCache` と違って鍵が増え続けるから。** あちらの鍵は
# 画像の参照だけなので作品の画像数で頭打ちになるが、こちらは設定も鍵に
# 入る。メニューで「濃く」を10回押せば10通りの鍵ができる。1枚 10MB 前後
# あるので、放っておくとメモリを食い潰す
BAKED_CACHE_LIMIT = 24


def bake_key(image) -> tuple[str, str, tuple]:
    """焼き込み済みキャッシュの鍵（→ `SAM3実装計画.md` 4.3）。

    **画像の矩形・回転角・不透明度・コマのIDは入れない。** 焼いた1枚は
    「元画像に何を掛けたか」だけで決まり、どこにどう置くかは描くときの
    変換で決まる。入れてしまうと、動かすたびに焼き直すことになる。

    画面用と書き出し用で同じ鍵を作る（大きさは入れ物ごとに分かれている
    ので、鍵に混ぜない → `ImageCache` の注記）。
    """
    tone = getattr(image, "tone", None)
    return (
        image.asset,
        getattr(image, "mask_asset", ""),
        () if tone is None else tone.key(),
    )


class BakedCache:
    """焼き込み済みの1枚を覚える。鍵は **元画像・マスク・トーンの組**（`bake_key`）。

    焼くのはトーン（→ 要件定義 10.1）と切り抜きのマスク（→ 10.3）。
    **入れ物を種類ごとに増やさない。** 増やすと、同じ絵に対して縮小版と原寸、
    トーンあり・マスクありの組み合わせぶんだけ入れ物が並び、どれを手放し
    忘れたかを人が追えなくなる。

    **`ImageCache` とは分けてある。** 混ぜて鍵だけ増やす手もあるが、それだと
    同じ画像をトーン違いで2枚使ったときに **PNG の展開が2回**走る。
    分けておけば展開は画像ごとに1回で、こちらは焼く手間だけを持つ
    （要件定義 10.1 で並べた2案のうち、後者）。

    焼くのは1枚 40ms 程度（トーン、2048×2048 の実測）。マスクの合成は
    4K で 27ms 前後（2026-08-27 実測）。画面はコマを毎回描き直す作りなので、
    **覚えずにいると1フレームごとに払うことになる**。
    """

    def __init__(self, limit: int = BAKED_CACHE_LIMIT) -> None:
        self._items: dict[tuple[str, str, tuple], Preview] = {}
        self._limit = limit

    def __len__(self) -> int:
        return len(self._items)

    def get(
        self, key: tuple[str, str, tuple], make: Callable[[], Preview | None]
    ) -> Preview | None:
        """その鍵の1枚。まだ無ければ `make()` に焼いてもらう。

        **焼き方はここが決めない。** 画面用は縮小版、書き出し用は原寸と
        大きさが違ううえ、マスクの有無で元にする絵も変わる（マスクは元画像の
        原寸座標に結び付いているので、掛けるのは必ず原寸 → 計画 4.3）。
        """
        found = self._items.get(key)
        if found is not None:
            return found

        baked = make()
        if baked is None:
            # 実体が無い・壊れている。**覚えない**——`ImageCache` の側が
            # 既に覚えているので、ここで二重に持つ意味が無い
            return None

        if len(self._items) >= self._limit:
            # いちばん古い1枚を捨てる（辞書は入れた順を保つ）
            self._items.pop(next(iter(self._items)))
        self._items[key] = baked
        return baked

    def forget(self, ref: str) -> None:
        """その参照が関わる1枚を全部捨てる。設定違いをまとめて落とす。

        **元画像とマスクの両方を見る。** マスクを外した直後に手放すのは
        マスク側の参照なので、元画像だけを見ていると焼いた1枚が残り続ける。
        """
        for key in [k for k in self._items if ref in (k[0], k[1])]:
            self._items.pop(key)

    def clear(self) -> None:
        self._items.clear()
