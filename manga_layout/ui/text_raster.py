"""セリフを1枚の画像に焼く（ラスタライズ → 要件定義 6.34）。

**フキダシの外に置く小さめのセリフを、透明な背景に字が乗っただけの PNG に
変える。** 焼いたあとはマーク（→ 6.14）として置き直すので、回せるようになる
代わりに文字としての編集はできなくなる。

ここが持つのは「1つのセリフを PNG のバイト列にする」ところまで。**置き換え
（元のセリフを消してマークを作る）は `EditorState.rasterize_text`**、動線は
右クリックのメニュー。

描くのは `PageRenderer` に任せる。**画面と同じ経路で描く**ので、縦書きの
組み方・寄せ・書体が画面と食い違うことがない（→ 2章「画面で見た通りに
出力される」）。
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter

from .. import vertical
from ..geometry import Rect
from ..images import to_png_bytes
from ..model import TextObject

# 透明な縁を落とす処理。**PSD 書き出しのために書かれたが、中身は
# 「透明でない画素を囲む矩形を返す」だけの汎用の処理**で、行ごとに
# `lstrip` / `rstrip` を使うので大きな画像でも速い。写すと片方だけ
# 古くなるので、置き場所は動かさずにここから引く
from ..psd import crop_to_content
from .render import TEXT_ALIGN_FLAGS, PageRenderer, qrect, text_font

# ページ座標の何倍で焼くか。
#
# **大きく焼くほどきれいになる、ではない。** 描くときの縮小は
# `QPainter.drawImage` が行い、これは滑らかにする指定を付けても
# **2×2 の画素しか見ない**。4:1 や 8:1 に縮めると、8個か 62個の画素を
# 捨てて2×2 だけで決めることになり、**細い線の縁が階段状になる。**
# 実測では 4倍が 2倍よりはっきり粗く、8倍はさらに粗かった（2026-09-06）。
#
# **2倍にしてある。** 縮小率がちょうど 2:1 になり、2×2 の補間が
# 「4画素の平均」と一致する——**この倍率でだけ、粗い補間が正しい縮小と
# 同じ結果になる。** 実測でも、焼かないセリフと見分けが付かない。
#
# 1倍でも同じ見た目になるが、**画面を 200% に拡大したときに差が出る**
# （2倍なら等倍で出せる）。書き出しの最大 117%（→ 6.7）にも余裕がある。
#
# 設定では変えられるようにしない。用紙ごとに書くべき数字が変わる類ではなく、
# 選ばせるだけの値打ちが無い（→ 6.31 と同じ線引き）
RASTER_SCALE = 2.0

# 焼いた画像の長辺の上限（px）。極端に大きなセリフで、何千万画素の PNG を
# 作らないための歯止め。ここに当たると倍率のほうを落とす
RASTER_MAX_PX = 4096

# 描き紙を四方へ広げる幅（字の大きさに対する割合）。字は正方形からわずかに
# はみ出すことがあり、縁を滑らかにするぶんも外側へ出る。**広く取っても
# 透明なまま切り落とされる**ので、損はしない
MARGIN_RATIO = 0.5


def render_area(text: TextObject) -> Rect:
    """焼くときに用意する描き紙の範囲（ページ座標）。

    **枠より広く取る。** セリフは枠に収まらない字も隠さずに出す作りなので
    （→ 6.5）、枠をそのまま紙にすると、はみ出した字が焼くときだけ切れる。
    「画面で見えているものが焼ける」を守るには、実際に描かれる範囲を先に
    知る必要がある。

    縦書きは1文字ずつの置き場所が分かる（`vertical.layout`）ので、その和を取る。
    横書きは字送りが書体しだいなので、**Qt に測ってもらう**
    （`QFontMetricsF.boundingRect` は、同じ寄せの指定で `drawText` が使う矩形を
    返す）。どちらも最後に余白を足して丸め落としを防ぐ。
    """
    rect = text.rect
    margin = max(text.font.size_px * MARGIN_RATIO, 1.0)

    if text.direction == "vertical":
        cells = [
            g.cell
            for g in vertical.layout(text.content, rect, text.font.size_px, text.align)
        ]
        drawn = _union(cells) if cells else rect
    else:
        flags = (
            TEXT_ALIGN_FLAGS.get(text.align, Qt.AlignmentFlag.AlignHCenter)
            | Qt.AlignmentFlag.AlignVCenter
            | Qt.TextFlag.TextDontClip
        )
        measured: QRectF = QFontMetricsF(text_font(text.font)).boundingRect(
            qrect(rect), int(flags), text.content
        )
        drawn = _union([rect, _from_qrectf(measured)])

    # **枠そのものも必ず含める。** 測った結果が枠より小さいときに紙を
    # 縮めると、寄せの計算が枠を基準にしている描画側とずれる
    drawn = _union([drawn, rect])
    return Rect(
        drawn.x - margin, drawn.y - margin, drawn.w + margin * 2, drawn.h + margin * 2
    )


def rasterize(
    state, text: TextObject, scale: float = RASTER_SCALE
) -> tuple[bytes, tuple[int, int], Rect] | None:
    """セリフ1つを PNG に焼く。

    返すのは **PNG のバイト列・その原寸（px）・ページ上の置き場所**の3つ。
    字が1画素も描かれなければ `None`（空白だけのセリフなど）。

    置き場所は**焼いた字にぴったり付いた矩形**になる。透明な縁を落とすので
    （→ 6.14 で素材に対してやっているのと同じ）、当たり判定が字に密着し、
    回転の中心も字の中心に来る。
    """
    if not text.content.strip():
        return None

    area = render_area(text)
    scale = _fit_scale(area, scale)
    width = max(1, math.ceil(area.w * scale))
    height = max(1, math.ceil(area.h * scale))

    # **`Format_ARGB32`。** `_Premultiplied` にするとセリフだけが 2px ずれて
    # 描かれる（→ PySide6の落とし穴.md の 4）。PSD 書き出しが同じ理由で
    # こちらを選んでいる
    canvas = QImage(width, height, QImage.Format.Format_ARGB32)
    if canvas.isNull():
        return None
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.scale(scale, scale)
        painter.translate(-area.x, -area.y)
        # 補助表示は切る（空のセリフの点線枠を焼き付けないため → `PageRenderer`）
        PageRenderer(state, aids=False).draw_text_alone(painter, text)
    finally:
        painter.end()

    cropped = crop_to_content(canvas)
    if cropped is None:
        return None
    image, left, top = cropped

    placed = Rect(
        area.x + left / scale,
        area.y + top / scale,
        image.width() / scale,
        image.height() / scale,
    )
    return to_png_bytes(image), (image.width(), image.height()), placed


def _fit_scale(area: Rect, scale: float) -> float:
    """長辺が `RASTER_MAX_PX` を超えないところまで倍率を落とす。

    **落とすのは倍率だけで、範囲は削らない。** 範囲を削ると字が切れるが、
    倍率が下がるだけなら粗くなるだけで済む。
    """
    longest = max(area.w, area.h)
    if longest <= 0.0:
        return scale
    return min(scale, RASTER_MAX_PX / longest)


def _union(rects: list[Rect]) -> Rect:
    left = min(r.x for r in rects)
    top = min(r.y for r in rects)
    right = max(r.right for r in rects)
    bottom = max(r.bottom for r in rects)
    return Rect(left, top, right - left, bottom - top)


def _from_qrectf(rect: QRectF) -> Rect:
    return Rect(rect.x(), rect.y(), rect.width(), rect.height())
