"""画像に掛ける白黒マスク（→ 要件定義 10.3、`SAM3実装計画.md` 4.2）。

**この層は SAM 3 を知らない。** どこから来たマスクでも同じ扱いにするための
共通形式で、別のモデルが返したものでも、手で描いたものでも同じように通る。
提供者の名前がここに入ると、モデルを替えるたびに保存形式と描画まで
付いてくることになる。

Qt に依存する非 UI 層は、ここと `images.py`・`tone.py` の3つ。画素を触るので
ここも Qt が要る。`__init__.py` からは公開しない（理由は `images.py` と同じ）。
"""

from __future__ import annotations

from PySide6.QtGui import QImage, QPainter

from .errors import AssetError, MaskSizeError
from .images import Preview, decode, make_preview, size_px
from .model import Tone
from .tone import apply_tone, as_alpha


def decode_mask(data: bytes) -> QImage:
    """バイト列をマスクとして展開する。展開できなければ例外。

    **8bit グレースケールに揃えてから返す。** マスクとして意味を持つのは
    0〜255 の濃淡だけで、0 が透明・255 が不透明（→ 4.2）。色や透明度の付いた
    PNG を渡されても明るさだけを見る。ここで1つの形へ寄せておけば、
    この先の合成が形の違いを気にせずに済む。

    展開そのものは `images.decode` に任せる。大きすぎる画像を断る判断
    （`QImageReader.allocationLimit`）を2か所に書き分けないため。
    """
    return decode(data).convertToFormat(QImage.Format.Format_Grayscale8)


def apply_mask(image: QImage, mask: QImage) -> QImage:
    """`image` に `mask` を掛けた1枚を返す。元の画像は変えない。

    **元のアルファと掛け合わせる。置き換えない。** クリスタから貼った絵は
    透明を持ったまま入っている（→ 要件定義 9章）ので、置き換えてしまうと
    切り抜きのついでに元の透明が埋まる。

    寸法が違えば断る（`MaskSizeError`）。**縮めて合わせない。** 合わせて
    しまうと、ずれた組み合わせが生まれても「輪郭がわずかにずれた絵」に
    なるだけで、人が見て気づけない（→ 4.2「画像とマスクを別々に変換しない」）。
    """
    if size_px(image) != size_px(mask):
        raise MaskSizeError(
            f"マスクの大きさが画像と違います"
            f"（画像 {image.width():,} × {image.height():,} 画素、"
            f"マスク {mask.width():,} × {mask.height():,} 画素）"
        )

    # **濃淡を透明度として読み替えてから重ねる。** `DestinationIn` は
    # 「元の alpha × 重ねる側の alpha」を残す合成なので、これで掛け算になる。
    #
    # 読み替えは `tone.as_alpha` に任せる。**`convertToFormat` で書くと
    # 全画素 255 になり、マスクが「画像全部」を指す**（→ PySide6の落とし穴 3）。
    # トーンと切り抜きで2か所に書くと、片方だけがその穴に落ちる
    out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, as_alpha(mask))
    painter.end()
    return out


def is_binary(mask: QImage) -> bool:
    """濃淡が 0 と 255 だけか。

    保存形式は 0〜255 を受け入れるが、**最初の SAM 3 実装が作るのは白と黒
    だけ**という取り決めがある（→ 4.2）。中間値が混じり始めたことに気づく
    ための確認で、単体確認（段階4）と自動テストが使う。

    画素は Python で1つずつ見ない（4K で 800万回になる）。
    バイト列のまま集合にすれば、走査は C 側で終わる。
    """
    gray = mask.convertToFormat(QImage.Format.Format_Grayscale8)
    # 行の末尾には詰め物が入ることがある（`bytesPerLine` は4の倍数）。
    # 詰め物は絵ではないので、行ごとに幅のぶんだけ切り出して見る
    raw = bytes(gray.constBits())
    stride = gray.bytesPerLine()
    width = gray.width()
    found: set[int] = set()
    for y in range(gray.height()):
        found |= set(raw[y * stride : y * stride + width])
    return found <= {0, 255}


def masked_preview(
    data: bytes, mask_data: bytes, tone: Tone | None, *, reduced: bool
) -> Preview:
    """元画像にマスク（とトーン）を焼いた1枚。**掛けるのは必ず原寸。**

    マスクは元画像のピクセル座標に結び付いているので、先に縮めた絵へ
    先に縮めたマスクを掛けることはしない。丸めが2回に分かれて、髪や1pxの線の
    縁が崩れる（→ `SAM3実装計画.md` 4.3）。画面用（`reduced=True`）は、
    **原寸で合成してから縮める**。

    合成は 4K で 27ms、そこからの縮小が 4.5ms（2026-08-27 実測）。
    焼いた1枚は `BakedCache` が覚えるので、払うのは組み合わせごとに1回だけ。

    **順番はマスクが先、トーンが後。** トーンは透明な所には乗らないので
    （→ `tone.apply_tone`）、どちらが先でも同じ絵になるが、先に切り抜いて
    おけば「切り抜いた絵にトーンを掛けた」1本の説明で済む。PSD の
    トーン3枚（→ `psd_export.TonePieceImages`）も同じ順で作れる。
    """
    full = decode(data)
    source_px = size_px(full)
    out = apply_mask(full, decode_mask(mask_data))
    if tone is not None:
        out = apply_tone(out, tone)
    return Preview(image=make_preview(out) if reduced else out, source_px=source_px)


def safe_masked_preview(
    data: bytes | None, mask_data: bytes | None, tone: Tone | None, *, reduced: bool
) -> Preview | None:
    """材料が揃っていれば焼いた1枚、揃わなければ None。

    **描くときにマスクの不備で止めない。** 実体が無い・壊れている・寸法が
    違うのどれでも None を返し、呼ぶ側は切り抜き無しの絵で描き進める
    （→ `SAM3実装計画.md` 段階1〜3）。断るのは**適用するとき**であって、
    描くときではない——欠けた絵1枚で作品が読めなくなるのは割に合わない、
    という `images.ImageCache` の考え方と揃えてある。

    元画像そのものが無いときも None。こちらは呼ぶ側が「×印を出す」まで
    落ちるので、ここで区別しなくてよい。
    """
    if data is None or mask_data is None:
        return None
    try:
        return masked_preview(data, mask_data, tone, reduced=reduced)
    except (AssetError, MaskSizeError):
        return None
