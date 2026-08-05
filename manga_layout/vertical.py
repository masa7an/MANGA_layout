"""縦書きのセリフで、1文字ずつの置き場所を決める。

**Qt には日本語の縦書きが無い。** `QTextOption` にあるのは左右の向き
（アラビア語などの右横書き）だけで、縦書きの指定はどこにも無い
（2026-08-03 に Qt 6.11.1 で確認）。そのため 1 文字ずつ位置を計算して描く。

ここが持つのは**その計算だけ**で、Qt も描画も知らない。画面を出さずに
検証できるようにするためで、字の見た目に関わる話は一切入れない。

字形そのもの——句読点を右上へ寄せる、長音符「ー」と括弧を 90 度回す、
小書き文字をずらす——は**フォント側の縦書き字形（OpenType の `vert`）が
行う**。手元の日本語フォント 9 種すべてで効くことを確認済みなので、
こちらで文字ごとの例外表を持つ必要はない。有効にするのは描く側の役目
（`ui.render.vertical_font`）。

折り返しはしない。横書きと同じく手動改行のみ（要件定義 9章）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect

# 列と列の間隔（字の大きさに対する倍率）。
#
# 横書きは Qt が持つ行送りで組まれ、それが游ゴシックで字の大きさの
# 約 1.33 倍だった（64px 指定で行の高さ 85px）。同じページに縦横が
# 混ざったときに間隔だけ食い違うと落ち着かないので、そこへ合わせてある。
COLUMN_PITCH = 1.33


@dataclass(frozen=True)
class Glyph:
    """1 文字と、その字が占める正方形（px）。

    日本語の縦書きは字の大きさと同じ一辺の正方形を単位に組む。横書きと
    違って**字ごとの送り幅を使わない**ので、ここは幅を測らずに決められる。

    正方形の中でどう置くかは描く側に任せる（中央に置けばよい）。句読点が
    右上に寄るのはフォントの縦書き字形の仕事で、この正方形の位置とは別。
    """

    ch: str
    cell: Rect


# 整列の値を縦書きの意味へ読み替えるための対応。
#
# `align` は横書きのために作った項目なので、そのままでは意味が合わない。
# 「行の始まりに寄せる」が left、「終わりに寄せる」が right、という
# 読み方をすると縦書きにもそのまま渡せる。横書きの行は左から始まり、
# 縦書きの列は上から始まるので、**left は上寄せ**になる。
#
# 項目を増やさないのは、増やすと保存形式が変わり、横書きに戻したときに
# どちらの値を使うのかという問題が別に生まれるため。
ALIGN_TO_TOP = ("left",)
ALIGN_TO_BOTTOM = ("right",)


def layout(
    content: str, rect: Rect, size_px: float, align: str = "center"
) -> list[Glyph]:
    """縦書きに組んだ結果を、置く順に返す。

    列は**右から左へ**進む（日本語の縦書きの並び）。1 行目がいちばん右の列。

    枠に収まらないぶんは、はみ出したまま返す。切り詰めると、はみ出して
    いることに気づけない（横書きで折り返さないのと同じ理由）。
    """
    if not content or size_px <= 0:
        return []

    columns = content.split("\n")
    pitch = size_px * COLUMN_PITCH
    # 列の集まりを枠の左右中央に置く。1 列目の中心はその右端から半列ぶん内側
    block_w = pitch * len(columns)
    first_center_x = rect.x + (rect.w + block_w) / 2.0 - pitch / 2.0

    # center 揃えは行頭を列間で揃えたい（短い行だけ字下がりになるのは避ける）。
    # そのため、いちばん長い列を基準にブロックとしての開始位置を1つだけ決め、
    # 全列で共有する。left は常に rect.y、right は常に「自分の文字数ぶん
    # 手前」が rect.y + rect.h に一致するので、これらは元から列間で行頭・
    # 行末が揃っており、共有する基準を作る必要が無い。
    max_len = max((len(line) for line in columns), default=0)
    center_top = rect.y + (rect.h - size_px * max_len) / 2.0

    glyphs: list[Glyph] = []
    for index, line in enumerate(columns):
        center_x = first_center_x - pitch * index
        top = _column_top(rect, line, size_px, align, center_top)
        for order, ch in enumerate(line):
            glyphs.append(
                Glyph(
                    ch,
                    Rect(
                        center_x - size_px / 2.0,
                        top + size_px * order,
                        size_px,
                        size_px,
                    ),
                )
            )
    return glyphs


def _column_top(
    rect: Rect, line: str, size_px: float, align: str, center_top: float
) -> float:
    """1 列ぶんの、いちばん上の字の上端。"""
    if align in ALIGN_TO_TOP:
        return rect.y
    if align in ALIGN_TO_BOTTOM:
        return rect.y + rect.h - size_px * len(line)
    return center_top
