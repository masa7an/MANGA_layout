"""PSD（Photoshop の保存形式）を書き出す（要件定義 10.1）。

**ここは形式のことしか知らない。** ページ・コマ・フキダシといった作品の
言葉は出てこず、受け取るのは「名前の付いた画像を下から順に並べたもの」
だけ。作品をレイヤーへ分解するのは `ui.psd_export` の仕事で、分けて
おくと形式の細かい決まりと、何をレイヤーにするかの判断が混ざらない。

## 扱う範囲

8bit RGB のラスターレイヤーと、それを束ねるフォルダだけ（要件定義
10.1）。調整レイヤー・スマートオブジェクト・ベクター・編集できる
テキスト・レイヤーマスクは書かない。**クリスタ側で作れるものを、
こちらで持つ意味が無い。**

## 外部のライブラリを使わない理由

書き込みができるものは numpy と C 言語の拡張を要求し、更新が止まって
いる。**出来合いのパッケージが無ければその場でコンパイルが要り、2台の
PC で同じ環境を作れなくなる**（`requirements.txt` はバージョンを固定
して運用している）。必要な範囲だけなら標準機能で書けるので、依存を
増やさない。トーン（→ 6.27）で numpy を足さずに済んだのと同じ判断。

## 踏むと分かりにくい所

- **レイヤー名の欄は2つある。** 古いほうは1バイト文字しか入らないので、
  日本語は `luni` のほうへ UTF-16 で書く。古いほうだけに書くと**クリスタ
  でレイヤー名が文字化けする**
- **並びは下から上。** 最初のレイヤーが一番奥になる
- **矩形の下端・右端は「含まない」。** `bottom = top + 高さ`
- **透明度は掛けていない値（ストレート）。** Qt の `Format_ARGB32` が
  そのまま使える（`_Premultiplied` は掛けてあるので、渡す前に戻す）
- **合成済みの1枚（merged image）も要る。** これが無いと開けないソフトが
  ある。レイヤーを重ねた結果と食い違っていても形式としては通ってしまうので、
  渡す側が責任を持つ（→ `ui.psd_export`）
"""

from __future__ import annotations

import pathlib
import re
import struct
import sys
from dataclasses import dataclass

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage

from .errors import ExportError
from .export_io import replace_or_raise, tmp_path_for

SIGNATURE = b"8BPS"
VERSION = 1
DEPTH = 8
COLOR_MODE_RGB = 3

#: 1辺の上限。形式そのものの制限（超えるなら PSB という別形式になる）。
#: 書き出し側の上限（`ui.export.MAX_SIDE_PX` = 8,000）のほうが厳しいので、
#: ここに引っかかることは実際には無い
MAX_SIDE_PX = 30000

# 圧縮の種類。0 が無圧縮、1 が PackBits（同じ値の連続をまとめる方式）。
# **1 を使う。** 無圧縮だと1ページで数十MBになり、2 以降（zip）は読めない
# ソフトがある
COMPRESSION_RLE = 1

# PackBits のひとかたまりの上限（127+1 個）
_MAX_RUN = 128

@dataclass(frozen=True)
class PsdLayer:
    """PSD に並べる1枚。

    `image` は**すでに切り詰めてある**ものを渡す（`crop_to_content`）。
    キャンバス全面のまま渡しても書けるが、透明なだけの所まで圧縮に
    掛けることになり、枚数ぶんの待ち時間が積み上がる。

    `x` / `y` はキャンバスの中での左上の位置。切り詰めた分をここで戻す。

    `alias` は古い名前欄に入れる1バイト文字の名前。日本語の `name` は
    `luni` のほうへ書くが、そちらを読まないソフトのために**役割が分かる
    英字**を別に持たせる（全部同じ名前にすると見分けが付かなくなる）。
    """

    name: str
    alias: str
    image: QImage
    x: int = 0
    y: int = 0
    visible: bool = True
    opacity: float = 1.0


@dataclass(frozen=True)
class PsdGroup:
    """レイヤーフォルダ（要件定義 10.1 の第2段階）。

    `children` は**下から上**の順。入れ子にもできるが、この道具が使うのは
    1段だけ（コマ1つ＝フォルダ1つ）。

    `expanded` はクリスタで開いた状態にするか。**既定は閉じておく。**
    コマの数だけフォルダが並ぶので、全部開いていると一覧が長くなり、
    「どのコマか」を探すのに縦に流すことになる。

    PSD のフォルダは**入れ物ではなく、前後を挟む2枚の目印**で表す。
    下端に区切り（`</Layer group>`）を、上端に名前を持つ1枚を置き、
    その間に挟まったものが中身になる。どちらも大きさ0のレイヤー。
    """

    name: str
    alias: str
    children: list[PsdLayer | PsdGroup]
    expanded: bool = False
    visible: bool = True
    opacity: float = 1.0


#: `lsct`（フォルダの目印）に書く値。1 は開いたフォルダ、2 は閉じた
#: フォルダ、3 はフォルダの下端を示す区切り
SECTION_OPEN = 1
SECTION_CLOSED = 2
SECTION_DIVIDER = 3

#: 区切りの1枚に付ける名前。**Photoshop が書くものと同じ綴りにする。**
#: 中身を持たない目印なので、画面に出ることは無い
DIVIDER_NAME = "</Layer group>"


# -- 画素を取り出す ----------------------------------------------------------


def _planes(image: QImage) -> tuple[list[bytes], list[bytes], list[bytes], list[bytes]]:
    """1行ずつの R / G / B / A を取り出す。

    PSD は色ごとに分けて（面ごとに）持つが、`QImage` は1画素ぶんを
    まとめて持つ。並び替えは拡張スライス（`raw[off::4]`）で行う。
    Python の繰り返しで1バイトずつ拾うと、300万画素で数十秒かかる。
    """
    raw, w, h, bpl = _raw_of(image)

    # `Format_ARGB32` は「32bit の整数」として決まっているので、メモリの
    # 上での並びは環境の endian（バイトの並び順）で変わる。Windows は
    # little なので B, G, R, A の順に入っている
    if sys.byteorder == "little":
        b, g, r, a = 0, 1, 2, 3
    else:
        a, r, g, b = 0, 1, 2, 3

    def plane(off: int) -> list[bytes]:
        return [raw[y * bpl + off : y * bpl + w * 4 : 4] for y in range(h)]

    return plane(r), plane(g), plane(b), plane(a)


def _alpha_rows(image: QImage) -> list[bytes]:
    """1行ずつの透明度だけ。中身のある範囲を探すのに使う。"""
    raw, w, h, bpl = _raw_of(image)
    off = 3 if sys.byteorder == "little" else 0
    return [raw[y * bpl + off : y * bpl + w * 4 : 4] for y in range(h)]


def _raw_of(image: QImage) -> tuple[bytes, int, int, int]:
    """生バイト列と寸法。**`Format_ARGB32` に揃えてから取る。**

    `_Premultiplied`（透明度を色に掛け込んである形式）のまま取ると、
    半透明の所の色が暗く沈む。PSD が持つのは掛けていない値なので、
    ここで戻しておく。
    """
    if image.format() != QImage.Format.Format_ARGB32:
        image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return (
        bytes(image.constBits()),
        image.width(),
        image.height(),
        image.bytesPerLine(),
    )


def content_bounds(image: QImage) -> QRect | None:
    """透明でない画素を囲む一番小さい矩形。全部透明なら None。

    **行ごとに標準の文字列操作で済ませる。** 1画素ずつ見ると300万回の
    繰り返しになるが、`count` / `lstrip` / `rstrip` は中で C の処理が
    走るので、行の数（数千回）しか Python の繰り返しが要らない。
    """
    rows = _alpha_rows(image)
    top: int | None = None
    bottom = 0
    left = image.width()
    right = 0
    for y, row in enumerate(rows):
        if row.count(0) == len(row):
            continue
        if top is None:
            top = y
        bottom = y
        first = len(row) - len(row.lstrip(b"\x00"))
        last = len(row.rstrip(b"\x00"))
        left = min(left, first)
        right = max(right, last)
    if top is None:
        return None
    return QRect(left, top, right - left, bottom - top + 1)


def crop_to_content(image: QImage) -> tuple[QImage, int, int] | None:
    """透明な縁を落とした1枚と、その左上の位置。全部透明なら None。"""
    box = content_bounds(image)
    if box is None:
        return None
    return image.copy(box), box.x(), box.y()


# -- 圧縮 --------------------------------------------------------------------

# 「同じ値が3つ以上続く場所」を正規表現1つで探す。`[\s\S]` は改行も含めた
# 任意の1バイトに一致させるため（`.` は既定で改行に一致しない）。
#
# **以前は 0x00 / 0xFF の2値だけを種にしていた。** 透明な所（0）と、白・黒で
# 塗られた所（255 / 0）が長く続く典型例だが、**灰色トーン**（→ 6.27）は
# 中間値（例: 濃さ 0.35 で RGB=166）のベタが面積いっぱいに続くことがあり、
# その値は種に無いため見つからずリテラルのまま書かれていた。ファイルが
# 肥大するだけで壊れはしないが（2026-08-08 に発見）、正規表現なら値を
# 限定せずに済み、C 実装なので速度もこれまでと変わらない。
_RUN_RE = re.compile(rb"([\s\S])\1{2,}")


def packbits(data: bytes) -> bytes:
    """PackBits で縮める。

    決まりは2つだけ。

    - 先頭が 0〜127 なら、続く「その数+1」個をそのまま並べたもの
    - 先頭が 129〜255 なら、次の1バイトを「257-その数」回（2〜128回）繰り返す

    **一番縮む並べ方でなくてよい。** 読む側は上の決まりどおりに戻すだけ
    なので、取りこぼした繰り返しはそのまま並べても壊れない。
    """
    out = bytearray()
    n = len(data)
    pos = 0
    while pos < n:
        start = _next_run(data, pos)
        if start < 0:
            start = n
        _literals(out, data, pos, start)
        pos = start
        if pos >= n:
            break
        # `_next_run` が見つけた以上、ここから3個以上は続いている
        chunk = data[pos : pos + _MAX_RUN]
        value = chunk[:1]
        run = len(chunk) - len(chunk.lstrip(value))
        out.append(257 - run)
        out += value
        pos += run
    return bytes(out)


def _next_run(data: bytes, pos: int) -> int:
    """次に同じ値が3つ以上続く場所。見つからなければ -1。"""
    match = _RUN_RE.search(data, pos)
    return match.start() if match else -1


def _literals(out: bytearray, data: bytes, start: int, end: int) -> None:
    """そのまま並べる区間を、128 バイトずつに切って書く。"""
    while start < end:
        size = min(_MAX_RUN, end - start)
        out.append(size - 1)
        out += data[start : start + size]
        start += size


def _rle_channel(rows: list[bytes]) -> bytes:
    """1つの面を PackBits で書く。**行ごとの長さの表が先に来る。**"""
    packed = [packbits(row) for row in rows]
    counts = b"".join(struct.pack(">H", len(p)) for p in packed)
    return struct.pack(">H", COMPRESSION_RLE) + counts + b"".join(packed)


# -- 各部を組み立てる --------------------------------------------------------


def _header(width: int, height: int, channels: int = 3) -> bytes:
    return struct.pack(
        ">4sH6xHIIHH",
        SIGNATURE,
        VERSION,
        channels,
        height,
        width,
        DEPTH,
        COLOR_MODE_RGB,
    )


def _resources(dpi: float) -> bytes:
    """解像度だけ書く（番号 1005）。

    入れておかないと**クリスタが 72dpi の画像として開く**。PNG に
    `setDotsPerMeterX/Y` を入れているのと同じ理由（→ 要件定義 6.7）。

    値は 16.16 の固定小数点（整数部と小数部を 16bit ずつ）。単位の 1 は
    「1インチあたりの画素数」。
    """
    fixed = round(dpi * 65536)
    data = struct.pack(">IHHIHH", fixed, 1, 1, fixed, 1, 1)
    # 名前は空（長さ0の文字列＋詰めもの1バイト）
    return b"8BIM" + struct.pack(">H", 1005) + b"\x00\x00" + struct.pack(">I", len(data)) + data


def _pascal(text: str, pad: int) -> bytes:
    """先頭に長さを持つ古い形式の文字列。`pad` の倍数まで詰める。"""
    raw = text.encode("ascii", "replace")[:255]
    body = bytes([len(raw)]) + raw
    return body + b"\x00" * (-len(body) % pad)


def _additional(key: bytes, data: bytes) -> bytes:
    return b"8BIM" + key + struct.pack(">I", len(data)) + data + b"\x00" * (len(data) % 2)


def _unicode_name(name: str) -> bytes:
    """`luni`。**日本語のレイヤー名はここにしか入らない。**"""
    encoded = name.encode("utf-16-be")
    return _additional(b"luni", struct.pack(">I", len(encoded) // 2) + encoded)


@dataclass
class _Record:
    """PSD に1行ぶんとして並ぶもの。

    **フォルダも「大きさ0のレイヤー」として並ぶ**ので、普通のレイヤーと
    同じ入れ物で扱える。違うのは `section`（`lsct` に書く値）を持つか
    どうかだけ。
    """

    left: int
    top: int
    right: int
    bottom: int
    channels: list[bytes]
    name: str
    alias: str
    visible: bool = True
    opacity: float = 1.0
    section: int | None = None


def _empty_channels() -> list[bytes]:
    """大きさ0のレイヤーの中身。圧縮の種類を書く2バイトだけ。"""
    empty = _rle_channel([])
    return [empty, empty, empty, empty]


def _records(items: list[PsdLayer | PsdGroup]) -> list[_Record]:
    """並べるものを、PSD に書く順（下から上）の一列にほどく。

    フォルダは**下端の区切りと、上端の名前付き1枚**に化け、その間に
    中身が挟まる。この形にしておけば、書く側はフォルダを知らなくてよい。
    """
    out: list[_Record] = []
    for item in items:
        if isinstance(item, PsdGroup):
            out.append(
                _Record(
                    0, 0, 0, 0, _empty_channels(),
                    DIVIDER_NAME, "divider", section=SECTION_DIVIDER,
                )
            )
            out.extend(_records(item.children))
            out.append(
                _Record(
                    0, 0, 0, 0, _empty_channels(),
                    item.name, item.alias,
                    visible=item.visible,
                    opacity=item.opacity,
                    section=SECTION_OPEN if item.expanded else SECTION_CLOSED,
                )
            )
            continue

        r, g, b, a = _planes(item.image)
        out.append(
            _Record(
                item.x,
                item.y,
                item.x + item.image.width(),
                item.y + item.image.height(),
                [_rle_channel(rows) for rows in (a, r, g, b)],
                item.name,
                item.alias,
                visible=item.visible,
                opacity=item.opacity,
            )
        )
    return out


def _layer_record(record: _Record) -> bytes:
    """1枚ぶんの見出し。**画素そのものはここには入らない**（後ろにまとめて置く）。

    面ごとの長さには、圧縮の種類を書く2バイトを**含める**。
    """
    parts = [
        struct.pack(">4i", record.top, record.left, record.bottom, record.right),
        struct.pack(">H", 4),
    ]
    for channel_id, data in zip((-1, 0, 1, 2), record.channels, strict=True):
        parts.append(struct.pack(">hI", channel_id, len(data)))

    # 8BIM + 重ね方（通常）+ 不透明度 + 切り抜き + 目印 + 詰めもの。
    # 目印の 2 のビットが立っていると**非表示**（0 が表示）。ラフを
    # 非表示で入れるのにこれを使う（→ 要件定義 10.1）
    flags = 0 if record.visible else 2
    opacity = max(0, min(255, round(record.opacity * 255)))
    parts.append(b"8BIMnorm" + bytes([opacity, 0, flags, 0]))

    extra = (
        struct.pack(">I", 0)  # レイヤーマスク（使わない）
        + struct.pack(">I", 0)  # 重ねる範囲の指定（使わない）
        + _pascal(record.alias, 4)
        + _unicode_name(record.name)
    )
    if record.section is not None:
        extra += _additional(b"lsct", struct.pack(">I", record.section))
    parts.append(struct.pack(">I", len(extra)) + extra)
    return b"".join(parts)


def _layer_section(items: list[PsdLayer | PsdGroup]) -> bytes:
    """レイヤーの見出しと画素をまとめた一区画。

    見出しには面ごとの長さが入るので、**先に全部の画素を圧縮してから**
    見出しを組む（`_records` がそこまで済ませている）。
    """
    records = _records(items)
    heads = b"".join(_layer_record(r) for r in records)
    pixels = b"".join(data for r in records for data in r.channels)

    info = struct.pack(">h", len(records)) + heads + pixels
    info += b"\x00" * (len(info) % 2)

    section = struct.pack(">I", len(info)) + info + struct.pack(">I", 0)
    return struct.pack(">I", len(section)) + section


def _merged(image: QImage) -> bytes:
    """合成済みの1枚。**行の長さの表は、3つの面をまとめて先に置く。**

    レイヤーの側（面ごとに表を持つ）と決まりが違うので、そのまま
    真似すると開けないファイルになる。
    """
    r, g, b, _a = _planes(image)
    packed = [[packbits(row) for row in rows] for rows in (r, g, b)]
    counts = b"".join(struct.pack(">H", len(p)) for rows in packed for p in rows)
    body = b"".join(p for rows in packed for p in rows)
    return struct.pack(">H", COMPRESSION_RLE) + counts + body


# -- 書き出す ----------------------------------------------------------------


def psd_bytes(
    layers: list[PsdLayer | PsdGroup], merged: QImage, dpi: float
) -> bytes:
    """PSD 1つぶんのバイト列。

    `layers` は**下から上**の順（最初が一番奥）。`PsdGroup` を混ぜると
    レイヤーフォルダになる。`merged` は全部を重ねた結果で、キャンバスの
    大きさもここから取る。
    """
    width, height = merged.width(), merged.height()
    if width < 1 or height < 1:
        raise ExportError(f"PSD の大きさが不正です（{width} × {height} px）")
    if max(width, height) > MAX_SIDE_PX:
        raise ExportError(
            f"{width} × {height} 画素は PSD にできません（1辺の上限 {MAX_SIDE_PX:,}px）"
        )

    return b"".join(
        [
            _header(width, height),
            struct.pack(">I", 0),  # 色の対応表（RGB では使わない）
            _with_length(_resources(dpi)),
            _layer_section(layers),
            _merged(merged),
        ]
    )


def _with_length(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def write_psd(
    path: pathlib.Path,
    layers: list[PsdLayer | PsdGroup],
    merged: QImage,
    dpi: float,
) -> None:
    """1つ書く。**別名で書き切ってから置き換える。**

    途中で落ちても、前回の書き出しが壊れた状態で残らない。置き換えの
    最後の1歩は `ui.export.write_image` と共有（→ `export_io`）。
    """
    data = psd_bytes(layers, merged, dpi)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path_for(path)
    try:
        tmp.write_bytes(data)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ExportError(f"書き出せませんでした: {path}（{e}）") from e
    replace_or_raise(tmp, path)
